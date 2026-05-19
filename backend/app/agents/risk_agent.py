"""
Risk Agent

Responsibilities:
-----------------
1. Perform deterministic risk checks
2. Protect portfolio from catastrophic loss
3. Monitor leverage and exposure
4. Control volatility risk
5. Provide independent VETO authority

IMPORTANT:
-----------
This agent should NEVER rely on LLMs.

Reason:
--------
Risk systems must be:
- deterministic
- auditable
- mathematically consistent
- regulator-friendly

Institutional systems always separate:
- prediction systems
- risk systems

Prediction can be probabilistic.
Risk management must be strict.
"""

# ============================================================
# IMPORTS
# ============================================================

# Mathematical utilities
import math

# Structured logging
from loguru import logger

# Shared state and schemas
from app.graph.state import (
    AgentState,
    AgentVote,
    PortfolioSnapshot,
    MarketContext
)


# ============================================================
# VALUE AT RISK (VAR)
# ============================================================

def calculate_var(
    returns: list[float],
    confidence: float = 0.95
) -> float:
    """
    Historical Value at Risk (VaR)

    VaR estimates:
    ----------------
    Maximum expected loss under normal conditions.

    Example:
    ----------
    95% VaR = 2%

    Means:
    -------
    We expect losses greater than 2%
    only 5% of the time.

    Widely used in:
    - hedge funds
    - banks
    - institutional trading
    """

    # --------------------------------------------------------
    # EMPTY DATA CHECK
    # --------------------------------------------------------

    if not returns:
        return 0.0


    # --------------------------------------------------------
    # SORT RETURNS
    # --------------------------------------------------------

    """
    Worst losses move to beginning.
    """

    sorted_returns = sorted(returns)


    # --------------------------------------------------------
    # VAR INDEX
    # --------------------------------------------------------

    """
    Example:
    confidence = 95%

    Then:
    use worst 5% region.
    """

    index = int(
        (1 - confidence)
        * len(sorted_returns)
    )


    # --------------------------------------------------------
    # RETURN ABSOLUTE LOSS
    # --------------------------------------------------------

    return abs(sorted_returns[index])


# ============================================================
# KELLY CRITERION
# ============================================================

def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float
) -> float:
    """
    Kelly Criterion

    Used for optimal position sizing.

    Purpose:
    --------
    Maximize long-term capital growth
    while minimizing probability of ruin.

    Formula balances:
    - win probability
    - reward/risk ratio

    IMPORTANT:
    ----------
    Full Kelly is extremely aggressive.

    Real systems often use:
    - half Kelly
    - quarter Kelly
    """

    # --------------------------------------------------------
    # SAFETY CHECK
    # --------------------------------------------------------

    if avg_loss == 0:
        return 0.0


    # --------------------------------------------------------
    # WIN/LOSS RATIO
    # --------------------------------------------------------

    """
    Example:
    avg_win = 200
    avg_loss = 100

    b = 2
    """

    b = avg_win / avg_loss


    # --------------------------------------------------------
    # LOSS PROBABILITY
    # --------------------------------------------------------

    q = 1 - win_rate


    # --------------------------------------------------------
    # KELLY FORMULA
    # --------------------------------------------------------

    """
    Kelly Formula:

    (p*b - q) / b
    """

    kelly = (
        (win_rate * b - q)
        / b
    )


    # --------------------------------------------------------
    # SAFETY CAP
    # --------------------------------------------------------

    """
    Prevent overleveraging.

    Real systems cap position size
    to avoid catastrophic ruin.
    """

    return max(
        0.0,
        min(kelly, 0.25)
    )


# ============================================================
# RISK AGENT NODE
# ============================================================

