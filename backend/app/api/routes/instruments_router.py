"""
app/api/routes/instruments_router.py
======================================
API endpoints for instrument (stock) search and watchlist management.

ENDPOINTS:
-----------
GET  /api/v1/instruments/search?q=RELIANCE&exchange=NSE
    → Returns matching instruments for user-driven symbol selection.
    → Powers the searchable stock picker in the dashboard.

GET  /api/v1/instruments/token?symbol=RELIANCE
    → Returns the Zerodha instrument_token for a given symbol.
    → Used by the frontend to validate watchlist additions.
"""

from fastapi import APIRouter, Query, HTTPException
from loguru import logger

from app.services.instrument_service import search_instruments, get_instrument_token

router = APIRouter(prefix="/api/v1/instruments", tags=["instruments"])


@router.get("/search")
async def search_stocks(
    q: str = Query(..., min_length=2, max_length=50, description="Stock symbol or company name"),
    exchange: str = Query("NSE", description="Exchange: NSE, BSE, NFO"),
    limit: int = Query(20, ge=1, le=50, description="Max results"),
):
    """
    Search for tradeable instruments by symbol or company name.

    Supports fuzzy prefix matching — e.g. "RELI" matches "RELIANCE".
    Results are sorted by relevance: exact match > prefix match > partial.

    Returns:
        List of instrument dicts with token, symbol, name, exchange, lot_size
    """
    try:
        results = await search_instruments(query=q, exchange=exchange, limit=limit)
        return {
            "query":    q,
            "exchange": exchange,
            "count":    len(results),
            "results":  results,
        }
    except Exception as e:
        logger.error(f"Instrument search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")


@router.get("/token")
async def get_token(
    symbol: str = Query(..., description="Trading symbol e.g. RELIANCE"),
    exchange: str = Query("NSE", description="Exchange"),
):
    """
    Get the Zerodha instrument token for a given symbol.

    The instrument token is required to subscribe to live ticks via
    KiteTicker WebSocket.
    """
    try:
        token = await get_instrument_token(symbol=symbol, exchange=exchange)
        if token is None:
            raise HTTPException(
                status_code=404,
                detail=f"Symbol '{symbol}' not found on {exchange}",
            )
        return {"symbol": symbol, "exchange": exchange, "instrument_token": token}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token lookup failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
