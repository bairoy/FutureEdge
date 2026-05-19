"""
User Model

Represents platform users.
"""

# ============================================================
# IMPORTS
# ============================================================

import uuid

from datetime import datetime

from sqlalchemy import (
    String,
    DateTime,
    Boolean
)

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship
)

from app.db.base import Base


# ============================================================
# USER MODEL
# ============================================================

class User(Base):

    __tablename__ = "users"


    # ========================================================
    # PRIMARY KEY
    # ========================================================

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )


    # ========================================================
    # AUTHENTICATION
    # ========================================================

    email: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True
    )

    hashed_password: Mapped[str] = (
        mapped_column(String)
    )


    # ========================================================
    # USER STATUS
    # ========================================================

    is_active: Mapped[bool] = (
        mapped_column(
            Boolean,
            default=True
        )
    )


    # ========================================================
    # TIMESTAMPS
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

    broker_accounts = relationship(
        "BrokerAccount",
        back_populates="user"
    )

    portfolios = relationship(
        "Portfolio",
        back_populates="user"
    )

    trades = relationship(
        "Trade",
        back_populates="user"
    )