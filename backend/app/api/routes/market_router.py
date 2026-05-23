"""
app/api/routes/market_router.py
=================================
WebSocket endpoint — streams live market data, agent results,
HITL events, and trade updates to the frontend in real time.

AUTHENTICATION ON WEBSOCKET:
------------------------------
HTTP WebSocket upgrades cannot send the Authorization header
in most browsers. Instead we accept the token as a query param:

    ws://localhost:8000/api/v1/market/stream?token=eyJhbGci...

The endpoint validates the token before accepting the connection.
If the token is missing or invalid, the WebSocket is closed with
code 4001 (our custom "unauthorized" code).

WHAT IS STREAMED:
-----------------
  type: "tick"         → live NSE price tick
  type: "agent_result" → all 4 agent votes after each cycle
  type: "hitl_pending" → trade proposal awaiting human approval
  type: "trade"        → executed or failed trade
  type: "kill_switch"  → trading halted or resumed

FRONTEND JAVASCRIPT EXAMPLE:
------------------------------
    const token = localStorage.getItem("access_token");
    const ws = new WebSocket(
        `ws://localhost:8000/api/v1/market/stream?token=${token}`
    );

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "tick")         updateChart(msg.data);
        if (msg.type === "agent_result") updateAgentPanel(msg.data);
        if (msg.type === "hitl_pending") showApprovalModal(msg.data);
        if (msg.type === "trade")        updateTradeTable(msg.data);
        if (msg.type === "kill_switch")  updateHaltButton(msg.data);
    };

    ws.onclose = (event) => {
        if (event.code === 4001) redirectToLogin();
    };
"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from loguru import logger

from app.auth.tokens import decode_token
from app.db.postgres import AsyncSessionLocal
from app.db.repos.user_repo import UserRepo
from app.db.redis import (
    redis_client,
    STREAM_TICKS,
    CHANNEL_AGENT_RESULTS,
    CHANNEL_HITL_PENDING,
    CHANNEL_TRADE_EXECUTED,
    CHANNEL_KILL_SWITCH,
)


router = APIRouter(prefix="/api/v1/market", tags=["Market Stream"])


# ============================================================
# WEBSOCKET AUTH HELPER
# ============================================================

async def _authenticate_websocket(token: str | None) -> str | None:
    """
    Validate a JWT token passed as a query parameter.

    Returns the user_id if valid, None if invalid/missing.

    We cannot use Depends() on WebSocket endpoints — FastAPI
    does not support it. So we manually decode and verify here.
    """

    if not token:
        return None

    payload = decode_token(token)

    if payload is None:
        return None

    if payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    # Verify user still exists and is active in the database
    async with AsyncSessionLocal() as session:
        user = await UserRepo.get_by_id(session, user_id)

    if user is None or not user.is_active:
        return None

    return user_id


# ============================================================
# WEBSOCKET ENDPOINT
# ============================================================

@router.websocket("/stream")
async def market_stream(
    websocket: WebSocket,
    token: str | None = Query(default=None, description="JWT access token"),
):
    """
    WebSocket that streams everything the frontend needs.

    Authentication: pass the access token as ?token=<your_token>

    Messages format:
        { "type": "tick",         "data": { "ltp": 22500, ... } }
        { "type": "agent_result", "data": { "direction": "LONG", ... } }
        { "type": "hitl_pending", "data": { "symbol": "RELIANCE", ... } }
        { "type": "trade",        "data": { "shares": 2, ... } }
        { "type": "kill_switch",  "data": { "halted": true, ... } }
    """

    # --------------------------------------------------------
    # AUTHENTICATE BEFORE ACCEPTING
    # --------------------------------------------------------
    # We check the token BEFORE calling websocket.accept().
    # If invalid, we close with code 4001 (our "unauthorized" code).

    user_id = await _authenticate_websocket(token)

    if user_id is None:
        await websocket.close(code=4001, reason="Unauthorized — invalid or missing token")
        logger.warning("WebSocket connection rejected — invalid token")
        return

    await websocket.accept()

    logger.info(f"WebSocket connected | user_id={user_id} | client={websocket.client}")

    # --------------------------------------------------------
    # SUBSCRIBE TO REDIS PUB/SUB CHANNELS
    # --------------------------------------------------------
    # We use a SEPARATE Redis connection for pub/sub.
    # Why? Once subscribed, a Redis connection is in a special mode
    # where you can only call subscribe/unsubscribe/get_message.
    # You cannot do regular commands (get, set, xread, etc.) on it.
    # So we keep two connections:
    #   redis_client        → for xread (stream) and regular commands
    #   pubsub_client       → dedicated to pub/sub listening

    import redis.asyncio as aioredis
    from app.core.config import settings

    pubsub_client = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        decode_responses=True,
    )

    pubsub = pubsub_client.pubsub()

    await pubsub.subscribe(
        CHANNEL_AGENT_RESULTS,
        CHANNEL_HITL_PENDING,
        CHANNEL_TRADE_EXECUTED,
        CHANNEL_KILL_SWITCH,
    )

    # "$" means: only new entries added AFTER we started listening.
    # We do not want to replay old ticks to a freshly connected user.
    last_tick_id = "$"

    try:
        while True:

            # ------------------------------------------------
            # READ LIVE TICKS  (from Redis Stream)
            # ------------------------------------------------
            # xread() reads new entries from the stream.
            # block=0 means: return immediately even if empty
            #   (we handle the empty case by just continuing the loop)

            try:
                entries = await redis_client.xread(
                    {STREAM_TICKS: last_tick_id},
                    count=10,
                    block=0,
                )

                if entries:
                    # entries = [("stream_name", [(id, {fields}), ...])]
                    for _stream_name, messages in entries:
                        for msg_id, fields in messages:
                            last_tick_id = msg_id   # advance our cursor

                            await websocket.send_json({
                                "type": "tick",
                                "data": {
                                    "ltp":       float(fields.get("ltp",    0)),
                                    "open":      float(fields.get("open",   0)),
                                    "high":      float(fields.get("high",   0)),
                                    "low":       float(fields.get("low",    0)),
                                    "close":     float(fields.get("close",  0)),
                                    "volume":    int(fields.get("volume",   0)),
                                    "timestamp": fields.get("timestamp", ""),
                                },
                            })

            except Exception as e:
                logger.warning(f"Tick stream read error: {e}")

            # ------------------------------------------------
            # READ PUB/SUB MESSAGES  (agent results, etc.)
            # ------------------------------------------------
            # timeout=0.01 means: wait at most 10ms for a message.
            # If nothing arrives in 10ms, return None and continue.
            # This keeps the loop responsive.

            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=0.01,
                )

                if message and message.get("data"):
                    channel = message.get("channel", "")
                    data    = message["data"]

                    try:
                        parsed = json.loads(data)
                    except Exception:
                        parsed = {"raw": data}

                    # Map Redis channel name → frontend message type
                    msg_type = {
                        CHANNEL_AGENT_RESULTS:  "agent_result",
                        CHANNEL_HITL_PENDING:   "hitl_pending",
                        CHANNEL_TRADE_EXECUTED: "trade",
                        CHANNEL_KILL_SWITCH:    "kill_switch",
                    }.get(channel, "unknown")

                    await websocket.send_json({
                        "type": msg_type,
                        "data": parsed,
                    })

            except Exception as e:
                logger.warning(f"PubSub read error: {e}")

            # ------------------------------------------------
            # SMALL SLEEP — prevent CPU busy loop
            # ------------------------------------------------
            # 100ms = we check for new data 10 times per second.
            # Fast enough for a trading dashboard, slow enough
            # to not waste CPU when the market is quiet.

            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected | user_id={user_id}")

    except Exception as e:
        logger.error(f"WebSocket error | user_id={user_id} | {e}")

    finally:
        # Always clean up Redis connections when WebSocket closes
        await pubsub.unsubscribe()
        await pubsub_client.close()