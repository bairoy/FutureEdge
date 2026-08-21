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

    # 5. EMA 9/21 (for crossover score matching live SignalAgent)
    ema9  = df["close"].ewm(span=9,  adjust=False).mean()
    ema21 = df["close"].ewm(span=21, adjust=False).mean()
    df["ema_9"]       = ema9
    df["ema_21"]      = ema21
    df["ema_9_prev"]  = ema9.shift(1)
    df["ema_21_prev"] = ema21.shift(1)

    # 6. ATR (Wilder smoothing, same as indicator_cache._calc_atr)
    atr_raw = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = atr_raw.ewm(alpha=1.0/period, adjust=False).mean().fillna(df["close"] * 0.002)

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
    execution_delay_candles: int = 0,
    trailing_stop_enabled: bool = True,
    trailing_stop_trigger_pct: float = 2.0,
    trailing_stop_sl_pct: float = 1.5,
    start_idx: int = None,
    end_idx: int = None,
    candles: list[dict] | None = None,
) -> dict:
    """
    Run a historical simulation on OHLCV candles.

    Bypasses OpenAI LLM reasoning calls to keep execution fast and free,
    using the exact indicator logic of SignalAgent and risk parameters.

    `candles` lets a caller supply its own OHLCV series instead of fetching
    `period`/`interval` from yfinance. The CI regression gate uses it to pin an
    explicit, fixed date range: gating on a rolling "last 6 months" would move
    the result every day and fail the build because the market moved, not
    because the strategy regressed.
    """

    # 1. Fetch candles (unless the caller supplied them)
    if candles is None:
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
                "max_consecutive_losses": 0,
                "avg_winner_hold_minutes": 0.0,
                "avg_loser_hold_minutes": 0.0,
                "time_of_day_breakdown": {},
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
    position = 0          # Number of shares (positive for LONG, negative for SHORT)
    entry_price = 0.0     # entry price with slippage applied
    entry_time = None
    entry_regime = "RANGEBOUND"   # regime at trade entry — tracked for research
    direction = "NONE"    # "LONG" | "SHORT" | "NONE"
    trades = []
    equity_curve = []

    # Trailing Stop variables
    peak_price = 0.0
    valley_price = 0.0
    sl_price = 0.0
    tp_price = 0.0
    initial_sl_price = 0.0

    # Execution Delay variables
    pending_entry = None  # None or dict

    # Prepare historical sliding window
    for t in range(30, len(candles)):
        current_candle = candles[t]
        timestamp = current_candle["timestamp"]
        open_price = current_candle["open"]
        exited_this_candle = False

        # Restrict entries/evaluations to window if start_idx/end_idx are provided
        is_in_trading_window = True
        if start_idx is not None and t < start_idx:
            is_in_trading_window = False
        if end_idx is not None and t >= end_idx:
            is_in_trading_window = False

        # --- 0. Check pending entry orders (execution delay simulation) ---
        if pending_entry is not None and t >= pending_entry["trigger_time_idx"]:
            pe_dir = pending_entry["direction"]
            pe_reg = pending_entry["regime"]
            pe_sl_pct = pending_entry["dynamic_sl_pct"]
            pe_tp_pct = pending_entry["dynamic_tp_pct"]

            if position == 0 and not exited_this_candle:
                trade_size_val = cash * (size_pct / 100.0)
                if pe_dir == "BUY":
                    entry_price_with_slippage = open_price * (1.0 + slippage_pct / 100.0)
                    shares = int(trade_size_val / entry_price_with_slippage)
                    if shares > 0:
                        gross_value = shares * entry_price_with_slippage
                        entry_cost = calculate_transaction_cost(gross_value, is_buy=True)
                        cash -= (gross_value + entry_cost)
                        position = shares
                        entry_price = entry_price_with_slippage
                        entry_time = timestamp
                        entry_regime = pe_reg
                        direction = "LONG"
                        peak_price = open_price
                        sl_price = entry_price * (1.0 - pe_sl_pct / 100.0)
                        initial_sl_price = sl_price
                        tp_price = entry_price * (1.0 + pe_tp_pct / 100.0)
                elif pe_dir == "SELL":
                    entry_price_with_slippage = open_price * (1.0 - slippage_pct / 100.0)
                    shares = int(trade_size_val / entry_price_with_slippage)
                    if shares > 0:
                        gross_value = shares * entry_price_with_slippage
                        entry_cost = calculate_transaction_cost(gross_value, is_buy=False)
                        cash -= (gross_value + entry_cost)
                        position = -shares
                        entry_price = entry_price_with_slippage
                        entry_time = timestamp
                        entry_regime = pe_reg
                        direction = "SHORT"
                        valley_price = open_price
                        sl_price = entry_price * (1.0 + pe_sl_pct / 100.0)
                        initial_sl_price = sl_price
                        tp_price = entry_price * (1.0 - pe_tp_pct / 100.0)
            pending_entry = None

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

        # Replicate SignalAgent scoring math (regime-adaptive)
        rsi_oversold  = 40.0 if regime == "TRENDING_UP" else 30.0
        rsi_overbought = 60.0 if regime == "TRENDING_DOWN" else 70.0

        rsi_score = 0.0
        if rsi < rsi_oversold:
            rsi_score = 0.3
        elif rsi > rsi_overbought:
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

        # EMA 9/21 crossover (matches live SignalAgent logic)
        ema9_curr  = float(row_prev["ema_9"])
        ema21_curr = float(row_prev["ema_21"])
        ema9_prev  = float(row_prev["ema_9_prev"]) if not pd.isna(row_prev["ema_9_prev"]) else ema9_curr
        ema21_prev = float(row_prev["ema_21_prev"]) if not pd.isna(row_prev["ema_21_prev"]) else ema21_curr

        crossover_score = 0.0
        if ema9_curr > ema21_curr:
            crossover_score = 0.2 + (0.1 if ema9_prev <= ema21_prev else 0.0)   # +0.1 for fresh cross
        elif ema9_curr < ema21_curr:
            crossover_score = -0.2 - (0.1 if ema9_prev >= ema21_prev else 0.0)  # -0.1 for fresh cross

        # DYNAMIC REGIME WEIGHTING (mirrors live SignalAgent exactly)
        score = 0.0
        if regime == "RANGEBOUND":
            score = (rsi_score * 1.5) + (bb_score * 1.5) + (macd_score * 0.2)
        elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
            score = (macd_score * 1.2) + (crossover_score * 1.5)
            if regime == "TRENDING_UP" and rsi_score > 0:
                score += rsi_score * 0.5
            elif regime == "TRENDING_DOWN" and rsi_score < 0:
                score += rsi_score * 0.5
        elif regime == "HIGH_VOLATILITY":
            score = ((rsi_score * 0.4) + (macd_score * 0.4) + (bb_score * 0.4)) * 0.5
        else:
            score = rsi_score + macd_score + bb_score + crossover_score

        # Volatility filter (match live SignalAgent threshold)
        if vol > 0.05:
            score *= 0.7

        # Make decision
        decision = "HOLD"
        if score > 0.2:
            decision = "BUY"
        elif score < -0.2:
            decision = "SELL"

        # --- ATR-based dynamic SL/TP (uses real ATR, not fixed %) ---
        atr_val = float(row_prev["atr"]) if "atr" in row_prev and not pd.isna(row_prev["atr"]) else 0.0
        if atr_val > 0:
            dynamic_sl_pct = (1.5 * atr_val / open_price) * 100.0  # 1.5×ATR as %
            dynamic_tp_pct = (3.0 * atr_val / open_price) * 100.0  # 3.0×ATR as %
        else:
            dynamic_sl_pct = stop_loss_pct
            dynamic_tp_pct = take_profit_pct

        # --- Trailing Stop Adjustment ---
        if trailing_stop_enabled and position != 0:
            if position > 0:  # LONG
                if current_candle["high"] > peak_price:
                    peak_price = current_candle["high"]
                unrealized_gain = (peak_price - entry_price) / entry_price
                if unrealized_gain >= (trailing_stop_trigger_pct / 100.0):
                    new_sl_price = peak_price * (1.0 - (trailing_stop_sl_pct / 100.0))
                    sl_price = max(sl_price, new_sl_price)
            else:  # SHORT
                if current_candle["low"] < valley_price:
                    valley_price = current_candle["low"]
                unrealized_gain = (entry_price - valley_price) / entry_price
                if unrealized_gain >= (trailing_stop_trigger_pct / 100.0):
                    new_sl_price = valley_price * (1.0 + (trailing_stop_sl_pct / 100.0))
                    sl_price = min(sl_price, new_sl_price)

        # --- Check exit conditions (Stop-Loss and Take-Profit) ---
        exit_triggered = False
        exit_reason = ""

        if position > 0:  # LONG
            # Check Stop Loss first (conservative)
            if current_candle["low"] <= sl_price:
                exit_triggered = True
                exit_price = sl_price
                exit_reason = "TRAILING_STOP" if sl_price > initial_sl_price else "STOP_LOSS"
            elif current_candle["high"] >= tp_price:
                exit_triggered = True
                exit_price = tp_price
                exit_reason = "TAKE_PROFIT"

        elif position < 0:  # SHORT
            if current_candle["high"] >= sl_price:
                exit_triggered = True
                exit_price = sl_price
                exit_reason = "TRAILING_STOP" if sl_price < initial_sl_price else "STOP_LOSS"
            elif current_candle["low"] <= tp_price:
                exit_triggered = True
                exit_price = tp_price
                exit_reason = "TAKE_PROFIT"

        # Force-exit if end of testing window reached
        if position != 0 and end_idx is not None and t == end_idx - 1:
            exit_triggered = True
            exit_price = current_candle["close"]
            exit_reason = "END_OF_TEST_WINDOW"

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
                "symbol":      symbol,
                "direction":   "LONG" if position > 0 else "SHORT",
                "entry_time":  entry_time,
                "exit_time":   timestamp,
                "entry_price": round(entry_price, 2),
                "exit_price":  round(exit_price_with_slippage, 2),
                "shares":      shares_qty,
                "pnl":         round(trade_pnl, 2),
                "pnl_pct":     round(pnl_pct, 2),
                "exit_reason": exit_reason,
                "regime":      entry_regime,  # regime at entry for research analysis
            })

            position = 0
            direction = "NONE"
            exited_this_candle = True

        # --- Check execution signals (BUY/SELL Reversals or New entries) ---
        if position == 0 and not exited_this_candle:
            # Check if there is an active decision and we are in the trading window
            if is_in_trading_window and decision in ("BUY", "SELL"):
                if execution_delay_candles > 0:
                    # Queue a pending entry
                    pending_entry = {
                        "direction": decision,
                        "trigger_time_idx": t + execution_delay_candles,
                        "dynamic_sl_pct": dynamic_sl_pct,
                        "dynamic_tp_pct": dynamic_tp_pct,
                        "regime": regime,
                    }
                else:
                    # Execute immediately at current candle's open price
                    trade_size_val = cash * (size_pct / 100.0)
                    if decision == "BUY":
                        entry_price_with_slippage = open_price * (1.0 + slippage_pct / 100.0)
                        shares = int(trade_size_val / entry_price_with_slippage)
                        if shares > 0:
                            gross_value = shares * entry_price_with_slippage
                            entry_cost = calculate_transaction_cost(gross_value, is_buy=True)
                            cash -= (gross_value + entry_cost)
                            position = shares
                            entry_price = entry_price_with_slippage
                            entry_time = timestamp
                            entry_regime = regime
                            direction = "LONG"
                            peak_price = open_price
                            sl_price = entry_price * (1.0 - dynamic_sl_pct / 100.0)
                            initial_sl_price = sl_price
                            tp_price = entry_price * (1.0 + dynamic_tp_pct / 100.0)
                    elif decision == "SELL":
                        entry_price_with_slippage = open_price * (1.0 - slippage_pct / 100.0)
                        shares = int(trade_size_val / entry_price_with_slippage)
                        if shares > 0:
                            gross_value = shares * entry_price_with_slippage
                            entry_cost = calculate_transaction_cost(gross_value, is_buy=False)
                            cash -= (gross_value + entry_cost)
                            position = -shares
                            entry_price = entry_price_with_slippage
                            entry_time = timestamp
                            entry_regime = regime
                            direction = "SHORT"
                            valley_price = open_price
                            sl_price = entry_price * (1.0 + dynamic_sl_pct / 100.0)
                            initial_sl_price = sl_price
                            tp_price = entry_price * (1.0 - dynamic_tp_pct / 100.0)

        else:  # Active position exists, check for reversal
            # Reverse signals are only processed if we are in the trading window
            if is_in_trading_window:
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
                        "regime": entry_regime,
                    })

                    # Open SHORT (if delayed, queue it; otherwise execute immediately)
                    if execution_delay_candles > 0:
                        pending_entry = {
                            "direction": "SELL",
                            "trigger_time_idx": t + execution_delay_candles,
                            "dynamic_sl_pct": dynamic_sl_pct,
                            "dynamic_tp_pct": dynamic_tp_pct,
                            "regime": regime,
                        }
                        position = 0
                        direction = "NONE"
                    else:
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
                            entry_regime = regime
                            direction = "SHORT"
                            valley_price = open_price
                            sl_price = entry_price * (1.0 + dynamic_sl_pct / 100.0)
                            initial_sl_price = sl_price
                            tp_price = entry_price * (1.0 - dynamic_tp_pct / 100.0)
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
                        "regime": entry_regime,
                    })

                    # Open LONG (if delayed, queue it; otherwise execute immediately)
                    if execution_delay_candles > 0:
                        pending_entry = {
                            "direction": "BUY",
                            "trigger_time_idx": t + execution_delay_candles,
                            "dynamic_sl_pct": dynamic_sl_pct,
                            "dynamic_tp_pct": dynamic_tp_pct,
                            "regime": regime,
                        }
                        position = 0
                        direction = "NONE"
                    else:
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
                            entry_regime = regime
                            direction = "LONG"
                            peak_price = open_price
                            sl_price = entry_price * (1.0 - dynamic_sl_pct / 100.0)
                            initial_sl_price = sl_price
                            tp_price = entry_price * (1.0 + dynamic_tp_pct / 100.0)
                        else:
                            position = 0
                            direction = "NONE"

        # --- Track Equity Curve ---
        current_candle_close = current_candle["close"]
        position_value = 0.0
        if position > 0:
            position_value = position * current_candle_close
        elif position < 0:
            position_value = abs(position) * entry_price + (entry_price - current_candle_close) * abs(position)

        current_equity = cash + position_value
        equity_curve.append({
            "time": timestamp,
            "equity": round(current_equity, 2),
        })

    # Close any remaining position at end of backtest data range
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
            "symbol":      symbol,
            "direction":   "LONG" if position > 0 else "SHORT",
            "entry_time":  entry_time,
            "exit_time":   last_candle["timestamp"],
            "entry_price": round(entry_price, 2),
            "exit_price":  round(exit_price_with_slippage, 2),
            "shares":      shares_qty,
            "pnl":         round(trade_pnl, 2),
            "pnl_pct":     round(pnl_pct, 2),
            "exit_reason": "END_OF_DATA",
            "regime":      entry_regime,
        })
        if equity_curve:
            equity_curve[-1]["equity"] = round(cash, 2)

    # 4. Compute Metrics
    total_trades = len(trades)
    winning_trades = [t for t in trades if t["pnl"] > 0]
    losing_trades  = [t for t in trades if t["pnl"] < 0]

    win_rate    = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
    total_pnl   = cash - initial_capital
    return_pct  = (total_pnl / initial_capital) * 100.0

    gross_profits = sum([t["pnl"] for t in winning_trades])
    gross_losses  = sum([abs(t["pnl"]) for t in losing_trades])
    profit_factor = round(gross_profits / gross_losses, 2) if gross_losses > 0 else (round(gross_profits, 2) if gross_profits > 0 else 1.0)

    # Average win / loss per trade
    avg_win_pct  = round(sum(t["pnl_pct"] for t in winning_trades) / len(winning_trades), 2) if winning_trades else 0.0
    avg_loss_pct = round(sum(abs(t["pnl_pct"]) for t in losing_trades) / len(losing_trades), 2) if losing_trades else 0.0
    win_loss_ratio = round(avg_win_pct / avg_loss_pct, 2) if avg_loss_pct > 0 else 0.0

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

    # GAP 1: Max consecutive losses (losing streak)
    max_consecutive_losses = 0
    current_streak = 0
    for trade in trades:
        if trade["pnl"] < 0:
            current_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_streak)
        else:
            current_streak = 0

    # GAP 2: Holding period analysis
    winning_hold_times = []
    losing_hold_times = []
    for trade in trades:
        try:
            entry_dt = pd.to_datetime(trade["entry_time"])
            exit_dt = pd.to_datetime(trade["exit_time"])
            hold_minutes = (exit_dt - entry_dt).total_seconds() / 60.0
            trade["hold_minutes"] = round(hold_minutes, 1)
            if trade["pnl"] > 0:
                winning_hold_times.append(hold_minutes)
            elif trade["pnl"] < 0:
                losing_hold_times.append(hold_minutes)
        except Exception:
            trade["hold_minutes"] = 0.0

    avg_winner_hold = round(sum(winning_hold_times) / len(winning_hold_times), 1) if winning_hold_times else 0.0
    avg_loser_hold = round(sum(losing_hold_times) / len(losing_hold_times), 1) if losing_hold_times else 0.0

    # GAP 3: Time of day performance breakdown
    time_of_day_stats = {}
    for trade in trades:
        try:
            entry_dt = pd.to_datetime(trade["entry_time"])
            hour = entry_dt.hour
            time_of_day_stats.setdefault(hour, []).append(trade["pnl"])
        except Exception:
            pass

    time_of_day_breakdown = {}
    for hour, pnls in time_of_day_stats.items():
        wins = [p for p in pnls if p > 0]
        time_of_day_breakdown[hour] = {
            "total_trades": len(pnls),
            "win_rate_pct": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
            "total_pnl": round(sum(pnls), 2),
        }

    # ── Time-Series Sharpe Ratio (annualized) ──────────────────────────────
    daily_equities: dict = {}
    for pt in equity_curve:
        date_str = pt["time"][:10]          # Group by YYYY-MM-DD
        daily_equities[date_str] = pt["equity"]

    sorted_dates     = sorted(daily_equities.keys())
    daily_equity_lst = [daily_equities[d] for d in sorted_dates]

    daily_returns = []
    for i in range(1, len(daily_equity_lst)):
        prev = daily_equity_lst[i - 1]
        curr = daily_equity_lst[i]
        if prev > 0:
            daily_returns.append((curr - prev) / prev)

    if len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum([(r - mean_ret) ** 2 for r in daily_returns]) / (len(daily_returns) - 1)
        std_ret  = math.sqrt(variance)
        sharpe   = (mean_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    # ── Calmar Ratio = CAGR / Max Drawdown ────────────────────────────────
    calmar = round(return_pct / max_dd, 2) if max_dd > 0 else 0.0

    # ── Per-Regime Breakdown ─────────────────────────
    regimes_seen = {t.get("regime", "UNKNOWN") for t in trades}
    regime_breakdown: dict = {}
    for reg in regimes_seen:
        reg_trades = [t for t in trades if t.get("regime") == reg]
        reg_wins   = [t for t in reg_trades if t["pnl"] > 0]
        regime_breakdown[reg] = {
            "total_trades": len(reg_trades),
            "wins":         len(reg_wins),
            "win_rate_pct": round(len(reg_wins) / len(reg_trades) * 100, 2) if reg_trades else 0.0,
            "total_pnl":    round(sum(t["pnl"] for t in reg_trades), 2),
        }

    return {
        "symbol":   symbol,
        "period":   period,
        "interval": interval,
        "metrics": {
            # ── Core ──────────────────────────────────────────────────────
            "total_trades":       total_trades,
            "win_rate":           round(win_rate, 2),
            "total_pnl":          round(total_pnl, 2),
            "return_pct":         round(return_pct, 2),
            "profit_factor":      profit_factor,
            # ── Risk-adjusted performance ──────────────────────────────────
            "max_drawdown_pct":   round(max_dd, 2),
            "sharpe_ratio":       round(sharpe, 2),
            "calmar_ratio":       calmar,
            # ── Trade quality ─────────────────────────────────────────────
            "avg_win_pct":        avg_win_pct,
            "avg_loss_pct":       avg_loss_pct,
            "win_loss_ratio":     win_loss_ratio,
            # ── Capital ──────────────────────────────────────────────────
            "initial_capital":    initial_capital,
            "final_capital":      round(cash, 2),
            # ── GAP 1-3 additions ─────────────────────────────────────────
            "max_consecutive_losses": max_consecutive_losses,
            "avg_winner_hold_minutes": avg_winner_hold,
            "avg_loser_hold_minutes": avg_loser_hold,
            "time_of_day_breakdown": time_of_day_breakdown,
            "correlation_filter_note": "Portfolio-level correlation filter is RECOMMENDED for multiple live symbols to avoid concentration risk.",
        },
        "regime_breakdown": regime_breakdown,
        "trades":           trades,
        "equity_curve":     equity_curve,
    }


async def run_walk_forward_backtest(
    symbol: str,
    period: str = "6mo",
    interval: str = "5m",
    train_pct: float = 0.70,
    n_folds: int = 4,
    initial_capital: float = 100000.0,
    stop_loss_pct: float = 1.5,
    take_profit_pct: float = 3.0,
    size_pct: float = 10.0,
    slippage_pct: float = 0.05,
    execution_delay_candles: int = 0,
    trailing_stop_enabled: bool = True,
    trailing_stop_trigger_pct: float = 2.0,
    trailing_stop_sl_pct: float = 1.5,
) -> dict:
    """
    Run walk-forward validation backtest by splitting historical data into rolling folds.
    """
    candles = load_historical_candles(symbol, period=period, interval=interval)
    if not candles or len(candles) < 100:
        return {
            "status": "error",
            "message": f"Insufficient historical candles for walk-forward validation (minimum 100 needed, got {len(candles) if candles else 0})",
        }

    L = len(candles)
    test_chunk_size = int((L * (1.0 - train_pct)) / n_folds)
    if test_chunk_size < 10:
        return {
            "status": "error",
            "message": f"Walk-forward validation test chunk size too small ({test_chunk_size} candles). Increase data range or decrease n_folds.",
        }

    folds = []
    all_test_trades = []
    
    # Capital starts at initial_capital and resets per fold to isolate metrics
    for i in range(n_folds):
        train_end = int(L * train_pct) + i * test_chunk_size
        test_start = train_end
        test_end = min(test_start + test_chunk_size, L)
        
        logger.info(f"Walk-forward Fold {i+1}/{n_folds} | Test window indices: [{test_start}, {test_end})")
        
        res = await run_backtest(
            symbol=symbol,
            period=period,
            interval=interval,
            initial_capital=initial_capital,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            size_pct=size_pct,
            slippage_pct=slippage_pct,
            execution_delay_candles=execution_delay_candles,
            trailing_stop_enabled=trailing_stop_enabled,
            trailing_stop_trigger_pct=trailing_stop_trigger_pct,
            trailing_stop_sl_pct=trailing_stop_sl_pct,
            start_idx=test_start,
            end_idx=test_end,
        )
        
        fold_trades = res.get("trades", [])
        all_test_trades.extend(fold_trades)
        
        folds.append({
            "fold_index": i + 1,
            "test_start_time": candles[test_start]["timestamp"],
            "test_end_time": candles[min(test_end, L - 1)]["timestamp"],
            "metrics": res.get("metrics", {}),
            "regime_breakdown": res.get("regime_breakdown", {}),
        })

    # Compute aggregate out-of-sample metrics
    total_trades = len(all_test_trades)
    winning_trades = [t for t in all_test_trades if t["pnl"] > 0]
    losing_trades = [t for t in all_test_trades if t["pnl"] < 0]
    
    win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
    total_pnl = sum(t["pnl"] for t in all_test_trades)
    return_pct = (total_pnl / initial_capital) * 100.0
    
    gross_profits = sum([t["pnl"] for t in winning_trades])
    gross_losses = sum([abs(t["pnl"]) for t in losing_trades])
    profit_factor = round(gross_profits / gross_losses, 2) if gross_losses > 0 else (round(gross_profits, 2) if gross_profits > 0 else 1.0)
    
    max_consecutive_losses = 0
    current_streak = 0
    for trade in all_test_trades:
        if trade["pnl"] < 0:
            current_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_streak)
        else:
            current_streak = 0
            
    winning_hold_times = []
    losing_hold_times = []
    for trade in all_test_trades:
        if "hold_minutes" in trade:
            if trade["pnl"] > 0:
                winning_hold_times.append(trade["hold_minutes"])
            elif trade["pnl"] < 0:
                losing_hold_times.append(trade["hold_minutes"])
                
    avg_winner_hold = round(sum(winning_hold_times) / len(winning_hold_times), 1) if winning_hold_times else 0.0
    avg_loser_hold = round(sum(losing_hold_times) / len(losing_hold_times), 1) if losing_hold_times else 0.0
    
    # Time-of-day breakdown for aggregate
    time_of_day_stats = {}
    for trade in all_test_trades:
        try:
            entry_dt = pd.to_datetime(trade["entry_time"])
            hour = entry_dt.hour
            time_of_day_stats.setdefault(hour, []).append(trade["pnl"])
        except Exception:
            pass

    time_of_day_breakdown = {}
    for hour, pnls in time_of_day_stats.items():
        wins = [p for p in pnls if p > 0]
        time_of_day_breakdown[hour] = {
            "total_trades": len(pnls),
            "win_rate_pct": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
            "total_pnl": round(sum(pnls), 2),
        }

    aggregate_metrics = {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 2),
        "profit_factor": profit_factor,
        "max_consecutive_losses": max_consecutive_losses,
        "avg_winner_hold_minutes": avg_winner_hold,
        "avg_loser_hold_minutes": avg_loser_hold,
        "time_of_day_breakdown": time_of_day_breakdown,
        "correlation_filter_note": "Portfolio-level correlation filter is RECOMMENDED for multiple live symbols to avoid concentration risk.",
    }

    return {
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "folds": folds,
        "aggregate_metrics": aggregate_metrics,
        "all_trades": all_test_trades,
    }

