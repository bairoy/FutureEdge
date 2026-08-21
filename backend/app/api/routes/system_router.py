"""
app/api/routes/system_router.py
=================================
One endpoint that answers "what is this system actually doing right now?"

WHY THIS EXISTS:
-----------------
FutureEdge has two independent switches that users kept conflating, because
the UI only ever surfaced one of them:

  DATA FEED  — where prices come from.  Zerodha KiteTicker (realtime) or
               yfinance (~15 min delayed).  Read-only, no money at risk.
  BROKER     — where orders go.  MockBroker (simulated) or Zerodha (REAL
               MONEY on a live NSE account).

They are genuinely orthogonal. The normal development setup is *live feed +
mock broker*: trade against real prices without risking capital. The old
dashboard exposed a single "Enable Paper Trading" toggle wired only to the
broker, so there was no way to tell whether the prices on screen were live
NSE ticks or quarter-hour-old Yahoo data — and a stalled ticker looked
identical to a working one.

This endpoint reports both, separately, plus enough detail for the UI to say
*why* a feed is degraded rather than just that it is.

FEED LIVENESS IS MEASURED, NOT ASSUMED:
-----------------------------------------
`feed.is_live` is not "is Zerodha configured" — it is "did a tick actually
arrive recently". A socket can sit connected while delivering nothing (an
expired token mid-session does exactly this). So we read the age of the
newest entry in the Redis tick stream and require it to be fresh. Outside
market hours no ticks arrive by design, so staleness is only reported as a
problem while the market is open.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from loguru import logger

from app.auth.dependencies import require_viewer
from app.core.config import settings
from app.db.models.user import User
from app.db.redis import redis_client, STREAM_TICKS

router = APIRouter()

# A tick older than this while the market is open means the feed has stalled.
# Kite pushes several ticks a second per subscribed instrument, so anything
# beyond a few seconds is already anomalous; 15s avoids false alarms on
# thinly-traded symbols without hiding a genuinely dead socket.
MAX_TICK_AGE_SECONDS = 15


async def _newest_tick_age_seconds() -> float | None:
    """
    Age in seconds of the most recent tick in the Redis stream.

    Returns None if the stream is empty or unreadable — the caller treats
    that as "not live", never as "fine".
    """
    try:
        entries = await redis_client.xrevrange(STREAM_TICKS, count=1)
        if not entries:
            return None

        _entry_id, fields = entries[0]
        raw_ts = fields.get("timestamp")
        if not raw_ts:
            return None

        tick_time = datetime.fromisoformat(raw_ts)
        if tick_time.tzinfo is None:
            tick_time = tick_time.replace(tzinfo=timezone.utc)

        return (datetime.now(timezone.utc) - tick_time).total_seconds()

    except Exception as e:
        logger.warning(f"Could not determine tick age: {e}")
        return None


@router.get("/api/v1/system/status", summary="Current data feed and broker state")
async def get_system_status(current_user: User = Depends(require_viewer)):
    """
    Report the feed and the broker as two separate concerns.

    Viewer-level access: this is read-only operational state, and every user
    looking at a price on the dashboard deserves to know whether it is live.
    """
    from app.brokers.base import get_broker
    from app.brokers.symbol_mapper import is_market_open
    from app.data.feed import tick_publisher

    market_open = is_market_open()

    # ---------------- BROKER (where orders go) ----------------
    broker_mode = settings.ACTIVE_BROKER.lower()
    is_live_broker = broker_mode == "zerodha"

    broker_connected = False
    try:
        broker_connected = await get_broker().is_connected()
    except Exception as e:
        logger.warning(f"Broker connection check failed: {e}")

    # ---------------- FEED (where prices come from) ----------------
    feed_mode = settings.ACTIVE_FEED.lower()
    streaming = tick_publisher.is_streaming()
    tick_age = await _newest_tick_age_seconds()

    # Live means ticks are genuinely arriving. While the market is closed we
    # cannot expect fresh ticks, so an up socket is the best signal available.
    if feed_mode == "zerodha" and streaming:
        feed_live = (tick_age is not None and tick_age <= MAX_TICK_AGE_SECONDS) or not market_open
    else:
        feed_live = False

    if feed_live:
        feed_source, feed_label, feed_detail = (
            "zerodha_ticker", "Zerodha Live", "Realtime NSE ticks via KiteTicker",
        )
    elif feed_mode == "zerodha":
        # Configured for Zerodha but not delivering — say which failure it is.
        if not streaming:
            feed_detail = "KiteTicker not connected — reconnect Zerodha to restore live ticks"
        else:
            feed_detail = "KiteTicker connected but no recent ticks — session may have expired"
        feed_source, feed_label = "yfinance", "Yahoo Finance"
    else:
        feed_source, feed_label = "yfinance", "Yahoo Finance"
        feed_detail = "Feed set to mock — prices come from Yahoo Finance"

    return {
        "market_open": market_open,
        "feed": {
            "mode":            feed_mode,          # configured intent
            "source":          feed_source,        # what is actually serving prices
            "label":           feed_label,
            "detail":          feed_detail,
            "is_live":         feed_live,
            "is_delayed":      not feed_live,
            "streaming":       streaming,
            "tick_age_seconds": round(tick_age, 1) if tick_age is not None else None,
        },
        "broker": {
            "mode":       broker_mode,
            "label":      "Zerodha" if is_live_broker else "Paper (Mock)",
            "detail": (
                "Orders execute on your REAL Zerodha account"
                if is_live_broker
                else "Orders are simulated — no real money at risk"
            ),
            "is_live":    is_live_broker,
            "is_paper":   not is_live_broker,
            "connected":  broker_connected,
        },
        "zerodha": {
            # Whether a session can be established at all, independent of
            # whether Zerodha is currently the selected broker.
            "configured": bool(settings.ZERODHA_API_KEY and settings.ZERODHA_API_SECRET),
            "connected":  streaming or (is_live_broker and broker_connected),
        },
    }
