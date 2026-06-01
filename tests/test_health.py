import sys
import os
import pytest

# Add backend directory to sys.path so app imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from unittest.mock import patch
from app.main import health


@pytest.mark.asyncio
@patch("app.main.check_postgres")
@patch("app.main.check_redis")
@patch("app.main.check_qdrant")
async def test_health_all_ok(mock_qdrant, mock_redis, mock_postgres):
    mock_postgres.return_value = True
    mock_redis.return_value = True
    mock_qdrant.return_value = True

    response = await health()
    assert response["status"] == "ok"
    assert response["dependencies"]["postgres"] == "ok"
    assert response["dependencies"]["redis"] == "ok"
    assert response["dependencies"]["qdrant"] == "ok"


@pytest.mark.asyncio
@patch("app.main.check_postgres")
@patch("app.main.check_redis")
@patch("app.main.check_qdrant")
async def test_health_degraded(mock_qdrant, mock_redis, mock_postgres):
    mock_postgres.return_value = False
    mock_redis.return_value = True
    mock_qdrant.return_value = True

    response = await health()
    assert response["status"] == "degraded"
    assert response["dependencies"]["postgres"] == "down"
    assert response["dependencies"]["redis"] == "ok"
    assert response["dependencies"]["qdrant"] == "ok"


def test_state_serialization():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from app.graph.state import MarketContext, PortfolioSnapshot, AgentVote, TradeProposal

    serde = JsonPlusSerializer(
        pickle_fallback=True,
        allowed_msgpack_modules=[
            ("app.graph.state", "MarketContext"),
            ("app.graph.state", "PortfolioSnapshot"),
            ("app.graph.state", "AgentVote"),
            ("app.graph.state", "TradeProposal"),
        ]
    )

    # Instantiate the objects
    vote = AgentVote(agent="SignalAgent", decision="BUY", confidence=0.9, reasoning="Test reason")
    proposal = TradeProposal(
        symbol="RELIANCE",
        direction="LONG",
        size=10000.0,
        entry_price=2500.0,
        risk_score=0.1,
        agent_consensus=[vote]
    )
    portfolio = PortfolioSnapshot(
        total_equity=100000.0,
        margin_used=10000.0,
        margin_available=90000.0,
        unrealized_pnl=0.0
    )
    context = MarketContext(
        symbol="RELIANCE",
        current_price=2500.0,
        ohlcv_1m=[]
    )

    for obj in [vote, proposal, portfolio, context]:
        dumped = serde.dumps_typed(obj)
        loaded = serde.loads_typed(dumped)
        assert type(loaded) == type(obj)
        assert getattr(loaded, "symbol", None) == getattr(obj, "symbol", None)

