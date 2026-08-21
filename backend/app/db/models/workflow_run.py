"""
app/db/models/workflow_run.py
==============================
WorkflowRun table — one row per agent cycle triggered by a user.

WHY THIS TABLE EXISTS:
-----------------------
Without this table, you have no way to answer:
  - "Which user started this workflow?"
  - "How many cycles has this user run today?"
  - "What was the final decision for run_id=abc123?"
  - "Can this user resume this workflow?" (security check)

RELATIONSHIP TO LANGGRAPH CHECKPOINTS:
----------------------------------------
LangGraph stores checkpoints in its own internal tables
(langgraph_checkpoints etc.) keyed by thread_id.

We store the thread_id here so we can:
  1. Look up "which user owns this thread_id" before allowing resume
  2. Show the user their own workflow history
  3. Clean up old checkpoints when a workflow is complete

THREAD ID FORMAT:
-----------------
thread_id = run_id   (e.g. "a1b2c3d4")

This is stored in BOTH:
  - workflow_runs.run_id  (this table, for our queries)
  - LangGraph checkpoint tables (internal, for resume)

MULTI-USER CHECKPOINTS:
-------------------------
Because every workflow gets a unique run_id (uuid4[:8]),
two users running simultaneously will NEVER have the same
thread_id, so their checkpoints are completely isolated.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, JSON, Text, ForeignKey,Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class WorkflowRun(Base):

    __tablename__ = "workflow_runs"

    # --------------------------------------------------------
    # IDENTITY
    # --------------------------------------------------------

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Which user triggered this workflow
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to users.id — who started this workflow",
    )

    # The short 8-char ID used as LangGraph thread_id
    # This is the key for checkpoint lookup and resume
    run_id: Mapped[str] = mapped_column(
        String(8),
        unique=True,
        index=True,
        nullable=False,
        comment="LangGraph thread_id — used for checkpoint lookup and resume",
    )

    # --------------------------------------------------------
    # WORKFLOW DETAILS
    # --------------------------------------------------------

    # The symbol being traded in this run
    symbol: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # Final orchestrator decision: LONG | SHORT | NONE
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Risk score from orchestrator (0.0 to 1.0)
    risk_score: Mapped[float | None] = mapped_column(
        # stored as nullable — not set until orchestrator runs
        nullable=True,
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    # RUNNING | HITL_PENDING | HITL_RESOLVING | COMPLETED | FAILED | REJECTED
    #
    # HITL_RESOLVING is a short-lived claim state. `resume_workflow` flips
    # HITL_PENDING → HITL_RESOLVING with a conditional UPDATE before running the
    # graph, so exactly one concurrent request can execute a given trade. See
    # the "atomic claim" section of workflow_router.py::resume_workflow.
    status: Mapped[str] = mapped_column(
        String(20),
        default="RUNNING",
        index=True,
        comment="RUNNING | HITL_PENDING | HITL_RESOLVING | COMPLETED | FAILED | REJECTED",
    )

    # --------------------------------------------------------
    # HITL TRACKING
    # --------------------------------------------------------

    # Was human approval required for this run?
    hitl_required: Mapped[bool] = mapped_column(Boolean, default=False)

    # Who reviewed it (user_id of the risk_manager)
    hitl_reviewed_by: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        comment="user_id of the risk_manager who approved/rejected HITL",
    )

    # When was the HITL decision made
    hitl_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --------------------------------------------------------
    # NODES COMPLETED  (list of agent names that finished)
    # --------------------------------------------------------

    # Stored as JSON list: ["signal_agent", "sentiment_agent", ...]
    completed_nodes: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    # If anything failed, the error message is stored here
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --------------------------------------------------------
    # TIMESTAMPS
    # --------------------------------------------------------

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    # Many workflow runs → one user
    user = relationship("User", back_populates="workflow_runs")

    # One workflow run → many trades
    # (usually 0 or 1 trade per run, but modelled as one-to-many)
    trades = relationship(
        "Trade",
        back_populates="workflow_run",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        return (
            f"WorkflowRun(run_id={self.run_id!r}, "
            f"user_id={self.user_id!r}, status={self.status!r})"
        )