# FutureEdge Quantitative Handbook: Zerodha & Telegram Integrations

This developer handbook provides a comprehensive reference, design pattern, and code library for integrating **Zerodha Kite Connect APIs** and **Telegram Bots** into quantitative trading applications.

Use the concepts and templates below to build or upgrade other trading and multi-agent workflow automation systems.

---

## Part 1: Zerodha Kite Connect API Integration

The Zerodha Kite Connect API is a set of REST-like HTTP APIs that expose services required to build a complete trading platform (orders, positions, user data, live/historical market data).

### 1. Authentication & Session Lifecycle
Zerodha enforces a daily authentication cycle. A session is valid for only one day (expiring at midnight/6 AM IST). 

#### Sequence Flow:
```
1. Redirect User to: https://kite.zerodha.com/connect/login?v=3&api_key=API_KEY
2. User authenticates with client ID, password, and 2FA TOTP.
3. Zerodha redirects browser to callback URL with request token:
   https://yourdomain.com/auth/zerodha/callback?request_token=ABC123XYZ...
4. Your backend captures request_token and exchanges it for a persistent access_token.
```

#### Code: Session Exchange & Persistence
To prevent system reloads on credential updates inside containerized setups (like Docker with Uvicorn reload on `.env` changes), store the token in a Redis cache (for rapid memory access) and write a local JSON file (for persistent disk backup).

```python
import json
from kiteconnect import KiteConnect

def exchange_and_persist_token(api_key: str, api_secret: str, request_token: str, redis_client=None):
    """
    Exchanges a daily request token for a session access token and stores it.
    """
    # 1. Initialize KiteConnect
    kite = KiteConnect(api_key=api_key)
    
    # 2. Exchange request_token for access_token
    session = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session["access_token"]
    user_name = session.get("user_name", "unknown")
    
    # 3. Store in Redis (for fast, runtime memory checks)
    if redis_client:
        redis_client.set("zerodha:access_token", access_token)
        
    # 4. Store in JSON configuration (to survive restarts without env reloads)
    with open("broker_token.json", "w") as f:
        json.dump({"ZERODHA_ACCESS_TOKEN": access_token}, f)
        
    return access_token, user_name
```

---

### 2. Initializing & Reconnecting Clients
Initialize the sync/async connection dynamically by looking up the token in Redis, then the JSON backup, and finally the environment fallback.

```python
import os
import json
from kiteconnect import KiteConnect

def get_authorized_kite_client(api_key: str, env_token: str, redis_client=None) -> KiteConnect:
    token = None
    
    # 1. Try Redis cache
    if redis_client:
        try:
            val = redis_client.get("zerodha:access_token")
            if val:
                token = val.decode() if isinstance(val, bytes) else val
        except Exception:
            pass
            
    # 2. Try local JSON backup
    if not token and os.path.exists("broker_token.json"):
        try:
            with open("broker_token.json", "r") as f:
                token = json.load(f).get("ZERODHA_ACCESS_TOKEN")
        except Exception:
            pass
            
    # 3. Fallback to Env variable
    if not token:
        token = env_token
        
    if not token:
        raise ValueError("No Zerodha access token found in Redis, JSON, or env settings.")
        
    # Initialize connection client
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token)
    return kite
```

---

### 3. Live Streaming Feed: KiteTicker (WebSockets)
Live tick streaming is implemented via WebSockets using the `KiteTicker` library. In a production backend, do not handle ticks directly inside your main thread. Instead, publish them to a **Redis Stream** or **Message Queue** so that analytical agents can consume them asynchronously.

