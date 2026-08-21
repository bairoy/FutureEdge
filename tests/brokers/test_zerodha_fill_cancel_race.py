"""
tests/brokers/test_zerodha_fill_cancel_race.py
================================================
The fill/cancel race in ZerodhaBroker.place_order — a money-moving path.

THE RACE:
----------
place_order polls order_history for 2s. If the order has not filled it calls
cancel_order and reports failure. But an order can fill in the window between
the final poll and the cancel landing at Zerodha. The old code returned
success=False unconditionally in that case, so execution_agent wrote no trade
row while a REAL position sat open at the broker.

That is the dangerous direction. The phantom-trade bug fixed alongside this
one had the DB inventing a position that did not exist — loud, and caught by
exit_monitor thrashing. This one is silent: the position exists, nothing
tracks it, no stop-loss guards it, and it stays open until Zerodha's ~3:20 PM
MIS square-off decides the outcome for you.

The fix is to stop trusting the cancel and re-read the broker afterwards.
These tests pin every branch of that verification, including partial fills
(Kite can fill part of a quantity and cancel the rest, leaving real exposure
under a CANCELLED status) and the case where verification itself fails.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.brokers.zerodha import ZerodhaBroker

ORDER_ID = "250805000123456"


def _broker_with_kite(order_history_sequence, cancel_raises=False) -> ZerodhaBroker:
    """
    A connected ZerodhaBroker whose order_history returns each supplied
    response in turn, so a test can script "unfilled during polling, then
    filled by the time we verify".
    """
    kite = MagicMock()
    kite.TRANSACTION_TYPE_BUY = "BUY"
    kite.TRANSACTION_TYPE_SELL = "SELL"
    kite.ORDER_TYPE_MARKET = "MARKET"
    kite.ORDER_TYPE_LIMIT = "LIMIT"
    kite.VARIETY_REGULAR = "regular"
    kite.PRODUCT_MIS = "MIS"

    kite.place_order.return_value = ORDER_ID
    kite.order_history.side_effect = order_history_sequence
    if cancel_raises:
        kite.cancel_order.side_effect = Exception("Order cannot be cancelled — already executed")

    broker = ZerodhaBroker()
    broker._kite = kite
    broker._connected = True
    return broker


def _open():
    return [{"status": "OPEN", "filled_quantity": 0, "average_price": 0.0}]


def _complete(avg=733.60, qty=2):
    return [{"status": "COMPLETE", "filled_quantity": qty, "average_price": avg}]


def _partial(avg=733.55, qty=1):
    return [{"status": "CANCELLED", "filled_quantity": qty, "average_price": avg}]


def _cancelled():
    return [{"status": "CANCELLED", "filled_quantity": 0, "average_price": 0.0}]


@pytest.fixture(autouse=True)
def _no_sleep():
    """The poll loop sleeps 10 × 200ms; tests should not pay for that."""
    async def _instant(_seconds):
        return None

    with patch("asyncio.sleep", new=_instant):
        yield


# ============================================================
# THE RACE ITSELF
# ============================================================

@pytest.mark.asyncio
async def test_order_filled_during_cancel_is_reported_as_a_position():
    """
    Unfilled through every poll, but COMPLETE by the time we verify.

    This is the regression: the broker holds a real position, so place_order
    MUST report success so a trade row gets written and the exit monitor can
    manage it.
    """
    broker = _broker_with_kite(
        [_open()] * 10 + [_complete(avg=733.60, qty=2)],
        cancel_raises=True,   # cancel fails precisely because it already filled
    )

    result = await broker.place_order("HDFCBANK", "SHORT", 2, order_type="MARKET")

    assert result.success is True, (
        "Order filled during the cancel window but was reported as failed — "
        "a real position would go unrecorded and unguarded."
    )
    assert result.status == "COMPLETE"
    assert result.fill_price == 733.60
    assert result.quantity == 2
    assert result.raw_response["filled_during_cancel"] is True

    # 10 polls + 1 post-cancel verification. The 11th call is the whole fix:
    # without it the outcome above is unreachable.
    assert broker._kite.order_history.call_count == 11
    broker._kite.cancel_order.assert_called_once()


@pytest.mark.asyncio
async def test_partial_fill_under_cancelled_status_is_still_a_position():
    """
    Kite reports CANCELLED with filled_quantity=1. One share of real exposure
    exists; reporting failure would leave it untracked.
    """
    broker = _broker_with_kite([_open()] * 10 + [_partial(avg=733.55, qty=1)])

    result = await broker.place_order("HDFCBANK", "SHORT", 2, order_type="MARKET")

    assert result.success is True, "a partial fill is still a live position"
    assert result.quantity == 1, "must record the FILLED quantity, not the requested one"
    assert result.fill_price == 733.55
    assert result.raw_response["requested_quantity"] == 2


@pytest.mark.asyncio
async def test_genuinely_cancelled_order_still_reports_failure():
    """The fix must not turn every timeout into a phantom position."""
    broker = _broker_with_kite([_open()] * 10 + [_cancelled()])

    result = await broker.place_order("HDFCBANK", "SHORT", 2, order_type="MARKET")

    assert result.success is False
    assert result.status == "CANCELLED"
    assert result.quantity == 0.0


@pytest.mark.asyncio
async def test_unverifiable_order_is_flagged_not_silently_dropped():
    """
    If the post-cancel status read fails we cannot know whether we hold a
    position. Report a distinct UNKNOWN status telling the operator to check
    the broker, rather than a plain "cancelled" that reads as flat.
    """
    broker = _broker_with_kite(
        [_open()] * 10 + [Exception("network error during verification")]
    )

    result = await broker.place_order("HDFCBANK", "SHORT", 2, order_type="MARKET")

    assert result.success is False
    assert result.status == "UNKNOWN"
    assert "UNVERIFIED" in result.error_message


# ============================================================
# THE NORMAL PATH MUST BE UNAFFECTED
# ============================================================

@pytest.mark.asyncio
async def test_order_filled_during_polling_never_reaches_cancel():
    """A prompt fill returns immediately and must not cancel anything."""
    broker = _broker_with_kite([_complete(avg=733.70, qty=2)])

    result = await broker.place_order("HDFCBANK", "SHORT", 2, order_type="MARKET")

    assert result.success is True
    assert result.fill_price == 733.70
    broker._kite.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_disconnected_broker_places_nothing():
    broker = ZerodhaBroker()  # never connected

    result = await broker.place_order("HDFCBANK", "SHORT", 2)

    assert result.success is False
    assert result.error_message == "Broker not connected"
