"""
app/data/feed.py
=================
Live market data feed for Indian stocks (NSE/BSE).

TWO DATA SOURCES (both free):
------------------------------

1. yfinance  — historical OHLCV data
   Used for: loading past candles on startup so indicators
             like RSI and MACD have enough history to work.
   No API key needed.
   Example: yf.download("RELIANCE.NS", period="1d", interval="1m")

2. Zerodha KiteTicker WebSocket — live streaming ticks
   Used for: real-time price updates during market hours.
   Requires: ZERODHA_API_KEY + ZERODHA_ACCESS_TOKEN in .env
   Each tick is pushed into Redis Streams so all parts of
   the system can consume it.

FLOW:
------
startup → load historical candles via yfinance
          ↓
market open → KiteTicker WebSocket connects
          ↓
tick arrives → validate → publish to Redis Stream
          ↓
agents read latest candles from Redis Stream each cycle

MARKET HOURS:
--------------
NSE is open 9:15 AM – 3:30 PM IST, Monday to Friday.
Outside these hours, the WebSocket feed is inactive and
the feed manager automatically returns mock data.
"""

import json
import asyncio
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from typing import Optional

import yfinance as yf
from loguru import logger

from app.db.redis import redis_client, STREAM_TICKS, KEY_ZERODHA_ACCESS_TOKEN
import redis as sync_redis
from app.core.config import settings


# ============================================================
# TIMEZONE
# ============================================================

IST = ZoneInfo("Asia/Kolkata")   # Indian Standard Time


# ============================================================
# MARKET HOURS CHECK
# ============================================================

def is_market_open() -> bool:
    """
    Return True if NSE is currently open for trading.

    NSE hours: 9:15 AM – 3:30 PM IST, Monday–Friday.
    We also exclude weekends.
    """

    now = datetime.now(IST)

    # Weekend check (Monday=0, Sunday=6)
    if now.weekday() > 4:
        return False

    market_open  = dt_time(9, 15)
    market_close = dt_time(15, 30)

    return market_open <= now.time() <= market_close


# ============================================================
# HISTORICAL CANDLES  (via yfinance or Zerodha API)
# ============================================================

INTERVAL_MAP = {
    "1m": "minute",
    "3m": "3minute",
    "5m": "5minute",
    "10m": "10minute",
    "15m": "15minute",
    "30m": "30minute",
    "60m": "60minute",
    "1d": "day",
}

def get_date_range_from_period(period: str) -> tuple[datetime, datetime]:
    """Calculate from_date and to_date based on period (e.g. '5d', '1mo')."""
    from datetime import timedelta
    to_date = datetime.now(IST)
    
    # Parse numbers and characters
    num = int(''.join(filter(str.isdigit, period)) or 5)
    unit = ''.join(filter(str.isalpha, period)) or 'd'
    
    if unit == 'd':
        from_date = to_date - timedelta(days=num)
    elif unit == 'mo' or unit == 'm':
        from_date = to_date - timedelta(days=num * 30)
    elif unit == 'y':
        from_date = to_date - timedelta(days=num * 365)
    else:
        from_date = to_date - timedelta(days=5)
        
    return from_date, to_date

def _get_instrument_token_sync(symbol: str) -> Optional[int]:
    """Get the instrument token synchronously from Redis cache or fallbacks."""
    try:
        import redis as sync_redis
        import json
        r = sync_redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            decode_responses=True,
        )
        cache_key = "futureedge:instruments:NSE"
        cached = r.get(cache_key)
        r.close()
        if cached:
            instruments = json.loads(cached)
            sym_upper = symbol.strip().upper()
            for inst in instruments:
                if inst["tradingsymbol"].upper() == sym_upper:
                    return int(inst["instrument_token"])
    except Exception as e:
        logger.warning(f"Failed to lookup token synchronously for {symbol}: {e}")
    
    # Fallback default index/mock tokens
    index_map = {
        "NIFTY 50": 256265,
        "NIFTY50": 256265,
        "NIFTY BANK": 260105,
        "BANKNIFTY": 260105,
        "RELIANCE": 738561,
        "INFY": 408065,
        "TCS": 2953217,
    }
    return index_map.get(symbol.upper(), None)

