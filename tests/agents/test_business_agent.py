"""
tests/agents/test_business_agent.py
=====================================
Tests for Stage 1 — the 18 qualitative questions.

The properties that matter, all learned from watching this run against a real
RELIANCE annual report:

1. A status of ANSWERED must mean the question was ANSWERED. Keying it off
   "did we retrieve passages" scored 17/18 while eight of those answers said
   "the excerpts do not provide this". Completeness feeds the verdict gate, so
   an inflated count silently buys a verdict the evidence does not support.
2. But a trailing caveat is not a non-answer. Narrowing (1) to substring
   matching threw away Q14 (which named the statutory auditor) and Q18 (which
   counted 235 subsidiaries) for hedging in their last sentence.
3. "RED FLAG: None." is not a red flag. Asked to flag something if present, a
   model will answer the question literally.
4. Q2 is never settled from company-published documents. No annual report says
   its own promoter has a criminal record.
5. Filings answer first and the web only fills what they leave empty, and every
   answer records which of the two it came from. An audited filing and a page a
   model found must never become indistinguishable downstream.

WEB SEARCH IS DISABLED FOR THIS WHOLE MODULE, BY FIXTURE:
-----------------------------------------------------------
It is a real, paid, non-deterministic network call. When it was left enabled by
default these tests silently started making live API calls — `answered` came
back as 16 where the test asserted 0, because the web had answered questions the
stubbed retrieval was supposed to fail. Tests that exercise the fallback mock
`search_answer` explicitly.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.agents import business_agent as ba
from app.agents.business_agent import (
    QUESTIONS, QuestionSpec, Kind, AnswerStatus,
    _is_non_answer, _detect_red_flag, _answer_from_shareholding,
    run_business_checklist,
)


def _passages(n=3):
    return [{"citation": f"AR FY2026, p.{10 + i}", "text": f"passage {i}", "score": 0.9 - i * 0.1}
            for i in range(n)]


@pytest.fixture(autouse=True)
def _no_live_web_search():
    """
    No test in this module may reach the live search API. Opt back in per test
    by patching `app.data.web_search.search_answer`, which is looked up at call
    time inside `_web_answer`.
    """
    with patch.object(ba.settings, "STAGE1_WEB_SEARCH_ENABLED", False):
        yield


# ============================================================
# Structure
# ============================================================

def test_all_eighteen_questions_are_defined():
    assert len(QUESTIONS) == 18
    assert [q.n for q in QUESTIONS] == list(range(1, 19))


def test_every_retrieval_question_has_multiple_curated_queries():
    """
    One phrasing rarely covers a topic an annual report spreads across
    sections — and queries in the reader's words retrieve worse than queries
    in the document's words (measured: ~0.36 vs ~0.57 on the same corpus).
    """
    for q in QUESTIONS:
        if q.kind in (Kind.RETRIEVAL, Kind.JUDGMENT):
            assert len(q.queries) >= 2, f"Q{q.n} needs more than one phrasing"


def test_promoter_background_is_marked_external():
    """No company-published document discloses its own promoter's criminal record."""
    assert next(q for q in QUESTIONS if q.n == 2).kind is Kind.EXTERNAL


def test_replicability_question_is_marked_judgment():
    assert next(q for q in QUESTIONS if q.n == 17).kind is Kind.JUDGMENT


# ============================================================
# 1 + 2 — non-answer detection
# ============================================================

@pytest.mark.parametrize("text", [
    "NOT ANSWERED: the excerpts cover sustainability, not capacity.",
    "The excerpts do not provide the number of plants.",
    "The passages do not specify the raw materials required.",
    "I cannot answer the question from these passages.",
    "",
])
def test_non_answers_are_detected(text):
    assert _is_non_answer(text) is True


@pytest.mark.parametrize("text", [
    # Real shapes observed live — both answer, then hedge.
    "The auditors are Chaturvedi & Shah LLP, appointed for five years at the 45th AGM. "
    "However, the passages do not specify the bankers.",
    "Reliance has a total of 235 subsidiaries. The passages do not provide each one's purpose.",
])
def test_answer_with_a_trailing_caveat_still_counts_as_answered(text):
    """
    Matching anywhere in the text disqualified genuinely good answers. Only the
    opening decides: lead with content and you answered; lead with "the
    passages do not provide" and you did not.
    """
    assert _is_non_answer(text) is False


