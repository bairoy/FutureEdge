"""
app/graph/builder.py
=====================
LangGraph graph definition and run_agent_cycle function.

CHANGE: run_agent_cycle now accepts user_id.
The user_id is stored in AgentState so execution_agent
can write it to the trades table.
"""

import uuid
from datetime import datetime, timezone

from loguru import logger
from langgraph.graph import StateGraph, START, END

from app.graph.state import AgentState, MarketContext, PortfolioSnapshot
from app.agents.signal_agent        import signal_agent_node
from app.agents.sentiment_agent     import sentiment_agent_node
from app.agents.risk_agent          import risk_agent_node
from app.agents.portfolio_agent     import portfolio_agent_node
from app.agents.orchestration_agent import orchestrator_node
from app.agents.human_agent         import human_review_node, should_human_review
from app.agents.execution_agent     import execution_node
from app.agents.regime_agent        import regime_agent_node
from app.agents.macro_agent         import macro_agent_node
from app.agents.investing_nodes     import (
    data_fetch_node,
    business_agent_node,
    financial_agent_node,
    valuation_agent_node,
    thesis_agent_node,
)


# ============================================================
# MODE ROUTING
# ============================================================

def route_by_analysis_mode(state: AgentState) -> str:
    """
    Conditional edge on START — picks which branch of the graph runs.

    Defaults to "TRADING" for anything missing or unrecognised. Existing
    callers do not set analysis_mode, and neither do checkpoints written
    before this field existed; both must keep working unchanged.
    """
    mode = (state.get("analysis_mode") or "TRADING").upper()
    return "INVESTING" if mode == "INVESTING" else "TRADING"


def create_graph() -> StateGraph:
    """
    Build the LangGraph workflow graph (stateless — no checkpointer here).
    Checkpointer is attached in runtime.lifespan() via compile().
    """

    builder = StateGraph(AgentState)

    builder.add_node("regime_agent",     regime_agent_node)
    builder.add_node("signal_agent",    signal_agent_node)
    builder.add_node("sentiment_agent", sentiment_agent_node)
    builder.add_node("risk_agent",      risk_agent_node)
    builder.add_node("portfolio_agent", portfolio_agent_node)
    builder.add_node("macro_agent",     macro_agent_node)
    builder.add_node("orchestrator",    orchestrator_node)
    builder.add_node("human_review",    human_review_node)
    builder.add_node("execution",       execution_node)

    # ---- Investing branch (advisory — never reaches execution) ----
    builder.add_node("data_fetch",      data_fetch_node)
    builder.add_node("business_agent",  business_agent_node)
    builder.add_node("financial_agent", financial_agent_node)
    builder.add_node("valuation_agent", valuation_agent_node)
    builder.add_node("thesis_agent",    thesis_agent_node)

    # Branch on mode. The two branches are disjoint and share no tail:
    # Investing places no orders, so there is no execution path to reuse —
    # and no path by which an investing run could reach one.
    builder.add_conditional_edges(
        START,
        route_by_analysis_mode,
        {"TRADING": "regime_agent", "INVESTING": "data_fetch"},
    )

    # Parallel fan-out: all 5 agents start simultaneously once regime is determined
    builder.add_edge("regime_agent", "signal_agent")
    builder.add_edge("regime_agent", "sentiment_agent")
    builder.add_edge("regime_agent", "risk_agent")
    builder.add_edge("regime_agent", "portfolio_agent")
    builder.add_edge("regime_agent", "macro_agent")

    # Fan-in: all agents must finish before orchestrator runs
    builder.add_edge("signal_agent",    "orchestrator")
    builder.add_edge("sentiment_agent", "orchestrator")
    builder.add_edge("risk_agent",      "orchestrator")
    builder.add_edge("portfolio_agent", "orchestrator")
    builder.add_edge("macro_agent",     "orchestrator")

    # Conditional: HITL or direct execution
    builder.add_conditional_edges(
        "orchestrator",
        should_human_review,
        {"human_review": "human_review", "execute": "execution"},
    )

    builder.add_edge("human_review", "execution")
    builder.add_edge("execution",    END)

    # ---- Investing: data_fetch fans out to three siblings, then thesis ----
    # Stage 3 (DCF) looks like it depends on Stage 2 (it needs free cash flow),
    # but both read the same statements. data_fetch derives the shared numbers
    # once, which is what lets these three run as siblings instead of a chain.
    builder.add_edge("data_fetch", "business_agent")
    builder.add_edge("data_fetch", "financial_agent")
    builder.add_edge("data_fetch", "valuation_agent")

    builder.add_edge("business_agent",  "thesis_agent")
    builder.add_edge("financial_agent", "thesis_agent")
    builder.add_edge("valuation_agent", "thesis_agent")

    builder.add_edge("thesis_agent", END)

    return builder


