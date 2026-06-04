"""
app/agents/orchestration_agent.py
===================================
Orchestrator — collects all 4 agent votes, applies weighted
consensus, decides the final trade, and sets HITL flag.

CHANGES FROM ORIGINAL:
-----------------------
1. Now ASYNC  (async def orchestrator_node)

2. Publishes agent results to Redis pub/sub after every cycle.
   The frontend WebSocket listens to this channel and updates
   the live dashboard in real time — no polling needed.

3. Uses Kelly fraction from risk_vote metadata for position sizing.
   Instead of hardcoded 2%, the actual Kelly result from
   risk_agent is used to size the position correctly.

4. Indian market: position size in Rupees, not USD.

HOW POSITION SIZING WORKS NOW:
-------------------------------
risk_agent computes Kelly fraction from trade history.
Example: Kelly = 0.04 (4% of equity)
equity = ₹100,000
position_size = ₹100,000 × 0.04 = ₹4,000

The execution agent converts this to number of shares:
shares = ₹4,000 / current_price
"""

import json

from loguru import logger

from app.graph.state import (
    AgentState, AgentVote, TradeProposal,
    PortfolioSnapshot, MarketContext,
)
from app.db.redis import redis_client, CHANNEL_AGENT_RESULTS, CHANNEL_HITL_PENDING


# Default weights — used only if Redis has no stored weights.
# The orchestrator now reads from Redis via get_agent_weights()
# so these are just the initial fallback.
DEFAULT_AGENT_WEIGHTS = {
    "SignalAgent":    0.30,
    "SentimentAgent": 0.20,
    "RiskAgent":      0.25,
    "PortfolioAgent": 0.25,
}


# ============================================================
# DISAGREEMENT METRIC
# ============================================================

def _calculate_disagreement(votes: list[AgentVote]) -> float:
    """
    Measure how much agents disagree with each other.

    Returns:
      0.0 → full agreement
      1.0 → extreme disagreement (half say BUY, half say SELL)

    Higher disagreement → higher uncertainty → more likely HITL.

    We only count active votes (BUY or SELL), not HOLDs.
    A HOLD means "no opinion", not disagreement.
    """

    active = [v.decision for v in votes if v.decision in ("BUY", "SELL")]

    if len(active) <= 1:
        return 0.0

    buys  = active.count("BUY")
    sells = active.count("SELL")
    total = buys + sells

    # Minority fraction = how split the active votes are
    return round(min(buys, sells) / total, 3)


# ============================================================
# ORCHESTRATOR NODE  (async)
# ============================================================

