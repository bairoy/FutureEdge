"""
app/db/models/fundamental_scorecard.py
=========================================
FundamentalScorecard table — one row per symbol per generation, so you can
track whether a company's fundamentals are improving or degrading over time
(the trend matters more than any single snapshot).

Drop this file in as app/db/models/fundamental_scorecard.py, then:
    1. Import it in app/db/models/__init__.py alongside the other models
       (so Alembic/create_tables picks it up)
    2. Run: docker compose run --rm backend python -m app.scripts.create_tables
       (or generate a proper Alembic migration if you're past initial setup)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, JSON, Float, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FundamentalScorecard(Base):
    __tablename__ = "fundamental_scorecards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    data_as_of: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "Mar 2026"

    verdict: Mapped[str] = mapped_column(String(20), nullable=False)          # INVESTMENT_GRADE / NEUTRAL / AVOID
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)      # HIGH / MEDIUM / LOW
    suggested_allocation_pct: Mapped[float] = mapped_column(Float, nullable=False)

    # Full structured scorecard (ratios, flags, DuPont breakdown) — kept as
    # JSON rather than a wide column-per-ratio table, since the ratio set
    # here is still likely to evolve as you tune the agent.
    profitability: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    leverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    growth: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    valuation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    dupont: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    red_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    green_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    rationale: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
