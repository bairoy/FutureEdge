"""
Kill Switch Service

Responsibilities:
-----------------
1. Halt all trading instantly
2. Resume trading safely
3. Check trading state
4. Centralize emergency controls

IMPORTANT:
-----------
This is critical risk infrastructure.

Used during:
- market crashes
- runaway execution
- broker failures
- model instability
- infrastructure incidents
"""

# ============================================================
# IMPORTS
# ============================================================

from loguru import logger

from app.db.redis import redis_client


# ============================================================
# REDIS KEY
# ============================================================

"""
Centralized emergency halt key.
"""

KILL_SWITCH_KEY = "TRADING_HALT"


# ============================================================
# ACTIVATE KILL SWITCH
# ============================================================

async def activate_kill_switch(
    duration_seconds: int = 3600,
    reason: str = "Manual halt"
):
    """
    Halt all trading activity.

    Parameters:
    ------------
    duration_seconds:
        automatic expiration time

    reason:
        why trading halted
    """

    # --------------------------------------------------------
    # STORE HALT STATE
    # --------------------------------------------------------

    await redis_client.set(
        KILL_SWITCH_KEY,
        "1"
    )


    # --------------------------------------------------------
    # AUTO EXPIRATION
    # --------------------------------------------------------

    """
    Prevent permanent accidental halt.
    """

    await redis_client.expire(
        KILL_SWITCH_KEY,
        duration_seconds
    )


    # --------------------------------------------------------
    # STORE REASON
    # --------------------------------------------------------

    await redis_client.set(

        "TRADING_HALT_REASON",

        reason
    )


    logger.critical(
        f"🛑 KILL SWITCH ACTIVATED | "
        f"Reason={reason}"
    )


# ============================================================
# RELEASE KILL SWITCH
# ============================================================

async def release_kill_switch():
    """
    Resume trading activity.
    """

    await redis_client.delete(
        KILL_SWITCH_KEY
    )

    await redis_client.delete(
        "TRADING_HALT_REASON"
    )


    logger.warning(
        "✅ KILL SWITCH RELEASED"
    )


# ============================================================
# CHECK KILL SWITCH
# ============================================================

async def is_trading_halted() -> bool:
    """
    Returns True if trading halted.
    """

    halt = await redis_client.get(
        KILL_SWITCH_KEY
    )

    return halt == "1"


# ============================================================
# GET HALT STATUS
# ============================================================

async def get_kill_switch_status() -> dict:
    """
    Get detailed halt status.
    """

    halted = await is_trading_halted()

    reason = await redis_client.get(
        "TRADING_HALT_REASON"
    )


    return {

        "halted": halted,

        "reason": reason
    }