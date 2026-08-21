"""
tests/jobs/test_investing_review.py
======================================
Tests for the quarterly re-review.

THE PROPERTY THIS FILE EXISTS FOR:
------------------------------------
A grade can fall because the business deteriorated, or because the data stopped
arriving — and insufficient data forces NOT_RATED, so both land in the same
column. Reporting them the same way would eventually tell someone their
compounder had degraded when a parser had broken, and the natural response to
"your holding degraded" is to sell it.

So NOT_RATED must never count as a degradation, and the tests below pin that
from several directions.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.jobs import investing_review as ir
from app.jobs.investing_review import SymbolReview, ReviewSummary


def _review(previous, current, **kw):
    return SymbolReview(symbol="TESTCO", held=kw.pop("held", False),
                        previous_grade=previous, current_grade=current, **kw)


# ============================================================
# Degradation
# ============================================================

def test_a_real_fall_in_quality_is_a_degradation():
    assert _review("INVESTMENT_GRADE", "WATCHLIST").degraded
    assert _review("WATCHLIST", "NOT_INVESTABLE").degraded
    assert _review("INVESTMENT_GRADE", "NOT_INVESTABLE").degraded


def test_a_rise_in_quality_is_not_a_degradation():
    r = _review("WATCHLIST", "INVESTMENT_GRADE")
    assert r.upgraded and not r.degraded


def test_an_unchanged_grade_is_neither():
    r = _review("WATCHLIST", "WATCHLIST")
    assert not r.degraded and not r.upgraded and not r.needs_attention


@pytest.mark.parametrize("previous,current", [
    ("INVESTMENT_GRADE", "NOT_RATED"),
    ("WATCHLIST", "NOT_RATED"),
    ("NOT_RATED", "WATCHLIST"),
    ("NOT_RATED", "NOT_RATED"),
])
def test_not_rated_is_never_a_degradation(previous, current):
    """
    NOT_RATED is a refusal to judge, not a rung on the investability ladder.
    Ranking it would let a failed scrape masquerade as a downgrade — and the
    action a downgrade prompts is selling a company that may be perfectly fine.
    """
    assert not _review(previous, current).degraded


def test_a_missing_previous_grade_is_not_a_degradation():
    """First ever review of a symbol. Nothing to compare against is not bad news."""
    assert not _review(None, "WATCHLIST").degraded


def test_losing_the_verdict_is_reported_as_a_data_problem():
    r = _review("INVESTMENT_GRADE", "NOT_RATED", data_problem="INSUFFICIENT_DATA")
    assert not r.degraded          # not a thesis break
    assert r.needs_attention       # but you are still told


# ============================================================
# Red flags
# ============================================================

def test_only_flags_new_since_last_quarter_count():
    previous = [{"question": 2, "flag": "pledged promoter shares"}]
    current = [{"question": 2, "flag": "pledged promoter shares"},
               {"question": 13, "flag": "CCI investigation opened"}]
    new = ir._flag_texts(current) - ir._flag_texts(previous)
    assert new == {"CCI investigation opened"}


def test_flag_extraction_tolerates_both_shapes_and_drops_blanks():
    assert ir._flag_texts([{"flag": "a"}, {"reason": "b"}, "c", {"flag": "  "}, {}]) == {"a", "b", "c"}


def test_new_flags_alone_trigger_attention_without_a_downgrade():
    r = _review("INVESTMENT_GRADE", "INVESTMENT_GRADE", new_red_flags=["CCI investigation"])
    assert not r.degraded and r.needs_attention


# ============================================================
# Prioritisation
# ============================================================

def test_holdings_are_listed_before_watchlist_symbols():
    """A watchlist symbol going stale costs an opportunity; a holding going
    stale costs money."""
    summary = ReviewSummary(reviewed=[
        SymbolReview("AAA", held=False, previous_grade="INVESTMENT_GRADE", current_grade="WATCHLIST"),
        SymbolReview("ZZZ", held=True, previous_grade="INVESTMENT_GRADE", current_grade="WATCHLIST"),
    ])
    assert [r.symbol for r in summary.alerting] == ["ZZZ", "AAA"]


def test_unchanged_symbols_are_not_alerted_on():
    summary = ReviewSummary(reviewed=[
        SymbolReview("AAA", held=True, previous_grade="WATCHLIST", current_grade="WATCHLIST"),
    ])
    assert summary.alerting == []


# ============================================================
# Notification
# ============================================================

@pytest.mark.asyncio
async def test_no_message_is_sent_when_nothing_changed():
    """
    A quarterly job that always says "all fine" teaches you to skim it, and
    then the one that matters gets skimmed too.
    """
    sender = AsyncMock()
    summary = ReviewSummary(reviewed=[
        SymbolReview("AAA", held=True, previous_grade="WATCHLIST", current_grade="WATCHLIST"),
    ])
    with patch("app.services.telegram_service.send_telegram_message", sender):
        await ir._notify(summary)
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_data_problem_is_worded_as_a_system_problem():
    """The message must not read as "the business got worse" when the real
    cause is a scrape that stopped working."""
    sender = AsyncMock()
    summary = ReviewSummary(reviewed=[
        SymbolReview("AAA", held=True, previous_grade="INVESTMENT_GRADE",
                     current_grade="NOT_RATED", data_problem="INSUFFICIENT_DATA"),
    ])
    with patch("app.services.telegram_service.send_telegram_message", sender):
        await ir._notify(summary)

    body = sender.await_args.args[0]
    assert "not evidence the business got worse" in body
    assert "Quality fell" not in body


@pytest.mark.asyncio
async def test_a_degradation_is_named_plainly():
    sender = AsyncMock()
    summary = ReviewSummary(reviewed=[
        SymbolReview("AAA", held=True, previous_grade="INVESTMENT_GRADE", current_grade="NOT_INVESTABLE"),
    ])
    with patch("app.services.telegram_service.send_telegram_message", sender):
        await ir._notify(summary)

    body = sender.await_args.args[0]
    assert "Quality fell: INVESTMENT_GRADE → NOT_INVESTABLE" in body
    assert "HELD" in body
    # Advisory, always — this job must never read as having done something.
    assert "no order" in body


@pytest.mark.asyncio
async def test_a_dead_notification_channel_does_not_lose_the_review():
    """The findings are already in the log; a Telegram outage must not turn a
    completed review into an exception."""
    summary = ReviewSummary(reviewed=[
        SymbolReview("AAA", held=True, previous_grade="INVESTMENT_GRADE", current_grade="WATCHLIST"),
    ])
    with patch("app.services.telegram_service.send_telegram_message",
               AsyncMock(side_effect=RuntimeError("telegram down"))):
        await ir._notify(summary)     # must not raise


# ============================================================
# The sweep
# ============================================================

@pytest.mark.asyncio
async def test_one_failing_symbol_does_not_abort_the_sweep():
    """Fifteen symbols, one bad scrape — the other fourteen still get reviewed."""
    async def fake_review(symbol, held=False):
        if symbol == "BBB":
            return SymbolReview(symbol, held, error="scrape failed")
        return SymbolReview(symbol, held, previous_grade="WATCHLIST", current_grade="WATCHLIST")

    with patch.object(ir, "review_symbol", fake_review), \
         patch.object(ir, "_notify", AsyncMock()):
        summary = await ir.run_review(["AAA", "BBB", "CCC"])

    assert [r.symbol for r in summary.reviewed] == ["AAA", "BBB", "CCC"]
    assert [r.symbol for r in summary.alerting] == ["BBB"]
