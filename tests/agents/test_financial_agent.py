"""
tests/agents/test_financial_agent.py
======================================
Tests for Stage 2 — the 10-point financial checklist.

Priorities here, in order of how expensive the bug would be:

1. NOT_COMPUTABLE never masquerades as a pass. `completeness` gates whether a
   verdict is issued at all, so a check that silently scores itself when it
   has no data corrupts the gate, not just one line of output.
2. The trend tests are material. A percentage change off a near-zero base is
   not a finding — check 4 fired on every debt-free company before this.
3. The calculation guards fire. They are the difference between a CAGR and a
   meaningless CAGR.
4. TTM is excluded from year-over-year series (it is a trailing window, not a
   reporting year — including it double-counts the latest period).
"""

import sys
import os
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.data.fundamentals_scraper import FundamentalRaw
from app.agents import financial_agent as fin
from app.agents.financial_agent import run_checklist, CheckStatus


YEARS = ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"]


def _raw(**overrides) -> FundamentalRaw:
    """A healthy general-schema company with 5 years plus a TTM column."""
    pnl = {
        y: {"Sales": s, "Operating Profit": s * 0.2, "OPM %": 20.0,
            "Other Income": s * 0.01, "Interest": 10.0, "Depreciation": s * 0.05,
            "Profit before tax": s * 0.15, "Net Profit": np, "EPS in Rs": eps}
        for y, s, np, eps in zip(YEARS, [1000, 1100, 1200, 1300, 1400],
                                 [100, 110, 120, 130, 140], [10.0, 11.0, 12.0, 13.0, 14.0])
    }
    pnl["TTM"] = {"Sales": 1500.0, "Operating Profit": 300.0, "Net Profit": 150.0, "EPS in Rs": 15.0}

    base = dict(
        symbol="TESTCO",
        fetched_at=datetime.now(timezone.utc),
        consolidated=True,
        top_ratios={"ROE %": 30.0, "Stock P/E": 20.0, "Face Value": 10.0},
        annual_pnl=pnl,
        balance_sheet={y: {"Equity Capital": 100.0, "Reserves": 900.0,
                           "Borrowings": 300.0, "Total Assets": 2000.0} for y in YEARS},
        cash_flow={y: {"Cash from Operating Activity": c, "Free Cash Flow": c * 0.7}
                   for y, c in zip(YEARS, [90, 100, 110, 120, 130])},
        ratios_trend={y: {"Debtor Days": 30.0, "Inventory Days": 60.0} for y in YEARS},
        growth={
            "Compounded Sales Growth":  {"10 Years:": 12.0, "5 Years:": 10.0, "3 Years:": 9.0},
            "Compounded Profit Growth": {"10 Years:": 12.0, "5 Years:": 10.0, "3 Years:": 9.0},
            "Return on Equity":         {"5 Years:": 30.0, "3 Years:": 30.0, "Last Year:": 30.0},
        },
        sector_schema="GENERAL",
        shares_outstanding=1e8,
    )
    base.update(overrides)
    return FundamentalRaw(**base)


def _check(result, n):
    return next(c for c in result.checks if c.n == n)


# ============================================================
# Structure: ten checks, completeness, and the TTM exclusion
# ============================================================

def test_always_returns_exactly_ten_checks():
    assert len(run_checklist(_raw()).checks) == 10


def test_completeness_counts_only_computable_checks():
    """
    Checks 1, 9 and 10 need the annual report, so a Screener-only run tops out
    at 70%. If this ever reads 100%, something is scoring itself without data.
    """
    r = run_checklist(_raw())
    assert r.completeness == pytest.approx(0.7)
    for n in (1, 9, 10):
        assert _check(r, n).status is CheckStatus.NOT_COMPUTABLE


def test_ttm_is_excluded_from_annual_series():
    """
    TTM is a trailing twelve-month window, not a reporting year. Including it
    in a year-over-year series double-counts the most recent period and
    inflates every trend.
    """
    assert "TTM" not in fin._annual_periods(_raw())
    assert len(fin._annual_periods(_raw())) == 5


def test_gross_margin_falls_back_to_the_opm_proxy_without_the_schedules():
    """
    Operating margin is strictly lower than gross margin. Testing a >20%
    gross-margin bar against it would fail companies that actually pass, so
    when the material-cost breakdown is unavailable the proxy is reported for
    context and the check does not score.
    """
    c = _check(run_checklist(_raw()), 1)      # no expense_breakdown
    assert c.status is CheckStatus.NOT_COMPUTABLE
    assert c.value == 20.0                    # OPM, for the reader
    assert "annual report" in c.detail


def _with_material_cost(material, manufacturing=None):
    breakdown = {"Material Cost %": {y: material for y in YEARS}}
    if manufacturing is not None:
        breakdown["Manufacturing Cost %"] = {y: manufacturing for y in YEARS}
    return _raw(expense_breakdown=breakdown)


