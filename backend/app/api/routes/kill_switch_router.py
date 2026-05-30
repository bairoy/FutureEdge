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
    Halt all trading activity and log the event.
    Only risk_manager and admin can call this.
    """
    from app.services.kill_switch_service import activate_kill_switch

    await activate_kill_switch(
        duration_seconds=14400,  # 4 hours default
        reason=request.reason,
        user_id=current_user.id,
        user_email=current_user.email,
    )

    return {"status": "halted", "reason": request.reason, "by": current_user.email}


@router.post("/resume", summary="Resume trading (risk_manager only)")
async def resume_trading(
    current_user: User = Depends(require_risk_manager),
):
    """Resume trading activity and log the event."""
    from app.services.kill_switch_service import release_kill_switch

    await release_kill_switch(
        user_id=current_user.id,
        user_email=current_user.email,
    )

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