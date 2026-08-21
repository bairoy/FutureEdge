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
from dataclasses import dataclass, field, fields
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


class SymbolNotFound(ValueError):
    """
    Screener has no page for this symbol, under either view.

    Distinct from a scrape that failed: a mistyped ticker is the user's to fix
    and should say so, while a transient failure is ours and should not be
    reported as "no such company". Callers map the two to different responses.
    """

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

    # Screener's internal numeric company id (from data-company-id on the page).
    # Needed to call the JSON schedules endpoint — see _fetch_schedules.
    company_id: int | None = None

    # Which P&L schema Screener served: "GENERAL" or "FINANCIAL".
    # Banks/NBFCs get entirely different line items — see detect_sector_schema.
    sector_schema: str = "GENERAL"

    # {period_label: value_in_cr} — from the JSON schedules endpoint, not the
    # main balance-sheet table, which doesn't break out cash. Needed for Net Debt.
    cash_equivalents: dict[str, float] = field(default_factory=dict)

    # {line_item: {period_label: percent_of_sales}} — the breakdown behind the
    # P&L "Expenses" row, from the same JSON endpoint.
    #
    # This is what makes gross margin computable at all. Screener's HTML tables
    # publish only total Expenses and OPM%, which is why check 1 used to report
    # NOT_COMPUTABLE and fall back to operating margin as a labelled proxy. The
    # schedules endpoint breaks the same row into Material / Manufacturing /
    # Employee / Other cost percentages, with a dozen years of history.
    expense_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)

    # Derived from Equity Capital and Face Value — the DCF divides by this.
    shares_outstanding: float | None = None

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

    raw_html, served_consolidated = await _fetch_page(symbol, consolidated)
    # `served_consolidated`, not the requested flag — a standalone fallback must
    # not be recorded as a consolidated scrape.
    parsed = _parse_page(symbol, served_consolidated, raw_html)

    # Cash lives behind a second (JSON) request, so it happens here rather than
    # in _parse_page — which stays a pure HTML-in, dataclass-out function.
    if parsed.company_id:
        parsed.cash_equivalents = await _fetch_cash_equivalents(parsed.company_id)
        parsed.expense_breakdown = await _fetch_expense_breakdown(parsed.company_id)

    await _set_cached(cache_key, parsed)
    return parsed


# ============================================================
# HTTP FETCH (rate-limited, cached)
# ============================================================

async def _throttle() -> None:
    """
    Hold the shared request gap. Callers must already hold _request_lock —
    both the page fetch and the JSON schedules fetch go through this so the
    courtesy rate limit covers every request to Screener, not just page loads.
    """
    global _last_request_at
    now = asyncio.get_event_loop().time()
    wait = MIN_REQUEST_GAP_SECONDS - (now - _last_request_at)
    if wait > 0:
        await asyncio.sleep(wait)


async def _get(url: str, symbol: str) -> httpx.Response:
    """
    One throttled request to Screener.

    THE LOCK IS ACQUIRED HERE AND RELEASED BEFORE RETURNING, DELIBERATELY:
    ------------------------------------------------------------------------
    `asyncio.Lock` is NOT reentrant. `_fetch_page` used to hold this lock while
    recursing into itself for the standalone fallback, so the inner call waited
    forever on a lock its own caller held. The coroutine simply stopped — no
    exception, no timeout, nothing in the log after "falling back to
    standalone". It only ever triggered for companies with no consolidated page
    (KPITECH is one), which is why it survived so long: every symbol tested
    before it had one.

    Keeping the critical section down to a single request is what makes that
    class of bug impossible here rather than merely fixed.
    """
    global _last_request_at

    async with _request_lock:
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
                resp = await client.get(url)
            _last_request_at = asyncio.get_event_loop().time()
            return resp
        except httpx.HTTPError as e:
            logger.error(f"Screener fetch failed for {symbol}: {e}")
            raise


async def _fetch_page(symbol: str, consolidated: bool) -> tuple[str, bool]:
    """
    Fetch a company page. Returns (html, was_consolidated).

    The second element matters: asking for the consolidated view and silently
    receiving the standalone one made every downstream figure claim to be
    consolidated when it was not. For a company with subsidiaries those are
    materially different statements, so the caller is told which it actually got.
    """
    path = f"/company/{symbol}/consolidated/" if consolidated else f"/company/{symbol}/"
    resp = await _get(f"{SCREENER_BASE_URL}{path}", symbol)

    if resp.status_code == 404:
        # Consolidated view doesn't exist for this company (common for
        # standalone-only businesses) — fall back to standalone. Safe to
        # recurse: the lock was released before this line.
        if consolidated:
            logger.info(f"No consolidated page for {symbol}, falling back to standalone")
            return await _fetch_page(symbol, consolidated=False)
        raise SymbolNotFound(
            f"Screener has no page for '{symbol}'. Check the ticker — Screener uses "
            f"the NSE symbol (KPIT Technologies is KPITTECH, not KPITECH)."
        )

    resp.raise_for_status()
    return resp.text, consolidated


