"""
app/memory/embedder.py
=======================
Converts a market snapshot into a 12-dimensional numerical vector
for storage and similarity search in Qdrant.

WHY A CUSTOM EMBEDDER (NOT A LANGUAGE MODEL)?
----------------------------------------------
Market data is structured numbers — RSI, MACD, volatility etc.
Language model embeddings (like sentence-transformers) are
designed for text. Using them for numerical market data would
produce meaningless similarity scores.

Instead, we hand-craft a normalised feature vector where:
  - Every feature is scaled to roughly [0, 1] or [-1, 1]
  - Similar market conditions → similar vectors
  - Cosine similarity in Qdrant gives meaningful results

VECTOR STRUCTURE (12 dimensions):
-----------------------------------
Index  Feature                         Range
-----  -------                         -----
  0    RSI normalised                  0.0 to 1.0  (RSI/100)
  1    MACD histogram normalised       ~-1.0 to 1.0
  2    Bollinger band position         0.0 to 1.0  (where is price in band?)
  3    Volatility 24h normalised       0.0 to 1.0  (capped at 20%)
  4    Sentiment score                 -1.0 to 1.0
  5    Regime: TRENDING_UP             0 or 1  (one-hot)
  6    Regime: TRENDING_DOWN           0 or 1
  7    Regime: SIDEWAYS                0 or 1
  8    Regime: HIGH_VOL                0 or 1
  9    Buy score (orchestrator)        0.0 to 1.0
 10    Sell score (orchestrator)       0.0 to 1.0
 11    Risk score (orchestrator)       0.0 to 1.0

USAGE:
------
    from app.memory.embedder import build_market_vector

    vector = build_market_vector(
        rsi=65.0,
        macd_hist=1.2,
        bollinger_upper=22600,
        bollinger_lower=22000,
        current_price=22300,
        volatility_24h=0.015,
        sentiment_score=0.4,
        regime="TRENDING_UP",
        buy_score=0.62,
        sell_score=0.1,
        risk_score=0.38,
    )
    # Returns: [0.65, 0.12, 0.50, 0.075, 0.4, 1, 0, 0, 0, 0.62, 0.1, 0.38]
"""


def build_market_vector(
    rsi:              float,
    macd_hist:        float,
    bollinger_upper:  float,
    bollinger_lower:  float,
    current_price:    float,
    volatility_24h:   float,
    sentiment_score:  float,
    regime:           str,
    buy_score:        float,
    sell_score:       float,
    risk_score:       float,
) -> list[float]:
    """
    Convert market snapshot values into a normalised 12-dim vector.

    All values are clipped/normalised so they fall within [0,1] or [-1,1].
    This prevents any single feature from dominating the similarity score.

    Parameters:
    -----------
    rsi             : RSI value (0 to 100)
    macd_hist       : MACD histogram value (can be negative)
    bollinger_upper : upper Bollinger band price
    bollinger_lower : lower Bollinger band price
    current_price   : current market price
    volatility_24h  : 24-hour volatility as fraction (e.g. 0.02 = 2%)
    sentiment_score : from SentimentAgent (-1.0 bearish to +1.0 bullish)
    regime          : TRENDING_UP | TRENDING_DOWN | SIDEWAYS | HIGH_VOL | UNKNOWN
    buy_score       : orchestrator's normalised buy score (0 to 1)
    sell_score      : orchestrator's normalised sell score (0 to 1)
    risk_score      : orchestrator's risk score (0 to 1)

    Returns:
    --------
    list of 12 floats, all normalised
    """

    # --------------------------------------------------------
    # FEATURE 0: RSI normalised
    # RSI is already 0-100. Divide by 100 to get 0-1.
    # --------------------------------------------------------
    rsi_norm = _clip(rsi / 100.0, 0.0, 1.0)

    # --------------------------------------------------------
    # FEATURE 1: MACD histogram normalised
    # MACD histogram can be any value. We normalise using tanh
    # which maps any real number to (-1, 1) smoothly.
    # Small values stay small. Large values are compressed toward ±1.
    # --------------------------------------------------------
    import math
    macd_norm = math.tanh(macd_hist / 10.0)   # /10 scales typical values

    # --------------------------------------------------------
    # FEATURE 2: Bollinger band position
    # Where is price within the Bollinger bands?
    # 0.0 = at lower band, 0.5 = at middle, 1.0 = at upper band
    # This captures mean-reversion potential.
    # --------------------------------------------------------
    band_range = bollinger_upper - bollinger_lower

    if band_range > 0:
        bollinger_pos = _clip(
            (current_price - bollinger_lower) / band_range,
            0.0, 1.0
        )
    else:
        bollinger_pos = 0.5   # no band info → neutral

    # --------------------------------------------------------
    # FEATURE 3: Volatility normalised
    # Cap at 20% (0.20) to avoid extreme values dominating.
    # Most normal trading days have volatility < 5%.
    # --------------------------------------------------------
    vol_norm = _clip(volatility_24h / 0.20, 0.0, 1.0)

    # --------------------------------------------------------
    # FEATURE 4: Sentiment score
    # Already in [-1, 1] from SentimentAgent.
    # --------------------------------------------------------
    sentiment_norm = _clip(sentiment_score, -1.0, 1.0)

    # --------------------------------------------------------
    # FEATURES 5-8: Regime one-hot encoding
    # One-hot means exactly one of these is 1, the rest are 0.
    # This lets Qdrant find memories with the same market regime.
    # --------------------------------------------------------
    regime_upper = regime.upper()

    regime_trending_up   = 1.0 if regime_upper == "TRENDING_UP"   else 0.0
    regime_trending_down = 1.0 if regime_upper == "TRENDING_DOWN"  else 0.0
    regime_sideways      = 1.0 if regime_upper == "SIDEWAYS"       else 0.0
    regime_high_vol      = 1.0 if regime_upper == "HIGH_VOL"       else 0.0

    # --------------------------------------------------------
    # FEATURES 9-11: Orchestrator scores
    # Already in [0, 1].
    # --------------------------------------------------------
    buy_norm  = _clip(buy_score,  0.0, 1.0)
    sell_norm = _clip(sell_score, 0.0, 1.0)
    risk_norm = _clip(risk_score, 0.0, 1.0)

    # --------------------------------------------------------
    # ASSEMBLE VECTOR (must be exactly 12 floats)
    # --------------------------------------------------------
    vector = [
        rsi_norm,           # dim 0
        macd_norm,          # dim 1
        bollinger_pos,      # dim 2
        vol_norm,           # dim 3
        sentiment_norm,     # dim 4
        regime_trending_up, # dim 5
        regime_trending_down, # dim 6
        regime_sideways,    # dim 7
        regime_high_vol,    # dim 8
        buy_norm,           # dim 9
        sell_norm,          # dim 10
        risk_norm,          # dim 11
    ]

    return [round(v, 6) for v in vector]


# ============================================================
# HELPER
# ============================================================

def _clip(value: float, min_val: float, max_val: float) -> float:
    """Clip a value between min and max."""
    return max(min_val, min(max_val, value))