async def orchestrator_node(state: AgentState) -> dict:
    """
    Combines all agent votes into one final trade decision.

    Flow:
    -----
    Collect votes → check VETO → weighted score → decide →
    size position → evaluate HITL → publish to Redis → return
    """

    try:
        ctx: MarketContext           = state["market_context"]
        portfolio: PortfolioSnapshot = state["portfolio"]
        symbol  = ctx.symbol
        price   = ctx.current_price
        run_id  = state["run_id"]

        # --------------------------------------------------------
        # LOAD ADAPTIVE WEIGHTS FROM REDIS
        # --------------------------------------------------------
        # Phase 2: weights are updated by weight_updater.py based
        # on each agent's historical prediction accuracy.
        # Falls back to DEFAULT_AGENT_WEIGHTS if Redis is empty.

        from app.jobs.weight_updater import get_agent_weights
        agent_weights = await get_agent_weights()

        # --------------------------------------------------------
        # COLLECT VOTES
        # --------------------------------------------------------

        votes = [
            v for v in [
                state.get("signal_vote"),
                state.get("sentiment_vote"),
                state.get("risk_vote"),
                state.get("portfolio_vote"),
            ]
            if v is not None
        ]

        if not votes:
            logger.warning("Orchestrator: no agent votes received")

            proposal = TradeProposal(
                symbol=symbol, direction="NONE",
                size=0, entry_price=price, risk_score=1.0,
            )

            return {
                "consensus":   proposal,
                "hitl_required": False,
                "hitl_status": "NOT_REQUIRED",
                "completed_nodes": ["orchestrator"],
                "logs": ["Orchestrator: no votes — NONE"],
            }

        # --------------------------------------------------------
        # VETO CHECK
        # --------------------------------------------------------

        # Any VETO from any agent = no trade, no questions asked.
        # The risk agent has absolute authority to block trades.

        vetoes = [v for v in votes if v.decision == "VETO"]

        if vetoes:
            veto = vetoes[0]
            logger.warning(f"🚫 VETO by {veto.agent}: {veto.reasoning}")

            proposal = TradeProposal(
                symbol         = symbol,
                direction      = "NONE",
                size           = 0,
                entry_price    = price,
                risk_score     = 1.0,
                agent_consensus= votes,
                human_approved = False,
            )

            await _publish_results(symbol, proposal, votes, run_id)

            return {
                "consensus":       proposal,
                "hitl_required":   False,
                "hitl_status":     "NOT_REQUIRED",
                "completed_nodes": ["orchestrator"],
                "logs":            [f"Trade VETOED by {veto.agent}"],
            }

        # --------------------------------------------------------
        # WEIGHTED CONSENSUS SCORING
        # --------------------------------------------------------

        buy_score  = 0.0
        sell_score = 0.0
        active_wt  = 0.0
        hold_wt    = 0.0

        for vote in votes:
            wt = agent_weights.get(vote.agent, 0.2)
            if vote.decision in ("BUY", "SELL"):
                active_wt += wt
                if vote.decision == "BUY":
                    buy_score  += wt * vote.confidence
                elif vote.decision == "SELL":
                    sell_score += wt * vote.confidence
            else:
                hold_wt += wt

        total_wt = active_wt + 0.25 * hold_wt

        if total_wt > 0:
            buy_score  /= total_wt
            sell_score /= total_wt

        disagreement = _calculate_disagreement(votes)

        # --------------------------------------------------------
        # FINAL DECISION
        # --------------------------------------------------------

        # Threshold of 0.55: a signal must be reasonably strong
        # before we act. Weak signals produce too many bad trades.

        if buy_score > 0.55 and buy_score > sell_score:
            direction  = "LONG"
            confidence = buy_score
            decision   = "BUY"

        elif sell_score > 0.55 and sell_score > buy_score:
            direction  = "SHORT"
            confidence = sell_score
            decision   = "SELL"

        else:
            direction  = "NONE"
            confidence = max(buy_score, sell_score)
            decision   = "HOLD"

        # --------------------------------------------------------
        # RISK SCORE
        # --------------------------------------------------------

        # Low confidence → high risk
        # High disagreement → higher risk
        risk_score = min(1.0, max(0.0,
            (1.0 - confidence) + (disagreement * 0.3)
        ))

        # --------------------------------------------------------
        # POSITION SIZING  (using Kelly from risk agent)
        # --------------------------------------------------------

        # Extract Kelly fraction computed by risk_agent
        risk_vote = state.get("risk_vote")
        kelly_fraction = 0.02   # safe default

        if risk_vote and risk_vote.metadata:
            kelly_fraction = risk_vote.metadata.get("kelly_fraction", 0.02)

        if decision != "HOLD":
            # Position size in Rupees
            position_rupees = portfolio.total_equity * kelly_fraction
        else:
            position_rupees = 0.0

        # --------------------------------------------------------
        # HITL EVALUATION
        # --------------------------------------------------------

        hitl_required = False
        hitl_reasons  = []

        if direction != "NONE":
            hitl_required = True
            hitl_reasons.append(f"Human-in-the-Loop verification required for {direction} proposal.")

        # --------------------------------------------------------
        # EPISODIC MEMORY: RETRIEVE SIMILAR PAST TRADES (Phase 2 — new)
        # --------------------------------------------------------
        episodic_memories = []
        market_vector = None
        try:
            from app.data.indicator_cache import get_indicators
            from app.memory.embedder import build_market_vector
            from app.memory.qdrant_store import retrieve_similar_memories

            ind = await get_indicators(symbol, ctx.ohlcv_1m)
            rsi              = ind.get("rsi", 50.0)
            macd_hist        = ind.get("macd_hist", 0.0)
            bollinger_upper  = ind.get("bollinger_upper", price)
            bollinger_lower  = ind.get("bollinger_lower", price)

            market_vector = build_market_vector(
                rsi=rsi,
                macd_hist=macd_hist,
                bollinger_upper=bollinger_upper,
                bollinger_lower=bollinger_lower,
                current_price=price,
                volatility_24h=ctx.volatility_24h,
                sentiment_score=ctx.sentiment_score,
                regime=ctx.regime if hasattr(ctx, "regime") else "UNKNOWN",
                buy_score=buy_score,
                sell_score=sell_score,
                risk_score=risk_score,
            )

            episodic_memories = await retrieve_similar_memories(
                vector=market_vector,
                symbol=symbol,
                limit=5,
            )
            logger.info(f"Retrieved {len(episodic_memories)} similar past trades from Qdrant")
        except Exception as mem_err:
            logger.warning(f"Failed to query past memories: {mem_err}")

        # --------------------------------------------------------
        # GENERATE LLM RATIONALE  (only if not holding)
        # --------------------------------------------------------
        llm_rationale = None
        from app.core.config import settings
        if decision != "HOLD" and settings.LLM_REASONING_ENABLED:
            try:
                from app.models.llm_reasoner import generate_trade_rationale
                memories = episodic_memories
                regime = ctx.regime if hasattr(ctx, "regime") else "UNKNOWN"
                
                llm_rationale = await generate_trade_rationale(
                    symbol=symbol,
                    direction=direction,
                    votes=votes,
                    buy_score=buy_score,
                    sell_score=sell_score,
                    risk_score=risk_score,
                    disagreement=disagreement,
                    episodic_memories=memories,
                    regime=regime,
                )
            except Exception as le:
                logger.warning(f"Failed to generate LLM rationale: {le}")
                from app.models.llm_reasoner import _template_rationale
                llm_rationale = _template_rationale(direction, votes, risk_score, disagreement)

        # --------------------------------------------------------
        # TRADE PROPOSAL
        # --------------------------------------------------------

        proposal = TradeProposal(
            symbol         = symbol,
            direction      = direction,
            size           = round(position_rupees, 2),
            entry_price    = price,
            risk_score     = round(risk_score, 3),
            agent_consensus= votes,
            human_approved = None,
            llm_rationale  = llm_rationale,
        )

        # --------------------------------------------------------
        # SET STOP-LOSS & TAKE-PROFIT  (ATR-based)
        # --------------------------------------------------------
        # Without SL/TP, the exit monitor has nothing to enforce
        # and trades stay OPEN forever.
        #
        # Using volatility as ATR proxy:
        #   SL = 1.5 × ATR (tight enough to limit loss)
        #   TP = 3.0 × ATR (2:1 reward-to-risk ratio)

        if direction != "NONE" and price > 0:
            atr_proxy = max(ctx.volatility_24h * price, price * 0.005)  # floor at 0.5%

            if direction == "LONG":
                proposal.stop_loss   = round(price - (1.5 * atr_proxy), 2)
                proposal.take_profit = round(price + (3.0 * atr_proxy), 2)
            elif direction == "SHORT":
                proposal.stop_loss   = round(price + (1.5 * atr_proxy), 2)
                proposal.take_profit = round(price - (3.0 * atr_proxy), 2)

        # --------------------------------------------------------
        # PUBLISH TO REDIS  (frontend WebSocket picks this up)
        # --------------------------------------------------------

        await _publish_results(symbol, proposal, votes, run_id, hitl_required, hitl_reasons)

        # --------------------------------------------------------
        # LOG
        # --------------------------------------------------------

        logger.info(
            f"🎯 Orchestrator | {decision} {symbol} | "
            f"conf={confidence:.2f} | risk={risk_score:.2f} | "
            f"kelly={kelly_fraction:.3f} | "
            f"size=₹{position_rupees:.0f} | HITL={hitl_required}"
        )

        return {
            "consensus":       proposal,
            "llm_rationale":   llm_rationale,
            "hitl_required":   hitl_required,
            "hitl_status":     "PENDING" if hitl_required else "NOT_REQUIRED",
            "episodic_memory": episodic_memories,
            "market_vector":   market_vector,
            "completed_nodes": ["orchestrator"],
            "logs":            [f"Orchestrator generated {decision} for {symbol}"],
        }

    except Exception as e:
        logger.exception(f"Orchestrator failure: {e}")

        fallback = TradeProposal(
            symbol="UNKNOWN", direction="NONE",
            size=0, entry_price=0, risk_score=1.0,
        )

        return {
            "consensus":       fallback,
            "hitl_required":   True,
            "hitl_status":     "PENDING",
            "execution_error": str(e),
            "episodic_memory": [],
            "market_vector":   None,
            "completed_nodes": ["orchestrator"],
            "logs":            [f"Orchestrator failed: {e}"],
        }


