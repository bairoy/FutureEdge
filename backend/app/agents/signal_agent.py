"""
Signal Agent

Responsibilities:
-----------------
1. Read market candle data
2. Calculate technical indicators
3. Generate bullish/bearish score
4. Produce BUY / SELL / HOLD vote
5. Update shared LangGraph state

This agent acts like a technical analyst inside
the multi-agent trading system.
"""

# ============================================================
# IMPORTS
# ============================================================

# Used for numerical operations
import numpy as np

# Used for time-series data analysis
import pandas as pd

# Better structured logging library
from loguru import logger

# Shared state + schemas
from app.graph.state import (
    AgentState,
    AgentVote,
    MarketContext
)


# ============================================================
# RSI CALCULATION
# ============================================================

def calculate_rsi(
    prices: pd.Series,
    period: int = 14
) -> float:
    """
    Calculate Relative Strength Index (RSI)

    RSI measures momentum strength.

    Logic:
    -------
    Compare:
    - average gains
    - average losses

    Interpretation:
    ----------------
    RSI < 30:
        market may be oversold

    RSI > 70:
        market may be overbought
    """

    # --------------------------------------------------------
    # PRICE DIFFERENCE
    # --------------------------------------------------------

    """
    diff() calculates candle-to-candle movement.

    Example:
    100 → 102 → 101

    becomes:
    +2, -1
    """

    delta = prices.diff()


    # --------------------------------------------------------
    # AVERAGE GAINS
    # --------------------------------------------------------

    """
    Keep only positive movement.

    Negative values become 0.
    """

    gain = (
        delta.where(delta > 0, 0)
        .rolling(window=period)
        .mean()
    )


    # --------------------------------------------------------
    # AVERAGE LOSSES
    # --------------------------------------------------------

    """
    Keep only downward movement.

    Negative values converted into magnitude.
    """

    loss = (
        -delta.where(delta < 0, 0)
        .rolling(window=period)
        .mean()
    )


    # --------------------------------------------------------
    # SAFETY CHECK
    # --------------------------------------------------------

    """
    Prevent division by zero.

    If market only moved upward recently,
    loss can become 0.
    """

    loss = loss.replace(0, 1e-10)


    # --------------------------------------------------------
    # RELATIVE STRENGTH
    # --------------------------------------------------------

    rs = gain / loss


    # --------------------------------------------------------
    # RSI FORMULA
    # --------------------------------------------------------

    rsi = 100 - (100 / (1 + rs.iloc[-1]))

    return float(rsi)


# ============================================================
# MACD CALCULATION
# ============================================================

def calculate_macd(
    prices: pd.Series
) -> tuple[float, float, float]:
    """
    Calculate MACD indicator.

    MACD detects:
    - trend direction
    - momentum shifts
    - bullish/bearish crossovers
    """

    # --------------------------------------------------------
    # FAST EMA
    # --------------------------------------------------------

    """
    Short-term trend.
    Reacts faster to recent prices.
    """

    ema12 = prices.ewm(span=12).mean()


    # --------------------------------------------------------
    # SLOW EMA
    # --------------------------------------------------------

    """
    Long-term trend.
    """

    ema26 = prices.ewm(span=26).mean()


    # --------------------------------------------------------
    # MACD LINE
    # --------------------------------------------------------

    """
    Difference between fast and slow EMA.

    Positive:
        bullish momentum

    Negative:
        bearish momentum
    """

    macd = ema12 - ema26


    # --------------------------------------------------------
    # SIGNAL LINE
    # --------------------------------------------------------

    """
    Smoothed MACD average.

    Used for crossover detection.
    """

    signal = macd.ewm(span=9).mean()


    # --------------------------------------------------------
    # HISTOGRAM
    # --------------------------------------------------------

    """
    Difference between MACD and signal line.

    Helps measure momentum acceleration.
    """

    hist = macd - signal


    return (
        float(macd.iloc[-1]),
        float(signal.iloc[-1]),
        float(hist.iloc[-1])
    )


# ============================================================
# BOLLINGER BANDS
# ============================================================

