"""
app/db/models/trade.py
=======================
Trade table — every executed or rejected trade stored permanently.

RELATIONSHIP TO USER:
----------------------
Every trade now has a user_id foreign key.
This means:
  - "show me MY trades" → WHERE trades.user_id = current_user.id
  - The risk agent's Kelly calculation uses only YOUR trade history
  - Each user's PnL is tracked independently

RELATIONSHIP TO WORKFLOW RUN:
-------------------------------
Every trade also links to the workflow_run that created it.
This lets you trace: which agent cycle → which trade → which outcome.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Boolean, DateTime, JSON, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Trade(Base):

    __tablename__ = "trades"

    # --------------------------------------------------------
    # IDENTITY
    # --------------------------------------------------------

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Which user triggered this trade
    # ON DELETE CASCADE: if user is deleted, their trades are deleted too
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to users.id — which user owns this trade",
    )

    # The 8-char LangGraph workflow run ID
    run_id: Mapped[str] = mapped_column(
        String(8),
        index=True,
        comment="LangGraph run_id that created this trade",
    )

    # Optional FK to workflow_runs table for full traceability
    workflow_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to workflow_runs.id",
    )

    # --------------------------------------------------------
    # TRADE DETAILS
    # --------------------------------------------------------

    symbol:      Mapped[str]   = mapped_column(String(30))
    direction:   Mapped[str]   = mapped_column(String(10))  # LONG | SHORT | NONE
    size:        Mapped[float] = mapped_column(Float)        # position size in Rupees
    quantity:    Mapped[int]   = mapped_column(Integer, default=0,
                                               comment="Number of shares/contracts traded")
    entry_price: Mapped[float] = mapped_column(Float)

    stop_loss:   Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --------------------------------------------------------
    # RISK
    # --------------------------------------------------------

    risk_score: Mapped[float] = mapped_column(Float)

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    # OPEN | CLOSED | REJECTED | FAILED
    status: Mapped[str] = mapped_column(String(20), default="OPEN")

    # --------------------------------------------------------
    # HITL
    # --------------------------------------------------------

    hitl_required:  Mapped[bool]       = mapped_column(Boolean, default=False)
    human_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    human_notes:    Mapped[str | None]  = mapped_column(Text,    nullable=True)

    # Who approved/rejected (their user_id)
    reviewed_by: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        comment="user_id of the risk_manager who reviewed HITL",
    )

    # --------------------------------------------------------
    # AGENT VOTES  (full JSON audit trail)
    # --------------------------------------------------------

    agent_consensus: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # --------------------------------------------------------
    # BROKER EXECUTION
    # --------------------------------------------------------

    broker:            Mapped[str | None]   = mapped_column(String(20),  nullable=True)
    broker_order_id:   Mapped[str | None]   = mapped_column(String(100), nullable=True)
    actual_fill_price: Mapped[float | None] = mapped_column(Float,       nullable=True)
    slippage:          Mapped[float | None] = mapped_column(Float,       nullable=True)

    # --------------------------------------------------------
    # PnL
    # --------------------------------------------------------

    exit_price:   Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct:      Mapped[float | None] = mapped_column(Float, nullable=True)

    # --------------------------------------------------------
    # TIMESTAMPS
    # --------------------------------------------------------

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    # Many trades → one user
    user = relationship("User", back_populates="trades")

    # Many trades → one workflow run
    workflow_run = relationship("WorkflowRun", back_populates="trades")

    def __repr__(self) -> str:
        return (
            f"Trade(id={self.id!r}, symbol={self.symbol!r}, "
            f"direction={self.direction!r}, user_id={self.user_id!r})"
        )