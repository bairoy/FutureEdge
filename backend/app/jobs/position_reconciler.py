"""
app/jobs/position_reconciler.py
===============================
Background position reconciliation engine — continuously compares open positions in the
database against actual broker positions and raises alerts or activates the kill switch on discrepancies.
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from loguru import logger

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.db.redis import redis_client
from app.brokers.base import get_broker


class PositionReconciler:
    """
    Background task that fetches positions from the broker, aggregates
    open trades from the database, and flags discrepancies.
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the reconciliation loop as a background task."""
        if self._running:
            logger.warning("PositionReconciler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._reconcile_loop())
        logger.info(
            f"🟢 PositionReconciler started | "
            f"interval={settings.POSITION_RECONCILE_INTERVAL_SECONDS}s | "
            f"auto_halt={settings.RECONCILE_AUTO_HALT}"
        )

    async def stop(self) -> None:
        """Stop the reconciliation loop cleanly."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🔴 PositionReconciler stopped")

    async def _reconcile_loop(self) -> None:
        """Main loop — runs every POSITION_RECONCILE_INTERVAL_SECONDS."""
        # Initial sleep to let startup finalize
        await asyncio.sleep(5)

        while self._running:
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"PositionReconciler error (non-fatal): {e}")

            await asyncio.sleep(settings.POSITION_RECONCILE_INTERVAL_SECONDS)

    async def reconcile(self) -> None:
        """Perform a single reconciliation check."""
        # Skip reconciliation in paper/mock mode — the mock broker always returns
        # zero positions, which would always mismatche against DB open trades and
        # trigger a false kill switch every cycle.
        if settings.ACTIVE_BROKER.lower() == "mock":
            logger.debug("PositionReconciler: skipping in paper/mock mode")
            return

        broker = get_broker()
        if not await broker.is_connected():
            logger.warning("PositionReconciler: Broker not connected, skipping check")
            return

        # 1. Fetch expected positions from DB
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            from app.db.models.trade import Trade

            result = await session.execute(
                select(Trade).where(
                    Trade.status == "OPEN",
                    Trade.broker == settings.ACTIVE_BROKER.lower(),
                )
            )
            open_trades = result.scalars().all()

        # Aggregate quantities by symbol
        expected_qtys = defaultdict(int)
        for trade in open_trades:
            if trade.direction == "LONG":
                expected_qtys[trade.symbol] += trade.quantity
            elif trade.direction == "SHORT":
                expected_qtys[trade.symbol] -= trade.quantity

        # 2. Fetch actual positions from broker
        try:
            broker_positions = await broker.get_positions()
        except Exception as e:
            logger.error(f"PositionReconciler: failed to fetch broker positions: {e}")
            return

        # Map actual quantities by symbol
        actual_qtys = defaultdict(int)
        for pos in broker_positions:
            actual_qtys[pos["symbol"]] = pos["quantity"]

        # 3. Compare positions
        all_symbols = set(expected_qtys.keys()).union(set(actual_qtys.keys()))
        discrepancies = []

        for symbol in all_symbols:
            expected = expected_qtys[symbol]
            actual = actual_qtys[symbol]

            if expected != actual:
                discrepancy = {
                    "symbol": symbol,
                    "expected": expected,
                    "actual": actual,
                    "difference": actual - expected,
                }
                discrepancies.append(discrepancy)
                logger.warning(
                    f"⚠️ POSITION DISCREPANCY | symbol={symbol} | "
                    f"DB expected={expected} | Broker actual={actual}"
                )

        # 4. Handle discrepancies
        if discrepancies:
            # Publish alert to Redis pub/sub
            try:
                await redis_client.publish(
                    "CHANNEL_ALERTS",
                    json.dumps({
                        "event": "POSITION_DISCREPANCY",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "discrepancies": discrepancies,
                    })
                )
            except Exception as pub_err:
                logger.warning(f"Failed to publish discrepancy alert to Redis: {pub_err}")

            # Auto-halt if enabled
            if settings.RECONCILE_AUTO_HALT:
                from app.services.kill_switch_service import activate_kill_switch
                desc = ", ".join([
                    f"{d['symbol']} (diff: {d['difference']})"
                    for d in discrepancies
                ])
                reason = f"POSITION RECONCILIATION DISCREPANCY: {desc}"
                await activate_kill_switch(
                    duration_seconds=14400,  # 4 hours
                    reason=reason,
                )
                logger.critical(
                    f"🛑 PositionReconciler: KILL SWITCH ACTIVATED | "
                    f"reason={reason}"
                )


# ============================================================
# GLOBAL INSTANCE
# ============================================================

position_reconciler = PositionReconciler()
