"""
app/memory/document_store.py
===============================
Chunking, embedding and retrieval for company documents — the store Stage 1
reads to answer the 18 qualitative questions.

WHY A SEPARATE QDRANT COLLECTION:
-----------------------------------
qdrant_store.py already runs a collection called `trade_memories`, whose
vectors are 12-dimensional numbers hand-built from market conditions
(memory/embedder.py). These are text embeddings, typically 768-dimensional.
Qdrant fixes vector size at collection creation, so they cannot share one —
and conceptually should not: one is episodic trade memory with a short life,
the other is a document corpus refreshed once a year.

WHY A HOSTED EMBEDDING API RATHER THAN A LOCAL MODEL:
-------------------------------------------------------
Running an embedding model locally means either an LLM runtime this machine
cannot support, or sentence-transformers pulling ~2 GB of torch into the
backend image. Neither is warranted, because the cost that supposedly justified
them is not real: embedding the entire watchlist is a one-time ~5.5M tokens
(a 187-page annual report is ~285k), which is cents at text-embedding-3-small
rates, and re-ingesting an already-stored document is a no-op.

The client is OpenAI-compatible either way. Set OPENAI_API_KEY for the hosted
API, or point LOCAL_MODEL_BASE_URL at a local endpoint — no code changes.

WHY CHUNKS NEVER SPAN PAGES:
------------------------------
Every Stage 1 answer must cite a page. A chunk covering the end of p.141 and
the start of p.142 cannot honestly cite either, and a citation that is not
checkable is not evidence. So pages are chunked independently, and each chunk
carries its page number in the payload.

IDEMPOTENCE:
--------------
Point ids are a uuid5 of (doc_id, chunk index), so re-ingesting the same
document overwrites its own points rather than duplicating them. Ingesting the
same annual report twice is a no-op, which matters because the scheduled job
will keep seeing documents it has already read.

USAGE:
------
    from app.memory.document_store import ingest_document, retrieve

    await ingest_document(ref, pages)
    hits = await retrieve("RELIANCE", "how many manufacturing plants", top_k=5)
    hits[0]["page"], hits[0]["text"], hits[0]["doc_type"]
"""

import asyncio
import uuid

from loguru import logger

from app.core.config import settings

_DOCUMENT_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


# ============================================================
# COLLECTION
# ============================================================

def init_document_collection() -> None:
    """
    Create the document collection if absent. Safe to call repeatedly; call it
    from runtime.lifespan alongside init_collection().
    """
    from qdrant_client.models import VectorParams, Distance
    from app.memory.qdrant_store import get_qdrant_client

    client = get_qdrant_client()
    existing = [c.name for c in client.get_collections().collections]

    if settings.QDRANT_DOCUMENTS_COLLECTION in existing:
        return

    client.create_collection(
        collection_name=settings.QDRANT_DOCUMENTS_COLLECTION,
        vectors_config=VectorParams(
            size=settings.EMBEDDING_DIMENSION,
            distance=Distance.COSINE,
        ),
    )
    logger.info(
        f"Qdrant collection '{settings.QDRANT_DOCUMENTS_COLLECTION}' created "
        f"| dimensions={settings.EMBEDDING_DIMENSION} | model={settings.EMBEDDING_MODEL_NAME}"
    )


# ============================================================
# CHUNKING
# ============================================================

def chunk_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """
    [(page, text)] -> [(page, chunk)], never crossing a page boundary.

    Overlap is applied within a page so a sentence split across two chunks is
    still retrievable from at least one of them.
    """
    size = settings.DOCUMENT_CHUNK_CHARS
    overlap = settings.DOCUMENT_CHUNK_OVERLAP
    chunks: list[tuple[int, str]] = []

    for page_number, text in pages:
        text = " ".join(text.split())      # collapse PDF whitespace
        if not text:
            continue
        if len(text) <= size:
            chunks.append((page_number, text))
            continue

        start = 0
        while start < len(text):
            piece = text[start:start + size]
            if piece.strip():
                chunks.append((page_number, piece.strip()))
            if start + size >= len(text):
                break
            start += size - overlap

    return chunks


# ============================================================
# EMBEDDING
# ============================================================

