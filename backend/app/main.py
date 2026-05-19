"""
FutureEdge — FastAPI Application Entry Point

Key wiring:
-----------
1. lifespan=lifespan  →  opens the PostgreSQL checkpointer
                          connection ONCE at startup and keeps
                          it alive until shutdown.

2. hitl_router        →  /api/v1/workflow/resume endpoint so
                          the frontend can approve/reject trades.

3. /workflow/run      →  triggers a new agent cycle and returns
                          the thread_id so the caller can track
                          or resume the workflow later.
"""

# ============================================================
# IMPORTS
# ============================================================

from fastapi import FastAPI
from loguru  import logger

# Lifespan: opens DB connection + compiles graph at startup
from app.graph.runtime import lifespan

# HITL API router
from app.api.routes.hitl_router import router as hitl_router

# Workflow entry point
from app.graph.builder import run_agent_cycle

# Schemas for the run endpoint
from app.graph.state import MarketContext, PortfolioSnapshot


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="FutureEdge Trading Agent",
    version="1.0.0",
    lifespan=lifespan,          # ← keeps checkpointer alive
)


# ============================================================
# ROUTERS
# ============================================================

app.include_router(hitl_router)


# ============================================================
# WORKFLOW RUN ENDPOINT
# ============================================================

@app.post(
    "/api/v1/workflow/run",
    summary="Start a new agent workflow cycle",
)
async def run_workflow(
    market_context: MarketContext,
    portfolio:      PortfolioSnapshot,
):
    """
    Trigger a full multi-agent trading cycle.

    Returns:
    --------
    {
        "thread_id": "a1b2c3d4",   ← save this!
        "hitl_status": "PENDING",  ← PENDING means paused for review
        "state": { ... }
    }

    If hitl_status == "PENDING", the workflow is paused.
    Call POST /api/v1/workflow/resume with the thread_id to
    approve or reject the trade.

    If hitl_status == "NOT_REQUIRED" or "APPROVED", the trade
    was or is being executed automatically.
    """

    result = await run_agent_cycle(
        market_context=market_context,
        portfolio=portfolio,
    )

    return {
        "thread_id":   result["thread_id"],
        "hitl_status": result["state"].get("hitl_status", "UNKNOWN"),
        "state":       result["state"],
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok"}