"""
tests/data/test_fundamentals_scraper.py
=========================================
Unit tests for the Screener.in fundamentals scraper.

Every test here pins ONE way the scraper used to return a wrong number rather
than an error. That is the dangerous failure mode for this file: a parser that
raises gets noticed, a parser that silently drops Market Cap or returns a
competitor's company id ends up inside a valuation.

1. "Cr." suffixes parse (Market Cap was silently dropped)
2. Composite "High / Low" cells return None, not one half of the range
3. Bank/NBFC pages are detected as a different schema
4. company_id comes from data-company-id, never from a peer's URL
5. Shares outstanding derive correctly from Equity Capital + Face Value
6. Cached entries survive a change to the dataclass fields
"""

import sys
import os
import asyncio
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup

from app.data import fundamentals_scraper as fs
from app.data.fundamentals_scraper import FundamentalRaw


# ============================================================
# 1 + 2 — _parse_number
# ============================================================

@pytest.mark.parametrize("text,expected", [
    # The Market Cap bug: stripping only ₹ and commas left "1779122 Cr.",
    # which float() rejects, so Market Cap never reached any scorecard.
    ("₹ 17,79,122 Cr.", 1779122.0),
    ("1,234 Cr", 1234.0),
    ("₹ 1,315", 1315.0),
    ("0.45 %", 0.45),
    ("23.8", 23.8),
    ("(1,234)", -1234.0),      # negatives in parentheses
    ("-", None),
    ("", None),
    ("  ", None),
    (None, None),
])
def test_parse_number(text, expected):
    assert fs._parse_number(text) == expected


def test_composite_range_cell_returns_none():
    """
    "High / Low ₹ 1,612 / 1,250" holds two numbers. Returning either one as
    "the" value would be silently wrong, so it must parse to None.
    """
    assert fs._parse_number("₹ 1,612 / 1,250") is None


# ============================================================
# 3 — sector schema detection
# ============================================================

def test_detects_general_schema():
    pnl = {"Mar 2026": {"Sales": 100.0, "Operating Profit": 20.0, "OPM %": 20.0}}
    assert fs.detect_sector_schema(pnl) == "GENERAL"


def test_detects_financial_schema():
    """
    Banks get "Revenue"/"Financing Profit" instead of "Sales"/"Operating
    Profit". Verified live against HDFCBANK — every general-schema lookup
    returns None on these pages.
    """
    pnl = {"Mar 2026": {"Revenue": 100.0, "Financing Profit": 30.0, "Financing Margin %": 30.0}}
    assert fs.detect_sector_schema(pnl) == "FINANCIAL"


def test_unparseable_pnl_defaults_to_general_not_financial():
    """An empty P&L means the table failed to parse — it is not a sector claim."""
    assert fs.detect_sector_schema({}) == "GENERAL"


# ============================================================
# 4 — company id extraction
# ============================================================

def test_extracts_company_id_from_data_attribute():
    html = '<div data-company-id="2726" data-consolidated="true" id="company-info"></div>'
    assert fs._extract_company_id(BeautifulSoup(html, "html.parser")) == 2726


def test_company_id_ignores_peer_company_urls():
    """
    The peer-comparison block links to /company/1001/ etc. for OTHER companies.
    Regex-matching those URLs would return a competitor's id, and the schedules
    fetch would then attach a competitor's cash balance to this company's DCF.
    """
    html = """
    <div data-company-id="2726" id="company-info"></div>
    <table id="peers">
      <tr><td><a href="/company/1001/">PEER A</a></td></tr>
      <tr><td><a href="/company/1002/">PEER B</a></td></tr>
    </table>
    """
    assert fs._extract_company_id(BeautifulSoup(html, "html.parser")) == 2726


def test_missing_company_id_returns_none_not_raises():
    """Cash is one DCF input — losing it must degrade, not fail the scrape."""
    assert fs._extract_company_id(BeautifulSoup("<div></div>", "html.parser")) is None


# ============================================================
# 5 — shares outstanding
# ============================================================

def test_shares_outstanding_derived_from_equity_capital_and_face_value():
    """
    shares = (Equity Capital in ₹ Cr x 1e7) / Face Value in ₹.
    RELIANCE: 13,532 Cr equity capital at ₹10 face value -> ~13.5bn shares.
    """
    bs = {"Mar 2025": {"Equity Capital": 6766.0}, "Mar 2026": {"Equity Capital": 13532.0}}
    top = {"Face Value": 10.0}
    assert fs._compute_shares_outstanding(bs, top) == pytest.approx(1.3532e10)


