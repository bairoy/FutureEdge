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
from datetime import datetime

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
# HISTORY SEEDING ENDPOINT
# ============================================================

@router.get("/history/{symbol}")
async def get_market_history(symbol: str):
    """
    Fetch the last 50 minutes of history for a symbol via yfinance.
    Used to seed the chart immediately when a user searches for a stock.
    """
    from app.data.feed import load_historical_candles
    candles = load_historical_candles(symbol, period="5d", interval="1m")
    
    # Format for lightweight-charts (Unix seconds)
    formatted = []
    for c in candles[-500:]:
        try:
            ts = int(datetime.fromisoformat(c["timestamp"]).timestamp())
            formatted.append({
                "time":   ts,
                "open":   c["open"],
                "high":   c["high"],
                "low":    c["low"],
                "close":  c["close"],
                "volume": c["volume"],
            })
        except:
            continue
    return formatted


# ============================================================
# WEBSOCKET ENDPOINT
# ============================================================

from app.core.config import settings

@router.websocket("/stream")
async def market_stream(
    websocket: WebSocket,
    token: str | None = Query(default=None),
):
    from app.services.instrument_service import get_instrument_token

    user_id = await _authenticate_websocket(token)
    if user_id is None:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    logger.info(f"WebSocket connected | user_id={user_id}")

    current_symbol = "NIFTY 50"
    current_token = await get_instrument_token(current_symbol)
    if not current_token:
        current_token = settings.DEFAULT_INSTRUMENT_TOKEN

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(
        CHANNEL_AGENT_RESULTS,
        CHANNEL_HITL_PENDING,
        CHANNEL_TRADE_EXECUTED,
        CHANNEL_KILL_SWITCH,
    )

    # Background task to listen for commands FROM the client (like subscribe)
    async def listen_to_client():
        nonlocal current_symbol, current_token
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "subscribe":
                    new_symbol = data.get("symbol", "NIFTY 50")
                    # Clear current ticks for the new symbol
                    await websocket.send_json({"type": "clear_ticks"})
                    current_symbol = new_symbol
                    
                    resolved_token = await get_instrument_token(new_symbol)
                    if resolved_token:
                        current_token = resolved_token
                    else:
                        if new_symbol.upper() == settings.DEFAULT_SYMBOL.upper():
                            current_token = settings.DEFAULT_INSTRUMENT_TOKEN
                        else:
                            current_token = None

                    logger.info(f"User {user_id} subscribed to {new_symbol} (token: {current_token})")
                    
                    # Dynamically subscribe KiteTicker if active
                    if current_token:
                        try:
                            from app.data.feed import tick_publisher
                            tick_publisher.subscribe_tokens([current_token])
                        except Exception as sub_err:
                            logger.error(f"Error subscribing dynamically to {new_symbol}: {sub_err}")
        except:
            pass

    asyncio.create_task(listen_to_client())

    last_tick_id = "$"
    try:
        while True:
            # 1. READ LIVE TICKS
            try:
                entries = await redis_client.xread({STREAM_TICKS: last_tick_id}, count=5, block=10)
                if entries:
                    for _, messages in entries:
                        for msg_id, fields in messages:
                            last_tick_id = msg_id
                            
                            # Only send if it matches the current subscribed token
                            tick_token = fields.get("token")
                            expected_token = current_token

                            if not expected_token or not tick_token or str(tick_token) != str(expected_token):
                                continue

                            await websocket.send_json({
                                "type": "tick",
                                "data": {
                                    "ltp":       float(fields.get("ltp", 0)),
                                    "open":      float(fields.get("open", 0)),
                                    "high":      float(fields.get("high", 0)),
                                    "low":       float(fields.get("low", 0)),
                                    "close":     float(fields.get("close", 0)),
                                    "volume":    int(fields.get("volume", 0)),
                                    "timestamp": fields.get("timestamp", ""),
                                },
                            })
            except (TimeoutError, asyncio.TimeoutError):
                pass

            # 2. READ PUB/SUB
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.01)
            if message and message.get("data"):
                channel = message.get("channel", "")
                data = message["data"]
                try:
                    parsed = json.loads(data)
                except:
                    parsed = {"raw": data}

                msg_type = {
                    CHANNEL_AGENT_RESULTS:  "agent_result",
                    CHANNEL_HITL_PENDING:   "hitl_pending",
                    CHANNEL_TRADE_EXECUTED: "trade",
                    CHANNEL_KILL_SWITCH:    "kill_switch",
                }.get(channel, "unknown")

                await websocket.send_json({"type": msg_type, "data": parsed})

            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected | user_id={user_id}")

    except Exception as e:
        logger.error(f"WebSocket error | user_id={user_id} | {e}")

    finally:
        # Always clean up Redis connections when WebSocket closes
        try:
            await pubsub.unsubscribe()
            await pubsub.close()
        except:
            pass