# ============================================================
# 3 — red flags
# ============================================================

@pytest.mark.parametrize("appended", ["None.", "none", "N/A", "No red flag"])
def test_empty_red_flag_is_not_recorded(appended):
    """Observed live on Q14: the model answered the instruction literally."""
    spec = QuestionSpec(14, "auditors?", Kind.RETRIEVAL, ["q"], red_flag_hint="auditor change")
    assert _detect_red_flag(spec, f"The auditors are X.\nRED FLAG: {appended}") is None


def test_real_red_flag_is_recorded():
    spec = QuestionSpec(18, "subsidiaries?", Kind.RETRIEVAL, ["q"], red_flag_hint="too many")
    flag = _detect_red_flag(spec, "It has 235 subsidiaries.\nRED FLAG: 235 subsidiaries is a large number.")
    assert flag == "235 subsidiaries is a large number."


def test_no_red_flag_without_the_marker():
    spec = QuestionSpec(18, "subsidiaries?", Kind.RETRIEVAL, ["q"], red_flag_hint="too many")
    assert _detect_red_flag(spec, "It has 3 subsidiaries, all operating.") is None


# ============================================================
# Q9 — structured, not retrieved
# ============================================================

class _Raw:
    def __init__(self, shareholding):
        self.shareholding = shareholding


def test_shareholding_answer_uses_the_scraped_table():
    spec = next(q for q in QUESTIONS if q.n == 9)
    raw = _Raw({f"Q{i}": {"Promoters": 50.0 + i * 0.1, "FIIs": 18.0} for i in range(4)})
    answer = _answer_from_shareholding(spec, raw)
    assert answer.status == AnswerStatus.ANSWERED.value
    assert "Promoters" in answer.answer


def test_falling_promoter_holding_raises_a_red_flag():
    spec = next(q for q in QUESTIONS if q.n == 9)
    raw = _Raw({f"Q{i}": {"Promoters": 55.0 - i * 1.5} for i in range(4)})
    assert "Promoter holding fell" in _answer_from_shareholding(spec, raw).red_flag


def test_stable_promoter_holding_raises_nothing():
    spec = next(q for q in QUESTIONS if q.n == 9)
    raw = _Raw({f"Q{i}": {"Promoters": 50.0} for i in range(4)})
    assert _answer_from_shareholding(spec, raw).red_flag is None


# ============================================================
# End to end, with retrieval and synthesis stubbed
# ============================================================

@pytest.mark.asyncio
async def test_checklist_marks_non_answers_as_not_found():
    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=_passages())), \
         patch.object(ba, "_synthesise", AsyncMock(return_value="The passages do not provide this.")):
        result = await run_business_checklist("TESTCO")

    assert result.answered == 0
    assert result.completeness == 0.0


@pytest.mark.asyncio
async def test_checklist_counts_real_answers():
    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=_passages())), \
         patch.object(ba, "_synthesise", AsyncMock(return_value="The company refines crude oil [AR FY2026, p.16].")):
        result = await run_business_checklist("TESTCO")

    # Every RETRIEVAL/JUDGMENT question answers; Q2 stays external, Q9 has no data.
    assert result.answered == 16
    assert next(a for a in result.answers if a.n == 2).status == AnswerStatus.NEEDS_EXTERNAL.value


@pytest.mark.asyncio
async def test_retrieval_failure_does_not_fail_the_checklist():
    """One question failing must not take the other seventeen with it."""
    with patch("app.memory.document_store.retrieve", AsyncMock(side_effect=RuntimeError("qdrant down"))):
        result = await run_business_checklist("TESTCO")

    assert len(result.answers) == 18
    assert all(a.status == AnswerStatus.NOT_FOUND.value
               for a in result.answers if a.kind == Kind.RETRIEVAL.value)


@pytest.mark.asyncio
async def test_red_flags_set_the_gate_to_flag_never_block():
    """
    A BLOCK must rest on a Tier-1 regulatory source with a confirmed identity
    match. Those fetchers do not exist yet, and a false block is invisible when
    it happens — so findings are surfaced, not enforced.
    """
    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=_passages())), \
         patch.object(ba, "_synthesise",
                      AsyncMock(return_value="It has 235 subsidiaries.\nRED FLAG: 235 is a lot.")):
        result = await run_business_checklist("TESTCO")

    assert result.red_flags
    assert result.gate == "FLAG"


