"""
app/brokers/symbol_mapper.py
============================
Centralised symbol mapping and market hours utility.

This module maps strategy symbols (e.g. "NIFTY 50") to broker-specific trading symbols
and determines the correct exchange (NSE, NFO, etc.). It also provides functions to check 
if market hours are open (NSE: 9:15 AM - 3:30 PM IST, Mon-Fri).
"""

from datetime import datetime
import pytz
from loguru import logger

# Mappings for Zerodha Kite
# Map index names to tradeable ETFs/equities so they can be executed seamlessly in Zerodha/Mock
ZERODHA_SYMBOL_MAP = {
    "NIFTY 50": "NIFTYBEES",
    "NIFTY50": "NIFTYBEES",
    "NIFTY BANK": "BANKBEES",
    "BANKNIFTY": "BANKBEES",
    "RELIANCE": "RELIANCE",
    "INFY": "INFY",
    "TCS": "TCS",
}

# Default exchanges for Zerodha
ZERODHA_EXCHANGE_MAP = {
    "NIFTYBEES": "NSE",
    "BANKBEES": "NSE",
    "RELIANCE": "NSE",
    "INFY": "NSE",
    "TCS": "NSE",
}

NSE_TZ = pytz.timezone("Asia/Kolkata")
NSE_OPEN = (9, 15)   # 9:15 AM
NSE_CLOSE = (15, 30)  # 3:30 PM

def map_symbol(symbol: str, broker: str = "zerodha") -> str:
    """
    Map strategy symbol to broker tradingsymbol.
    """
    sym = symbol.strip()
    if broker.lower() == "zerodha":
        mapped = ZERODHA_SYMBOL_MAP.get(sym, sym)
        # Fallback cleanup for other stock symbols
        if mapped == sym:
            mapped = sym.replace(" ", "")
        return mapped
    return sym

def get_exchange(symbol: str, broker: str = "zerodha") -> str:
    """
    Determine the exchange for a given symbol (NSE, NFO, etc.).
    """
    mapped_sym = map_symbol(symbol, broker)
    if broker.lower() == "zerodha":
        return ZERODHA_EXCHANGE_MAP.get(mapped_sym, "NSE")
    return "NSE"

def is_market_open() -> bool:
    """
    Check if the National Stock Exchange (NSE) is currently open.
    Trading hours: Monday - Friday, 9:15 AM to 3:30 PM IST.
    """
    now = datetime.now(NSE_TZ)
    # Check weekday (0 = Monday, 6 = Sunday)
    if now.weekday() >= 5:
        return False
    
    t = (now.hour, now.minute)
    return NSE_OPEN <= t < NSE_CLOSE
