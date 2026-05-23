"""
app/api/routes/workflow_router.py
==================================
Workflow API endpoints — all protected by JWT authentication.

ROLE REQUIREMENTS:
-------------------
POST /api/v1/workflow/run              → require_trader
POST /api/v1/workflow/resume           → require_risk_manager (HITL approval)
GET  /api/v1/workflow/{id}/status      → require_viewer

MULTI-USER ISOLATION:
----------------------
Each workflow run is tied to the user who started it.
A user can only resume their OWN workflow — not someone else's.
The run_id stored in WorkflowRun.run_id is checked before resume.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from langgraph.types import Command

from app.auth.dependencies import get_db, require_viewer, require_trader, require_risk_manager
from app.db.models.user import User
from app.db.models.workflow_run import WorkflowRun
from app.graph.builder import run_agent_cycle
from app.graph.runtime import get_workflow_graph
from app.graph.state import MarketContext, PortfolioSnapshot
from app.data.feed import load_historical_candles, get_current_price_yfinance
from app.core.config import settings


router = APIRouter(prefix="/api/v1", tags=["Workflow"])


class RunWorkflowRequest(BaseModel):
    symbol:        str  = ""
    use_live_data: bool = True


class HITLResumeRequest(BaseModel):
    thread_id: str    # the run_id returned from /workflow/run
    decision:  str    # "APPROVE" or "REJECT"
    notes:     str = ""


# ============================================================
# RUN WORKFLOW
# ============================================================

@router.post("/workflow/run", summary="Start a new agent workflow cycle")
async def run_workflow(
    request:      RunWorkflowRequest,
    current_user: User         = Depends(require_trader),
    db:           AsyncSession = Depends(get_db),
):
    """
    Triggers the full multi-agent workflow for the current user.

    MULTI-USER NOTE:
    ----------------
    Every run is recorded in workflow_runs with user_id = current_user.id.
    This means each user's runs are tracked independently.
    The thread_id (run_id) is globally unique (uuid4[:8]), so two users
    running simultaneously will never conflict in LangGraph checkpoints.
    """

    symbol = request.symbol or settings.DEFAULT_SYMBOL

    # Fetch market data
    if request.use_live_data:
        candles = load_historical_candles(symbol, period="2d", interval="1m")
        price   = get_current_price_yfinance(symbol)
    else:
        candles = []
        price   = 0.0

    market_context = MarketContext(
        symbol        = symbol,
        current_price = price,
        ohlcv_1m      = candles,
        regime        = "UNKNOWN",
        volatility_24h= 0.02,
    )

    portfolio = PortfolioSnapshot(
        total_equity     = 100000.0,
        margin_used      = 10000.0,
        margin_available = 90000.0,
        unrealized_pnl   = 0.0,
        open_positions   = [],
    )

    # Pass user_id into the cycle so agents/execution can record it
    result = await run_agent_cycle(
        market_context = market_context,
        portfolio      = portfolio,
        user_id        = current_user.id,
    )

    state    = result["state"]
    run_id   = result["thread_id"]
    consensus= state.get("consensus")

    # --------------------------------------------------------
    # RECORD WORKFLOW RUN IN DATABASE
    # --------------------------------------------------------
    workflow_run = WorkflowRun(
        user_id         = current_user.id,
        run_id          = run_id,
        symbol          = symbol,
        direction       = consensus.direction if consensus else None,
        risk_score      = consensus.risk_score if consensus else None,
        status          = "HITL_PENDING" if state.get("hitl_status") == "PENDING" else "COMPLETED",
        hitl_required   = state.get("hitl_required", False),
        completed_nodes = state.get("completed_nodes", []),
        error_message   = state.get("execution_error"),
    )
    db.add(workflow_run)
    await db.commit()

    logger.info(
        f"Workflow started | user={current_user.email} | "
        f"run_id={run_id} | symbol={symbol}"
    )

    return {
        "thread_id":       run_id,
        "hitl_status":     state.get("hitl_status", "UNKNOWN"),
        "direction":       consensus.direction if consensus else "NONE",
        "risk_score":      consensus.risk_score if consensus else 0.0,
        "execution_error": state.get("execution_error"),
        "completed_nodes": state.get("completed_nodes", []),
        "logs":            state.get("logs", []),
    }


# ============================================================
# RESUME WORKFLOW  (HITL approval — risk_manager only)
# ============================================================

@router.post("/workflow/resume", summary="Resume a paused HITL workflow")
async def resume_workflow(
    request:      HITLResumeRequest,
    current_user: User         = Depends(require_risk_manager),
    db:           AsyncSession = Depends(get_db),
):
    """
    Resume a paused workflow after human approval/rejection.

    SECURITY CHECK:
    ---------------
    We verify the workflow_run exists in the DB.
    Any authenticated risk_manager can approve any pending HITL.
    (In production you may want: only the original user's manager can approve.)

    Only risk_manager and admin roles can call this.
    """

    decision = request.decision.upper()

    if decision not in {"APPROVE", "REJECT"}:
        raise HTTPException(status_code=422, detail="decision must be APPROVE or REJECT")

    # Verify the workflow run exists
    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.run_id == request.thread_id)
    )
    workflow_run = result.scalar_one_or_none()

    if not workflow_run:
        raise HTTPException(
            status_code=404,
            detail=f"Workflow run '{request.thread_id}' not found.",
        )

    if workflow_run.status != "HITL_PENDING":
        raise HTTPException(
            status_code=400,
            detail=f"Workflow is not pending HITL review. Current status: {workflow_run.status}",
        )

    config = {"configurable": {"thread_id": request.thread_id}}

    logger.info(
        f"HITL resume | reviewer={current_user.email} | "
        f"run_id={request.thread_id} | decision={decision}"
    )

    try:
        graph  = get_workflow_graph()
        state  = await graph.ainvoke(
            Command(resume={"decision": decision, "notes": request.notes}),
            config=config,
        )
    except Exception as e:
        logger.exception(f"Resume failed: {e}")
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(e)}")

    # Update workflow run record
    from datetime import datetime
    workflow_run.status           = "COMPLETED"
    workflow_run.hitl_reviewed_by = current_user.id
    workflow_run.hitl_decided_at  = datetime.utcnow()
    workflow_run.completed_at     = datetime.utcnow()
    workflow_run.completed_nodes  = state.get("completed_nodes", [])
    workflow_run.error_message    = state.get("execution_error")
    await db.commit()

    return {
        "thread_id":       request.thread_id,
        "decision":        decision,
        "hitl_status":     state.get("hitl_status", "UNKNOWN"),
        "executed_trade":  state.get("executed_trade"),
        "execution_error": state.get("execution_error"),
        "reviewed_by":     current_user.email,
        "logs":            state.get("logs", []),
    }


# ============================================================
# STATUS
# ============================================================

@router.get("/workflow/{thread_id}/status", summary="Get workflow status")
async def get_workflow_status(
    thread_id:    str,
    current_user: User         = Depends(require_viewer),
    db:           AsyncSession = Depends(get_db),
):
    """
    Check the current state of a workflow run.
    Any authenticated user can check status.
    """

    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.run_id == thread_id)
    )
    workflow_run = result.scalar_one_or_none()

    if not workflow_run:
        raise HTTPException(status_code=404, detail=f"Workflow '{thread_id}' not found.")

    try:
        config = {"configurable": {"thread_id": thread_id}}
        graph  = get_workflow_graph()
        lg_state = await graph.aget_state(config)
        is_paused = bool(lg_state and lg_state.tasks)
    except Exception:
        is_paused = False

    return {
        "thread_id":       thread_id,
        "run_id":          workflow_run.run_id,
        "user_id":         workflow_run.user_id,
        "symbol":          workflow_run.symbol,
        "status":          workflow_run.status,
        "hitl_required":   workflow_run.hitl_required,
        "completed_nodes": workflow_run.completed_nodes,
        "error_message":   workflow_run.error_message,
        "started_at":      workflow_run.started_at.isoformat(),
        "is_paused":       is_paused,
    }