@pytest.mark.asyncio
async def test_synthesis_disabled_returns_the_passages_themselves():
    """
    Retrieval-only is a usable Stage 1: three cited paragraphs beats a
    190-page annual report, and every claim is sourced because you read it.
    """
    spec = next(q for q in QUESTIONS if q.n == 1)
    with patch.object(ba.settings, "STAGE1_SYNTHESIS_ENABLED", False):
        out = await ba._synthesise("TESTCO", spec, _passages(2))

    assert "AR FY2026, p.10" in out and "passage 0" in out


# ============================================================
# Web fallback
#
# The properties here are about PRECEDENCE and PROVENANCE, not about search
# working. Search is someone else's network; what this file has to pin down is
# that filings are asked first, that the web is asked when they come up empty,
# and that the answer always says which one produced it.
# ============================================================

def _web(text="Asian Paints competes with Berger and Nerolac.", sources=None):
    from app.data.web_search import WebAnswer
    return WebAnswer(text=text, sources=sources if sources is not None else [
        {"title": "Annual Report", "url": "https://bseindia.com/x.pdf", "domain": "bseindia.com"},
    ])


def _enable_web(mock):
    """Turn search on and route it at `mock`."""
    return (
        patch.object(ba.settings, "STAGE1_WEB_SEARCH_ENABLED", True),
        patch.object(ba.settings, "OPENAI_API_KEY", "sk-test"),
        patch("app.data.web_search.search_answer", mock),
    )


@pytest.mark.asyncio
async def test_documents_answer_first_and_the_web_is_never_called():
    """
    The precedence is the whole design: filings are audited and the open web is
    not. If a passing retrieval still fired a paid search, the tier recorded on
    the answer would stop meaning anything.
    """
    searcher = AsyncMock(return_value=_web())
    enable_a, enable_b, enable_c = _enable_web(searcher)

    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=_passages())), \
         patch.object(ba, "_synthesise", AsyncMock(return_value="It makes paint [AR FY2026, p.16].")), \
         enable_a, enable_b, enable_c:
        result = await run_business_checklist("TESTCO")

    q1 = next(a for a in result.answers if a.n == 1)
    assert q1.source == "DOCUMENTS"
    # Q2 is EXTERNAL and goes to the web by design; nothing else should.
    assert all(a.source != "WEB" for a in result.answers if a.n != 2)


@pytest.mark.asyncio
async def test_empty_document_store_falls_back_to_the_web():
    """
    The case that prompted this: a symbol whose annual report was never
    ingested scored 1/18, with every answer reading "no relevant passage found"
    — indistinguishable from the information not existing.
    """
    searcher = AsyncMock(return_value=_web())
    enable_a, enable_b, enable_c = _enable_web(searcher)

    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=[])), \
         enable_a, enable_b, enable_c:
        result = await run_business_checklist("TESTCO")

    q1 = next(a for a in result.answers if a.n == 1)
    assert q1.status == AnswerStatus.ANSWERED.value
    assert q1.source == "WEB"
    assert q1.sources[0]["domain"] == "bseindia.com"
    assert q1.citations == ["bseindia.com — Annual Report"]


@pytest.mark.asyncio
async def test_retrieved_but_unanswered_still_falls_back_to_the_web():
    """Retrieving passages that do not answer is a different failure from
    retrieving nothing, and the web can rescue it too."""
    searcher = AsyncMock(return_value=_web())
    enable_a, enable_b, enable_c = _enable_web(searcher)

    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=_passages())), \
         patch.object(ba, "_synthesise", AsyncMock(return_value="The passages do not provide this.")), \
         enable_a, enable_b, enable_c:
        result = await run_business_checklist("TESTCO")

    q1 = next(a for a in result.answers if a.n == 1)
    assert q1.source == "WEB" and q1.status == AnswerStatus.ANSWERED.value


