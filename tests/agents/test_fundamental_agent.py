"""
tests/agents/test_fundamental_agent.py
========================================
Unit tests for the fundamental analysis agent's scoring.

Every test here pins ONE defect that made the agent emit a confident but wrong
number. None of these ever raised — that is why they need tests: a scorecard
that quietly omits Debt/Equity, or overstates interest coverage by the whole
depreciation charge, looks exactly like a correct one.

1. Balance-sheet lookups skip "TTM" (it exists only in the P&L)
   -> before this, DuPont NEVER computed and Debt/Equity was never produced
2. DuPont uses average balance-sheet figures, not closing
3. Interest coverage is EBIT/Interest, not EBITDA/Interest
4. A debt-free company scores GREEN on coverage, not "unavailable"
5. P/E is judged against the 25-30x band, not 60x
6. ROE is judged against 18%, not 15%
7. Banks/NBFCs are refused (NOT_RATED), not scored on meaningless ratios
8. No position size is emitted — Investing mode is advisory
"""

import sys
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.data.fundamentals_scraper import FundamentalRaw
from app.agents import fundamental_agent as fa
from app.agents.fundamental_agent import Verdict, FlagColor


def _raw(**overrides) -> FundamentalRaw:
    """
    A general-schema company with two full years plus a TTM column — the shape
    Screener actually serves. TTM is present in the P&L and absent from the
    balance sheet, which is the whole point of the fixture.
    """
    base = dict(
        symbol="TESTCO",
        fetched_at=datetime.now(timezone.utc),
        consolidated=True,
        top_ratios={"Stock P/E": 20.0, "ROE %": 20.0, "ROCE %": 20.0, "Face Value": 10.0},
        annual_pnl={
            "Mar 2025": {"Sales": 900000.0, "Operating Profit": 150000.0,
                         "Depreciation": 50000.0, "Interest": 25000.0, "Net Profit": 70000.0},
            "Mar 2026": {"Sales": 1000000.0, "Operating Profit": 160000.0,
                         "Depreciation": 55000.0, "Interest": 26000.0, "Net Profit": 80000.0},
            "TTM": {"Sales": 1055780.0, "Operating Profit": 165000.0, "Net Profit": 95754.0},
        },
        balance_sheet={
            "Mar 2025": {"Equity Capital": 6766.0, "Reserves": 700000.0,
                         "Borrowings": 300000.0, "Total Assets": 1600000.0},
            "Mar 2026": {"Equity Capital": 13532.0, "Reserves": 800000.0,
                         "Borrowings": 320000.0, "Total Assets": 1800000.0},
        },
        sector_schema="GENERAL",
    )
    base.update(overrides)
    return FundamentalRaw(**base)


def _flag(flags, label):
    return next((f for f in flags if f.label == label), None)


# ============================================================
# 1 — the TTM period bug
# ============================================================

def test_latest_period_is_ttm_but_common_period_is_not():
    """Pins the exact mismatch that broke every balance-sheet lookup."""
    raw = _raw()
    assert fa._latest_period(raw) == "TTM"
    assert fa._latest_common_period(raw) == "Mar 2026"


def test_dupont_computes_despite_ttm_column():
    """
    Before the fix, DuPont was keyed on "TTM", which is absent from the balance
    sheet, so the whole block was skipped and the scorecard silently shipped
    with no DuPont breakdown at all — for every symbol.
    """
    _, dupont = fa._score_profitability(_raw())
    assert dupont, "DuPont did not compute — the TTM period bug is back"
    assert dupont["period"] == "Mar 2026"
    assert dupont["net_margin"] == pytest.approx(8.0)


def test_debt_equity_is_produced_despite_ttm_column():
    """Same root cause, second victim: leverage keyed on TTM produced nothing."""
    de = _flag(fa._score_leverage(_raw()), "Debt/Equity")
    assert de is not None, "Debt/Equity missing — the TTM period bug is back"
    # 320000 / (13532 + 800000)
    assert de.value == pytest.approx(0.393, abs=0.01)
    assert de.color == FlagColor.GREEN


# ============================================================
# 2 — DuPont averaging
# ============================================================

def test_dupont_uses_average_balance_sheet_figures():
    """
    DuPont mixes a flow (P&L) with a stock (balance sheet). Closing-balance
    figures understate turnover and leverage for a growing company.
      averaged : assets (1.8m + 1.6m)/2 = 1.7m -> turnover 0.588
      closing  : assets 1.8m             -> turnover 0.556
    """
    _, dupont = fa._score_profitability(_raw())
    assert dupont["averaged"] is True
    assert dupont["asset_turnover"] == pytest.approx(1000000 / 1700000, abs=0.001)
    assert dupont["asset_turnover"] != pytest.approx(1000000 / 1800000, abs=0.001)

    avg_equity = ((13532 + 800000) + (6766 + 700000)) / 2
    assert dupont["equity_multiplier"] == pytest.approx(1700000 / avg_equity, abs=0.01)


def test_dupont_falls_back_to_closing_when_no_prior_year():
    raw = _raw(balance_sheet={"Mar 2026": {"Equity Capital": 13532.0, "Reserves": 800000.0,
                                           "Borrowings": 320000.0, "Total Assets": 1800000.0}})
    _, dupont = fa._score_profitability(raw)
    assert dupont["averaged"] is False
    assert dupont["asset_turnover"] == pytest.approx(1000000 / 1800000, abs=0.001)