def test_shares_outstanding_uses_latest_period_with_data():
    bs = {"Mar 2025": {"Equity Capital": 100.0}, "Mar 2026": {"Reserves": 5.0}}
    assert fs._compute_shares_outstanding(bs, {"Face Value": 10.0}) == pytest.approx(1e8)


def test_shares_outstanding_returns_none_rather_than_guessing():
    """The DCF divides by this, so a wrong value scales intrinsic price 1:1."""
    assert fs._compute_shares_outstanding({"Mar 2026": {"Equity Capital": 100.0}}, {}) is None
    assert fs._compute_shares_outstanding({}, {"Face Value": 10.0}) is None


# ============================================================
# 6 — cache tolerates dataclass schema drift
# ============================================================

@pytest.mark.asyncio
async def test_cache_read_ignores_unknown_fields(monkeypatch):
    """
    A cached entry written by an older build carries fields this one dropped.
    Without filtering, FundamentalRaw(**d) raises, every read looks like a
    cache miss, and the scraper re-fetches for the full 3-day cache window.
    """
    import json

    payload = {
        "symbol": "RELIANCE",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "consolidated": True,
        "a_field_that_no_longer_exists": 123,
    }

    class _FakeRedis:
        async def get(self, key):
            return json.dumps(payload)

    monkeypatch.setattr(fs, "redis_client", _FakeRedis())
    result = await fs._get_cached("any-key")

    assert result is not None
    assert result.symbol == "RELIANCE"
    assert result.sector_schema == "GENERAL"   # new field filled from its default


# ============================================================
# 7 — the growth "ranges-table" parser
# ============================================================

_GROWTH_HTML = """
<section id="profit-loss">
  <p>Consolidated Figures in Rs. Crores / View Standalone</p>
  <table class="data-table"><thead><tr><th></th><th>Mar 2026</th></tr></thead>
    <tbody><tr><td>Sales +</td><td>1,055,780</td></tr></tbody></table>
  <table class="ranges-table">
    <tr><th>Compounded Sales Growth</th></tr>
    <tr><td>10 Years:</td><td>15%</td></tr>
    <tr><td>5 Years:</td><td>18%</td></tr>
  </table>
  <table class="ranges-table">
    <tr><th>Compounded Profit Growth</th></tr>
    <tr><td>10 Years:</td><td>10%</td></tr>
    <tr><td>5 Years:</td><td>12%</td></tr>
  </table>
  <table class="ranges-table">
    <tr><th>Return on Equity</th></tr>
    <tr><td>5 Years:</td><td>9%</td></tr>
    <tr><td>Last Year:</td><td>9%</td></tr>
  </table>
</section>
"""


def test_growth_tables_are_keyed_by_their_own_first_row():
    """
    REGRESSION. The title of a ranges-table is its own first row, not a
    preceding <p>/<h3>. Reading find_previous() picked up the PREVIOUS table's
    title, so all of these collapsed into one entry under the wrong name and
    every caller asking for "Compounded Sales Growth" got nothing back.

    Silent, and long-lived: growth and ROE simply read as "unavailable" for
    every symbol, which looks identical to a company that has no history.
    """
    growth = fs._parse_growth_tables(BeautifulSoup(_GROWTH_HTML, "lxml"))

    assert set(growth) == {
        "Compounded Sales Growth", "Compounded Profit Growth", "Return on Equity",
    }
    assert growth["Compounded Sales Growth"]["5 Years:"] == 18.0
    assert growth["Compounded Profit Growth"]["5 Years:"] == 12.0
    assert growth["Return on Equity"]["Last Year:"] == 9.0


def test_growth_parser_ignores_the_main_data_table():
    """Only ranges-tables are growth summaries — the P&L table is not one."""
    growth = fs._parse_growth_tables(BeautifulSoup(_GROWTH_HTML, "lxml"))
    assert not any("Sales +" in table for table in growth.values())


def test_growth_parser_returns_empty_when_section_missing():
    assert fs._parse_growth_tables(BeautifulSoup("<div></div>", "lxml")) == {}


# ============================================================
# Expense breakdown — the schedules endpoint
#
# This is what makes gross margin computable. Screener's HTML tables carry only
# total "Expenses" and "OPM %", so check 1 used to report NOT_COMPUTABLE and
# offer operating margin as a labelled proxy. The JSON schedules endpoint
# breaks the same row into Material / Manufacturing / Employee / Other, as
# percentages of sales, with a dozen years of history.
# ============================================================

