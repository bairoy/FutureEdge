"""
app/agents/tools/market_tools.py
================================
Market data tools for multi-agent trade analysis.
"""

from loguru import logger
from app.brokers.base import get_broker
from app.brokers.symbol_mapper import is_market_open, map_symbol, get_exchange
from app.data.feed import get_current_price_yfinance

async def get_live_price(symbol: str) -> float:
    """
    Get the latest traded price (LTP) for a symbol.
    First tries the active broker, and if unavailable/fails, falls back to yfinance.
    """
    try:
        broker = get_broker()
        if await broker.is_connected():
            mapped_symbol = map_symbol(symbol)
            price = await broker.get_ltp(mapped_symbol)
            if price > 0:
                logger.info(f"Tool: get_live_price from broker for {symbol} ({mapped_symbol}) = {price}")
                return price
    except Exception as e:
        logger.warning(f"Failed to get live price from broker for {symbol}: {e}")

    # Fallback to yfinance
    try:
        price = get_current_price_yfinance(symbol)
        logger.info(f"Tool: get_live_price from yfinance fallback for {symbol} = {price}")
        return price
    except Exception as e:
        logger.error(f"Failed to get live price from yfinance fallback for {symbol}: {e}")
        return 0.0

async def get_order_book(symbol: str) -> dict:
    """
    Fetch the order book depth (bid/ask prices and volumes) for a symbol.
    """
    broker = get_broker()
    mapped_symbol = map_symbol(symbol)
    
    # If using Zerodha and connected, try fetching order book depth
    if await broker.is_connected() and hasattr(broker, "_kite") and broker._kite:
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            exchange = get_exchange(symbol)
            # Kite.quote returns full quote details including depth
            quote = await loop.run_in_executor(None, broker._kite.quote, f"{exchange}:{mapped_symbol}")
            depth = quote.get(f"{exchange}:{mapped_symbol}", {}).get("depth", {})
            if depth:
                return {
                    "symbol": symbol,
                    "mapped_symbol": mapped_symbol,
                    "exchange": exchange,
                    "bids": [{"price": b.get("price"), "quantity": b.get("quantity")} for b in depth.get("buy", [])],
                    "asks": [{"price": a.get("price"), "quantity": a.get("quantity")} for a in depth.get("sell", [])],
                }
        except Exception as e:
            logger.warning(f"Could not fetch real depth from Zerodha: {e}")

    # Fallback/Mock order book
    price = await get_live_price(symbol)
    if price <= 0:
        price = 100.0
    return {
        "symbol": symbol,
        "mapped_symbol": mapped_symbol,
        "exchange": get_exchange(symbol),
        "bids": [
            {"price": round(price * 0.999, 2), "quantity": 1500},
            {"price": round(price * 0.998, 2), "quantity": 2200},
            {"price": round(price * 0.997, 2), "quantity": 3500},
        ],
        "asks": [
            {"price": round(price * 1.001, 2), "quantity": 1200},
            {"price": round(price * 1.002, 2), "quantity": 1800},
            {"price": round(price * 1.003, 2), "quantity": 2900},
        ],
    }

async def get_nse_status() -> dict:
    """
    Get current NSE market status, session, and timezone.
    """
    is_open = is_market_open()
    from datetime import datetime
    import pytz
    NSE_TZ = pytz.timezone("Asia/Kolkata")
    now = datetime.now(NSE_TZ)
    
    return {
        "is_open": is_open,
        "timezone": "Asia/Kolkata",
        "current_time": now.isoformat(),
        "trading_hours": "09:15 to 15:30 IST",
        "weekday": now.strftime("%A"),
    }

async def get_instrument_details(symbol: str) -> dict:
    """
    Get trading instrument details (token, exchange, lot size).
    """
    from app.services.instrument_service import get_instrument_token
    
    mapped_symbol = map_symbol(symbol)
    exchange = get_exchange(symbol)
    
    token = await get_instrument_token(symbol)
    if not token:
        token = await get_instrument_token(mapped_symbol)
            
    return {
        "symbol": symbol,
        "mapped_symbol": mapped_symbol,
        "exchange": exchange,
        "token": token or "MOCK_TOKEN",
        "lot_size": 1,
    }


async def get_india_vix() -> float:
    """
    Fetch the latest India VIX (fear index) from yfinance.
    """
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        import yfinance as yf
        ticker = yf.Ticker("^INDIAVIX")
        price = await loop.run_in_executor(None, lambda: ticker.fast_info.last_price)
        logger.info(f"Tool: get_india_vix = {price}")
        return round(float(price), 2)
    except Exception as e:
        logger.error(f"Failed to fetch India VIX: {e} — defaulting to 15.0")
        return 15.0


async def get_usd_inr() -> dict:
    """
    Fetch USD/INR rate and daily percent change.
    """
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        import yfinance as yf
        ticker = yf.Ticker("USDINR=X")
        
        def _fetch_usd():
            info = ticker.fast_info
            price = info.last_price
            prev_close = info.previous_close
            change_pct = ((price - prev_close) / prev_close) * 100.0 if prev_close else 0.0
            return price, change_pct
            
        price, change_pct = await loop.run_in_executor(None, _fetch_usd)
        logger.info(f"Tool: get_usd_inr = {price} ({change_pct:+.2f}%)")
        return {
            "rate": round(float(price), 4),
            "change_pct": round(float(change_pct), 2)
        }
    except Exception as e:
        logger.error(f"Failed to fetch USD/INR: {e} — defaulting to neutral")
        return {
            "rate": 83.50,
            "change_pct": 0.0
        }

