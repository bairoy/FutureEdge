"""
backend/app/api/routes/trades_router.py
========================================
Trade history endpoints — reads from PostgreSQL trades table.

Powers the frontend Trade History page (GET /api/v1/trades).

ENDPOINTS:
----------
GET  /api/v1/trades              → current user's trade list (paginated)
GET  /api/v1/trades/{trade_id}   → single trade with full agent_consensus

MULTI-USER ISOLATION:
---------------------
Every query filters by Trade.user_id == current_user.id so users
can only ever see their own trades. Admin can pass ?all=true to
see all users' trades for system-wide audit.

USAGE FROM FRONTEND:
--------------------
    const { data } = useSWR("/api/v1/trades", fetcher);
    // returns list of Trade objects for the logged-in user
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_db, require_viewer
from app.db.models.trade   import Trade
from app.db.models.user    import User


router = APIRouter(prefix="/api/v1", tags=["Trades"])


# ============================================================
# LIST TRADES
# ============================================================

@router.get("/trades", summary="Get trade history for current user")
async def list_trades(
    limit:        int  = Query(default=100, le=500, description="Max records to return"),
    offset:       int  = Query(default=0,   ge=0,   description="Pagination offset"),
    all_users:    bool = Query(default=False, alias="all", description="Admin only: see all users"),
    current_user: User = Depends(require_viewer),
    db:           AsyncSession = Depends(get_db),
):
    """
    Returns the calling user's trades, newest first.

    Admin users can pass ?all=true to see every trade in the system.
    This is useful for system-wide PnL reporting and audit.

    Regular users always see only their own trades regardless of params.
    """

    query = select(Trade).order_by(desc(Trade.opened_at))

    # Enforce ownership — only admin + all=true can see other users' trades
    if not (all_users and current_user.role == "admin"):
        query = query.where(Trade.user_id == current_user.id)

    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    trades = result.scalars().all()

    return [_serialize(t) for t in trades]


# ============================================================
# SINGLE TRADE
# ============================================================

@router.get("/trades/{trade_id}", summary="Get a single trade by ID")
async def get_trade(
    trade_id:     str,
    current_user: User = Depends(require_viewer),
    db:           AsyncSession = Depends(get_db),
):
    """
    Returns one trade by its UUID.

    Ownership enforced: a user can only fetch their own trades.
    Admin can fetch any trade.
    Returns 404 for both "not found" and "not yours" — prevents
    information leakage about other users' trade IDs.
    """

    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade  = result.scalar_one_or_none()

    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if current_user.role != "admin" and trade.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Trade not found")

    return _serialize(trade)


# ============================================================
# CLOSE/EXIT TRADE
# ============================================================

@router.post("/trades/{trade_id}/close", summary="Manually exit/close an open trade")
async def close_trade_endpoint(
    trade_id:     str,
    current_user: User = Depends(require_viewer),
    db:           AsyncSession = Depends(get_db),
):
    """
    Manually close an open position/trade.
    Fetches the current LTP, places a counter-order via the broker,
    and updates the trade record to CLOSED.
    """
    # 1. Fetch the trade
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade  = result.scalar_one_or_none()

    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if current_user.role != "admin" and trade.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Trade not found")

    if trade.status != "OPEN":
        raise HTTPException(status_code=400, detail="Only OPEN trades can be closed")

    # 2. Get current price
    from app.brokers.base import get_broker
    if trade.broker in ("paper", "mock"):
        from app.brokers.mock import MockBroker
        broker = MockBroker()
    else:
        broker = get_broker()

    try:
        current_price = await broker.get_ltp(trade.symbol)
    except Exception:
        try:
            from app.data.feed import get_current_price_yfinance
            import asyncio
            loop = asyncio.get_running_loop()
            current_price = await loop.run_in_executor(
                None, get_current_price_yfinance, trade.symbol
            )
        except Exception:
            current_price = trade.entry_price

    if current_price <= 0:
        current_price = trade.entry_price

    # 3. Place counter-order via broker (opposite direction)
    actual_exit = current_price
    if trade.quantity > 0:
        try:
            exit_direction = "SHORT" if trade.direction == "LONG" else "LONG"
            price_buffer = current_price * 0.0005
            limit_price = round(current_price - price_buffer if exit_direction == "SHORT" else current_price + price_buffer, 2)

            order_result = await broker.place_order(
                symbol=trade.symbol,
                direction=exit_direction,
                quantity=float(trade.quantity),
                order_type="LIMIT",
                price=limit_price,
            )
            if order_result.success:
                actual_exit = order_result.fill_price or current_price
        except Exception as e:
            from loguru import logger
            logger.error(f"Manual exit broker order failed for trade {trade.id}: {e}")

    # 4. Close trade in database
    from app.db.repos.trade_repo import TradeRepo
    closed_trade = await TradeRepo.close_trade(
        session=db,
        trade_id=trade.id,
        user_id=trade.user_id,
        exit_price=actual_exit,
    )

    if not closed_trade:
        raise HTTPException(status_code=500, detail="Failed to close trade record")

    # 5. Publish Redis event
    from app.db.redis import redis_client, CHANNEL_TRADE_EXECUTED
    import json
    try:
        await redis_client.publish(
            CHANNEL_TRADE_EXECUTED,
            json.dumps({
                "event":     "TRADE_CLOSED",
                "reason":    "MANUAL_EXIT",
                "trade_id":  trade.id,
                "user_id":   trade.user_id,
                "symbol":    trade.symbol,
                "direction": trade.direction,
                "entry":     trade.entry_price,
                "exit":      actual_exit,
                "pnl":       closed_trade.realized_pnl,
                "pnl_pct":   closed_trade.pnl_pct,
            }),
        )
    except Exception:
        pass

    return {
        "success": True,
        "trade": _serialize(closed_trade)
    }


# ============================================================
# SERIALIZER
# ============================================================

def _serialize(t: Trade) -> dict:
    """
    Convert a Trade ORM object to a plain dict for JSON response.
    Handles datetime → ISO string conversion.
    """
    return {
        "id":                t.id,
        "user_id":           t.user_id,
        "run_id":            t.run_id,
        "workflow_run_id":   t.workflow_run_id,
        "symbol":            t.symbol,
        "direction":         t.direction,
        "size":              t.size,
        "quantity":          t.quantity,
        "entry_price":       t.entry_price,
        "stop_loss":         t.stop_loss,
        "take_profit":       t.take_profit,
        "risk_score":        t.risk_score,
        "status":            t.status,
        "hitl_required":     t.hitl_required,
        "human_approved":    t.human_approved,
        "human_notes":       t.human_notes,
        "agent_consensus":   t.agent_consensus,
        "broker":            t.broker,
        "broker_order_id":   t.broker_order_id,
        "actual_fill_price": t.actual_fill_price,
        "slippage":          t.slippage,
        "exit_price":        t.exit_price,
        "realized_pnl":      t.realized_pnl,
        "pnl_pct":           t.pnl_pct,
        "opened_at":         t.opened_at.isoformat() if t.opened_at else None,
        "closed_at":         t.closed_at.isoformat() if t.closed_at else None,
    }