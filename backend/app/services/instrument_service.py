"""
app/services/instrument_service.py
=====================================
Zerodha instrument lookup & dynamic watchlist engine.

WHY THIS IS NEEDED:
--------------------
The old system hardcoded only 5 instrument tokens.
Professional traders need to trade any stock on Zerodha (NSE/BSE),
dynamically search and add instruments to their personal watchlist,
and have the system automatically subscribe to live ticks for those symbols.

HOW IT WORKS:
--------------
1. On startup (or daily at 8 AM), downloads the full Zerodha
   instrument master CSV (~50,000 instruments) and caches it in Redis.
2. The `/api/v1/instruments/search` endpoint fuzzy-searches Redis.
3. Users add symbols to their `user_watchlist` in PostgreSQL.
4. The KiteTicker auto-subscribes to the union of all users' tokens.

ZERODHA INSTRUMENT CSV FORMAT:
--------------------------------
instrument_token, exchange_token, tradingsymbol, name, last_price,
expiry, strike, tick_size, lot_size, instrument_type, segment, exchange

EXAMPLE REDIS STRUCTURE:
--------------------------
Key: "futureedge:instruments:NSE"
Value: JSON list of instrument dicts
TTL: 86400 seconds (1 day)
"""

import json
import asyncio
from typing import Optional

from loguru import logger

from app.db.redis import redis_client
from app.core.config import settings


# ============================================================
# REDIS KEYS
# ============================================================

INSTRUMENTS_KEY_PREFIX = "futureedge:instruments"
INSTRUMENTS_TTL = 86400  # 1 day


# ============================================================
# LOAD INSTRUMENTS FROM ZERODHA
# ============================================================

async def load_instruments(exchange: str = "NSE") -> list[dict]:
    """
    Download and cache Zerodha instruments for an exchange.

    In production (ACTIVE_BROKER=zerodha), this uses the authenticated
    Kite client. In mock mode, returns a small built-in list for testing.

    Args:
        exchange: "NSE", "BSE", "NFO", etc.

    Returns:
        List of instrument dicts
    """
    cache_key = f"{INSTRUMENTS_KEY_PREFIX}:{exchange}"

    # 1. Try cache first
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            instruments = json.loads(cached)
            logger.debug(f"Instruments loaded from cache | {exchange}: {len(instruments)} instruments")
            return instruments
    except Exception as e:
        logger.warning(f"Redis cache miss for instruments: {e}")

    # 2. Load from Zerodha API (if live) or use mock data
    instruments = []

    if settings.ACTIVE_BROKER.lower() == "zerodha":
        instruments = await _load_from_zerodha(exchange)
    else:
        # Fallback to downloading public instruments in mock mode
        try:
            loop = asyncio.get_running_loop()
            instruments = await loop.run_in_executor(None, _fetch_public_instruments, exchange)
        except Exception as e:
            logger.warning(f"Failed to load public instruments: {e} — using fallback mock list")
            instruments = _mock_instruments()

    # 3. Cache in Redis
    if instruments:
        try:
            await redis_client.set(
                cache_key,
                json.dumps(instruments),
                ex=INSTRUMENTS_TTL,
            )
            logger.info(f"Cached {len(instruments)} instruments for {exchange}")
        except Exception as e:
            logger.warning(f"Could not cache instruments: {e}")

    return instruments


async def _load_from_zerodha(exchange: str) -> list[dict]:
    """Load instruments from Zerodha API via executor (blocking call)."""
    try:
        from app.brokers.zerodha import ZerodhaBroker
        broker = ZerodhaBroker()
        await broker.connect()

        if not await broker.is_connected():
            logger.warning("Zerodha not connected — falling back to public endpoint")
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _fetch_public_instruments, exchange)

        def _fetch():
            return broker._kite.instruments(exchange)

        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _fetch)

        instruments = [
            {
                "instrument_token": inst["instrument_token"],
                "exchange_token":   inst.get("exchange_token", 0),
                "tradingsymbol":    inst["tradingsymbol"],
                "name":             inst.get("name", ""),
                "exchange":         inst.get("exchange", exchange),
                "instrument_type":  inst.get("instrument_type", "EQ"),
                "lot_size":         inst.get("lot_size", 1),
                "tick_size":        inst.get("tick_size", 0.05),
            }
            for inst in raw
            if inst.get("instrument_type") in ("EQ", "")  # Equity only
        ]

        logger.info(f"Loaded {len(instruments)} equity instruments from Zerodha {exchange}")
        return instruments

    except Exception as e:
        logger.error(f"Failed to load instruments from Zerodha API: {e} — falling back to public endpoint")
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _fetch_public_instruments, exchange)
        except Exception as pub_err:
            logger.error(f"Failed to load public instruments fallback: {pub_err} — using mock list")
            return _mock_instruments()


