"""
app/data/indicator_cache.py
============================
Compute RSI, MACD, and Bollinger Bands — then cache in Redis.

THE PROBLEM THIS SOLVES:
--------------------------
Without caching, every agent cycle recomputes all indicators
from scratch by processing the full candle history.
With 60 candles and 3 indicators this is fast, but at scale
(hundreds of symbols, sub-second cycles) it becomes a bottleneck.

THE SOLUTION:
--------------
After computing indicators once, store the result in Redis
with a 60-second TTL (time to live).

On the next cycle:
  - Check Redis first
  - If the cached value is still fresh → use it (fast, no compute)
  - If it expired or does not exist → recompute and cache again

HOW TTL IS CHOSEN:
-------------------
60 seconds = one 1-minute candle duration.
This means we recompute at most once per new candle.
After each new candle arrives, the cache expires and fresh
indicators are computed on the next agent cycle.

USAGE FROM signal_agent.py:
-----------------------------
    from app.data.indicator_cache import get_indicators

    indicators = await get_indicators(symbol, candles)
    rsi  = indicators["rsi"]
    macd = indicators["macd"]
"""

import json
import pandas as pd
from loguru import logger

from app.db.redis import redis_client, KEY_INDICATORS


# ============================================================
# CACHE TTL
# ============================================================

# Indicators are valid for 60 seconds (one 1-minute candle)
INDICATOR_TTL_SECONDS = 60


# ============================================================
# MAIN ENTRY POINT
# ============================================================

async def get_indicators(
    symbol:  str,
    candles: list[dict],
) -> dict:
    """
    Return computed indicators for a symbol.

    Checks Redis cache first. If cache hit, return cached values.
    If cache miss (expired or first time), compute from candles
    and store in Redis.

    Parameters:
    -----------
    symbol  : e.g. "NIFTY 50" or "RELIANCE"
    candles : list of OHLCV dicts [{"close": 100.0, ...}, ...]

    Returns:
    --------
    {
        "rsi":               float,   # 0 to 100
        "macd":              float,   # MACD line value
        "macd_signal":       float,   # signal line value
        "macd_hist":         float,   # histogram = macd - signal
        "bollinger_upper":   float,
        "bollinger_middle":  float,
        "bollinger_lower":   float,
        "current_price":     float,   # last close price
        "from_cache":        bool,    # True if served from Redis
    }
    """

    cache_key = KEY_INDICATORS.format(symbol=symbol.replace(" ", "_"))

    # --------------------------------------------------------
    # TRY CACHE FIRST
    # --------------------------------------------------------

    try:
        cached = await redis_client.get(cache_key)

        if cached:
            result = json.loads(cached)
            result["from_cache"] = True
            logger.debug(f"📦 Indicators from cache | {symbol}")
            return result

    except Exception as e:
        # Cache read failure is non-fatal — just compute fresh
        logger.warning(f"Indicator cache read failed: {e}")

    # --------------------------------------------------------
    # COMPUTE FRESH INDICATORS
    # --------------------------------------------------------

    result = _compute_indicators(candles)
    result["from_cache"] = False

    # --------------------------------------------------------
    # STORE IN REDIS WITH TTL
    # --------------------------------------------------------

    try:
        await redis_client.setex(
            cache_key,
            INDICATOR_TTL_SECONDS,
            json.dumps(result),
        )
        logger.debug(f"💾 Indicators cached | {symbol} | TTL={INDICATOR_TTL_SECONDS}s")

    except Exception as e:
        logger.warning(f"Indicator cache write failed: {e}")

    return result


# ============================================================
# INDICATOR COMPUTATIONS
# ============================================================