def _get_sync_kite_client() -> Optional[any]:
    """Initialize a KiteConnect client synchronously using cached credentials."""
    try:
        from kiteconnect import KiteConnect
        import redis as sync_redis
        
        token = None
        # 1. Try Redis cache
        try:
            r = sync_redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                decode_responses=True,
            )
            from app.db.redis import KEY_ZERODHA_ACCESS_TOKEN
            val = r.get(KEY_ZERODHA_ACCESS_TOKEN)
            if val:
                token = val.decode() if isinstance(val, bytes) else val
            r.close()
        except:
            pass
            
        # 2. Try JSON file
        if not token:
            import json
            import os
            if os.path.exists("broker_token.json"):
                try:
                    with open("broker_token.json", "r") as f:
                        token_data = json.load(f)
                        token = token_data.get("ZERODHA_ACCESS_TOKEN")
                except:
                    pass
                    
        # 3. Try settings env fallback
        if not token:
            token = settings.ZERODHA_ACCESS_TOKEN
            
        if not settings.ZERODHA_API_KEY or not token:
            return None
            
        kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
        kite.set_access_token(token)
        return kite
    except Exception as e:
        logger.warning(f"Failed to initialize sync KiteConnect client: {e}")
        return None

def _load_historical_candles_from_zerodha(
    symbol: str,
    period: str = "5d",
    interval: str = "1m",
) -> list[dict]:
    """
    Load historical candles synchronously from Zerodha Kite Connect.
    Requires paid historical data add-on. Falls back to empty list on failure.
    """
    try:
        token = _get_instrument_token_sync(symbol)
        if not token:
            return []

        kite = _get_sync_kite_client()
        if not kite:
            return []

        from_date, to_date = get_date_range_from_period(period)
        kite_interval = INTERVAL_MAP.get(interval, "minute")

        logger.info(
            f"📥 Loading historical candles from Zerodha API | "
            f"symbol={symbol} (token={token}) | interval={kite_interval} | range={from_date.date()} to {to_date.date()}"
        )
        
        raw = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=kite_interval,
        )

        candles = []
        for row in raw:
            candles.append({
                "open":      round(float(row["open"]), 2),
                "high":      round(float(row["high"]), 2),
                "low":       round(float(row["low"]), 2),
                "close":     round(float(row["close"]), 2),
                "volume":    int(row["volume"]),
                "timestamp": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
            })

        logger.info(f"✅ Loaded {len(candles)} candles from Zerodha historical API for {symbol}")
        return candles

    except Exception as e:
        logger.warning(
            f"Zerodha historical API failed for {symbol}: {e} — "
            f"this is expected if you do not have the paid ₹2000/month historical API subscription. Falling back to yfinance."
        )
        return []

def _load_historical_candles_from_yfinance(
    symbol: str,
    period: str = "5d",
    interval: str = "1m",
) -> list[dict]:
    """Download historical candles from Yahoo Finance (free)."""
    yf_symbol = _to_yfinance_symbol(symbol)

    logger.info(
        f"📥 Loading historical candles from Yahoo Finance | "
        f"symbol={yf_symbol} | period={period} | interval={interval}"
    )

    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            logger.warning(f"yfinance returned no data for {yf_symbol}")
            return []

        candles = []
        for ts, row in df.iterrows():
            candles.append({
                "open":      round(float(row["Open"]),   2),
                "high":      round(float(row["High"]),   2),
                "low":       round(float(row["Low"]),    2),
                "close":     round(float(row["Close"]),  2),
                "volume":    int(row["Volume"]),
                "timestamp": ts.isoformat(),
            })

        logger.info(
            f"✅ Loaded {len(candles)} candles for {yf_symbol} from yfinance"
        )
        return candles

    except Exception as e:
        logger.error(f"yfinance error for {yf_symbol}: {e}")
        return []