def _fetch_public_instruments(exchange: str) -> list[dict]:
    """
    Download and parse public instruments list from Zerodha Kite.
    Does not require authentication.
    """
    import requests
    import csv
    import io

    url = f"https://api.kite.trade/instruments/{exchange}"
    logger.info(f"Downloading public instrument list from {url}...")
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Error fetching public instruments from {url}: {e}")
        raise

    f = io.StringIO(response.text)
    reader = csv.DictReader(f)
    
    instruments = []
    for row in reader:
        # Filter for equities (EQ or empty type)
        inst_type = row.get("instrument_type", "EQ")
        if inst_type in ("EQ", ""):
            try:
                instruments.append({
                    "instrument_token": int(row["instrument_token"]),
                    "exchange_token":   int(row.get("exchange_token", 0) or 0),
                    "tradingsymbol":    row["tradingsymbol"],
                    "name":             row.get("name", ""),
                    "exchange":         row.get("exchange", exchange),
                    "instrument_type":  inst_type,
                    "lot_size":         int(row.get("lot_size", 1) or 1),
                    "tick_size":        float(row.get("tick_size", 0.05) or 0.05),
                })
            except Exception:
                continue

    logger.info(f"Loaded {len(instruments)} public instruments for {exchange}")
    return instruments


def _mock_instruments() -> list[dict]:
    """Fallback mock instrument list for development/mock mode."""
    return [
        {"instrument_token": 256265,  "tradingsymbol": "NIFTY 50",   "name": "Nifty 50 Index",              "exchange": "NSE", "instrument_type": "EQ", "lot_size": 50,  "tick_size": 0.05},
        {"instrument_token": 260105,  "tradingsymbol": "BANKNIFTY",  "name": "Bank Nifty Index",            "exchange": "NSE", "instrument_type": "EQ", "lot_size": 25,  "tick_size": 0.05},
        {"instrument_token": 738561,  "tradingsymbol": "RELIANCE",   "name": "Reliance Industries Ltd",     "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 408065,  "tradingsymbol": "INFY",       "name": "Infosys Ltd",                 "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 2953217, "tradingsymbol": "TCS",        "name": "Tata Consultancy Services",   "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 341249,  "tradingsymbol": "HDFCBANK",   "name": "HDFC Bank Ltd",               "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 1270529, "tradingsymbol": "ICICIBANK",  "name": "ICICI Bank Ltd",              "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 225537,  "tradingsymbol": "WIPRO",      "name": "Wipro Ltd",                   "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 3861249, "tradingsymbol": "SBIN",       "name": "State Bank of India",         "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 2815745, "tradingsymbol": "TATAMOTORS", "name": "Tata Motors Ltd",             "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 492033,  "tradingsymbol": "HINDUNILVR", "name": "Hindustan Unilever Ltd",      "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 1346049, "tradingsymbol": "BAJFINANCE", "name": "Bajaj Finance Ltd",           "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 779521,  "tradingsymbol": "LT",         "name": "Larsen & Toubro Ltd",         "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 969473,  "tradingsymbol": "KOTAKBANK",  "name": "Kotak Mahindra Bank Ltd",     "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
        {"instrument_token": 895745,  "tradingsymbol": "AXISBANK",   "name": "Axis Bank Ltd",               "exchange": "NSE", "instrument_type": "EQ", "lot_size": 1,   "tick_size": 0.05},
    ]


# ============================================================
# SEARCH INSTRUMENTS
# ============================================================

async def search_instruments(
    query: str,
    exchange: str = "NSE",
    limit: int = 20,
) -> list[dict]:
    """
    Search instruments by symbol or company name.

    Performs case-insensitive substring matching on both
    tradingsymbol and company name.

    Args:
        query:    Search string (e.g. "RELIANCE", "infosys", "hdfc")
        exchange: Filter by exchange (default: NSE)
        limit:    Maximum results to return

    Returns:
        List of matching instrument dicts, sorted by relevance
    """
    if not query or len(query) < 2:
        return []

    instruments = await load_instruments(exchange)
    q = query.upper()

    # Sort by relevance: exact prefix match > partial symbol > name match
    exact   = []
    prefix  = []
    partial = []

    for inst in instruments:
        sym  = inst["tradingsymbol"].upper()
        name = inst.get("name", "").upper()

        if sym == q:
            exact.append(inst)
        elif sym.startswith(q):
            prefix.append(inst)
        elif q in sym or q in name:
            partial.append(inst)

    results = (exact + prefix + partial)[:limit]
    return results


# ============================================================
# GET INSTRUMENT TOKEN BY SYMBOL
# ============================================================

async def get_instrument_token(symbol: str, exchange: str = "NSE") -> Optional[int]:
    """
    Look up the Zerodha instrument token for a given symbol.

    The instrument token is required for KiteTicker subscriptions.
    Returns None if the symbol is not found.
    """
    index_map = {
        "NIFTY 50": 256265,
        "NIFTY50": 256265,
        "NIFTY BANK": 260105,
        "BANKNIFTY": 260105,
        "NIFTY FIN SERVICE": 257801,
        "FINNIFTY": 257801,
        "SENSEX": 265,
        "INDIA VIX": 264337,
        "INDIAVIX": 264337,
    }
    sym_upper = symbol.strip().upper()
    if sym_upper in index_map:
        return index_map[sym_upper]

    instruments = await load_instruments(exchange)

    for inst in instruments:
        if inst["tradingsymbol"].upper() == sym_upper:
            return inst["instrument_token"]

    logger.warning(f"Instrument token not found for {symbol} on {exchange}")
    return None