# ============================================================
# HTML PARSING
# ============================================================

# Unit/currency suffixes Screener appends to values. "Cr." is the important
# one — it is on Market Cap, and stripping only the symbols (as this used to)
# left "1779122 Cr." which float() rejects, so Market Cap was silently dropped
# from every scorecard. Longest-first so "Cr." is tried before "Cr".
_NUMERIC_SUFFIXES = ("Cr.", "Cr", "Lakh", "Crores", "Crore", "x")


def _parse_number(text: str) -> float | None:
    """
    Parse a Screener numeric cell into a float, or None if it isn't a number.

    Handles: thousands separators, ₹ and %, the "Cr." magnitude suffix, and
    negatives written in parentheses. Returns None (rather than guessing) for
    composite cells like "High / Low ₹ 1,612 / 1,250" — those carry two values
    and any single float would be wrong.
    """
    if text is None:
        return None

    t = text.strip().replace(",", "").replace("₹", "").replace("%", "").strip()

    # Composite range cells ("1612 / 1250") are not a single number.
    if "/" in t:
        return None

    for suffix in _NUMERIC_SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
            break

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
    The four small summary tables beside Profit & Loss: Compounded Sales Growth,
    Compounded Profit Growth, Stock Price CAGR, and Return on Equity — each
    broken down by lookback period ("10 Years:", "5 Years:", "3 Years:", "TTM:").

    Returns {table_title: {lookback_label: percent}}.

    THE TITLE IS THE TABLE'S OWN FIRST ROW, not a preceding heading. These are
    `<table class="ranges-table">` whose first row is a single cell holding the
    title. An earlier version read `find_previous(["p", "h3"])`, which picked up
    the PREVIOUS table's title — so all four tables collapsed into one entry
    under the wrong name, and every caller reading "Compounded Sales Growth"
    got nothing. Silent: growth and ROE simply read as unavailable forever.
    """
    growth: dict[str, dict[str, float]] = {}
    section = soup.find(id="profit-loss")
    if not section:
        return growth

    for table in section.find_all("table", class_="ranges-table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        title_cells = rows[0].find_all(["td", "th"])
        if len(title_cells) != 1:
            continue
        title = title_cells[0].get_text(strip=True)

        values: dict[str, float] = {}
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) != 2:
                continue
            label = cells[0].get_text(strip=True)
            val = _parse_number(cells[1].get_text(strip=True))
            if val is not None:
                values[label] = val

        if values:
            growth[title] = values

    if not growth:
        logger.warning("No ranges-table growth tables parsed — Screener markup may have changed")

    return growth


# The row labels Screener uses for lenders. Banks and NBFCs are served an
# entirely different P&L schema: "Revenue"/"Financing Profit"/"Financing Margin %"
# instead of "Sales"/"Operating Profit"/"OPM %". Every general-schema lookup
# returns None against these pages, which is why detecting the schema matters
# more than mapping the keys — see fundamental_agent._sector_supported.
FINANCIAL_SCHEMA_MARKERS = ("Financing Profit", "Financing Margin %")
GENERAL_SCHEMA_MARKERS = ("Operating Profit", "OPM %")


def detect_sector_schema(annual_pnl: dict[str, dict[str, float]]) -> str:
    """
    Return "FINANCIAL" for banks/NBFCs, "GENERAL" otherwise.

    Decided from the P&L row labels rather than from a sector name, because the
    row labels are what actually breaks downstream parsing — and because
    Screener's own sector labels are not stable enough to branch on.
    """
    labels = {label for period in annual_pnl.values() for label in period}
    if any(m in labels for m in FINANCIAL_SCHEMA_MARKERS):
        return "FINANCIAL"
    if any(m in labels for m in GENERAL_SCHEMA_MARKERS):
        return "GENERAL"
    # No markers at all usually means the P&L table failed to parse. Treat as
    # GENERAL so the caller sees missing ratios rather than a bogus sector claim.
    return "GENERAL"


def _extract_company_id(soup: BeautifulSoup) -> int | None:
    """
    Screener's internal numeric company id, needed for the JSON schedules API.

    Read from data-company-id on the #company-info div. Deliberately NOT
    regex-matched out of "/company/(\\d+)/" URLs — the peer-comparison block
    contains those for OTHER companies, so that pattern would silently return
    a competitor's id.
    """
    el = soup.find(attrs={"data-company-id": True})
    if not el:
        logger.warning("No data-company-id on page — schedules (cash) fetch will be skipped")
        return None
    try:
        return int(el["data-company-id"])
    except (ValueError, TypeError):
        return None


async def _fetch_schedules(company_id: int, parent: str, section: str) -> dict:
    """
    Screener's JSON schedules endpoint — the breakdown behind one balance-sheet
    line. Used for "Other Assets" -> "Cash Equivalents", which the main balance
    sheet table rolls up and does not expose.

    Being JSON, this is more stable than the HTML table parsing above. Failures
    are non-fatal: cash is one DCF input, and the caller degrades to reporting
    it as missing rather than failing the whole scrape.
    """
    global _last_request_at
    url = f"{SCREENER_BASE_URL}/api/company/{company_id}/schedules/"

    async with _request_lock:
        await _throttle()
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={**HEADERS, "Accept": "application/json"},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, params={"parent": parent, "section": section})
            _last_request_at = asyncio.get_event_loop().time()

            if resp.status_code != 200:
                logger.warning(f"Schedules fetch returned {resp.status_code} for company_id={company_id}")
                return {}
            return resp.json()
        except Exception as e:
            logger.warning(f"Schedules fetch failed for company_id={company_id}: {e}")
            return {}


async def _fetch_cash_equivalents(company_id: int) -> dict[str, float]:
    """{period_label: cash_in_cr} — the cash side of Net Debt for the DCF."""
    data = await _fetch_schedules(company_id, parent="Other Assets", section="balance-sheet")
    raw_cash = data.get("Cash Equivalents") or {}
    if not isinstance(raw_cash, dict):
        return {}

    out: dict[str, float] = {}
    for period, value in raw_cash.items():
        parsed = _parse_number(str(value))
        if parsed is not None:
            out[period] = parsed
    return out


async def _fetch_expense_breakdown(company_id: int) -> dict[str, dict[str, float]]:
    """
    {line_item: {period: percent_of_sales}} for the P&L "Expenses" row.

    Screener serves these as percentage strings ("49.31%"), and percentages of
    sales are exactly the right shape here: gross margin is 100 minus the cost
    percentage, with no need to reconcile two absolute figures that may be
    scaled or consolidated differently.

    Non-fatal like the cash fetch — a failure degrades check 1 back to its
    operating-margin proxy rather than failing the whole scrape.
    """
    data = await _fetch_schedules(company_id, parent="Expenses", section="profit-loss")
    if not isinstance(data, dict):
        return {}

    out: dict[str, dict[str, float]] = {}
    for line_item, series in data.items():
        if not isinstance(series, dict):
            continue
        parsed_series: dict[str, float] = {}
        for period, value in series.items():
            # Screener embeds a JS callback under this key in expandable rows.
            if period == "isExpandable":
                continue
            parsed = _parse_number(str(value))
            if parsed is not None:
                parsed_series[period] = parsed
        if parsed_series:
            out[line_item] = parsed_series
    return out


def _compute_shares_outstanding(
    balance_sheet: dict[str, dict[str, float]],
    top_ratios: dict[str, float],
) -> float | None:
    """
    Total shares outstanding, derived rather than scraped — Screener doesn't
    publish the count directly, but it does publish both halves of it.

        shares = (Equity Capital in ₹ Cr × 1e7) / Face Value in ₹

    Uses the latest balance-sheet period. The DCF divides equity value by this
    number, so a wrong answer here scales the intrinsic price 1:1 — returns
    None rather than a guess when either input is missing.
    """
    face_value = top_ratios.get("Face Value")
    if not face_value:
        return None

    periods = list(balance_sheet.keys())
    for period in reversed(periods):
        equity_capital = balance_sheet[period].get("Equity Capital")
        if equity_capital:
            return (equity_capital * 1e7) / face_value
    return None


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

    top_ratios = _parse_top_ratios(soup)
    annual_pnl = _parse_data_table(soup, "profit-loss")
    balance_sheet = _parse_data_table(soup, "balance-sheet")

    return FundamentalRaw(
        symbol=symbol,
        fetched_at=datetime.now(timezone.utc),
        consolidated=consolidated,
        top_ratios=top_ratios,
        pros=pros,
        cons=cons,
        quarterly_pnl=_parse_data_table(soup, "quarters"),
        annual_pnl=annual_pnl,
        balance_sheet=balance_sheet,
        cash_flow=_parse_data_table(soup, "cash-flow"),
        ratios_trend=_parse_data_table(soup, "ratios"),
        shareholding=_parse_data_table(soup, "shareholding"),
        growth=_parse_growth_tables(soup),
        company_id=_extract_company_id(soup),
        sector_schema=detect_sector_schema(annual_pnl),
        shares_outstanding=_compute_shares_outstanding(balance_sheet, top_ratios),
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
        # Drop keys this build no longer knows about, and let the dataclass
        # defaults fill in fields added since the entry was written. Without
        # this, adding or removing a field makes every cached entry raise on
        # read, which reads as "cache miss" but is really a silent 3-day stall.
        known = {f.name for f in fields(FundamentalRaw)}
        return FundamentalRaw(**{k: v for k, v in d.items() if k in known})
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