def _compute_indicators(candles: list[dict]) -> dict:
    """
    Compute all indicators from a list of OHLCV candles.

    This is pure Python/pandas — no external API calls.
    Returns default safe values if not enough candles.
    """

    if len(candles) < 30:
        return _empty_indicators(candles)

    # Extract closing prices as a pandas Series
    # pandas makes rolling calculations easy
    closes = pd.Series([c["close"] for c in candles], dtype=float)

    return {
        "rsi":              _calc_rsi(closes),
        "macd":             _calc_macd(closes)[0],
        "macd_signal":      _calc_macd(closes)[1],
        "macd_hist":        _calc_macd(closes)[2],
        "bollinger_upper":  _calc_bollinger(closes)[0],
        "bollinger_middle": _calc_bollinger(closes)[1],
        "bollinger_lower":  _calc_bollinger(closes)[2],
        "current_price":    float(closes.iloc[-1]),
    }


def _empty_indicators(candles: list[dict]) -> dict:
    """Return safe defaults when not enough candle history."""

    last_price = float(candles[-1]["close"]) if candles else 0.0

    return {
        "rsi":              50.0,
        "macd":             0.0,
        "macd_signal":      0.0,
        "macd_hist":        0.0,
        "bollinger_upper":  last_price * 1.02,
        "bollinger_middle": last_price,
        "bollinger_lower":  last_price * 0.98,
        "current_price":    last_price,
    }


# --------------------------------------------------------
# RSI  (Relative Strength Index)
# --------------------------------------------------------

def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
    """
    RSI measures momentum.

    Interpretation:
      RSI < 30 → oversold  → possible buy signal
      RSI > 70 → overbought → possible sell signal
      RSI = 50 → neutral

    Formula:
      RSI = 100 - (100 / (1 + RS))
      RS  = average gain / average loss over N periods
    """

    delta = prices.diff()

    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)

    # Welles Wilder's Smoothing RMA is equivalent to an EMA with alpha = 1 / period
    avg_gain = gain.ewm(alpha=1.0/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, adjust=False).mean()

    # Avoid division by zero
    avg_loss = avg_loss.replace(0, 1e-10)

    rs  = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs.iloc[-1]))

    return round(float(rsi), 2)


# --------------------------------------------------------
# MACD  (Moving Average Convergence Divergence)
# --------------------------------------------------------

def _calc_macd(
    prices: pd.Series,
    fast:   int = 12,
    slow:   int = 26,
    signal: int = 9,
) -> tuple[float, float, float]:
    """
    MACD detects trend direction and momentum shifts.

    Three values returned:
      macd_line   = fast EMA - slow EMA
      signal_line = 9-period EMA of macd_line
      histogram   = macd_line - signal_line

    Interpretation:
      histogram > 0 and rising → bullish momentum
      histogram < 0 and falling → bearish momentum
      histogram crosses zero → potential trend change
    """

    ema_fast   = prices.ewm(span=fast,   adjust=False).mean()
    ema_slow   = prices.ewm(span=slow,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line

    return (
        round(float(macd_line.iloc[-1]),   4),
        round(float(signal_line.iloc[-1]), 4),
        round(float(histogram.iloc[-1]),   4),
    )


# --------------------------------------------------------
# BOLLINGER BANDS
# --------------------------------------------------------

def _calc_bollinger(
    prices: pd.Series,
    period: int = 20,
    std:    int = 2,
) -> tuple[float, float, float]:
    """
    Bollinger Bands measure volatility and price extremes.

    Three lines:
      upper  = 20-day SMA + 2 standard deviations
      middle = 20-day SMA (simple moving average)
      lower  = 20-day SMA - 2 standard deviations

    Interpretation:
      price near upper band → potentially overbought
      price near lower band → potentially oversold
      bands narrow → low volatility, breakout likely soon
      bands widen  → high volatility
    """

    sma   = prices.rolling(window=period).mean()
    stdev = prices.rolling(window=period).std()

    upper  = sma + (stdev * std)
    lower  = sma - (stdev * std)

    return (
        round(float(upper.iloc[-1]),  2),
        round(float(sma.iloc[-1]),    2),
        round(float(lower.iloc[-1]),  2),
    )