import json
from datetime import datetime, timezone
from loguru import logger

from app.db.redis import redis_client, CHANNEL_KILL_SWITCH
from app.core.config import settings


# ============================================================
# REDIS KEY
# ============================================================

KILL_SWITCH_KEY = "TRADING_HALT"


# ============================================================
# ACTIVATE KILL SWITCH
# ============================================================

async def activate_kill_switch(
    duration_seconds: int = 3600,
    reason: str = "Manual halt",
    user_id: str | None = None,
    user_email: str | None = None,
):
    """
    Halt all trading activity.

    Parameters:
    ------------
    duration_seconds:
        automatic expiration time
    reason:
        why trading halted
    user_id:
        who triggered the halt (None for system auto-halts)
    user_email:
        email of the user who triggered the halt
    """

    # 1. Store halt state in Redis
    await redis_client.set(KILL_SWITCH_KEY, "1")
    await redis_client.expire(KILL_SWITCH_KEY, duration_seconds)
    await redis_client.set("TRADING_HALT_REASON", reason)

    # 2. Write audit event to PostgreSQL
    from app.db.postgres import AsyncSessionLocal
    from app.db.models.kill_switch_event import KillSwitchEvent

    try:
        async with AsyncSessionLocal() as session:
            event = KillSwitchEvent(
                user_id=user_id,
                action="HALT",
                reason=reason,
                timestamp=datetime.now(timezone.utc),
            )
            session.add(event)
            await session.commit()
            logger.info("Kill switch HALT event written to DB")
    except Exception as db_err:
        logger.error(f"Failed to save kill switch HALT event to DB: {db_err}")

    # 3. Publish to Redis pub/sub (for WebSocket update)
    try:
        payload = {
            "halted":    True,
            "reason":    reason,
            "halted_by": user_email or "SYSTEM",
            "halted_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.publish(CHANNEL_KILL_SWITCH, json.dumps(payload))
    except Exception as pub_err:
        logger.warning(f"Failed to publish kill switch halt event: {pub_err}")

    logger.critical(
        f"🛑 KILL SWITCH ACTIVATED | Reason={reason} | InitiatedBy={user_email or 'SYSTEM'}"
    )


# ============================================================
# RELEASE KILL SWITCH
# ============================================================

async def release_kill_switch(
    user_id: str | None = None,
    user_email: str | None = None,
):
    """
    Resume trading activity.
    """

    # 1. Clear Redis keys
    await redis_client.delete(KILL_SWITCH_KEY)
    await redis_client.delete("TRADING_HALT_REASON")

    # 2. Write audit event to PostgreSQL
    from app.db.postgres import AsyncSessionLocal
    from app.db.models.kill_switch_event import KillSwitchEvent

    try:
        async with AsyncSessionLocal() as session:
            event = KillSwitchEvent(
                user_id=user_id,
                action="RESUME",
                reason="Manual release",
                timestamp=datetime.now(timezone.utc),
            )
            session.add(event)
            await session.commit()
            logger.info("Kill switch RESUME event written to DB")
    except Exception as db_err:
        logger.error(f"Failed to save kill switch RESUME event to DB: {db_err}")

    # 3. Publish to Redis pub/sub (for WebSocket update)
    try:
        payload = {
            "halted":     False,
            "resumed_by": user_email or "SYSTEM",
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.publish(CHANNEL_KILL_SWITCH, json.dumps(payload))
    except Exception as pub_err:
        logger.warning(f"Failed to publish kill switch resume event: {pub_err}")

    logger.warning(
        f"✅ KILL SWITCH RELEASED | InitiatedBy={user_email or 'SYSTEM'}"
    )


# ============================================================
# CHECK KILL SWITCH
# ============================================================

async def is_trading_halted() -> bool:
    """
    Returns True if trading halted.
    """
    halt = await redis_client.get(KILL_SWITCH_KEY)
    return halt == "1"


# ============================================================
# GET HALT STATUS
# ============================================================

async def get_kill_switch_status() -> dict:
    """
    Get detailed halt status.
    """
    halted = await is_trading_halted()
    reason = await redis_client.get("TRADING_HALT_REASON")

    return {
        "halted": halted,
        "reason": reason,
    }