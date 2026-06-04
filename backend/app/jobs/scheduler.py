"""
app/jobs/scheduler.py
======================
APScheduler-based cron job manager for FutureEdge.

REPLACES: ad-hoc asyncio.create_task() calls for recurring work.

WHY APSCHEDULER:
-----------------
- Handles misfires (job skipped while server was down)
- Provides persistent job store (optional)
- Timezone-aware scheduling (IST for all NSE jobs)
- Job locking (prevents double execution)
- Clean lifecycle management

SCHEDULED JOBS:
----------------
| Job                      | Schedule          | Purpose                         |
|--------------------------|-------------------|---------------------------------|
| pre_market_warmup        | 9:00 AM IST M-F   | Load historical candles         |
| instruments_refresh      | 8:00 AM IST M-F   | Refresh instrument master       |
| weight_updater_daily     | 4:00 PM IST M-F   | Recalculate agent weights       |
| daily_pnl_report         | 4:30 PM IST M-F   | Compile daily P&L log           |

USAGE (called from runtime.py lifespan):
    from app.jobs.scheduler import scheduler
    scheduler.start()   # in startup
    scheduler.shutdown()  # in shutdown
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

IST = ZoneInfo("Asia/Kolkata")


# ============================================================
# JOB FUNCTIONS
# ============================================================

async def _instruments_refresh():
    """Refresh Zerodha instrument master CSV into Redis cache."""
    logger.info("⏰ Scheduler: refreshing instrument master...")
    try:
        from app.services.instrument_service import load_instruments
        # Force refresh by calling without cache
        from app.db.redis import redis_client
        await redis_client.delete("futureedge:instruments:NSE")
        await load_instruments("NSE")
        logger.info("✅ Scheduler: instrument master refreshed")
    except Exception as e:
        logger.error(f"Scheduler: instrument refresh failed: {e}")


async def _pre_market_warmup():
    """Pre-load historical candles for all user watchlist symbols."""
    logger.info("⏰ Scheduler: pre-market warmup starting...")
    try:
        from app.data.feed import load_historical_candles
        from app.services.instrument_service import _mock_instruments

        # In production, load from user watchlists in DB
        # For now, warm up the default instruments
        default_symbols = ["NIFTY 50", "BANKNIFTY", "RELIANCE", "INFY", "TCS"]
        for symbol in default_symbols:
            try:
                candles = load_historical_candles(symbol, period="5d", interval="1m")
                logger.info(f"Warmup: loaded {len(candles)} candles for {symbol}")
            except Exception as se:
                logger.warning(f"Warmup failed for {symbol}: {se}")

        logger.info("✅ Scheduler: pre-market warmup complete")
    except Exception as e:
        logger.error(f"Scheduler: pre-market warmup failed: {e}")


async def _weight_updater_daily():
    """Recalculate global agent weights from recent closed trades."""
    logger.info("⏰ Scheduler: running daily weight recalculation...")
    try:
        from app.db.postgres import AsyncSessionLocal
        from app.db.repos.trade_repo import TradeRepo
        from app.jobs.weight_updater import _calculate_new_weights
        from app.db.redis import redis_client
        import json

        async with AsyncSessionLocal() as session:
            # Get last 100 closed trades globally (across all users)
            from sqlalchemy import select
            from app.db.models.trade import Trade
            result = await session.execute(
                select(Trade)
                .where(Trade.status == "CLOSED")
                .order_by(Trade.created_at.desc())
                .limit(100)
            )
            trades = list(result.scalars().all())

        if len(trades) >= 10:
            new_weights = _calculate_new_weights(trades)
            await redis_client.set(
                "futureedge:agent_weights",
                json.dumps(new_weights),
            )
            logger.info(f"✅ Scheduler: agent weights updated: {new_weights}")
        else:
            logger.info(f"Scheduler: only {len(trades)} closed trades — skipping weight update")
    except Exception as e:
        logger.error(f"Scheduler: weight update failed: {e}")


async def _calibration_daily():
    """Run daily agent calibration meta-analysis."""
    logger.info("⏰ Scheduler: running daily agent calibration...")
    try:
        from app.agents.calibration_agent import run_calibration
        result = await run_calibration()
        logger.info(f"✅ Scheduler: daily agent calibration completed: {result}")
    except Exception as e:
        logger.error(f"Scheduler: daily agent calibration failed: {e}")


async def _daily_pnl_report():
    """Compile daily P&L summary and log it."""
    from datetime import date
    today = date.today()
    if today.weekday() > 4:
        return

    logger.info("⏰ Scheduler: generating daily P&L report...")
    try:
        from app.db.postgres import AsyncSessionLocal
        from sqlalchemy import select, func
        from app.db.models.trade import Trade

        async with AsyncSessionLocal() as session:
            # Get today's closed trades
            result = await session.execute(
                select(Trade).where(
                    Trade.status == "CLOSED",
                    func.date(Trade.updated_at) == today,
                )
            )
            trades = list(result.scalars().all())

        if not trades:
            logger.info("Scheduler: no closed trades today — skipping P&L report")
            return

        wins   = [t for t in trades if (t.realized_pnl or 0) > 0]
        losses = [t for t in trades if (t.realized_pnl or 0) <= 0]
        total_pnl = sum(t.realized_pnl or 0 for t in trades)

        logger.info(
            f"📊 DAILY P&L REPORT ({today.strftime('%Y-%m-%d')}):\n"
            f"  - Total Trades: {len(trades)}\n"
            f"  - Wins: {len(wins)} / Losses: {len(losses)}\n"
            f"  - Net P&L: ₹{total_pnl:.2f}"
        )
    except Exception as e:
        logger.error(f"Scheduler: daily P&L report failed: {e}")


# ============================================================
# SCHEDULER SETUP
# ============================================================

def build_scheduler() -> AsyncIOScheduler:
    """
    Build and configure the APScheduler instance with all jobs.

    All times are in Asia/Kolkata (IST) timezone.
    Cron expressions: minute hour day month day_of_week
    """
    scheduler = AsyncIOScheduler(timezone=IST)

    # 8:00 AM IST Mon-Fri — refresh instrument master
    scheduler.add_job(
        _instruments_refresh,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=IST),
        id="instruments_refresh",
        name="Refresh Zerodha Instrument Master",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 9:00 AM IST Mon-Fri — pre-market warmup
    scheduler.add_job(
        _pre_market_warmup,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=IST),
        id="pre_market_warmup",
        name="Pre-Market Data Warmup",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 4:00 PM IST Mon-Fri — agent weight recalculation
    scheduler.add_job(
        _weight_updater_daily,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=IST),
        id="weight_updater_daily",
        name="Daily Agent Weight Recalculation",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # 4:15 PM IST Mon-Fri — daily agent calibration
    scheduler.add_job(
        _calibration_daily,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=IST),
        id="calibration_daily",
        name="Daily Agent Calibration Analysis",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # 4:30 PM IST Mon-Fri — daily P&L report
    scheduler.add_job(
        _daily_pnl_report,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=IST),
        id="daily_pnl_report",
        name="Daily P&L Report",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    logger.info(f"⏰ Scheduler configured with {len(scheduler.get_jobs())} jobs")
    return scheduler


# ============================================================
# GLOBAL SCHEDULER INSTANCE
# ============================================================

scheduler = build_scheduler()
