"""
app/data/regime_detector.py
============================
Detect market regime from OHLCV candle data.

WHY REGIME DETECTION MATTERS:
-------------------------------
Different indicators work well in different market conditions:

  TRENDING (strong directional move):
    → MACD and moving average crossovers are RELIABLE
    → RSI overbought/oversold signals are UNRELIABLE (trend can persist)

  SIDEWAYS (price oscillating in a range):
    → RSI mean reversion is RELIABLE
    → MACD crossovers are UNRELIABLE (too many false signals)

  HIGH_VOL (chaotic, unpredictable):
    → Most indicators are UNRELIABLE
    → Risk agent should reduce position size or VETO

By detecting the regime first, signal_agent.py can adjust
which indicators it trusts and how much weight to give them.

REGIMES:
---------
  TRENDING_UP    : price making higher highs and higher lows
  TRENDING_DOWN  : price making lower highs and lower lows
  SIDEWAYS       : price oscillating with no clear direction
  HIGH_VOL       : extreme volatility — unpredictable conditions
  UNKNOWN        : not enough data to classify

HOW WE DETECT REGIME:
-----------------------
We use three signals together:

1. ADX (Average Directional Index)
   ADX > 25 → trending market (direction doesn't matter)
   ADX < 20 → sideways / ranging market

2. Price vs Moving Average
   Price above 20-period SMA → bullish direction
   Price below 20-period SMA → bearish direction

3. Volatility vs historical average
   Current volatility >> historical average → HIGH_VOL regime

USAGE FROM signal_agent.py:
-----------------------------
    from app.data.regime_detector import detect_regime

    regime = detect_regime(candles)
    # Returns: "TRENDING_UP" | "TRENDING_DOWN" | "SIDEWAYS" | "HIGH_VOL" | "UNKNOWN"
"""

import pandas as pd
import numpy as np
from loguru import logger


# ============================================================
# REGIME THRESHOLDS
# ============================================================

ADX_TRENDING_THRESHOLD  = 25.0    # ADX above this = trending
ADX_SIDEWAYS_THRESHOLD  = 20.0    # ADX below this = sideways
HIGH_VOL_MULTIPLIER     = 2.0     # current vol > 2x avg → high vol
MIN_CANDLES_REQUIRED    = 30      # need at least 30 candles for reliable detection


# ============================================================
# MAIN FUNCTION
# ============================================================

def detect_regime(candles: list[dict]) -> str:
    """
    Classify the current market regime from OHLCV candles.

    Parameters:
    -----------
    candles : list of OHLCV dicts
              [{"open": 100, "high": 105, "low": 98, "close": 103, ...}]

    Returns:
    --------
    "TRENDING_UP"   : strong uptrend
    "TRENDING_DOWN" : strong downtrend
    "SIDEWAYS"      : ranging, no clear direction
    "HIGH_VOL"      : extreme volatility — be cautious
    "UNKNOWN"       : not enough data

    This function is SYNCHRONOUS (no async) because it's pure math —
    no DB or network calls. Fast enough to call inline.
    """

    if len(candles) < MIN_CANDLES_REQUIRED:
        logger.debug(
            f"Regime: UNKNOWN (only {len(candles)} candles, "
            f"need {MIN_CANDLES_REQUIRED})"
        )
        return "UNKNOWN"

    try:
        # Build price series
        closes = pd.Series([float(c["close"]) for c in candles])
        highs  = pd.Series([float(c.get("high",  c["close"])) for c in candles])
        lows   = pd.Series([float(c.get("low",   c["close"])) for c in candles])

        # --------------------------------------------------------
        # STEP 1: DETECT HIGH VOLATILITY
        # --------------------------------------------------------
        # Compare current candle-to-candle volatility vs average.
        # If current volatility is 2x the long-run average,
        # the market is in a chaotic regime.

        returns          = closes.pct_change().dropna()
        current_vol      = float(returns.tail(10).std())    # recent 10 candles
        historical_vol   = float(returns.std())             # all candles

        if historical_vol > 0 and current_vol > (HIGH_VOL_MULTIPLIER * historical_vol):
            logger.debug(
                f"Regime: HIGH_VOL | "
                f"current_vol={current_vol:.4f} | "
                f"hist_vol={historical_vol:.4f}"
            )
            return "HIGH_VOL"

        # --------------------------------------------------------
        # STEP 2: CALCULATE ADX
        # --------------------------------------------------------
        # ADX measures TREND STRENGTH (not direction).
        # High ADX = strong trend. Low ADX = no trend / ranging.

        adx = _calculate_adx(highs, lows, closes, period=14)

        # --------------------------------------------------------
        # STEP 3: DETERMINE DIRECTION (if trending)
        # --------------------------------------------------------
        # Use 20-period SMA to find direction.
        # Price above SMA → uptrend. Below → downtrend.

        sma_20      = closes.rolling(window=20).mean()
        current_sma = float(sma_20.iloc[-1])
        current_price = float(closes.iloc[-1])

        above_sma = current_price > current_sma

        # --------------------------------------------------------
        # CLASSIFY REGIME
        # --------------------------------------------------------

        if adx > ADX_TRENDING_THRESHOLD:
            regime = "TRENDING_UP" if above_sma else "TRENDING_DOWN"

        elif adx < ADX_SIDEWAYS_THRESHOLD:
            regime = "SIDEWAYS"

        else:
            # ADX between 20-25: weakly trending or transitioning
            # We bias toward SIDEWAYS for caution
            regime = "SIDEWAYS"

        logger.debug(
            f"Regime: {regime} | ADX={adx:.2f} | "
            f"price={current_price:.2f} | SMA20={current_sma:.2f}"
        )

        return regime

    except Exception as e:
        logger.warning(f"Regime detection failed: {e}")
        return "UNKNOWN"


# ============================================================
# ADX CALCULATION
# ============================================================

def _calculate_adx(
    highs:  pd.Series,
    lows:   pd.Series,
    closes: pd.Series,
    period: int = 14,
) -> float:
    """
    Calculate the Average Directional Index (ADX).

    ADX measures TREND STRENGTH regardless of direction:
      ADX < 20 : no trend / ranging
      ADX 20-25: weak trend
      ADX > 25 : strong trend
      ADX > 50 : very strong trend

    Formula:
      1. True Range (TR) = max of:
           high - low
           abs(high - previous_close)
           abs(low  - previous_close)
      2. +DM = high - previous_high if positive, else 0
      3. -DM = previous_low - low if positive, else 0
      4. Smooth TR, +DM, -DM over `period` bars
      5. +DI = 100 × (+DM / TR)
      6. -DI = 100 × (-DM / TR)
      7. DX  = 100 × abs(+DI - -DI) / (+DI + -DI)
      8. ADX = smooth(DX) over `period` bars

    Returns the latest ADX value as a float.
    """

    # True Range
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move   = highs  - highs.shift(1)
    down_move = lows.shift(1) - lows

    plus_dm  = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move,   0.0),
        index=highs.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=highs.index
    )

    # Smooth over period
    atr      = tr.rolling(window=period).mean()
    plus_di  = 100 * (plus_dm.rolling(window=period).mean()  / atr)
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)

    # DX and ADX
    di_sum  = plus_di + minus_di
    di_sum  = di_sum.replace(0, 1e-10)      # avoid division by zero
    dx      = 100 * ((plus_di - minus_di).abs() / di_sum)
    adx     = dx.rolling(window=period).mean()

    latest  = adx.iloc[-1]

    return float(latest) if not pd.isna(latest) else 0.0