"""
app/jobs/document_ingestion.py
=================================
Scheduled ingestion of company documents into the Qdrant document store.

WHY THIS IS A JOB AND NOT PART OF AN ANALYSIS RUN:
----------------------------------------------------
A single annual report is ~190 pages and takes tens of seconds to fetch and
extract, plus embedding time. An annual report is published once a year, and a
concall transcript once a quarter. Doing this inside a run the user is waiting
on would make every analysis slow in order to re-read a document that has not
changed since the last one.

So: ingest once, out of band. Analysis runs query what this left behind, which
is a vector search measured in milliseconds.

HOW MUCH IT INGESTS, AND WHY NOT MORE:
----------------------------------------
The most recent 2 annual reports and 4 concalls per symbol by default. Stage 1
asks what the business is like NOW — a 2019 annual report mostly adds noise to
retrieval, and every extra document is a large download from BSE. Older filings
are better fetched deliberately when a specific question needs history.

FAILURE POLICY:
-----------------
One document failing (a scan with no text, a dead BSE link) skips that document
and continues. The whole job failing on one bad PDF would mean a single
scanned annual report blocks an entire watchlist.

USAGE:
------
    from app.jobs.document_ingestion import ingest_symbol, ingest_watchlist

    await ingest_symbol("RELIANCE")
"""

from loguru import logger

from app.core.config import settings
from app.data.documents import discover_documents, download_and_extract, DocType
from app.memory.document_store import ingest_document, init_document_collection

# Recency caps per document type — see the module docstring.
DEFAULT_LIMITS = {
    DocType.ANNUAL_REPORT: 2,
    DocType.CONCALL: 4,
    DocType.CREDIT_RATING: 1,
}


async def ingest_symbol(symbol: str, limits: dict | None = None) -> dict:
    """
    Ingest the recent documents for one symbol.

    Returns a per-type summary of chunks stored, plus anything skipped and why,
    so a symbol that silently ingested nothing is distinguishable from one with
    no documents to ingest.
    """
    limits = limits or DEFAULT_LIMITS
    summary = {"symbol": symbol, "documents": 0, "chunks": 0, "skipped": []}

    try:
        init_document_collection()
    except Exception as e:
        logger.error(f"Document collection unavailable — cannot ingest {symbol}: {e}")
        summary["skipped"].append(f"collection unavailable: {e}")
        return summary

    refs = await discover_documents(symbol)
    if not refs:
        summary["skipped"].append("no documents linked on Screener")
        return summary

    # Screener lists newest first within each block, so a head slice is the
    # most recent N.
    selected = []
    for doc_type, limit in limits.items():
        selected.extend([r for r in refs if r.doc_type is doc_type][:limit])

    for ref in selected:
        try:
            pages = await download_and_extract(ref)
            if not pages:
                summary["skipped"].append(f"{ref.doc_id}: no extractable text (likely a scan)")
                continue

            stored = await ingest_document(ref, pages)
            summary["documents"] += 1
            summary["chunks"] += stored

        except Exception as e:
            # One bad document must not take the symbol down with it.
            logger.warning(f"Skipping {ref.doc_id}: {e}")
            summary["skipped"].append(f"{ref.doc_id}: {e}")

    logger.info(
        f"Ingestion complete | {symbol} | {summary['documents']} documents, "
        f"{summary['chunks']} chunks, {len(summary['skipped'])} skipped"
    )
    return summary


async def ingest_watchlist() -> list[dict]:
    """Ingest every symbol on the investing watchlist. Intended for a monthly
    scheduler job — annual reports and transcripts do not appear faster."""
    symbols = [
        s.strip()
        for s in getattr(settings, "INVESTING_WATCHLIST_SYMBOLS", "").split(",")
        if s.strip()
    ]
    if not symbols:
        logger.warning("INVESTING_WATCHLIST_SYMBOLS is empty — nothing to ingest")
        return []

    return [await ingest_symbol(symbol) for symbol in symbols]
