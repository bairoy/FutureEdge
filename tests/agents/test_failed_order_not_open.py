"""
tests/agents/test_failed_order_not_open.py
============================================
A broker-rejected order must never be recorded as an open position.

WHY THIS EXISTS:
-----------------
`TradeRepo.save_trade()` defaults `status` to "OPEN", and `execution_node`
called it without that argument on every path — success or failure. So an
order the broker refused still wrote a row with status=OPEN and a NULL
broker_order_id.

Observed live on 2026-08-05: Kite rejected a real SHORT with "No IPs
configured for this app", and FutureEdge recorded an OPEN 2-share HDFCBANK
short that did not exist at the broker. `exit_monitor` then tried to
stop-loss the phantom every few seconds, each attempt failing the same way.

The failure mode is dangerous in both directions: the system believes it holds
risk it does not (blocking re-entry, skewing Kelly sizing and win-rate stats),
and a real position could be masked by the noise. Any broker rejection —
expired token, missing IP allowlist, insufficient margin — reaches this path.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.graph.state import MarketContext, PortfolioSnapshot, TradeProposal
from app.agents.execution_agent import execution_node
from app.brokers.base import OrderResult


@pytest.fixture(autouse=True)
def _kill_switch_off():
    """Execution routing is under test here, not the halt switch (fails closed)."""
    with patch(
        "app.services.kill_switch_service.is_trading_halted",
        new=AsyncMock(return_value=False),
    ):
        yield


def _live_execution_state() -> dict:
    """A HITL-approved live (non-paper) SHORT, mirroring the observed incident."""
    market_ctx = MagicMock(spec=MarketContext)
    market_ctx.symbol = "HDFCBANK"
    market_ctx.current_price = 733.65
    market_ctx.volatility_24h = 0.02
    market_ctx.regime = "TRENDING_DOWN"

    portfolio = MagicMock(spec=PortfolioSnapshot)
    portfolio.margin_available = 90000.0
    portfolio.total_equity = 100000.0
    portfolio.margin_used = 10000.0
    portfolio.unrealized_pnl = 0.0
    portfolio.exposure_ratio = 0.1

    proposal = TradeProposal(
        symbol="HDFCBANK",
        direction="SHORT",
        size=1467.30,
        entry_price=733.65,
        risk_score=0.3,
        agent_consensus=[],
        human_approved=True,
    )

    return {
        "user_id": "test_user_1",
        "symbol": "HDFCBANK",
        "market_context": market_ctx,
        "portfolio": portfolio,
        "signal_vote": None,
        "sentiment_vote": None,
        "risk_vote": None,
        "portfolio_vote": None,
        "consensus": proposal,
        "hitl_required": True,
        "hitl_status": "APPROVED",
        "executed_trade": None,
        "execution_error": None,
        "run_id": "test_fail1",
        "timestamp": "2026-08-05T14:22:00",
        "logs": [],
        "completed_nodes": [],
        "episodic_memory": [],
        "market_vector": None,
        "llm_rationale": None,
        "user_override_quantity": 2,
        "user_override_rupees": None,
        "override_kelly": False,
        "paper_trade": False,          # live broker path
    }


def _rejecting_broker() -> AsyncMock:
    """A broker that refuses the order the way Kite did during the incident."""
    broker = AsyncMock()
    broker.is_connected.return_value = True
    broker.get_ltp.return_value = 733.65
    broker.place_order.return_value = OrderResult(
        success       = False,
        order_id      = "",
        fill_price    = 0.0,
        quantity      = 0.0,
        status        = "ERROR",
        error_message = "No IPs configured for this app.",
    )
    return broker


@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
@patch("app.agents.execution_agent.redis_client")
@patch("app.agents.execution_agent.AsyncSessionLocal")
@patch("app.agents.execution_agent.TradeRepo")
@patch("app.agents.execution_agent.get_broker")
async def test_rejected_order_is_not_saved_as_open(
    mock_get_broker, mock_trade_repo, mock_db_session, mock_redis, mock_market_open
):
    """The regression itself: a refused order must be persisted as FAILED."""
    mock_get_broker.return_value = _rejecting_broker()

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    mock_db_session.return_value.__aenter__.return_value = mock_db

    await execution_node(_live_execution_state())

    mock_trade_repo.save_trade.assert_called_once()
    saved = mock_trade_repo.save_trade.call_args[1]

    assert saved["status"] == "FAILED", (
        "A broker-rejected order was persisted as an open position — "
        "exit_monitor will chase a phantom trade forever."
    )
    assert saved["status"] != "OPEN"

    # No order reached the exchange, so there is nothing to reference.
    assert saved["broker_order_id"] is None
    assert saved["actual_fill_price"] is None


@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
@patch("app.agents.execution_agent.redis_client")
@patch("app.agents.execution_agent.AsyncSessionLocal")
@patch("app.agents.execution_agent.TradeRepo")
@patch("app.agents.execution_agent.get_broker")
async def test_successful_order_still_opens_a_position(
    mock_get_broker, mock_trade_repo, mock_db_session, mock_redis, mock_market_open
):
    """
    The other half of the guard: the fix must not suppress real positions.
    A filled order still writes the default OPEN status (status=None).
    """
    broker = AsyncMock()
    broker.is_connected.return_value = True
    broker.get_ltp.return_value = 733.65
    broker.place_order.return_value = OrderResult(
        success    = True,
        order_id   = "250805000123456",
        fill_price = 733.60,
        quantity   = 2.0,
        status     = "COMPLETE",
    )
    mock_get_broker.return_value = broker

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    mock_db_session.return_value.__aenter__.return_value = mock_db

    await execution_node(_live_execution_state())

    saved = mock_trade_repo.save_trade.call_args[1]
    assert saved["status"] is None, "a filled order must keep save_trade's OPEN default"
    assert saved["broker_order_id"] == "250805000123456"
    assert saved["actual_fill_price"] == 733.60
