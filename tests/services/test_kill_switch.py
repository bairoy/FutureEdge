"""
tests/services/test_kill_switch.py
====================================
Unit tests for the trading halt switch.

Every test here exists to pin down ONE fail-closed property. The kill switch
previously failed OPEN in several ways, and each of those regressions is cheap
to reintroduce by accident, so they are asserted individually:

1. A halt with no explicit duration never expires (no Redis TTL is set)
2. An explicitly time-boxed halt still gets its TTL
3. A halt writes durable Postgres state, not just the Redis cache
4. Redis restarted mid-halt → startup reconciliation restores the halt
5. Postgres unreadable at startup → fail closed (halt), never "assume fine"
6. Redis unreachable during a check → is_trading_halted() reports True
7. Release aborts if the durable state cannot be cleared (stays halted)
8. A genuinely expired time-boxed halt does clear on startup
9. The execution path (money-moving) blocks orders when halted
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.services import kill_switch_service as ks


def _fake_redis() -> AsyncMock:
    """A Redis stand-in that records set/expire/persist/delete calls."""
    redis = AsyncMock()
    redis.get.return_value = None
    return redis


def _halt_state_row(
    is_halted: bool = True,
    reason: str = "Manual halt",
    halted_until: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.is_halted    = is_halted
    row.reason       = reason
    row.halted_by    = "risk@futureedge.test"
    row.halted_at    = datetime.now(timezone.utc)
    row.halted_until = halted_until
    return row


# ============================================================
# TEST 1: NO DURATION → NO EXPIRY
# ============================================================

@pytest.mark.asyncio
async def test_halt_without_duration_never_expires():
    """
    The regression that started this: duration_seconds defaulted to 3600, so a
    halt quietly lifted itself an hour later. With no duration passed, Redis
    must get NO TTL — and any TTL left over from a previous halt is cleared.
    """
    redis = _fake_redis()

    with patch.object(ks, "redis_client", redis), \
         patch.object(ks, "_write_halt_state", new=AsyncMock(return_value=True)) as write_state, \
         patch.object(ks, "_write_audit_event", new=AsyncMock()):

        await ks.activate_kill_switch(reason="Loss cap breached")

    redis.expire.assert_not_called()
    redis.persist.assert_awaited_once_with(ks.KILL_SWITCH_KEY)
    redis.set.assert_any_await(ks.KILL_SWITCH_KEY, "1")

    # …and the durable row records "no expiry"
    assert write_state.await_args.kwargs["is_halted"] is True
    assert write_state.await_args.kwargs["halted_until"] is None


# ============================================================
# TEST 2: EXPLICIT DURATION IS STILL HONOURED
# ============================================================

@pytest.mark.asyncio
async def test_halt_with_explicit_duration_sets_ttl():
    """Opt-in time-boxed halts still work — they just aren't the default."""
    redis = _fake_redis()

    with patch.object(ks, "redis_client", redis), \
         patch.object(ks, "_write_halt_state", new=AsyncMock(return_value=True)) as write_state, \
         patch.object(ks, "_write_audit_event", new=AsyncMock()):

        await ks.activate_kill_switch(duration_seconds=600, reason="Short maintenance halt")

    redis.expire.assert_awaited_once_with(ks.KILL_SWITCH_KEY, 600)
    redis.persist.assert_not_called()
    assert write_state.await_args.kwargs["halted_until"] is not None


# ============================================================
# TEST 3: HALT IS PERSISTED, NOT JUST CACHED
# ============================================================

@pytest.mark.asyncio
async def test_halt_persists_to_postgres():
    """Redis is a cache. If only Redis knows about the halt, a restart resumes trading."""
    redis = _fake_redis()

    with patch.object(ks, "redis_client", redis), \
         patch.object(ks, "_write_halt_state", new=AsyncMock(return_value=True)) as write_state, \
         patch.object(ks, "_write_audit_event", new=AsyncMock()):

        await ks.activate_kill_switch(reason="Broker token expired", user_email="ops@futureedge.test")

    write_state.assert_awaited_once()
    assert write_state.await_args.kwargs["is_halted"] is True
    assert write_state.await_args.kwargs["reason"] == "Broker token expired"
    assert write_state.await_args.kwargs["halted_by"] == "ops@futureedge.test"


# ============================================================
# TEST 4: REDIS RESTART DURING A HALT
# ============================================================

@pytest.mark.asyncio
async def test_redis_restart_during_halt_does_not_resume_trading():
    """
    The scenario from the plan: trading is halted, the Redis container restarts,
    the key is gone. Startup reconciliation reads Postgres and puts it back —
    with no TTL, because the stored halt had no expiry.
    """
    redis = _fake_redis()          # empty, as after a restart
    row   = _halt_state_row(is_halted=True, reason="Daily loss cap breached")

    with patch.object(ks, "redis_client", redis), \
         patch.object(ks, "_read_halt_state", new=AsyncMock(return_value=row)):

        still_halted = await ks.reconcile_kill_switch_from_db()

    assert still_halted is True
    redis.set.assert_any_await(ks.KILL_SWITCH_KEY, "1")
    redis.persist.assert_awaited_once_with(ks.KILL_SWITCH_KEY)
    redis.expire.assert_not_called()