@pytest.mark.asyncio
async def test_expense_breakdown_parses_percentage_series():
    payload = {
        "Material Cost %": {"Mar 2025": "50.44%", "Mar 2026": "49.31%"},
        "Manufacturing Cost %": {"Mar 2025": "8.23%", "Mar 2026": "8.06%"},
    }
    with patch.object(fs, "_fetch_schedules", AsyncMock(return_value=payload)):
        out = await fs._fetch_expense_breakdown(295)

    assert out["Material Cost %"]["Mar 2026"] == 49.31
    assert out["Manufacturing Cost %"]["Mar 2025"] == 8.23


@pytest.mark.asyncio
async def test_expandable_marker_is_not_parsed_as_a_period():
    """
    Screener embeds a JS callback under an `isExpandable` key inside the same
    series dict. Left in, it becomes a bogus period on the series.
    """
    payload = {"Material Cost %": {
        "Mar 2026": "49.31%",
        "isExpandable": 'Company.showSchedule("Material Cost %", "profit-loss", this)',
    }}
    with patch.object(fs, "_fetch_schedules", AsyncMock(return_value=payload)):
        out = await fs._fetch_expense_breakdown(295)

    assert list(out["Material Cost %"].keys()) == ["Mar 2026"]


@pytest.mark.asyncio
async def test_a_failed_schedules_call_yields_an_empty_breakdown():
    """Non-fatal, like the cash fetch: check 1 degrades to its operating-margin
    proxy rather than the whole scrape failing."""
    with patch.object(fs, "_fetch_schedules", AsyncMock(return_value={})):
        assert await fs._fetch_expense_breakdown(295) == {}


# ============================================================
# The standalone fallback
#
# This hung production. `_fetch_page` held the module-level asyncio.Lock while
# recursing into itself for the 404 fallback, and asyncio.Lock is not
# reentrant — the inner call waited forever on a lock its own caller held. No
# exception, no timeout: the request simply never came back, and the log
# stopped dead after "falling back to standalone".
#
# It only fired for companies with no consolidated page, which is why every
# symbol tested before KPITECH missed it.
# ============================================================

class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected raise_for_status on {self.status_code}")


@pytest.mark.asyncio
async def test_standalone_fallback_completes_instead_of_deadlocking():
    """
    The regression test for the hang. If the lock is ever re-acquired inside
    itself again, this does not fail — it never returns — so it runs under a
    timeout.
    """
    calls: list[str] = []

    async def fake_get(url, symbol):
        calls.append(url)
        return _Resp(404) if "consolidated" in url else _Resp(200, "<html>standalone</html>")

    with patch.object(fs, "_get", fake_get):
        html, served_consolidated = await asyncio.wait_for(
            fs._fetch_page("KPITTECH", consolidated=True), timeout=5
        )

    assert html == "<html>standalone</html>"
    assert served_consolidated is False       # and the caller is told
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_consolidated_page_is_reported_as_consolidated():
    async def fake_get(url, symbol):
        return _Resp(200, "<html>consolidated</html>")

    with patch.object(fs, "_get", fake_get):
        html, served_consolidated = await fs._fetch_page("RELIANCE", consolidated=True)

    assert served_consolidated is True


@pytest.mark.asyncio
async def test_an_unknown_symbol_raises_symbol_not_found():
    """
    Both views 404. Distinct from a failed scrape: a mistyped ticker is the
    user's to fix, and the API turns this into a 404 rather than storing a
    NOT_RATED scorecard for a company it never read.
    """
    async def fake_get(url, symbol):
        return _Resp(404)

    with patch.object(fs, "_get", fake_get):
        with pytest.raises(fs.SymbolNotFound) as exc:
            await asyncio.wait_for(fs._fetch_page("KPITECH", consolidated=True), timeout=5)

    assert "KPITECH" in str(exc.value)


@pytest.mark.asyncio
async def test_the_request_lock_is_released_between_requests():
    """
    The property that makes the deadlock impossible rather than merely fixed:
    the critical section is one request wide, so the lock is free by the time
    any retry or fallback runs.
    """
    async def fake_get(url, symbol):
        assert not fs._request_lock.locked(), "the lock must not be held across calls"
        return _Resp(404) if "consolidated" in url else _Resp(200, "ok")

    with patch.object(fs, "_get", fake_get):
        await asyncio.wait_for(fs._fetch_page("KPITTECH", consolidated=True), timeout=5)
