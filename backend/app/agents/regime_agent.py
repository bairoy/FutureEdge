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

        # 1. 1-minute indicators
        # Average True Range (ATR)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        period = 14
        # Welles Wilder's Smoothing RMA
        atr = tr.ewm(alpha=1.0/period, adjust=False).mean()
        atr = atr.replace(0, 1e-10) # avoid division by zero

        # Directional Movement (+DM, -DM)
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

        # DX and ADX (Average Directional Index)
        denom = plus_di + minus_di
        denom = denom.replace(0, 1e-10)
        dx = 100 * (plus_di - minus_di).abs() / denom
        adx = dx.ewm(alpha=1.0/period, adjust=False).mean()

        latest_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 20.0

        # EMA 20 Slope (Trend direction)
        ema20 = df["close"].ewm(span=20, adjust=False).mean()
        ema_slope = float(ema20.diff(3).iloc[-1]) if len(ema20) >= 3 else 0.0

        # Volatility (std of log returns over last 30 candles, scaled to daily)
        returns = np.log(df["close"] / df["close"].shift(1))
        raw_vol = float(returns.tail(30).std()) if len(returns) >= 30 else 0.02 / np.sqrt(375)
        if pd.isna(raw_vol):
            raw_vol = 0.02 / np.sqrt(375)
        vol = raw_vol * np.sqrt(375)

        # 2. 15-minute indicators (Multi-Timeframe Macro Trend)
        df_copy = df.copy()
        df_copy["timestamp"] = pd.to_datetime(df_copy["timestamp"])
        df_copy.set_index("timestamp", inplace=True)
        
        # Resample to 15m
        df_15m = df_copy.resample("15Min").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        }).dropna()
        
        latest_adx_15m = 20.0
        ema_slope_15m = 0.0
        
        if len(df_15m) >= 15:
            tr1_15m = df_15m["high"] - df_15m["low"]
            tr2_15m = (df_15m["high"] - df_15m["close"].shift(1)).abs()
            tr3_15m = (df_15m["low"] - df_15m["close"].shift(1)).abs()
            tr_15m = pd.concat([tr1_15m, tr2_15m, tr3_15m], axis=1).max(axis=1)
            atr_15m = tr_15m.ewm(alpha=1.0/14, adjust=False).mean().replace(0, 1e-10)
            
            up_move_15m = df_15m["high"].diff()
            down_move_15m = df_15m["low"].diff().abs()
            plus_dm_15m = pd.Series(np.where((up_move_15m > down_move_15m) & (up_move_15m > 0), up_move_15m, 0.0), index=df_15m.index)
            minus_dm_15m = pd.Series(np.where((down_move_15m > up_move_15m) & (down_move_15m > 0), down_move_15m, 0.0), index=df_15m.index)
            plus_di_15m = 100 * (plus_dm_15m.ewm(alpha=1.0/14, adjust=False).mean() / atr_15m)
            minus_di_15m = 100 * (minus_dm_15m.ewm(alpha=1.0/14, adjust=False).mean() / atr_15m)
            
            denom_15m = plus_di_15m + minus_di_15m
            denom_15m = denom_15m.replace(0, 1e-10)
            dx_15m = 100 * (plus_di_15m - minus_di_15m).abs() / denom_15m
            adx_15m = dx_15m.ewm(alpha=1.0/14, adjust=False).mean()
            latest_adx_15m = float(adx_15m.iloc[-1])
            
            # EMA 20 slope on 15m
            ema20_15m = df_15m["close"].ewm(span=20, adjust=False).mean()
            ema_slope_15m = float(ema20_15m.diff(2).iloc[-1]) if len(ema20_15m) >= 2 else 0.0

        # Blended classification logic
        # 15m macro trend carries significant weight
        price_level = float(df["close"].iloc[-1])
        slope_threshold_1m = price_level * 0.0001
        slope_threshold_15m = price_level * 0.0002
        
        regime = "RANGEBOUND"
        if latest_adx_15m > 22 and abs(ema_slope_15m) > slope_threshold_15m:
            if ema_slope_15m > slope_threshold_15m:
                regime = "TRENDING_UP"
            else:
                regime = "TRENDING_DOWN"
        else:
            if latest_adx > 25:
                if ema_slope > slope_threshold_1m:
                    regime = "TRENDING_UP"
                elif ema_slope < -slope_threshold_1m:
                    regime = "TRENDING_DOWN"
            else:
                if vol > 0.03:
                    regime = "HIGH_VOLATILITY"

        logger.info(
            f"📊 RegimeAgent | {symbol} | Detected Regime: {regime} | "
            f"ADX_1m={latest_adx:.1f} | ADX_15m={latest_adx_15m:.1f} | Vol={vol*100:.2f}%"
        )

        ctx.regime = regime
        ctx.volatility_24h = vol

        return {
            "market_context": ctx,
            "completed_nodes": ["regime_agent"],
            "logs": [f"RegimeAgent detected {regime} (ADX_1m={latest_adx:.1f}, ADX_15m={latest_adx_15m:.1f}, Vol={vol*100:.1f}%)"],
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
