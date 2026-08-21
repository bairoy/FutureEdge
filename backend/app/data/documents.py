"""
app/data/documents.py
========================
Discovery, download and text extraction for the primary-source documents that
Stage 1 reads: annual reports, concall transcripts and credit rating reports.

WHY THIS EXISTS:
------------------
Most of the 18 Stage 1 questions ("how many plants and where", "who are the
auditors", "how many subsidiaries") are not in any ratio table — they are in
the annual report. That was originally taken to mean a human had to read it.
It does not: Screener's #documents section already links BSE-hosted annual
report PDFs, and those PDFs carry real extractable text rather than scans.

Verified 2026-08-19 against RELIANCE FY2026: 187 pages, 11 MB, 4-10k characters
of text per page.

WHY EXTRACTION IS PAGE-BY-PAGE:
---------------------------------
Every Stage 1 answer has to be citable — "AR FY2026, p.142" — because a
qualitative claim without a source is not evidence. Keeping the page number
with the text is the only way the citation survives to the other end.

WHY THIS IS NOT PART OF AN ANALYSIS RUN:
------------------------------------------
A 187-page PDF takes tens of seconds to fetch and extract, and longer to embed.
An annual report is published once a year. So ingestion is a scheduled job that
runs once per document; analysis runs query what it left in Qdrant. That is
what keeps "read the whole annual report" compatible with a fast analysis.

BE A GOOD CITIZEN:
--------------------
These are 10 MB+ files on BSE's servers. The same courtesy throttle the
Screener scraper uses applies here, with a separate lock (different host).

USAGE:
------
    from app.data.documents import discover_documents, download_and_extract

    refs = await discover_documents("RELIANCE")
    pages = await download_and_extract(refs[0])   # [(page_no, text), ...]
"""

import asyncio
import re
from dataclasses import dataclass
from enum import Enum

import httpx
from bs4 import BeautifulSoup
from loguru import logger

SCREENER_BASE_URL = "https://www.screener.in"

REQUEST_TIMEOUT = 90.0          # annual reports are large
MIN_REQUEST_GAP_SECONDS = 3.0
_last_request_at: float = 0.0
_request_lock = asyncio.Lock()

# A PDF yielding less than this per page is almost certainly a scan.
MIN_CHARS_PER_PAGE_FOR_TEXT = 100

HEADERS = {
    "User-Agent": "FutureEdge-FundamentalAgent/1.0 (personal research tool)",
    "Accept": "text/html,application/pdf",
}


class DocType(str, Enum):
    ANNUAL_REPORT = "ANNUAL_REPORT"
    CONCALL = "CONCALL"
    CREDIT_RATING = "CREDIT_RATING"
    ANNOUNCEMENT = "ANNOUNCEMENT"


@dataclass
class DocumentRef:
    symbol: str
    doc_type: DocType
    title: str
    url: str
    period: str | None = None      # "2026" for an annual report, "Jul 2026" for a concall

    @property
    def doc_id(self) -> str:
        """Stable id, so re-ingesting the same document is a no-op."""
        return f"{self.symbol}:{self.doc_type.value}:{self.period or self.title}"[:200]


# ============================================================
# DISCOVERY
# ============================================================

# Each block under #documents carries a type class alongside "documents".
_SECTION_CLASSES = {
    "annual-reports": DocType.ANNUAL_REPORT,
    "concalls": DocType.CONCALL,
    "credit-ratings": DocType.CREDIT_RATING,
}


