"""
app/scripts/test_agent.py
==========================
End-to-end test for the full agent workflow.

Tests with REAL yfinance data for Indian stocks.
No Zerodha API key needed — yfinance is completely free.

HOW TO RUN:
-----------
    docker compose exec backend python -m app.scripts.test_agent

WHAT THIS TESTS:
----------------
1. Historical candle download from yfinance (free NSE data)
2. Indicator computation and caching via Redis
3. All 4 agents running in parallel
4. Orchestrator consensus and HITL decision
5. Execution agent (mock broker, no real orders)
6. PostgreSQL trade record write
7. Redis pub/sub publish

EXPECTED OUTPUT:
----------------
You should see:
  - Candles loaded for RELIANCE.NS or NIFTY 50
  - All 4 agents voting
  - Orchestrator decision
  - If HITL triggered: hitl_status = PENDING
  - If no HITL: executed_trade with mock order_id
"""

import asyncio
from pprint import pprint

from loguru import logger
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.graph.builder import create_graph, run_agent_cycle
from app.graph.state import MarketContext, PortfolioSnapshot
from app.core.config import settings
from app.data.feed import load_historical_candles, get_current_price_yfinance

import app.graph.runtime as runtime


# ============================================================
# DATABASE URI
# ============================================================

DB_URI = (
    f"postgresql://"
    f"{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}"
    f"/{settings.POSTGRES_DB}"
)


# ============================================================
# MAIN TEST
# ============================================================

async def main():

    # --------------------------------------------------------
    # CHOOSE SYMBOL TO TEST
    # --------------------------------------------------------
    # Change this to test different instruments.
    # Yahoo Finance format for NSE stocks: "SYMBOL.NS"
    # NIFTY 50 index: "^NSEI" (mapped automatically)

    symbol = "NIFTY 50"     # or try "RELIANCE", "INFY", "TCS"

    # --------------------------------------------------------
    # FETCH REAL MARKET DATA  (free via yfinance)
    # --------------------------------------------------------

    logger.info(f"Fetching historical candles for {symbol}...")

    candles = load_historical_candles(
        symbol   = symbol,
        period   = "5d",     # last 5 trading days
        interval = "1m",     # 1-minute candles
    )

    price = get_current_price_yfinance(symbol)

    logger.info(f"Got {len(candles)} candles | Current price: {price}")

    if not candles:
        logger.warning("No candles loaded — using mock data for test")
        candles = [{"open": 22000, "high": 22100, "low": 21900,
                    "close": 22000 + i*10, "volume": 100000, "timestamp": f"2024-01-01T{9+i//60:02d}:{i%60:02d}:00"}
                   for i in range(60)]
        price = 22600.0

    # --------------------------------------------------------
    # BUILD MARKET CONTEXT
    # --------------------------------------------------------

    market_context = MarketContext(
        symbol         = symbol,
        current_price  = price,
        ohlcv_1m       = candles,
        ohlcv_5m       = [],
        recent_news    = [
            {"title": "Nifty hits all-time high on FII buying"},
            {"title": "RBI holds rates steady — market positive"},
        ],
        regime         = "TRENDING",
        volatility_24h = 0.02,
    )

    # --------------------------------------------------------
    # BUILD PORTFOLIO  (mock for test)
    # --------------------------------------------------------

    portfolio = PortfolioSnapshot(
        total_equity     = 100000.0,
        margin_used      = 10000.0,
        margin_available = 90000.0,
        unrealized_pnl   = 1200.0,
        open_positions   = [],
    )

    # --------------------------------------------------------
    # INIT GRAPH WITH CHECKPOINTER  (same as lifespan() does)
    # --------------------------------------------------------

    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:

        await checkpointer.setup()

        runtime._checkpointer  = checkpointer
        runtime.workflow_graph = create_graph().compile(checkpointer=checkpointer)

        logger.info("Graph compiled for test")

        # --------------------------------------------------------
        # RUN CYCLE
        # --------------------------------------------------------

        result = await run_agent_cycle(
            market_context = market_context,
            portfolio      = portfolio,
        )

    # --------------------------------------------------------
    # PRINT RESULTS
    # --------------------------------------------------------

    state = result["state"]

    print("\n" + "="*60)
    print("THREAD ID (save this to resume if HITL triggers)")
    print("="*60)
    print(result["thread_id"])

    print("\n" + "="*60)
    print("HITL STATUS")
    print("="*60)
    print(state.get("hitl_status", "N/A"))

    print("\n" + "="*60)
    print("AGENT VOTES")
    print("="*60)
    for key in ["signal_vote", "sentiment_vote", "risk_vote", "portfolio_vote"]:
        vote = state.get(key)
        if vote:
            print(f"  {vote.agent:20s} → {vote.decision:5s}  conf={vote.confidence:.2f}  | {vote.reasoning[:60]}")

    print("\n" + "="*60)
    print("ORCHESTRATOR DECISION")
    print("="*60)
    consensus = state.get("consensus")
    if consensus:
        print(f"  Direction  : {consensus.direction}")
        print(f"  Size       : ₹{consensus.size:.2f}")
        print(f"  Entry Price: ₹{consensus.entry_price:.2f}")
        print(f"  Risk Score : {consensus.risk_score:.2f}")

    print("\n" + "="*60)
    print("EXECUTED TRADE")
    print("="*60)
    pprint(state.get("executed_trade"))

    print("\n" + "="*60)
    print("LOGS")
    print("="*60)
    for log in state.get("logs", []):
        print(f"  {log}")

    if state.get("hitl_status") == "PENDING":
        print("\n" + "="*60)
        print("WORKFLOW PAUSED - HITL REQUIRED")
        print(f"Run this to approve:")
        print(f"  python -m app.scripts.test_resume {result['thread_id']} APPROVE")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(main())