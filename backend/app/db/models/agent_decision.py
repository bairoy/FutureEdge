
"""
Agent Decision Model

Stores explainability + audit trail.
"""

# ============================================================
# IMPORTS
# ============================================================

import uuid

from datetime import datetime

from sqlalchemy import (
    String,
    Float,
    JSON,
    DateTime,
    ForeignKey
)

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship
)

from app.db.base import Base


# ============================================================
# AGENT DECISION MODEL
# ============================================================

class AgentDecision(Base):

    __tablename__ = "agent_decisions"


    # ========================================================
    # PRIMARY KEY
    # ========================================================

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )


    # ========================================================
    # RELATIONSHIPS
    # ========================================================

    trade_id: Mapped[str] = mapped_column(
        ForeignKey("trades.id")
    )


    # ========================================================
    # AGENT OUTPUT
    # ========================================================

    agent_name: Mapped[str] = (
        mapped_column(String)
    )

    decision: Mapped[str] = (
        mapped_column(String)
    )

    confidence: Mapped[float] = (
        mapped_column(Float)
    )

    reasoning: Mapped[str] = (
        mapped_column(String)
    )

    decision_metadata: Mapped[dict] = (
        mapped_column(JSON)
    )


    # ========================================================
    # TIMESTAMP
    # ========================================================

    created_at: Mapped[datetime] = (
        mapped_column(
            DateTime,
            default=datetime.utcnow
        )
    )


    # ========================================================
    # RELATIONSHIPS
    # ========================================================

    trade = relationship(
        "Trade",
        back_populates="agent_decisions"
    )