# ============================================================
# TEST 5: POSTGRES UNREADABLE AT STARTUP → FAIL CLOSED
# ============================================================

@pytest.mark.asyncio
async def test_unreadable_db_at_startup_fails_closed():
    """
    If we cannot prove trading is safe to resume, we do not resume it. Booting
    into "not halted" because the DB was down is exactly the failure this
    switch exists to prevent.
    """
    redis = _fake_redis()

    with patch.object(ks, "redis_client", redis), \
         patch.object(ks, "_read_halt_state", new=AsyncMock(side_effect=OSError("db down"))):

        still_halted = await ks.reconcile_kill_switch_from_db()

    assert still_halted is True
    redis.set.assert_any_await(ks.KILL_SWITCH_KEY, "1")


# ============================================================
# TEST 6: REDIS UNREACHABLE ON A CHECK → FAIL CLOSED
# ============================================================

@pytest.mark.asyncio
async def test_is_trading_halted_fails_closed_when_redis_errors():
    """An unreachable Redis must read as HALTED, never as "go ahead"."""
    redis = _fake_redis()
    redis.get.side_effect = ConnectionError("redis unreachable")

    with patch.object(ks, "redis_client", redis):
        assert await ks.is_trading_halted() is True


@pytest.mark.asyncio
async def test_is_trading_halted_false_when_key_absent():
    """The ordinary path still works: no key, no halt."""
    redis = _fake_redis()
    redis.get.return_value = None

    with patch.object(ks, "redis_client", redis):
        assert await ks.is_trading_halted() is False


# ============================================================
# TEST 7: RELEASE ABORTS IF STATE CANNOT BE CLEARED
# ============================================================

@pytest.mark.asyncio
async def test_release_aborts_when_db_write_fails():
    """
    Clearing only the Redis cache would resume trading now and re-halt at the
    next restart — an inconsistent state nobody can reason about. Staying
    halted is the safe outcome, so release raises instead.
    """
    redis = _fake_redis()

    with patch.object(ks, "redis_client", redis), \
         patch.object(ks, "_write_halt_state", new=AsyncMock(return_value=False)), \
         patch.object(ks, "_write_audit_event", new=AsyncMock()):

        with pytest.raises(RuntimeError):
            await ks.release_kill_switch(user_email="risk@futureedge.test")

    redis.delete.assert_not_called()


@pytest.mark.asyncio
async def test_release_clears_redis_when_db_write_succeeds():
    """The happy path: durable state cleared, then the cache."""
    redis = _fake_redis()

    with patch.object(ks, "redis_client", redis), \
         patch.object(ks, "_write_halt_state", new=AsyncMock(return_value=True)), \
         patch.object(ks, "_write_audit_event", new=AsyncMock()):

        await ks.release_kill_switch(user_email="risk@futureedge.test")

    redis.delete.assert_any_await(ks.KILL_SWITCH_KEY)


# ============================================================
# TEST 8: A GENUINELY EXPIRED TIME-BOXED HALT DOES CLEAR
# ============================================================

@pytest.mark.asyncio
async def test_expired_timeboxed_halt_clears_on_startup():
    """Fail-closed must not mean "halted forever" — an explicit window that has
    already passed is over, and startup clears it."""
    redis = _fake_redis()
    row   = _halt_state_row(
        is_halted=True,
        halted_until=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    with patch.object(ks, "redis_client", redis), \
         patch.object(ks, "_read_halt_state", new=AsyncMock(return_value=row)), \
         patch.object(ks, "_write_halt_state", new=AsyncMock(return_value=True)):

        still_halted = await ks.reconcile_kill_switch_from_db()

    assert still_halted is False
    redis.delete.assert_any_await(ks.KILL_SWITCH_KEY)


# ============================================================
# TEST 9: THE MONEY PATH ACTUALLY RESPECTS THE SWITCH
# ============================================================
# CLAUDE.md requires a test for any change to the order-placement path.

@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
async def test_execution_node_blocks_orders_when_halted(_mock_market_open):
    """
    execution_node must refuse to place an order while halted, and must decide
    that via the fail-closed service call rather than a raw Redis read.
    """
    from app.agents.execution_agent import execution_node

    state = {
        "user_id": "test_user_1",
        "symbol": "RELIANCE",
        "run_id": "test_run_halted",
        "consensus": MagicMock(direction="LONG", size=10000.0, entry_price=2500.0),
        "hitl_required": False,
        "hitl_status": "APPROVED",
        "logs": [],
        "completed_nodes": [],
        "paper_trade": True,
    }

    with patch(
        "app.services.kill_switch_service.is_trading_halted",
        new=AsyncMock(return_value=True),
    ):
        result = await execution_node(state)

    assert result["execution_error"] == "KILL_SWITCH_ACTIVE"
    assert result["executed_trade"] is None
