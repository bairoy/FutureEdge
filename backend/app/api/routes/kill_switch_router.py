"""
app/api/routes/kill_switch_router.py
======================================
Kill switch — protected by risk_manager role.

Only risk_manager and admin can halt or resume trading.
Viewers and traders can only CHECK the status.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from loguru import logger

from app.auth.dependencies import require_viewer, require_risk_manager
from app.db.models.user import User
from app.db.redis import redis_client, KEY_TRADING_HALT, CHANNEL_KILL_SWITCH


router = APIRouter(prefix="/api/v1/kill-switch", tags=["Kill Switch"])


class HaltRequest(BaseModel):
    reason: str = "Manual operator halt"


@router.post("/halt", summary="Halt all trading (risk_manager only)")
async def halt_trading(
    request:      HaltRequest,
    current_user: User = Depends(require_risk_manager),
):
    """
    Set TRADING_HALT=1 in Redis. Blocks all new executions.
    Only risk_manager and admin can call this.
    """

    await redis_client.set(KEY_TRADING_HALT, "1")

    payload = {
        "halted":    True,
        "reason":    request.reason,
        "halted_by": current_user.email,
        "halted_at": datetime.utcnow().isoformat(),
    }

    await redis_client.publish(CHANNEL_KILL_SWITCH, json.dumps(payload))

    logger.critical(
        f"KILL SWITCH ACTIVATED | by={current_user.email} | reason={request.reason}"
    )

    return {"status": "halted", "reason": request.reason, "by": current_user.email}


@router.post("/resume", summary="Resume trading (risk_manager only)")
async def resume_trading(
    current_user: User = Depends(require_risk_manager),
):
    """Set TRADING_HALT=0 in Redis. Trading resumes on next cycle."""

    await redis_client.set(KEY_TRADING_HALT, "0")

    payload = {
        "halted":     False,
        "resumed_by": current_user.email,
        "resumed_at": datetime.utcnow().isoformat(),
    }

    await redis_client.publish(CHANNEL_KILL_SWITCH, json.dumps(payload))

    logger.info(f"Kill switch released | by={current_user.email}")

    return {"status": "active", "by": current_user.email}


@router.get("/status", summary="Check kill switch state (any user)")
async def kill_switch_status(
    current_user: User = Depends(require_viewer),
):
    """Any authenticated user can check if trading is halted."""

    value     = await redis_client.get(KEY_TRADING_HALT)
    is_halted = value == "1"

    return {
        "halted": is_halted,
        "status": "HALTED" if is_halted else "ACTIVE",
    }