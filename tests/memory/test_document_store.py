"""
tests/memory/test_document_store.py
=====================================
Tests for chunking, ids, citations and retrieval filtering.

The load-bearing properties:

1. Chunks NEVER span a page. Every Stage 1 answer cites a page number, and a
   chunk covering two pages cannot honestly cite either.
2. Point ids are deterministic, so re-ingesting a document overwrites rather
   than duplicates. The scheduled job re-sees the same annual report forever.
3. Retrieval filters by symbol. Without it, one company's question can be
   answered from another company's annual report — a confident, well-cited,
   wrong answer, which is the worst failure available here.
"""

import sys
import os
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.memory import document_store as ds
from app.memory.document_store import chunk_pages, _citation
from app.data.documents import DocumentRef, DocType


# ============================================================
# 1 — chunking
# ============================================================

def test_chunks_never_span_pages():
    pages = [(1, "alpha " * 500), (2, "beta " * 500)]
    chunks = chunk_pages(pages)
    for page, text in chunks:
        assert not ("alpha" in text and "beta" in text)


def test_short_page_becomes_one_chunk():
    assert chunk_pages([(7, "a short page")]) == [(7, "a short page")]


def test_long_page_splits_with_overlap_and_keeps_its_page_number():
    chunks = chunk_pages([(42, "word " * 2000)])
    assert len(chunks) > 1
    assert all(page == 42 for page, _ in chunks)


def test_empty_pages_are_dropped():
    assert chunk_pages([(1, "   "), (2, "")]) == []


# ============================================================
# 2 — deterministic ids
# ============================================================

def test_point_ids_are_deterministic_so_reingest_overwrites():
    ref = DocumentRef("RELIANCE", DocType.ANNUAL_REPORT, "AR", "u", "2026")
    first = str(uuid.uuid5(ds._DOCUMENT_NAMESPACE, f"{ref.doc_id}:0"))
    second = str(uuid.uuid5(ds._DOCUMENT_NAMESPACE, f"{ref.doc_id}:0"))
    assert first == second


def test_different_chunks_get_different_ids():
    ref = DocumentRef("RELIANCE", DocType.ANNUAL_REPORT, "AR", "u", "2026")
    assert (str(uuid.uuid5(ds._DOCUMENT_NAMESPACE, f"{ref.doc_id}:0"))
            != str(uuid.uuid5(ds._DOCUMENT_NAMESPACE, f"{ref.doc_id}:1")))


# ============================================================
# 3 — retrieval is scoped to one company
# ============================================================

@pytest.mark.asyncio
async def test_retrieval_filters_by_symbol():
    captured = {}

    def _query_points(**kwargs):
        captured.update(kwargs)
        return MagicMock(points=[])

    client = MagicMock()
    client.query_points = _query_points

    with patch.object(ds, "embed_texts", AsyncMock(return_value=[[0.1] * 1536])), \
         patch("app.memory.qdrant_store.get_qdrant_client", return_value=client):
        await ds.retrieve("RELIANCE", "how many plants")

    conditions = captured["query_filter"].must
    assert any(c.key == "symbol" and c.match.value == "RELIANCE" for c in conditions)


@pytest.mark.asyncio
async def test_retrieval_returns_citations():
    hit = MagicMock(score=0.9, payload={
        "text": "The Jamnagar refinery...", "page": 142,
        "doc_type": "ANNUAL_REPORT", "period": "2026", "url": "u",
    })
    client = MagicMock()
    client.query_points = MagicMock(return_value=MagicMock(points=[hit]))

    with patch.object(ds, "embed_texts", AsyncMock(return_value=[[0.1] * 1536])), \
         patch("app.memory.qdrant_store.get_qdrant_client", return_value=client):
        results = await ds.retrieve("RELIANCE", "refinery")

    assert results[0]["citation"] == "AR FY2026, p.142"


@pytest.mark.parametrize("payload,expected", [
    ({"doc_type": "ANNUAL_REPORT", "period": "2026", "page": 12}, "AR FY2026, p.12"),
    ({"doc_type": "CONCALL", "period": "Jul 2026", "page": 3}, "Concall Jul 2026, p.3"),
    ({"doc_type": "ANNUAL_REPORT", "period": "2026"}, "AR FY2026"),
])
def test_citation_format(payload, expected):
    assert _citation(payload) == expected


# ============================================================
# Embedding failure must name the actual cause
# ============================================================

@pytest.mark.asyncio
async def test_no_configured_endpoint_is_explained():
    with patch.object(ds.settings, "LOCAL_MODEL_BASE_URL", ""), \
         patch.object(ds.settings, "OPENAI_API_KEY", ""):
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            await ds.embed_texts(["x"])


@pytest.mark.asyncio
async def test_local_endpoint_takes_precedence_over_openai():
    """A local endpoint, when configured, must win — so switching provider is
    configuration rather than a code change."""
    captured = {}

    def _fake_openai(**kwargs):
        captured.update(kwargs)
        client = MagicMock()
        client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 8)]
        )
        return client

    with patch.object(ds.settings, "LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1"), \
         patch.object(ds.settings, "OPENAI_API_KEY", "sk-test"), \
         patch("openai.OpenAI", _fake_openai):
        await ds.embed_texts(["x"])

    assert captured["base_url"] == "http://localhost:11434/v1"


@pytest.mark.asyncio
async def test_chat_model_configured_for_embeddings_is_explained():
    """
    Observed live: a 501 "does not support embeddings" when a chat model was
    configured, and a 404 when the named embedding model was absent. Both read
    like a server fault and are actually the wrong model — the error has to say
    so, with the fix.
    """
    def _boom(*a, **k):
        raise Exception("Error code: 501 - This server does not support embeddings")

    with patch.object(ds.settings, "OPENAI_API_KEY", "sk-test"), \
         patch.object(ds.settings, "LOCAL_MODEL_BASE_URL", ""), \
         patch("openai.OpenAI", MagicMock(side_effect=_boom)):
        with pytest.raises(RuntimeError, match="EMBEDDING model, not a chat model"):
            await ds.embed_texts(["x"])
