"""
app/graph/runtime.py
=====================
Application startup/shutdown lifecycle + compiled graph singleton.

WHAT THIS FILE DOES:
---------------------
On startup (before the app serves any requests):
  1. Open PostgreSQL connection for LangGraph checkpoints
  2. Create checkpoint tables if they don't exist
  3. Compile the LangGraph graph once (with checkpointer attached)
  4. Load historical NSE candles via yfinance (free, no API key)
  5. Start KiteTicker WebSocket for live tick streaming (if market open)
  6. Connect to the broker (mock or Zerodha)

While running:
  - workflow_graph is available to all API endpoints
  - Live ticks flow: KiteTicker -> Redis Stream -> agents

On shutdown:
  - Stop KiteTicker WebSocket cleanly
  - Close PostgreSQL connection
  - Disconnect from broker

WHY THE CHECKPOINTER MUST STAY ALIVE:
---------------------------------------
When interrupt() fires (HITL), LangGraph saves the workflow
state to PostgreSQL and ainvoke() returns early.
Later, when the resume API is called, LangGraph must reload
that checkpoint from PostgreSQL to continue the workflow.

If the PostgreSQL connection closes between the interrupt and
the resume call, the workflow cannot be resumed.

The lifespan() context manager solves this by keeping the
connection open for the entire application lifetime.
"""

from contextlib import asynccontextmanager
from loguru import logger
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.core.config import settings


# ============================================================
# MODULE-LEVEL SINGLETONS
# ============================================================

_checkpointer: AsyncPostgresSaver | None = None
workflow_graph = None


