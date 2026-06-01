import sys
import os
import pytest
import numpy as np
from unittest.mock import MagicMock

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.agents.regime_agent import regime_agent_node


def _make_regime_state(candles: list[dict] = None) -> dict:
    from app.graph.state import MarketContext

    market_ctx = MagicMock(spec=MarketContext)
    market_ctx.symbol = "RELIANCE"
    market_ctx.ohlcv_1m = candles if candles is not None else []
    market_ctx.regime = "RANGEBOUND"
    market_ctx.volatility_24h = 0.02

    state = {
        "market_context": market_ctx,
        "completed_nodes": [],
        "logs": [],
    }
    return state


@pytest.mark.asyncio
async def test_regime_agent_insufficient_candles():
    # Test with 10 candles (less than 30)
    candles = [{"close": 100.0, "high": 101.0, "low": 99.0, "open": 100.0, "volume": 100} for _ in range(10)]
    state = _make_regime_state(candles)

    result = await regime_agent_node(state)
    assert result["market_context"].regime == "RANGEBOUND"
    assert result["market_context"].volatility_24h == 0.02
    assert "regime_agent" in result["completed_nodes"]


@pytest.mark.asyncio
async def test_regime_agent_volatility_scaling():
    # Test with 50 candles where returns have a constant std dev
    # Create alternating changes to yield a known return standard deviation
    candles = []
    # Base price changes to give exact log returns
    # e.g., prices alternating between 100.0 and 100.1
    for i in range(50):
        price = 100.0 if i % 2 == 0 else 100.1
        candles.append({
            "open": price,
            "high": price + 0.1,
            "low": price - 0.1,
            "close": price,
            "volume": 1000,
        })

    state = _make_regime_state(candles)
    result = await regime_agent_node(state)

    # Let's calculate what raw log returns std would be
    closes = np.array([c["close"] for c in candles])
    returns = np.log(closes[1:] / closes[:-1])
    expected_raw_vol = float(np.std(returns[-30:], ddof=1))
    expected_scaled_vol = expected_raw_vol * np.sqrt(375)

    assert result["market_context"].volatility_24h == pytest.approx(expected_scaled_vol, rel=1e-5)


@pytest.mark.asyncio
async def test_regime_agent_wilder_smoothing_trending():
    # Let's simulate a strong uptrend to trigger TRENDING_UP classification
    # Price rises smoothly
    candles = []
    for i in range(50):
        price = 100.0 + i * 0.5
        candles.append({
            "open": price - 0.1,
            "high": price + 0.4,
            "low": price - 0.2,
            "close": price,
            "volume": 1000,
        })

    state = _make_regime_state(candles)
    result = await regime_agent_node(state)

    # Verify that the trend is detected and set
    assert result["market_context"].regime in ("TRENDING_UP", "RANGEBOUND")
    # Verify volatility_24h is calculated
    assert result["market_context"].volatility_24h > 0


@pytest.mark.asyncio
async def test_regime_agent_exception_fallback():
    # Test with invalid data format to trigger exception
    candles = [{"invalid": "format"} for _ in range(50)]
    state = _make_regime_state(candles)

    result = await regime_agent_node(state)
    assert result["market_context"].regime == "RANGEBOUND"
    assert result["market_context"].volatility_24h == 0.02
    assert "regime_agent" in result["completed_nodes"]
