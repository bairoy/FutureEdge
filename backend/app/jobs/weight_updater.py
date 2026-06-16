"""
app/jobs/weight_updater.py
===========================
Adaptive agent weight system — recalculates agent influence
weights based on each agent's actual prediction accuracy.

THE PROBLEM WITH FIXED WEIGHTS:
---------------------------------
In Phase 1, agent weights are hardcoded:
  SignalAgent:    0.30
  SentimentAgent: 0.20
  RiskAgent:      0.25
  PortfolioAgent: 0.25

These never change, even if SignalAgent is consistently wrong
and SentimentAgent is consistently right.

THE PHASE 2 SOLUTION:
----------------------
After every N completed trades, we:
  1. Look at each agent's vote on past closed trades
  2. Check if the trade was a WIN or LOSS
  3. Calculate each agent's accuracy:
       "What % of the time did this agent vote in the direction
        of the final trade outcome?"
  4. Update weights proportional to accuracy

EXAMPLE:
---------
Last 20 closed trades:
  SignalAgent    voted correct direction on 14/20 = 70% accuracy
  SentimentAgent voted correct direction on 16/20 = 80% accuracy
  RiskAgent      voted HOLD on 18/20, so accuracy not counted
  PortfolioAgent voted HOLD on 17/20, accuracy not counted

New weights (proportional to accuracy):
  SignalAgent:    0.70 / (0.70 + 0.80) = 0.467 × 0.50 = 0.233
  SentimentAgent: 0.80 / (0.70 + 0.80) = 0.533 × 0.50 = 0.267
  RiskAgent:      stays at its baseline 0.25 (not enough active votes)
  PortfolioAgent: stays at its baseline 0.25

STORAGE:
---------
Updated weights are stored in Redis with no expiry.
The orchestrator reads from Redis at the start of each cycle.
If Redis has no weights, it falls back to the hardcoded defaults.

SCHEDULE:
----------
This job runs automatically after every
settings.WEIGHT_UPDATE_INTERVAL_TRADES closed trades.
It is called from trade_repo.close_trade() after each close.

USAGE:
------
    from app.jobs.weight_updater import maybe_update_weights

    # Call after closing a trade
    await maybe_update_weights(user_id="uuid-here", symbol="NIFTY 50")
"""

import json
from loguru import logger

from app.core.config import settings
from app.db.redis import redis_client


# ============================================================
# REDIS KEY FOR WEIGHTS
# ============================================================

WEIGHTS_KEY = "futureedge:agent_weights"

# Default weights by regime (Phase 2 regime-specific weights)
# Issue 5: SentimentAgent weight is reduced to 0.05 (from 0.15/0.20) as it is noise, not signal.
DEFAULT_WEIGHTS_BY_REGIME = {
    "TRENDING_UP": {
        "SignalAgent":    0.40,
        "SentimentAgent": 0.05,
        "RiskAgent":      0.15,
        "PortfolioAgent": 0.20,
        "MacroAgent":     0.20,
    },
    "TRENDING_DOWN": {
        "SignalAgent":    0.35,
        "SentimentAgent": 0.05,
        "RiskAgent":      0.20,
        "PortfolioAgent": 0.20,
        "MacroAgent":     0.20,
    },
    "RANGEBOUND": {
        "SignalAgent":    0.30,
        "SentimentAgent": 0.05,
        "RiskAgent":      0.20,
        "PortfolioAgent": 0.25,
        "MacroAgent":     0.20,
    },
    "HIGH_VOLATILITY": {
        "SignalAgent":    0.20,
        "SentimentAgent": 0.05,
        "RiskAgent":      0.35,
        "PortfolioAgent": 0.20,
        "MacroAgent":     0.20,
    },
    "UNKNOWN": {
        "SignalAgent":    0.35,
        "SentimentAgent": 0.05,
        "RiskAgent":      0.20,
        "PortfolioAgent": 0.20,
        "MacroAgent":     0.20,
    }
}

DEFAULT_WEIGHTS = DEFAULT_WEIGHTS_BY_REGIME["UNKNOWN"]

# Minimum weight any agent can have (prevents zeroing out an agent)
MIN_WEIGHT = 0.05

# Maximum weight any agent can have (prevents one agent dominating)
MAX_WEIGHT = 0.60


# ============================================================
# GET CURRENT WEIGHTS  (called by orchestrator each cycle)
# ============================================================

