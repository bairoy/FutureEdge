"""
app/memory/qdrant_store.py
===========================
Qdrant vector database client for episodic memory.

WHAT IS EPISODIC MEMORY?
--------------------------
After every completed trade, we save a "memory" containing:
  - the market conditions at the time (regime, RSI, MACD, sentiment)
  - what the agents decided (LONG/SHORT/NONE)
  - what actually happened (WIN / LOSS, PnL %)

At the start of each new cycle, we retrieve the top-K most
SIMILAR past situations. The orchestrator reads these and
can say "last 3 times we saw this exact pattern, we lost —
maybe we should be more cautious."

WHY QDRANT?
-----------
Qdrant is a vector database. It stores data as numerical vectors
(lists of floats) and can find the most similar vectors very fast.

"Similar" means similar MARKET CONDITIONS — not just the same symbol.
A RELIANCE trade in a HIGH_VOLATILITY regime after an RSI spike
will be matched against ALL similar situations across any symbol.

HOW THE VECTOR IS BUILT:
-------------------------
We represent each trade situation as a 12-dimensional vector:
  [rsi_norm, macd_norm, bollinger_pos, volatility_norm,
   sentiment_score, regime_encoded (4 values),
   confidence, buy_score, sell_score]

Two situations with similar market conditions → similar vectors
→ high cosine similarity → retrieved as relevant memories.

SETUP:
------
Qdrant runs in Docker. No API key needed for self-hosted.
Collection is created automatically on first startup.

.env:
    QDRANT_HOST=qdrant
    QDRANT_PORT=6333
    QDRANT_COLLECTION=trade_memories
"""

from loguru import logger

from app.core.config import settings

# ============================================================
# VECTOR DIMENSION
# ============================================================
# Our market snapshot vector has 12 dimensions.
# Must match what embedder.py produces.
VECTOR_DIMENSION = 12


# ============================================================
# GET QDRANT CLIENT
# ============================================================

def get_qdrant_client():
    """
    Returns a Qdrant client connected to our local instance.

    We create a new client per call because QdrantClient is
    synchronous. We call it from async code using run_in_executor.
    """

    from qdrant_client import QdrantClient

    return QdrantClient(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
    )


# ============================================================
# INITIALISE COLLECTION
# ============================================================

def init_collection() -> None:
    """
    Create the Qdrant collection if it does not exist.

    Called once at app startup (from runtime.lifespan).
    Safe to call multiple times — skips if collection exists.

    Collection settings:
      - vectors: 12-dimensional floats
      - distance: Cosine similarity (good for normalised vectors)
    """

    from qdrant_client.models import VectorParams, Distance

    client = get_qdrant_client()

    # Check if collection already exists
    existing = [c.name for c in client.get_collections().collections]

    if settings.QDRANT_COLLECTION in existing:
        logger.info(
            f"Qdrant collection '{settings.QDRANT_COLLECTION}' already exists"
        )
        return

    client.create_collection(
        collection_name = settings.QDRANT_COLLECTION,
        vectors_config  = VectorParams(
            size     = VECTOR_DIMENSION,
            distance = Distance.COSINE,
        ),
    )

    logger.info(
        f"Qdrant collection '{settings.QDRANT_COLLECTION}' created "
        f"| dimensions={VECTOR_DIMENSION} | distance=Cosine"
    )


# ============================================================
# STORE A TRADE MEMORY
# ============================================================

async def store_trade_memory(
    run_id:         str,
    user_id:        str,
    symbol:         str,
    direction:      str,          # LONG | SHORT | NONE
    vector:         list[float],  # 12-dim market snapshot vector
    outcome:        str,          # WIN | LOSS | NEUTRAL
    pnl_pct:        float,        # realised PnL percentage
    regime:         str,          # TRENDING_UP | SIDEWAYS | etc.
    agent_votes:    dict,         # {"SignalAgent": "BUY", ...}
    risk_score:     float,
) -> None:
    """
    Save a completed trade as a memory point in Qdrant.

    Called by execution_agent.py after a trade is placed.
    The memory is stored immediately — before we know the outcome.

    For live trades, a separate job updates the outcome once
    the trade closes (WIN/LOSS determined from PnL).

    Parameters:
    -----------
    run_id      : the workflow run_id (links back to workflow_runs table)
    user_id     : which user made this trade
    symbol      : e.g. "NIFTY 50" or "RELIANCE"
    direction   : what the system decided (LONG/SHORT/NONE)
    vector      : 12-float market embedding from embedder.py
    outcome     : WIN | LOSS | NEUTRAL (updated later when trade closes)
    pnl_pct     : profit/loss as a percentage
    regime      : market regime at the time
    agent_votes : which agents voted what
    risk_score  : orchestrator risk score
    """

    import asyncio
    from qdrant_client.models import PointStruct
    import uuid as _uuid

    # We run Qdrant client in executor because it is synchronous
    def _store():
        client = get_qdrant_client()

        point = PointStruct(
            id      = str(_uuid.uuid4()),   # unique point ID
            vector  = vector,
            payload = {
                # Identifiers
                "run_id":     run_id,
                "user_id":    user_id,
                "symbol":     symbol,

                # Decision context
                "direction":  direction,
                "regime":     regime,
                "risk_score": risk_score,

                # Agent votes as flat dict for easy filtering
                "agent_votes": agent_votes,

                # Outcome — updated when trade closes
                "outcome":    outcome,
                "pnl_pct":    pnl_pct,
            },
        )

        client.upsert(
            collection_name = settings.QDRANT_COLLECTION,
            points          = [point],
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _store)

    logger.info(
        f"Memory stored | run_id={run_id} | {symbol} {direction} | "
        f"regime={regime} | outcome={outcome}"
    )