async def discover_documents(symbol: str, doc_types: list[DocType] | None = None) -> list[DocumentRef]:
    """
    List the documents Screener links for a symbol, newest first within each type.

    Announcements are deliberately excluded by default: there are dozens per
    year, most are routine, and the ones that matter for Stage 1 (SEBI, legal)
    are better found through the regulator than by reading every filing.
    """
    html = await _fetch(f"{SCREENER_BASE_URL}/company/{symbol}/consolidated/", as_bytes=False)
    soup = BeautifulSoup(html, "lxml")
    container = soup.find(id="documents")
    if not container:
        logger.warning(f"No #documents section for {symbol} — Screener markup may have changed")
        return []

    wanted = doc_types or [DocType.ANNUAL_REPORT, DocType.CONCALL, DocType.CREDIT_RATING]
    refs: list[DocumentRef] = []

    for block in container.find_all(class_="documents"):
        classes = block.get("class") or []
        doc_type = next((t for cls, t in _SECTION_CLASSES.items() if cls in classes), None)
        if doc_type is None or doc_type not in wanted:
            continue

        for li in block.find_all("li"):
            ref = _parse_document_item(symbol, doc_type, li)
            if ref:
                refs.append(ref)

    logger.info(f"Discovered {len(refs)} documents for {symbol}")
    return refs


def _parse_document_item(symbol: str, doc_type: DocType, li) -> DocumentRef | None:
    text = li.get_text(" ", strip=True)

    # A concall row links several artefacts (Transcript, AI Summary, PPT).
    # Only the transcript is worth ingesting — the summary is someone else's
    # interpretation, and the PPT is mostly charts.
    if doc_type is DocType.CONCALL:
        link = next((a for a in li.find_all("a", href=True)
                     if "transcript" in a.get_text(strip=True).lower()), None)
    else:
        link = li.find("a", href=True)

    if not link:
        return None

    url = link["href"]
    if url.startswith("/"):
        return None      # an internal Screener page, not a document

    return DocumentRef(
        symbol=symbol, doc_type=doc_type, title=text[:200],
        url=url, period=_extract_period(doc_type, text),
    )


def _extract_period(doc_type: DocType, text: str) -> str | None:
    if doc_type is DocType.ANNUAL_REPORT:
        match = re.search(r"Financial Year (\d{4})", text)
        return match.group(1) if match else None
    match = re.search(r"([A-Z][a-z]{2} \d{4})", text)
    return match.group(1) if match else None


# ============================================================
# DOWNLOAD + EXTRACT
# ============================================================

async def download_and_extract(ref: DocumentRef) -> list[tuple[int, str]]:
    """
    Fetch a document and return [(page_number, text), ...], 1-indexed.

    Returns an empty list rather than raising when the document is unreachable
    or is a scan — losing one document degrades Stage 1, it does not fail the
    analysis. A scanned PDF is logged explicitly so the reason is visible
    rather than looking like an empty annual report.
    """
    try:
        content = await _fetch(ref.url, as_bytes=True)
    except Exception as e:
        logger.warning(f"Could not download {ref.doc_id}: {e}")
        return []

    if not content[:5].startswith(b"%PDF"):
        logger.warning(f"{ref.doc_id} is not a PDF — skipping")
        return []

    return _extract_pdf_pages(content, ref.doc_id)


def _extract_pdf_pages(content: bytes, doc_id: str) -> list[tuple[int, str]]:
    import io
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as e:
        logger.warning(f"Could not parse {doc_id} as a PDF: {e}")
        return []

    pages: list[tuple[int, str]] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            pages.append((number, text))

    if not pages:
        logger.warning(f"{doc_id}: no extractable text — almost certainly a scanned PDF, needs OCR")
        return []

    avg = sum(len(t) for _, t in pages) / len(pages)
    if avg < MIN_CHARS_PER_PAGE_FOR_TEXT:
        logger.warning(
            f"{doc_id}: only {avg:.0f} chars/page across {len(pages)} pages — "
            f"likely a partial scan; treat coverage as incomplete"
        )

    logger.info(f"{doc_id}: extracted {len(pages)} pages ({avg:.0f} chars/page)")
    return pages


# ============================================================
# HTTP
# ============================================================

async def _fetch(url: str, as_bytes: bool):
    """Throttled fetch. The gap is held across every call in this module, so
    discovery and bulk PDF downloads share one courtesy budget."""
    global _last_request_at

    async with _request_lock:
        now = asyncio.get_event_loop().time()
        wait = MIN_REQUEST_GAP_SECONDS - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)

        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True
        ) as client:
            response = await client.get(url)

        _last_request_at = asyncio.get_event_loop().time()
        response.raise_for_status()
        return response.content if as_bytes else response.text
