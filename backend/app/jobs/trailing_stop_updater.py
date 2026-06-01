"""
app/jobs/trailing_stop_updater.py
====================================
Background engine that dynamically updates stop-loss levels
for winning trades (trailing stop-loss).

WHY TRAILING STOPS ARE CRITICAL FOR PROFIT:
---------------------------------------------
Fixed stop-loss + take-profit is a binary outcome.
A trailing stop "locks in" profits as the trade moves favorably:

Example (RELIANCE LONG):
  Entry:   ₹2,500   SL: ₹2,462.50 (−1.5%)  TP: ₹2,575 (+3%)
  
  Price moves to ₹2,562 (+2.5%):
    Trailing SL kicks in: ₹2,562 − (1.5% × ₹2,562) = ₹2,523.57
    
  Price moves to ₹2,590 (+3.6%):
    Trailing SL updates: ₹2,590 − (1.5% × ₹2,590) = ₹2,551.15

  If price then drops to ₹2,551 → EXIT at ₹2,551 instead of ₹2,462
  Net gain: ₹51 instead of −₹38 (from original SL)

CONFIG (set in .env):
  TRAILING_STOP_TRIGGER_PCT=2.0   # activate trailing when gain >= 2%
  TRAILING_STOP_DISTANCE_PCT=1.5  # trail this far below peak

HOW IT RUNS:
  Started in lifespan() as a background asyncio task.
  Runs every 30 seconds during market hours.
  Updates Trade.stop_loss in PostgreSQL when it improves.
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.brokers.base import get_broker

IST = ZoneInfo("Asia/Kolkata")


# ============================================================
# INTERVAL
# ============================================================

TRAILING_STOP_INTERVAL_SECONDS = 30


# ============================================================
# TRAILING STOP UPDATER CLASS
# ============================================================

class TrailingStopUpdater:
    """
    Background task that evaluates and updates trailing stop-losses
    for all OPEN trades with unrealized gains.
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start trailing stop loop as a background asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"🟢 TrailingStopUpdater started | "
            f"trigger={settings.TRAILING_STOP_TRIGGER_PCT}% | "
            f"distance={settings.TRAILING_STOP_DISTANCE_PCT}%"
        )

    async def stop(self) -> None:
        """Stop the trailing stop loop cleanly."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🔴 TrailingStopUpdater stopped")

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    async def _loop(self) -> None:
        """Run trailing stop evaluation every 30 seconds."""
        while self._running:
            try:
                await self._update_trailing_stops()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TrailingStopUpdater cycle error (non-fatal): {e}")

            await asyncio.sleep(TRAILING_STOP_INTERVAL_SECONDS)

    # --------------------------------------------------------
    # EVALUATE ALL OPEN TRADES
    # --------------------------------------------------------

    async def _update_trailing_stops(self) -> None:
        """
        Query all OPEN trades and update SL if trailing condition is met.
        Only runs during NSE market hours.
        """
        # Only run during market hours
        now = datetime.now(IST)
        if now.weekday() > 4:  # Weekend
            return
        if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30)):
            return

        from sqlalchemy import select
        from app.db.models.trade import Trade

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(Trade.status == "OPEN")
            )
            open_trades = list(result.scalars().all())

        if not open_trades:
            return

        broker = get_broker()
        updated_count = 0

        for trade in open_trades:
            try:
                updated = await self._evaluate_trade(trade, broker)
                if updated:
                    updated_count += 1
            except Exception as e:
                logger.warning(f"TrailingStop: error on trade {trade.id}: {e}")

        if updated_count > 0:
            logger.info(f"TrailingStopUpdater: updated {updated_count} trailing SL(s)")

    # --------------------------------------------------------
    # EVALUATE A SINGLE TRADE
    # --------------------------------------------------------

    async def _evaluate_trade(self, trade, broker) -> bool:
        """
        Check if trailing SL should be activated or moved.

        Returns True if stop_loss was updated in the DB.
        """
        if trade.entry_price is None or trade.entry_price <= 0:
            return False

        # Get current price
        try:
            current_price = await broker.get_ltp(trade.symbol)
        except Exception:
            from app.data.feed import get_latest_tick
            tick = await get_latest_tick(trade.symbol)
            current_price = tick["ltp"] if tick else 0.0

        if not current_price or current_price <= 0:
            return False

        entry = trade.entry_price
        trigger_pct = settings.TRAILING_STOP_TRIGGER_PCT / 100.0
        distance_pct = settings.TRAILING_STOP_DISTANCE_PCT / 100.0

        if trade.direction == "LONG":
            unrealized_gain_pct = (current_price - entry) / entry

            # Only activate trailing when gain >= trigger threshold
            if unrealized_gain_pct < trigger_pct:
                return False

            # New trailing SL = current_price - distance
            new_sl = round(current_price * (1.0 - distance_pct), 2)

            # Only RAISE the stop-loss (never lower it for a LONG)
            if trade.stop_loss is None or new_sl > trade.stop_loss:
                await self._update_sl_in_db(trade.id, new_sl, current_price, unrealized_gain_pct)
                return True

        elif trade.direction == "SHORT":
            unrealized_gain_pct = (entry - current_price) / entry

            if unrealized_gain_pct < trigger_pct:
                return False

            # For SHORT: SL is above current price
            new_sl = round(current_price * (1.0 + distance_pct), 2)

            # Only LOWER the stop-loss (never raise it for a SHORT)
            if trade.stop_loss is None or new_sl < trade.stop_loss:
                await self._update_sl_in_db(trade.id, new_sl, current_price, unrealized_gain_pct)
                return True

        return False

    # --------------------------------------------------------
    # DB UPDATE
    # --------------------------------------------------------

    async def _update_sl_in_db(
        self,
        trade_id: str,
        new_sl: float,
        current_price: float,
        gain_pct: float,
    ) -> None:
        """Write updated stop-loss to PostgreSQL."""
        from sqlalchemy import update
        from app.db.models.trade import Trade

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Trade)
                .where(Trade.id == trade_id)
                .values(stop_loss=new_sl)
            )
            await session.commit()

        logger.info(
            f"🔼 TrailingStop UPDATED | trade={trade_id} | "
            f"price=₹{current_price:.2f} | gain={gain_pct*100:.1f}% | "
            f"new_sl=₹{new_sl:.2f}"
        )


# ============================================================
# GLOBAL INSTANCE
# ============================================================

trailing_stop_updater = TrailingStopUpdater()