async def get_agent_weights(regime: str = "UNKNOWN") -> dict:
    """
    Return current agent weights from Redis for the given regime.

    The orchestrator calls this at the start of each cycle
    to get the most up-to-date weights.

    If no weights are stored yet (first run, or Redis cleared),
    returns the DEFAULT_WEIGHTS_BY_REGIME for that regime.

    Returns:
    --------
    {
        "SignalAgent":    0.32,
        "SentimentAgent": 0.05,
        "RiskAgent":      0.20,
        "PortfolioAgent": 0.23,
        "MacroAgent":     0.20,
    }
    """
    regime = (regime or "UNKNOWN").upper()
    if regime not in DEFAULT_WEIGHTS_BY_REGIME:
        regime = "UNKNOWN"

    try:
        # Try regime-specific weights first
        raw = await redis_client.get(f"{WEIGHTS_KEY}:{regime}")
        if raw:
            weights = json.loads(raw)
            logger.debug(f"Agent weights for regime {regime} from Redis: {weights}")
            return weights

        # Fallback to global/unknown weights
        raw_global = await redis_client.get(WEIGHTS_KEY)
        if raw_global:
            weights = json.loads(raw_global)
            logger.debug(f"Global agent weights from Redis (fallback for {regime}): {weights}")
            return weights

    except Exception as e:
        logger.warning(f"Could not read agent weights from Redis: {e}")

    logger.debug(f"Using default agent weights for regime {regime}")
    return DEFAULT_WEIGHTS_BY_REGIME[regime].copy()



# ============================================================
# HELPER: GET REGIME FROM TRADE METADATA
# ============================================================

def _get_trade_regime(trade) -> str:
    """Extract regime from the agent_consensus metadata list."""
    agent_consensus = trade.agent_consensus or []
    for vote_data in agent_consensus:
        if isinstance(vote_data, dict) and "regime" in vote_data:
            return vote_data["regime"]
    return "UNKNOWN"


# ============================================================
# MAYBE UPDATE WEIGHTS  (called after each trade closes)
# ============================================================

async def maybe_update_weights(user_id: str, symbol: str) -> None:
    """
    Check if it's time to recalculate weights and do so if needed.

    We update weights every WEIGHT_UPDATE_INTERVAL_TRADES closed trades.

    This is called from trade_repo.close_trade() — so it runs
    automatically whenever a trade is closed, no manual trigger needed.

    Parameters:
    -----------
    user_id : only considers trades by this user
    symbol  : only considers trades on this symbol
    """

    from app.db.postgres import AsyncSessionLocal
    from app.db.repos.trade_repo import TradeRepo

    try:
        async with AsyncSessionLocal() as session:
            trades = await TradeRepo.get_recent_closed_trades(
                session = session,
                symbol  = symbol,
                user_id = user_id,
                limit   = 100,
            )

        total = len(trades)

        # Only update if we have enough trades
        if total < settings.MIN_TRADES_FOR_WEIGHT_UPDATE:
            logger.debug(
                f"Weight update skipped | {total} trades < "
                f"minimum {settings.MIN_TRADES_FOR_WEIGHT_UPDATE}"
            )
            return

        # Only update every N trades (not every single close)
        if total % settings.WEIGHT_UPDATE_INTERVAL_TRADES != 0:
            return

        # Group trades by regime
        trades_by_regime = {}
        for trade in trades:
            reg = _get_trade_regime(trade).upper()
            if reg not in DEFAULT_WEIGHTS_BY_REGIME:
                reg = "UNKNOWN"
            trades_by_regime.setdefault(reg, []).append(trade)

        # Update weights for each regime that has active trades
        for reg, reg_trades in trades_by_regime.items():
            # Only update weights if we have enough trades in this specific regime
            if len(reg_trades) < 5:
                continue

            new_weights = _calculate_new_weights(reg_trades, regime=reg)

            # Store in Redis: f"{WEIGHTS_KEY}:{reg}"
            await redis_client.set(
                f"{WEIGHTS_KEY}:{reg}",
                json.dumps(new_weights),
            )

            # Keep global weights updated if calibrating UNKNOWN regime
            if reg == "UNKNOWN":
                await redis_client.set(
                    WEIGHTS_KEY,
                    json.dumps(new_weights),
                )

            logger.info(
                f"Agent weights updated for regime {reg} | user={user_id} | "
                f"symbol={symbol} | based_on={len(reg_trades)} trades | "
                f"weights={new_weights}"
            )

    except Exception as e:
        logger.warning(f"Weight update failed (non-fatal): {e}")


