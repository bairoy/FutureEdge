"""
app/data/fundamentals_scraper.py
==================================
Fetches fundamental data (ratios, P&L, balance sheet, shareholding, and
Screener's own machine-generated pros/cons flags) from Screener.in.

WHY SCREENER.IN, NOT KITE CONNECT:
------------------------------------
Zerodha has confirmed on their own developer forum that fundamental data
shown inside Kite (P/E, ratios, financials widget) is licensed from a data
vendor (Tijori) under terms that forbid redistribution via the API.
Kite Connect gives you market data + order execution — never fundamentals.
Screener.in is the source you already use for manual research, so this
module automates exactly that workflow.

IMPORTANT — THIS IS A SCRAPER, NOT AN OFFICIAL API:
-----------------------------------------------------
Screener.in has no public API for this data. This module:
  1. Fetches the public company page (no login required for the data below)
  2. Parses the HTML tables with BeautifulSoup
  3. Caches aggressively (default 3 days) since fundamentals don't change
     daily — there is no reason to hit Screener more than once every few
     days per symbol
  4. Rate-limits itself (min delay between requests) and identifies itself
     honestly via User-Agent
  5. Is written defensively — if Screener changes their markup, this should
     fail loudly (raise) rather than silently return wrong numbers

VERIFY BEFORE RELYING ON THIS IN PRODUCTION:
----------------------------------------------
The CSS/ID selectors below were checked against a live fetch of Screener's
Reliance Industries page. Screener does periodically change their markup —
if this stops working, view-source a company page and check these selectors
against `EXPECTED_SECTION_IDS` first; that's the most common failure mode.

Respect Screener's terms of service (screener.in/guides/terms/) and
robots.txt. This module is for personal research/analysis, one request
every few days per symbol — not high-frequency polling or redistribution.

USAGE:
------
    from app.data.fundamentals_scraper import get_fundamentals

    data = await get_fundamentals("RELIANCE")
    # data.top_ratios["Stock P/E"] -> 23.6
    # data.pros -> ["Company is almost debt free.", ...]
    # data.cons -> ["Company has a low return on equity of 8.77%...", ...]
    # data.annual_pnl -> {"Mar 2026": {"Sales": 1055780, "Net Profit": 95754, ...}, ...}
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.core.config import settings
from app.db.redis import redis_client

# ============================================================
# CONFIGURATION
# ============================================================

SCREENER_BASE_URL = "https://www.screener.in"

# Fundamentals change quarterly at most — cache hard.
# Override with settings.FUNDAMENTALS_CACHE_SECONDS if you add it to config.py.
CACHE_SECONDS = getattr(settings, "FUNDAMENTALS_CACHE_SECONDS", 3 * 24 * 3600)  # 3 days

REQUEST_TIMEOUT = 15.0

# Minimum gap between live (non-cached) requests to Screener, across the
# whole process — a simple courtesy rate limit, not per-symbol.
MIN_REQUEST_GAP_SECONDS = 2.0
_last_request_at: float = 0.0
_request_lock = asyncio.Lock()

# Screener changes their markup occasionally — these anchor IDs are the
# same ones used in the page's own table-of-contents links, so they're
# reasonably stable across redesigns.
EXPECTED_SECTION_IDS = [
    "top-ratios", "analysis", "quarters", "profit-loss",
    "balance-sheet", "cash-flow", "ratios", "shareholding",
]

HEADERS = {
    # Honest identification — do not spoof a browser UA to evade blocking.
    "User-Agent": "FutureEdge-FundamentalAgent/1.0 (personal research tool; contact: <your-email>)",
    "Accept": "text/html",
}


@dataclass
class FundamentalRaw:
    """Raw scraped data for one symbol, before ratio/flag computation."""
    symbol: str
    fetched_at: datetime
    consolidated: bool

    top_ratios: dict[str, float] = field(default_factory=dict)   # P/E, ROE, ROCE, Book Value, Div Yield...
    pros: list[str] = field(default_factory=list)                 # Screener's own green flags
    cons: list[str] = field(default_factory=list)                 # Screener's own red flags

    # {period_label: {line_item: value}} — most recent last
    quarterly_pnl: dict[str, dict[str, float]] = field(default_factory=dict)
    annual_pnl: dict[str, dict[str, float]] = field(default_factory=dict)
    balance_sheet: dict[str, dict[str, float]] = field(default_factory=dict)
    cash_flow: dict[str, dict[str, float]] = field(default_factory=dict)
    ratios_trend: dict[str, dict[str, float]] = field(default_factory=dict)  # debtor days, ROCE trend etc.
    shareholding: dict[str, dict[str, float]] = field(default_factory=dict)  # Promoters/FII/DII % by quarter

    growth: dict[str, dict[str, float]] = field(default_factory=dict)  # Compounded sales/profit growth, ROE trend

    raw_html_hash: str | None = None   # so callers can detect "markup probably changed"


# ============================================================
# PUBLIC ENTRY POINT
# ============================================================

async def get_fundamentals(symbol: str, consolidated: bool = True, force_refresh: bool = False) -> FundamentalRaw:
    """
    Get fundamental data for a symbol, using the Redis cache unless
    force_refresh=True or the cache is stale.
    """
    cache_key = f"futureedge:fundamentals:{symbol}:{'consolidated' if consolidated else 'standalone'}"

    if not force_refresh:
        cached = await _get_cached(cache_key)
        if cached is not None:
            logger.debug(f"Fundamentals cache hit | {symbol}")
            return cached

    raw_html = await _fetch_page(symbol, consolidated)
    parsed = _parse_page(symbol, consolidated, raw_html)

    await _set_cached(cache_key, parsed)
    return parsed


# ============================================================
# HTTP FETCH (rate-limited, cached)
# ============================================================

async def _fetch_page(symbol: str, consolidated: bool) -> str:
    global _last_request_at

    path = f"/company/{symbol}/consolidated/" if consolidated else f"/company/{symbol}/"
    url = f"{SCREENER_BASE_URL}{path}"

    async with _request_lock:
        now = asyncio.get_event_loop().time()
        wait = MIN_REQUEST_GAP_SECONDS - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
                resp = await client.get(url)

            _last_request_at = asyncio.get_event_loop().time()

            if resp.status_code == 404:
                # Consolidated view doesn't exist for this company (common for
                # standalone-only businesses) — fall back to standalone.
                if consolidated:
                    logger.info(f"No consolidated page for {symbol}, falling back to standalone")
                    return await _fetch_page(symbol, consolidated=False)
                raise ValueError(f"Screener has no page for symbol '{symbol}'")

            resp.raise_for_status()
            return resp.text

        except httpx.HTTPError as e:
            logger.error(f"Screener fetch failed for {symbol}: {e}")
            raise


# ============================================================
# HTML PARSING
# ============================================================

def _parse_number(text: str) -> float | None:
    """Parse a Screener numeric cell: strips commas, %, ₹, Cr., handles negatives in parens."""
    if text is None:
        return None
    t = text.strip().replace(",", "").replace("₹", "").replace("%", "").strip()
    if t in ("", "-", "—"):
        return None
    negative = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        val = float(t)
        return -val if negative else val
    except ValueError:
        return None


def _parse_top_ratios(soup: BeautifulSoup) -> dict[str, float]:
    """
    The top-of-page ratio grid: Market Cap, Current Price, Stock P/E, Book Value,
    Dividend Yield, ROCE %, ROE %, Face Value, etc.
    Rendered as a <ul> of <li> items, each with a label span and a value span.
    """
    ratios: dict[str, float] = {}
    container = soup.find(id="top-ratios")
    if not container:
        logger.warning("Could not find #top-ratios section — Screener markup may have changed")
        return ratios

    for li in container.find_all("li"):
        label_el = li.find(class_="name") or li.find("span")
        value_el = li.find(class_="number") or li.find_all("span")
        label = label_el.get_text(strip=True) if label_el else None
        value_text = li.get_text(" ", strip=True)
        if not label:
            continue
        # Value is whatever's left after stripping the label from the full text
        value_str = value_text.replace(label, "", 1).strip()
        val = _parse_number(value_str)
        if val is not None:
            ratios[label] = val

    return ratios


def _parse_pros_cons(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    """Screener's machine-generated 'Pros' / 'Cons' checklist — this IS a red/green flag system already."""
    pros, cons = [], []
    analysis = soup.find(id="analysis")
    if not analysis:
        return pros, cons

    pros_list = analysis.find(class_="pros")
    cons_list = analysis.find(class_="cons")

    if pros_list:
        pros = [li.get_text(strip=True) for li in pros_list.find_all("li")]
    if cons_list:
        cons = [li.get_text(strip=True) for li in cons_list.find_all("li")]

    return pros, cons


