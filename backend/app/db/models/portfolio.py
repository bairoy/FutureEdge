

"""
Portfolio Model

Stores current portfolio state.
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
# PORTFOLIO MODEL
# ============================================================

class Portfolio(Base):

    __tablename__ = "portfolios"


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

    broker_account_id: Mapped[str] = (
        mapped_column(
            ForeignKey("broker_accounts.id")
        )
    )


    # ========================================================
    # PORTFOLIO METRICS
    # ========================================================

    total_equity: Mapped[float] = (
        mapped_column(Float)
    )

    margin_used: Mapped[float] = (
        mapped_column(Float)
    )

    margin_available: Mapped[float] = (
        mapped_column(Float)
    )

    unrealized_pnl: Mapped[float] = (
        mapped_column(Float)
    )


    # ========================================================
    # OPEN POSITIONS
    # ========================================================

    open_positions: Mapped[list] = (
        mapped_column(JSON)
    )


    # ========================================================
    # TIMESTAMP
    # ========================================================

    updated_at: Mapped[datetime] = (
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
        back_populates="portfolios"
    )

    broker_account = relationship(
        "BrokerAccount",
        back_populates="portfolios"
    )

    trades = relationship(
        "Trade",
        back_populates="portfolio"
    )