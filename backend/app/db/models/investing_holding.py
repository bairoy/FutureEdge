"""
app/db/models/investing_holding.py
=====================================
InvestingHolding — the manual register of long-term positions the user bought
themselves, at their own broker.

WHY THIS TABLE EXISTS:
------------------------
Investing mode is advisory. It never places an order, so no `Trade` row is ever
written for a long-term position and the system has no other way to learn what
is actually owned. Without that, two things are impossible:

  1. The stance matrix collapses. BUY, ADD and HOLD are different answers to
     the same numbers depending only on whether the position already exists —
     with no register, every answer is BUY or WATCH.
  2. The quarterly re-review has nothing to watch. Knowing when to SELL is the
     single most valuable output of this feature, and it is driven by the
     thesis degrading on something held, not by a screen of the whole market.

Rows are entered by hand after executing at the broker. It is a record of
intent, not a reconciliation against broker holdings — deliberately, because
Investing mode does not touch the broker at all.

    docker compose run --rm backend python -m app.scripts.create_tables
"""

import uuid
from datetime import datetime, timezone, date

from sqlalchemy import String, Float, Integer, Date, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InvestingHolding(Base):
    __tablename__ = "investing_holdings"
    __table_args__ = (
        # One row per user per symbol — a second purchase updates quantity and
        # the average price rather than adding a lot, matching how the broker
        # reports delivery holdings back to the user.
        UniqueConstraint("user_id", "symbol", name="uq_investing_holding_user_symbol"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_buy_price: Mapped[float] = mapped_column(Float, nullable=False)
    buy_date: Mapped[date] = mapped_column(Date, nullable=False)

    # The scorecard that was current when the position was opened. The quarterly
    # job diffs against this, so "the thesis changed" is measured from what was
    # actually believed at purchase rather than from the previous run.
    thesis_snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fundamental_scorecards.id", ondelete="SET NULL"), nullable=True
    )

    notes: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
