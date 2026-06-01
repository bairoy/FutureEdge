"""
app/graph/backtester.py
========================
Event-driven backtesting engine for FutureEdge.

Runs the multi-agent consensus algorithm (pure mathematical components)
over historical candles fetched via yfinance to evaluate performance.
"""

import math
import numpy as np
import pandas as pd
from loguru import logger

from app.data.feed import load_historical_candles


def calculate_transaction_cost(value: float, is_buy: bool) -> float:
    """
    Calculate detailed Zerodha/NSE MIS equity taxes and fees.

    Taxes modelled:
      - Brokerage: 0.03% or Rs 20 (whichever is lower) per order
      - STT: 0.025% on SELL only
      - Exchange Transaction Charges: 0.00345%
      - SEBI turnover fees: Rs 10 per crore (0.0001%)
      - Stamp duty: 0.003% on BUY only
      - GST: 18% of (Brokerage + Exchange Txn + SEBI)
    """
    if value <= 0:
        return 0.0
    brokerage = min(20.0, value * 0.0003)
    exchange_txn = value * 0.0000345
    sebi_fee = value * 0.0000001
    gst = (brokerage + exchange_txn + sebi_fee) * 0.18
    stamp_duty = value * 0.00003 if is_buy else 0.0
    stt = value * 0.00025 if not is_buy else 0.0

    return brokerage + exchange_txn + sebi_fee + gst + stamp_duty + stt


