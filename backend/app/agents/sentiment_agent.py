"""
app/agents/sentiment_agent.py
==============================
Sentiment agent — Phase 2 upgrade.

WHAT CHANGED FROM PHASE 1:
----------------------------
1. Fetches REAL news from free RSS feeds (ET, Moneycontrol, Livemint)
   via app/data/news_fetcher.py — no API key required.

2. Uses FinBERT NLP model when FINBERT_ENABLED=true in .env
   FinBERT understands financial language context — much more
   accurate than keyword matching for ambiguous headlines.

3. Falls back to keyword matching when FinBERT is disabled
   (keeps Phase 1 behaviour as the default for light setups).

TOGGLE:
-------
.env:
    FINBERT_ENABLED=false   → keyword matching (default, fast)
    FINBERT_ENABLED=true    → FinBERT NLP (~800MB RAM, 5s startup)
"""

from loguru import logger

from app.core.config import settings
from app.graph.state import AgentState, AgentVote


# ============================================================
# KEYWORDS  (used when FinBERT is disabled — Indian market focused)
# ============================================================

BULLISH_KEYWORDS = [
    "bull", "surge", "rally", "growth", "profit", "beat", "strong",
    "upgrade", "record", "high", "gain", "rise", "positive", "optimistic",
    "outperform", "buy", "fii buying", "dii buying", "inflow", "nifty high",
    "sensex high", "rbi rate cut", "gst collection", "gdp growth",
    "earnings beat", "dividend",
]

BEARISH_KEYWORDS = [
    "bear", "crash", "decline", "loss", "drop", "weak", "downgrade",
    "recession", "fall", "negative", "sell", "underperform", "concern",
    "risk", "fear", "fii selling", "outflow", "nifty low", "sensex low",
    "rbi rate hike", "inflation", "rupee fall", "deficit",
    "sebi action", "fraud", "scam", "earnings miss",
]


# ============================================================
# SENTIMENT AGENT NODE
# ============================================================

async def sentiment_agent_node(state: AgentState) -> dict:
    """
    Phase 2: fetches real news + uses FinBERT or keyword scoring.

    Flow:
    1. Try to fetch real news from RSS feeds (cached 5 min in Redis)
    2. If no news in state AND feed fails → HOLD
    3. If FinBERT enabled → run NLP inference
    4. Else → keyword matching (Phase 1 logic)
    5. Return AgentVote
    """

    try:
        ctx    = state["market_context"]
        symbol = ctx.symbol

        # --------------------------------------------------------
        # STEP 1: GET NEWS
        # --------------------------------------------------------
        # Use news already in MarketContext if available.
        # If not, fetch from RSS feeds (free, no API key).

        news = ctx.recent_news

        if not news:
            # Try fetching real news from free RSS feeds
            try:
                from app.data.news_fetcher import fetch_news
                news = await fetch_news(symbol=symbol, max_articles=15)
            except Exception as e:
                logger.warning(f"News fetch failed: {e}")
                news = []

        if not news:
            return {
                "sentiment_vote": AgentVote(
                    agent      = "SentimentAgent",
                    decision   = "HOLD",
                    confidence = 0.5,
                    reasoning  = "No news available from any source",
                    metadata   = {"news_count": 0, "model": "none"},
                ),
                "completed_nodes": ["sentiment_agent"],
                "logs":            ["SentimentAgent: no news"],
            }

        headlines = [a.get("title", "") for a in news if a.get("title")]

        # --------------------------------------------------------
        # STEP 2: SCORE SENTIMENT
        # --------------------------------------------------------

        if settings.FINBERT_ENABLED:
            result = await _score_with_finbert(headlines)
        else:
            result = _score_with_keywords(headlines)

        sentiment_score = result["score"]
        model_used      = result["model"]

        # --------------------------------------------------------
        # STEP 3: DECISION
        # --------------------------------------------------------

        if sentiment_score > 0.2:
            decision   = "BUY"
            confidence = min(0.5 + sentiment_score, 0.9)
            reasoning  = (
                f"Bullish news sentiment "
                f"({result.get('positive', 0)}/{len(headlines)} positive) "
                f"via {model_used}"
            )

        elif sentiment_score < -0.2:
            decision   = "SELL"
            confidence = min(0.5 + abs(sentiment_score), 0.9)
            reasoning  = (
                f"Bearish news sentiment "
                f"({result.get('negative', 0)}/{len(headlines)} negative) "
                f"via {model_used}"
            )

        else:
            decision   = "HOLD"
            confidence = 0.5 + abs(sentiment_score)
            reasoning  = (
                f"Mixed/neutral news sentiment (score={sentiment_score:.2f}) "
                f"via {model_used}"
            )

        vote = AgentVote(
            agent      = "SentimentAgent",
            decision   = decision,
            confidence = round(confidence, 3),
            reasoning  = reasoning,
            metadata   = {
                "news_count":      len(headlines),
                "sentiment_score": round(sentiment_score, 3),
                "positive":        result.get("positive", 0),
                "negative":        result.get("negative", 0),
                "neutral":         result.get("neutral", 0),
                "model":           model_used,
            },
        )

        logger.info(
            f"SentimentAgent | {symbol} | {decision} | "
            f"conf={confidence:.2f} | score={sentiment_score:.2f} | model={model_used}"
        )

        return {
            "sentiment_vote":  vote,
            "completed_nodes": ["sentiment_agent"],
            "logs":            [f"SentimentAgent generated {decision} for {symbol}"],
        }

    except Exception as e:
        logger.exception(f"SentimentAgent failure: {e}")

        return {
            "sentiment_vote": AgentVote(
                agent     = "SentimentAgent",
                decision  = "HOLD",
                confidence= 0.1,
                reasoning = f"SentimentAgent error: {e}",
            ),
            "completed_nodes": ["sentiment_agent"],
            "logs":            [f"SentimentAgent failed: {e}"],
        }


# ============================================================
# FINBERT SCORING
# ============================================================

async def _score_with_finbert(headlines: list[str]) -> dict:
    """Use FinBERT NLP model for sentiment scoring."""

    from app.models.finbert import analyze_sentiment, is_loaded

    if not is_loaded():
        logger.debug("FinBERT not loaded — falling back to keywords")
        return _score_with_keywords(headlines)

    return await analyze_sentiment(headlines)


# ============================================================
# KEYWORD SCORING  (Phase 1 fallback)
# ============================================================

def _score_with_keywords(headlines: list[str]) -> dict:
    """Keyword matching — fast fallback when FinBERT is disabled."""

    bullish = 0
    bearish = 0

    for title in headlines:
        t = title.lower()
        if any(kw in t for kw in BULLISH_KEYWORDS):
            bullish += 1
        if any(kw in t for kw in BEARISH_KEYWORDS):
            bearish += 1

    total = len(headlines)
    score = (bullish - bearish) / total if total > 0 else 0.0

    return {
        "score":    round(score, 3),
        "positive": bullish,
        "negative": bearish,
        "neutral":  total - bullish - bearish,
        "model":    "keyword_v1",
    }