"""
app/agents/execution_agent.py
==============================
Execution agent — the FINAL action layer of the system.

This node:
  - Checks kill switch (Redis)
  - Validates HITL approval
  - Converts Rupees to shares
  - Places order via broker adapter (mock or Zerodha)
  - Writes trade record to PostgreSQL WITH user_id
  - Publishes trade result to Redis pub/sub for frontend

MULTI-USER CHANGE:
------------------
The trade record now includes user_id from AgentState.
This means each user's trade history is completely separate.
The risk agent's Kelly criterion uses per-user trade history.

SAFETY LAYERS (in order):
--------------------------
1. Kill switch check   → Redis TRADING_HALT key
2. No-trade check      → direction is NONE or size is 0
3. HITL approval check → must be approved if hitl_required
4. Share conversion    → Rupees / price = integer shares
5. Broker order        → mock or real Zerodha Kite
6. PostgreSQL write    → permanent record with user_id
7. Redis publish       → frontend real-time update
"""

import json

from loguru import logger

from app.db.redis import redis_client, KEY_TRADING_HALT, CHANNEL_TRADE_EXECUTED
from app.db.postgres import AsyncSessionLocal
from app.db.repos.trade_repo import TradeRepo
from app.brokers.base import get_broker
from app.graph.state import AgentState, TradeProposal


