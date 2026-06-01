"""
app/agents/regime_agent.py
===========================
Market Regime Detection Agent.
Calculates ADX, EMA slope, and rolling volatility to classify the active market regime.
Updates state["market_context"].regime so that downstream agents can adapt.
"""

import numpy as np
import pandas as pd
from loguru import logger

from app.graph.state import AgentState


async def regime_agent_node(state: AgentState) -> dict:
    """
    Agent node that runs first in the graph to classify the market regime.
    """
    try:
        ctx = state["market_context"]
        symbol = ctx.symbol
        candles = ctx.ohlcv_1m

        if not candles or len(candles) < 30:
            logger.info(f"RegimeAgent | Insufficient candles for {symbol} — defaulting to RANGEBOUND")
            # Update the context in state directly or return the state update
            ctx.regime = "RANGEBOUND"
            ctx.volatility_24h = 0.02
            return {
                "market_context": ctx,
                "completed_nodes": ["regime_agent"],
                "logs": ["RegimeAgent: insufficient data, default to RANGEBOUND"],
            }

        # Convert candles to pandas DataFrame
        df = pd.DataFrame(candles)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        # 1. Average True Range (ATR)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        period = 14
        # Welles Wilder's Smoothing RMA
        atr = tr.ewm(alpha=1.0/period, adjust=False).mean()
        atr = atr.replace(0, 1e-10) # avoid division by zero

        # 2. Directional Movement (+DM, -DM)
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

        # 3. DX and ADX (Average Directional Index)
        denom = plus_di + minus_di
        denom = denom.replace(0, 1e-10)
        dx = 100 * (plus_di - minus_di).abs() / denom
        adx = dx.ewm(alpha=1.0/period, adjust=False).mean()

        latest_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 20.0

        # 4. EMA 20 Slope (Trend direction)
        ema20 = df["close"].ewm(span=20, adjust=False).mean()
        ema_slope = float(ema20.diff(3).iloc[-1]) if len(ema20) >= 3 else 0.0

        # 5. Volatility (std of log returns over last 30 candles, scaled to daily)
        returns = np.log(df["close"] / df["close"].shift(1))
        raw_vol = float(returns.tail(30).std()) if len(returns) >= 30 else 0.02 / np.sqrt(375)
        if pd.isna(raw_vol):
            raw_vol = 0.02 / np.sqrt(375)
        vol = raw_vol * np.sqrt(375)

        # 6. Classification Logic
        # ADX > 25 indicates a strong trend
        if latest_adx > 25:
            # Significant positive slope -> TRENDING_UP, negative -> TRENDING_DOWN
            # Normalize slope relative to price level (e.g. 0.01% of price)
            slope_threshold = float(df["close"].iloc[-1]) * 0.0001
            if ema_slope > slope_threshold:
                regime = "TRENDING_UP"
            elif ema_slope < -slope_threshold:
                regime = "TRENDING_DOWN"
            else:
                regime = "RANGEBOUND"
        else:
            # ADX <= 25 is rangebound. Check volatility for high-risk consolidation
            if vol > 0.03: # 3% volatility in 1m returns represents a high-vol range
                regime = "HIGH_VOLATILITY"
            else:
                regime = "RANGEBOUND"

        logger.info(
            f"📊 RegimeAgent | {symbol} | Detected Regime: {regime} | "
            f"ADX={latest_adx:.1f} | EMA Slope={ema_slope:.3f} | Vol={vol*100:.2f}%"
        )

        ctx.regime = regime
        ctx.volatility_24h = vol

        return {
            "market_context": ctx,
            "completed_nodes": ["regime_agent"],
            "logs": [f"RegimeAgent detected {regime} (ADX={latest_adx:.1f}, Vol={vol*100:.1f}%)"],
        }

    except Exception as e:
        logger.exception(f"RegimeAgent failure: {e}")
        # Graceful fallback to RANGEBOUND to not halt execution
        try:
            ctx = state["market_context"]
            ctx.regime = "RANGEBOUND"
            ctx.volatility_24h = 0.02
            return {
                "market_context": ctx,
                "completed_nodes": ["regime_agent"],
                "logs": [f"RegimeAgent failed, fallback to RANGEBOUND: {e}"],
            }
        except Exception:
            return {
                "completed_nodes": ["regime_agent"],
                "logs": [f"RegimeAgent failed completely: {e}"],
            }
