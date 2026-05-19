"""
Human-in-the-Loop (HITL) Node

Responsibilities:
-----------------
1. Pause high-risk workflows via LangGraph interrupt()
2. Persist checkpoint to PostgreSQL
3. Wait for external human decision (approve / reject)
4. Resume workflow safely with the human's response

KEY FIX vs original code:
--------------------------
The original `except Exception` block swallowed the
GraphInterrupt exception that interrupt() raises internally.
LangGraph uses this exception as a control-flow signal —
catching and suppressing it broke the entire pause mechanism.

Fix: explicitly catch GraphInterrupt and re-raise it before
the generic Exception handler runs.
"""

# ============================================================
# IMPORTS
# ============================================================

from loguru import logger

# LangGraph native interrupt mechanism
from langgraph.types  import interrupt
from langgraph.errors import GraphInterrupt   # ← must re-raise this

# Shared state + schemas
from app.graph.state import AgentState, TradeProposal


# ============================================================
# HUMAN REVIEW ROUTING
# ============================================================

def should_human_review(state: AgentState) -> str:
    """
    Conditional edge function called by the orchestrator node.

    Returns:
    --------
    "human_review"  → pause workflow, request approval
    "execute"       → continue directly to execution
    """

    # --------------------------------------------------------
    # ORCHESTRATOR REQUESTED HITL
    # --------------------------------------------------------

    # Orchestrator sets this flag when it detects:
    # - high risk score
    # - large position size
    # - low agent confidence
    # - agent disagreement

    if state.get("hitl_required", False):
        return "human_review"

    # --------------------------------------------------------
    # RISK AGENT REQUESTED MODIFICATION
    # --------------------------------------------------------

    risk_vote = state.get("risk_vote")

    if risk_vote and risk_vote.decision == "MODIFY":
        return "human_review"

    # --------------------------------------------------------
    # DEFAULT → DIRECT EXECUTION
    # --------------------------------------------------------

    return "execute"


# ============================================================
# HUMAN REVIEW NODE
# ============================================================

def human_review_node(state: AgentState) -> dict:
    """
    REAL interrupt-based HITL node.

    Execution flow:
    ---------------
    1. Extract trade proposal from state
    2. Call interrupt() with review payload
       → LangGraph raises GraphInterrupt internally
       → Checkpoint is saved to PostgreSQL
       → ainvoke() returns early to caller
    3. Workflow is paused until the resume API sends:
       graph.ainvoke(Command(resume={...}), config=config)
    4. interrupt() returns the human_response dict
    5. Node applies APPROVE or REJECT and returns state update

    CRITICAL:
    ---------
    Never catch GraphInterrupt here.
    It is LangGraph's internal control-flow signal —
    swallowing it silently disables the pause mechanism.
    """

    proposal: TradeProposal = state["consensus"]
    run_id: str             = state["run_id"]

    # --------------------------------------------------------
    # LOG INTERRUPT REQUEST
    # --------------------------------------------------------

    logger.warning(f"⏸️  HITL INTERRUPT    | run_id={run_id}")
    logger.warning(f"    Symbol    = {proposal.symbol}")
    logger.warning(f"    Direction = {proposal.direction}")
    logger.warning(f"    Size      = ${proposal.size:.2f}")
    logger.warning(f"    Risk      = {proposal.risk_score:.2f}")
    logger.warning("    Waiting for human review...")

    # --------------------------------------------------------
    # INTERRUPT — WORKFLOW PAUSES HERE
    # --------------------------------------------------------

    # NOTE: do NOT wrap interrupt() in try/except Exception.
    # GraphInterrupt must propagate up to LangGraph's runner.
    #
    # The dict passed to interrupt() is available to the
    # frontend / API so it can display review information.

    try:
        human_response = interrupt(
            {
                "type":    "human_review",
                "run_id":  run_id,

                # Trade details for the reviewer
                "symbol":      proposal.symbol,
                "direction":   proposal.direction,
                "size":        proposal.size,
                "entry_price": proposal.entry_price,
                "risk_score":  proposal.risk_score,

                # Per-agent reasoning
                "agent_votes": [
                    {
                        "agent":      vote.agent,
                        "decision":   vote.decision,
                        "confidence": vote.confidence,
                        "reasoning":  vote.reasoning,
                    }
                    for vote in proposal.agent_consensus
                ],

                "message": "Approve or reject this trade",
            }
        )

    except GraphInterrupt:
        # ← ALWAYS re-raise.  This is not an error — it is
        #   LangGraph pausing execution to save the checkpoint.
        raise

    except Exception as e:
        # Any other exception during the interrupt() call
        # (e.g. serialisation error) → reject trade safely.
        logger.exception(f"HITL setup failure: {e}")
        return _reject(proposal, run_id, reason=str(e))

    # --------------------------------------------------------
    # WORKFLOW RESUMES HERE
    # --------------------------------------------------------
    # Execution only reaches this line after the resume API
    # calls graph.ainvoke(Command(resume={...}), config=...).
    # human_response contains whatever was passed to resume=.

    logger.info(f"▶️  HITL RESUMED      | run_id={run_id}")

    decision: str = human_response.get("decision", "REJECT").upper()
    notes:    str = human_response.get("notes", "")

    # --------------------------------------------------------
    # APPLY DECISION
    # --------------------------------------------------------

    if decision == "APPROVE":
        return _approve(proposal, run_id, notes)

    return _reject(proposal, run_id, reason=notes or "Rejected by human")


# ============================================================
# HELPERS
# ============================================================

def _approve(
    proposal: TradeProposal,
    run_id:   str,
    notes:    str,
) -> dict:

    proposal.human_approved = True
    proposal.human_notes    = notes

    logger.info(f"✅ HUMAN APPROVED     | run_id={run_id}")

    return {
        "consensus":       proposal,
        "hitl_status":     "APPROVED",
        "completed_nodes": ["human_review"],
        "logs": [
            f"HITL approved trade for {proposal.symbol}"
        ],
    }


def _reject(
    proposal: TradeProposal,
    run_id:   str,
    reason:   str,
) -> dict:

    proposal.human_approved = False
    proposal.human_notes    = reason

    logger.warning(f"❌ HUMAN REJECTED    | run_id={run_id} | reason={reason}")

    return {
        "consensus":       proposal,
        "hitl_status":     "REJECTED",
        "execution_error": "TRADE_REJECTED_BY_HUMAN",
        "completed_nodes": ["human_review"],
        "logs": [
            f"HITL rejected trade for {proposal.symbol}: {reason}"
        ],
    }