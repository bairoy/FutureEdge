from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
import pytz
from loguru import logger
import asyncio

from app.db.models.trade import Trade
from app.brokers.base import get_broker
from app.data.feed import get_current_price_yfinance

async def calculate_paper_portfolio_data(db: AsyncSession, user_id: str) -> dict:
    """
    Calculates simulated paper trading margins and positions from the database.
    """
    try:
        # 1. Fetch open paper trades
        result = await db.execute(
            select(Trade).where(
                and_(
                    Trade.user_id == user_id,
                    Trade.status == "OPEN",
                    Trade.broker == "paper"
                )
            )
        )
        open_trades = result.scalars().all()
        
        # Fetch closed paper trades of today for daily P&L
        IST = pytz.timezone("Asia/Kolkata")
        today_start = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        
        closed_result = await db.execute(
            select(Trade).where(
                and_(
                    Trade.user_id == user_id,
                    Trade.status == "CLOSED",
                    Trade.broker == "paper",
                    Trade.closed_at >= today_start
                )
            )
        )
        closed_trades = closed_result.scalars().all()
        realized_pnl_today = sum(t.realized_pnl or 0.0 for t in closed_trades)
        
        # 2. Get current price & calculate PnL for each open paper position
        positions = []
        unrealized_pnl = 0.0
        margin_used = 0.0
        
        broker = get_broker()
        for t in open_trades:
            try:
                is_connected = await broker.is_connected()
                if not is_connected:
                    await broker.connect()
                current_price = await broker.get_ltp(t.symbol)
            except Exception:
                try:
                    loop = asyncio.get_running_loop()
                    current_price = await loop.run_in_executor(
                        None, get_current_price_yfinance, t.symbol
                    )
                except Exception:
                    current_price = t.entry_price
            
            # Calculate P&L
            if t.direction == "LONG":
                pos_qty = t.quantity
                pnl = (current_price - t.entry_price) * t.quantity
            else:
                pos_qty = -t.quantity
                pnl = (t.entry_price - current_price) * t.quantity
                
            unrealized_pnl += pnl
            margin_used += t.entry_price * t.quantity
            
            positions.append({
                "trade_id": t.id,
                "symbol": t.symbol,
                "quantity": pos_qty,
                "avg_price": t.entry_price,
                "pnl": round(pnl, 2),
                "notional": round(current_price * t.quantity, 2)
            })
            
        starting_equity = 1000000.0  # ₹10 Lakhs paper money
        total_equity = starting_equity + realized_pnl_today + unrealized_pnl
        margin_available = total_equity - margin_used
        
        return {
            "connected": True,
            "mock_data": True,
            "paper_mode": True,
            "account": {
                "total_equity": round(total_equity, 2),
                "margin_used": round(margin_used, 2),
                "margin_available": round(margin_available, 2),
                "unrealized_pnl": round(unrealized_pnl, 2)
            },
            "positions": positions
        }
    except Exception as pe:
        logger.error(f"Failed to calculate paper portfolio: {pe}")
        return {
            "connected": False,
            "mock_data": True,
            "account": {"total_equity": 0.0, "margin_used": 0.0, "margin_available": 0.0, "unrealized_pnl": 0.0},
            "positions": [],
            "error": str(pe)
        }