def _parse_data_table(soup: BeautifulSoup, section_id: str) -> dict[str, dict[str, float]]:
    """
    Generic parser for Screener's period-columns tables (Quarterly Results,
    Profit & Loss, Balance Sheet, Cash Flow, Ratios).

    Returns {period_label: {row_label: value}}, e.g.:
        {"Mar 2026": {"Sales": 1055780.0, "Net Profit": 95754.0, ...}, ...}
    """
    result: dict[str, dict[str, float]] = {}
    section = soup.find(id=section_id)
    if not section:
        logger.warning(f"Could not find #{section_id} section — Screener markup may have changed")
        return result

    table = section.find("table")
    if not table:
        return result

    header_cells = table.find("thead").find_all("th") if table.find("thead") else []
    periods = [th.get_text(strip=True) for th in header_cells[1:]]  # skip first (row-label) column

    for row in table.find("tbody").find_all("tr") if table.find("tbody") else []:
        cells = row.find_all("td")
        if not cells:
            continue
        row_label = cells[0].get_text(strip=True).rstrip("+").strip()
        for period, cell in zip(periods, cells[1:]):
            val = _parse_number(cell.get_text(strip=True))
            if val is None:
                continue
            result.setdefault(period, {})[row_label] = val

    return result


def _parse_growth_tables(soup: BeautifulSoup) -> dict[str, dict[str, float]]:
    """
    The small summary tables next to Profit & Loss: Compounded Sales Growth,
    Compounded Profit Growth, Stock Price CAGR, Return on Equity (by lookback period).
    """
    growth: dict[str, dict[str, float]] = {}
    section = soup.find(id="profit-loss")
    if not section:
        return growth

    for table in section.find_all("table"):
        caption = table.find_previous(["p", "h3"])
        title = caption.get_text(strip=True) if caption else "unknown"
        rows: dict[str, float] = {}
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) == 2:
                label = cells[0].get_text(strip=True)
                val = _parse_number(cells[1].get_text(strip=True))
                if val is not None:
                    rows[label] = val
        if rows:
            growth[title] = rows

    return growth


