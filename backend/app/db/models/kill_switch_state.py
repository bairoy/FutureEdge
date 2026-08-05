"""
app/db/models/kill_switch_state.py
===================================
KillSwitchState table — the DURABLE source of truth for "is trading halted?".

WHY THIS EXISTS (separate from kill_switch_events):
----------------------------------------------------
`kill_switch_events` is an append-only audit log — it answers "who halted
trading and when". It is written best-effort inside a try/except, so it can
silently miss an entry and must never be trusted as current state.

The live halt flag used to live ONLY in Redis, which made the kill switch fail
OPEN in two ways:
  1. It was written with a TTL, so a halt silently expired and trading resumed
     with no human involved.
  2. Redis is an in-memory cache. Restarting the container while halted wiped
     the key, and trading silently resumed.

Both are unacceptable for a halt switch: it must stay halted until a human
explicitly releases it. This table is the durable record that survives both, and
`reconcile_kill_switch_from_db()` replays it into Redis on every startup.

HOW TO USE IT:
--------------
Never query this model directly from trading code — go through
`app.services.kill_switch_service`, which keeps Redis and this table in sync:

    from app.services.kill_switch_service import is_trading_halted
    if await is_trading_halted():
        return  # blocked

SINGLE ROW:
-----------
This table holds exactly one row, pinned to id=SINGLETON_ID. The service
upserts it; nothing else should insert into this table.
"""

from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# The one and only row id. Fixed so concurrent writers upsert the same row
# instead of racing to create competing state rows.
SINGLETON_ID = "singleton"


class KillSwitchState(Base):

    __tablename__ = "kill_switch_state"

    id: Mapped[str] = mapped_column(
        String(16),
        primary_key=True,
        default=SINGLETON_ID,
        comment="always 'singleton' — this table holds exactly one row",
    )

    # THE flag. True = trading halted.
    is_halted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True = all trading halted until explicitly released",
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Why trading was halted",
    )

    # Free text (email or "SYSTEM"), deliberately not a users FK — a system
    # auto-halt has no user, and we never want an FK violation to block a halt.
    halted_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Email of the human who halted, or 'SYSTEM' for auto-halts",
    )

    halted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # NULL means "no expiry" — the normal, fail-closed case. Only set when a
    # caller explicitly asks for a time-boxed halt.
    halted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="NULL = halt never expires (default). Set only for explicit time-boxed halts.",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"KillSwitchState(is_halted={self.is_halted!r}, "
            f"reason={self.reason!r}, halted_until={self.halted_until!r})"
        )
