

"""
Trade Model

Stores executed trades.
"""

# ============================================================
# IMPORTS
# ============================================================

import uuid

from datetime import datetime

from sqlalchemy import (
    String,
    Float,
    Boolean,
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
# TRADE MODEL
# ============================================================

class Trade(Base):

    __tablename__ = "trades"


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

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id")
    )

    portfolio_id: Mapped[str] = (
        mapped_column(
            ForeignKey("portfolios.id")
        )
    )


    # ========================================================
    # TRADE DETAILS
    # ========================================================

    symbol: Mapped[str] = mapped_column(
        String,
        index=True
    )

    direction: Mapped[str] = (
        mapped_column(String)
    )

    entry_price: Mapped[float] = (
        mapped_column(Float)
    )

    quantity: Mapped[float] = (
        mapped_column(Float)
    )

    status: Mapped[str] = (
        mapped_column(String)
    )


    # ========================================================
    # AI DECISION DATA
    # ========================================================

    risk_score: Mapped[float] = (
        mapped_column(Float)
    )

    human_approved: Mapped[bool] = (
        mapped_column(Boolean)
    )

    agent_consensus: Mapped[dict] = (
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

    user = relationship(
        "User",
        back_populates="trades"
    )

    portfolio = relationship(
        "Portfolio",
        back_populates="trades"
    )

    agent_decisions = relationship(
        "AgentDecision",
        back_populates="trade"
    )