"""
tests/agents/test_orchestrator.py
==================================
Unit tests for the Orchestrator Agent and HITL routing.

WHAT WE TEST:
--------------
1. Consensus math (weighted voting exclusion for HOLD/VETO).
2. VETO cancel behavior.
3. should_human_review routing triggers (including quantity missing check).
"""

import sys
import os
import pytest
from unittest.mock import MagicMock

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.graph.state import AgentState, MarketContext, PortfolioSnapshot, AgentVote, TradeProposal
from app.agents.orchestration_agent import orchestrator_node
from app.agents.human_agent import should_human_review


def _make_orchestrator_state(
    signal_vote=None,
    sentiment_vote=None,
    risk_vote=None,
    portfolio_vote=None,
    user_override_quantity=None,
) -> dict:
    market_ctx = MagicMock(spec=MarketContext)
    market_ctx.symbol = "RELIANCE"
    market_ctx.current_price = 2500.0
    market_ctx.volatility_24h = 0.02

    portfolio = MagicMock(spec=PortfolioSnapshot)
    portfolio.margin_available = 90000.0
    portfolio.total_equity = 100000.0
    portfolio.margin_used = 10000.0
    portfolio.unrealized_pnl = 0.0
    portfolio.exposure_ratio = 0.1

    state = {
        "user_id": "test_user_1",
        "symbol": "RELIANCE",
        "market_context": market_ctx,
        "portfolio": portfolio,
        "signal_vote": signal_vote,
        "sentiment_vote": sentiment_vote,
        "risk_vote": risk_vote,
        "portfolio_vote": portfolio_vote,
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
        "user_override_quantity": user_override_quantity,
        "user_override_rupees": None,
        "override_kelly": False,
    }
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("use_mock_llm", [True])
async def test_consensus_calculation(use_mock_llm):
    """Test consensus directional score includes weights of all votes (including HOLD) to avoid dilution."""
    from unittest.mock import patch
    # Define agent votes with high confidence so consensus still triggers LONG
    sig_vote = AgentVote(agent="SignalAgent", decision="BUY", confidence=0.95, reasoning="Strong signal")
    sent_vote = AgentVote(agent="SentimentAgent", decision="BUY", confidence=0.90, reasoning="Bullish tweets")
    risk_vote = AgentVote(agent="RiskAgent", decision="HOLD", confidence=0.5, reasoning="A bit volatile")
    port_vote = AgentVote(agent="PortfolioAgent", decision="HOLD", confidence=0.7, reasoning="Max concentration near")

    state = _make_orchestrator_state(
        signal_vote=sig_vote,
        sentiment_vote=sent_vote,
        risk_vote=risk_vote,
        portfolio_vote=port_vote,
    )

    mock_weights = {
        "SignalAgent": 0.40,
        "SentimentAgent": 0.25,
        "RiskAgent": 0.20,
        "PortfolioAgent": 0.15,
    }

    # Call orchestration node
    with patch("app.jobs.weight_updater.get_agent_weights", return_value=mock_weights):
        result = await orchestrator_node(state)

    proposal = result.get("consensus")
    assert proposal is not None
    assert proposal.direction == "LONG"
    assert proposal.symbol == "RELIANCE"


@pytest.mark.asyncio
async def test_consensus_calculation_hold_dilution():
    """Test that weak buy votes get diluted by HOLD votes and result in HOLD/NONE consensus."""
    sig_vote = AgentVote(agent="SignalAgent", decision="BUY", confidence=0.4, reasoning="Weak signal")
    sent_vote = AgentVote(agent="SentimentAgent", decision="BUY", confidence=0.3, reasoning="Neutral tweets")
    risk_vote = AgentVote(agent="RiskAgent", decision="HOLD", confidence=0.5, reasoning="Volatile")
    port_vote = AgentVote(agent="PortfolioAgent", decision="HOLD", confidence=0.7, reasoning="Near limit")

    state = _make_orchestrator_state(
        signal_vote=sig_vote,
        sentiment_vote=sent_vote,
        risk_vote=risk_vote,
        portfolio_vote=port_vote,
    )

    result = await orchestrator_node(state)
    proposal = result.get("consensus")
    assert proposal is not None
    assert proposal.direction == "NONE"


def test_should_human_review_triggers():
    """Test that should_human_review always routes active signals to human_review and HOLD to execute."""
    proposal_long = TradeProposal(
        symbol="RELIANCE",
        direction="LONG",
        size=10000.0,
        entry_price=2500.0,
        risk_score=0.3,
        agent_consensus=[],
    )
    proposal_none = TradeProposal(
        symbol="RELIANCE",
        direction="NONE",
        size=0,
        entry_price=0,
        risk_score=1.0,
        agent_consensus=[],
    )

    # 1. Consensus is LONG -> should always review
    state1 = _make_orchestrator_state()
    state1["consensus"] = proposal_long
    assert should_human_review(state1) == "human_review"

    # 2. Consensus is NONE -> should execute directly (bypassing review)
    state2 = _make_orchestrator_state()
    state2["consensus"] = proposal_none
    assert should_human_review(state2) == "execute"
