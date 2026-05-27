"""
app/models/llm_reasoner.py
===========================
OpenAI LLM reasoning for the orchestrator node.

WHAT THIS DOES:
---------------
After the orchestrator calculates the trade direction using
weighted voting, this module calls OpenAI GPT to produce a clear
EXPLANATION of why the system made that decision.

The explanation is shown to the risk manager in the HITL review
modal, so they can understand the reasoning before approving
or rejecting a high-risk trade.

WITHOUT THIS:
  Risk manager sees: "LONG NIFTY 50 | Risk: 0.78 | HITL required"
  They have no idea why. Hard to make an informed decision.

WITH THIS:
  Risk manager sees: "3 of 4 agents voted BUY. Signal agent detected
  RSI oversold (28) and bullish MACD crossover. Sentiment is positive
  (FII inflows). Risk agent flagged high risk due to 2x normal
  volatility. Consider reducing position size."

WHEN IS THIS CALLED?
---------------------
Only when ALL of:
  1. settings.LLM_REASONING_ENABLED = True
  2. settings.OPENAI_API_KEY is set
  3. A trade direction has been decided (not when VETO)

If the API call fails for any reason, we gracefully fall back
to a template-based explanation — the trade is never blocked
because of LLM failure.

TOGGLE:
--------
.env:
    LLM_REASONING_ENABLED=true
    OPENAI_API_KEY=your_openai_api_key

USAGE FROM orchestration_agent.py:
------------------------------------
    from app.models.llm_reasoner import generate_trade_rationale

    rationale = await generate_trade_rationale(
        symbol="NIFTY 50",
        direction="LONG",
        votes=votes,
        buy_score=0.68,
        sell_score=0.12,
        risk_score=0.78,
        disagreement=0.25,
        episodic_memories=memories,
        regime="TRENDING_UP",
    )
"""

import asyncio
from loguru import logger

from openai import OpenAI

from app.core.config import settings
from app.graph.state import AgentVote


# ============================================================
# GENERATE TRADE RATIONALE
# ============================================================

async def generate_trade_rationale(
    symbol:            str,
    direction:         str,
    votes:             list[AgentVote],
    buy_score:         float,
    sell_score:        float,
    risk_score:        float,
    disagreement:      float,
    episodic_memories: list[dict],
    regime:            str,
) -> str:
    """
    Ask OpenAI GPT to explain why the system decided on this trade.

    This runs AFTER the mathematical consensus is already computed.
    GPT's job is to EXPLAIN the decision in plain English,
    not to make the decision.

    Parameters:
    -----------
    symbol            : e.g. "NIFTY 50"
    direction         : "LONG" | "SHORT" | "NONE"
    votes             : all 4 AgentVote objects
    buy_score         : normalised buy score (0-1)
    sell_score        : normalised sell score (0-1)
    risk_score        : calculated risk score (0-1)
    disagreement      : agent disagreement metric (0-1)
    episodic_memories : top-K similar past situations from Qdrant
    regime            : detected market regime

    Returns:
    --------
    A plain-English explanation string (2-4 sentences).
    Falls back to a template-based string if API call fails.
    """

    # --------------------------------------------------------
    # CHECK IF LLM REASONING IS ENABLED
    # --------------------------------------------------------

    if not settings.LLM_REASONING_ENABLED:
        return _template_rationale(direction, votes, risk_score, disagreement)

    if not settings.OPENAI_API_KEY:
        logger.debug("OPENAI_API_KEY not set — using template rationale")
        return _template_rationale(direction, votes, risk_score, disagreement)

    # --------------------------------------------------------
    # BUILD PROMPT
    # --------------------------------------------------------

    votes_summary = "\n".join([
        f"  {v.agent}: {v.decision} (confidence={v.confidence:.2f}) — {v.reasoning}"
        for v in votes
    ])

    memory_summary = ""

    if episodic_memories:
        memory_summary = "\n\nSimilar past situations:\n" + "\n".join([
            f"  - {m['symbol']} {m['direction']} in {m['regime']}: "
            f"{m['outcome']} ({m['pnl_pct']:+.1f}%) | similarity={m['similarity']:.2f}"
            for m in episodic_memories[:3]
        ])

    prompt = f"""You are the reasoning module of an algorithmic trading system for Indian equities.

The system has just decided to go {direction} on {symbol}.

Agent votes:
{votes_summary}

Consensus scores: buy={buy_score:.2f}, sell={sell_score:.2f}
Risk score: {risk_score:.2f} (higher = more risky)
Agent disagreement: {disagreement:.2f} (higher = less consensus)
Market regime: {regime}{memory_summary}

Write a 2-3 sentence explanation for the risk manager who will review this trade.

Explain WHY the system chose {direction}, what the key signals were,
and what risk factors they should consider.

Be specific about the indicators.
Be concise.
Do not use bullet points.
Do not recommend approving or rejecting — just explain the reasoning.
"""

    # --------------------------------------------------------
    # CALL OPENAI API
    # --------------------------------------------------------

    loop = asyncio.get_event_loop()

    def _call_api():
        """
        Synchronous OpenAI API call executed in a thread pool.

        We use run_in_executor so the FastAPI / async event loop
        does not get blocked during the network request.
        """

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",

            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional financial AI reasoning engine. "
                        "Your job is to clearly explain algorithmic trading decisions "
                        "to human risk managers."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            max_tokens=300,
            temperature=0.3,
        )

        return response.choices[0].message.content.strip()

    # --------------------------------------------------------
    # EXECUTE API CALL
    # --------------------------------------------------------

    try:
        rationale = await loop.run_in_executor(
            None,
            _call_api
        )

        logger.info(
            f"LLM rationale generated | "
            f"{symbol} {direction} | "
            f"chars={len(rationale)}"
        )

        return rationale

    except Exception as e:

        logger.warning(
            f"LLM rationale failed (using template): {e}"
        )

        return _template_rationale(
            direction,
            votes,
            risk_score,
            disagreement
        )


# ============================================================
# TEMPLATE FALLBACK
# ============================================================

def _template_rationale(
    direction:    str,
    votes:        list[AgentVote],
    risk_score:   float,
    disagreement: float,
) -> str:
    """
    Generate a simple template-based rationale when GPT is
    unavailable (API key missing, network error, etc.).

    Not as rich as the LLM version but still informative.
    """

    # --------------------------------------------------------
    # HANDLE EMPTY VOTES
    # --------------------------------------------------------

    if not votes:
        return (
            f"System decided {direction} "
            f"with no agent votes available."
        )

    # --------------------------------------------------------
    # BUILD VOTE SUMMARY
    # --------------------------------------------------------

    vote_summary = ", ".join(
        [f"{v.agent}={v.decision}" for v in votes]
    )

    # --------------------------------------------------------
    # CLASSIFY RISK LEVEL
    # --------------------------------------------------------

    if risk_score < 0.4:
        risk_level = "low"

    elif risk_score < 0.7:
        risk_level = "moderate"

    else:
        risk_level = "high"

    # --------------------------------------------------------
    # CLASSIFY CONSENSUS LEVEL
    # --------------------------------------------------------

    if disagreement < 0.2:
        agreement = "strong consensus"

    else:
        agreement = "moderate disagreement"

    # --------------------------------------------------------
    # RETURN TEMPLATE
    # --------------------------------------------------------

    return (
        f"System decided {direction} based on agent votes: "
        f"{vote_summary}. "
        f"Risk level is {risk_level} "
        f"(score={risk_score:.2f}) "
        f"with {agreement} between agents "
        f"(disagreement={disagreement:.2f})."
    )