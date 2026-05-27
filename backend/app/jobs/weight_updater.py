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

# Default weights (Phase 1 hardcoded values)
DEFAULT_WEIGHTS = {
    "SignalAgent":    0.30,
    "SentimentAgent": 0.20,
    "RiskAgent":      0.25,
    "PortfolioAgent": 0.25,
}

# Minimum weight any agent can have (prevents zeroing out an agent)
MIN_WEIGHT = 0.10

# Maximum weight any agent can have (prevents one agent dominating)
MAX_WEIGHT = 0.50


# ============================================================
# GET CURRENT WEIGHTS  (called by orchestrator each cycle)
# ============================================================

async def get_agent_weights() -> dict:
    """
    Return current agent weights from Redis.

    The orchestrator calls this at the start of each cycle
    to get the most up-to-date weights.

    If no weights are stored yet (first run, or Redis cleared),
    returns the DEFAULT_WEIGHTS from Phase 1.

    Returns:
    --------
    {
        "SignalAgent":    0.32,
        "SentimentAgent": 0.28,
        "RiskAgent":      0.22,
        "PortfolioAgent": 0.18,
    }
    """

    try:
        raw = await redis_client.get(WEIGHTS_KEY)

        if raw:
            weights = json.loads(raw)
            logger.debug(f"Agent weights from Redis: {weights}")
            return weights

    except Exception as e:
        logger.warning(f"Could not read agent weights from Redis: {e}")

    logger.debug("Using default agent weights")
    return DEFAULT_WEIGHTS.copy()


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

        # --------------------------------------------------------
        # CALCULATE ACCURACY PER AGENT
        # --------------------------------------------------------

        new_weights = _calculate_new_weights(trades)

        # --------------------------------------------------------
        # STORE IN REDIS
        # --------------------------------------------------------

        await redis_client.set(
            WEIGHTS_KEY,
            json.dumps(new_weights),
        )

        logger.info(
            f"Agent weights updated | user={user_id} | "
            f"symbol={symbol} | based_on={total} trades | "
            f"weights={new_weights}"
        )

    except Exception as e:
        logger.warning(f"Weight update failed (non-fatal): {e}")


# ============================================================
# WEIGHT CALCULATION LOGIC
# ============================================================

def _calculate_new_weights(trades: list) -> dict:
    """
    Calculate new weights based on each agent's historical accuracy.

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

    Returns:
    --------
    Updated weights dict
    """

    agent_stats = {
        "SignalAgent":    {"correct": 0, "total": 0},
        "SentimentAgent": {"correct": 0, "total": 0},
        "RiskAgent":      {"correct": 0, "total": 0},
        "PortfolioAgent": {"correct": 0, "total": 0},
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

            for agent in DEFAULT_WEIGHTS:
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
            new_weights = DEFAULT_WEIGHTS.copy()
    else:
        new_weights = DEFAULT_WEIGHTS.copy()

    # Renormalise so weights sum to 1.0
    total = sum(new_weights.values())
    if total > 0:
        new_weights = {
            agent: round(w / total, 4)
            for agent, w in new_weights.items()
        }

    return new_weights