def risk_agent_node(
    state: AgentState
) -> dict:
    """
    Main LangGraph node for risk management.

    Workflow:
    ----------
    Portfolio State
        ↓
    Margin Checks
        ↓
    Exposure Checks
        ↓
    Volatility Checks
        ↓
    Risk Decision

    IMPORTANT:
    ----------
    Risk agent does NOT predict market.

    It protects capital.
    """

    try:

        # ====================================================
        # EXTRACT STATE
        # ====================================================

        portfolio: PortfolioSnapshot = state["portfolio"]

        market_ctx: MarketContext = (
            state["market_context"]
        )

        symbol = market_ctx.symbol


        # ====================================================
        # DEFAULT RISK POSTURE
        # ====================================================

        """
        Risk agent normally stays neutral.

        It only:
        - approves
        - warns
        - vetoes

        It does NOT initiate trades.
        """

        decision = "HOLD"

        confidence = 0.8

        reasons = []

        metadata = {}


        # ====================================================
        # MARGIN UTILIZATION CHECK
        # ====================================================

        """
        Margin utilization measures leverage usage.

        Formula:
        --------
        margin_used / total_equity

        Example:
        --------
        Equity = $100,000
        Margin Used = $80,000

        Utilization = 80%

        High utilization increases liquidation risk.
        """

        if portfolio.total_equity > 0:

            margin_utilization = (
                portfolio.margin_used
                / portfolio.total_equity
            )

        else:

            margin_utilization = 0


        metadata["margin_utilization"] = round(
            margin_utilization,
            4
        )


        # ----------------------------------------------------
        # EXTREME MARGIN RISK
        # ----------------------------------------------------

        if margin_utilization > 0.8:

            """
            Above 80% leverage usage.

            Very dangerous.

            Risk agent activates VETO.
            """

            decision = "VETO"

            confidence = 0.99

            reasons.append(
                f"Margin utilization "
                f"{margin_utilization*100:.1f}% "
                f"> 80% limit"
            )


        # ----------------------------------------------------
        # ELEVATED RISK
        # ----------------------------------------------------

        elif margin_utilization > 0.6:

            """
            Elevated leverage.

            Not catastrophic yet,
            but needs monitoring.
            """

            reasons.append(
                f"Margin utilization "
                f"{margin_utilization*100:.1f}% "
                f"- elevated"
            )


        # ====================================================
        # DRAWDOWN LIMIT CHECK
        # ====================================================

        """
        Drawdown = peak-to-loss decline.

        Institutional systems enforce:
        maximum survivable drawdown.

        Example:
        --------
        15% drawdown limit.
        """

        max_dd_limit = (
            portfolio.max_drawdown_limit
        )

        metadata["max_drawdown_limit"] = (
            max_dd_limit
        )


        # ====================================================
        # POSITION CONCENTRATION CHECK
        # ====================================================

        """
        Prevent excessive concentration
        in single asset.

        Diversification reduces:
        - tail risk
        - catastrophic exposure
        """

        symbol_exposure = sum(

            position.get("notional", 0)

            for position in portfolio.open_positions

            if position.get("symbol") == symbol
        )


        # ----------------------------------------------------
        # EXPOSURE PERCENTAGE
        # ----------------------------------------------------

        if portfolio.total_equity > 0:

            exposure_pct = (
                symbol_exposure
                / portfolio.total_equity
            )

        else:

            exposure_pct = 0


        metadata["symbol_exposure_pct"] = round(
            exposure_pct,
            4
        )


        # ----------------------------------------------------
        # OVER-CONCENTRATION
        # ----------------------------------------------------

        if exposure_pct > 0.3:

            """
            More than 30% exposure
            in single symbol.

            Dangerous concentration risk.
            """

            decision = "VETO"

            confidence = 0.95

            reasons.append(
                f"{symbol} exposure "
                f"{exposure_pct*100:.1f}% "
                f"> 30% limit"
            )


        # ----------------------------------------------------
        # ELEVATED CONCENTRATION
        # ----------------------------------------------------

        elif exposure_pct > 0.2:

            reasons.append(
                f"{symbol} exposure "
                f"{exposure_pct*100:.1f}% "
                f"- consider reducing"
            )


        # ====================================================
        # KELLY POSITION SIZING
        # ====================================================

        """
        Simulated conservative Kelly sizing.

        Production version should use:
        - real trade history
        - win rate statistics
        - dynamic optimization
        """

        kelly_size = 0.02


        metadata["kelly_fraction"] = (
            kelly_size
        )

        metadata["suggested_position_pct"] = round(
            kelly_size * 100,
            2
        )


        # ====================================================
        # VOLATILITY REGIME CHECK
        # ====================================================

        """
        High volatility increases:
        - slippage
        - liquidation risk
        - unpredictability

        Risk systems often reduce
        or halt trading during chaos.
        """

        vol = market_ctx.volatility_24h


        # ----------------------------------------------------
        # EXTREME VOLATILITY
        # ----------------------------------------------------

        if vol > 0.08:

            """
            Above 8% daily volatility.

            Market considered unstable.
            """

            decision = "VETO"

            confidence = 0.9

            reasons.append(
                f"Extreme volatility "
                f"{vol*100:.1f}% "
                f"- trading halted"
            )


        # ----------------------------------------------------
        # HIGH VOLATILITY
        # ----------------------------------------------------

        elif vol > 0.05:

            reasons.append(
                f"High volatility "
                f"{vol*100:.1f}% "
                f"- reduced size recommended"
            )


        # ====================================================
        # FINAL SAFE STATUS
        # ====================================================

        """
        If no warnings triggered,
        portfolio considered healthy.
        """

        if not reasons:

            reasons.append(
                "All risk checks passed"
            )

            """
            HOLD means:
            no veto triggered.
            """

            decision = "HOLD"


        # ====================================================
        # CREATE FINAL VOTE
        # ====================================================

        vote = AgentVote(

            agent="RiskAgent",

            decision=decision,

            confidence=round(confidence, 3),

            reasoning=" | ".join(reasons),

            metadata=metadata
        )


        # ====================================================
        # LOG RESULT
        # ====================================================

        logger.info(
            f"🛡️ RiskAgent | "
            f"{symbol} | "
            f"{decision} | "
            f"Confidence={confidence:.2f}"
        )


        # ====================================================
        # RETURN LANGGRAPH STATE UPDATE
        # ====================================================

        return {

            # Main risk output
            "risk_vote": vote,

            # Workflow observability

            "completed_nodes": [
               
                "risk_agent"
            ],

            "logs": [
                
                (
                    f"RiskAgent generated "
                    f"{decision} for {symbol}"
                )
            ]
        }


    # ========================================================
    # FAILSAFE HANDLING
    # ========================================================

    except Exception as e:

        """
        Risk systems must fail safely.

        If risk engine crashes:
        safest action = VETO
        """

        logger.exception(
            f"RiskAgent failure: {str(e)}"
        )

        error_vote = AgentVote(

            agent="RiskAgent",

            decision="VETO",

            confidence=1.0,

            reasoning=(
                f"Risk system failure: {str(e)}"
            )
        )

        return {

            "risk_vote": error_vote,

            "execution_error": str(e),


            "logs": [
                
                f"RiskAgent failed: {str(e)}"
            ]
        }