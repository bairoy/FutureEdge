"""
app/services/kill_switch_service.py
====================================
The trading halt switch. Every order-placing path checks this first.

WHY THIS FILE IS WRITTEN THE WAY IT IS — READ BEFORE CHANGING:
---------------------------------------------------------------
This switch must FAIL CLOSED. "Fail closed" means every failure mode has to
land on "trading is halted", never on "trading is allowed". Three rules follow
from that, and all three have bitten this codebase before:

  1. A halt has NO expiry unless a caller explicitly passes one.
     `duration_seconds` used to default to 3600, so every halt silently
     un-halted itself an hour later with no human involved.

  2. Postgres, not Redis, is the source of truth.
     Redis is an in-memory cache — restarting it while halted used to wipe the
     flag and silently resume trading. `KillSwitchState` is durable, and
     `reconcile_kill_switch_from_db()` replays it into Redis at startup.

  3. If we cannot determine the halt state, we report HALTED.
     A Redis outage means `is_trading_halted()` returns True, not False.

Redis stays in the picture because it is the fast, cross-process read that the
per-trade hot path needs — but it is a cache of the Postgres row, not the truth.

USAGE:
------
    from app.services.kill_switch_service import is_trading_halted
    if await is_trading_halted():
        return  # blocked — do not place orders

Never read the TRADING_HALT Redis key directly; that skips the fail-closed
handling above.
"""

import json
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.db.redis import redis_client, CHANNEL_KILL_SWITCH, KEY_TRADING_HALT


# ============================================================
# REDIS KEYS
# ============================================================

# Alias kept so existing imports/tests keep working — the canonical name lives
# in app.db.redis so the key string is defined exactly once.
KILL_SWITCH_KEY = KEY_TRADING_HALT

KILL_SWITCH_REASON_KEY = "TRADING_HALT_REASON"


# ============================================================
# POSTGRES STATE (source of truth)
# ============================================================

async def _write_halt_state(
    is_halted:    bool,
    reason:       str | None = None,
    halted_by:    str | None = None,
    halted_until: datetime | None = None,
) -> bool:
    """
    Upsert the single durable halt-state row.

    Returns True if the state was persisted. The caller MUST surface a False
    return loudly: it means Redis is halted but nothing on disk knows it, so a
    Redis restart would resume trading.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.db.models.kill_switch_state import KillSwitchState, SINGLETON_ID

    try:
        async with AsyncSessionLocal() as session:
            row = await session.get(KillSwitchState, SINGLETON_ID)

            if row is None:
                row = KillSwitchState(id=SINGLETON_ID)
                session.add(row)

            row.is_halted    = is_halted
            row.reason       = reason
            row.halted_by    = halted_by
            row.halted_at    = datetime.now(timezone.utc) if is_halted else None
            row.halted_until = halted_until

            await session.commit()
        return True

    except Exception as db_err:
        logger.error(f"Failed to persist kill switch state to DB: {db_err}")
        return False


async def _read_halt_state():
    """
    Read the durable halt-state row.

    Returns the row, or None if there is no row yet (never halted). Raises on a
    DB error — callers decide how to fail, and for this switch that means closed.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.db.models.kill_switch_state import KillSwitchState, SINGLETON_ID

    async with AsyncSessionLocal() as session:
        return await session.get(KillSwitchState, SINGLETON_ID)


