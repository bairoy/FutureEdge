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
# HISTORICAL CANDLES  (via yfinance — free, no API key)
# ============================================================

def load_historical_candles(
    symbol:   str,
    period:   str = "5d",    # how far back: "1d", "5d", "1mo"
    interval: str = "1m",    # candle size:  "1m", "5m", "15m"
) -> list[dict]:
    """
    Download historical OHLCV candles from Yahoo Finance.

    WHY THIS IS NEEDED:
    --------------------
    RSI needs 14 candles minimum.
    MACD needs 26 candles minimum.
    Bollinger Bands need 20 candles minimum.

    When the app starts, we load enough history so agents
    can immediately compute indicators on the first cycle.

    SYMBOL FORMAT FOR NSE:
    -----------------------
    Yahoo Finance uses ".NS" suffix for NSE stocks.
    Examples:
      RELIANCE  → "RELIANCE.NS"
      INFY      → "INFY.NS"
      TCS       → "TCS.NS"
      NIFTY 50  → "^NSEI"  (index, no suffix)
      NIFTY BANK → "^NSEBANK"

    RETURNS:
    ---------
    List of dicts, each dict is one candle:
    [
      {"open": 2500.0, "high": 2510.0, "low": 2495.0,
       "close": 2505.0, "volume": 123456, "timestamp": "..."},
      ...
    ]
    """

    # Convert our internal symbol to Yahoo Finance format
    yf_symbol = _to_yfinance_symbol(symbol)

    logger.info(
        f"📥 Loading historical candles | "
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
            f"✅ Loaded {len(candles)} candles for {yf_symbol}"
        )

        return candles

    except Exception as e:
        logger.error(f"yfinance error for {yf_symbol}: {e}")
        return []


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

    return index_map.get(symbol.upper(), f"{symbol.upper()}.NS")


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

        logger.debug(f"📊 Tick | token={token} | LTP={ltp}")

        # Do NOT close if using persistent client
        if not self._redis_sync:
            r.close()


# ============================================================
# READ LATEST TICK FROM REDIS STREAM
# ============================================================

async def get_latest_tick(symbol: str = "NIFTY 50") -> dict | None:
    """
    Read the most recent tick from Redis Stream.

    Called by agents at the start of each cycle to get
    the current market price.

    Returns None if the stream is empty (market closed,
    feed not started, or mock mode).
    """

    try:
        # xrevrange reads stream in reverse (newest first)
        # COUNT 1 = give us only the latest entry
        entries = await redis_client.xrevrange(
            STREAM_TICKS,
            count=1,
        )

        if not entries:
            return None

        # entries = [("stream_id", {field: value, ...})]
        _, fields = entries[0]

        return {
            "ltp":       float(fields.get("ltp",    0.0)),
            "open":      float(fields.get("open",   0.0)),
            "high":      float(fields.get("high",   0.0)),
            "low":       float(fields.get("low",    0.0)),
            "close":     float(fields.get("close",  0.0)),
            "volume":    int(fields.get("volume",   0)),
            "timestamp": fields.get("timestamp", ""),
        }

    except Exception as e:
        logger.error(f"get_latest_tick failed: {e}")
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