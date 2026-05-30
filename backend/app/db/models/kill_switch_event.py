"""
app/db/models/kill_switch_event.py
====================================
KillSwitchEvent table — records audit trail logs for every halt/resume of trading.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class KillSwitchEvent(Base):

    __tablename__ = "kill_switch_events"

    # --------------------------------------------------------
    # IDENTITY
    # --------------------------------------------------------

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Which user activated/deactivated the kill switch (can be NULL if system-activated)
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="user_id of the person who triggered the event (NULL for system/auto halts)",
    )

    # HALT | RESUME
    action: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
        comment="HALT | RESUME",
    )

    # Why the kill switch was triggered/released
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Reason for action",
    )

    # When this event occurred
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    # Many events → one user
    user = relationship("User")

    def __repr__(self) -> str:
        return (
            f"KillSwitchEvent(action={self.action!r}, "
            f"user_id={self.user_id!r}, timestamp={self.timestamp!r})"
        )