async def execution_node(state: AgentState) -> dict:
    """
    Final node in the LangGraph workflow.

    Receives the complete AgentState including:
      - consensus     : the TradeProposal from the orchestrator
      - user_id       : who triggered this cycle
      - hitl_required : whether human approval was needed
      - run_id        : the unique workflow identifier
    """

    proposal: TradeProposal = state["consensus"]
    run_id:   str           = state["run_id"]
    user_id:  str           = state.get("user_id", "anonymous")

    try:

        # ====================================================
        # LAYER 0: NSE MARKET HOURS CHECK
        # ====================================================
        from app.brokers.symbol_mapper import is_market_open
        from app.core.config import settings
        
        if settings.ACTIVE_BROKER.lower() != "mock" and not is_market_open():
            logger.warning(
                f"MARKET CLOSED | Execution blocked | run_id={run_id} | user_id={user_id}"
            )
            return {
                "executed_trade":  None,
                "execution_error": "MARKET_CLOSED",
                "completed_nodes": ["execution"],
                "logs":            ["Execution blocked — National Stock Exchange (NSE) is closed."],
            }

        # ====================================================
        # LAYER 1: KILL SWITCH
        # ====================================================
        # Redis key TRADING_HALT="1" means halt all trading.
        # Set by risk managers via POST /api/v1/kill-switch/halt.
        # Checked here BEFORE doing anything else.

        halt = await redis_client.get(KEY_TRADING_HALT)

        if halt == "1":
            logger.critical(
                f"KILL SWITCH ACTIVE | run_id={run_id} | user_id={user_id}"
            )
            return {
                "executed_trade":  None,
                "execution_error": "KILL_SWITCH_ACTIVE",
                "completed_nodes": ["execution"],
                "logs":            ["Execution blocked by kill switch"],
            }

        # ====================================================
        # LAYER 2: NO-TRADE CHECK
        # ====================================================
        # HOLD decisions produce direction="NONE" and size=0.
        # Nothing to execute — return cleanly.

        if proposal.direction == "NONE" or (proposal.size <= 0 and not state.get("user_override_quantity")):
            logger.info(f"No trade to execute (HOLD) | run_id={run_id}")
            return {
                "executed_trade":  None,
                "completed_nodes": ["execution"],
                "logs":            ["No trade executed (HOLD decision)"],
            }

        # ====================================================
        # LAYER 3: HITL APPROVAL CHECK
        # ====================================================
        # If the orchestrator flagged this trade for human review,
        # the human_approved flag MUST be True before we proceed.
        # This is a second safety check independent of the HITL node.

        if state.get("hitl_required") and not proposal.human_approved:
            logger.error(
                f"HITL not approved | run_id={run_id} | user_id={user_id}"
            )
            return {
                "executed_trade":  None,
                "execution_error": "HITL_NOT_APPROVED",
                "completed_nodes": ["execution"],
                "logs":            ["Execution blocked — HITL approval missing"],
            }

        # ====================================================
        # CONVERT RUPEES TO SHARES (WITH OVERRIDES)
        # ====================================================
        price  = proposal.entry_price

        if state.get("user_override_quantity") is not None:
            shares = int(state["user_override_quantity"])
            logger.info(f"Using user override quantity: {shares} shares")
        elif state.get("user_override_rupees") is not None:
            user_rupees = float(state["user_override_rupees"])
            shares = int(user_rupees / price) if price > 0 else 0
            logger.info(f"Using user override rupees: ₹{user_rupees} -> {shares} shares")
        else:
            shares = int(proposal.size / price) if price > 0 else 0
            logger.info(f"Using Kelly size: ₹{proposal.size:.2f} -> {shares} shares")

        if shares <= 0:
            logger.warning(
                f"Position ₹{proposal.size:.0f} too small "
                f"at ₹{price:.2f} per share | run_id={run_id}"
            )
            return {
                "executed_trade":  None,
                "execution_error": "POSITION_TOO_SMALL",
                "completed_nodes": ["execution"],
                "logs": [
                    f"Position too small: calculated {shares} shares at ₹{price:.2f}"
                ],
            }

        # ====================================================
        # PLACE ORDER VIA BROKER
        # ====================================================
        # get_broker() reads settings.ACTIVE_BROKER:
        #   "mock"    → MockBroker (simulates, no real money)
        #   "zerodha" → ZerodhaBroker (real NSE orders)
        #
        # Both implement the same BrokerBase interface,
        # so this code never needs to change when switching brokers.

        broker = get_broker()
        # Broker connection is established at startup via lifespan.
        # Only reconnect if the connection was lost.
        if not await broker.is_connected():
            await broker.connect()

        # SEBI compliance: use LIMIT orders with a price buffer
        # instead of MARKET orders (prohibited via API as of 2026).
        # Buffer: 0.05% above LTP for BUY, 0.05% below for SELL.
        price_buffer = price * 0.0005  # 0.05%
        if proposal.direction == "LONG":
            limit_price = round(price + price_buffer, 2)
        else:
            limit_price = round(price - price_buffer, 2)

        order_result = await broker.place_order(
            symbol     = proposal.symbol,
            direction  = proposal.direction,
            quantity   = float(shares),
            order_type = "LIMIT",
            price      = limit_price,
        )

        # ====================================================
        # BUILD TRADE RECORD
        # ====================================================

        from app.core.config import settings
        broker_name = settings.ACTIVE_BROKER

        if order_result.success:
            trade_record = {
                "run_id":          run_id,
                "user_id":         user_id,
                "symbol":          proposal.symbol,
                "direction":       proposal.direction,
                "shares":          shares,
                "position_rupees": round(proposal.size, 2),
                "entry_price":     price,
                "fill_price":      order_result.fill_price,
                "order_id":        order_result.order_id,
                "status":          order_result.status,
                "broker":          broker_name,
                "human_approved":  proposal.human_approved,
                "risk_score":      proposal.risk_score,
            }

            logger.info(
                f"EXECUTED | {proposal.direction} {shares} shares "
                f"{proposal.symbol} @ ₹{order_result.fill_price:.2f} | "
                f"order_id={order_result.order_id} | "
                f"run_id={run_id} | user_id={user_id}"
            )

        else:
            trade_record = {
                "run_id":    run_id,
                "user_id":   user_id,
                "symbol":    proposal.symbol,
                "direction": proposal.direction,
                "shares":    shares,
                "status":    "FAILED",
                "error":     order_result.error_message,
            }

            logger.error(
                f"ORDER FAILED | {proposal.symbol} | "
                f"{order_result.error_message} | run_id={run_id}"
            )

        # ====================================================
        # WRITE TO POSTGRESQL
        # ====================================================
        # We write to DB regardless of success/failure.
        # This gives us:
        #   - Complete audit trail per user
        #   - Data for Kelly criterion (per-user win rate)
        #   - PnL tracking per user
        #
        # user_id on the trade record links it back to the user
        # who triggered this workflow cycle.

        try:
            async with AsyncSessionLocal() as session:
                await TradeRepo.save_trade(
                    session           = session,
                    proposal          = proposal,
                    run_id            = run_id,
                    user_id           = user_id,
                    quantity          = shares,
                    broker            = broker_name,
                    broker_order_id   = order_result.order_id if order_result.success else None,
                    actual_fill_price = order_result.fill_price if order_result.success else None,
                )

        except Exception as db_err:
            # DB write failure is logged but does NOT stop the workflow.
            # The trade already happened — we cannot undo it.
            logger.error(
                f"DB write failed for trade run_id={run_id}: {db_err}"
            )

        # ====================================================
        # WRITE TO QDRANT EPISODIC MEMORY (Phase 2 — new)
        # ====================================================
        market_vector = state.get("market_vector")
        if market_vector is not None:
            try:
                from app.memory.qdrant_store import store_trade_memory
                
                agent_votes_dict = {
                    vote.agent: vote.decision
                    for vote in proposal.agent_consensus
                }
                
                # Initially NEUTRAL outcome with 0.0 P&L pct.
                # The exit monitor will update this to WIN/LOSS when closed.
                await store_trade_memory(
                    run_id=run_id,
                    user_id=user_id,
                    symbol=proposal.symbol,
                    direction=proposal.direction,
                    vector=market_vector,
                    outcome="NEUTRAL",
                    pnl_pct=0.0,
                    regime=state["market_context"].regime if hasattr(state["market_context"], "regime") else "UNKNOWN",
                    agent_votes=agent_votes_dict,
                    risk_score=proposal.risk_score,
                )
                logger.info(f"Episodic memory stored in Qdrant for run_id={run_id}")
            except Exception as q_err:
                logger.warning(f"Failed to store episodic memory in Qdrant: {q_err}")

        # ====================================================
        # PUBLISH TO REDIS PUB/SUB
        # ====================================================
        # The frontend WebSocket listens to this channel.
        # On publish, the dashboard updates the trade table
        # and portfolio panel for this user in real time.

        try:
            await redis_client.publish(
                CHANNEL_TRADE_EXECUTED,
                json.dumps({
                    "run_id":    run_id,
                    "user_id":   user_id,
                    "symbol":    proposal.symbol,
                    "direction": proposal.direction,
                    "shares":    shares,
                    "price":     order_result.fill_price if order_result.success else price,
                    "status":    order_result.status,
                    "success":   order_result.success,
                }),
            )
        except Exception as pub_err:
            logger.warning(f"Redis publish failed: {pub_err}")

        # ====================================================
        # RETURN STATE UPDATE
        # ====================================================

        if order_result.success:
            return {
                "executed_trade":  trade_record,
                "execution_error": None,
                "completed_nodes": ["execution"],
                "logs": [
                    f"Executed {proposal.direction} {shares} "
                    f"{proposal.symbol} @ ₹{order_result.fill_price:.2f}"
                ],
            }
        else:
            return {
                "executed_trade":  None,
                "execution_error": order_result.error_message,
                "completed_nodes": ["execution"],
                "logs": [
                    f"Order failed: {order_result.error_message}"
                ],
            }

    except Exception as e:
        # Catch-all failsafe — log aggressively and fail cleanly.
        # Never leave the system in an unknown state silently.
        logger.exception(
            f"Execution node failure | run_id={run_id} | user_id={user_id} | {e}"
        )
        return {
            "executed_trade":  None,
            "execution_error": str(e),
            "completed_nodes": ["execution"],
            "logs":            [f"Execution failed: {e}"],
        }