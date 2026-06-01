"""
app/jobs/exit_monitor.py
=========================
Background exit monitoring engine — continuously checks all OPEN
trades against current market prices and triggers automatic exits
when stop-loss or take-profit levels are breached.

WHY THIS IS CRITICAL:
----------------------
Without this engine:
  - Trades stay OPEN forever → infinite position accumulation
  - Kelly criterion never gets closed-trade data → falls back to defaults
  - Agent weight updater never triggers → weights stay hardcoded
  - Real Zerodha positions accumulate → margin depletion / margin call

HOW IT WORKS:
--------------
1. Runs as an asyncio background task started in lifespan()
2. Every EXIT_MONITOR_INTERVAL_SECONDS (default 10s):
   a. Queries all OPEN trades from PostgreSQL
   b. Gets current price from Redis stream (live ticks) or broker LTP
   c. For each trade, checks:
      - Has stop-loss been breached?
      - Has take-profit been reached?
   d. On breach: places exit order via broker, calls close_trade(),
      updates Qdrant memory, and publishes to Redis pub/sub
3. Also tracks daily cumulative P&L and auto-halts if MAX_DAILY_LOSS_PCT
   is exceeded (daily drawdown protection)

INTEGRATION:
-------------
Started in runtime.lifespan() alongside KiteTicker and broker.
Stopped cleanly on shutdown.

    from app.jobs.exit_monitor import exit_monitor

    await exit_monitor.start()   # in lifespan startup
    await exit_monitor.stop()    # in lifespan shutdown
"""

import asyncio
import json
from datetime import datetime, timezone, time as dt_time
from zoneinfo import ZoneInfo

from loguru import logger

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.db.repos.trade_repo import TradeRepo
from app.db.redis import redis_client, CHANNEL_TRADE_EXECUTED
from app.brokers.base import get_broker

IST = ZoneInfo("Asia/Kolkata")
MARKET_FORCE_EXIT_TIME = dt_time(15, 0)   # 3:00 PM IST — force-close all MIS positions
MARKET_FORCE_EXIT_END_TIME = dt_time(15, 20)  # 3:20 PM IST — Zerodha auto-squareoff time limit


