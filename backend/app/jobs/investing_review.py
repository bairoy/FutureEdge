"""
app/jobs/investing_review.py
===============================
The quarterly re-review — the job that tells you when to sell.

WHY THIS IS THE POINT OF INVESTING MODE:
------------------------------------------
A one-off analysis tells you whether to buy. Nothing tells you when to get
out, and "when to get out" is the harder half: you bought by hand, at your own
broker, and the system has no position feed to watch. What it can watch is the
BUSINESS — so every quarter it re-runs the full pipeline on everything you
hold plus everything you are watching, and compares the verdict to last time.

The holdings register exists for this job. Without it there is nothing to
re-review but a static watchlist.

WHY QUARTERLY, AND WHY THESE MONTHS:
--------------------------------------
Indian listed companies report quarterly, and the filings land roughly:
Q3 by mid-February, Q4 and the annual report by end-May, Q1 by mid-August,
Q2 by mid-November. Running on the 1st of March, June, September and December
puts each review just after a results season rather than in the middle of one,
so it reads fresh statements instead of re-reading the same ones.

THE DISTINCTION THIS JOB EXISTS TO GET RIGHT:
-----------------------------------------------
A grade can fall for two completely different reasons:

    the business deteriorated        -> a thesis break. Act on it.
    the data stopped arriving        -> Screener changed its markup, or a
                                        scrape failed. Act on the SCRAPER.

Both look identical in the stored grade, because insufficient data forces
NOT_RATED. Reporting them the same way would eventually tell someone their
compounder had degraded when in fact a parser broke — and the natural response
to "your holding degraded" is to sell it. So NOT_RATED is never counted as a
degradation here. It is reported separately, in different words, as a problem
with the system rather than with the company.

WHAT IT DOES NOT DO:
----------------------
It does not compute a stance. The verb depends on the live price, and a
quarterly job's opinion of the price is stale within the hour — the stance is
derived on read, in the API, for exactly that reason. This job answers "has
the business changed", which is the question a quarter is the right unit for.

It also places no orders, alerts only, and never sells anything for you.

USAGE:
------
    from app.jobs.investing_review import run_review

    summary = await run_review()          # every holding + the watchlist
    summary = await run_review(["ITC"])   # or a specific set
"""

from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.db.models.fundamental_scorecard import FundamentalScorecard
from app.db.models.investing_holding import InvestingHolding


# Quality ordered by how investable it is. NOT_RATED is deliberately ABSENT:
# it is a refusal to judge, not a rung on this ladder, and giving it a number
# is what would let a failed scrape masquerade as a downgrade.
_GRADE_RANK = {
    "NOT_INVESTABLE": 1,
    "WATCHLIST": 2,
    "INVESTMENT_GRADE": 3,
}


@dataclass
class SymbolReview:
    """What one quarter changed for one symbol."""
    symbol: str
    held: bool
    previous_grade: str | None = None
    current_grade: str | None = None
    new_red_flags: list[str] = field(default_factory=list)
    # Set when the pipeline could not produce a verdict this quarter. Kept
    # apart from `degraded` on purpose — see the module docstring.
    data_problem: str | None = None
    error: str | None = None

    @property
    def degraded(self) -> bool:
        """A real fall in quality, with both grades on the investability scale."""
        before = _GRADE_RANK.get(self.previous_grade or "")
        after = _GRADE_RANK.get(self.current_grade or "")
        return bool(before and after and after < before)

    @property
    def upgraded(self) -> bool:
        before = _GRADE_RANK.get(self.previous_grade or "")
        after = _GRADE_RANK.get(self.current_grade or "")
        return bool(before and after and after > before)

    @property
    def needs_attention(self) -> bool:
        return bool(self.degraded or self.new_red_flags or self.data_problem or self.error)


@dataclass
class ReviewSummary:
    reviewed: list[SymbolReview] = field(default_factory=list)

    @property
    def alerting(self) -> list[SymbolReview]:
        """Held positions first — those are the ones with money behind them."""
        return sorted(
            (r for r in self.reviewed if r.needs_attention),
            key=lambda r: (not r.held, r.symbol),
        )


# ============================================================
# SYMBOL SELECTION
# ============================================================

async def _symbols_to_review(session) -> list[tuple[str, bool]]:
    """
    [(symbol, is_held)] — everything held, by anyone, plus the watchlist.

    Holdings come first and are never skipped: a watchlist symbol going stale
    costs you an opportunity, while a holding going stale costs you money.
    """
    rows = (await session.execute(select(InvestingHolding.symbol).distinct())).scalars().all()
    held = {s.upper().strip() for s in rows if s}

    watchlist = {
        s.strip().upper()
        for s in settings.INVESTING_WATCHLIST_SYMBOLS.split(",")
        if s.strip()
    }

    ordered = [(s, True) for s in sorted(held)]
    ordered += [(s, False) for s in sorted(watchlist - held)]
    return ordered


