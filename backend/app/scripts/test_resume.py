"""
app/scripts/test_resume.py
===========================
Resume a paused HITL workflow from the command line.

HOW TO RUN:
-----------
    docker compose exec backend python -m app.scripts.test_resume <thread_id> APPROVE
    docker compose exec backend python -m app.scripts.test_resume <thread_id> REJECT "Too risky"

EXAMPLE:
--------
    docker compose exec backend python -m app.scripts.test_resume a1b2c3d4 APPROVE
    docker compose exec backend python -m app.scripts.test_resume a1b2c3d4 REJECT "Market too volatile"
"""

import sys
import asyncio
from pprint import pprint

from loguru import logger
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.graph.builder import create_graph
from app.core.config import settings

import app.graph.runtime as runtime


DB_URI = (
    f"postgresql://"
    f"{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}"
    f"/{settings.POSTGRES_DB}"
)


async def resume(thread_id: str, decision: str, notes: str = ""):

    decision = decision.upper()

    if decision not in {"APPROVE", "REJECT"}:
        print(f"Error: decision must be APPROVE or REJECT, got '{decision}'")
        sys.exit(1)

    config = {"configurable": {"thread_id": thread_id}}

    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:

        runtime._checkpointer  = checkpointer
        runtime.workflow_graph = create_graph().compile(checkpointer=checkpointer)

        graph = runtime.workflow_graph

        logger.info(f"Resuming | thread_id={thread_id} | decision={decision}")

        result = await graph.ainvoke(
            Command(resume={"decision": decision, "notes": notes}),
            config=config,
        )

    print("\n" + "="*60)
    print("RESUME RESULT")
    print("="*60)
    print(f"hitl_status   : {result.get('hitl_status')}")
    print(f"executed_trade: {result.get('executed_trade')}")
    print(f"execution_error: {result.get('execution_error')}")

    print("\nLOGS:")
    for log in result.get("logs", []):
        print(f"  {log}")


if __name__ == "__main__":

    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    _thread_id = sys.argv[1]
    _decision  = sys.argv[2]
    _notes     = sys.argv[3] if len(sys.argv) > 3 else ""

    asyncio.run(resume(_thread_id, _decision, _notes))