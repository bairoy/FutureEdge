"""
app/agents/calibration_agent.py
================================
Calibration agent — nightly meta-agent that monitors and calibrates the system.
Compares agent votes against actual trade outcomes (wins/losses).
Computes Accuracy, Precision, and Brier score.
Updates consensus weights and adaptive decision thresholds in Redis.
"""

import json
from datetime import datetime, timezone
from loguru import logger
from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.db.models.trade import Trade
from app.db.redis import redis_client

# Redis Keys
REDIS_KEY_CALIBRATION = "futureedge:calibration:stats"
REDIS_KEY_WEIGHTS = "futureedge:agent_weights"
REDIS_KEY_THRESHOLD = "futureedge:decision_threshold"

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

# Default Baseline Weights
DEFAULT_WEIGHTS = DEFAULT_WEIGHTS_BY_REGIME["UNKNOWN"]

MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.60



async def run_calibration() -> dict:
    """
    Run nightly calibration of agent votes against trade outcomes.
    Reads last 100 closed trades, calculates statistics,
    and updates weights and decision thresholds in Redis.
    """
    logger.info("Running CalibrationAgent nightly analysis...")
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade)
                .where(Trade.status == "CLOSED")
                .order_by(Trade.created_at.desc())
                .limit(100)
            )
            trades = list(result.scalars().all())
            
        if not trades:
            logger.info("CalibrationAgent: No closed trades found in database. Setting defaults.")
            await _set_default_redis_values()
            return {"status": "skipped", "reason": "no trades"}

        # --------------------------------------------------------
        # 1. EVALUATE AGENT PERFORMANCE (ACCURACY, PRECISION, BRIER)
        # --------------------------------------------------------
        agent_stats = {
            agent: {
                "correct": 0,
                "total_active": 0,
                "buy_correct": 0,
                "buy_total": 0,
                "sell_correct": 0,
                "sell_total": 0,
                "brier_sum": 0.0,
            }
            for agent in DEFAULT_WEIGHTS
        }

        for trade in trades:
            if trade.realized_pnl is None:
                continue

            trade_won = trade.realized_pnl > 0
            trade_long = trade.direction == "LONG"
            trade_short = trade.direction == "SHORT"
            
            # Map vote records
            votes = trade.agent_consensus or []
            for v in votes:
                agent = v.get("agent")
                decision = v.get("decision")
                confidence = v.get("confidence", 0.5)

                if agent not in agent_stats or decision not in ("BUY", "SELL"):
                    continue

                stats = agent_stats[agent]
                stats["total_active"] += 1

                # Check if decision was correct relative to trade outcome
                is_correct = False
                if trade_long:
                    # LONG trade: BUY was correct if it won, SELL was correct if it lost
                    is_correct = (decision == "BUY" and trade_won) or (decision == "SELL" and not trade_won)
                elif trade_short:
                    # SHORT trade: SELL was correct if it won, BUY was correct if it lost
                    is_correct = (decision == "SELL" and trade_won) or (decision == "BUY" and not trade_won)

                if is_correct:
                    stats["correct"] += 1

                # Update Brier score components
                # Brier score error = (confidence - actual_binary_outcome)^2
                actual_outcome = 1.0 if is_correct else 0.0
                stats["brier_sum"] += (confidence - actual_outcome) ** 2

                # Direction-specific precision
                if decision == "BUY":
                    stats["buy_total"] += 1
                    if is_correct:
                        stats["buy_correct"] += 1
                elif decision == "SELL":
                    stats["sell_total"] += 1
                    if is_correct:
                        stats["sell_correct"] += 1

        # Format and compile calibration metrics
        report = {}
        for agent, stats in agent_stats.items():
            tot = stats["total_active"]
            accuracy = stats["correct"] / tot if tot > 0 else 0.5
            brier_score = stats["brier_sum"] / tot if tot > 0 else 0.25  # 0.25 is brier score for random 50% forecast
            
            p_buy = stats["buy_correct"] / stats["buy_total"] if stats["buy_total"] > 0 else None
            p_sell = stats["sell_correct"] / stats["sell_total"] if stats["sell_total"] > 0 else None

            report[agent] = {
                "accuracy": round(accuracy, 4),
                "precision_buy": round(p_buy, 4) if p_buy is not None else "N/A",
                "precision_sell": round(p_sell, 4) if p_sell is not None else "N/A",
                "brier_score": round(brier_score, 4),
                "total_active_votes": tot
            }

        # Write stats to Redis
        calibration_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sample_size": len(trades),
            "agents": report
        }
        await redis_client.set(REDIS_KEY_CALIBRATION, json.dumps(calibration_payload))
        logger.info(f"✅ CalibrationAgent report stored: {report}")

        # --------------------------------------------------------
        # 2. UPDATE SYSTEM CONSENSUS WEIGHTS (REGIME-SPECIFIC)
        # --------------------------------------------------------
        # Group trades by regime
        trades_by_regime = {}
        for t in trades:
            reg = "UNKNOWN"
            consensus = t.agent_consensus or []
            for vote_data in consensus:
                if isinstance(vote_data, dict) and "regime" in vote_data:
                    reg = vote_data["regime"].upper()
                    break
            if reg not in DEFAULT_WEIGHTS_BY_REGIME:
                reg = "UNKNOWN"
            trades_by_regime.setdefault(reg, []).append(t)

        calibrated_weights = {}
        for reg in DEFAULT_WEIGHTS_BY_REGIME:
            reg_trades = trades_by_regime.get(reg, [])
            # Only update if we have at least 5 trades in this specific regime
            if len(reg_trades) >= 5:
                reg_weights = _calibrate_weights_for_regime(reg_trades, reg)
                # Store regime-specific weights: f"futureedge:agent_weights:{reg}"
                await redis_client.set(f"{REDIS_KEY_WEIGHTS}:{reg}", json.dumps(reg_weights))
                calibrated_weights[reg] = reg_weights
                logger.info(f"✅ CalibrationAgent: agent weights updated for regime {reg}: {reg_weights}")
                if reg == "UNKNOWN":
                    # Also write to global key
                    await redis_client.set(REDIS_KEY_WEIGHTS, json.dumps(reg_weights))
            else:
                logger.info(f"CalibrationAgent: skipped weight update for regime {reg} due to insufficient trades ({len(reg_trades)} < 5)")

        # --------------------------------------------------------
        # 3. UPDATE ADAPTIVE CONSENSUS DECISION THRESHOLD
        # --------------------------------------------------------
        # Win rate of the last 20 trades
        recent_trades = trades[:20]
        wins = sum(1 for t in recent_trades if t.realized_pnl and t.realized_pnl > 0)
        total_pnl_trades = sum(1 for t in recent_trades if t.realized_pnl is not None)
        
        win_rate = wins / total_pnl_trades if total_pnl_trades > 0 else 0.5
        
        # Adaptive Threshold Formula:
        # - High win rate (>=60%) -> lower threshold to 0.35 (capitalize on streak)
        # - Low win rate (<=45%) -> raise threshold to 0.48 (filter entries strictly)
        # - Otherwise -> default 0.40
        if win_rate >= 0.60:
            threshold = 0.35
        elif win_rate <= 0.45:
            threshold = 0.48
        else:
            threshold = 0.40

        await redis_client.set(REDIS_KEY_THRESHOLD, str(threshold))
        logger.info(f"✅ CalibrationAgent: adaptive decision threshold set to {threshold:.2f} (win_rate={win_rate*100:.1f}%)")

        return {
            "status": "success",
            "report": report,
            "weights": calibrated_weights,
            "threshold": threshold,
            "win_rate_20": win_rate
        }

    except Exception as e:
        logger.error(f"CalibrationAgent failed: {e}")
        return {"status": "error", "error": str(e)}


