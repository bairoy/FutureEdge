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


@pytest.mark.asyncio
@patch("app.graph.backtester.load_historical_candles")
async def test_same_candle_reentry_blocked(mock_load):
    # Simulate a scenario where candle 31 has a BUY signal AND triggers a stop loss.
    # The backtester should exit, and NOT re-enter on the same candle.
    mock_candles = []
    
    # 35 initial alternating candles to keep RSI around 50
    for i in range(35):
        price = 100.0 if i % 2 == 0 else 100.1
        mock_candles.append({
            "open": price,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": 1000,
            "timestamp": f"2026-05-30T09:{i:02d}:00",
        })
    
    # Candle 35 (index 35): Drop price to trigger oversold RSI (<30)
    mock_candles.append({
        "open": 100.0,
        "high": 100.0,
        "low": 80.0,
        "close": 80.0,
        "volume": 1000,
        "timestamp": "2026-05-30T09:35:00",
    })
    
    # Candle 36 (index 36): Entry candle. Opens at 80.
    mock_candles.append({
        "open": 80.0,
        "high": 80.0,
        "low": 80.0,
        "close": 80.0,
        "volume": 1000,
        "timestamp": "2026-05-30T09:36:00",
    })

    # Candle 37 (index 37): Stop loss candle. Drops low to 50.
    mock_candles.append({
        "open": 80.0,
        "high": 80.0,
        "low": 50.0,
        "close": 80.0,
        "volume": 1000,
        "timestamp": "2026-05-30T09:37:00",
    })

    # Candle 38 (index 38): Extra candle
    mock_candles.append({
        "open": 80.0,
        "high": 80.0,
        "low": 80.0,
        "close": 80.0,
        "volume": 1000,
        "timestamp": "2026-05-30T09:38:00",
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
        trailing_stop_enabled=False,
    )

    trades = result["trades"]
    # We should have 2 trades:
    # 1. Enters at 09:36:00, exits at 09:37:00 (TAKE_PROFIT)
    # 2. Enters at 09:38:00, exits at 09:38:00 (END_OF_DATA)
    # Crucially, it did NOT re-enter at 09:37:00 when it exited.
    assert len(trades) == 2
    assert trades[0]["entry_time"] == "2026-05-30T09:36:00"
    assert trades[0]["exit_time"] == "2026-05-30T09:37:00"
    assert trades[0]["exit_reason"] == "TAKE_PROFIT"
    assert trades[1]["entry_time"] == "2026-05-30T09:38:00"
    assert trades[1]["exit_reason"] == "END_OF_DATA"