def calculate_bollinger(
    prices: pd.Series,
    period: int = 20
) -> tuple[float, float, float]:
    """
    Calculate Bollinger Bands.

    Bollinger bands measure:
    - volatility
    - price expansion
    - stretched market conditions
    """

    # --------------------------------------------------------
    # MOVING AVERAGE
    # --------------------------------------------------------

    """
    Represents equilibrium price.
    """

    sma = prices.rolling(window=period).mean()


    # --------------------------------------------------------
    # STANDARD DEVIATION
    # --------------------------------------------------------

    """
    Measures volatility.

    High std:
        unstable market

    Low std:
        stable market
    """

    std = prices.rolling(window=period).std()


    # --------------------------------------------------------
    # UPPER BAND
    # --------------------------------------------------------

    upper = sma + (std * 2)


    # --------------------------------------------------------
    # LOWER BAND
    # --------------------------------------------------------

    lower = sma - (std * 2)


    return (
        float(upper.iloc[-1]),
        float(sma.iloc[-1]),
        float(lower.iloc[-1])
    )


# ============================================================
# SIGNAL AGENT NODE
# ============================================================

def signal_agent_node(
    state: AgentState
) -> dict:
    """
    Main LangGraph node for technical analysis.

    Workflow:
    ----------
    Market Data
        ↓
    Technical Indicators
        ↓
    Signal Scoring
        ↓
    Confidence Estimation
        ↓
    Agent Vote
    """

    try:

        # ====================================================
        # EXTRACT MARKET CONTEXT
        # ====================================================

        """
        Shared LangGraph state contains
        current market information.
        """

        ctx: MarketContext = state["market_context"]

        symbol = ctx.symbol
        price = ctx.current_price


        # ====================================================
        # VALIDATE MARKET DATA
        # ====================================================

        """
        Technical indicators require historical candles.

        Without candles:
        - no momentum analysis
        - no trend analysis
        - no volatility analysis
        """

        if not ctx.ohlcv_1m:

            hold_vote = AgentVote(
                agent="SignalAgent",
                decision="HOLD",
                confidence=0.5,
                reasoning="Insufficient market data"
            )

            return {
                "signal_vote": hold_vote,
            }


        # ====================================================
        # BUILD PRICE SERIES
        # ====================================================

        """
        Extract closing prices from candles.

        Most indicators use close prices because:
        - they represent final market consensus
        - industry standard
        """

        closes = pd.Series([
            candle["close"]
            for candle in ctx.ohlcv_1m
        ])


        # ====================================================
        # MINIMUM DATA CHECK
        # ====================================================

        """
        Indicators like MACD/Bollinger require
        sufficient historical candles.

        Too little data causes unstable indicators.
        """

        if len(closes) < 30:

            hold_vote = AgentVote(
                agent="SignalAgent",
                decision="HOLD",
                confidence=0.4,
                reasoning="Not enough candles for stable indicators"
            )

            return {
                "signal_vote": hold_vote,
            }


        # ====================================================
        # CALCULATE INDICATORS
        # ====================================================

        """
        Convert raw prices into structured signals.

        This process is called:
        FEATURE EXTRACTION
        """

        rsi = calculate_rsi(closes)

        macd_val, signal_val, hist = calculate_macd(closes)

        upper, middle, lower = calculate_bollinger(closes)


        # ====================================================
        # SIGNAL SCORING
        # ====================================================

        """
        Instead of direct decisions,
        combine evidence gradually.

        Positive score:
            bullish bias

        Negative score:
            bearish bias
        """

        score = 0.0

        """
        reasons stores explainability data.

        Important for:
        - debugging
        - audit systems
        - human review
        """

        reasons = []


        # ====================================================
        # RSI ANALYSIS
        # ====================================================

        """
        RSI gives medium-strength reversal signal.

        Weight:
            0.3

        Why not too high?
        -----------------
        RSI alone is unreliable.
        """

        if rsi < 30:

            """
            Market heavily sold recently.

            Sellers may be exhausted.
            Possible upward reversal.
            """

            score += 0.3

            reasons.append(
                f"RSI oversold ({rsi:.1f})"
            )

        elif rsi > 70:

            """
            Market heavily bought recently.

            Buyers may be exhausted.
            Possible downward reversal.
            """

            score -= 0.3

            reasons.append(
                f"RSI overbought ({rsi:.1f})"
            )


        # ====================================================
        # MACD ANALYSIS
        # ====================================================

        """
        MACD is stronger for trend-following.

        Detects momentum shift.
        """

        if hist > 0 and macd_val > signal_val:

            """
            Bullish momentum strengthening.
            """

            score += 0.25

            reasons.append(
                "MACD bullish crossover"
            )

        elif hist < 0 and macd_val < signal_val:

            """
            Bearish momentum strengthening.
            """

            score -= 0.25

            reasons.append(
                "MACD bearish crossover"
            )


        # ====================================================
        # BOLLINGER BAND ANALYSIS
        # ====================================================

        """
        Bollinger bands identify stretched prices.

        Mean reversion assumption:
        price tends to return toward average.
        """

        if price < lower:

            """
            Price stretched downward.

            Possible rebound zone.
            """

            score += 0.2

            reasons.append(
                "Price below lower Bollinger band"
            )

        elif price > upper:

            """
            Price stretched upward.

            Possible pullback zone.
            """

            score -= 0.2

            reasons.append(
                "Price above upper Bollinger band"
            )


        # ====================================================
        # VOLATILITY FILTER
        # ====================================================

        """
        High volatility reduces prediction reliability.

        Even strong indicators become less trustworthy
        during chaotic markets.
        """

        if ctx.volatility_24h > 0.05:

            """
            Reduce confidence without changing direction.
            """

            score *= 0.7

            reasons.append(
                f"High volatility "
                f"({ctx.volatility_24h*100:.1f}%)"
            )


        # ====================================================
        # FINAL DECISION
        # ====================================================

        """
        Convert continuous score
        into discrete action.

        Thresholds reduce overtrading.
        """

        if score > 0.2:

            decision = "BUY"

            """
            Stronger score → higher confidence.
            """

            confidence = min(
                0.5 + score,
                0.95
            )

        elif score < -0.2:

            decision = "SELL"

            confidence = min(
                0.5 + abs(score),
                0.95
            )

        else:

            """
            Weak uncertain signal.

            Safest action:
            HOLD
            """

            decision = "HOLD"

            confidence = 0.5 + abs(score)


        # ====================================================
        # BUILD EXPLANATION
        # ====================================================

        """
        Convert reasoning list
        into readable explanation.
        """

        reasoning = (
            " | ".join(reasons)
            if reasons
            else "No strong signals"
        )


        # ====================================================
        # CREATE FINAL VOTE
        # ====================================================

        vote = AgentVote(

            agent="SignalAgent",

            decision=decision,

            confidence=round(confidence, 3),

            reasoning=reasoning,

            metadata={

                # Store indicators for:
                # - debugging
                # - observability
                # - orchestrator analysis

                "rsi": round(rsi, 2),

                "macd": round(macd_val, 4),

                "macd_signal": round(signal_val, 4),

                "macd_hist": round(hist, 4),

                "bollinger_upper": round(upper, 2),

                "bollinger_middle": round(middle, 2),

                "bollinger_lower": round(lower, 2),

                "volatility_24h": round(
                    ctx.volatility_24h,
                    4
                ),

                "final_score": round(score, 4)
            }
        )


        # ====================================================
        # LOG RESULT
        # ====================================================

        logger.info(
            f"📡 SignalAgent | "
            f"{symbol} | "
            f"{decision} | "
            f"Confidence={confidence:.2f}"
        )


        # ====================================================
        # RETURN LANGGRAPH STATE UPDATE
        # ====================================================

        """
        LangGraph merges this partial update
        into global workflow state.
        """

        return {

            # Main agent output
            "signal_vote": vote,

            # Observability updates

            "completed_nodes": [
           
                "signal_agent"
            ],

            "logs": [
                
                (
                    f"SignalAgent generated "
                    f"{decision} for {symbol}"
                )
            ]
        }


    # ========================================================
    # FAILSAFE HANDLING
    # ========================================================

    except Exception as e:

        """
        Trading systems should fail safely.

        If signal generation crashes:
        - avoid accidental trading
        - return HOLD
        """

        logger.exception(
            f"SignalAgent failure: {str(e)}"
        )

        error_vote = AgentVote(
            agent="SignalAgent",
            decision="HOLD",
            confidence=0.1,
            reasoning=f"Signal agent error: {str(e)}"
        )

        return {

            "signal_vote": error_vote,

            "execution_error": str(e),


            "logs": [
                
                f"SignalAgent failed: {str(e)}"
            ]
        }