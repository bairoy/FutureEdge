"""
Sentiment Agent

Responsibilities:
-----------------
1. Read recent market news
2. Analyze bullish/bearish sentiment
3. Generate sentiment score
4. Produce BUY / SELL / HOLD vote
5. Update LangGraph shared state

Current Version:
----------------
Uses mock keyword-based sentiment analysis.

Future Upgrade:
----------------
Replace with:
- FinBERT
- fine-tuned FuturesFinBERT
- LLM sentiment analysis
- event impact models
"""

# ============================================================
# IMPORTS
# ============================================================

# Structured logging
from loguru import logger

# Shared state and schemas
from app.graph.state import (
    AgentState,
    AgentVote,
    MarketContext
)


# ============================================================
# SENTIMENT AGENT NODE
# ============================================================

def sentiment_agent_node(
    state: AgentState
) -> dict:
    """
    Main LangGraph node for news sentiment analysis.

    Workflow:
    ----------
    News Data
        ↓
    Sentiment Extraction
        ↓
    Bull/Bear Scoring
        ↓
    Confidence Estimation
        ↓
    Agent Vote
    """

    try:

        # ====================================================
        # EXTRACT MARKET CONTEXT
        # ====================================================

        """
        Read shared market context
        from LangGraph state.
        """

        ctx: MarketContext = state["market_context"]

        symbol = ctx.symbol


        # ====================================================
        # EXTRACT NEWS DATA
        # ====================================================

        """
        recent_news contains:
        - headlines
        - articles
        - news metadata

        Example:
        [
            {
                "title": "Fed signals rate cuts"
            }
        ]
        """

        news = ctx.recent_news


        # ====================================================
        # VALIDATE NEWS AVAILABILITY
        # ====================================================

        """
        Without news:
        no sentiment analysis possible.

        Safest action:
        HOLD
        """

        if not news:

            hold_vote = AgentVote(

                agent="SentimentAgent",

                decision="HOLD",

                confidence=0.5,

                reasoning="No recent news available",

                metadata={
                    "news_count": 0
                }
            )

            return {

                "sentiment_vote": hold_vote,


                "completed_nodes": [
                  
                    "sentiment_agent"
                ],

                "logs": [
                  
                    "SentimentAgent skipped due to no news"
                ]
            }


        # ====================================================
        # MOCK SENTIMENT ANALYSIS
        # ====================================================

        """
        Current implementation:
        keyword-based sentiment detection.

        Future production version:
        --------------------------
        from transformers import pipeline

        classifier = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert"
        )

        This mock version teaches:
        - architecture flow
        - scoring system
        - orchestration logic
        """

        bullish_count = 0
        bearish_count = 0


        # ====================================================
        # PROCESS EACH NEWS ARTICLE
        # ====================================================

        for article in news:

            """
            Extract headline safely.

            .get() avoids KeyError
            if title missing.
            """

            title = article.get(
                "title",
                ""
            ).lower()


            # ------------------------------------------------
            # BULLISH KEYWORDS
            # ------------------------------------------------

            """
            Very simplified bullish detection.

            Real FinBERT would understand:
            - context
            - tone
            - financial meaning
            """

            bullish_keywords = [
                "bull",
                "surge",
                "rally",
                "growth",
                "profit",
                "beat",
                "strong",
                "upgrade"
            ]


            # ------------------------------------------------
            # BEARISH KEYWORDS
            # ------------------------------------------------

            bearish_keywords = [
                "bear",
                "crash",
                "decline",
                "loss",
                "drop",
                "weak",
                "downgrade",
                "recession"
            ]


            # ------------------------------------------------
            # COUNT BULLISH SIGNALS
            # ------------------------------------------------

            """
            If any bullish keyword exists,
            increase bullish counter.
            """

            if any(
                word in title
                for word in bullish_keywords
            ):

                bullish_count += 1


            # ------------------------------------------------
            # COUNT BEARISH SIGNALS
            # ------------------------------------------------

            if any(
                word in title
                for word in bearish_keywords
            ):

                bearish_count += 1


        # ====================================================
        # CALCULATE TOTAL ARTICLES
        # ====================================================

        total_news = len(news)


        # ====================================================
        # SENTIMENT SCORE
        # ====================================================

        """
        Normalize sentiment score.

        Formula:
        --------
        bullish - bearish
        -----------------
             total

        Range:
        ------
        +1 → fully bullish
        -1 → fully bearish
         0 → balanced
        """

        sentiment_score = (
            (bullish_count - bearish_count)
            / total_news
        )


        # ====================================================
        # FINAL DECISION
        # ====================================================

        """
        Convert continuous sentiment score
        into trading action.

        Thresholds reduce noisy trades.
        """

        if sentiment_score > 0.2:

            """
            Strong bullish news environment.
            """

            decision = "BUY"

            confidence = min(
                0.5 + sentiment_score,
                0.9
            )

            reasoning = (
                f"Bullish sentiment dominant "
                f"({bullish_count}/{total_news} articles)"
            )


        elif sentiment_score < -0.2:

            """
            Strong bearish news environment.
            """

            decision = "SELL"

            confidence = min(
                0.5 + abs(sentiment_score),
                0.9
            )

            reasoning = (
                f"Bearish sentiment dominant "
                f"({bearish_count}/{total_news} articles)"
            )


        else:

            """
            Mixed or weak sentiment.

            Market narrative unclear.
            """

            decision = "HOLD"

            confidence = (
                0.5 + abs(sentiment_score)
            )

            reasoning = (
                f"Mixed sentiment "
                f"({bullish_count} bull, "
                f"{bearish_count} bear)"
            )


        # ====================================================
        # CREATE AGENT VOTE
        # ====================================================

        """
        Standardized output format
        for orchestrator compatibility.
        """

        vote = AgentVote(

            agent="SentimentAgent",

            decision=decision,

            confidence=round(confidence, 3),

            reasoning=reasoning,

            metadata={

                # Observability data

                "news_count": total_news,

                "bullish_mentions": bullish_count,

                "bearish_mentions": bearish_count,

                "sentiment_score": round(
                    sentiment_score,
                    3
                ),

                "analysis_model": "mock_keyword_sentiment"
            }
        )


        # ====================================================
        # LOG RESULT
        # ====================================================

        logger.info(
            f"📰 SentimentAgent | "
            f"{symbol} | "
            f"{decision} | "
            f"Confidence={confidence:.2f}"
        )


        # ====================================================
        # RETURN LANGGRAPH STATE UPDATE
        # ====================================================

        return {

            # Main output
            "sentiment_vote": vote,

            # Workflow observability

            "completed_nodes": [
                
                "sentiment_agent"
            ],

            "logs": [
               
                (
                    f"SentimentAgent generated "
                    f"{decision} for {symbol}"
                )
            ]
        }


    # ========================================================
    # FAILSAFE HANDLING
    # ========================================================

    except Exception as e:

        """
        Sentiment failure should never
        crash entire trading workflow.

        Safest fallback:
        HOLD
        """

        logger.exception(
            f"SentimentAgent failure: {str(e)}"
        )

        error_vote = AgentVote(

            agent="SentimentAgent",

            decision="HOLD",

            confidence=0.1,

            reasoning=f"Sentiment agent error: {str(e)}"
        )

        return {

            "sentiment_vote": error_vote,

            "execution_error": str(e),


            "logs": [
             
                f"SentimentAgent failed: {str(e)}"
            ]
        }