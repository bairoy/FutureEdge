import asyncio
from app.db.postgres import AsyncSessionLocal
from sqlalchemy import update
from app.db.models.user import User
from app.db.models.workflow_run import WorkflowRun
from app.db.models.trade import Trade
from app.db.models.refresh_token import RefreshToken
from app.db.models.kill_switch_event import KillSwitchEvent

async def main():
    async with AsyncSessionLocal() as session:
        # Reset all paper trades that were closed back to OPEN status
        result = await session.execute(
            update(Trade)
            .where(Trade.broker == "paper", Trade.status == "CLOSED")
            .values(status="OPEN", exit_price=None, realized_pnl=None, pnl_pct=None)
        )
        await session.commit()
        print("Successfully reset closed paper trades back to OPEN.")

if __name__ == "__main__":
    asyncio.run(main())