def load_historical_candles(
    symbol:   str,
    period:   str = "5d",
    interval: str = "1m",
) -> list[dict]:
    """
    Load historical candles for seeding charts and agent context.
    Tries the official Zerodha API first, falling back to Yahoo Finance if not subscribed.
    """
    # 1. Try Zerodha API if active broker or feed is zerodha
    if settings.ACTIVE_BROKER.lower() == "zerodha" or settings.ACTIVE_FEED.lower() == "zerodha":
        candles = _load_historical_candles_from_zerodha(symbol, period, interval)
        if candles:
            return candles

    # 2. Fallback to free yfinance
    return _load_historical_candles_from_yfinance(symbol, period, interval)


def get_current_price_yfinance(symbol: str) -> float:
    """
    Get the latest price from Yahoo Finance.
    Used when market is closed or KiteTicker is not connected.
    Has a small delay (5–15 minutes) — not suitable for live trading.
    """

    yf_symbol = _to_yfinance_symbol(symbol)

    try:
        ticker = yf.Ticker(yf_symbol)
        data   = ticker.fast_info

        price = float(data.last_price)

        logger.debug(f"yfinance LTP | {yf_symbol} = {price}")

        return price

    except Exception as e:
        logger.error(f"yfinance get_current_price failed for {symbol}: {e}")
        return 0.0


def _to_yfinance_symbol(symbol: str) -> str:
    """
    Convert internal symbol name to Yahoo Finance format.

    Internal → Yahoo Finance:
      "NIFTY 50"   → "^NSEI"
      "NIFTY BANK" → "^NSEBANK"
      "RELIANCE"   → "RELIANCE.NS"
      "INFY"       → "INFY.NS"
    """

    index_map = {
        "NIFTY 50":   "^NSEI",
        "NIFTY50":    "^NSEI",
        "NIFTY BANK": "^NSEBANK",
        "BANKNIFTY":  "^NSEBANK",
        "SENSEX":     "^BSESN",
    }

    clean_symbol = symbol.strip()
    if clean_symbol.upper() in index_map:
        return index_map[clean_symbol.upper()]

    equity_symbol = clean_symbol.replace(" ", "").upper()
    return f"{equity_symbol}.NS"


# ============================================================
# LIVE TICK PUBLISHER  (Zerodha KiteTicker WebSocket)
# ============================================================