async def _latest_scorecard(session, symbol: str) -> FundamentalScorecard | None:
    return (await session.execute(
        select(FundamentalScorecard)
        .where(FundamentalScorecard.symbol == symbol)
        .order_by(FundamentalScorecard.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


def _flag_texts(scorecard_flags) -> set[str]:
    """Red flags as comparable strings, so 'new since last quarter' is answerable."""
    out = set()
    for flag in scorecard_flags or []:
        if isinstance(flag, dict):
            text = flag.get("flag") or flag.get("reason") or ""
        else:
            text = str(flag)
        text = text.strip()
        if text:
            out.add(text)
    return out


# ============================================================
# ONE SYMBOL
# ============================================================

async def review_symbol(symbol: str, held: bool = False) -> SymbolReview:
    """
    Re-run the pipeline for one symbol and diff it against the last stored run.

    Persists the new scorecard whatever the outcome — the history is what makes
    the next quarter's comparison possible, and a quarter where the data went
    missing is itself worth having on the record.
    """
    review = SymbolReview(symbol=symbol, held=held)

    async with AsyncSessionLocal() as session:
        previous = await _latest_scorecard(session, symbol)
        review.previous_grade = previous.quality_grade if previous else None
        previous_flags = _flag_texts(previous.red_flags) if previous else set()

        try:
            from app.graph.builder import run_investing_cycle

            result = await run_investing_cycle(symbol, user_id="scheduler")
            state = result["state"]
            thesis = state.get("investment_thesis")

            if thesis is None:
                review.error = "the pipeline produced no thesis"
                return review

            business = state.get("business_report")
            financial = state.get("financial_report")
            valuation = state.get("valuation_report")

            row = FundamentalScorecard(
                symbol=symbol,
                run_id=result["thread_id"],
                data_as_of=thesis.data_as_of,
                quality_grade=thesis.quality_grade,
                not_rated_reason=thesis.not_rated_reason,
                completeness=thesis.completeness,
                conviction=thesis.conviction,
                business_report=business.model_dump() if business else {},
                financial_report=financial.model_dump() if financial else {},
                valuation_report=valuation.model_dump() if valuation else {},
                red_flags=thesis.red_flags or [],
                missing_data=[m.model_dump() for m in (state.get("missing_data") or [])],
                narrative=thesis.narrative,
            )
            session.add(row)
            await session.commit()

            review.current_grade = thesis.quality_grade
            review.new_red_flags = sorted(_flag_texts(thesis.red_flags) - previous_flags)

            # NOT_RATED means the pipeline refused to judge, which is almost
            # always missing inputs rather than a worse business. Reported as
            # its own kind of finding rather than folded into `degraded`.
            if thesis.quality_grade == "NOT_RATED":
                review.data_problem = (
                    thesis.not_rated_reason
                    or f"no verdict could be issued (completeness {thesis.completeness:.0%})"
                )

        except Exception as e:
            await session.rollback()
            logger.error(f"Quarterly review failed for {symbol}: {e}")
            review.error = str(e)

    return review


# ============================================================
# THE SWEEP
# ============================================================

async def run_review(symbols: list[str] | None = None) -> ReviewSummary:
    """
    Re-review every holding and watchlist symbol, then alert on what changed.

    Deliberately SEQUENTIAL. Each symbol is a full pipeline run — a scrape, a
    document retrieval, paid web searches and a DCF — and the scraper already
    serialises its own requests behind a courtesy throttle. Running fifteen of
    these at once would buy nothing and would hammer both Screener and the API.
    A quarterly job has no deadline worth optimising for.
    """
    summary = ReviewSummary()

    async with AsyncSessionLocal() as session:
        targets = (
            [(s.upper().strip(), False) for s in symbols]
            if symbols else await _symbols_to_review(session)
        )

    if not targets:
        logger.info("Quarterly investing review: nothing to review")
        return summary

    logger.info(f"Quarterly investing review starting | {len(targets)} symbol(s)")

    for symbol, held in targets:
        review = await review_symbol(symbol, held=held)
        summary.reviewed.append(review)
        logger.info(
            f"Reviewed {symbol} | {review.previous_grade} -> {review.current_grade}"
            + (f" | {len(review.new_red_flags)} new flag(s)" if review.new_red_flags else "")
            + (f" | DATA: {review.data_problem}" if review.data_problem else "")
            + (f" | ERROR: {review.error}" if review.error else "")
        )

    await _notify(summary)
    return summary


# ============================================================
# ALERTING
# ============================================================

async def _notify(summary: ReviewSummary) -> None:
    """
    Message only when something changed.

    A quarterly job that always reports "all fine" teaches you to skim it, and
    then the one that matters gets skimmed too. Silence is the healthy result;
    the run is in the log either way.
    """
    alerting = summary.alerting
    if not alerting:
        logger.info(f"Quarterly investing review: no changes across {len(summary.reviewed)} symbol(s)")
        return

    lines = ["<b>Quarterly investing review</b>", ""]

    for r in alerting:
        tag = "HELD" if r.held else "watchlist"
        lines.append(f"<b>{r.symbol}</b> ({tag})")

        if r.degraded:
            lines.append(f"  ⚠️ Quality fell: {r.previous_grade} → {r.current_grade}")
        if r.new_red_flags:
            lines.append(f"  🚩 {len(r.new_red_flags)} new red flag(s):")
            for flag in r.new_red_flags[:3]:
                lines.append(f"     • {flag[:160]}")
        if r.data_problem:
            # Worded as a system problem, because that is what it usually is.
            lines.append(f"  ℹ️ No verdict this quarter — {r.data_problem}")
            lines.append("     This is missing data, not evidence the business got worse.")
        if r.error:
            lines.append(f"  ❌ Review failed: {r.error[:160]}")
        lines.append("")

    upgrades = [r for r in summary.reviewed if r.upgraded]
    if upgrades:
        lines.append("Improved: " + ", ".join(
            f"{r.symbol} ({r.previous_grade} → {r.current_grade})" for r in upgrades
        ))
        lines.append("")

    lines.append(
        f"Reviewed {len(summary.reviewed)} symbol(s). This is advisory — no order "
        f"has been placed, and none will be."
    )

    try:
        from app.services.telegram_service import send_telegram_message
        await send_telegram_message("\n".join(lines))
    except Exception as e:
        # The findings are already in the log; a dead notification channel must
        # not lose the review that produced them.
        logger.error(f"Quarterly review alert could not be sent: {e}")