# ============================================================
# 3 + 4 — interest coverage
# ============================================================

def test_interest_coverage_uses_ebit_not_ebitda():
    """
    Screener's "Operating Profit" sits above the Depreciation line — it is
    EBITDA. Coverage is EBIT/Interest.
      correct   : (160000 - 55000) / 26000 = 4.04  -> YELLOW
      old (bad) :  160000          / 26000 = 6.15  -> GREEN
    The bug turned a marginal borrower into a comfortable one.
    """
    ic = _flag(fa._score_leverage(_raw()), "Interest Coverage")
    assert ic is not None
    assert ic.value == pytest.approx(4.038, abs=0.01)
    assert ic.color == FlagColor.YELLOW


def test_interest_coverage_missing_depreciation_is_not_guessed():
    """No D&A figure means EBIT is unknown — omit rather than fall back to EBITDA."""
    raw = _raw()
    del raw.annual_pnl["Mar 2026"]["Depreciation"]
    assert _flag(fa._score_leverage(raw), "Interest Coverage") is None


def test_debt_free_company_scores_green_not_missing():
    """
    Zero interest is the best possible answer. The old truthiness check treated
    it as absent, so debt-free companies lost the flag entirely and their data
    completeness — and therefore confidence — was scored down for it.
    """
    raw = _raw()
    raw.annual_pnl["Mar 2026"]["Interest"] = 0.0
    ic = _flag(fa._score_leverage(raw), "Interest Coverage")
    assert ic is not None
    assert ic.color == FlagColor.GREEN
    assert "debt-free" in ic.note


# ============================================================
# 5 — P/E band
# ============================================================

@pytest.mark.parametrize("pe,expected", [
    (15.0, FlagColor.GREEN),
    (25.0, FlagColor.GREEN),
    (28.0, FlagColor.YELLOW),
    (30.0, FlagColor.YELLOW),
    # The old code flagged nothing below 60x, so a 45x stock read as GREEN —
    # well past the multiple the source method says to avoid.
    (45.0, FlagColor.RED),
    (-5.0, FlagColor.RED),
])
def test_pe_judged_against_25_to_30x_band(pe, expected):
    raw = _raw(top_ratios={"Stock P/E": pe})
    assert _flag(fa._score_valuation(raw), "P/E").color == expected


# ============================================================
# 6 — ROE band
# ============================================================

@pytest.mark.parametrize("roe,expected", [
    (25.0, FlagColor.GREEN),
    (18.0, FlagColor.GREEN),
    # 16% passed as GREEN under the old 15% bar; the method wants 18%+.
    (16.0, FlagColor.YELLOW),
    (8.0,  FlagColor.RED),
])
def test_roe_judged_against_18pct(roe, expected):
    raw = _raw(top_ratios={"ROE %": roe})
    flags, _ = fa._score_profitability(raw)
    assert _flag(flags, "ROE %").color == expected


# ============================================================
# 7 — banks and NBFCs are refused, not scored
# ============================================================

@pytest.mark.asyncio
async def test_financial_sector_returns_not_rated():
    """
    Screener serves lenders a different P&L schema, so every ratio reads None.
    The deeper problem is that Debt/Equity and interest coverage are meaningless
    for a bank — borrowing IS the raw material. A low score would be an artefact
    of the model, not a finding about the company.
    """
    raw = _raw(
        sector_schema="FINANCIAL",
        annual_pnl={"Mar 2026": {"Revenue": 100.0, "Financing Profit": 30.0,
                                 "Financing Margin %": 30.0, "Net Profit": 25.0}},
    )
    with patch.object(fa, "get_fundamentals", AsyncMock(return_value=raw)):
        card = await fa.generate_scorecard("HDFCBANK")

    assert card.verdict == Verdict.NOT_RATED
    assert card.not_rated_reason == "SECTOR_UNSUPPORTED"
    assert card.profitability == [] and card.leverage == []
    assert card.rationale, "a refusal must say why"


@pytest.mark.asyncio
async def test_general_sector_is_still_scored():
    """Guard against the sector gate swallowing everything."""
    with patch.object(fa, "get_fundamentals", AsyncMock(return_value=_raw())), \
         patch.object(fa, "_generate_rationale", AsyncMock(return_value="ok")):
        card = await fa.generate_scorecard("TESTCO")

    assert card.verdict != Verdict.NOT_RATED
    assert card.dupont_net_margin == pytest.approx(8.0)


# ============================================================
# 8 — advisory only
# ============================================================

@pytest.mark.asyncio
async def test_scorecard_emits_no_position_size():
    """
    Investing mode places no orders and does not size positions. A suggested
    allocation the system cannot act on still reads as advice, so the field is
    gone rather than ignored.
    """
    with patch.object(fa, "get_fundamentals", AsyncMock(return_value=_raw())), \
         patch.object(fa, "_generate_rationale", AsyncMock(return_value="ok")):
        card = await fa.generate_scorecard("TESTCO")

    assert not hasattr(card, "suggested_allocation_pct")
