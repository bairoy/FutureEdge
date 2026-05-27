"""
app/graph/builder.py
=====================
LangGraph graph definition and run_agent_cycle function.

CHANGE: run_agent_cycle now accepts user_id.
The user_id is stored in AgentState so execution_agent
can write it to the trades table.
"""

import uuid
from datetime import datetime

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


def create_graph() -> StateGraph:
    """
    Build the LangGraph workflow graph (stateless — no checkpointer here).
    Checkpointer is attached in runtime.lifespan() via compile().
    """

    builder = StateGraph(AgentState)

    builder.add_node("signal_agent",    signal_agent_node)
    builder.add_node("sentiment_agent", sentiment_agent_node)
    builder.add_node("risk_agent",      risk_agent_node)
    builder.add_node("portfolio_agent", portfolio_agent_node)
    builder.add_node("orchestrator",    orchestrator_node)
    builder.add_node("human_review",    human_review_node)
    builder.add_node("execution",       execution_node)

    # Parallel fan-out: all 4 agents start simultaneously
    builder.add_edge(START, "signal_agent")
    builder.add_edge(START, "sentiment_agent")
    builder.add_edge(START, "risk_agent")
    builder.add_edge(START, "portfolio_agent")

    # Fan-in: all agents must finish before orchestrator runs
    builder.add_edge("signal_agent",    "orchestrator")
    builder.add_edge("sentiment_agent", "orchestrator")
    builder.add_edge("risk_agent",      "orchestrator")
    builder.add_edge("portfolio_agent", "orchestrator")

    # Conditional: HITL or direct execution
    builder.add_conditional_edges(
        "orchestrator",
        should_human_review,
        {"human_review": "human_review", "execute": "execution"},
    )

    builder.add_edge("human_review", "execution")
    builder.add_edge("execution",    END)

    return builder


async def run_agent_cycle(
    market_context: MarketContext,
    portfolio:      PortfolioSnapshot,
    user_id:        str | None = None,   # NEW: which user triggered this
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
        "market_context": market_context,
        "portfolio":      portfolio,

        # Agent outputs (populated by each agent node)
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

        # Observability + user tracking
        "run_id":          run_id,
        "user_id":         user_id or "anonymous",
        "timestamp":       datetime.utcnow().isoformat(),
        "episodic_memory": [],
        "logs":            [],
        "completed_nodes": [],
    }

    if config is None:
        config = {"configurable": {"thread_id": run_id}}

    logger.info(f"Starting workflow | run_id={run_id} | user_id={user_id}")

    result = await graph.ainvoke(initial_state, config=config)

    logger.info(
        f"Workflow finished | run_id={run_id} | "
        f"hitl_status={result.get('hitl_status', 'N/A')}"
    )

    return {"thread_id": run_id, "state": result}