```python
import time
from kiteconnect import KiteTicker
from loguru import logger

class LiveTickStreamer:
    def __init__(self, api_key: str, access_token: str, redis_client, symbol_token_map: dict):
        self.ticker = KiteTicker(api_key, access_token)
        self.redis = redis_client
        self.tokens = list(symbol_token_map.keys()) # Zerodha numeric instrument tokens
        self.token_to_symbol = symbol_token_map     # Map of token -> symbol string

        # Register event callbacks
        self.ticker.on_ticks = self.on_ticks
        self.ticker.on_connect = self.on_connect
        self.ticker.on_close = self.on_close

    def on_connect(self, ws, response):
        logger.info("Connected to KiteTicker. Subscribing to tokens...")
        # Subscribe to modeQuote (contains price, volume, open/high/low/close data)
        ws.subscribe(self.tokens)
        ws.set_mode(ws.modeQuote, self.tokens)

    def on_ticks(self, ws, ticks):
        for tick in ticks:
            instrument_token = tick.get("instrument_token")
            symbol = self.token_to_symbol.get(instrument_token, f"UNKNOWN_{instrument_token}")
            last_price = tick.get("last_price")
            
            if last_price:
                # Publish to Redis Stream for downstream agent consumption
                payload = {
                    "symbol": symbol,
                    "price": str(last_price),
                    "timestamp": str(time.time())
                }
                self.redis.xadd("market:ticks", payload, max_len=1000, approximate=True)

    def on_close(self, ws, code, reason):
        logger.warning(f"KiteTicker connection closed: {code} - {reason}")

    def start(self):
        # Start the WebSocket in a background thread
        self.ticker.connect(threaded=True)

    def stop(self):
        self.ticker.close()
```

---

### 4. Order Execution Pattern (With Price Buffers)
To guarantee execution during fast-moving markets, use **Compliant Limit Orders** with a price buffer rather than raw Market Orders (which are highly susceptible to slippage and flash crashes).

* **For Buy Orders**: Set limit price slightly **higher** than the Last Traded Price (LTP).
* **For Sell Orders**: Set limit price slightly **lower** than the LTP.

```python
def place_order_with_buffer(kite: KiteConnect, symbol: str, direction: str, quantity: int, ltp: float, buffer_pct=0.0005):
    """
    Places a limit order with a small price buffer to guarantee immediate execution.
    """
    buffer_amt = ltp * buffer_pct
    
    if direction.upper() == "BUY":
        # Buy Limit slightly higher than current price
        limit_price = round(ltp + buffer_amt, 2)
        transaction_type = kite.TRANSACTION_TYPE_BUY
    else:
        # Sell Limit slightly lower than current price
        limit_price = round(ltp - buffer_amt, 2)
        transaction_type = kite.TRANSACTION_TYPE_SELL
        
    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=transaction_type,
            quantity=quantity,
            product=kite.PRODUCT_MIS, # Intra-day (Margin Intraday Squareoff)
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            validity=kite.VALIDITY_DAY
        )
        return {"success": True, "order_id": order_id, "limit_price": limit_price}
    except Exception as e:
        logger.error(f"Failed to place order: {e}")
        return {"success": False, "error": str(e)}
```

---

### 5. Historical Candle Fetching & Fallbacks
If you do not have Zerodha's paid **₹2000/month Historical Data API add-on**, the Kite historical data calls will fail. Implement a transparent fallback that defaults to Yahoo Finance (`yfinance`) so the system continues running.

```python
import yfinance as yf
from datetime import datetime, timedelta

def load_candles(kite_client, symbol: str, token: int, active_feed: str) -> list[dict]:
    """
    Loads historical candles, automatically falling back to yfinance on authentication failure.
    """
    if active_feed.lower() == "zerodha" and kite_client:
        try:
            to_date = datetime.now()
            from_date = to_date - timedelta(days=5)
            # Official Zerodha historical data fetch
            records = kite_client.historical_data(
                instrument_token=token,
                from_date=from_date.strftime("%Y-%m-%d"),
                to_date=to_date.strftime("%Y-%m-%d"),
                interval="minute"
            )
            return [{"time": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]} for r in records]
        except Exception as e:
            logger.warning(f"Zerodha Historical API failed (e.g. no historical subscription add-on): {e}. Falling back to yfinance.")

    # Fallback: Yahoo Finance
    yf_symbol = "^NSEI" if symbol == "NIFTY 50" else symbol
    ticker = yf.Ticker(yf_symbol)
    df = ticker.history(period="5d", interval="1m")
    
    candles = []
    for idx, row in df.iterrows():
        candles.append({
            "time": idx.to_pydatetime(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"])
        })
    return candles
```

---
---

## Part 2: Telegram Bot Integration

Telegram bots are controlled via incoming HTTP requests forwarded to your server from Telegram's cloud Servers (Webhooks).

### 1. Webhook Payload Structure
Telegram sends a structured JSON payload to your webhook. A single webhook handles two primary categories of interaction:

