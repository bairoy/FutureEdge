"""
Single End-to-End Agent Test

Runs the full multi-agent workflow outside of FastAPI.

NOTE:
-----
Since we're not running through FastAPI's lifespan,
we manually initialize the checkpointer and compile
the graph before calling run_agent_cycle().
"""

# ============================================================
# IMPORTS
# ============================================================

import asyncio
from pprint import pprint

from loguru import logger

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.graph.builder import create_graph, run_agent_cycle
from app.graph.state   import MarketContext, PortfolioSnapshot
from app.core.config   import settings

import app.graph.runtime as runtime


# ============================================================
# DATABASE URI
# ============================================================

DB_URI = (
    f"postgresql://"
    f"{settings.POSTGRES_USER}:"
    f"{settings.POSTGRES_PASSWORD}@"
    f"{settings.POSTGRES_HOST}:"
    f"{settings.POSTGRES_PORT}/"
    f"{settings.POSTGRES_DB}"
)


# ============================================================
# TEST RUN
# ============================================================

async def main():

    # --------------------------------------------------------
    # MOCK MARKET DATA
    # --------------------------------------------------------

    market_context = MarketContext(

        symbol="BTCUSDT",

        current_price=118500.0,

        ohlcv_1m=[
            {"close": 118000 + i * 10}
            for i in range(60)
        ],

        ohlcv_5m=[],

        recent_news=[
            {"title": "Bitcoin bull rally continues"},
            {"title": "Institutional investors bullish"},
        ],

        macro_indicators={},

        regime="TRENDING",

        volatility_24h=0.02,
    )


    # --------------------------------------------------------
    # MOCK PORTFOLIO
    # --------------------------------------------------------

    portfolio = PortfolioSnapshot(

        total_equity=100000.0,

        margin_used=10000.0,

        margin_available=90000.0,

        unrealized_pnl=1200.0,

        open_positions=[],
    )


    # --------------------------------------------------------
    # MANUALLY INIT CHECKPOINTER + COMPILE GRAPH
    #
    # In production this is handled by FastAPI's lifespan().
    # For standalone tests we do it explicitly here so that
    # run_agent_cycle() can find the singleton graph.
    # --------------------------------------------------------

    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:

        await checkpointer.setup()

        runtime._checkpointer  = checkpointer
        runtime.workflow_graph = create_graph().compile(
            checkpointer=checkpointer
        )

        logger.info("✅ Graph compiled for test run")

        # ----------------------------------------------------
        # RUN COMPLETE AGENT WORKFLOW
        # ----------------------------------------------------

        result = await run_agent_cycle(
            market_context=market_context,
            portfolio=portfolio,
        )


    # --------------------------------------------------------
    # PRINT RESULT
    # --------------------------------------------------------

    print("\n")
    print("=" * 60)
    print("THREAD ID")
    print("=" * 60)
    print(result["thread_id"])

    print("\n")
    print("=" * 60)
    print("HITL STATUS")
    print("=" * 60)
    print(result["state"].get("hitl_status", "N/A"))

    print("\n")
    print("=" * 60)
    print("FINAL STATE")
    print("=" * 60)
    pprint(result["state"])

    # --------------------------------------------------------
    # HITL HINT
    # --------------------------------------------------------

    if result["state"].get("hitl_status") == "PENDING":
        print("\n")
        print("=" * 60)
        print("⏸️  WORKFLOW PAUSED — awaiting human review")
        print(f"   thread_id : {result['thread_id']}")
        print("   To resume run:  python test_resume.py")
        print("=" * 60)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())