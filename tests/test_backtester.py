import sys
import os
import pytest

# Add backend directory to sys.path so app imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from unittest.mock import patch
from app.graph.backtester import run_backtest, calculate_transaction_cost


def test_calculate_transaction_cost():
    # Buy transaction value: 100,000
    cost_buy = calculate_transaction_cost(100000.0, is_buy=True)
    # Sell transaction value: 100,000
    cost_sell = calculate_transaction_cost(100000.0, is_buy=False)

    assert cost_buy > 0
    assert cost_sell > 0
    assert cost_buy != cost_sell  # stamp duty and STT differ


@pytest.mark.asyncio
@patch("app.graph.backtester.load_historical_candles")
async def test_run_backtest(mock_load):
    # Mock a steady uptrend to trigger BUY signals and win trades
    mock_candles = []
    base_price = 100.0
    for i in range(50):
        price = base_price + i  # Uptrend
        mock_candles.append({
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price + 0.2,
            "volume": 1000,
            "timestamp": f"2026-05-29T09:{i:02d}:00",
        })

    mock_load.return_value = mock_candles

    result = await run_backtest(
        symbol="RELIANCE",
        period="5d",
        interval="1m",
        initial_capital=10000.0,
        stop_loss_pct=1.0,
        take_profit_pct=3.0,
        size_pct=50.0,
        slippage_pct=0.0,
    )

    assert result["symbol"] == "RELIANCE"
    assert "metrics" in result
    assert "trades" in result
    assert "equity_curve" in result
    assert result["metrics"]["initial_capital"] == 10000.0
