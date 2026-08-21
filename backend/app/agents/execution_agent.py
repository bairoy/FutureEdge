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
0. Analysis mode guard → refuses INVESTING mode outright (it places no orders)
1. Kill switch check   → kill_switch_service.is_trading_halted() (fails closed)
2. No-trade check      → direction is NONE or size is 0
3. HITL approval check → must be approved if hitl_required
4. Share conversion    → Rupees / price = integer shares
5. Broker order        → mock or real Zerodha Kite
6. PostgreSQL write    → permanent record with user_id
7. Redis publish       → frontend real-time update
"""

import json

from loguru import logger

from app.db.redis import redis_client, CHANNEL_TRADE_EXECUTED
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

    run_id:  str = state.get("run_id", "unknown")
    user_id: str = state.get("user_id", "anonymous")

    # ====================================================
    # LAYER -1: ANALYSIS MODE GUARD
    # ====================================================
    # Investing mode is advisory: it produces research and never places an
    # order. The graph enforces that structurally — the investing branch ends
    # at thesis_agent and has no edge to this node — so reaching here in
    # INVESTING mode means the graph was rewired, not that a trade was
    # approved.
    #
    # Checked BEFORE state["consensus"] is read, because the investing branch
    # never populates it. Checked before the kill switch too: this is not a
    # risk decision that could be waived, it is a mode that has no orders.
    #
    # This guard is why "advisory only" is a property of the system rather
    # than of the current graph topology. If CNC execution is ever added, the
    # hard rule in CLAUDE.md still stands and this is where it is enforced.

    if (state.get("analysis_mode") or "TRADING").upper() == "INVESTING":
        logger.error(
            f"BLOCKED: execution reached in INVESTING mode | run_id={run_id} | "
            f"user_id={user_id} — investing mode places no orders"
        )
        return {
            "executed_trade":  None,
            "execution_error": "INVESTING_MODE_NO_EXECUTION",
            "completed_nodes": ["execution"],
            "logs":            ["Execution refused — Investing mode is advisory and places no orders."],
        }

    proposal: TradeProposal = state["consensus"]

    try:
        logs_accumulated = []

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
        # Set by risk managers via POST /api/v1/kill-switch/halt.
        # Checked here BEFORE doing anything else.
        #
        # Goes through is_trading_halted() rather than reading the Redis key
        # directly: the service treats an unreachable Redis as HALTED. Reading
        # the key here used to let a Redis outage raise past this check, or a
        # None reply read as "not halted" — both fail open on the order path.

        from app.services.kill_switch_service import is_trading_halted

        if await is_trading_halted():
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

        if state.get("hitl_required") and not proposal.human_approved:
            logger.error(
                f"HITL not approved | run_id={run_id} | user_id={user_id}"
            )
            
            # Notify the frontend of the rejection/failure so the dashboard syncs
            try:
                await redis_client.publish(
                    CHANNEL_TRADE_EXECUTED,
                    json.dumps({
                        "run_id":    run_id,
                        "user_id":   user_id,
                        "symbol":    proposal.symbol,
                        "direction": proposal.direction,
                        "shares":    0,
                        "price":     0.0,
                        "status":    "REJECTED",
                        "success":   False,
                    }),
                )
            except Exception as pub_err:
                logger.warning(f"Redis publish on reject failed: {pub_err}")

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
            logger.info(f"No user overrides. Using consensus proposal size: ₹{proposal.size} -> {shares} shares")

        if shares <= 0:
            logger.warning(
                f"Position too small at ₹{price:.2f} per share | run_id={run_id}"
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
        # LAYER 1.5: EXISTING POSITION CHECK (AUTO-EXITS & SCALE-INS)
        # ====================================================
        # If we already have an open position in this symbol for this user:
        # - Same direction -> scale-in (pyramiding/averaging price)
        # - Opposite direction -> close the position (reversal exit) and continue to new entry.
        from sqlalchemy import select, and_
        from app.db.models.trade import Trade
        from datetime import timezone, datetime
        
        is_paper = state.get("paper_trade", True)
        broker_name = "paper" if is_paper else settings.ACTIVE_BROKER.lower()
        
        existing_trade = None
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Trade).where(
                        and_(
                            Trade.user_id == user_id,
                            Trade.symbol == proposal.symbol,
                            Trade.status == "OPEN",
                            Trade.broker == broker_name
                        )
                    )
                )
                existing_trade = result.scalar_one_or_none()
        except Exception as db_err:
            logger.error(f"Error checking for existing open trades: {db_err}")

        if proposal.direction == "CLOSE":
            if not existing_trade:
                logger.warning(f"Consensus exit proposal received for {proposal.symbol}, but no open position found.")
                return {
                    "executed_trade":  None,
                    "completed_nodes": ["execution"],
                    "logs":            [f"No active position found to close for {proposal.symbol}."],
                }

            logger.warning(
                f"🎯 CONSENSUS EXIT | Agent analysis exit signal received for "
                f"open trade {existing_trade.id} ({existing_trade.direction}) on {proposal.symbol}"
            )
            
            if is_paper:
                from app.brokers.mock import MockBroker
                broker = MockBroker()
            else:
                broker = get_broker()
                
            if not await broker.is_connected():
                await broker.connect()
                
            exit_direction = "SHORT" if existing_trade.direction == "LONG" else "LONG"
            qty = existing_trade.quantity
            
            # SEBI compliance: use LIMIT order with buffer
            price_buffer = price * 0.0005
            limit_price = round(price - price_buffer if exit_direction == "SHORT" else price + price_buffer, 2)
            
            try:
                order_result = await broker.place_order(
                    symbol     = proposal.symbol,
                    direction  = exit_direction,
                    quantity   = float(qty),
                    order_type = "LIMIT",
                    price      = limit_price,
                )
            except Exception as e:
                logger.error(f"Consensus exit broker order failed: {e}")
                from app.brokers.kite_errors import handle_kite_error
                await handle_kite_error(
                    e, context=f"execution:consensus_exit:{proposal.symbol}"
                )
                order_result = None
                
            if not order_result or not order_result.success:
                err_msg = order_result.error_message if order_result else "Broker order exception"
                logger.error(f"Consensus exit order failed for trade {existing_trade.id}: {err_msg}")
                return {
                    "executed_trade":  None,
                    "execution_error": f"CONSENSUS_EXIT_FAILED: {err_msg}",
                    "completed_nodes": ["execution"],
                    "logs":            [f"Consensus exit order failed: {err_msg}"],
                }
                
            actual_exit = order_result.fill_price or price
            
            # Close trade in database
            async with AsyncSessionLocal() as session:
                closed_trade = await TradeRepo.close_trade(
                    session=session,
                    trade_id=existing_trade.id,
                    user_id=user_id,
                    exit_price=actual_exit,
                )
                
            pnl = 0.0
            if closed_trade:
                pnl = closed_trade.realized_pnl or 0.0
                try:
                    await redis_client.publish(
                        CHANNEL_TRADE_EXECUTED,
                        json.dumps({
                            "event":     "TRADE_CLOSED",
                            "reason":    "CONSENSUS_EXIT",
                            "trade_id":  existing_trade.id,
                            "user_id":   user_id,
                            "symbol":    proposal.symbol,
                            "direction": existing_trade.direction,
                            "entry":     existing_trade.entry_price,
                            "exit":      actual_exit,
                            "pnl":       pnl,
                            "pnl_pct":   closed_trade.pnl_pct,
                        }),
                    )
                except Exception as pub_err:
                    logger.warning(f"Redis publish failed on consensus exit: {pub_err}")
                    
                # Trigger episodic memory outcome update in Qdrant
                try:
                    from app.memory.qdrant_store import update_trade_outcome
                    outcome_str = "WIN" if pnl > 0.0 else ("LOSS" if pnl < 0.0 else "NEUTRAL")
                    pnl_pct_val = closed_trade.pnl_pct or 0.0
                    await update_trade_outcome(
                        run_id=existing_trade.run_id,
                        outcome=outcome_str,
                        pnl_pct=pnl_pct_val,
                    )
                except Exception as q_err:
                    logger.warning(f"Failed to update consensus exit outcome in Qdrant: {q_err}")
                    
            return {
                "executed_trade":  {
                    "id":                existing_trade.id,
                    "user_id":           user_id,
                    "symbol":            proposal.symbol,
                    "direction":         existing_trade.direction,
                    "status":            "CLOSED",
                    "exit_price":        actual_exit,
                    "realized_pnl":      pnl,
                    "pnl_pct":           closed_trade.pnl_pct if closed_trade else 0.0,
                },
                "execution_error": None,
                "completed_nodes": ["execution"],
                "logs":            [f"Closed active {existing_trade.direction} position on consensus exit proposal"],
            }

        if existing_trade:
            if proposal.direction == existing_trade.direction:
                logger.info(f"Already in {existing_trade.direction} position for {proposal.symbol}. Attempting scale-in.")
                
                # Risk Guard 1: Limit max scale-in entries
                agent_consensus = existing_trade.agent_consensus or []
                scale_ins = [item for item in agent_consensus if isinstance(item, dict) and item.get("type") == "scale_in"]
                if len(scale_ins) >= settings.MAX_PYRAMID_ENTRIES:
                    logger.info(f"Max scale-in limit reached ({len(scale_ins)}/{settings.MAX_PYRAMID_ENTRIES}) for {proposal.symbol}.")
                    return {
                        "executed_trade":  None,
                        "completed_nodes": ["execution"],
                        "logs":            [f"Already in {existing_trade.direction} position, max scale-in limit reached ({settings.MAX_PYRAMID_ENTRIES}). Holding."],
                    }
                
                # Risk Guard 2: Total symbol exposure cap
                new_qty = existing_trade.quantity + shares
                new_exposure = price * new_qty
                if new_exposure >= settings.MAX_SYMBOL_EXPOSURE:
                    logger.info(f"Max exposure limit reached (₹{new_exposure:.2f} >= ₹{settings.MAX_SYMBOL_EXPOSURE:.2f}) for {proposal.symbol}.")
                    return {
                        "executed_trade":  None,
                        "completed_nodes": ["execution"],
                        "logs":            [f"Already in {existing_trade.direction} position, max exposure limit reached. Holding."],
                    }

                if is_paper:
                    from app.brokers.mock import MockBroker
                    broker = MockBroker()
                else:
                    broker = get_broker()
                    
                if not await broker.is_connected():
                    await broker.connect()

                # SEBI compliance: use LIMIT order with buffer
                price_buffer = price * 0.0005
                limit_price = round(price + price_buffer if proposal.direction == "LONG" else price - price_buffer, 2)

                try:
                    order_result = await broker.place_order(
                        symbol     = proposal.symbol,
                        direction  = proposal.direction,
                        quantity   = float(shares),
                        order_type = "LIMIT",
                        price      = limit_price,
                    )
                except Exception as e:
                    logger.error(f"Scale-in broker order failed: {e}")
                    from app.brokers.kite_errors import handle_kite_error
                    await handle_kite_error(
                        e, context=f"execution:scale_in:{proposal.symbol}"
                    )
                    order_result = None

                if not order_result or not order_result.success:
                    err_msg = order_result.error_message if order_result else "Broker order exception"
                    logger.error(f"Scale-in order failed for trade {existing_trade.id}: {err_msg}")
                    return {
                        "executed_trade":  None,
                        "execution_error": f"SCALE_IN_FAILED: {err_msg}",
                        "completed_nodes": ["execution"],
                        "logs":            [f"Scale-in order failed: {err_msg}"],
                    }

                actual_fill = order_result.fill_price or price
                new_entry_price = round(((existing_trade.entry_price * existing_trade.quantity) + (actual_fill * shares)) / new_qty, 4)

                # Recalculate Stop Loss and Take Profit targets relative to the new average entry price
                ctx = state.get("market_context")
                if ctx and hasattr(ctx, "volatility_24h"):
                    atr_proxy = max(ctx.volatility_24h * new_entry_price, new_entry_price * 0.005)
                else:
                    atr_proxy = new_entry_price * 0.02

                if existing_trade.direction == "LONG":
                    new_sl = round(new_entry_price - (1.5 * atr_proxy), 2)
                    new_tp = round(new_entry_price + (3.0 * atr_proxy), 2)
                else:
                    new_sl = round(new_entry_price + (1.5 * atr_proxy), 2)
                    new_tp = round(new_entry_price - (3.0 * atr_proxy), 2)

                updated_trade_record = None
                try:
                    async with AsyncSessionLocal() as session:
                        db_trade = await session.get(Trade, existing_trade.id)
                        if db_trade:
                            db_trade.quantity = new_qty
                            db_trade.entry_price = new_entry_price
                            db_trade.size = round(new_qty * new_entry_price, 2)
                            db_trade.stop_loss = new_sl
                            db_trade.take_profit = new_tp
                            
                            scale_in_event = {
                                "type": "scale_in",
                                "added_quantity": shares,
                                "fill_price": actual_fill,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "order_id": order_result.order_id,
                                "run_id": run_id,
                            }
                            db_trade.agent_consensus = (db_trade.agent_consensus or []) + [scale_in_event]
                            
                            await session.commit()
                            await session.refresh(db_trade)
                            
                            updated_trade_record = {
                                "id":                db_trade.id,
                                "user_id":           user_id,
                                "symbol":            proposal.symbol,
                                "direction":         db_trade.direction,
                                "status":            db_trade.status,
                                "quantity":          db_trade.quantity,
                                "entry_price":       db_trade.entry_price,
                                "size":              db_trade.size,
                                "stop_loss":         db_trade.stop_loss,
                                "take_profit":       db_trade.take_profit,
                                "agent_consensus":   db_trade.agent_consensus,
                            }
                except Exception as db_err:
                    logger.error(f"Failed to save scaled-in trade updates to DB: {db_err}")

                try:
                    await redis_client.publish(
                        CHANNEL_TRADE_EXECUTED,
                        json.dumps({
                            "event":           "TRADE_SCALED",
                            "run_id":          run_id,
                            "user_id":         user_id,
                            "symbol":          proposal.symbol,
                            "direction":       existing_trade.direction,
                            "added_shares":    shares,
                            "new_shares":      new_qty,
                            "fill_price":      actual_fill,
                            "new_entry_price": new_entry_price,
                            "status":          order_result.status,
                            "success":         order_result.success,
                        }),
                    )
                except Exception as pub_err:
                    logger.warning(f"Redis publish failed on scale-in: {pub_err}")

                return {
                    "executed_trade":  updated_trade_record or {
                        "id":                existing_trade.id,
                        "user_id":           user_id,
                        "symbol":            proposal.symbol,
                        "direction":         existing_trade.direction,
                        "status":            existing_trade.status,
                        "quantity":          new_qty,
                        "entry_price":       new_entry_price,
                        "size":              round(new_qty * new_entry_price, 2),
                    },
                    "execution_error": None,
                    "completed_nodes": ["execution"],
                    "logs":            [f"Scaled in {proposal.direction} position for {proposal.symbol}: added {shares} shares @ ₹{actual_fill:.2f}, new avg price ₹{new_entry_price:.2f}"],
                }

            elif proposal.direction not in ("NONE", ""):
                # Reversal exit!
                logger.warning(
                    f"🔄 REVERSAL EXIT | Opposite signal {proposal.direction} "
                    f"received while holding {existing_trade.direction} for {proposal.symbol} | "
                    f"trade={existing_trade.id}"
                )
                
                if is_paper:
                    from app.brokers.mock import MockBroker
                    broker = MockBroker()
                else:
                    broker = get_broker()
                    
                if not await broker.is_connected():
                    await broker.connect()
                    
                exit_direction = "SHORT" if existing_trade.direction == "LONG" else "LONG"
                qty = existing_trade.quantity
                
                # SEBI compliance: use LIMIT order with buffer
                price_buffer = price * 0.0005
                limit_price = round(price - price_buffer if exit_direction == "SHORT" else price + price_buffer, 2)
                
                try:
                    order_result = await broker.place_order(
                        symbol     = proposal.symbol,
                        direction  = exit_direction,
                        quantity   = float(qty),
                        order_type = "LIMIT",
                        price      = limit_price,
                    )
                except Exception as e:
                    logger.error(f"Reversal exit broker order failed: {e}")
                    from app.brokers.kite_errors import handle_kite_error
                    await handle_kite_error(
                        e, context=f"execution:reversal_exit:{proposal.symbol}"
                    )
                    order_result = None
                    
                if not order_result or not order_result.success:
                    err_msg = order_result.error_message if order_result else "Broker order exception"
                    logger.error(f"Reversal exit order failed for trade {existing_trade.id}: {err_msg}")
                    return {
                        "executed_trade":  None,
                        "execution_error": f"REVERSAL_EXIT_FAILED: {err_msg}",
                        "completed_nodes": ["execution"],
                        "logs":            [f"Reversal exit order failed: {err_msg}"],
                    }
                    
                actual_exit = order_result.fill_price or price
                
                # Close trade in database
                async with AsyncSessionLocal() as session:
                    closed_trade = await TradeRepo.close_trade(
                        session=session,
                        trade_id=existing_trade.id,
                        user_id=user_id,
                        exit_price=actual_exit,
                    )
                    
                if closed_trade:
                    pnl = closed_trade.realized_pnl or 0.0
                    try:
                        await redis_client.publish(
                            CHANNEL_TRADE_EXECUTED,
                            json.dumps({
                                "event":     "TRADE_CLOSED",
                                "reason":    "REVERSAL_EXIT",
                                "trade_id":  existing_trade.id,
                                "user_id":   user_id,
                                "symbol":    proposal.symbol,
                                "direction": existing_trade.direction,
                                "entry":     existing_trade.entry_price,
                                "exit":      actual_exit,
                                "pnl":       pnl,
                                "pnl_pct":   closed_trade.pnl_pct,
                            }),
                        )
                    except Exception as pub_err:
                        logger.warning(f"Redis publish failed on reversal exit: {pub_err}")
                        
                    # Trigger episodic memory outcome update in Qdrant
                    try:
                        from app.memory.qdrant_store import update_trade_outcome
                        outcome_str = "WIN" if pnl > 0.0 else ("LOSS" if pnl < 0.0 else "NEUTRAL")
                        pnl_pct_val = closed_trade.pnl_pct or 0.0
                        await update_trade_outcome(
                            run_id=existing_trade.run_id,
                            outcome=outcome_str,
                            pnl_pct=pnl_pct_val,
                        )
                    except Exception as q_err:
                        logger.warning(f"Failed to update reversal exit outcome in Qdrant: {q_err}")
                        
                logs_accumulated.append(f"Closed existing {existing_trade.direction} position due to opposite signal ({proposal.direction})")

        # ====================================================
        # PLACE ORDER VIA BROKER (Normal / Reversal Entry)
        # ====================================================
        is_paper = state.get("paper_trade", True)

        if is_paper:
            from app.brokers.mock import MockBroker
            broker = MockBroker()
            broker_name = "paper"
        else:
            broker = get_broker()
            from app.core.config import settings
            broker_name = settings.ACTIVE_BROKER

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
                    # A rejected order must NOT be recorded as an open position.
                    # save_trade() defaults status to "OPEN", and this argument
                    # was previously omitted — so an order the broker refused
                    # (expired token, no static IP allowlist, insufficient
                    # margin) still wrote status=OPEN with a NULL
                    # broker_order_id. The system then believed it held a
                    # position that does not exist at the broker, and
                    # exit_monitor spun trying to square it off forever.
                    status            = None if order_result.success else "FAILED",
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
                "logs": logs_accumulated + [
                    f"Executed {proposal.direction} {shares} "
                    f"{proposal.symbol} @ ₹{order_result.fill_price:.2f}"
                ],
            }
        else:
            return {
                "executed_trade":  None,
                "execution_error": order_result.error_message,
                "completed_nodes": ["execution"],
                "logs": logs_accumulated + [
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