async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed via Ollama's OpenAI-compatible endpoint.

    Raises if the model is unreachable. Ingestion is a background job, so
    failing loudly is right here — silently storing nothing would look like a
    company with no annual report.
    """
    # LOCAL_MODEL_BASE_URL wins when set, so a local endpoint can be swapped in
    # without touching code. Otherwise the hosted OpenAI API is used.
    use_local = bool(settings.LOCAL_MODEL_BASE_URL)
    api_key = (settings.LOCAL_MODEL_API_KEY or "local") if use_local else settings.OPENAI_API_KEY

    if not api_key:
        raise RuntimeError(
            "No embedding endpoint configured — set OPENAI_API_KEY, or point "
            "LOCAL_MODEL_BASE_URL at a local OpenAI-compatible endpoint."
        )

    from openai import OpenAI

    def _call() -> list[list[float]]:
        client = OpenAI(
            api_key=api_key,
            base_url=settings.LOCAL_MODEL_BASE_URL or None,
            timeout=120.0,
        )
        response = client.embeddings.create(
            model=settings.EMBEDDING_MODEL_NAME, input=texts
        )
        return [item.embedding for item in response.data]

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _call)
    except Exception as e:
        # The two failures worth naming, because both look like a config bug
        # and are actually a missing model:
        #   404 "model not found"        -> the embedding model isn't pulled
        #   501 "does not support embeddings" -> a CHAT model was configured;
        #        general LLMs cannot serve /v1/embeddings, only embedding models can
        message = str(e)
        # Both of these read like a server fault and are actually the wrong
        # model configured. A CHAT model cannot serve /v1/embeddings — observed
        # live as a 501 from a local endpoint, and as a 404 when the named
        # embedding model was simply absent.
        if "not found" in message or "does not support embeddings" in message:
            raise RuntimeError(
                f"Embedding model '{settings.EMBEDDING_MODEL_NAME}' is unavailable "
                f"({'local endpoint' if settings.LOCAL_MODEL_BASE_URL else 'OpenAI'}). "
                f"It must be an EMBEDDING model, not a chat model. Set "
                f"EMBEDDING_MODEL_NAME to text-embedding-3-small, and make sure "
                f"EMBEDDING_DIMENSION matches it (3-small=1536, 3-large=3072, "
                f"nomic-embed-text=768)."
            ) from e
        raise


# ============================================================
# INGEST
# ============================================================

async def ingest_document(ref, pages: list[tuple[int, str]], batch_size: int = 64) -> int:
    """
    Chunk, embed and upsert one document. Returns the number of chunks stored.

    Re-ingesting the same document overwrites its own points (deterministic
    ids), so this is safe to call on every scheduled run.
    """
    from qdrant_client.models import PointStruct
    from app.memory.qdrant_store import get_qdrant_client

    chunks = chunk_pages(pages)
    if not chunks:
        logger.warning(f"{ref.doc_id}: nothing to ingest (no extractable text)")
        return 0

    client = get_qdrant_client()
    loop = asyncio.get_event_loop()
    stored = 0

    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset:offset + batch_size]
        vectors = await embed_texts([text for _, text in batch])

        points = [
            PointStruct(
                id=str(uuid.uuid5(_DOCUMENT_NAMESPACE, f"{ref.doc_id}:{offset + i}")),
                vector=vector,
                payload={
                    "symbol": ref.symbol,
                    "doc_id": ref.doc_id,
                    "doc_type": ref.doc_type.value,
                    "period": ref.period,
                    "title": ref.title,
                    "url": ref.url,
                    "page": page,
                    "text": text,
                },
            )
            for i, ((page, text), vector) in enumerate(zip(batch, vectors))
        ]

        await loop.run_in_executor(
            None,
            lambda p=points: client.upsert(
                collection_name=settings.QDRANT_DOCUMENTS_COLLECTION, points=p
            ),
        )
        stored += len(points)

    logger.info(f"{ref.doc_id}: stored {stored} chunks from {len(pages)} pages")
    return stored


# ============================================================
# RETRIEVE
# ============================================================

async def retrieve(
    symbol: str,
    query: str,
    top_k: int = 5,
    doc_types: list[str] | None = None,
) -> list[dict]:
    """
    Nearest chunks for one question, filtered to one company.

    The symbol filter is not an optimisation — without it a question about one
    company can be answered from another company's annual report, which is the
    worst possible failure here: a confident, well-cited, wrong answer.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
    from app.memory.qdrant_store import get_qdrant_client

    vector = (await embed_texts([query]))[0]

    conditions = [FieldCondition(key="symbol", match=MatchValue(value=symbol))]
    if doc_types:
        conditions.append(FieldCondition(key="doc_type", match=MatchAny(any=doc_types)))

    client = get_qdrant_client()
    loop = asyncio.get_event_loop()

    def _search():
        return client.query_points(
            collection_name=settings.QDRANT_DOCUMENTS_COLLECTION,
            query=vector,
            query_filter=Filter(must=conditions),
            limit=top_k,
            with_payload=True,
        ).points

    hits = await loop.run_in_executor(None, _search)

    return [
        {
            "score": hit.score,
            "text": hit.payload.get("text", ""),
            "page": hit.payload.get("page"),
            "doc_type": hit.payload.get("doc_type"),
            "period": hit.payload.get("period"),
            "url": hit.payload.get("url"),
            "citation": _citation(hit.payload),
        }
        for hit in hits
    ]


def _citation(payload: dict) -> str:
    """"AR FY2026, p.142" — the string that travels with a Stage 1 answer."""
    doc_type = payload.get("doc_type", "")
    period = payload.get("period") or "?"
    page = payload.get("page")
    label = {
        "ANNUAL_REPORT": f"AR FY{period}",
        "CONCALL": f"Concall {period}",
        "CREDIT_RATING": f"Credit rating {period}",
    }.get(doc_type, f"{doc_type} {period}")
    return f"{label}, p.{page}" if page else label
