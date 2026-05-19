
"""
Broker Account Model

Stores broker/exchange integrations.
"""

# ============================================================
# IMPORTS
# ============================================================

import uuid

from datetime import datetime

from sqlalchemy import (
    String,
    ForeignKey,
    DateTime
)

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship
)

from app.db.base import Base


# ============================================================
# BROKER ACCOUNT MODEL
# ============================================================

class BrokerAccount(Base):

    __tablename__ = "broker_accounts"


    # ========================================================
    # PRIMARY KEY
    # ========================================================

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )


    # ========================================================
    # USER LINK
    # ========================================================

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id")
    )


    # ========================================================
    # BROKER DETAILS
    # ========================================================

    broker_name: Mapped[str] = (
        mapped_column(String)
    )

    api_key_encrypted: Mapped[str] = (
        mapped_column(String)
    )

    api_secret_encrypted: Mapped[str] = (
        mapped_column(String)
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
        back_populates="broker_accounts"
    )

    portfolios = relationship(
        "Portfolio",
        back_populates="broker_account"
    )