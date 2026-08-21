"""
tests/data/test_documents.py
==============================
Tests for document discovery and PDF text extraction.

What matters here:

1. Concall rows link several artefacts (Transcript, AI Summary, PPT). Only the
   transcript is a primary source — the AI summary is someone else's reading.
2. A scanned PDF must be reported as such, not ingested as an empty document.
   "No extractable text" and "a company with nothing to say" look identical
   downstream unless this is caught here.
3. Internal Screener links in the announcements block are not documents.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from bs4 import BeautifulSoup
from app.data import documents as docs
from app.data.documents import DocType, DocumentRef, _parse_document_item, _extract_period


_DOCS_HTML = """
<section id="documents">
  <div class="documents annual-reports flex-column">
    <h3>Annual reports</h3>
    <ul>
      <li><a href="https://bse.example/ar2026.pdf">Financial Year 2026 from bse</a></li>
      <li><a href="https://bse.example/ar2025.pdf">Financial Year 2025 from bse</a></li>
    </ul>
  </div>
  <div class="documents concalls flex-column">
    <h3>Concalls</h3>
    <ul>
      <li>Jul 2026
        <a href="https://bse.example/transcript.pdf">Transcript</a>
        <a href="https://ai.example/summary">AI Summary</a>
        <a href="https://bse.example/ppt.pdf">PPT</a>
      </li>
    </ul>
  </div>
  <div class="documents flex-column">
    <h3>Announcements</h3>
    <ul><li><a href="/company/id/2726/">Clarification sought</a></li></ul>
  </div>
</section>
"""


def _items(css_class):
    block = BeautifulSoup(_DOCS_HTML, "lxml").find(class_=css_class)
    return block.find_all("li")


def test_annual_report_period_is_extracted():
    ref = _parse_document_item("RELIANCE", DocType.ANNUAL_REPORT, _items("annual-reports")[0])
    assert ref.period == "2026"
    assert ref.url.endswith("ar2026.pdf")


def test_concall_picks_the_transcript_not_the_ai_summary():
    """
    The AI summary is another party's interpretation and the PPT is mostly
    charts. Ingesting either instead of the transcript would put second-hand
    reasoning behind a citation that claims to be a primary source.
    """
    ref = _parse_document_item("RELIANCE", DocType.CONCALL, _items("concalls")[0])
    assert ref.url == "https://bse.example/transcript.pdf"
    assert ref.period == "Jul 2026"


def test_internal_screener_links_are_not_documents():
    block = BeautifulSoup(_DOCS_HTML, "lxml").find_all(class_="documents")[-1]
    assert _parse_document_item("RELIANCE", DocType.ANNOUNCEMENT, block.find("li")) is None


@pytest.mark.parametrize("doc_type,text,expected", [
    (DocType.ANNUAL_REPORT, "Financial Year 2024 from bse", "2024"),
    (DocType.CONCALL, "Jan 2026 Transcript", "Jan 2026"),
    (DocType.ANNUAL_REPORT, "no year here", None),
])
def test_period_extraction(doc_type, text, expected):
    assert _extract_period(doc_type, text) == expected


def test_doc_id_is_stable_for_the_same_document():
    """Ingestion is keyed on this — an unstable id duplicates every re-run."""
    a = DocumentRef("RELIANCE", DocType.ANNUAL_REPORT, "Financial Year 2026", "u", "2026")
    b = DocumentRef("RELIANCE", DocType.ANNUAL_REPORT, "different title", "other", "2026")
    assert a.doc_id == b.doc_id


def test_scanned_pdf_reports_no_text_rather_than_an_empty_document():
    """
    A PDF of page images extracts nothing. Returning [] with a warning keeps it
    distinguishable from a document that was read and had nothing in it.
    """
    class _BlankPage:
        def extract_text(self): return ""

    class _FakeReader:
        pages = [_BlankPage(), _BlankPage()]

    import app.data.documents as mod
    original = mod._extract_pdf_pages

    def _patched(content, doc_id):
        import sys as _s
        _s.modules["pypdf"] = type("m", (), {"PdfReader": lambda *_a, **_k: _FakeReader()})
        try:
            return original(content, doc_id)
        finally:
            del _s.modules["pypdf"]

    assert _patched(b"%PDF-fake", "TEST:AR:2026") == []


def test_non_pdf_content_is_skipped():
    assert docs._extract_pdf_pages(b"<html>not a pdf</html>", "TEST:AR:2026") == []