class ExitMonitor:
    """
    Background task that monitors open positions and triggers
    automatic exits when SL/TP levels are breached.
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._daily_pnl: float = 0.0       # cumulative daily realized P&L
        self._daily_pnl_reset_date: str = ""  # date string for reset tracking

    # --------------------------------------------------------
    # START / STOP
    # --------------------------------------------------------

    async def start(self) -> None:
        """Start the exit monitoring loop as a background task."""
        if self._running:
            logger.warning("ExitMonitor already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(
            f"🟢 ExitMonitor started | "
            f"interval={settings.EXIT_MONITOR_INTERVAL_SECONDS}s | "
            f"max_daily_loss={settings.MAX_DAILY_LOSS_PCT}%"
        )

    async def stop(self) -> None:
        """Stop the exit monitoring loop cleanly."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🔴 ExitMonitor stopped")

    # --------------------------------------------------------
    # MAIN MONITORING LOOP
    # --------------------------------------------------------

    async def _monitor_loop(self) -> None:
        """
        Main loop — runs every EXIT_MONITOR_INTERVAL_SECONDS.

        Catches all exceptions to ensure the monitor never crashes
        silently. A crash here would leave all trades unmonitored.
        """

        while self._running:
            try:
                await self._check_open_trades()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ExitMonitor cycle error (non-fatal): {e}")

            await asyncio.sleep(settings.EXIT_MONITOR_INTERVAL_SECONDS)

    # --------------------------------------------------------
    # CHECK ALL OPEN TRADES
    # --------------------------------------------------------

    async def _check_open_trades(self) -> None:
        """
        Query all OPEN trades and check each against current prices.
        """

        # Reset daily P&L counter at IST midnight (NOT UTC midnight).
        # NSE trading day is 9:15 AM – 3:30 PM IST. If we reset at UTC midnight,
        # the cap could be wrong by up to 5:30 hours.
        today = datetime.now(IST).strftime("%Y-%m-%d")  # IST date
        if today != self._daily_pnl_reset_date:
            self._daily_pnl = 0.0
            self._daily_pnl_reset_date = today
            logger.debug(f"ExitMonitor: daily P&L reset for {today} IST")

        async with AsyncSessionLocal() as session:
            # Get all OPEN trades across all users
            from sqlalchemy import select
            from app.db.models.trade import Trade

            result = await session.execute(
                select(Trade).where(
                    Trade.status == "OPEN",
                    Trade.stop_loss.is_not(None),  # only monitor trades with SL set
                )
            )
            open_trades = list(result.scalars().all())

        if not open_trades:
            return  # nothing to monitor

        # --------------------------------------------------------
        # TIME-BASED FORCED EXIT at 3:00 PM IST
        # NSE MIS (intraday) positions must be closed before 3:30 PM.
        # We force-close at 3:00 PM to avoid broker auto-squareoff fees.
        # --------------------------------------------------------
        now_ist = datetime.now(IST)
        if (
            now_ist.weekday() <= 4  # Monday-Friday
            and MARKET_FORCE_EXIT_TIME <= now_ist.time() <= MARKET_FORCE_EXIT_END_TIME
        ):
            logger.warning(
                f"⏰ FORCED EXIT: Market close approaching (3:00 PM IST). "
                f"Closing all {len(open_trades)} open trades."
            )
            broker = get_broker()
            for trade in open_trades:
                try:
                    current_price = await broker.get_ltp(trade.symbol)
                    if current_price > 0:
                        await self._execute_exit(trade, current_price, "MARKET_CLOSE_FORCED")
                except Exception as e:
                    logger.error(f"Forced exit failed for trade {trade.id}: {e}")
            return

        # Get current prices
        broker = get_broker()

        for trade in open_trades:
            try:
                await self._evaluate_trade(trade, broker)
            except Exception as e:
                logger.error(
                    f"ExitMonitor: error evaluating trade {trade.id}: {e}"
                )

    # --------------------------------------------------------
    # EVALUATE A SINGLE TRADE
    # --------------------------------------------------------

    async def _evaluate_trade(self, trade, broker) -> None:
        """
        Check if a trade's SL or TP has been breached.
        If so, place an exit order and close the trade.
        """

        # Get current price from broker
        try:
            current_price = await broker.get_ltp(trade.symbol)
        except Exception:
            # Fallback: try to get from Redis stream
            from app.data.feed import get_latest_tick
            tick = await get_latest_tick(trade.symbol)
            if tick:
                current_price = tick["ltp"]
            else:
                return  # can't get price, skip this cycle

        if current_price <= 0:
            return

        # --------------------------------------------------------
        # CHECK STOP-LOSS
        # --------------------------------------------------------

        sl_hit = False
        tp_hit = False

        if trade.stop_loss is not None:
            if trade.direction == "LONG" and current_price <= trade.stop_loss:
                sl_hit = True
            elif trade.direction == "SHORT" and current_price >= trade.stop_loss:
                sl_hit = True

        # --------------------------------------------------------
        # CHECK TAKE-PROFIT
        # --------------------------------------------------------

        if trade.take_profit is not None:
            if trade.direction == "LONG" and current_price >= trade.take_profit:
                tp_hit = True
            elif trade.direction == "SHORT" and current_price <= trade.take_profit:
                tp_hit = True

        # --------------------------------------------------------
        # TRIGGER EXIT
        # --------------------------------------------------------

        if sl_hit or tp_hit:
            exit_reason = "STOP_LOSS" if sl_hit else "TAKE_PROFIT"

            logger.warning(
                f"⚡ EXIT TRIGGERED | {exit_reason} | "
                f"trade={trade.id} | {trade.direction} {trade.symbol} | "
                f"entry=₹{trade.entry_price:.2f} | current=₹{current_price:.2f} | "
                f"SL={trade.stop_loss} | TP={trade.take_profit}"
            )

            await self._execute_exit(trade, current_price, exit_reason)

    # --------------------------------------------------------
    # EXECUTE EXIT ORDER
    # --------------------------------------------------------

    async def _execute_exit(
        self,
        trade,
        exit_price: float,
        reason: str,
    ) -> None:
        """
        Place an exit order and close the trade in the database.
        """

        if trade.broker == "paper":
            from app.brokers.mock import MockBroker
            broker = MockBroker()
        else:
            broker = get_broker()

        # Place counter-order to close position
        if trade.quantity > 0:
            try:
                # Exit direction is opposite of entry
                exit_direction = "SHORT" if trade.direction == "LONG" else "LONG"

                # Use LIMIT order for SEBI compliance
                price_buffer = exit_price * 0.0005
                if exit_direction == "LONG":
                    limit_price = round(exit_price + price_buffer, 2)
                else:
                    limit_price = round(exit_price - price_buffer, 2)

                order_result = await broker.place_order(
                    symbol=trade.symbol,
                    direction=exit_direction,
                    quantity=float(trade.quantity),
                    order_type="LIMIT",
                    price=limit_price,
                )

                if not order_result.success:
                    logger.error(
                        f"Exit order FAILED for trade {trade.id}: "
                        f"{order_result.error_message}"
                    )
                    return

                # Use actual fill price if available
                actual_exit = order_result.fill_price or exit_price

            except Exception as e:
                logger.error(f"Broker exit order failed for trade {trade.id}: {e}")
                actual_exit = exit_price
        else:
            actual_exit = exit_price

        # --------------------------------------------------------
        # CLOSE TRADE IN DATABASE
        # --------------------------------------------------------

        async with AsyncSessionLocal() as session:
            closed_trade = await TradeRepo.close_trade(
                session=session,
                trade_id=trade.id,
                user_id=trade.user_id,
                exit_price=actual_exit,
            )

        if closed_trade:
            pnl = closed_trade.realized_pnl or 0.0
            if closed_trade.broker != "paper":
                self._daily_pnl += pnl

            logger.info(
                f"✅ Trade CLOSED | {reason} | "
                f"trade={trade.id} | PnL=₹{pnl:.2f} | "
                f"daily_pnl=₹{self._daily_pnl:.2f} (excluding paper)"
            )

            # Update episodic memory outcome in Qdrant (Phase 2 — new)
            try:
                from app.memory.qdrant_store import update_trade_outcome
                outcome_str = "WIN" if pnl > 0.0 else ("LOSS" if pnl < 0.0 else "NEUTRAL")
                pnl_pct_val = closed_trade.pnl_pct or 0.0
                await update_trade_outcome(
                    run_id=trade.run_id,
                    outcome=outcome_str,
                    pnl_pct=pnl_pct_val,
                )
                logger.info(f"Updated episodic memory outcome in Qdrant for run_id={trade.run_id}")
            except Exception as q_err:
                logger.warning(f"Failed to update episodic memory outcome in Qdrant: {q_err}")

            # Publish to Redis for frontend update
            try:
                await redis_client.publish(
                    CHANNEL_TRADE_EXECUTED,
                    json.dumps({
                        "event":     "TRADE_CLOSED",
                        "reason":    reason,
                        "trade_id":  trade.id,
                        "user_id":   trade.user_id,
                        "symbol":    trade.symbol,
                        "direction": trade.direction,
                        "entry":     trade.entry_price,
                        "exit":      actual_exit,
                        "pnl":       pnl,
                        "pnl_pct":   closed_trade.pnl_pct,
                    }),
                )
            except Exception as pub_err:
                logger.warning(f"Redis publish failed on exit: {pub_err}")

            # --------------------------------------------------------
            # TRIGGER WEIGHT UPDATE  (maybe)
            # --------------------------------------------------------

            try:
                from app.jobs.weight_updater import maybe_update_weights
                await maybe_update_weights(
                    user_id=trade.user_id,
                    symbol=trade.symbol,
                )
            except Exception as wu_err:
                logger.warning(f"Weight update failed (non-fatal): {wu_err}")

            # --------------------------------------------------------
            # CHECK DAILY LOSS CAP
            # --------------------------------------------------------

            await self._check_daily_loss_cap()

    # --------------------------------------------------------
    # DAILY LOSS CAP CHECK
    # --------------------------------------------------------

    async def _check_daily_loss_cap(self) -> None:
        """
        Auto-activate kill switch if daily losses exceed MAX_DAILY_LOSS_PCT.

        This is a critical safety mechanism that prevents catastrophic
        losses during adverse market conditions or model failures.
        """

        if self._daily_pnl >= 0:
            return  # only check on losses

        # We need total equity to calculate loss percentage
        try:
            broker = get_broker()
            account = await broker.get_account()
            total_equity = account.get("total_equity", 0)

            if total_equity <= 0:
                return

            daily_loss_pct = abs(self._daily_pnl / total_equity) * 100

            if daily_loss_pct >= settings.MAX_DAILY_LOSS_PCT:
                from app.services.kill_switch_service import activate_kill_switch

                await activate_kill_switch(
                    duration_seconds=14400,  # 4 hours
                    reason=(
                        f"DAILY LOSS CAP BREACHED: "
                        f"₹{self._daily_pnl:.2f} = {daily_loss_pct:.1f}% of equity "
                        f"(limit: {settings.MAX_DAILY_LOSS_PCT}%)"
                    ),
                )

                logger.critical(
                    f"🛑 DAILY LOSS CAP BREACHED | "
                    f"loss=₹{self._daily_pnl:.2f} ({daily_loss_pct:.1f}%) | "
                    f"limit={settings.MAX_DAILY_LOSS_PCT}% | "
                    f"KILL SWITCH ACTIVATED"
                )



        except Exception as e:
            logger.error(f"Daily loss cap check failed: {e}")


# ============================================================
# GLOBAL INSTANCE
# ============================================================

exit_monitor = ExitMonitor()