# ============================================================
# REDIS PUBLISH HELPER
# ============================================================

async def _publish_results(
    symbol:        str,
    proposal:      TradeProposal,
    votes:         list[AgentVote],
    run_id:        str,
    hitl_required: bool = False,
    hitl_reasons:  list[str] | None = None,
) -> None:
    """
    Publish orchestrator results to Redis pub/sub.

    The frontend WebSocket server subscribes to this channel
    and pushes the data to all connected browser clients.
    This gives the live "agent vote panel" its real-time updates.

    If hitl_required=True, also publish to CHANNEL_HITL_PENDING
    so the frontend can show the HITL approval modal immediately.
    """

    try:
        payload = {
            "run_id":       run_id,
            "symbol":       symbol,
            "direction":    proposal.direction,
            "size":         proposal.size,
            "entry_price":  proposal.entry_price,
            "risk_score":   proposal.risk_score,
            "llm_rationale": proposal.llm_rationale,
            "hitl_required": hitl_required,
            "hitl_reasons": hitl_reasons or [],
            "votes": [
                {
                    "agent":      v.agent,
                    "decision":   v.decision,
                    "confidence": v.confidence,
                    "reasoning":  v.reasoning,
                    "metadata":   v.metadata,
                }
                for v in votes
            ],
        }

        await redis_client.publish(
            CHANNEL_AGENT_RESULTS,
            json.dumps(payload),
        )

        if hitl_required:
            await redis_client.publish(
                CHANNEL_HITL_PENDING,
                json.dumps({
                    "symbol":   symbol,
                    "reasons":  hitl_reasons or [],
                    "proposal": payload,
                }),
            )

    except Exception as e:
        # Publish failure is non-fatal — log and continue
        logger.warning(f"Redis publish failed: {e}")