```
                  ┌────────────────────────┐
                  │ Webhook Payload (JSON) │
                  └───────────┬────────────┘
                              │
                    Is "callback_query"?
                    /                 \
                 [YES]                [NO]
                  /                     \
      ┌──────────▼──────────┐   ┌────────▼──────────┐
      │ Inline Button Click │   │ Text Msg/Command  │
      │   (callback_data)   │   │     (text)        │
      └─────────────────────┘   └───────────────────┘
```

---

### 2. Callback Queries & Webhooks (HITL Workflow)
This pattern exposes inline callback buttons to the user, catches the button clicks on the webhook endpoint, and updates the chat.

#### Step A: Send an Alert with Inline Buttons
```python
async def send_hitl_alert(proposal_id: str, symbol: str, direction: str):
    """
    Sends a message to Telegram with inline [Approve] and [Reject] buttons.
    """
    text = (
        f"<b>🔔 NEW TRADE PROPOSAL</b>\n"
        f"Symbol: {symbol}\n"
        f"Direction: {direction}\n"
    )
    
    # Structure of inline keyboard
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{proposal_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{proposal_id}"}
            ]
        ]
    }
    
    # Send message using standard API
    await send_telegram_message(text, reply_markup=reply_markup)
```

#### Step B: Handle Callback Clicks (FastAPI Webhook Route)
* **Security Filter**: Ensure `chat_id` matches your target admin user.
* **Acknowledge Click**: Call `answerCallbackQuery` immediately to prevent client spinners.
* **Clean Up Message**: Edit the original message to remove buttons, preventing double execution.

```python
from fastapi import APIRouter, Request, HTTPException
import urllib.parse

router = APIRouter()
TELEGRAM_CHAT_ID = "5577526209" # Secure chat ID

@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    payload = await request.json()
    
    # 1. Process Button Clicks (Callback Queries)
    callback_query = payload.get("callback_query")
    if callback_query:
        chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
        
        # 🔒 Security Filter
        if str(chat_id) != TELEGRAM_CHAT_ID:
            return {"status": "unauthorized"}

        query_id = callback_query.get("id")
        callback_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        message_id = message.get("message_id")
        original_text = message.get("text", "")

        action, proposal_id = callback_data.split(":", 1)
        
        # Stop loading indicator in user client
        await answer_callback_query(query_id, f"Trade {action.upper()}")

        # Execute decision (e.g. approve/reject execution flow)
        # compile_execution(action, proposal_id)
        
        # Edit Telegram message: remove buttons and append decision details
        status_icon = "✅" if action == "approve" else "❌"
        updated_text = f"{original_text}\n\n{status_icon} DECISION: {action.upper()} (via Telegram)"
        await edit_telegram_message(message_id, updated_text, reply_markup={"inline_keyboard": []})
        
        return {"status": "success"}

    # 2. Process Text Messages (Auto-extract Redirect URLs)
    message_obj = payload.get("message")
    if message_obj:
        chat_id = message_obj.get("chat", {}).get("id")
        if str(chat_id) != TELEGRAM_CHAT_ID:
            return {"status": "unauthorized"}

        text = message_obj.get("text", "").strip()
        
        # Extract request token if user pasted full login redirect link
        request_token = None
        if "request_token=" in text:
            try:
                parsed = urllib.parse.urlparse(text)
                params = urllib.parse.parse_qs(parsed.query)
                request_token = params.get("request_token", [None])[0]
            except Exception:
                pass
        elif text.startswith("/refresh "):
            request_token = text.split(None, 1)[1]
            
        if request_token:
            await send_telegram_message("🔄 Processing token update...")
            # perform_broker_reconnect(request_token)
            await send_telegram_message("✅ Session updated!")
            return {"status": "session_refreshed"}

    return {"status": "ignored"}
```

---

## Part 3: Reusable Telegram Bot HTTP Helper Methods

Use these low-level asynchronous wrappers (built with `httpx`) to interface directly with Telegram's Bot API endpoints.

```python
import httpx

BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
DEFAULT_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

async def send_telegram_message(text: str, chat_id: str = DEFAULT_CHAT_ID, reply_markup: dict = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
        
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=10.0)
        return response.json()

async def edit_telegram_message(message_id: int, text: str, chat_id: str = DEFAULT_CHAT_ID, reply_markup: dict = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=10.0)
        return response.json()

async def answer_callback_query(callback_query_id: str, text: str) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
        "text": text
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=5.0)
        return response.json()
```