def test_gross_margin_is_computed_from_the_material_cost_schedule():
    """
    Screener's HTML has no COGS row, but the JSON schedules endpoint breaks the
    Expenses line into Material / Manufacturing / Employee / Other percentages
    of sales. Gross margin is 100 minus the material cost percentage.
    """
    c = _check(run_checklist(_with_material_cost(49.3)), 1)
    assert c.status is CheckStatus.PASS
    assert c.value == 50.7
    assert c.source == "screener"


def test_gross_margin_reports_both_definitions():
    """
    The method's own general rule: never silently pick one assumption when more
    than one is reasonable. Materials-only is the primary figure and what the
    bar is tested against; materials-plus-conversion is closer to a strict
    accounting COGS and is stated beside it.
    """
    c = _check(run_checklist(_with_material_cost(49.3, manufacturing=8.1)), 1)
    assert "50.7%" in c.detail          # primary
    assert "42.6%" in c.detail          # stricter
    assert c.value == 50.7              # the bar is tested against the primary


def test_gross_margin_fails_below_the_bar():
    c = _check(run_checklist(_with_material_cost(85.0)), 1)
    assert c.status is CheckStatus.FAIL
    assert c.value == 15.0


# ============================================================
# Check 2 — growth alignment
# ============================================================

def test_growth_alignment_passes_when_profit_tracks_sales():
    assert _check(run_checklist(_raw()), 2).status is CheckStatus.PASS


def test_growth_alignment_flags_margin_compression():
    raw = _raw()
    raw.growth["Compounded Profit Growth"] = {"10 Years:": 2.0, "5 Years:": 1.0, "3 Years:": 0.0}
    c = _check(run_checklist(raw), 2)
    assert c.status is CheckStatus.FLAG
    assert "margins compressing" in c.detail


def test_growth_alignment_flags_profit_far_ahead_of_sales():
    """Profit outrunning sales is not automatically good — it can be one-offs."""
    raw = _raw()
    raw.growth["Compounded Profit Growth"] = {"10 Years:": 40.0, "5 Years:": 38.0, "3 Years:": 35.0}
    assert _check(run_checklist(raw), 2).status is CheckStatus.FLAG


def test_growth_not_computable_without_the_tables():
    """
    Guards the scraper bug that made these tables read empty for every symbol:
    the checklist must report that plainly, not score around it.
    """
    raw = _raw(growth={})
    assert _check(run_checklist(raw), 2).status is CheckStatus.NOT_COMPUTABLE


# ============================================================
# Check 3 — EPS consistency and dilution
# ============================================================

def test_eps_consistency_passes_when_eps_tracks_profit():
    assert _check(run_checklist(_raw()), 3).status is CheckStatus.PASS


def test_eps_consistency_flags_dilution():
    """EPS lagging profit means the share count grew — quantify it."""
    raw = _raw()
    for y, eps in zip(YEARS, [10.0, 10.1, 10.2, 10.3, 10.4]):
        raw.annual_pnl[y]["EPS in Rs"] = eps
    c = _check(run_checklist(raw), 3)
    assert c.status is CheckStatus.FLAG
    assert "diluted" in c.detail


def test_large_share_count_jump_is_called_out_as_probable_bonus_issue():
    """
    A doubling of equity capital is mechanical (bonus or split), not the kind
    of dilution that hurts holders. The reader must not mistake one for the other.
    """
    raw = _raw()
    raw.balance_sheet["Mar 2025"]["Equity Capital"] = 200.0
    raw.balance_sheet["Mar 2026"]["Equity Capital"] = 200.0
    assert "bonus issue or split" in _check(run_checklist(raw), 3).detail


# ============================================================
# Check 4 — debt, and the near-zero-base regression
# ============================================================

def test_debt_passes_when_low_and_stable():
    assert _check(run_checklist(_raw()), 4).status is CheckStatus.PASS


def test_debt_fails_when_highly_leveraged():
    raw = _raw()
    for y in YEARS:
        raw.balance_sheet[y]["Borrowings"] = 3000.0     # D/E 3.0
    assert _check(run_checklist(raw), 4).status is CheckStatus.FAIL


def test_debt_trend_is_not_scored_off_an_immaterial_base():
    """
    REGRESSION: a debt-free company going from 2 Cr to 19 Cr of borrowings
    reads as "+863%" while remaining debt-free. Scoring that trend flagged
    every genuinely debt-free company — exactly the ones this checklist is
    meant to reward. Observed live on ITC (D/E 0.03).
    """
    raw = _raw()
    for y, b in zip(YEARS, [2.0, 4.0, 8.0, 14.0, 19.0]):
        raw.balance_sheet[y]["Borrowings"] = b          # D/E 0.019
    c = _check(run_checklist(raw), 4)
    assert c.status is CheckStatus.PASS
    assert "immaterial base" in c.detail


def test_debt_trend_is_scored_once_borrowings_are_material():
    """The materiality floor must not switch the test off entirely."""
    raw = _raw()
    for y, b in zip(YEARS, [200.0, 300.0, 400.0, 450.0, 480.0]):
        raw.balance_sheet[y]["Borrowings"] = b          # D/E 0.48, +140%
    c = _check(run_checklist(raw), 4)
    assert c.status is CheckStatus.FLAG
    assert "rising fast" in c.detail