def _parse_page(symbol: str, consolidated: bool, html: str) -> FundamentalRaw:
    soup = BeautifulSoup(html, "lxml")

    found_sections = [sid for sid in EXPECTED_SECTION_IDS if soup.find(id=sid)]
    if len(found_sections) < len(EXPECTED_SECTION_IDS) // 2:
        # If we're missing more than half the expected sections, something
        # structural has changed (blocked, redirected to login, redesigned) —
        # fail loudly instead of returning a mostly-empty scorecard.
        raise RuntimeError(
            f"Screener page for {symbol} is missing most expected sections "
            f"(found {found_sections}) — markup likely changed or request was blocked."
        )

    pros, cons = _parse_pros_cons(soup)

    return FundamentalRaw(
        symbol=symbol,
        fetched_at=datetime.now(timezone.utc),
        consolidated=consolidated,
        top_ratios=_parse_top_ratios(soup),
        pros=pros,
        cons=cons,
        quarterly_pnl=_parse_data_table(soup, "quarters"),
        annual_pnl=_parse_data_table(soup, "profit-loss"),
        balance_sheet=_parse_data_table(soup, "balance-sheet"),
        cash_flow=_parse_data_table(soup, "cash-flow"),
        ratios_trend=_parse_data_table(soup, "ratios"),
        shareholding=_parse_data_table(soup, "shareholding"),
        growth=_parse_growth_tables(soup),
    )


# ============================================================
# REDIS CACHE (serialize dataclass as JSON)
# ============================================================

async def _get_cached(cache_key: str) -> FundamentalRaw | None:
    import json
    try:
        raw = await redis_client.get(cache_key)
        if not raw:
            return None
        d = json.loads(raw)
        d["fetched_at"] = datetime.fromisoformat(d["fetched_at"])
        return FundamentalRaw(**d)
    except Exception as e:
        logger.warning(f"Fundamentals cache read failed: {e}")
        return None


async def _set_cached(cache_key: str, data: FundamentalRaw) -> None:
    import json
    from dataclasses import asdict
    try:
        d = asdict(data)
        d["fetched_at"] = data.fetched_at.isoformat()
        await redis_client.setex(cache_key, CACHE_SECONDS, json.dumps(d))
    except Exception as e:
        logger.warning(f"Fundamentals cache write failed: {e}")
