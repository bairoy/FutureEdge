"""
app/agents/sentiment_agent.py
==============================
News sentiment agent — reads recent news, scores sentiment,
votes BUY/SELL/HOLD based on market narrative.

CHANGES FROM ORIGINAL:
-----------------------
1. Now ASYNC  (async def sentiment_agent_node)
   Ready for Phase 2 where we'll await a FinBERT inference call.

2. Indian market keywords added
   Expanded keyword lists with SEBI, FII/DII, RBI terms
   that are common in Indian financial news.

CURRENT IMPLEMENTATION:
------------------------
Keyword matching — simple but works well for clear headlines.

PHASE 2 UPGRADE:
-----------------
Replace keyword matching with ProsusAI/finbert model:
    from transformers import pipeline
    classifier = pipeline("sentiment-analysis", model="ProsusAI/finbert")
    result = classifier(headline)
"""

from loguru import logger

from app.graph.state import AgentState, AgentVote


# ============================================================
# SENTIMENT KEYWORDS  (Indian market focused)
# ============================================================

# Words that suggest bullish / positive sentiment
BULLISH_KEYWORDS = [
    # General
    "bull", "surge", "rally", "growth", "profit", "beat",
    "strong", "upgrade", "record", "high", "gain", "rise",
    "positive", "optimistic", "outperform", "buy",
    # Indian market specific
    "fii buying", "dii buying", "inflow", "nifty high",
    "sensex high", "rbi rate cut", "gst collection",
    "gdp growth", "earnings beat", "dividend",
]

# Words that suggest bearish / negative sentiment
BEARISH_KEYWORDS = [
    # General
    "bear", "crash", "decline", "loss", "drop", "weak",
    "downgrade", "recession", "fall", "negative", "sell",
    "underperform", "concern", "risk", "fear",
    # Indian market specific
    "fii selling", "outflow", "nifty low", "sensex low",
    "rbi rate hike", "inflation", "rupee fall", "deficit",
    "sebi action", "fraud", "scam", "earnings miss",
]


# ============================================================
# SENTIMENT AGENT NODE  (async)
# ============================================================

async def sentiment_agent_node(state: AgentState) -> dict:
    """
    Reads recent_news from MarketContext → scores sentiment → votes.

    Returns a partial state update with sentiment_vote set.
    """

    try:
        ctx    = state["market_context"]
        symbol = ctx.symbol
        news   = ctx.recent_news

        # --------------------------------------------------------
        # NO NEWS → HOLD
        # --------------------------------------------------------

        if not news:
            logger.info(f"📰 SentimentAgent | {symbol} | No news → HOLD")

            return {
                "sentiment_vote": AgentVote(
                    agent     = "SentimentAgent",
                    decision  = "HOLD",
                    confidence= 0.5,
                    reasoning = "No recent news available",
                    metadata  = {"news_count": 0},
                ),
                "completed_nodes": ["sentiment_agent"],
                "logs":            ["SentimentAgent: no news available"],
            }

        # --------------------------------------------------------
        # SCORE EACH HEADLINE
        # --------------------------------------------------------

        bullish_count = 0
        bearish_count = 0

        for article in news:
            title = article.get("title", "").lower()

            has_bullish = any(kw in title for kw in BULLISH_KEYWORDS)
            has_bearish = any(kw in title for kw in BEARISH_KEYWORDS)

            if has_bullish:
                bullish_count += 1
            if has_bearish:
                bearish_count += 1

        total_news = len(news)

        # sentiment_score ranges from -1.0 (all bearish) to +1.0 (all bullish)
        sentiment_score = (bullish_count - bearish_count) / total_news

        # --------------------------------------------------------
        # CONVERT SCORE TO DECISION
        # --------------------------------------------------------

        if sentiment_score > 0.2:
            decision  = "BUY"
            confidence= min(0.5 + sentiment_score, 0.9)
            reasoning = (
                f"Bullish sentiment dominant "
                f"({bullish_count}/{total_news} articles bullish)"
            )

        elif sentiment_score < -0.2:
            decision  = "SELL"
            confidence= min(0.5 + abs(sentiment_score), 0.9)
            reasoning = (
                f"Bearish sentiment dominant "
                f"({bearish_count}/{total_news} articles bearish)"
            )

        else:
            decision  = "HOLD"
            confidence= 0.5 + abs(sentiment_score)
            reasoning = (
                f"Mixed sentiment "
                f"({bullish_count} bullish, {bearish_count} bearish)"
            )

        vote = AgentVote(
            agent     = "SentimentAgent",
            decision  = decision,
            confidence= round(confidence, 3),
            reasoning = reasoning,
            metadata  = {
                "news_count":      total_news,
                "bullish_count":   bullish_count,
                "bearish_count":   bearish_count,
                "sentiment_score": round(sentiment_score, 3),
                "model":           "keyword_v1",   # Phase 2: "finbert"
            },
        )

        logger.info(
            f"📰 SentimentAgent | {symbol} | {decision} | "
            f"conf={confidence:.2f} | score={sentiment_score:.2f}"
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