def _build_db_uri() -> str:
    """
    PostgreSQL URI for LangGraph checkpointer.
    Note: LangGraph uses plain postgresql:// not postgresql+asyncpg://
    """
    return (
        f"postgresql://"
        f"{settings.POSTGRES_USER}:"
        f"{settings.POSTGRES_PASSWORD}@"
        f"{settings.POSTGRES_HOST}:"
        f"{settings.POSTGRES_PORT}/"
        f"{settings.POSTGRES_DB}"
    )


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app):
    """
    FastAPI lifespan context manager.

    Wire into FastAPI like this in main.py:
        app = FastAPI(lifespan=lifespan)

    Everything before yield runs at STARTUP.
    Everything after  yield runs at SHUTDOWN.
    """

    global _checkpointer, workflow_graph

    # --------------------------------------------------------
    # STEP 1: POSTGRESQL CHECKPOINTER
    # --------------------------------------------------------

    logger.info("Connecting to PostgreSQL checkpointer...")

    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    serde = JsonPlusSerializer(
        pickle_fallback=True,
        allowed_msgpack_modules=[
            ("app.graph.state", "MarketContext"),
            ("app.graph.state", "PortfolioSnapshot"),
            ("app.graph.state", "AgentVote"),
            ("app.graph.state", "TradeProposal"),
        ]
    )
    async with AsyncPostgresSaver.from_conn_string(_build_db_uri(), serde=serde) as checkpointer:

        await checkpointer.setup()
        logger.info("Checkpoint tables ready")

        # --------------------------------------------------------
        # STEP 1b: RESTORE KILL-SWITCH STATE
        # --------------------------------------------------------
        # Runs before ANY trading machinery starts. Redis is a cache; if it was
        # restarted while trading was halted the flag is gone, and without this
        # the system would come back up happily placing orders. Postgres holds
        # the durable state — replay it now, and fail closed if it can't be read.

        from app.services.kill_switch_service import reconcile_kill_switch_from_db

        await reconcile_kill_switch_from_db()

        # --------------------------------------------------------
        # STEP 2: COMPILE GRAPH
        # --------------------------------------------------------

        from app.graph.builder import create_graph

        _checkpointer  = checkpointer
        workflow_graph = create_graph().compile(checkpointer=checkpointer)

        logger.info("LangGraph workflow compiled")

        # --------------------------------------------------------
        # STEP 3: LOAD HISTORICAL CANDLES (yfinance - free)
        # --------------------------------------------------------
        # Gives agents enough history for RSI/MACD/Bollinger
        # from the very first cycle without waiting for live ticks.

        try:
            from app.data.feed import load_historical_candles
            from app.db.redis import redis_client, STREAM_TICKS

            symbol  = settings.DEFAULT_SYMBOL
            candles = load_historical_candles(symbol, period="5d", interval="1m")

            if candles:
                for c in candles[-100:]:
                    await redis_client.xadd(
                        STREAM_TICKS,
                        {
                            "token":     str(settings.DEFAULT_INSTRUMENT_TOKEN),
                            "ltp":       str(c["close"]),
                            "open":      str(c["open"]),
                            "high":      str(c["high"]),
                            "low":       str(c["low"]),
                            "close":     str(c["close"]),
                            "volume":    str(c["volume"]),
                            "timestamp": c["timestamp"],
                            "source":    "yfinance_historical",
                        },
                        maxlen=1000,
                        approximate=True,
                    )

                logger.info(
                    f"Seeded Redis Stream with {min(len(candles), 100)} "
                    f"historical candles for {symbol}"
                )

        except Exception as e:
            logger.warning(f"Historical candle load failed (non-fatal): {e}")

        # --------------------------------------------------------
        # STEP 4: START LIVE FEED
        # --------------------------------------------------------

        from app.data.feed import tick_publisher

        if settings.ACTIVE_FEED == "zerodha":
            tick_publisher.start()
            logger.info("KiteTicker live feed started")
        else:
            logger.info("Mock feed mode - KiteTicker not started")

        # --------------------------------------------------------
        # STEP 5: CONNECT BROKER
        # --------------------------------------------------------

        from app.brokers.base import get_broker

        broker = get_broker()
        connected = await broker.connect()

        if connected:
            logger.info(f"Broker connected: {settings.ACTIVE_BROKER}")
        else:
            logger.warning(f"Broker connection failed: {settings.ACTIVE_BROKER}")

        # --------------------------------------------------------
        # STEP 6: INITIALISE QDRANT COLLECTION (Phase 2 — new)
        # --------------------------------------------------------
        try:
            from app.memory.qdrant_store import init_collection
            import asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, init_collection)
            logger.info("Qdrant collection check/creation complete")
        except Exception as q_err:
            logger.error(f"Failed to initialise Qdrant collection: {q_err}")

        # Investing mode's document corpus (annual reports, concall transcripts).
        # A SEPARATE collection from the one above: that holds 12-dimensional
        # market vectors, this holds text embeddings of a different size, and
        # Qdrant fixes vector size per collection.
        #
        # Failure is logged and tolerated for the same reason as above — the
        # trading path must still start without it. Investing-mode retrieval
        # will report itself unavailable rather than the app refusing to boot.
        try:
            from app.memory.document_store import init_document_collection
            await loop.run_in_executor(None, init_document_collection)
            logger.info("Qdrant document collection check/creation complete")
        except Exception as d_err:
            logger.error(f"Failed to initialise Qdrant document collection: {d_err}")

        # --------------------------------------------------------
        # STEP 7: START EXIT MONITORING ENGINE (Phase 2 — new)
        # --------------------------------------------------------
        from app.jobs.exit_monitor import exit_monitor
        await exit_monitor.start()

        # --------------------------------------------------------
        # STEP 8: START POSITION RECONCILER
        # --------------------------------------------------------
        from app.jobs.position_reconciler import position_reconciler
        await position_reconciler.start()

        # --------------------------------------------------------
        # STEP 9: START TRAILING STOP UPDATER  (new)
        # Dynamically moves stop-loss levels for winning trades.
        # --------------------------------------------------------
        from app.jobs.trailing_stop_updater import trailing_stop_updater
        await trailing_stop_updater.start()

        # --------------------------------------------------------
        # STEP 10: START APSCHEDULER CRON JOBS  (new)
        # Handles: pre-market warmup, weight updates, daily P&L logging.
        # --------------------------------------------------------
        from app.jobs.scheduler import scheduler
        scheduler.start()
        logger.info(f"APScheduler started with {len(scheduler.get_jobs())} jobs")

        # --------------------------------------------------------
        # STEP 11: PRELOAD INSTRUMENT MASTER  (new)
        # Cache Zerodha's instrument list for symbol search.
        # --------------------------------------------------------
        try:
            from app.services.instrument_service import load_instruments
            instruments = await load_instruments("NSE")
            logger.info(f"Instrument master loaded: {len(instruments)} NSE instruments")
        except Exception as inst_err:
            logger.warning(f"Instrument preload failed (non-fatal): {inst_err}")

        # --------------------------------------------------------
        # APP RUNS HERE
        # --------------------------------------------------------

        logger.info("FutureEdge is ready")

        yield

        # --------------------------------------------------------
        # SHUTDOWN — each step guarded so one failure doesn't abort the rest
        # --------------------------------------------------------

        logger.info("Shutting down FutureEdge...")

        for label, coro_or_fn in [
            ("position_reconciler", lambda: position_reconciler.stop()),
            ("trailing_stop_updater", lambda: trailing_stop_updater.stop()),
            ("exit_monitor", lambda: exit_monitor.stop()),
        ]:
            try:
                result = coro_or_fn()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                logger.warning(f"Shutdown step '{label}' raised: {exc} — continuing.")

        try:
            scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning(f"APScheduler shutdown raised: {exc} — continuing.")

        try:
            tick_publisher.stop()
        except Exception as exc:
            logger.warning(f"TickPublisher stop raised: {exc} — continuing.")

        try:
            await broker.disconnect()
        except Exception as exc:
            logger.warning(f"Broker disconnect raised: {exc} — continuing.")

        logger.info("Shutdown complete")


    logger.info("PostgreSQL checkpointer closed")


# ============================================================
# SAFE ACCESSOR
# ============================================================

def get_workflow_graph():
    """
    Returns the compiled LangGraph workflow.
    Raises RuntimeError if called before startup.
    """

    if workflow_graph is None:
        raise RuntimeError(
            "workflow_graph is not initialised. "
            "Make sure lifespan() is wired into FastAPI in main.py."
        )

    return workflow_graph