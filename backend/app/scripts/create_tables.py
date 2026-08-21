"""
app/scripts/create_tables.py
=============================
One-time script to create ALL database tables.

RUN THIS ONCE before starting the app for the first time:

    docker compose exec backend python -m app.scripts.create_tables

SAFE TO RUN MULTIPLE TIMES:
-----------------------------
SQLAlchemy uses CREATE TABLE IF NOT EXISTS internally.
Running again will NOT drop or change existing tables or data.

TABLES CREATED:
---------------
    users           — user accounts, roles, passwords
    refresh_tokens  — JWT refresh tokens for each device/session
    workflow_runs   — every agent cycle tied to a user
    trades          — every executed or rejected trade

INVESTING MODE TABLES:
-----------------------
    fundamental_scorecards     — quality verdict per symbol, one row per run
    fundamental_manual_inputs  — figures typed in from annual reports
    investing_holdings         — long-term positions the user bought by hand

LANGGRAPH TABLES:
-----------------
LangGraph creates its own checkpoint tables automatically
when checkpointer.setup() is called in runtime.lifespan().
You do NOT need to create those manually.

TABLE RELATIONSHIPS:
---------------------
    users
      └── refresh_tokens  (user_id FK)
      └── workflow_runs   (user_id FK)
      └── trades          (user_id FK)

    workflow_runs
      └── trades          (workflow_run_id FK)
"""

import asyncio
from loguru import logger

from app.db.postgres import engine
from app.db.base     import Base

# ============================================================
# IMPORT ALL MODELS
#
# Every model that inherits from Base must be imported here.
# SQLAlchemy discovers tables by tracking which classes
# inherit from Base. If you don't import a model here,
# its table will NOT be created.
#
# These imports look unused and are not — importing IS the registration.
# noqa: F401 on each, so `ruff check --select F` (which CI gates on) doesn't
# read the whole block as dead code.
# ============================================================

from app.db.models.user               import User                   # noqa: F401  users
from app.db.models.refresh_token      import RefreshToken           # noqa: F401  refresh_tokens
from app.db.models.workflow_run       import WorkflowRun            # noqa: F401  workflow_runs
from app.db.models.trade              import Trade                  # noqa: F401  trades
from app.db.models.kill_switch_event  import KillSwitchEvent        # noqa: F401  kill_switch_events
from app.db.models.kill_switch_state  import KillSwitchState        # noqa: F401  kill_switch_state (durable halt flag)

# Investing mode — advisory only, so nothing here writes to `trades`.
from app.db.models.fundamental_scorecard    import FundamentalScorecard   # noqa: F401  fundamental_scorecards
from app.db.models.fundamental_manual_input import FundamentalManualInput # noqa: F401  fundamental_manual_inputs
from app.db.models.investing_holding        import InvestingHolding       # noqa: F401  investing_holdings


async def create_tables() -> None:

    logger.info("Creating database tables...")

    async with engine.begin() as conn:
        # run_sync is needed because create_all() is a synchronous
        # SQLAlchemy function — we wrap it for async compatibility
        await conn.run_sync(Base.metadata.create_all)

    # Listed explicitly rather than read off the metadata, so add new tables
    # here too — a table missing from this list still gets created, it just
    # goes unmentioned, which reads like it was skipped.
    logger.info("All tables created successfully:")
    logger.info("  ✓ users")
    logger.info("  ✓ refresh_tokens")
    logger.info("  ✓ workflow_runs")
    logger.info("  ✓ trades")
    logger.info("  ✓ kill_switch_events")
    logger.info("  ✓ kill_switch_state")
    logger.info("  ✓ fundamental_scorecards")
    logger.info("  ✓ fundamental_manual_inputs")
    logger.info("  ✓ investing_holdings")
    logger.info("")
    logger.info("Next step: create your first admin user:")
    logger.info("  docker compose exec backend python -m app.scripts.create_admin")


if __name__ == "__main__":
    asyncio.run(create_tables())