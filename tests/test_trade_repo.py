import pytest
import sys
import os

# Add backend directory to sys.path so app imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.db.base import Base
from app.db.models.user import User
from app.db.models.workflow_run import WorkflowRun
from app.db.models.kill_switch_event import KillSwitchEvent
from app.db.models.refresh_token import RefreshToken
from app.db.models.trade import Trade
from app.db.repos.trade_repo import TradeRepo
from app.graph.state import TradeProposal


@pytest.fixture
async def db_session():
    # Use in-memory SQLite for fast isolated testing
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with Session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_close_trade_long_win(db_session):
    # 1. Create a mock trade proposal
    proposal = TradeProposal(
        symbol="RELIANCE",
        direction="LONG",
        size=50000.0,
        entry_price=2500.0,
        stop_loss=2400.0,
        take_profit=2700.0,
        risk_score=0.2,
    )

    # 2. Save the trade using the repo
    trade = await TradeRepo.save_trade(
        session=db_session,
        proposal=proposal,
        run_id="run123",
        user_id="user123",
        quantity=20,
    )

    assert trade.status == "OPEN"
    assert trade.quantity == 20

    # 3. Close the trade with a profit (exit price 2600)
    closed = await TradeRepo.close_trade(
        session=db_session,
        trade_id=trade.id,
        user_id="user123",
        exit_price=2600.0,
    )

    # P&L = (2600 - 2500) * 20 = 2000
    assert closed.status == "CLOSED"
    assert closed.realized_pnl == 2000.0
    # P&L % = ((2600 - 2500) / 2500) * 100 = 4.0%
    assert closed.pnl_pct == 4.0


@pytest.mark.asyncio
async def test_close_trade_short_loss(db_session):
    proposal = TradeProposal(
        symbol="INFY",
        direction="SHORT",
        size=30000.0,
        entry_price=1500.0,
        stop_loss=1550.0,
        take_profit=1400.0,
        risk_score=0.3,
    )

    trade = await TradeRepo.save_trade(
        session=db_session,
        proposal=proposal,
        run_id="run456",
        user_id="user123",
        quantity=20,
    )

    closed = await TradeRepo.close_trade(
        session=db_session,
        trade_id=trade.id,
        user_id="user123",
        exit_price=1530.0,
    )

    # For SHORT, P&L = (entry - exit) * quantity = (1500 - 1530) * 20 = -600
    assert closed.status == "CLOSED"
    assert closed.realized_pnl == -600.0
    # P&L % = ((1500 - 1530) / 1500) * 100 = -2.0%
    assert closed.pnl_pct == -2.0