@pytest.mark.asyncio
async def test_promoter_question_goes_to_the_web_before_retrieval():
    """
    Q2 is EXTERNAL: no annual report discloses its own promoter's regulatory
    history, so searching is its primary route rather than its fallback.
    """
    searcher = AsyncMock(return_value=_web(text="The promoters are the Dani and Vakil families."))
    retrieve = AsyncMock(return_value=_passages())
    enable_a, enable_b, enable_c = _enable_web(searcher)

    with patch("app.memory.document_store.retrieve", retrieve), \
         patch.object(ba, "_synthesise", AsyncMock(return_value="Something from the filing.")), \
         enable_a, enable_b, enable_c:
        result = await run_business_checklist("TESTCO")

    q2 = next(a for a in result.answers if a.n == 2)
    assert q2.source == "WEB"
    assert q2.status == AnswerStatus.ANSWERED.value


@pytest.mark.asyncio
async def test_a_web_non_answer_is_not_counted_as_answered():
    """
    The status-inflation bug, re-checked on the new path. Keying ANSWERED off
    "a search happened" would rebuild exactly the failure that scored RELIANCE
    17/18 while eight answers declined to answer.
    """
    searcher = AsyncMock(return_value=_web(text="NOT ANSWERED: no source discusses this."))
    enable_a, enable_b, enable_c = _enable_web(searcher)

    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=[])), \
         enable_a, enable_b, enable_c:
        result = await run_business_checklist("TESTCO")

    q1 = next(a for a in result.answers if a.n == 1)
    assert q1.status == AnswerStatus.NOT_FOUND.value
    assert q1.source == "WEB"          # searched and found nothing — not "never tried"


@pytest.mark.asyncio
async def test_search_budget_caps_paid_calls_per_run():
    """
    A company with no documents fails every retrieval, so without a ceiling one
    run fires a search for every question.
    """
    searcher = AsyncMock(return_value=_web())
    enable_a, enable_b, enable_c = _enable_web(searcher)

    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=[])), \
         patch.object(ba.settings, "STAGE1_WEB_SEARCH_MAX_QUESTIONS", 3), \
         enable_a, enable_b, enable_c:
        result = await run_business_checklist("TESTCO")

    assert searcher.await_count == 3
    assert sum(1 for a in result.answers if a.source == "WEB") == 3


@pytest.mark.asyncio
async def test_web_disabled_reports_silence_honestly():
    """With no web and no documents, the answer must say both are empty rather
    than implying the question is unanswerable."""
    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=[])):
        result = await run_business_checklist("TESTCO")

    q1 = next(a for a in result.answers if a.n == 1)
    assert q1.source == "NONE"
    assert "web search is unavailable" in q1.answer


@pytest.mark.asyncio
async def test_a_discarded_search_does_not_crash_the_checklist():
    """`search_answer` returns None for an unsourced answer. That is a refusal,
    not an exception, and the run must continue."""
    searcher = AsyncMock(return_value=None)
    enable_a, enable_b, enable_c = _enable_web(searcher)

    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=[])), \
         enable_a, enable_b, enable_c:
        result = await run_business_checklist("TESTCO")

    assert len(result.answers) == 18
    assert next(a for a in result.answers if a.n == 1).source == "NONE"


# ============================================================
# Synthesis outage
#
# Observed live: an exhausted API key produced a clean 18/18 whose "answers"
# were raw passage dumps. Completeness gates the verdict downstream, so an
# outage was buying a grade.
# ============================================================

@pytest.mark.asyncio
async def test_synthesis_failure_is_not_counted_as_an_answer():
    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=_passages())), \
         patch.object(ba, "_synthesise",
                      AsyncMock(side_effect=ba.SynthesisUnavailable("credit_balance_exhausted"))):
        result = await run_business_checklist("TESTCO")

    q1 = next(a for a in result.answers if a.n == 1)
    assert q1.status == AnswerStatus.PARTIAL.value
    assert q1.citations                      # the evidence is still shown
    assert "credit_balance_exhausted" in q1.answer
    assert result.answered == 0              # and nothing is claimed as answered


@pytest.mark.asyncio
async def test_synthesis_switched_off_still_counts_as_answered():
    """
    The supported mode, and the one that must NOT be caught by the fix above:
    with synthesis disabled the passages are the answer by design.
    """
    with patch("app.memory.document_store.retrieve", AsyncMock(return_value=_passages())), \
         patch.object(ba.settings, "STAGE1_SYNTHESIS_ENABLED", False):
        result = await run_business_checklist("TESTCO")

    q1 = next(a for a in result.answers if a.n == 1)
    assert q1.status == AnswerStatus.ANSWERED.value
