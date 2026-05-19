"""
FutureEdge Runtime

Responsibilities:
-----------------
1. Create a single AsyncPostgresSaver that lives for the
   entire application lifetime (NOT per workflow cycle).
2. Compile the LangGraph graph once against that checkpointer.
3. Expose workflow_graph so every part of the app uses the
   same compiled graph + same live DB connection.

WHY THIS MATTERS FOR HITL:
---------------------------
interrupt() saves checkpoint to PostgreSQL, then ainvoke()
returns early.  If the checkpointer connection is closed
after that ainvoke() call (as happens when you use
`async with` inside run_agent_cycle), the resume API has
no live connection to load the checkpoint from.

The lifespan context manager keeps the connection open from
app startup → app shutdown, solving this completely.
"""

# ============================================================
# IMPORTS
# ============================================================

from contextlib import asynccontextmanager

from loguru import logger

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings


# ============================================================
# MODULE-LEVEL SINGLETONS
# ============================================================

# Populated by lifespan() before the app starts serving
_checkpointer: AsyncPostgresSaver | None = None
workflow_graph = None


# ============================================================
# DATABASE URI
# ============================================================

def _build_db_uri() -> str:
    return (
        f"postgresql://"
        f"{settings.POSTGRES_USER}:"
        f"{settings.POSTGRES_PASSWORD}@"
        f"{settings.POSTGRES_HOST}:"
        f"{settings.POSTGRES_PORT}/"
        f"{settings.POSTGRES_DB}"
    )


# ============================================================
# LIFESPAN CONTEXT MANAGER
# ============================================================

@asynccontextmanager
async def lifespan(app):
    """
    FastAPI lifespan handler.

    Usage in main.py:
    -----------------
    from app.graph.runtime import lifespan
    app = FastAPI(lifespan=lifespan)

    Lifecycle:
    ----------
    startup  → open DB connection, create tables, compile graph
    running  → app serves requests (graph + checkpointer live)
    shutdown → close DB connection cleanly
    """

    global _checkpointer, workflow_graph

    db_uri = _build_db_uri()

    logger.info("🔌 Opening persistent PostgreSQL checkpointer...")

    async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:

        # --------------------------------------------------------
        # CREATE CHECKPOINT TABLES (idempotent)
        # --------------------------------------------------------

        await checkpointer.setup()

        logger.info("✅ Checkpoint tables ready")

        # --------------------------------------------------------
        # COMPILE GRAPH ONCE
        # --------------------------------------------------------

        # Import here to avoid circular imports at module load
        from app.graph.builder import create_graph

        _checkpointer  = checkpointer
        workflow_graph = create_graph().compile(
            checkpointer=checkpointer
        )

        logger.info("🚀 FutureEdge graph compiled and ready")

        # --------------------------------------------------------
        # APP RUNS HERE
        # --------------------------------------------------------

        yield

        # --------------------------------------------------------
        # SHUTDOWN
        # --------------------------------------------------------

        logger.info("🔌 Closing PostgreSQL checkpointer...")

    logger.info("👋 Checkpointer closed cleanly")


# ============================================================
# HELPER: GET GRAPH (safe accessor)
# ============================================================

def get_workflow_graph():
    """
    Returns the compiled workflow graph.

    Raises RuntimeError if called before lifespan() has run
    (i.e. before the app has started).
    """

    if workflow_graph is None:
        raise RuntimeError(
            "workflow_graph is not initialised. "
            "Make sure lifespan() is wired into FastAPI."
        )

    return workflow_graph