import asyncio
from app.db.postgres import AsyncSessionLocal
from sqlalchemy import delete
from app.db.models.user import User
from app.db.models.workflow_run import WorkflowRun
from app.db.models.trade import Trade
from app.db.models.refresh_token import RefreshToken
from app.db.models.kill_switch_event import KillSwitchEvent

async def main():
    async with AsyncSessionLocal() as session:
        # Delete closed and rejected trades
        result = await session.execute(
            delete(Trade).where(Trade.status.in_(["CLOSED", "REJECTED"]))
        )
        await session.commit()
        print("Successfully wiped closed and rejected trades from database.")

if __name__ == "__main__":
    asyncio.run(main())