async def _write_audit_event(action: str, reason: str, user_id: str | None) -> None:
    """
    Append to the kill_switch_events audit log. Best-effort by design: a failed
    audit write must never prevent a halt from taking effect.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.db.models.kill_switch_event import KillSwitchEvent

    try:
        async with AsyncSessionLocal() as session:
            session.add(
                KillSwitchEvent(
                    user_id=user_id,
                    action=action,
                    reason=reason,
                    timestamp=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            logger.info(f"Kill switch {action} event written to DB")
    except Exception as db_err:
        logger.error(f"Failed to save kill switch {action} event to DB: {db_err}")


# ============================================================
# ACTIVATE KILL SWITCH
# ============================================================

async def activate_kill_switch(
    duration_seconds: int | None = None,
    reason: str = "Manual halt",
    user_id: str | None = None,
    user_email: str | None = None,
) -> None:
    """
    Halt all trading activity.

    Parameters:
    ------------
    duration_seconds:
        None (the default) means the halt NEVER expires — it stays until a human
        calls release_kill_switch(). Pass an integer only when you genuinely want
        a self-releasing, time-boxed halt; nothing in this codebase currently does.
    reason:
        why trading halted
    user_id:
        who triggered the halt (None for system auto-halts)
    user_email:
        email of the user who triggered the halt
    """

    halted_until = (
        datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        if duration_seconds is not None
        else None
    )

    # 1. Persist to Postgres FIRST — it is the source of truth, and a halt that
    #    only exists in Redis does not survive a Redis restart.
    persisted = await _write_halt_state(
        is_halted=True,
        reason=reason,
        halted_by=user_email or "SYSTEM",
        halted_until=halted_until,
    )

    # 2. Set the Redis cache so every worker sees the halt immediately.
    await redis_client.set(KILL_SWITCH_KEY, "1")
    if duration_seconds is not None:
        await redis_client.expire(KILL_SWITCH_KEY, duration_seconds)
    else:
        # Explicitly clear any TTL left over from a previous time-boxed halt,
        # otherwise this halt inherits that expiry and silently un-halts.
        await redis_client.persist(KILL_SWITCH_KEY)
    await redis_client.set(KILL_SWITCH_REASON_KEY, reason)

    if not persisted:
        logger.critical(
            "🛑 KILL SWITCH HALT IS NOT DURABLE — Postgres write failed. Trading "
            "is halted in Redis only; a Redis restart WILL resume trading. "
            "Fix the DB and re-issue the halt."
        )

    # 3. Write the audit trail entry (best-effort).
    await _write_audit_event("HALT", reason, user_id)

    # 4. Publish to Redis pub/sub (for WebSocket update)
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

    expiry_note = (
        f"expires in {duration_seconds}s"
        if duration_seconds is not None
        else "no expiry — requires explicit release"
    )
    logger.critical(
        f"🛑 KILL SWITCH ACTIVATED | Reason={reason} | "
        f"InitiatedBy={user_email or 'SYSTEM'} | {expiry_note}"
    )


# ============================================================
# RELEASE KILL SWITCH
# ============================================================

async def release_kill_switch(
    user_id: str | None = None,
    user_email: str | None = None,
) -> None:
    """
    Resume trading activity. This is the ONLY way a halt ends by default.
    """

    # 1. Clear the durable state first. If this fails we do NOT clear Redis —
    #    staying halted is the safe outcome, and clearing only the cache would
    #    resume trading now and re-halt on the next restart.
    persisted = await _write_halt_state(is_halted=False)

    if not persisted:
        logger.critical(
            "KILL SWITCH RELEASE ABORTED — could not clear halt state in Postgres. "
            "Trading remains halted (fail closed). Retry once the DB is reachable."
        )
        raise RuntimeError("Kill switch release failed: durable state not updated")

    # 2. Clear the Redis cache.
    await redis_client.delete(KILL_SWITCH_KEY)
    await redis_client.delete(KILL_SWITCH_REASON_KEY)

    # 3. Write the audit trail entry (best-effort).
    await _write_audit_event("RESUME", "Manual release", user_id)

    # 4. Publish to Redis pub/sub (for WebSocket update)
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
# STARTUP RECONCILIATION
# ============================================================

async def reconcile_kill_switch_from_db() -> bool:
    """
    Replay the durable halt state into Redis. Called once at startup, BEFORE any
    trading machinery comes up.

    This is what stops a Redis restart during an active halt from silently
    resuming trading: Redis comes back empty, Postgres still says halted, and
    this puts the flag back.

    Returns True if trading is halted after reconciliation.
    """

    try:
        row = await _read_halt_state()
    except Exception as db_err:
        # We cannot prove trading is safe to resume → assume it isn't.
        logger.critical(
            f"🛑 Cannot read kill switch state from Postgres at startup ({db_err}). "
            "Failing CLOSED — halting trading. Release explicitly once the DB is healthy."
        )
        await redis_client.set(KILL_SWITCH_KEY, "1")
        await redis_client.persist(KILL_SWITCH_KEY)
        await redis_client.set(
            KILL_SWITCH_REASON_KEY,
            "Kill switch state unreadable at startup (fail closed)",
        )
        return True

    if row is None or not row.is_halted:
        logger.info("Kill switch state: not halted")
        return False

    # A time-boxed halt whose window already passed is genuinely over.
    if row.halted_until is not None and row.halted_until <= datetime.now(timezone.utc):
        logger.info(
            f"Kill switch halt expired at {row.halted_until.isoformat()} — clearing"
        )
        await _write_halt_state(is_halted=False)
        await redis_client.delete(KILL_SWITCH_KEY)
        await redis_client.delete(KILL_SWITCH_REASON_KEY)
        return False

    # Still halted — restore the Redis cache.
    await redis_client.set(KILL_SWITCH_KEY, "1")
    if row.halted_until is not None:
        remaining = int(
            (row.halted_until - datetime.now(timezone.utc)).total_seconds()
        )
        await redis_client.expire(KILL_SWITCH_KEY, max(remaining, 1))
    else:
        await redis_client.persist(KILL_SWITCH_KEY)

    await redis_client.set(KILL_SWITCH_REASON_KEY, row.reason or "Trading halted")

    logger.critical(
        f"🛑 KILL SWITCH STILL ACTIVE ON STARTUP | Reason={row.reason} | "
        f"HaltedBy={row.halted_by} | HaltedAt={row.halted_at}"
    )
    return True


# ============================================================
# CHECK KILL SWITCH
# ============================================================

async def is_trading_halted() -> bool:
    """
    Returns True if trading is halted. THE check every order path must call.

    Fails closed: if Redis cannot be reached we cannot prove trading is allowed,
    so we report halted rather than letting orders through blind.
    """
    try:
        return await redis_client.get(KILL_SWITCH_KEY) == "1"
    except Exception as redis_err:
        logger.critical(
            f"🛑 Cannot read kill switch state from Redis ({redis_err}). "
            "Failing CLOSED — treating trading as halted."
        )
        return True


# ============================================================
# GET HALT STATUS
# ============================================================

async def get_kill_switch_status() -> dict:
    """
    Get detailed halt status for the status API / dashboard.
    """
    halted = await is_trading_halted()

    try:
        reason = await redis_client.get(KILL_SWITCH_REASON_KEY)
    except Exception:
        reason = "Kill switch state unreadable (fail closed)"

    return {
        "halted": halted,
        "reason": reason,
    }
