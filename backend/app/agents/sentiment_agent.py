import email.utils
from datetime import datetime, timezone
import math
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
# RECENCY WEIGHT CALCULATOR
# ============================================================

def _get_recency_weight(published_str: str) -> float:
    """
    Calculate exponential decay weight for articles based on age in minutes.
    Half-life = 180 minutes (3 hours).
    Weight ranges from 1.0 (new) to 0.1 (old baseline).
    """
    if not published_str:
        return 1.0
    try:
        # RFC 2822 / RSS pubDate parsing
        pub_dt = email.utils.parsedate_to_datetime(published_str)
        now_dt = datetime.now(timezone.utc)
        pub_dt = pub_dt.astimezone(timezone.utc)
        
        age_minutes = max(0.0, (now_dt - pub_dt).total_seconds() / 60.0)
        # decay_rate = ln(2) / 180 = 0.00385
        weight = math.exp(-0.00385 * age_minutes)
        return max(0.1, min(1.0, weight))
    except Exception:
        try:
            # ISO 8601 fallback
            pub_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            age_minutes = max(0.0, (now_dt - pub_dt).total_seconds() / 60.0)
            return max(0.1, min(1.0, math.exp(-0.00385 * age_minutes)))
        except Exception:
            return 1.0


# ============================================================
# SENTIMENT AGENT NODE
# ============================================================

async def sentiment_agent_node(state: AgentState) -> dict:
    """
    Phase 2: fetches real news + uses FinBERT or keyword scoring.
    Sentiment is recency-weighted: newer articles count for more.
    """

    try:
        ctx    = state["market_context"]
        symbol = ctx.symbol

        news = ctx.recent_news

        if not news:
            # Try fetching real news from free RSS feeds
            try:
                from app.data.news_fetcher import fetch_news
                news = await fetch_news(symbol=symbol, max_articles=15)
            except Exception as e:
                logger.warning(f"News fetch failed: {e}")
                news = []

        # Fetch yfinance real-time stock-specific news
        ticker_news = []
        try:
            from app.agents.tools.news_tools import get_realtime_ticker_news
            ticker_news = await get_realtime_ticker_news(symbol)
        except Exception as e:
            logger.warning(f"YFinance news fetch failed: {e}")

        # Combine and de-duplicate by title
        seen_titles = set()
        merged_news = []
        for article in ticker_news:
            title = article.get("title", "").strip()
            if title and title.lower() not in seen_titles:
                seen_titles.add(title.lower())
                merged_news.append(article)

        for article in news:
            title = article.get("title", "").strip()
            if title and title.lower() not in seen_titles:
                seen_titles.add(title.lower())
                merged_news.append(article)

        news = merged_news

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

        # --------------------------------------------------------
        # SCORE SENTIMENT
        # --------------------------------------------------------

        if settings.FINBERT_ENABLED:
            result = await _score_with_finbert(news)
        else:
            result = _score_with_keywords(news)

        sentiment_score = result["score"]
        model_used      = result["model"]
        headlines_count = len(news)

        # --------------------------------------------------------
        # DECISION
        # --------------------------------------------------------

        if sentiment_score > 0.2:
            decision   = "BUY"
            confidence = min(0.5 + sentiment_score, 0.9)
            reasoning  = (
                f"Bullish news sentiment "
                f"({result.get('positive', 0)}/{headlines_count} positive) "
                f"via {model_used}"
            )

        elif sentiment_score < -0.2:
            decision   = "SELL"
            confidence = min(0.5 + abs(sentiment_score), 0.9)
            reasoning  = (
                f"Bearish news sentiment "
                f"({result.get('negative', 0)}/{headlines_count} negative) "
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
                "news_count":      headlines_count,
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

async def _score_with_finbert(news_articles: list[dict]) -> dict:
    """Use FinBERT NLP model with recency weighting."""

    from app.models.finbert import _finbert_pipeline, is_loaded
    import asyncio

    if not is_loaded():
        logger.debug("FinBERT not loaded — falling back to keywords")
        return _score_with_keywords(news_articles)

    headlines = [a.get("title", "") for a in news_articles if a.get("title")]
    if not headlines:
        return {
            "score":    0.0,
            "positive": 0,
            "negative": 0,
            "neutral":  0,
            "model":    "finbert_recency_weighted",
        }

    loop = asyncio.get_running_loop()
    def _run_inference():
        return _finbert_pipeline(headlines)

    try:
        results = await loop.run_in_executor(None, _run_inference)
    except Exception as e:
        logger.error(f"FinBERT inference failed in agent: {e}")
        return _score_with_keywords(news_articles)

    positive_count = 0
    negative_count = 0
    neutral_count  = 0
    weighted_score = 0.0
    total_weight = 0.0

    for article, result in zip(news_articles, results):
        label      = result["label"].lower()
        confidence = result["score"]
        published  = article.get("published", "")
        weight     = _get_recency_weight(published)

        art_score = 0.0
        if label == "positive":
            positive_count += 1
            art_score = confidence
        elif label == "negative":
            negative_count += 1
            art_score = -confidence
        else:
            neutral_count += 1
            
        weighted_score += art_score * weight
        total_weight += weight

    score = weighted_score / total_weight if total_weight > 0 else 0.0

    return {
        "score":    round(score, 3),
        "positive": positive_count,
        "negative": negative_count,
        "neutral":  neutral_count,
        "model":    "finbert_recency_weighted",
    }


# ============================================================
# KEYWORD SCORING  (Phase 1 fallback)
# ============================================================

def _score_with_keywords(news_articles: list[dict]) -> dict:
    """Keyword matching with recency weighting."""

    bullish = 0
    bearish = 0
    weighted_score = 0.0
    total_weight = 0.0

    for article in news_articles:
        title     = article.get("title", "")
        published = article.get("published", "")
        weight    = _get_recency_weight(published)
        t = title.lower()

        is_bull = any(kw in t for kw in BULLISH_KEYWORDS)
        is_bear = any(kw in t for kw in BEARISH_KEYWORDS)

        art_score = 0.0
        if is_bull and not is_bear:
            bullish += 1
            art_score = 1.0
        elif is_bear and not is_bull:
            bearish += 1
            art_score = -1.0
        elif is_bull and is_bear:
            bullish += 1
            bearish += 1

        weighted_score += art_score * weight
        total_weight += weight

    score = weighted_score / total_weight if total_weight > 0 else 0.0
    total = len(news_articles)

    return {
        "score":    round(score, 3),
        "positive": bullish,
        "negative": bearish,
        "neutral":  total - bullish - bearish if total > (bullish + bearish) else 0,
        "model":    "keyword_v1_recency_weighted",
    }