"""
HITL Resume API

Responsibilities:
-----------------
1. Receive human decision from frontend / risk desk
2. Load the interrupted workflow by thread_id
3. Inject the decision via Command(resume=...)
4. Let LangGraph continue execution from interrupt() onward

HOW RESUME WORKS:
-----------------
When interrupt() fires in human_review_node:
  - LangGraph raises GraphInterrupt
  - The runner saves a checkpoint keyed by thread_id
  - ainvoke() returns early (the workflow is paused)

When this endpoint is called:
  - We call graph.ainvoke(Command(resume=payload), config)
  - LangGraph loads the checkpoint for that thread_id
  - Execution resumes exactly where interrupt() was called
  - interrupt() returns the payload we pass here
  - human_review_node continues with human_response = payload
"""

# ============================================================
# IMPORTS
# ============================================================

from fastapi   import APIRouter, HTTPException
from pydantic  import BaseModel
from loguru    import logger

# LangGraph resume command
from langgraph.types import Command

# Singleton compiled graph
from app.graph.runtime import get_workflow_graph


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(prefix="/api/v1", tags=["HITL"])


# ============================================================
# REQUEST / RESPONSE MODELS
# ============================================================

class HITLDecisionRequest(BaseModel):

    # Workflow thread identifier returned by run_agent_cycle()
    thread_id: str

    # Human decision — must be "APPROVE" or "REJECT"
    decision: str

    # Optional reviewer notes / justification
    notes: str = ""


class HITLDecisionResponse(BaseModel):

    thread_id: str
    decision:  str
    hitl_status: str
    message:   str


# ============================================================
# RESUME ENDPOINT
# ============================================================

@router.post(
    "/workflow/resume",
    response_model=HITLDecisionResponse,
    summary="Resume a paused HITL workflow",
)
async def resume_workflow(request: HITLDecisionRequest):
    """
    Resume an interrupted LangGraph workflow.

    Request body:
    -------------
    {
        "thread_id": "a1b2c3d4",
        "decision":  "APPROVE",
        "notes":     "Risk looks acceptable"
    }

    The thread_id is the run_id returned by the initial
    run_agent_cycle() call.  It maps 1-to-1 with the
    LangGraph thread_id stored in the checkpoint.

    Response:
    ---------
    {
        "thread_id":   "a1b2c3d4",
        "decision":    "APPROVE",
        "hitl_status": "APPROVED",
        "message":     "Workflow resumed successfully"
    }
    """

    # --------------------------------------------------------
    # VALIDATE DECISION
    # --------------------------------------------------------

    decision = request.decision.upper()

    if decision not in {"APPROVE", "REJECT"}:
        raise HTTPException(
            status_code=422,
            detail=f"decision must be APPROVE or REJECT, got '{request.decision}'"
        )

    # --------------------------------------------------------
    # THREAD CONFIG
    # --------------------------------------------------------

    # CRITICAL: thread_id must match the one used in the
    # original ainvoke() call.  LangGraph uses it to locate
    # the saved checkpoint in PostgreSQL.

    config = {
        "configurable": {
            "thread_id": request.thread_id
        }
    }

    # --------------------------------------------------------
    # RESUME EXECUTION
    # --------------------------------------------------------

    # Command(resume=...) injects the payload back into
    # interrupt() inside human_review_node.
    # The workflow then continues normally.

    logger.info(
        f"▶️  Resuming workflow | "
        f"thread_id={request.thread_id} | "
        f"decision={decision}"
    )

    try:
        graph = get_workflow_graph()

        result = await graph.ainvoke(
            Command(
                resume={
                    "decision": decision,
                    "notes":    request.notes,
                }
            ),
            config=config,
        )

    except Exception as e:
        logger.exception(
            f"Failed to resume workflow {request.thread_id}: {e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resume workflow: {str(e)}"
        )

    # --------------------------------------------------------
    # RETURN RESULT
    # --------------------------------------------------------

    hitl_status = result.get("hitl_status", "UNKNOWN")

    logger.info(
        f"🏁 Resume complete | "
        f"thread_id={request.thread_id} | "
        f"hitl_status={hitl_status}"
    )

    return HITLDecisionResponse(
        thread_id=request.thread_id,
        decision=decision,
        hitl_status=hitl_status,
        message="Workflow resumed successfully",
    )


# ============================================================
# STATUS ENDPOINT  (optional — useful for polling)
# ============================================================

@router.get(
    "/workflow/{thread_id}/status",
    summary="Check the current state of a workflow",
)
async def get_workflow_status(thread_id: str):
    """
    Return the current persisted state of a workflow thread.

    Useful for the frontend to poll whether the workflow is:
    - still running
    - paused waiting for HITL
    - completed
    """

    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph = get_workflow_graph()
        state = await graph.aget_state(config)

    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Workflow {thread_id} not found: {str(e)}"
        )

    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No state found for thread_id={thread_id}"
        )

    values = state.values

    return {
        "thread_id":   thread_id,
        "hitl_status": values.get("hitl_status", "UNKNOWN"),
        "run_id":      values.get("run_id"),
        "timestamp":   values.get("timestamp"),
        "completed_nodes": values.get("completed_nodes", []),
        "execution_error": values.get("execution_error"),
        "interrupted":     bool(state.tasks),   # tasks present = paused
    }