# ============================================================
# Checks 5-8
# ============================================================

def test_inventory_flags_stock_building_faster_than_margins():
    raw = _raw()
    for y, days in zip(YEARS, [60.0, 70.0, 85.0, 100.0, 120.0]):
        raw.ratios_trend[y]["Inventory Days"] = days
    assert _check(run_checklist(raw), 5).status is CheckStatus.FLAG


def test_receivables_flag_when_growing_faster_than_sales():
    raw = _raw()
    for y, days in zip(YEARS, [30.0, 45.0, 60.0, 75.0, 95.0]):
        raw.ratios_trend[y]["Debtor Days"] = days
    assert _check(run_checklist(raw), 6).status is CheckStatus.FLAG


def test_cfo_fails_on_any_negative_year():
    """The method's bar is positive EVERY year, not positive on average."""
    raw = _raw()
    raw.cash_flow["Mar 2024"]["Cash from Operating Activity"] = -50.0
    c = _check(run_checklist(raw), 7)
    assert c.status is CheckStatus.FAIL
    assert "Mar 2024" in c.detail


def test_cfo_flags_positive_but_declining():
    raw = _raw()
    for y, v in zip(YEARS, [200.0, 180.0, 150.0, 120.0, 100.0]):
        raw.cash_flow[y]["Cash from Operating Activity"] = v
    assert _check(run_checklist(raw), 7).status is CheckStatus.FLAG


def test_roe_uses_the_25pct_checklist_bar():
    """Higher than the >18% general bar used elsewhere — on purpose."""
    assert _check(run_checklist(_raw()), 8).status is CheckStatus.PASS

    raw = _raw(top_ratios={"ROE %": 12.0})
    raw.growth["Return on Equity"] = {"5 Years:": 12.0, "3 Years:": 12.0}
    assert _check(run_checklist(raw), 8).status is CheckStatus.FAIL


def test_roe_flags_latest_well_below_multi_year_average():
    """A capital raise or a one-off loss — the method wants that explained."""
    raw = _raw(top_ratios={"ROE %": 18.0})
    raw.growth["Return on Equity"] = {"5 Years:": 32.0, "3 Years:": 30.0}
    c = _check(run_checklist(raw), 8)
    assert c.status is CheckStatus.FLAG
    assert "below the 5-year average" in c.detail


# ============================================================
# Calculation guards
# ============================================================

def test_guard_detects_outsized_other_income():
    """Observed live on ITC: Other Income at 42% of PBT in Mar 2025."""
    raw = _raw()
    raw.annual_pnl["Mar 2025"]["Other Income"] = 100.0
    raw.annual_pnl["Mar 2025"]["Profit before tax"] = 200.0     # 50%
    warnings = run_checklist(raw).warnings
    assert any("Other Income" in w and "Mar 2025" in w for w in warnings)


def test_guard_detects_negative_base_year():
    raw = _raw()
    raw.annual_pnl["Mar 2022"]["Net Profit"] = -50.0
    assert any("negative base" in w for w in run_checklist(raw).warnings)


def test_guard_detects_ebitda_growing_while_fcf_does_not():
    """
    The trap for a company mid-expansion: great EBITDA, little or no free cash.
    """
    raw = _raw()
    for y, op, fcf in zip(YEARS, [100, 160, 220, 300, 400], [70, 60, 50, 40, 30]):
        raw.annual_pnl[y]["Operating Profit"] = float(op)
        raw.cash_flow[y]["Free Cash Flow"] = float(fcf)
    assert any("do not read EBITDA as cash generation" in w for w in run_checklist(raw).warnings)


def test_guard_warns_when_free_cash_flow_row_is_absent():
    raw = _raw()
    for y in YEARS:
        del raw.cash_flow[y]["Free Cash Flow"]
    assert any("itemised capex" in w for w in run_checklist(raw).warnings)


# ============================================================
# Sector refusal + narration fallback
# ============================================================

def test_bank_refuses_the_whole_checklist():
    """
    For a lender, borrowing is the raw material — leverage and coverage do not
    carry their usual meaning, so no check is scored rather than 3 of 10.
    """
    r = run_checklist(_raw(sector_schema="FINANCIAL"))
    assert r.completeness == 0.0
    assert all(c.status is CheckStatus.NOT_COMPUTABLE for c in r.checks)
    assert len(r.checks) == 10


@pytest.mark.asyncio
async def test_narration_falls_back_to_template_without_a_model():
    """Stage 2 must never be blocked on an LLM call."""
    from unittest.mock import patch
    from app.agents.financial_agent import narrate_checklist

    r = run_checklist(_raw())
    with patch.object(fin.settings, "LOCAL_MODEL_BASE_URL", ""):
        assert await narrate_checklist("TESTCO", r) == r.summary
    assert "computable checks pass" in r.summary