async def run_agent_cycle(
    market_context: MarketContext,
    portfolio:      PortfolioSnapshot,
    user_id:        str | None = None,   # NEW: which user triggered this
    user_override_quantity: int | None = None,
    user_override_rupees: float | None = None,
    override_kelly: bool = False,
    paper_trade: bool = True,
    config:         dict | None = None,
) -> dict:
    """
    Execute one complete workflow cycle for a specific user.

    The user_id is included in the initial state so the
    execution_agent can write it to the trades table.

    Returns:
    --------
    {
        "thread_id": "a1b2c3d4",
        "state":     { ... full AgentState ... }
    }
    """

    from app.graph.runtime import get_workflow_graph
    graph  = get_workflow_graph()
    run_id = str(uuid.uuid4())[:8]

    initial_state: AgentState = {
        # Inputs
        "analysis_mode":  "TRADING",
        "symbol":         market_context.symbol,
        "market_context": market_context,
        "portfolio":      portfolio,

        # User overrides
        "user_override_quantity": user_override_quantity,
        "user_override_rupees":   user_override_rupees,
        "override_kelly":         override_kelly,
        "paper_trade":            paper_trade,

        # Agent outputs (populated by each agent node)
        "signal_vote":    None,
        "sentiment_vote": None,
        "risk_vote":      None,
        "portfolio_vote": None,
        "macro_vote":     None,

        # Orchestrator
        "consensus": None,

        # HITL
        "hitl_required": False,
        "hitl_status":   "NOT_REQUIRED",

        # Execution
        "executed_trade":  None,
        "execution_error": None,

        # Observability + user tracking
        "run_id":          run_id,
        "user_id":         user_id or "anonymous",
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "episodic_memory": [],
        "market_vector":   None,
        "logs":            [],
        "completed_nodes": [],

        # Investing keys, unused on this branch but declared so the state
        # shape is identical regardless of mode.
        "fundamentals_raw":  None,
        "derived_metrics":   None,
        "business_report":   None,
        "financial_report":  None,
        "valuation_report":  None,
        "investment_thesis": None,
        "missing_data":      [],
    }

    if config is None:
        config = {"configurable": {"thread_id": run_id}}

    logger.info(f"Starting workflow | run_id={run_id} | user_id={user_id} | qty_override={user_override_quantity} | rupees_override={user_override_rupees}")

    result = await graph.ainvoke(initial_state, config=config)

    logger.info(
        f"Workflow finished | run_id={run_id} | "
        f"hitl_status={result.get('hitl_status', 'N/A')}"
    )

    return {"thread_id": run_id, "state": result}

async def run_investing_cycle(
    symbol:  str,
    user_id: str | None = None,
    config:  dict | None = None,
) -> dict:
    """
    Run one Investing-mode (fundamental analysis) cycle.

    A SEPARATE ENTRYPOINT, NOT A FLAG ON run_agent_cycle:
    -------------------------------------------------------
    run_agent_cycle fetches 1-minute candles and a full PortfolioSnapshot from
    the broker before it invokes. Investing needs neither — it reads published
    financial statements, and it places no orders, so there is no position to
    size against and no margin to check. Threading a mode flag through that
    function would mean teaching it to skip most of its own setup.

    Same compiled graph, same checkpointer, same run history — different door.

    Returns:
    --------
    {
        "thread_id": "a1b2c3d4",
        "state":     { ... full AgentState, with investment_thesis populated ... }
    }
    """

    from app.graph.runtime import get_workflow_graph
    graph  = get_workflow_graph()
    run_id = str(uuid.uuid4())[:8]

    initial_state: AgentState = {
        "analysis_mode": "INVESTING",
        "symbol":        symbol,

        # Trading-branch inputs. Present so the state shape does not change
        # with the mode; unread, because no trading node runs on this branch.
        "market_context": MarketContext(symbol=symbol, current_price=0.0),
        "portfolio":      PortfolioSnapshot(
            total_equity=0.0, margin_used=0.0, margin_available=0.0, unrealized_pnl=0.0
        ),
        "user_override_quantity": None,
        "user_override_rupees":   None,
        "override_kelly":         False,
        "paper_trade":            True,
        "signal_vote":    None,
        "sentiment_vote": None,
        "risk_vote":      None,
        "portfolio_vote": None,
        "macro_vote":     None,
        "consensus":      None,

        # No order is placed on this branch, so there is nothing to approve.
        # The branch ends at thesis_agent — it never reaches human_review or
        # execution, which takes the whole HITL race-condition surface off
        # Investing mode rather than carefully re-securing it.
        "hitl_required":  False,
        "hitl_status":    "NOT_APPLICABLE",
        "executed_trade":  None,
        "execution_error": None,

        # Investing outputs
        "fundamentals_raw":  None,
        "derived_metrics":   None,
        "business_report":   None,
        "financial_report":  None,
        "valuation_report":  None,
        "investment_thesis": None,
        "missing_data":      [],

        "run_id":          run_id,
        "user_id":         user_id or "anonymous",
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "episodic_memory": [],
        "market_vector":   None,
        "logs":            [],
        "completed_nodes": [],
    }

    if config is None:
        config = {"configurable": {"thread_id": run_id}}

    logger.info(f"Starting investing analysis | run_id={run_id} | symbol={symbol} | user_id={user_id}")

    result = await graph.ainvoke(initial_state, config=config)

    thesis = result.get("investment_thesis")
    logger.info(
        f"Investing analysis finished | run_id={run_id} | symbol={symbol} | "
        f"grade={thesis.quality_grade if thesis else 'NONE'}"
    )

    return {"thread_id": run_id, "state": result}