class NSETickPublisher:
    """
    Connects to Zerodha KiteTicker WebSocket and streams
    live NSE ticks into Redis Streams.

    WHAT IS KITETICKER?
    --------------------
    Zerodha provides a WebSocket API that pushes live price
    ticks for any instrument you subscribe to.
    Each tick arrives within milliseconds of a trade on NSE.

    HOW THIS INTEGRATES WITH THE SYSTEM:
    --------------------------------------
    1. NSETickPublisher connects to KiteTicker
    2. On each tick: write to Redis Stream (STREAM_TICKS)
    3. Agents read from Redis Stream at cycle start
    4. Frontend WebSocket subscribes to Redis pub/sub for live UI

    IMPORTANT:
    ----------
    KiteTicker is synchronous internally (uses threading).
    We run it in an executor to avoid blocking asyncio.
    """

    def __init__(self, instrument_tokens: list[int]):
        self._tokens    = instrument_tokens
        self._ticker    = None
        self._running   = False
        self._redis_sync = None  # Persistent sync Redis client

    def start(self) -> None:
        """
        Start the WebSocket connection in a background thread.
        KiteTicker handles its own threading internally.

        Call this from your lifespan startup after market opens.
        """

        # Initialize persistent sync Redis client
        try:
            self._redis_sync = sync_redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                decode_responses=True,
            )
            token = self._redis_sync.get(KEY_ZERODHA_ACCESS_TOKEN)
        except Exception as re:
            logger.warning(f"Could not check Redis for Zerodha access token: {re}")
            token = None

        if token:
            token = token.decode() if isinstance(token, bytes) else token
        else:
            token = settings.ZERODHA_ACCESS_TOKEN

        if not settings.ZERODHA_API_KEY or not token:
            logger.warning(
                "⚠️  KiteTicker not started — "
                "ZERODHA_API_KEY or ZERODHA_ACCESS_TOKEN missing"
            )
            return

        if not is_market_open():
            if settings.APP_ENV == "production":
                logger.info("📴 Market is closed — KiteTicker not started in production")
                return
            else:
                logger.warning("📴 Market is closed, but starting KiteTicker anyway for testing in development")

        try:
            from kiteconnect import KiteTicker

            self._ticker = KiteTicker(
                settings.ZERODHA_API_KEY,
                token,
            )

            # Register callbacks
            self._ticker.on_connect  = self._on_connect
            self._ticker.on_ticks    = self._on_ticks
            self._ticker.on_error    = self._on_error
            self._ticker.on_close    = self._on_close

            # connect() is non-blocking; it starts a background thread
            self._ticker.connect(threaded=True)
            self._running = True

            logger.info(
                f"🟢 NSETickPublisher started | tokens={self._tokens}"
            )

        except ImportError:
            logger.error(
                "kiteconnect not installed. Run: pip install kiteconnect"
            )

        except Exception as e:
            logger.error(f"NSETickPublisher start failed: {e}")

        if self._ticker and self._running:
            self._ticker.stop()
            self._running = False
            if self._redis_sync:
                try:
                    self._redis_sync.close()
                except Exception:
                    pass
                self._redis_sync = None
            logger.info("🔴 NSETickPublisher stopped")

    def subscribe_tokens(self, tokens: list[int]) -> None:
        """
        Subscribe to additional tokens dynamically on the fly.
        """
        if not tokens:
            return

        tokens = [int(t) for t in tokens]
        new_tokens = []
        for t in tokens:
            if t not in self._tokens:
                self._tokens.append(t)
                new_tokens.append(t)

        if not new_tokens:
            return

        if self._ticker and self._running:
            try:
                self._ticker.subscribe(new_tokens)
                self._ticker.set_mode(self._ticker.MODE_QUOTE, new_tokens)
                logger.info(f"Dynamically subscribed KiteTicker to: {new_tokens}")
            except Exception as e:
                logger.error(f"Failed to subscribe to new tokens {new_tokens}: {e}")

    # --------------------------------------------------------
    # KITETICKER CALLBACKS
    # --------------------------------------------------------

    def _on_connect(self, ws, response) -> None:
        """Called when WebSocket connects successfully."""

        logger.info("🟢 KiteTicker WebSocket connected")

        # Subscribe to our instruments
        ws.subscribe(self._tokens)

        # QUOTE mode gives: LTP, OHLC, volume, bid/ask
        ws.set_mode(ws.MODE_QUOTE, self._tokens)

    def _on_ticks(self, ws, ticks: list[dict]) -> None:
        """
        Called for every tick (price update) from NSE.

        Each tick dict contains:
        {
          "instrument_token": 256265,
          "last_price":       22500.50,
          "ohlc": {
            "open":  22400.0,
            "high":  22550.0,
            "low":   22380.0,
            "close": 22450.0,
          },
          "volume":      1234567,
          "timestamp":   datetime(...)
        }

        We publish each tick to Redis Streams synchronously
        (KiteTicker runs in its own thread, not asyncio).
        """

        for tick in ticks:
            try:
                self._publish_tick_sync(tick)
            except Exception as e:
                logger.error(f"Tick publish failed: {e}")

    def _on_error(self, ws, code, reason) -> None:
        logger.error(f"KiteTicker error | code={code} | reason={reason}")

    def _on_close(self, ws, code, reason) -> None:
        logger.warning(
            f"KiteTicker closed | code={code} | reason={reason}"
        )
        self._running = False

    # --------------------------------------------------------
    # PUBLISH TICK TO REDIS STREAM
    # --------------------------------------------------------

    def _publish_tick_sync(self, tick: dict) -> None:
        """
        Write one tick to Redis Stream.

        Redis Streams are like an append-only log.
        Each entry gets a unique ID (timestamp-sequence).
        Agents read the latest entry to get current price.

        This runs synchronously in KiteTicker's thread.
        We use redis.Redis (sync) not redis.asyncio here.
        """

        # Use persistent sync Redis client if available, fallback otherwise
        if self._redis_sync:
            r = self._redis_sync
        else:
            import redis as sync_redis
            r = sync_redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                decode_responses=True,
            )

        token = tick.get("instrument_token", 0)
        ltp   = tick.get("last_price", 0.0)
        ohlc  = tick.get("ohlc", {})
        ts    = tick.get("timestamp", datetime.now(IST).isoformat())

        # Write to Redis Stream
        # xadd keeps a max of 1000 entries (MAXLEN) to avoid memory growth
        r.xadd(
            STREAM_TICKS,
            {
                "token":     str(token),
                "ltp":       str(ltp),
                "open":      str(ohlc.get("open",  ltp)),
                "high":      str(ohlc.get("high",  ltp)),
                "low":       str(ohlc.get("low",   ltp)),
                "close":     str(ohlc.get("close", ltp)),
                "volume":    str(tick.get("volume_traded", 0)),
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            },
            maxlen=1000,
            approximate=True,
        )

        # Cache latest tick by token key
        try:
            latest_key = f"futureedge:ticks:latest:{token}"
            tick_data = {
                "ltp":       float(ltp),
                "open":      float(ohlc.get("open",  ltp)),
                "high":      float(ohlc.get("high",  ltp)),
                "low":       float(ohlc.get("low",   ltp)),
                "close":     float(ohlc.get("close", ltp)),
                "volume":    int(tick.get("volume_traded", 0)),
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            }
            r.set(latest_key, json.dumps(tick_data))
        except Exception as cache_err:
            logger.warning(f"Failed to cache latest tick for token {token} in Redis: {cache_err}")

        logger.debug(f"📊 Tick | token={token} | LTP={ltp}")

        # Do NOT close if using persistent client
        if not self._redis_sync:
            r.close()