# ============================================================
# WEIGHT CALCULATION LOGIC
# ============================================================

def _calculate_new_weights(trades: list, regime: str = "UNKNOWN") -> dict:
    """
    Calculate new weights based on each agent's historical accuracy in a given regime.

    For each agent:
      1. Filter trades where agent made an active vote (BUY or SELL)
      2. Count how many times the vote matched the trade outcome:
           LONG trade + WIN + agent voted BUY = correct ✓
           SHORT trade + WIN + agent voted SELL = correct ✓
           LONG trade + LOSS + agent voted BUY = incorrect ✗
      3. Accuracy = correct / total active votes

    Only agents with enough active votes get their weights updated.
    Agents that mostly HOLD don't have enough signal to evaluate.

    Parameters:
    -----------
    trades : list of closed Trade ORM objects
    regime : the market regime we are calculating weights for

    Returns:
    --------
    Updated weights dict
    """
    regime = regime.upper()
    if regime not in DEFAULT_WEIGHTS_BY_REGIME:
        regime = "UNKNOWN"

    baseline_weights = DEFAULT_WEIGHTS_BY_REGIME[regime]

    agent_stats = {
        "SignalAgent":    {"correct": 0, "total": 0},
        "SentimentAgent": {"correct": 0, "total": 0},
        "RiskAgent":      {"correct": 0, "total": 0},
        "PortfolioAgent": {"correct": 0, "total": 0},
        "MacroAgent":     {"correct": 0, "total": 0},
    }

    for trade in trades:
        # Determine if this trade was a WIN or LOSS
        if trade.realized_pnl is None:
            continue

        trade_won     = trade.realized_pnl > 0
        trade_long    = trade.direction == "LONG"
        trade_short   = trade.direction == "SHORT"

        agent_consensus = trade.agent_consensus or []

        for vote_data in agent_consensus:
            agent    = vote_data.get("agent", "")
            decision = vote_data.get("decision", "")

            if agent not in agent_stats:
                continue

            # Only count BUY/SELL votes (not HOLD)
            if decision not in ("BUY", "SELL"):
                continue

            agent_stats[agent]["total"] += 1

            # Was the agent's vote aligned with the outcome?
            agent_correct = (
                (decision == "BUY"  and trade_long  and trade_won) or
                (decision == "SELL" and trade_short and trade_won) or
                (decision == "BUY"  and trade_short and not trade_won) or
                (decision == "SELL" and trade_long  and not trade_won)
            )

            if agent_correct:
                agent_stats[agent]["correct"] += 1

    # --------------------------------------------------------
    # COMPUTE ACCURACY AND NEW WEIGHTS
    # --------------------------------------------------------

    accuracies    = {}
    min_votes_req = 5   # agent must have at least 5 active votes

    for agent, stats in agent_stats.items():
        if stats["total"] >= min_votes_req:
            accuracies[agent] = stats["correct"] / stats["total"]
        else:
            # Not enough active votes — keep default weight
            accuracies[agent] = None

    # Agents with enough votes get weights proportional to accuracy
    # Agents without enough votes keep their default weight

    active_agents    = {a: acc for a, acc in accuracies.items() if acc is not None}
    inactive_agents  = {a for a, acc in accuracies.items() if acc is None}

    new_weights = {}

    if active_agents:
        total_accuracy = sum(active_agents.values())

        if total_accuracy > 0:
            # Allocate 50% of total weight to active agents, proportionally
            # Remaining 50% is split equally among inactive agents
            active_weight_pool   = 0.50
            inactive_weight_pool = 0.50

            active_share = {
                agent: (acc / total_accuracy) * active_weight_pool
                for agent, acc in active_agents.items()
            }

            inactive_share = inactive_weight_pool / max(len(inactive_agents), 1)

            for agent in baseline_weights:
                if agent in active_share:
                    weight = active_share[agent]
                else:
                    weight = inactive_share

                # Clip to min/max bounds
                new_weights[agent] = round(
                    max(MIN_WEIGHT, min(MAX_WEIGHT, weight)),
                    4
                )
        else:
            new_weights = baseline_weights.copy()
    else:
        new_weights = baseline_weights.copy()

    # Renormalise so weights sum to 1.0
    total = sum(new_weights.values())
    if total > 0:
        new_weights = {
            agent: round(w / total, 4)
            for agent, w in new_weights.items()
        }

    return new_weights