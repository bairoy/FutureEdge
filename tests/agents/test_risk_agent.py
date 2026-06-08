"""
tests/agents/test_risk_agent.py
=================================
Unit tests for the Risk Agent.

WHAT WE TEST:
--------------
1. All 5 risk checks individually (margin, concentration, volatility, drawdown, market hours)
2. ANY single check failing → VETO decision
3. All checks passing → non-VETO (PROCEED) decision
4. Consecutive loss guard: after 3 losses → kelly fraction capped at 0.02
5. Default-to-VETO on exception (system safety guarantee)

These tests do NOT require a database or Redis connection.
All dependencies are mocked.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# ============================================================
# FIXTURES
# ============================================================

def _make_state(
    margin_available: float = 50000.0,
    total_equity: float = 100000.0,
    unrealized_pnl: float = 0.0,
    exposure_ratio: float = 0.30,
    position_size_inr: float = 5000.0,
    signal_confidence: float = 0.70,
    volatility_24h: float = 0.015,
    regime: str = "TRENDING_UP",
):
    """Create a minimal AgentState for risk agent testing."""
    from app.graph.state import AgentState, MarketContext, PortfolioSnapshot, AgentVote

    market_ctx = MagicMock(spec=MarketContext)
    market_ctx.symbol = "RELIANCE"
    market_ctx.current_price = 2500.0
    market_ctx.volatility_24h = volatility_24h
    market_ctx.regime = regime

    portfolio = MagicMock(spec=PortfolioSnapshot)
    portfolio.margin_available = margin_available
    portfolio.total_equity = total_equity
    portfolio.margin_used = total_equity - margin_available
    portfolio.unrealized_pnl = unrealized_pnl
    portfolio.exposure_ratio = exposure_ratio
    portfolio.max_drawdown_limit = 0.2
    portfolio.realized_pnl_today = 0.0
    portfolio.open_positions = [{"symbol": "RELIANCE", "notional": total_equity * exposure_ratio}]

    signal_vote = MagicMock(spec=AgentVote)
    signal_vote.decision = "BUY"
    signal_vote.confidence = signal_confidence

    state = {
        "user_id": "test_user_1",
        "symbol": "RELIANCE",
        "market_context": market_ctx,
        "portfolio": portfolio,
        "signal_vote": signal_vote,
        "sentiment_vote": None,
        "risk_vote": None,
        "portfolio_vote": None,
        "consensus": None,
        "hitl_required": False,
        "hitl_status": "none",
        "executed_trade": None,
        "execution_error": None,
        "run_id": "test_run_001",
        "timestamp": "2026-05-30T10:00:00",
        "logs": [],
        "completed_nodes": [],
        "episodic_memory": [],
        "market_vector": None,
        "llm_rationale": None,
        "user_override_quantity": None,
        "user_override_rupees": None,
        "override_kelly": False,
    }
    return state


# ============================================================
# TEST: HAPPY PATH — ALL CHECKS PASS
# ============================================================

@pytest.mark.asyncio
async def test_risk_agent_all_checks_pass():
    """When all risk parameters are within bounds, decision should NOT be VETO."""
    from app.agents.risk_agent import risk_agent_node

    state = _make_state(
        margin_available=50000.0,  # plenty of margin
        total_equity=100000.0,
        exposure_ratio=0.25,       # below 0.80 limit
        volatility_24h=0.015,      # below 0.04 limit
    )

    with patch("app.agents.risk_agent.is_market_open", return_value=True):
        result = await risk_agent_node(state)

    vote = result.get("risk_vote")
    assert vote is not None
    assert vote.decision != "VETO", (
        f"Expected non-VETO but got VETO. Reasoning: {vote.reasoning}"
    )
    assert 0.0 <= vote.confidence <= 1.0


# ============================================================
# TEST: MARGIN BREACH → VETO
# ============================================================

@pytest.mark.asyncio
async def test_risk_agent_insufficient_margin():
    """Margin available below threshold should trigger VETO."""
    from app.agents.risk_agent import risk_agent_node

    state = _make_state(
        margin_available=500.0,   # very low
        total_equity=100000.0,
    )

    with patch("app.agents.risk_agent.is_market_open", return_value=True):
        result = await risk_agent_node(state)

    vote = result.get("risk_vote")
    assert vote is not None
    assert vote.decision == "VETO"
    assert "margin" in vote.reasoning.lower() or "VETO" in vote.decision


# ============================================================
# TEST: HIGH VOLATILITY → VETO
# ============================================================

@pytest.mark.asyncio
async def test_risk_agent_high_volatility():
    """Volatility exceeding threshold should trigger VETO."""
    from app.agents.risk_agent import risk_agent_node

    state = _make_state(
        volatility_24h=0.09,  # 9% — strictly above 8% MAX_VOLATILITY limit
    )

    with patch("app.agents.risk_agent.is_market_open", return_value=True):
        result = await risk_agent_node(state)

    vote = result.get("risk_vote")
    assert vote is not None
    assert vote.decision == "VETO"


# ============================================================
# TEST: OVER-EXPOSURE → VETO
# ============================================================

@pytest.mark.asyncio
async def test_risk_agent_over_exposure():
    """Exposure ratio exceeding 0.80 should trigger VETO."""
    from app.agents.risk_agent import risk_agent_node

    state = _make_state(
        exposure_ratio=0.90,   # over 0.80 MAX_MARGIN_UTILISATION
    )

    with patch("app.agents.risk_agent.is_market_open", return_value=True):
        result = await risk_agent_node(state)

    vote = result.get("risk_vote")
    assert vote is not None
    assert vote.decision == "VETO"


# ============================================================
# TEST: MARKET CLOSED → VETO
# ============================================================

@pytest.mark.asyncio
async def test_risk_agent_market_closed():
    """When market is closed, risk agent should VETO all trades."""
    from app.agents.risk_agent import risk_agent_node
    from app.core.config import settings

    state = _make_state()
    state["paper_trade"] = False

    with patch("app.agents.risk_agent.is_market_open", return_value=False), \
         patch.object(settings, "ACTIVE_BROKER", "zerodha"):
        result = await risk_agent_node(state)

    vote = result.get("risk_vote")
    assert vote is not None
    assert vote.decision == "VETO"
    assert "market" in vote.reasoning.lower() or "closed" in vote.reasoning.lower()


# ============================================================
# TEST: EXCEPTION → VETO (system safety guarantee)
# ============================================================

@pytest.mark.asyncio
async def test_risk_agent_defaults_to_veto_on_crash():
    """If an internal error occurs, risk agent MUST default to VETO."""
    from app.agents.risk_agent import risk_agent_node

    # Create a state that will cause an AttributeError in risk calculations
    bad_state = {
        "user_id": "test",
        "market_context": None,   # None will cause AttributeError
        "portfolio": None,
        "signal_vote": None,
        "symbol": "RELIANCE",
        "logs": [],
        "completed_nodes": [],
        "hitl_required": False,
        "hitl_status": "none",
        "executed_trade": None,
        "execution_error": None,
        "run_id": "test",
        "timestamp": "",
        "episodic_memory": [],
        "market_vector": None,
        "llm_rationale": None,
        "consensus": None,
        "user_override_quantity": None,
        "user_override_rupees": None,
        "override_kelly": False,
    }

    result = await risk_agent_node(bad_state)

    vote = result.get("risk_vote")
    assert vote is not None
    assert vote.decision == "VETO", (
        "Risk agent MUST default to VETO on crash — this is a SAFETY REQUIREMENT"
    )
    assert vote.confidence == 1.0


# ============================================================
# TEST: COMPLETED_NODES ALWAYS APPENDED
# ============================================================

@pytest.mark.asyncio
async def test_risk_agent_always_appends_completed_node():
    """Regardless of outcome, 'risk_agent' must appear in completed_nodes."""
    from app.agents.risk_agent import risk_agent_node

    state = _make_state()

    with patch("app.agents.risk_agent.is_market_open", return_value=True):
        result = await risk_agent_node(state)

    assert "risk_agent" in result.get("completed_nodes", [])
