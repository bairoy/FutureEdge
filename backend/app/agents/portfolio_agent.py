"""
Portfolio Agent

Responsibilities:
-----------------
1. Monitor portfolio health
2. Track current exposure
3. Prevent excessive position growth
4. Protect capital allocation balance
5. Provide portfolio-level risk awareness

IMPORTANT:
-----------
This agent does NOT predict market direction.

Signal Agent asks:
    "Should we trade?"

Portfolio Agent asks:
    "Can the portfolio safely support this trade?"
"""

# ============================================================
# IMPORTS
# ============================================================

# Structured logging
from loguru import logger

# Shared state + schemas
from app.graph.state import (
    AgentState,
    AgentVote,
    PortfolioSnapshot,
    MarketContext
)


# ============================================================
# PORTFOLIO AGENT NODE
# ============================================================

def portfolio_agent_node(
    state: AgentState
) -> dict:
    """
    Main LangGraph node for portfolio analysis.

    Workflow:
    ----------
    Portfolio State
        ↓
    Exposure Analysis
        ↓
    Capacity Analysis
        ↓
    Margin Analysis
        ↓
    Portfolio Decision
    """

    try:

        # ====================================================
        # EXTRACT SHARED STATE
        # ====================================================

        """
        Read portfolio and market data
        from shared LangGraph state.
        """

        portfolio: PortfolioSnapshot = state["portfolio"]

        market_ctx: MarketContext = state["market_context"]

        symbol = market_ctx.symbol


        # ====================================================
        # OPEN POSITION COUNT
        # ====================================================

        """
        Count currently active positions.

        Too many positions can create:
        - correlation risk
        - management complexity
        - hidden exposure
        """

        open_count = len(
            portfolio.open_positions
        )


        # ====================================================
        # TOTAL PORTFOLIO EXPOSURE
        # ====================================================

        """
        Calculate total capital exposed
        across all positions.

        Example:
        --------
        BTC = $10,000
        ETH = $5,000

        Total Exposure = $15,000
        """

        total_exposure = sum(
            position.get("notional", 0)
            for position in portfolio.open_positions
        )


        # ====================================================
        # METADATA STORAGE
        # ====================================================

        """
        Metadata improves:
        - debugging
        - observability
        - auditability
        - orchestrator intelligence
        """

        metadata = {

            "open_positions_count": open_count,

            "total_exposure": round(
                total_exposure,
                2
            ),

            "unrealized_pnl": round(
                portfolio.unrealized_pnl,
                2
            ),

            "margin_available": round(
                portfolio.margin_available,
                2
            )
        }


        # ====================================================
        # REASON STORAGE
        # ====================================================

        """
        Human-readable explanations.

        Important for:
        - explainability
        - debugging
        - human review
        """

        reasons = []


        # ====================================================
        # DEFAULT PORTFOLIO POSTURE
        # ====================================================

        """
        Portfolio agent normally stays neutral.

        It mainly:
        - monitors
        - warns
        - vetoes
        """

        decision = "HOLD"

        confidence = 0.6


        # ====================================================
        # MAX POSITION CHECK
        # ====================================================

        """
        Prevent excessive simultaneous positions.

        Too many positions increase:
        - complexity
        - hidden correlations
        - operational risk
        """

        if open_count >= 5:

            """
            Portfolio already saturated.

            Opening more positions may reduce
            risk control effectiveness.
            """

            reasons.append(
                f"Max positions "
                f"({open_count}) reached "
                f"- close before opening new"
            )

            decision = "VETO"

            confidence = 0.85


        # ====================================================
        # LOW AVAILABLE MARGIN CHECK
        # ====================================================

        elif portfolio.margin_available < (
            portfolio.total_equity * 0.2
        ):

            """
            Less than 20% capital remaining.

            Low flexibility increases risk.
            """

            reasons.append(
                "Low available margin "
                "- avoid new positions"
            )

            decision = "HOLD"

            confidence = 0.7


        # ====================================================
        # HEALTHY PORTFOLIO STATE
        # ====================================================

        else:

            """
            Portfolio currently has:
            - manageable exposure
            - available capacity
            - acceptable free margin
            """

            reasons.append(
                "Portfolio capacity available"
            )

            decision = "HOLD"

            confidence = 0.6


        # ====================================================
        # SIMPLIFIED CORRELATION MODEL
        # ====================================================

        """
        Production systems should calculate:
        - rolling correlations
        - beta exposure
        - sector concentration
        - factor exposure

        Current implementation uses:
        simple position count limit.
        """

        metadata["correlation_model"] = (
            "simplified_position_limit"
        )


        # ====================================================
        # PORTFOLIO STRESS CHECK
        # ====================================================

        """
        Large unrealized losses indicate:
        - unstable portfolio
        - bad market regime
        - excessive leverage
        """

        if portfolio.unrealized_pnl < (
            -0.05 * portfolio.total_equity
        ):

            """
            Portfolio losing more than 5%.

            Reduce aggressiveness.
            """

            reasons.append(
                "Portfolio under stress "
                "- elevated unrealized losses"
            )

            confidence = max(
                confidence,
                0.75
            )


        # ====================================================
        # CREATE FINAL AGENT VOTE
        # ====================================================

        """
        Standardized output format
        used by all agents.
        """

        vote = AgentVote(

            agent="PortfolioAgent",

            decision=decision,

            confidence=round(confidence, 3),

            reasoning=" | ".join(reasons),

            metadata=metadata
        )


        # ====================================================
        # LOG RESULT
        # ====================================================

        logger.info(
            f"📊 PortfolioAgent | "
            f"{symbol} | "
            f"{decision} | "
            f"Confidence={confidence:.2f}"
        )


        # ====================================================
        # RETURN LANGGRAPH STATE UPDATE
        # ====================================================

        """
        LangGraph merges returned values
        into global workflow state.
        """

        return {

            # Main portfolio output
            "portfolio_vote": vote,

            # Workflow observability

            "completed_nodes": [
                
                "portfolio_agent"
            ],

            "logs": [
                
                (
                    f"PortfolioAgent generated "
                    f"{decision} for {symbol}"
                )
            ]
        }


    # ========================================================
    # FAILSAFE HANDLING
    # ========================================================

    except Exception as e:

        """
        Portfolio failures should never
        crash trading workflow.

        Safest fallback:
        HOLD
        """

        logger.exception(
            f"PortfolioAgent failure: {str(e)}"
        )

        error_vote = AgentVote(

            agent="PortfolioAgent",

            decision="HOLD",

            confidence=0.1,

            reasoning=(
                f"Portfolio agent error: {str(e)}"
            )
        )

        return {

            "portfolio_vote": error_vote,

            "execution_error": str(e),


            "logs": [
                
                f"PortfolioAgent failed: {str(e)}"
            ]
        }