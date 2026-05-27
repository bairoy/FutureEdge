"""
app/models/finbert.py
======================
FinBERT sentiment analysis for financial news headlines.

WHAT IS FINBERT?
-----------------
FinBERT is a version of BERT (Bidirectional Encoder Representations
from Transformers) fine-tuned specifically on financial text.

Unlike general sentiment models, FinBERT understands:
  "The company missed earnings estimates" → NEGATIVE (bearish)
  "Revenue beat consensus by 15%" → POSITIVE (bullish)
  "The stock was unchanged on thin volumes" → NEUTRAL

It was trained on financial news, analyst reports, and earnings calls.
Model: ProsusAI/finbert on HuggingFace (~400MB download on first use)

WHY BETTER THAN KEYWORDS?
--------------------------
Keywords: "The stock FELL after RECORD profits" → mixed signal, unreliable
FinBERT:  Understands full context → correctly identifies as POSITIVE

PERFORMANCE:
-------------
CPU inference: ~50-200ms per headline (fast enough for trading)
GPU inference: ~5-10ms per headline

MODEL LOADING STRATEGY:
------------------------
The model is loaded ONCE at startup and cached in memory.
Loading takes ~5 seconds and ~800MB RAM.
After that, inference is fast.

TOGGLE:
--------
In your .env:
    FINBERT_ENABLED=true    → use FinBERT (better accuracy)
    FINBERT_ENABLED=false   → use keywords (faster, less RAM, no download)

USAGE:
------
    from app.models.finbert import analyze_sentiment

    result = await analyze_sentiment(["Nifty hits record high on FII buying"])
    # Returns: {"score": 0.85, "label": "positive", "count": 1}
"""

import asyncio
from typing import Optional
from loguru import logger


# ============================================================
# MODEL SINGLETON
# ============================================================
# Loaded once at startup, reused for all inference calls.
# None = model not loaded yet (or FinBERT disabled in config)

_finbert_pipeline = None


def load_finbert() -> bool:
    """
    Load the FinBERT model into memory.

    Called once at startup from runtime.lifespan() when
    settings.FINBERT_ENABLED=True.

    Returns True if loaded successfully, False if failed.
    On failure, sentiment_agent falls back to keyword matching.

    First run: downloads ~400MB from HuggingFace automatically.
    Subsequent runs: loads from local cache (~5 seconds).
    """

    global _finbert_pipeline

    if _finbert_pipeline is not None:
        return True     # already loaded

    try:
        from transformers import pipeline

        logger.info("Loading FinBERT model (may download ~400MB on first run)...")

        _finbert_pipeline = pipeline(
            task            = "sentiment-analysis",
            model           = "ProsusAI/finbert",
            tokenizer       = "ProsusAI/finbert",
            max_length      = 512,
            truncation      = True,
        )

        logger.info("FinBERT loaded successfully")
        return True

    except ImportError:
        logger.warning(
            "transformers or torch not installed — FinBERT unavailable. "
            "Install: pip install transformers torch"
        )
        return False

    except Exception as e:
        logger.error(f"FinBERT load failed: {e}")
        return False


# ============================================================
# ANALYZE SENTIMENT
# ============================================================

async def analyze_sentiment(headlines: list[str]) -> dict:
    """
    Analyze the sentiment of a list of news headlines using FinBERT.

    Runs model inference in a thread pool executor so it does not
    block the asyncio event loop (torch inference is CPU-bound).

    Parameters:
    -----------
    headlines : list of news headline strings

    Returns:
    --------
    {
        "score":    float,  # average sentiment (-1.0 bearish to +1.0 bullish)
        "label":    str,    # "positive" | "negative" | "neutral"
        "positive": int,    # count of bullish headlines
        "negative": int,    # count of bearish headlines
        "neutral":  int,    # count of neutral headlines
        "model":    str,    # "finbert" or "keyword_fallback"
    }
    """

    if not headlines:
        return {
            "score":    0.0,
            "label":    "neutral",
            "positive": 0,
            "negative": 0,
            "neutral":  0,
            "model":    "finbert",
        }

    if _finbert_pipeline is None:
        logger.warning("FinBERT not loaded — returning neutral")
        return {
            "score":    0.0,
            "label":    "neutral",
            "positive": 0,
            "negative": 0,
            "neutral":  len(headlines),
            "model":    "not_loaded",
        }

    # Run inference in executor (CPU-bound, not blocking asyncio)
    loop = asyncio.get_event_loop()

    def _run_inference():
        return _finbert_pipeline(headlines)

    try:
        results = await loop.run_in_executor(None, _run_inference)

    except Exception as e:
        logger.error(f"FinBERT inference failed: {e}")
        return {
            "score":    0.0,
            "label":    "neutral",
            "positive": 0,
            "negative": 0,
            "neutral":  len(headlines),
            "model":    "inference_error",
        }

    # --------------------------------------------------------
    # AGGREGATE RESULTS
    # --------------------------------------------------------
    # FinBERT returns per-headline: {"label": "positive", "score": 0.95}
    # We aggregate into a single overall score.

    positive_count = 0
    negative_count = 0
    neutral_count  = 0
    weighted_score = 0.0

    for result in results:
        label      = result["label"].lower()    # "positive" | "negative" | "neutral"
        confidence = result["score"]            # 0.0 to 1.0

        if label == "positive":
            positive_count += 1
            weighted_score += confidence        # positive → add

        elif label == "negative":
            negative_count += 1
            weighted_score -= confidence        # negative → subtract

        else:
            neutral_count += 1
            # neutral → no contribution to score

    total = len(results)
    avg_score = weighted_score / total if total > 0 else 0.0

    # Determine overall label based on averaged score
    if avg_score > 0.1:
        overall_label = "positive"
    elif avg_score < -0.1:
        overall_label = "negative"
    else:
        overall_label = "neutral"

    return {
        "score":    round(avg_score, 4),
        "label":    overall_label,
        "positive": positive_count,
        "negative": negative_count,
        "neutral":  neutral_count,
        "model":    "finbert",
    }


def is_loaded() -> bool:
    """Returns True if FinBERT model is loaded and ready."""
    return _finbert_pipeline is not None