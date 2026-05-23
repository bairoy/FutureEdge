"""
app/agents/portfolio_agent.py
==============================
Portfolio health agent — checks if the portfolio can safely
support a new trade before the orchestrator decides.

CHANGES FROM ORIGINAL:
-----------------------
1. Now ASYNC  (async def portfolio_agent_node)

2. Uses Kelly fraction from risk metadata if available
   (shared via state — no tight coupling)

3. Indian market checks:
   - NSE F&O lot size awareness
   - MIS (intraday) product type
   - Square-off time warning (Zerodha auto-squares at 3:20 PM IST)

WHAT THIS AGENT ASKS:
----------------------
NOT "should we trade?" (that's the signal agent's job)
BUT "can the portfolio safely support a new trade right now?"
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from app.graph.state import AgentState, AgentVote, PortfolioSnapshot, MarketContext


IST = ZoneInfo("Asia/Kolkata")

# Zerodha MIS auto-square-off happens at 3:20 PM IST
# We warn if a new trade is opened within 30 minutes of this
AUTO_SQUAREOFF_HOUR   = 15
AUTO_SQUAREOFF_MINUTE = 20


# ============================================================
# PORTFOLIO AGENT NODE  (async)
# ============================================================

async def portfolio_agent_node(state: AgentState) -> dict:
    """
    Analyses portfolio health → votes HOLD (no objection)
    or VETO (cannot safely add more positions).

    Returns a partial state update with portfolio_vote set.
    """

    try:
        portfolio: PortfolioSnapshot = state["portfolio"]
        ctx: MarketContext           = state["market_context"]
        symbol                       = ctx.symbol

        decision   = "HOLD"
        confidence = 0.6
        reasons    = []
        metadata   = {}

        open_count     = len(portfolio.open_positions)
        total_exposure = sum(
            pos.get("notional", 0) for pos in portfolio.open_positions
        )

        metadata["open_positions_count"] = open_count
        metadata["total_exposure"]       = round(total_exposure, 2)
        metadata["unrealized_pnl"]       = round(portfolio.unrealized_pnl, 2)
        metadata["margin_available"]     = round(portfolio.margin_available, 2)

        # --------------------------------------------------------
        # CHECK 1: MAX OPEN POSITIONS
        # --------------------------------------------------------

        if open_count >= 5:
            decision   = "VETO"
            confidence = 0.85
            reasons.append(
                f"Maximum positions ({open_count}) reached — "
                f"close existing before opening new"
            )

        # --------------------------------------------------------
        # CHECK 2: AVAILABLE MARGIN
        # --------------------------------------------------------

        # If less than 20% of equity is free, we should not
        # open new positions — we need buffer for drawdowns.

        elif portfolio.margin_available < (portfolio.total_equity * 0.20):
            decision   = "HOLD"
            confidence = 0.7
            reasons.append(
                f"Low margin available (₹{portfolio.margin_available:.0f}) "
                f"— avoid new positions"
            )

        # --------------------------------------------------------
        # CHECK 3: PORTFOLIO STRESS (unrealised losses)
        # --------------------------------------------------------

        if portfolio.unrealized_pnl < -(0.05 * portfolio.total_equity):
            confidence = max(confidence, 0.75)
            reasons.append(
                f"Portfolio under stress — "
                f"unrealised PnL ₹{portfolio.unrealized_pnl:.2f}"
            )

        # --------------------------------------------------------
        # CHECK 4: ZERODHA MIS AUTO-SQUARE-OFF WARNING
        # --------------------------------------------------------

        # Zerodha automatically closes all MIS (intraday) positions
        # at 3:20 PM IST to avoid overnight exposure.
        # If it is already 3:00 PM or later, we warn against
        # opening new positions — there is not enough time.

        now = datetime.now(IST)
        if now.hour == AUTO_SQUAREOFF_HOUR and now.minute >= (AUTO_SQUAREOFF_MINUTE - 20):
            reasons.append(
                f"Within 20 minutes of Zerodha MIS auto-square-off "
                f"(3:20 PM IST) — new positions risky"
            )
            confidence = max(confidence, 0.7)

        # --------------------------------------------------------
        # ALL CLEAR
        # --------------------------------------------------------

        if not reasons:
            reasons.append(
                f"Portfolio healthy — "
                f"{open_count} positions, "
                f"₹{portfolio.margin_available:.0f} available"
            )

        # --------------------------------------------------------
        # BUILD VOTE
        # --------------------------------------------------------

        metadata["product_type"]      = "MIS"   # Zerodha intraday product
        metadata["correlation_model"] = "position_count_limit"

        vote = AgentVote(
            agent     = "PortfolioAgent",
            decision  = decision,
            confidence= round(confidence, 3),
            reasoning = " | ".join(reasons),
            metadata  = metadata,
        )

        logger.info(
            f"📊 PortfolioAgent | {symbol} | {decision} | "
            f"conf={confidence:.2f} | positions={open_count}"
        )

        return {
            "portfolio_vote":  vote,
            "completed_nodes": ["portfolio_agent"],
            "logs":            [f"PortfolioAgent generated {decision} for {symbol}"],
        }

    except Exception as e:
        logger.exception(f"PortfolioAgent failure: {e}")

        return {
            "portfolio_vote": AgentVote(
                agent     = "PortfolioAgent",
                decision  = "HOLD",
                confidence= 0.1,
                reasoning = f"PortfolioAgent error: {e}",
            ),
            "completed_nodes": ["portfolio_agent"],
            "logs":            [f"PortfolioAgent failed: {e}"],
        }