def precompute_indicators_and_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Precompute all technical indicators and regimes over the entire DataFrame
    to run backtests fast without lookahead bias.
    """
    # 1. RSI 14
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    period = 14
    # Welles Wilder's Smoothing RMA
    avg_gain = gain.ewm(alpha=1.0/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, adjust=False).mean().replace(0, 1e-10)

    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50.0).round(2)

    # 2. MACD
    ema_fast = df["close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # 3. Bollinger Bands
    sma = df["close"].rolling(window=20).mean()
    stdev = df["close"].rolling(window=20).std()
    df["bollinger_upper"] = sma + (stdev * 2)
    df["bollinger_lower"] = sma - (stdev * 2)
    df["bollinger_middle"] = sma

    # Fill Bollinger Bands NaNs with close proxy
    df["bollinger_upper"] = df["bollinger_upper"].fillna(df["close"] * 1.02)
    df["bollinger_lower"] = df["bollinger_lower"].fillna(df["close"] * 0.98)
    df["bollinger_middle"] = df["bollinger_middle"].fillna(df["close"])

    # 4. Regime Detection & Volatility
    # ATR (Average True Range)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Welles Wilder's Smoothing RMA
    atr = tr.ewm(alpha=1.0/period, adjust=False).mean().replace(0, 1e-10)

    # DM (+DM, -DM)
    up_move = df["high"].diff()
    down_move = df["low"].diff().abs()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_series = pd.Series(plus_dm, index=df.index)
    minus_dm_series = pd.Series(minus_dm, index=df.index)
    plus_dm_smoothed = plus_dm_series.ewm(alpha=1.0/period, adjust=False).mean()
    minus_dm_smoothed = minus_dm_series.ewm(alpha=1.0/period, adjust=False).mean()

    plus_di = 100 * (plus_dm_smoothed / atr)
    minus_di = 100 * (minus_dm_smoothed / atr)

    denom = plus_di + minus_di
    denom = denom.replace(0, 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / denom
    df["adx"] = dx.ewm(alpha=1.0/period, adjust=False).mean().fillna(20.0)

    # EMA Slope
    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_slope"] = ema20.diff(3).fillna(0.0)

    # Volatility (std of log returns over last 30 candles, scaled to daily)
    returns = np.log(df["close"] / df["close"].shift(1))
    df["raw_vol"] = returns.rolling(window=30).std()
    df["vol"] = (df["raw_vol"] * np.sqrt(375)).fillna(0.02)

    # Classify Regime for each row
    regimes = []
    for idx in range(len(df)):
        latest_adx = df["adx"].iloc[idx]
        slope = df["ema_slope"].iloc[idx]
        price = df["close"].iloc[idx]
        v = df["vol"].iloc[idx]

        if latest_adx > 25:
            slope_threshold = price * 0.0001
            if slope > slope_threshold:
                regimes.append("TRENDING_UP")
            elif slope < -slope_threshold:
                regimes.append("TRENDING_DOWN")
            else:
                regimes.append("RANGEBOUND")
        else:
            if v > 0.03:
                regimes.append("HIGH_VOLATILITY")
            else:
                regimes.append("RANGEBOUND")

    df["regime"] = regimes
    return df


async def run_backtest(
    symbol: str,
    period: str,
    interval: str,
    initial_capital: float = 100000.0,
    stop_loss_pct: float = 1.5,
    take_profit_pct: float = 3.0,
    size_pct: float = 10.0,
    slippage_pct: float = 0.05,
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

    # Convert candles to DataFrame and precompute indicators
    df = pd.DataFrame(candles)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df = precompute_indicators_and_regimes(df)

    # 2. State setup
    cash = initial_capital
    position = 0  # Number of shares (positive for LONG, negative for SHORT)
    entry_price = 0.0  # entry price with slippage applied
    entry_time = None
    direction = "NONE"  # "LONG" | "SHORT" | "NONE"
    trades = []
    equity_curve = []

    # Prepare historical sliding window
    for t in range(30, len(candles)):
        current_candle = candles[t]
        timestamp = current_candle["timestamp"]
        open_price = current_candle["open"]
        exited_this_candle = False

        # Slices indicators at t-1 to compute inputs before current candle open
        row_prev = df.iloc[t - 1]

        # --- Indicator scoring (identical to SignalAgent node) ---
        rsi = row_prev["rsi"]
        macd_val = row_prev["macd"]
        signal_val = row_prev["macd_signal"]
        hist = row_prev["macd_hist"]
        bollinger_upper = row_prev["bollinger_upper"]
        bollinger_lower = row_prev["bollinger_lower"]
        regime = row_prev["regime"]
        vol = row_prev["vol"]

        # Replicate SignalAgent scoring math
        rsi_score = 0.0
        if rsi < 30:
            rsi_score = 0.3
        elif rsi > 70:
            rsi_score = -0.3

        macd_score = 0.0
        if hist > 0 and macd_val > signal_val:
            macd_score = 0.25
        elif hist < 0 and macd_val < signal_val:
            macd_score = -0.25

        bb_score = 0.0
        if open_price < bollinger_lower:
            bb_score = 0.2
        elif open_price > bollinger_upper:
            bb_score = -0.2

        # DYNAMIC REGIME WEIGHTING
        score = 0.0
        if regime == "RANGEBOUND":
            score = (rsi_score * 1.5) + (bb_score * 1.5) + (macd_score * 0.2)
        elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
            score = macd_score * 1.8
            if regime == "TRENDING_UP" and rsi_score > 0:
                score += rsi_score * 0.5
            elif regime == "TRENDING_DOWN" and rsi_score < 0:
                score += rsi_score * 0.5
        elif regime == "HIGH_VOLATILITY":
            score = ((rsi_score * 0.5) + (macd_score * 0.5) + (bb_score * 0.5)) * 0.5
        else:
            score = rsi_score + macd_score + bb_score

        # Volatility filter
        if vol > 0.05:
            score *= 0.7

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

            # Check Stop Loss first (conservative)
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
            # Apply slippage to exit price (selling LONG -> lower price, buying SHORT -> higher price)
            is_long_exit = position > 0
            exit_price_with_slippage = exit_price * (1.0 - slippage_pct / 100.0) if is_long_exit else exit_price * (1.0 + slippage_pct / 100.0)

            # Calculate PnL and cash changes
            shares_qty = abs(position)
            gross_value = shares_qty * exit_price_with_slippage
            exit_cost = calculate_transaction_cost(gross_value, is_buy=(not is_long_exit))

            # Cash changes on exit
            cash += gross_value - exit_cost

            # Net PnL of trade
            trade_pnl = (exit_price_with_slippage * position) - (entry_price * position) - exit_cost
            pnl_pct = (trade_pnl / (shares_qty * entry_price)) * 100.0 if entry_price > 0 else 0.0

            trades.append({
                "symbol": symbol,
                "direction": "LONG" if position > 0 else "SHORT",
                "entry_time": entry_time,
                "exit_time": timestamp,
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price_with_slippage, 2),
                "shares": shares_qty,
                "pnl": round(trade_pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": exit_reason,
            })

            position = 0
            direction = "NONE"
            exited_this_candle = True

        # --- Check execution signals (BUY/SELL Reversals or New entries) ---
        if position == 0 and not exited_this_candle:
            if decision == "BUY":
                # Open LONG position
                trade_size_val = cash * (size_pct / 100.0)
                entry_price_with_slippage = open_price * (1.0 + slippage_pct / 100.0)
                shares = int(trade_size_val / entry_price_with_slippage)

                if shares > 0:
                    gross_value = shares * entry_price_with_slippage
                    entry_cost = calculate_transaction_cost(gross_value, is_buy=True)
                    cash -= (gross_value + entry_cost)

                    position = shares
                    entry_price = entry_price_with_slippage
                    entry_time = timestamp
                    direction = "LONG"

            elif decision == "SELL":
                # Open SHORT position
                trade_size_val = cash * (size_pct / 100.0)
                entry_price_with_slippage = open_price * (1.0 - slippage_pct / 100.0)
                shares = int(trade_size_val / entry_price_with_slippage)

                if shares > 0:
                    gross_value = shares * entry_price_with_slippage
                    entry_cost = calculate_transaction_cost(gross_value, is_buy=False)
                    cash -= (gross_value + entry_cost)

                    position = -shares
                    entry_price = entry_price_with_slippage
                    entry_time = timestamp
                    direction = "SHORT"

        else:  # Active position exists, check for reversal
            if direction == "LONG" and decision == "SELL":
                # Close LONG
                exit_price_with_slippage = open_price * (1.0 - slippage_pct / 100.0)
                shares_qty = position
                gross_value = shares_qty * exit_price_with_slippage
                exit_cost = calculate_transaction_cost(gross_value, is_buy=False)

                cash += gross_value - exit_cost
                trade_pnl = (exit_price_with_slippage * position) - (entry_price * position) - exit_cost
                pnl_pct = (trade_pnl / (shares_qty * entry_price)) * 100.0

                trades.append({
                    "symbol": symbol,
                    "direction": "LONG",
                    "entry_time": entry_time,
                    "exit_time": timestamp,
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price_with_slippage, 2),
                    "shares": position,
                    "pnl": round(trade_pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": "SIGNAL_REVERSAL",
                })

                # Open SHORT
                trade_size_val = cash * (size_pct / 100.0)
                entry_price_with_slippage = open_price * (1.0 - slippage_pct / 100.0)
                shares = int(trade_size_val / entry_price_with_slippage)

                if shares > 0:
                    gross_value = shares * entry_price_with_slippage
                    entry_cost = calculate_transaction_cost(gross_value, is_buy=False)
                    cash -= (gross_value + entry_cost)

                    position = -shares
                    entry_price = entry_price_with_slippage
                    entry_time = timestamp
                    direction = "SHORT"
                else:
                    position = 0
                    direction = "NONE"

            elif direction == "SHORT" and decision == "BUY":
                # Close SHORT
                exit_price_with_slippage = open_price * (1.0 + slippage_pct / 100.0)
                shares_qty = abs(position)
                gross_value = shares_qty * exit_price_with_slippage
                exit_cost = calculate_transaction_cost(gross_value, is_buy=True)

                cash += gross_value - exit_cost
                trade_pnl = (exit_price_with_slippage * position) - (entry_price * position) - exit_cost
                pnl_pct = (trade_pnl / (shares_qty * entry_price)) * 100.0

                trades.append({
                    "symbol": symbol,
                    "direction": "SHORT",
                    "entry_time": entry_time,
                    "exit_time": timestamp,
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price_with_slippage, 2),
                    "shares": shares_qty,
                    "pnl": round(trade_pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": "SIGNAL_REVERSAL",
                })

                # Open LONG
                trade_size_val = cash * (size_pct / 100.0)
                entry_price_with_slippage = open_price * (1.0 + slippage_pct / 100.0)
                shares = int(trade_size_val / entry_price_with_slippage)

                if shares > 0:
                    gross_value = shares * entry_price_with_slippage
                    entry_cost = calculate_transaction_cost(gross_value, is_buy=True)
                    cash -= (gross_value + entry_cost)

                    position = shares
                    entry_price = entry_price_with_slippage
                    entry_time = timestamp
                    direction = "LONG"
                else:
                    position = 0
                    direction = "NONE"

        # --- Track Equity Curve ---
        current_candle_close = current_candle["close"]
        position_value = 0.0
        if position > 0:
            position_value = position * current_candle_close
        elif position < 0:
            # Short position value: entry value + (entry_price - close_price)*qty
            position_value = abs(position) * entry_price + (entry_price - current_candle_close) * abs(position)

        current_equity = cash + position_value
        equity_curve.append({
            "time": timestamp,
            "equity": round(current_equity, 2),
        })

    # Close any remaining position at end
    if position != 0:
        last_candle = candles[-1]
        close_price = last_candle["close"]
        is_long = position > 0
        exit_price_with_slippage = close_price * (1.0 - slippage_pct / 100.0) if is_long else close_price * (1.0 + slippage_pct / 100.0)

        shares_qty = abs(position)
        gross_value = shares_qty * exit_price_with_slippage
        exit_cost = calculate_transaction_cost(gross_value, is_buy=(not is_long))

        cash += gross_value - exit_cost
        trade_pnl = (exit_price_with_slippage * position) - (entry_price * position) - exit_cost
        pnl_pct = (trade_pnl / (shares_qty * entry_price)) * 100.0 if entry_price > 0 else 0.0

        trades.append({
            "symbol": symbol,
            "direction": "LONG" if position > 0 else "SHORT",
            "entry_time": entry_time,
            "exit_time": last_candle["timestamp"],
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price_with_slippage, 2),
            "shares": shares_qty,
            "pnl": round(trade_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": "END_OF_DATA",
        })
        equity_curve[-1]["equity"] = round(cash, 2)

    # 4. Compute Metrics
    total_trades = len(trades)
    winning_trades = [t for t in trades if t["pnl"] > 0]
    losing_trades = [t for t in trades if t["pnl"] < 0]

    win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
    total_pnl = cash - initial_capital

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

    # Time-Series Sharpe Ratio
    daily_equities = {}
    for pt in equity_curve:
        # Group by YYYY-MM-DD
        date_str = pt["time"][:10]
        daily_equities[date_str] = pt["equity"]

    sorted_dates = sorted(daily_equities.keys())
    daily_equity_list = [daily_equities[d] for d in sorted_dates]

    daily_returns = []
    for i in range(1, len(daily_equity_list)):
        prev = daily_equity_list[i - 1]
        curr = daily_equity_list[i]
        if prev > 0:
            daily_returns.append((curr - prev) / prev)

    if len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum([(r - mean_ret) ** 2 for r in daily_returns]) / (len(daily_returns) - 1)
        std_ret = math.sqrt(variance)
        if std_ret > 0:
            sharpe = (mean_ret / std_ret) * math.sqrt(252)  # annualized
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
            "final_capital": round(cash, 2),
        },
        "trades": trades,
        "equity_curve": equity_curve,
    }
