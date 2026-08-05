"""
tests/brokers/test_kite_errors.py
===================================
Kite failures must be classified, not swallowed (Implementation Plan, Phase 2).

The plan's acceptance criterion is the last section here: a call made with an
invalid/expired access token halts trading and alerts, instead of failing
silently and retrying indefinitely.

The rest pins the classification policy itself, because the cost of getting it
wrong runs in both directions:

  - Classifying an unrecoverable error as retryable reproduces the 2026-08-05
    incident — "No IPs configured" retried every few seconds for an hour,
    alerting nobody.
  - Classifying a transient network blip as HALT would stop trading and
    require a human to release the kill switch over a hiccup that fixes
    itself in a second.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from kiteconnect import exceptions as kex

from app.brokers.kite_errors import (
    KiteAction,
    classify_kite_error,
    handle_kite_error,
)


# ============================================================
# CLASSIFICATION POLICY
# ============================================================

@pytest.mark.parametrize(
    "exc, expected",
    [
        # Transient Kite ↔ OMS plumbing — retry quietly.
        (kex.NetworkException("upstream timeout"), KiteAction.RETRY),
        (kex.DataException("bad OMS response"), KiteAction.RETRY),

        # Session or app unusable — nothing can trade until a human acts.
        (kex.TokenException("Incorrect `api_key` or `access_token`."), KiteAction.HALT),
        (kex.PermissionException("permission denied"), KiteAction.HALT),

        # This call is dead; the system can keep running.
        (kex.InputException("invalid tradingsymbol"), KiteAction.FAIL),
        (kex.OrderException("insufficient margin"), KiteAction.FAIL),
        (kex.GeneralException("something broke"), KiteAction.FAIL),
    ],
)
def test_classification_policy(exc, expected):
    assert classify_kite_error(exc) is expected


def test_static_ip_rejection_halts_regardless_of_exception_class():
    """
    The exact 2026-08-05 failure. Kite has moved this error between error_types
    before, so it is matched on message as well as class — misclassifying it as
    retryable is what produced the hour-long retry storm.
    """
    exc = kex.GeneralException(
        "No IPs configured for this app. Add allowed IPs on the Kite developer console."
    )
    assert classify_kite_error(exc) is KiteAction.HALT


def test_unknown_exception_fails_loudly_rather_than_retrying():
    """A non-Kite error on a money path defaults to FAIL, never a quiet retry."""
    assert classify_kite_error(ValueError("something unexpected")) is KiteAction.FAIL


# ============================================================
# SIDE EFFECTS
# ============================================================

@pytest.mark.asyncio
async def test_transient_error_neither_halts_nor_alerts():
    """A network blip must not stop trading or page the operator."""
    halt = AsyncMock()
    alert = AsyncMock()

    with patch("app.services.kill_switch_service.activate_kill_switch", halt), \
         patch("app.services.telegram_service.send_telegram_message", alert):
        action = await handle_kite_error(
            kex.NetworkException("timeout"), context="exit_monitor:SBIN"
        )

    assert action is KiteAction.RETRY
    halt.assert_not_awaited()
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_retryable_error_alerts_but_keeps_trading():
    """One bad symbol should not halt the whole system."""
    halt = AsyncMock()
    alert = AsyncMock()

    with patch("app.services.kill_switch_service.activate_kill_switch", halt), \
         patch("app.services.telegram_service.send_telegram_message", alert):
        action = await handle_kite_error(
            kex.InputException("invalid tradingsymbol"), context="execution:BADSYM"
        )

    assert action is KiteAction.FAIL
    halt.assert_not_awaited()
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_alert_failure_never_masks_the_broker_error():
    """A Telegram outage must not turn a handled error into an unhandled one."""
    with patch("app.services.kill_switch_service.activate_kill_switch", AsyncMock()), \
         patch(
             "app.services.telegram_service.send_telegram_message",
             AsyncMock(side_effect=Exception("telegram down")),
         ):
        action = await handle_kite_error(
            kex.TokenException("expired"), context="execution:RELIANCE"
        )

    assert action is KiteAction.HALT  # returned normally despite the alert failing


# ============================================================
# THE PLAN'S ACCEPTANCE CRITERION
# ============================================================

@pytest.mark.asyncio
async def test_expired_token_halts_trading_and_alerts():
    """
    Plan, Phase 2: "a call made with a deliberately invalid/expired access
    token halts trading and sends an alert, instead of silently failing and
    retrying indefinitely."
    """
    halt = AsyncMock()
    alert = AsyncMock()

    with patch("app.services.kill_switch_service.activate_kill_switch", halt), \
         patch("app.services.telegram_service.send_telegram_message", alert):
        action = await handle_kite_error(
            kex.TokenException("Incorrect `api_key` or `access_token`."),
            context="ZerodhaBroker.connect",
        )

    assert action is KiteAction.HALT
    halt.assert_awaited_once()

    # The halt must NOT self-expire: only a human can clear the underlying
    # cause, so a timed halt would silently resume against a dead session.
    assert halt.await_args.kwargs["duration_seconds"] is None

    alert.assert_awaited_once()
    assert "HALTED" in alert.await_args.args[0]


@pytest.mark.asyncio
async def test_halt_failure_is_escalated_not_swallowed():
    """
    If the kill switch itself cannot be activated the operator must still be
    told — this is the worst case: unusable broker AND no halt.
    """
    alert = AsyncMock()

    with patch(
             "app.services.kill_switch_service.activate_kill_switch",
             AsyncMock(side_effect=Exception("redis down")),
         ), \
         patch("app.services.telegram_service.send_telegram_message", alert):
        action = await handle_kite_error(
            kex.TokenException("expired"), context="ZerodhaBroker.connect"
        )

    assert action is KiteAction.HALT
    alert.assert_awaited_once()


# ============================================================
# ALERT NOISE — the failure mode that makes alerting useless
# ============================================================

@pytest.mark.asyncio
async def test_missing_token_does_not_alert():
    """
    "Not logged in yet" is the NORMAL state every morning (Kite tokens expire
    6 AM IST), and connect() runs on every portfolio fetch and status poll.
    Alerting on it would page the operator continuously until they log in,
    training them to ignore the alerts that matter.
    """
    from app.brokers.zerodha import ZerodhaBroker

    halt = AsyncMock()
    alert = AsyncMock()
    broker = ZerodhaBroker()

    with patch("app.services.kill_switch_service.activate_kill_switch", halt), \
         patch("app.services.telegram_service.send_telegram_message", alert), \
         patch("app.brokers.zerodha.decrypt_stored_token", return_value=None), \
         patch("app.core.config.settings.ZERODHA_ACCESS_TOKEN", ""):
        connected = await broker.connect()

    assert connected is False
    halt.assert_not_awaited()
    alert.assert_not_awaited()
