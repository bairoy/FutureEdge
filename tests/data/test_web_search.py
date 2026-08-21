"""
tests/data/test_web_search.py
================================
Tests for the web-search source.

THE PROPERTY THAT MATTERS MOST:
---------------------------------
An answer produced WITHOUT searching must be discarded. Offering the tool
(`tools=[...]`) does not compel its use — measured against the live API, the
model will often decline it and answer from parametric memory instead: fluent,
plausible, and carrying no citations at all. For a research system that is the
worst available failure mode, because unsourced recall is indistinguishable
from sourced research once it reaches the page.

So the call forces the tool, and the response is checked for evidence that a
search actually happened before anything is returned.
"""

import sys
import os
import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.data import web_search as ws


# ─── Fakes shaped like the Responses API ──────────────────────────

def _ann(url, title):
    return SimpleNamespace(type="url_citation", url=url, title=title)


def _response(text, *, searched=True, annotations=()):
    content = [SimpleNamespace(annotations=list(annotations))]
    output = []
    if searched:
        output.append(SimpleNamespace(type="web_search_call", content=None))
    output.append(SimpleNamespace(type="message", content=content))
    return SimpleNamespace(output=output, output_text=text)


class _FakeClient:
    def __init__(self, response, capture=None):
        self._response = response
        self._capture = capture if capture is not None else {}
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self._capture.update(kwargs)
        return self._response


def _run(response, capture=None):
    with patch.object(ws.settings, "STAGE1_WEB_SEARCH_ENABLED", True), \
         patch.object(ws.settings, "OPENAI_API_KEY", "sk-test"), \
         patch("openai.OpenAI", lambda **_: _FakeClient(response, capture)):
        return asyncio.run(ws.search_answer("who audits this company?"))


# ─── Tests ────────────────────────────────────────────────────────

def test_the_search_tool_is_forced_not_merely_offered():
    capture: dict = {}
    _run(_response("Deloitte audits it."), capture)

    assert capture["tools"] == [{"type": "web_search"}]
    # Without this the model answers from memory and cites nothing.
    assert capture["tool_choice"] == {"type": "web_search"}


def test_an_answer_produced_without_searching_is_discarded():
    assert _run(_response("Deloitte audits it.", searched=False)) is None


def test_a_searched_answer_is_returned_with_its_sources():
    hit = _run(_response(
        "S R B C & Co. LLP audits it.",
        annotations=[_ann("https://www.bseindia.com/x.pdf", "Annual Report 2026")],
    ))
    assert hit is not None
    assert hit.text == "S R B C & Co. LLP audits it."
    assert hit.sources[0]["domain"] == "bseindia.com"     # www. stripped
    assert hit.citations == ["bseindia.com — Annual Report 2026"]


def test_inline_link_markup_is_stripped_from_the_prose():
    """
    The model writes its own inline citations AND returns the same URLs
    structurally. Keeping both leaves the prose unreadable and duplicates every
    source, so the inline copies go.
    """
    raw = ("Deloitte held office until the 80th AGM "
           "([economictimes.indiatimes.com](https://economictimes.indiatimes.com/x.cms)) "
           "and was replaced.")
    hit = _run(_response(raw))
    assert "](" not in hit.text
    assert hit.text.startswith("Deloitte held office until the 80th AGM and was replaced.")


def test_duplicate_sources_are_collapsed_in_the_citations():
    hit = _run(_response("text", annotations=[
        _ann("https://sebi.gov.in/a", "SEBI order"),
        _ann("https://sebi.gov.in/a", "SEBI order"),
        _ann("https://nseindia.com/b", "NSE filing"),
    ]))
    assert hit.citations == ["sebi.gov.in — SEBI order", "nseindia.com — NSE filing"]
    # The raw list keeps every occurrence; only the display strings collapse.
    assert len(hit.sources) == 3


def test_disabled_search_returns_none_without_calling_out():
    with patch.object(ws.settings, "STAGE1_WEB_SEARCH_ENABLED", False):
        assert asyncio.run(ws.search_answer("anything")) is None


def test_missing_api_key_returns_none():
    with patch.object(ws.settings, "STAGE1_WEB_SEARCH_ENABLED", True), \
         patch.object(ws.settings, "OPENAI_API_KEY", ""):
        assert asyncio.run(ws.search_answer("anything")) is None


def test_an_api_failure_is_swallowed_rather_than_raised():
    """Stage 1 answers 18 questions concurrently; one failing search must not
    take the other seventeen with it."""
    class _Boom:
        def __init__(self): self.responses = SimpleNamespace(create=self._raise)
        def _raise(self, **_): raise RuntimeError("rate limited")

    with patch.object(ws.settings, "STAGE1_WEB_SEARCH_ENABLED", True), \
         patch.object(ws.settings, "OPENAI_API_KEY", "sk-test"), \
         patch("openai.OpenAI", lambda **_: _Boom()):
        assert asyncio.run(ws.search_answer("anything")) is None


def test_the_limiter_survives_a_second_event_loop():
    """
    An asyncio.Semaphore binds to the loop that first awaits it. A plain
    module-level singleton works in the server (one long-lived loop) and breaks
    in anything calling asyncio.run twice — which is how this surfaced.
    """
    response = _response("text")
    assert _run(response) is not None
    assert _run(response) is not None      # different loop, must not raise
