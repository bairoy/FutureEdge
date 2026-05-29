"""
app/graph/backtester.py
========================
Event-driven backtesting engine for FutureEdge.

Runs the multi-agent consensus algorithm (pure mathematical components)
over historical candles fetched via yfinance to evaluate performance.
"""

import math
from datetime import datetime
from loguru import logger

from app.data.feed import load_historical_candles
from app.data.indicator_cache import _compute_indicators


async def run_backtest(
    symbol: str,
    period: str,
    interval: str,
    initial_capital: float = 100000.0,
    stop_loss_pct: float = 1.5,
    take_profit_pct: float = 3.0,
    size_pct: float = 10.0,
) -> dict:
    """
    Run a historical simulation on OHLCV candles.

    Bypasses OpenAI LLM reasoning calls to keep execution fast and free,
    using the exact indicator logic of SignalAgent and risk parameters.
    """

    # 1. Fetch candles
    candles = load_historical_candles(symbol, period=period, interval=interval)
    if not candles or len(candles) < 35:
        logger.warning(f"Insufficient historical candles for backtesting {symbol}")
        return {
            "symbol": symbol,
            "period": period,
            "interval": interval,
            "metrics": {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "profit_factor": 1.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
            },
            "trades": [],
            "equity_curve": [],
        }

    # 2. State setup
    capital = initial_capital
    position = 0  # Number of shares (positive for LONG, negative for SHORT)
    entry_price = 0.0
    entry_time = None
    direction = "NONE"  # "LONG" | "SHORT" | "NONE"
    trades = []
    equity_curve = []

    # Prepare historical sliding window
    for t in range(30, len(candles)):
        current_candle = candles[t]
        prev_candle = candles[t - 1]
        
        # Slices candles up to t-1 to compute indicators before current candle open
        window = candles[:t]
        ind = _compute_indicators(window)
        
        # We trade at the open price of the current candle
        exec_price = current_candle["open"]
        timestamp = current_candle["timestamp"]

        # --- Indicator scoring (identical to SignalAgent) ---
        score = 0.0
        rsi = ind.get("rsi", 50.0)
        macd_val = ind.get("macd", 0.0)
        signal_val = ind.get("macd_signal", 0.0)
        hist = ind.get("macd_hist", 0.0)
        bollinger_upper = ind.get("bollinger_upper", exec_price)
        bollinger_lower = ind.get("bollinger_lower", exec_price)

        if rsi < 30:
            score += 0.3
        elif rsi > 70:
            score -= 0.3

        if hist > 0 and macd_val > signal_val:
            score += 0.25
        elif hist < 0 and macd_val < signal_val:
            score -= 0.25

        if exec_price < bollinger_lower:
            score += 0.2
        elif exec_price > bollinger_upper:
            score -= 0.2

        # Make decision
        decision = "HOLD"
        if score > 0.2:
            decision = "BUY"
        elif score < -0.2:
            decision = "SELL"

        # --- Check exit conditions (Stop-Loss and Take-Profit) ---
        exit_triggered = False
        exit_price = 0.0
        exit_reason = ""

        if position > 0:  # LONG
            sl_price = entry_price * (1.0 - stop_loss_pct / 100.0)
            tp_price = entry_price * (1.0 + take_profit_pct / 100.0)
            
            if current_candle["low"] <= sl_price:
                exit_triggered = True
                exit_price = sl_price
                exit_reason = "STOP_LOSS"
            elif current_candle["high"] >= tp_price:
                exit_triggered = True
                exit_price = tp_price
                exit_reason = "TAKE_PROFIT"
                
        elif position < 0:  # SHORT
            sl_price = entry_price * (1.0 + stop_loss_pct / 100.0)
            tp_price = entry_price * (1.0 - take_profit_pct / 100.0)
            
            if current_candle["high"] >= sl_price:
                exit_triggered = True
                exit_price = sl_price
                exit_reason = "STOP_LOSS"
            elif current_candle["low"] <= tp_price:
                exit_triggered = True
                exit_price = tp_price
                exit_reason = "TAKE_PROFIT"

        if exit_triggered:
            # Calculate PnL
            if position > 0:
                pnl = position * (exit_price - entry_price)
            else:
                pnl = abs(position) * (entry_price - exit_price)

            capital += pnl
            pnl_pct = (pnl / (abs(position) * entry_price)) * 100.0 if position != 0 else 0.0
            
            trades.append({
                "symbol": symbol,
                "direction": "LONG" if position > 0 else "SHORT",
                "entry_time": entry_time,
                "exit_time": timestamp,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "shares": abs(position),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": exit_reason,
            })
            
            position = 0
            direction = "NONE"

        # --- Check execution signals (BUY/SELL Reversals) ---
        if position == 0:
            if decision == "BUY":
                # Open LONG position
                trade_size_val = capital * (size_pct / 100.0)
                shares = int(trade_size_val / exec_price)
                if shares > 0:
                    position = shares
                    entry_price = exec_price
                    entry_time = timestamp
                    direction = "LONG"
            elif decision == "SELL":
                # Open SHORT position
                trade_size_val = capital * (size_pct / 100.0)
                shares = int(trade_size_val / exec_price)
                if shares > 0:
                    position = -shares
                    entry_price = exec_price
                    entry_time = timestamp
                    direction = "SHORT"
                    
        else:  # Active position exists, check for reversal
            if direction == "LONG" and decision == "SELL":
                # Close LONG
                pnl = position * (exec_price - entry_price)
                capital += pnl
                pnl_pct = (pnl / (position * entry_price)) * 100.0
                
                trades.append({
                    "symbol": symbol,
                    "direction": "LONG",
                    "entry_time": entry_time,
                    "exit_time": timestamp,
                    "entry_price": entry_price,
                    "exit_price": exec_price,
                    "shares": position,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": "SIGNAL_REVERSAL",
                })
                
                # Open SHORT
                trade_size_val = capital * (size_pct / 100.0)
                shares = int(trade_size_val / exec_price)
                if shares > 0:
                    position = -shares
                    entry_price = exec_price
                    entry_time = timestamp
                    direction = "SHORT"
                else:
                    position = 0
                    direction = "NONE"
                    
            elif direction == "SHORT" and decision == "BUY":
                # Close SHORT
                pnl = abs(position) * (entry_price - exec_price)
                capital += pnl
                pnl_pct = (pnl / (abs(position) * entry_price)) * 100.0
                
                trades.append({
                    "symbol": symbol,
                    "direction": "SHORT",
                    "entry_time": entry_time,
                    "exit_time": timestamp,
                    "entry_price": entry_price,
                    "exit_price": exec_price,
                    "shares": abs(position),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": "SIGNAL_REVERSAL",
                })
                
                # Open LONG
                trade_size_val = capital * (size_pct / 100.0)
                shares = int(trade_size_val / exec_price)
                if shares > 0:
                    position = shares
                    entry_price = exec_price
                    entry_time = timestamp
                    direction = "LONG"
                else:
                    position = 0
                    direction = "NONE"

        # --- Track Equity Curve ---
        unrealized = 0.0
        if position > 0:
            unrealized = position * (current_candle["close"] - entry_price)
        elif position < 0:
            unrealized = abs(position) * (entry_price - current_candle["close"])
            
        current_equity = capital + unrealized
        equity_curve.append({
            "time": timestamp,
            "equity": round(current_equity, 2),
        })

    # Close any remaining position at end
    if position != 0:
        last_candle = candles[-1]
        exit_price = last_candle["close"]
        if position > 0:
            pnl = position * (exit_price - entry_price)
        else:
            pnl = abs(position) * (entry_price - exit_price)

        capital += pnl
        pnl_pct = (pnl / (abs(position) * entry_price)) * 100.0
        
        trades.append({
            "symbol": symbol,
            "direction": "LONG" if position > 0 else "SHORT",
            "entry_time": entry_time,
            "exit_time": last_candle["timestamp"],
            "entry_price": entry_price,
            "exit_price": exit_price,
            "shares": abs(position),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": "END_OF_DATA",
        })
        # Update last equity curve point
        equity_curve[-1]["equity"] = round(capital, 2)

    # 4. Compute Metrics
    total_trades = len(trades)
    winning_trades = [t for t in trades if t["pnl"] > 0]
    losing_trades = [t for t in trades if t["pnl"] < 0]
    
    win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
    total_pnl = capital - initial_capital
    
    gross_profits = sum([t["pnl"] for t in winning_trades])
    gross_losses = sum([abs(t["pnl"]) for t in losing_trades])
    profit_factor = round(gross_profits / gross_losses, 2) if gross_losses > 0 else (round(gross_profits, 2) if gross_profits > 0 else 1.0)
    
    # Max Drawdown
    max_dd = 0.0
    peak = initial_capital
    for pt in equity_curve:
        equity = pt["equity"]
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0
        if dd > max_dd:
            max_dd = dd
            
    # Sharpe Ratio (annualized returns)
    # Calculate returns of each trade as proxy for daily returns
    returns = [t["pnl_pct"] for t in trades]
    if len(returns) > 1:
        mean_ret = sum(returns) / len(returns)
        variance = sum([(r - mean_ret) ** 2 for r in returns]) / (len(returns) - 1)
        std_ret = math.sqrt(variance)
        if std_ret > 0:
            sharpe = (mean_ret / std_ret) * math.sqrt(252)  # Proxy annualization
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    return {
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "metrics": {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "initial_capital": initial_capital,
            "final_capital": round(capital, 2),
        },
        "trades": trades,
        "equity_curve": equity_curve,
    }
