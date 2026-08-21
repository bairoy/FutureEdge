"""
app/db/models/fundamental_manual_input.py
============================================
FundamentalManualInput — figures a human read out of an annual report and
typed in, because no free source publishes them in machine-readable form.

WHY THIS TABLE EXISTS:
------------------------
Screener covers roughly 7 of the 10 checks in the Stage 2 checklist. The rest
(gross profit margin — there is no COGS row; subsidiary lists; segment revenue
mix; capacity utilisation) exist only in the annual report.

This table is the FALLBACK, not the main path. The system ingests annual reports
and concall transcripts itself and retrieves those figures — see the document
ingestion pipeline. Rows land here only when that fails: a scanned-image PDF with
no extractable text, a company that publishes no transcripts, or a figure the
retrieval genuinely could not find. Reaching for this table often means ingestion
is broken, not that the reader has work to do.

A value entered here is the HIGHEST-trust tier in the system, not a fallback:
it was read by a human off a primary source. That is exactly why it carries
provenance — without `source_note` and `entered_by`, one typo becomes an
unattributable permanent verdict, and there is no way to tell a computed
figure from a typed one.

HOW IT IS USED:
-----------------
Keyed by (symbol, period, field_name) so entries are reusable — next quarter
you fill only what is new, not everything again. The agent looks up this table
after scraping and before scoring.

Two rules the reader of this table must enforce (they are not constraints the
database can express):
  1. A value is stale once its `period` is no longer the latest reporting
     period — surface it as stale rather than applying it to a newer year.
  2. If a manual value and a scraped value disagree beyond tolerance, show
     BOTH and make the human choose. Never silently overwrite, in either
     direction — that disagreement is itself a signal.

    docker compose run --rm backend python -m app.scripts.create_tables
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FundamentalManualInput(Base):
    __tablename__ = "fundamental_manual_inputs"
    __table_args__ = (
        # One current value per field per period. Re-entering updates in place;
        # the audit trail is entered_by + updated_at, not a row per revision.
        UniqueConstraint("symbol", "period", "field_name", name="uq_manual_input_symbol_period_field"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # The reporting period the figure belongs to, matching Screener's column
    # labels ("Mar 2026"). Staleness is judged against this, not entered_at.
    period: Mapped[str] = mapped_column(String(20), nullable=False)

    # e.g. "gross_profit_margin", "subsidiary_count". Snake_case, matching the
    # key the agent reported as missing, so the two sides line up.
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Numeric for ratios, text for things like a subsidiary list. Exactly one
    # is expected to be set; the agent reads whichever the field calls for.
    value_numeric: Mapped[float] = mapped_column(Float, nullable=True)
    value_text: Mapped[str] = mapped_column(Text, nullable=True)

    # Provenance. Without this the figure is unauditable — the whole point of
    # trusting human input over a scrape is that its source can be checked.
    entered_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_note: Mapped[str] = mapped_column(Text, nullable=True)  # e.g. "AR FY26 p.142"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