def _calibrate_weights_for_regime(reg_trades: list, regime: str) -> dict:
    """Helper to calibrate weights for a specific regime's trades."""
    regime = regime.upper()
    baseline_weights = DEFAULT_WEIGHTS_BY_REGIME.get(regime, DEFAULT_WEIGHTS)
    
    agent_stats = {
        agent: {"correct": 0, "total_active": 0}
        for agent in baseline_weights
    }

    for trade in reg_trades:
        if trade.realized_pnl is None:
            continue

        trade_won = trade.realized_pnl > 0
        trade_long = trade.direction == "LONG"
        trade_short = trade.direction == "SHORT"
        
        votes = trade.agent_consensus or []
        for v in votes:
            if not isinstance(v, dict):
                continue
            agent = v.get("agent")
            decision = v.get("decision")

            if agent not in agent_stats or decision not in ("BUY", "SELL"):
                continue

            stats = agent_stats[agent]
            stats["total_active"] += 1

            is_correct = False
            if trade_long:
                is_correct = (decision == "BUY" and trade_won) or (decision == "SELL" and not trade_won)
            elif trade_short:
                is_correct = (decision == "SELL" and trade_won) or (decision == "BUY" and not trade_won)

            if is_correct:
                stats["correct"] += 1

    report = {}
    for agent, stats in agent_stats.items():
        tot = stats["total_active"]
        accuracy = stats["correct"] / tot if tot > 0 else 0.5
        report[agent] = {"accuracy": accuracy, "total_active_votes": tot}

    new_weights = {}
    active_accuracies = {agent: info["accuracy"] for agent, info in report.items() if info["total_active_votes"] >= 5}
    inactive_agents = {agent for agent, info in report.items() if info["total_active_votes"] < 5}

    if active_accuracies:
        total_acc = sum(active_accuracies.values())
        if total_acc > 0:
            active_pool = 0.60
            inactive_pool = 0.40
            
            active_shares = {
                agent: (acc / total_acc) * active_pool
                for agent, acc in active_accuracies.items()
            }
            inactive_share = inactive_pool / max(len(inactive_agents), 1)

            for agent in baseline_weights:
                if agent in active_shares:
                    w = active_shares[agent]
                else:
                    w = inactive_share
                
                new_weights[agent] = round(max(MIN_WEIGHT, min(MAX_WEIGHT, w)), 4)
        else:
            new_weights = baseline_weights.copy()
    else:
        new_weights = baseline_weights.copy()

    total_wt = sum(new_weights.values())
    if total_wt > 0:
        new_weights = {agent: round(w / total_wt, 4) for agent, w in new_weights.items()}

    return new_weights


async def _set_default_redis_values():
    """Write standard default weights and thresholds to Redis."""
    try:
        await redis_client.set(REDIS_KEY_WEIGHTS, json.dumps(DEFAULT_WEIGHTS))
        await redis_client.set(REDIS_KEY_THRESHOLD, "0.40")
        for reg, w in DEFAULT_WEIGHTS_BY_REGIME.items():
            await redis_client.set(f"{REDIS_KEY_WEIGHTS}:{reg}", json.dumps(w))
    except Exception as e:
        logger.error(f"Failed to set default redis values: {e}")

