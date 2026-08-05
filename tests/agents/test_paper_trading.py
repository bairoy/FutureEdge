"""
tests/agents/test_paper_trading.py
==================================
Unit tests for paper trading execution and exit monitoring.

WHAT WE TEST:
--------------
1. execution_node respects paper_trade flag, routes to MockBroker, and saves trade as 'paper'.
2. exit_monitor respects paper trade broker attribute and exits simulated positions using MockBroker.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.graph.state import AgentState, MarketContext, PortfolioSnapshot, TradeProposal, AgentVote
from app.agents.execution_agent import execution_node
from app.jobs.exit_monitor import ExitMonitor


@pytest.fixture(autouse=True)
def _kill_switch_off():
    """
    These tests exercise execution routing, not the kill switch.

    The halt check fails CLOSED, so with no Redis reachable in a unit test run
    it reports HALTED and every execution gets blocked. Say explicitly that
    trading is not halted, so the assertions below test what they mean to.
    """
    with patch(
        "app.services.kill_switch_service.is_trading_halted",
        new=AsyncMock(return_value=False),
    ):
        yield


def _make_execution_state(
    direction: str = "LONG",
    size: float = 10000.0,
    entry_price: float = 2500.0,
    user_override_quantity: int = 10,
    paper_trade: bool = True,
) -> dict:
    market_ctx = MagicMock(spec=MarketContext)
    market_ctx.symbol = "RELIANCE"
    market_ctx.current_price = entry_price
    market_ctx.volatility_24h = 0.02
    market_ctx.regime = "TRENDING_UP"

    portfolio = MagicMock(spec=PortfolioSnapshot)
    portfolio.margin_available = 90000.0
    portfolio.total_equity = 100000.0
    portfolio.margin_used = 10000.0
    portfolio.unrealized_pnl = 0.0
    portfolio.exposure_ratio = 0.1

    proposal = TradeProposal(
        symbol=market_ctx.symbol,
        direction=direction,
        size=size,
        entry_price=entry_price,
        risk_score=0.3,
        agent_consensus=[],
        human_approved=True,
    )

    state = {
        "user_id": "test_user_1",
        "symbol": market_ctx.symbol,
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
        "run_id": "test_run_001",
        "timestamp": "2026-05-30T10:00:00",
        "logs": [],
        "completed_nodes": [],
        "episodic_memory": [],
        "market_vector": None,
        "llm_rationale": None,
        "user_override_quantity": user_override_quantity,
        "user_override_rupees": None,
        "override_kelly": False,
        "paper_trade": paper_trade,
    }
    return state


@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
@patch("app.agents.execution_agent.redis_client")
@patch("app.agents.execution_agent.AsyncSessionLocal")
@patch("app.agents.execution_agent.TradeRepo")
async def test_paper_trade_execution_routing(mock_trade_repo, mock_db_session, mock_redis, mock_market_open):
    """Confirm that execution_node uses MockBroker when paper_trade=True."""
    state = _make_execution_state(paper_trade=True)

    # Set up mock DB session context manager
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    mock_db_session.return_value.__aenter__.return_value = mock_db

    # Execute node
    result = await execution_node(state)

    # Ensure order result succeeded and executed_trade details are present
    assert result["execution_error"] is None
    assert result["executed_trade"] is not None
    assert result["executed_trade"]["broker"] == "paper"

    # Confirm it saved trade to DB as paper broker
    mock_trade_repo.save_trade.assert_called_once()
    saved_kwargs = mock_trade_repo.save_trade.call_args[1]
    assert saved_kwargs["broker"] == "paper"


@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
@patch("app.agents.execution_agent.redis_client")
@patch("app.agents.execution_agent.AsyncSessionLocal")
@patch("app.agents.execution_agent.TradeRepo")
async def test_paper_trade_reversal_exit(mock_trade_repo, mock_db_session, mock_redis, mock_market_open):
    """Confirm that execution_node triggers a reversal exit when the proposal has an opposite direction."""
    existing_trade = MagicMock()
    existing_trade.id = "trade_1"
    existing_trade.direction = "LONG"
    existing_trade.quantity = 10
    existing_trade.symbol = "RELIANCE"
    existing_trade.entry_price = 2500.0
    existing_trade.run_id = "test_run_001"
    existing_trade.broker = "paper"

    state = _make_execution_state(direction="SHORT", paper_trade=True)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_trade
    mock_db.execute.return_value = mock_result
    mock_db.get.return_value = existing_trade
    mock_db_session.return_value.__aenter__.return_value = mock_db

    closed_trade = MagicMock()
    closed_trade.realized_pnl = -10.0
    closed_trade.pnl_pct = -0.04
    mock_trade_repo.close_trade = AsyncMock(return_value=closed_trade)

    with patch("app.brokers.mock.MockBroker") as MockBrokerClass:
        mock_broker = MagicMock()
        mock_broker.is_connected = AsyncMock(return_value=True)
        mock_broker.connect = AsyncMock()
        
        order_res = MagicMock()
        order_res.success = True
        order_res.fill_price = 2499.0
        order_res.order_id = "ORDER_EXIT_123"
        mock_broker.place_order = AsyncMock(return_value=order_res)
        MockBrokerClass.return_value = mock_broker

        result = await execution_node(state)

        assert result["execution_error"] is None
        assert "completed_nodes" in result
        
        mock_trade_repo.save_trade.assert_called_once()
        mock_trade_repo.close_trade.assert_called_once()
        assert mock_broker.place_order.call_count == 2


@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
@patch("app.agents.execution_agent.redis_client")
@patch("app.agents.execution_agent.AsyncSessionLocal")
@patch("app.agents.execution_agent.TradeRepo")
async def test_paper_trade_same_direction_scale_in(mock_trade_repo, mock_db_session, mock_redis, mock_market_open):
    """Confirm that execution_node executes a scale-in order when signal is in the same direction."""
    existing_trade = MagicMock()
    existing_trade.id = "trade_1"
    existing_trade.direction = "LONG"
    existing_trade.quantity = 10
    existing_trade.symbol = "RELIANCE"
    existing_trade.entry_price = 2500.0
    existing_trade.broker = "paper"
    existing_trade.agent_consensus = []

    state = _make_execution_state(direction="LONG", user_override_quantity=5, paper_trade=True)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_trade
    mock_db.execute.return_value = mock_result
    mock_db.get.return_value = existing_trade
    mock_db_session.return_value.__aenter__.return_value = mock_db

    with patch("app.brokers.mock.MockBroker") as MockBrokerClass:
        mock_broker = MagicMock()
        mock_broker.is_connected = AsyncMock(return_value=True)
        mock_broker.connect = AsyncMock()
        
        order_res = MagicMock()
        order_res.success = True
        order_res.fill_price = 2510.0
        order_res.order_id = "ORDER_SCALE_123"
        mock_broker.place_order = AsyncMock(return_value=order_res)
        MockBrokerClass.return_value = mock_broker

        result = await execution_node(state)

        assert result["execution_error"] is None
        assert result["executed_trade"] is not None
        assert "Scaled in LONG position" in result["logs"][0]
        
        # New weighted entry price: ((2500 * 10) + (2510 * 5)) / 15 = 2503.3333
        assert existing_trade.quantity == 15
        assert existing_trade.entry_price == 2503.3333
        assert len(existing_trade.agent_consensus) == 1
        assert existing_trade.agent_consensus[0]["type"] == "scale_in"
        assert existing_trade.agent_consensus[0]["added_quantity"] == 5
        assert existing_trade.agent_consensus[0]["fill_price"] == 2510.0


@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
@patch("app.agents.execution_agent.redis_client")
@patch("app.agents.execution_agent.AsyncSessionLocal")
@patch("app.agents.execution_agent.TradeRepo")
async def test_paper_trade_scale_in_max_limit_blocked(mock_trade_repo, mock_db_session, mock_redis, mock_market_open):
    """Confirm that execution_node bypasses execution when max scale-in entries limit is reached."""
    existing_trade = MagicMock()
    existing_trade.id = "trade_1"
    existing_trade.direction = "LONG"
    existing_trade.quantity = 10
    existing_trade.symbol = "RELIANCE"
    existing_trade.entry_price = 2500.0
    existing_trade.broker = "paper"
    # 3 scale-in events inside agent_consensus (MAX_PYRAMID_ENTRIES = 3)
    existing_trade.agent_consensus = [
        {"type": "scale_in"},
        {"type": "scale_in"},
        {"type": "scale_in"}
    ]

    state = _make_execution_state(direction="LONG", user_override_quantity=5, paper_trade=True)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_trade
    mock_db.execute.return_value = mock_result
    mock_db_session.return_value.__aenter__.return_value = mock_db

    result = await execution_node(state)

    assert result["executed_trade"] is None
    assert "max scale-in limit reached" in result["logs"][0]
    mock_trade_repo.save_trade.assert_not_called()


@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
@patch("app.agents.execution_agent.redis_client")
@patch("app.agents.execution_agent.AsyncSessionLocal")
@patch("app.agents.execution_agent.TradeRepo")
async def test_paper_trade_scale_in_exposure_limit_blocked(mock_trade_repo, mock_db_session, mock_redis, mock_market_open):
    """Confirm that execution_node bypasses execution when max symbol exposure cap is reached."""
    existing_trade = MagicMock()
    existing_trade.id = "trade_1"
    existing_trade.direction = "LONG"
    existing_trade.quantity = 15
    existing_trade.symbol = "RELIANCE"
    existing_trade.entry_price = 2500.0
    existing_trade.broker = "paper"
    existing_trade.agent_consensus = []

    # Try to add 10 shares at 2500.0 -> total 25 shares. Total exposure = 62500.0. Limit is MAX_SYMBOL_EXPOSURE = 50000.0
    state = _make_execution_state(direction="LONG", user_override_quantity=10, paper_trade=True)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_trade
    mock_db.execute.return_value = mock_result
    mock_db_session.return_value.__aenter__.return_value = mock_db

    result = await execution_node(state)

    assert result["executed_trade"] is None
    assert "max exposure limit reached" in result["logs"][0]
    mock_trade_repo.save_trade.assert_not_called()


@pytest.mark.asyncio
@patch("app.jobs.exit_monitor.get_broker")
@patch("app.jobs.exit_monitor.AsyncSessionLocal")
@patch("app.jobs.exit_monitor.TradeRepo")
@patch("app.jobs.exit_monitor.redis_client")
async def test_paper_trade_exit_monitoring(mock_redis, mock_trade_repo, mock_db_session, mock_get_broker):
    """Confirm exit monitor uses MockBroker for trades with broker='paper'."""
    monitor = ExitMonitor()

    # Create mock trade ORM model
    trade = MagicMock()
    trade.id = "trade_uuid_123"
    trade.user_id = "test_user_1"
    trade.symbol = "RELIANCE"
    trade.direction = "LONG"
    trade.quantity = 10
    trade.entry_price = 2500.0
    trade.stop_loss = 2450.0
    trade.take_profit = 2600.0
    trade.broker = "paper"

    # Mock DB close trade response
    closed_trade = MagicMock()
    closed_trade.realized_pnl = 100.0
    closed_trade.pnl_pct = 4.0
    closed_trade.broker = "paper"
    mock_trade_repo.close_trade = AsyncMock(return_value=closed_trade)

    # Execute exit monitor exit trigger
    with patch("app.brokers.mock.MockBroker") as MockBrokerClass:
        mock_mock_broker = AsyncMock()
        MockBrokerClass.return_value = mock_mock_broker
        
        # Call exit execution
        await monitor._execute_exit(trade, 2550.0, "TAKE_PROFIT")

        # Verify MockBroker was instantiated and place_order was called
        MockBrokerClass.assert_called_once()
        mock_mock_broker.place_order.assert_called_once()
        
        # Verify real get_broker was NOT called for ordering
        mock_get_broker.assert_not_called()


@pytest.mark.asyncio
@patch("app.brokers.symbol_mapper.is_market_open", return_value=True)
@patch("app.agents.execution_agent.redis_client")
@patch("app.agents.execution_agent.AsyncSessionLocal")
@patch("app.agents.execution_agent.TradeRepo")
async def test_paper_trade_consensus_exit(mock_trade_repo, mock_db_session, mock_redis, mock_market_open):
    """Confirm that execution_node triggers a consensus exit when proposal direction is CLOSE."""
    existing_trade = MagicMock()
    existing_trade.id = "trade_1"
    existing_trade.direction = "LONG"
    existing_trade.quantity = 10
    existing_trade.symbol = "RELIANCE"
    existing_trade.entry_price = 2500.0
    existing_trade.broker = "paper"
    existing_trade.agent_consensus = []

    state = _make_execution_state(direction="CLOSE", paper_trade=True)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_trade
    mock_db.execute.return_value = mock_result
    mock_db.get.return_value = existing_trade
    mock_db_session.return_value.__aenter__.return_value = mock_db

    closed_trade = MagicMock()
    closed_trade.realized_pnl = 100.0
    closed_trade.pnl_pct = 4.0
    mock_trade_repo.close_trade = AsyncMock(return_value=closed_trade)

    with patch("app.brokers.mock.MockBroker") as MockBrokerClass:
        mock_broker = MagicMock()
        mock_broker.is_connected = AsyncMock(return_value=True)
        mock_broker.connect = AsyncMock()
        
        order_res = MagicMock()
        order_res.success = True
        order_res.fill_price = 2510.0
        order_res.order_id = "ORDER_EXIT_123"
        mock_broker.place_order = AsyncMock(return_value=order_res)
        MockBrokerClass.return_value = mock_broker

        result = await execution_node(state)

        assert result["execution_error"] is None
        assert result["executed_trade"] is not None
        assert result["executed_trade"]["status"] == "CLOSED"
        assert "Closed active LONG position" in result["logs"][0]
        
        mock_trade_repo.close_trade.assert_called_once_with(
            session=mock_db,
            trade_id=existing_trade.id,
            user_id="test_user_1",
            exit_price=2510.0,
        )
        mock_trade_repo.save_trade.assert_not_called()
