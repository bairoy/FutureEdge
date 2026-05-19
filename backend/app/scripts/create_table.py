
"""
Create Database Tables
"""

# ============================================================
# IMPORTS
# ============================================================

import asyncio

from app.db.postgres import engine

from app.db.base import Base

# IMPORTANT:
# Import all models so SQLAlchemy registers them

from app.db.models.user import User
from app.db.models.broker_account import (
    BrokerAccount
)
from app.db.models.portfolio import (
    Portfolio
)
from app.db.models.trade import Trade
from app.db.models.agent_decision import (
    AgentDecision
)


# ============================================================
# CREATE TABLES
# ============================================================

async def create_tables():

    async with engine.begin() as conn:

        await conn.run_sync(
            Base.metadata.create_all
        )


    print("✅ All tables created")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(create_tables())