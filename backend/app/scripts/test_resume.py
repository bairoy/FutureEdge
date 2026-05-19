"""
HITL Resume Test

Use this after test_agent.py pauses on a HITL interrupt.

Usage:
------
python test_resume.py <thread_id> <APPROVE|REJECT> [notes]

Example:
--------
python test_resume.py a1b2c3d4 APPROVE "Looks safe"
python test_resume.py a1b2c3d4 REJECT  "Risk too high"
"""

# ============================================================
# IMPORTS
# ============================================================

import sys
import asyncio
from pprint import pprint

from loguru import logger

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.graph.builder import create_graph
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
# RESUME
# ============================================================

async def resume(thread_id: str, decision: str, notes: str = ""):

    decision = decision.upper()

    if decision not in {"APPROVE", "REJECT"}:
        print(f"❌ Invalid decision '{decision}'. Use APPROVE or REJECT.")
        sys.exit(1)

    config = {"configurable": {"thread_id": thread_id}}

    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:

        # Re-compile graph with the same checkpointer so
        # LangGraph can load the saved checkpoint
        runtime._checkpointer  = checkpointer
        runtime.workflow_graph = create_graph().compile(
            checkpointer=checkpointer
        )

        graph = runtime.workflow_graph

        logger.info(
            f"▶️  Resuming | thread_id={thread_id} | decision={decision}"
        )

        result = await graph.ainvoke(
            Command(resume={"decision": decision, "notes": notes}),
            config=config,
        )

    print("\n")
    print("=" * 60)
    print("RESUME RESULT")
    print("=" * 60)
    print(f"hitl_status : {result.get('hitl_status')}")
    print(f"exec error  : {result.get('execution_error')}")
    print()
    pprint(result)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    _thread_id = sys.argv[1]
    _decision  = sys.argv[2]
    _notes     = sys.argv[3] if len(sys.argv) > 3 else ""

    asyncio.run(resume(_thread_id, _decision, _notes))