# ============================================================
# READ LATEST TICK FROM REDIS STREAM
# ============================================================

async def get_latest_tick(symbol: str = "NIFTY 50") -> dict | None:
    """
    Read the most recent tick for the given symbol.
    Tries the token-specific Redis cache first, falling back to the stream.

    Called by agents at the start of each cycle to get
    the current market price.

    Returns None if no tick is found.
    """
    try:
        from app.services.instrument_service import get_instrument_token
        token = await get_instrument_token(symbol)
        if not token:
            if symbol.upper() == settings.DEFAULT_SYMBOL.upper():
                token = settings.DEFAULT_INSTRUMENT_TOKEN

        if token:
            latest_key = f"futureedge:ticks:latest:{token}"
            cached = await redis_client.get(latest_key)
            if cached:
                return json.loads(cached)

        # Fallback to the latest stream entry if matching token (if any)
        entries = await redis_client.xrevrange(
            STREAM_TICKS,
            count=1,
        )

        if not entries:
            return None

        _, fields = entries[0]
        entry_token = fields.get("token")
        
        # If we have a token, verify the stream entry matches
        if token and entry_token and str(entry_token) == str(token):
            return {
                "ltp":       float(fields.get("ltp",    0.0)),
                "open":      float(fields.get("open",   0.0)),
                "high":      float(fields.get("high",   0.0)),
                "low":       float(fields.get("low",    0.0)),
                "close":     float(fields.get("close",  0.0)),
                "volume":    int(fields.get("volume",   0)),
                "timestamp": fields.get("timestamp", ""),
            }
        return None

    except Exception as e:
        logger.error(f"get_latest_tick failed for {symbol}: {e}")
        return None


# ============================================================
# GLOBAL TICK PUBLISHER INSTANCE
# ============================================================

# Created once when the module is imported.
# Configured with NIFTY 50 by default.
# Add more tokens to the list to subscribe to more instruments.

tick_publisher = NSETickPublisher(
    instrument_tokens=[
        settings.DEFAULT_INSTRUMENT_TOKEN,  # NIFTY 50
        260105,                             # NIFTY BANK
        738561,                             # RELIANCE
        408065,                             # INFY
        2953217,                            # TCS
    ]
)