"""
app/agents/macro_agent.py
==========================
Macroeconomic agent node. Checks VIX and USD/INR and votes.
"""

from loguru import logger
from app.graph.state import AgentState, AgentVote
from app.agents.tools.market_tools import get_india_vix, get_usd_inr

async def macro_agent_node(state: AgentState) -> dict:
    """
    Macro Agent Node.
    Fetches VIX and USD/INR daily percent change and votes BUY, SELL, HOLD, or VETO.
    """
    try:
        ctx = state["market_context"]
        symbol = ctx.symbol
        
        reasons = []
        decision = "HOLD"
        confidence = 0.5
        metadata = {}
        
        # 1. Fetch India VIX (Market Volatility/Fear)
        vix = await get_india_vix()
        metadata["india_vix"] = vix
        
        # 2. Fetch USD/INR change
        usd_inr_data = await get_usd_inr()
        usd_inr_rate = usd_inr_data["rate"]
        usd_inr_change = usd_inr_data["change_pct"]
        metadata["usd_inr_rate"] = usd_inr_rate
        metadata["usd_inr_change_pct"] = usd_inr_change
        
        # 3. Decision Logic
        # VETO if VIX > 25 (extreme market fear/panic)
        if vix > 25.0:
            decision = "VETO"
            confidence = 0.95
            reasons.append(f"India VIX {vix:.2f} exceeds extreme fear threshold (25.0) — trading halted")
        # VETO if INR weakening > 1.2% in a day (global risk-off / currency depreciation)
        elif usd_inr_change > 1.2:
            decision = "VETO"
            confidence = 0.90
            reasons.append(f"USD/INR surged by {usd_inr_change:+.2f}% — high currency risk and foreign outflow")
        else:
            # Macro environment dictates biases
            # Higher VIX dampens buy signals and pushes bias to SELL/HOLD
            if vix > 20.0:
                decision = "SELL"
                confidence = 0.60
                reasons.append(f"Elevated India VIX ({vix:.2f}) indicates market tension — sell bias")
            elif vix < 13.0:
                decision = "BUY"
                confidence = 0.65
                reasons.append(f"Low India VIX ({vix:.2f}) indicates market confidence — buy bias")
            
            # USD/INR direction
            if usd_inr_change > 0.5:
                # USD strengthening, bad for emerging market stocks
                reasons.append(f"Weakening Rupee ({usd_inr_change:+.2f}%) poses headwind for domestic equities")
                if decision == "BUY":
                    decision = "HOLD"
                    confidence = 0.55
                else:
                    decision = "SELL"
                    confidence = max(confidence, 0.60)
            elif usd_inr_change < -0.5:
                # INR strengthening, good for Indian equities
                reasons.append(f"Stronger Rupee ({usd_inr_change:+.2f}%) supports foreign inflows")
                if decision == "SELL":
                    decision = "HOLD"
                    confidence = 0.55
                else:
                    decision = "BUY"
                    confidence = max(confidence, 0.65)
                    
        if not reasons:
            reasons.append(f"Stable macro factors: VIX={vix:.1f}, USD/INR={usd_inr_rate:.2f}")

        vote = AgentVote(
            agent="MacroAgent",
            decision=decision,
            confidence=round(confidence, 3),
            reasoning=" | ".join(reasons),
            metadata=metadata,
        )
        
        logger.info(
            f"🌐 MacroAgent | {symbol} | {decision} | "
            f"VIX={vix:.1f} | USDINR={usd_inr_rate:.2f} ({usd_inr_change:+.2f}%)"
        )
        
        return {
            "macro_vote": vote,
            "completed_nodes": ["macro_agent"],
            "logs": [f"MacroAgent generated {decision} for {symbol}"],
        }
    except Exception as e:
        logger.error(f"MacroAgent failure: {e}")
        return {
            "macro_vote": AgentVote(
                agent="MacroAgent",
                decision="HOLD",
                confidence=0.1,
                reasoning=f"MacroAgent error: {e}",
            ),
            "completed_nodes": ["macro_agent"],
            "logs": [f"MacroAgent failed: {e}"],
        }
