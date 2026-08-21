"""
app/db/models/fundamental_scorecard.py
=========================================
FundamentalScorecard — one row per Investing-mode analysis run, so the trend in
a company's fundamentals is visible over time. The trend matters more than any
single snapshot: the quarterly re-review job diffs against these rows to detect
a thesis degrading.

WHAT THIS STORES, AND WHAT IT DELIBERATELY DOES NOT:
------------------------------------------------------
Stored: the QUALITY verdict and the VALUATION BAND, each with the date of the
data behind them. Both change about once a quarter and cost a full pipeline run
to produce.

NOT stored: the stance (BUY / HOLD / WATCH / ...). It depends on the live price
and on whether the position is held, so it is computed on read by
app/services/stance.py. Persisting it would mean re-running the whole pipeline
every time the price moved, and would leave a stale verb in the database
between runs.

Also not stored: any position size. Investing mode is advisory — it places no
orders, and how much to buy is the reader's decision.

    docker compose run --rm backend python -m app.scripts.create_tables

Note create_all() does NOT alter an existing table, so a column added here
after the table exists needs a manual migration or a drop and recreate.
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
    run_id: Mapped[str] = mapped_column(String(36), nullable=True)
    data_as_of: Mapped[str] = mapped_column(String(40), nullable=True)   # e.g. "Mar 2026"

    # INVESTMENT_GRADE | WATCHLIST | NOT_INVESTABLE | NOT_RATED
    quality_grade: Mapped[str] = mapped_column(String(20), nullable=False)

    # Set only when quality_grade is NOT_RATED, e.g. "SECTOR_UNSUPPORTED",
    # "INSUFFICIENT_DATA". A refusal has to say why, or it is indistinguishable
    # from a failure.
    not_rated_reason: Mapped[str] = mapped_column(String(40), nullable=True)

    # Kept separate on purpose: "the data is complete and the case is marginal"
    # is a different state from "the case looks strong but inputs are missing".
    completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    conviction: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # The three stages, as produced. JSON rather than a column per metric,
    # because the metric set is still evolving and the shapes are nested.
    business_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    financial_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    valuation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    red_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_data: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Non-prescriptive prose. The verb lives in the computed stance, not here.
    narrative: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
