"""
app/db/redis.py
===============
Single Redis async client + all channel/key name constants.

WHAT REDIS IS USED FOR IN FUTUREEDGE:
--------------------------------------
1. Kill switch        TRADING_HALT key
                      Set to "1" to halt all trading instantly.

2. Tick Streams       Redis Streams (STREAM_TICKS)
                      Live NSE price ticks written here by the
                      data feed and read by agents each cycle.

3. Pub/Sub channels   After every agent cycle the orchestrator
                      publishes results to CHANNEL_AGENT_RESULTS.
                      The frontend WebSocket listens here for
                      real-time updates without polling.

4. Indicator cache    RSI, MACD, Bollinger computed once and
                      stored in Redis with a short TTL so
                      back-to-back cycles reuse the same values.

HOW TO USE:
-----------
    from app.db.redis import redis_client, KEY_TRADING_HALT

    # Read
    halted = await redis_client.get(KEY_TRADING_HALT)

    # Write with TTL (expires after 60 seconds)
    await redis_client.setex("my_key", 60, "value")

    # Publish to a channel (WebSocket listeners receive this)
    import json
    await redis_client.publish(CHANNEL_AGENT_RESULTS, json.dumps(data))
"""

import redis.asyncio as redis

from app.core.config import settings


# ============================================================
# CLIENT
# ============================================================

# decode_responses=True  : Redis returns Python str not bytes
# socket_connect_timeout : fail fast if Redis is unreachable

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True,
    socket_connect_timeout=10,
    socket_timeout=10,
    retry_on_timeout=True,
    health_check_interval=30,
)


# ============================================================
# KEY NAMES  (single source of truth — no typos across files)
# ============================================================

# Kill switch — set to "1" to halt, "0" to resume
KEY_TRADING_HALT = "TRADING_HALT"

# Indicator cache keys  (TTL = 60 seconds)
KEY_INDICATORS = "futureedge:indicators:{symbol}"   # format with symbol

# Zerodha dynamic access token (expires daily at 6:00 AM IST)
KEY_ZERODHA_ACCESS_TOKEN = "futureedge:zerodha:access_token"


# ============================================================
# REDIS STREAM NAMES
# ============================================================

# Live NSE ticks are written here by data/feed.py
# and read by agents at the start of each cycle.
STREAM_TICKS = "futureedge:ticks"


# ============================================================
# PUB/SUB CHANNEL NAMES
# ============================================================

# Published after every orchestrator decision
# Frontend subscribes to this for live agent vote updates
CHANNEL_AGENT_RESULTS = "futureedge:agent_results"

# Published after a trade is executed (or rejected)
CHANNEL_TRADE_EXECUTED = "futureedge:trade_executed"

# Published when HITL pauses a workflow
# Frontend uses this to show the approval modal
CHANNEL_HITL_PENDING = "futureedge:hitl_pending"

# Published when kill switch is toggled
CHANNEL_KILL_SWITCH = "futureedge:kill_switch"