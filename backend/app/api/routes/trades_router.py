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