# ============================================================
# RETRIEVE SIMILAR MEMORIES
# ============================================================

async def retrieve_similar_memories(
    vector:   list[float],
    symbol:   str,
    limit:    int = 5,
) -> list[dict]:
    """
    Find the top-K most similar past trade situations.

    Given the current market conditions as a vector,
    returns the most similar past memories from Qdrant.

    These are injected into the orchestrator's context so it
    can learn: "last time we saw this exact setup, we lost
    because RSI was overbought — be cautious."

    Parameters:
    -----------
    vector  : current market snapshot vector (12 floats)
    symbol  : filter to same symbol only (avoid cross-contamination)
    limit   : how many memories to return (default: 5)

    Returns:
    --------
    List of memory dicts, each containing:
    {
        "symbol":     "NIFTY 50",
        "direction":  "LONG",
        "regime":     "TRENDING_UP",
        "outcome":    "WIN",
        "pnl_pct":    1.2,
        "risk_score": 0.45,
        "similarity": 0.91,
        "agent_votes": {"SignalAgent": "BUY", ...}
    }
    """

    import asyncio

    def _retrieve():
        client = get_qdrant_client()

        # Search with a filter to only return memories for
        # this specific symbol (avoids matching RELIANCE patterns
        # when we are trading NIFTY 50)
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        results = client.search(
            collection_name = settings.QDRANT_COLLECTION,
            query_vector    = vector,
            query_filter    = Filter(
                must=[
                    FieldCondition(
                        key   = "symbol",
                        match = MatchValue(value=symbol),
                    )
                ]
            ),
            limit           = limit,
            with_payload    = True,
        )

        memories = []
        for hit in results:
            payload = hit.payload or {}
            memories.append({
                "symbol":      payload.get("symbol", ""),
                "direction":   payload.get("direction", ""),
                "regime":      payload.get("regime", ""),
                "outcome":     payload.get("outcome", "UNKNOWN"),
                "pnl_pct":     payload.get("pnl_pct", 0.0),
                "risk_score":  payload.get("risk_score", 0.0),
                "agent_votes": payload.get("agent_votes", {}),
                "similarity":  round(hit.score, 4),
            })

        return memories

    loop = asyncio.get_event_loop()

    try:
        memories = await loop.run_in_executor(None, _retrieve)
        logger.debug(
            f"Retrieved {len(memories)} memories for {symbol} "
            f"| top similarity={memories[0]['similarity'] if memories else 0:.3f}"
        )
        return memories

    except Exception as e:
        # Memory retrieval failure is non-fatal.
        # The orchestrator will just work without episodic context.
        logger.warning(f"Qdrant retrieval failed (non-fatal): {e}")
        return []


# ============================================================
# UPDATE TRADE OUTCOME  (called when trade closes)
# ============================================================

async def update_trade_outcome(
    run_id:   str,
    outcome:  str,    # WIN | LOSS | NEUTRAL
    pnl_pct:  float,
) -> None:
    """
    Update the outcome of a stored memory once the trade closes.

    When a trade is initially stored, outcome = "UNKNOWN".
    When the trade closes (exit_price is recorded in Postgres),
    this function updates the Qdrant payload with the real outcome.

    This is what makes episodic memory useful — past memories with
    real win/loss data are far more informative than unknowns.
    """

    import asyncio

    def _update():
        client = get_qdrant_client()

        from qdrant_client.models import Filter, FieldCondition, MatchValue, SetPayload

        # Find the point with this run_id
        results = client.scroll(
            collection_name = settings.QDRANT_COLLECTION,
            scroll_filter   = Filter(
                must=[
                    FieldCondition(
                        key   = "run_id",
                        match = MatchValue(value=run_id),
                    )
                ]
            ),
            limit      = 1,
            with_payload= True,
        )

        points, _ = results

        if not points:
            return

        # Update the payload on this specific point
        client.set_payload(
            collection_name = settings.QDRANT_COLLECTION,
            payload         = {"outcome": outcome, "pnl_pct": pnl_pct},
            points          = [points[0].id],
        )

    loop = asyncio.get_event_loop()

    try:
        await loop.run_in_executor(None, _update)
        logger.info(f"Memory outcome updated | run_id={run_id} | outcome={outcome}")
    except Exception as e:
        logger.warning(f"Memory outcome update failed: {e}")