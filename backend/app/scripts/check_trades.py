import asyncio
from app.db.postgres import AsyncSessionLocal
from sqlalchemy import select
from app.db.models.user import User
from app.db.models.workflow_run import WorkflowRun
from app.db.models.trade import Trade
from app.db.models.refresh_token import RefreshToken
from app.db.models.kill_switch_event import KillSwitchEvent

async def main():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Trade))
        trades = res.scalars().all()
        print(f"Total trades in DB: {len(trades)}")
        for t in trades:
            print(f"ID: {t.id}, UserID: {t.user_id}, Symbol: {t.symbol}, Direction: {t.direction}, Status: {t.status}, Qty: {t.quantity}, Price: {t.entry_price}, SL: {t.stop_loss}, TP: {t.take_profit}, Exit: {t.exit_price}, PnL: {t.realized_pnl}")

if __name__ == "__main__":
    asyncio.run(main())
