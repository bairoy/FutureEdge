"""
app/api/routes/kill_switch_router.py
======================================
Kill switch — protected by risk_manager role.

Only risk_manager and admin can halt or resume trading.
Viewers and traders can only CHECK the status.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import require_viewer, require_risk_manager
from app.db.models.user import User


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

    # No duration: an operator halt lasts until an operator lifts it. This used
    # to pass 14400s, so trading silently resumed 4 hours later on its own.
    await activate_kill_switch(
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

    # Via the service, not a raw Redis read — an unreachable Redis must report
    # HALTED here too, otherwise the dashboard shows a green "ACTIVE" light
    # while the system is actually blocking every order.
    from app.services.kill_switch_service import get_kill_switch_status

    state = await get_kill_switch_status()

    return {
        "halted": state["halted"],
        "status": "HALTED" if state["halted"] else "ACTIVE",
        "reason": state["reason"],
    }