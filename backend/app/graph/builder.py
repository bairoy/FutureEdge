"""
FutureEdge LangGraph Builder

Parallel Multi-Agent Architecture

"""

# ============================================================
# IMPORTS
# ============================================================

import uuid
from datetime import datetime

from loguru import logger

from langgraph.graph import StateGraph, START, END

# Shared state
from app.graph.state import (
    AgentState,
    MarketContext,
    PortfolioSnapshot,
)

# Agents
from app.agents.signal_agent      import signal_agent_node
from app.agents.sentiment_agent   import sentiment_agent_node
from app.agents.risk_agent        import risk_agent_node
from app.agents.portfolio_agent   import portfolio_agent_node
from app.agents.orchestration_agent import orchestrator_node
from app.agents.human_agent       import human_review_node, should_human_review
from app.agents.execution_agent   import execution_node


# ============================================================
# CREATE GRAPH  (stateless — no checkpointer attached here)
# ============================================================

def create_graph() -> StateGraph:
    """
    Build and return the StateGraph builder.

    The checkpointer is attached in runtime.lifespan() via
    builder.compile(checkpointer=...), NOT here.
    This keeps the graph definition stateless and testable.
    """

    builder = StateGraph(AgentState)

    # --------------------------------------------------------
    # REGISTER NODES
    # --------------------------------------------------------

    builder.add_node("signal_agent",    signal_agent_node)
    builder.add_node("sentiment_agent", sentiment_agent_node)
    builder.add_node("risk_agent",      risk_agent_node)
    builder.add_node("portfolio_agent", portfolio_agent_node)
    builder.add_node("orchestrator",    orchestrator_node)
    builder.add_node("human_review",    human_review_node)
    builder.add_node("execution",       execution_node)

    # --------------------------------------------------------
    # TRUE PARALLEL FAN-OUT
    # --------------------------------------------------------

    builder.add_edge(START, "signal_agent")
    builder.add_edge(START, "sentiment_agent")
    builder.add_edge(START, "risk_agent")
    builder.add_edge(START, "portfolio_agent")

    # --------------------------------------------------------
    # FAN-IN → ORCHESTRATOR
    # --------------------------------------------------------

    builder.add_edge("signal_agent",    "orchestrator")
    builder.add_edge("sentiment_agent", "orchestrator")
    builder.add_edge("risk_agent",      "orchestrator")
    builder.add_edge("portfolio_agent", "orchestrator")

    # --------------------------------------------------------
    # CONDITIONAL ROUTING  (HITL or direct execution)
    # --------------------------------------------------------

    builder.add_conditional_edges(
        "orchestrator",
        should_human_review,
        {
            "human_review": "human_review",
            "execute":      "execution",
        },
    )

    # --------------------------------------------------------
    # HITL → EXECUTION → END
    # --------------------------------------------------------

    builder.add_edge("human_review", "execution")
    builder.add_edge("execution",    END)

    return builder


# ============================================================
# RUN AGENT CYCLE
# ============================================================

async def run_agent_cycle(
    market_context: MarketContext,
    portfolio:      PortfolioSnapshot,
    config:         dict | None = None,
) -> dict:
    """
    Execute one complete workflow cycle.

    Returns a dict with:
    --------------------
    - thread_id : str   → use this to resume if HITL pauses
    - state     : dict  → final (or interrupted) graph state

    IMPORTANT:
    ----------
    This function uses the singleton workflow_graph from
    app.graph.runtime.  That graph already has a live
    AsyncPostgresSaver attached.  Do NOT create a new
    checkpointer here — doing so would close the connection
    after ainvoke() returns on interrupt, making resume
    impossible.
    """

    # --------------------------------------------------------
    # IMPORT SINGLETON  (late import avoids circular deps)
    # --------------------------------------------------------

    from app.graph.runtime import get_workflow_graph

    graph = get_workflow_graph()

    # --------------------------------------------------------
    # WORKFLOW IDENTITY
    # --------------------------------------------------------

    run_id = str(uuid.uuid4())[:8]

    # --------------------------------------------------------
    # INITIAL STATE
    # --------------------------------------------------------

    initial_state: AgentState = {

        # Inputs
        "market_context": market_context,
        "portfolio":      portfolio,

        # Agent outputs (empty until nodes run)
        "signal_vote":    None,
        "sentiment_vote": None,
        "risk_vote":      None,
        "portfolio_vote": None,

        # Orchestrator
        "consensus": None,

        # HITL
        "hitl_required": False,
        "hitl_status":   "NOT_REQUIRED",

        # Execution
        "executed_trade":  None,
        "execution_error": None,

        # Observability
        "run_id":          run_id,
        "timestamp":       datetime.utcnow().isoformat(),
        "episodic_memory": [],
        "logs":            [],
        "completed_nodes": [],
    }

    # --------------------------------------------------------
    # THREAD CONFIG
    # --------------------------------------------------------

    if config is None:
        config = {
            "configurable": {
                "thread_id": run_id
            }
        }

    # --------------------------------------------------------
    # EXECUTE
    # --------------------------------------------------------

    logger.info(f"🎬 Starting workflow | run_id={run_id}")

    result = await graph.ainvoke(initial_state, config=config)

    # ainvoke() returns early when interrupt() fires.
    # The caller can check result["hitl_status"] == "PENDING"
    # to know the workflow is paused and needs a resume call.

    logger.info(
        f"🏁 Workflow cycle finished | "
        f"run_id={run_id} | "
        f"hitl_status={result.get('hitl_status', 'N/A')}"
    )

    return {
        "thread_id": run_id,
        "state":     result,
    }