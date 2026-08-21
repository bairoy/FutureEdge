"""
tests/agents/test_valuation_agent.py
======================================
Tests for Stage 3 — intrinsic value by DCF.

A DCF fails quietly and confidently, which is what makes it worth testing hard.
The properties pinned here:

1. The arithmetic is self-consistent — a reverse DCF run at the intrinsic price
   must return the base free cash flow it started from. That single invariant
   validates the projection, terminal value, discounting and net-debt handling
   together, because it inverts all of them.
2. Net cash ADDS to value. Negative net debt is the sign error most easily made.
3. It refuses rather than guesses: negative base FCF, terminal growth at or
   above the discount rate, banks, missing share count.
4. The cautions fire — outlier base year, terminal value dominating, the two
   terminal methods disagreeing.
5. The band is exactly ×1.10 / ×0.90, and margin of safety ×0.70 off the lower
   band, not off the intrinsic value.
"""

import sys
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.data.fundamentals_scraper import FundamentalRaw
from app.data.market_risk import BetaEstimate, discount_rate_capm
from app.agents import valuation_agent as val
from app.agents.valuation_agent import run_dcf, _project_and_discount

YEARS = ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"]


def _raw(**overrides) -> FundamentalRaw:
    pnl = {y: {"Sales": 10000.0, "Net Profit": 1000.0} for y in YEARS}
    pnl["TTM"] = {"Sales": 11000.0, "Net Profit": 1100.0}
    base = dict(
        symbol="TESTCO",
        fetched_at=datetime.now(timezone.utc),
        consolidated=True,
        top_ratios={"Current Price": 500.0, "Face Value": 10.0},
        annual_pnl=pnl,
        balance_sheet={y: {"Borrowings": 2000.0, "Investments": 500.0,
                           "Equity Capital": 100.0, "Reserves": 9000.0} for y in YEARS},
        cash_flow={y: {"Free Cash Flow": f} for y, f in zip(YEARS, [800, 900, 1000, 1000, 1000])},
        cash_equivalents={y: 1500.0 for y in YEARS},
        sector_schema="GENERAL",
        shares_outstanding=1e8,
    )
    base.update(overrides)
    return FundamentalRaw(**base)


def _beta(value=1.0, disagreement=0.05):
    return AsyncMock(return_value=BetaEstimate(
        symbol="TESTCO", beta=value, estimates={"2y_weekly": value}, disagreement=disagreement, note="test",
    ))


async def _dcf(raw=None, beta=1.0):
    with patch.object(val, "get_beta", _beta(beta)):
        return await run_dcf(raw or _raw())


# ============================================================
# 1 — the self-consistency invariant
# ============================================================

@pytest.mark.asyncio
async def test_reverse_dcf_at_intrinsic_price_returns_the_base_fcf():
    """
    THE key test. If the market price happens to equal the intrinsic value,
    the free cash flow the price implies must be exactly the base FCF the
    model started from.

    This inverts the projection, the terminal value, the discounting and the
    net-debt adjustment all at once — so it catches a sign error or an
    off-by-one year anywhere in the chain, which eyeballing a plausible-looking
    intrinsic value never would.
    """
    d = await _dcf()
    raw = _raw()
    raw.top_ratios["Current Price"] = d.intrinsic     # price == intrinsic

    d2 = await _dcf(raw)
    assert d2.reverse_dcf_implied_fcf == pytest.approx(d2.base_fcf, rel=1e-6)


@pytest.mark.asyncio
async def test_reverse_dcf_scales_with_price():
    """Double the price, double the free cash flow it implies (net of debt)."""
    raw = _raw()
    raw.top_ratios["Current Price"] = 1000.0
    high = await _dcf(raw)

    raw2 = _raw()
    raw2.top_ratios["Current Price"] = 500.0
    low = await _dcf(raw2)

    assert high.reverse_dcf_implied_fcf > low.reverse_dcf_implied_fcf


# ============================================================
# 2 — net debt sign
# ============================================================

@pytest.mark.asyncio
async def test_net_cash_increases_intrinsic_value():
    """
    Negative net debt is net cash and must ADD to equity value. Getting this
    backwards understates every cash-rich company — and ITC, a real watchlist
    name, carries about ₹38,000 Cr of net cash.
    """
    indebted = await _dcf(_raw())

    rich = _raw()
    for y in YEARS:
        rich.balance_sheet[y]["Borrowings"] = 0.0
    rich.cash_equivalents = {y: 5000.0 for y in YEARS}
    cash_rich = await _dcf(rich)

    assert cash_rich.net_debt < 0
    assert cash_rich.intrinsic > indebted.intrinsic


@pytest.mark.asyncio
async def test_net_debt_subtracts_cash_and_current_investments():
    d = await _dcf()
    # Borrowings 2000 - (cash 1500 + investments 500) = 0
    assert d.net_debt == pytest.approx(0.0)


# ============================================================
# 3 — refusals
# ============================================================

@pytest.mark.asyncio
async def test_negative_base_fcf_is_refused_not_valued():
    raw = _raw(cash_flow={y: {"Free Cash Flow": -500.0} for y in YEARS})
    d = await _dcf(raw)
    assert not d.complete
    assert "negative" in d.not_computable_reason


@pytest.mark.asyncio
async def test_terminal_growth_at_or_above_discount_rate_is_refused():
    """The Gordon formula diverges — it must refuse, not return a huge number."""
    raw = _raw()
    with patch.object(val.settings, "DCF_TERMINAL_GROWTH_PCT", 20.0):
        d = await _dcf(raw)
    assert not d.complete
    assert "diverges" in d.not_computable_reason


@pytest.mark.asyncio
async def test_bank_is_refused():
    d = await _dcf(_raw(sector_schema="FINANCIAL"))
    assert not d.complete
    assert "lender" in d.not_computable_reason


@pytest.mark.asyncio
async def test_too_few_years_of_fcf_is_refused():
    raw = _raw(cash_flow={"Mar 2025": {"Free Cash Flow": 1000.0}})
    d = await _dcf(raw)
    assert not d.complete


@pytest.mark.asyncio
async def test_missing_share_count_is_refused():
    raw = _raw(shares_outstanding=None)
    d = await _dcf(raw)
    assert not d.complete
    assert "Share count" in d.not_computable_reason


# ============================================================
# 4 — the cautions
# ============================================================

@pytest.mark.asyncio
async def test_outlier_base_year_produces_an_alternative_average():
    """
    An unusually heavy capex year drags the 3-year average. The method wants
    both figures shown, not one silently chosen — observed live on RELIANCE,
    where the latest FCF was 70,023 Cr against a 41,079 Cr median.
    """
    raw = _raw(cash_flow={y: {"Free Cash Flow": f}
                          for y, f in zip(YEARS, [800, 900, 1000, 1000, 3000])})
    d = await _dcf(raw)
    assert d.base_fcf_ex_outlier is not None
    assert d.base_fcf_ex_outlier < d.base_fcf
    assert any("3-year median" in w for w in d.warnings)


@pytest.mark.asyncio
async def test_terminal_value_dominating_is_flagged():
    """
    Terminal value is typically over half the answer. Past 70% the result is
    an assumption about the far future rather than a reading of the business,
    and the reader has to be told.
    """
    d = await _dcf()
    assert 0.0 < d.terminal_share_of_value < 1.0
    if d.terminal_share_of_value > val.TERMINAL_SHARE_WARNING:
        assert any("far future" in w for w in d.warnings)


@pytest.mark.asyncio
async def test_both_terminal_methods_are_reported():
    """Gordon growth and an exit multiple can diverge a lot — show both."""
    d = await _dcf()
    assert d.terminal_value_gordon is not None
    assert d.terminal_value_exit_multiple is not None


@pytest.mark.asyncio
async def test_unstable_beta_is_surfaced_as_a_warning():
    with patch.object(val, "get_beta", _beta(1.4, disagreement=0.6)):
        d = await run_dcf(_raw())
    assert any("test" in w for w in d.warnings)


# ============================================================
# 5 — band, margin of safety, and price classification
# ============================================================

@pytest.mark.asyncio
async def test_band_and_margin_of_safety_arithmetic():
    """
    MoS is 70% of the LOWER band, not of the intrinsic value — a second
    discount on top of the modelling band, not instead of it.
    """
    d = await _dcf()
    assert d.upper_band == pytest.approx(d.intrinsic * 1.10)
    assert d.lower_band == pytest.approx(d.intrinsic * 0.90)
    assert d.mos_buy_price == pytest.approx(d.lower_band * 0.70)
    assert d.mos_buy_price < d.lower_band < d.intrinsic < d.upper_band


@pytest.mark.asyncio
@pytest.mark.parametrize("multiplier,expected", [
    (0.5, "UNDERVALUED"),
    (1.0, "FAIRLY_VALUED"),
    (2.0, "OVERVALUED"),
])
async def test_price_vs_band_classification(multiplier, expected):
    reference = await _dcf()
    raw = _raw()
    raw.top_ratios["Current Price"] = reference.intrinsic * multiplier
    assert (await _dcf(raw)).price_vs_band == expected


# ============================================================
# Projection mechanics + sensitivity
# ============================================================

def test_projection_runs_ten_years_in_two_growth_stages():
    core = _project_and_discount(1000.0, 15.0, 10.0, 3.5, 11.0)
    projections = core["projections"]
    assert len(projections) == 10
    assert [p.growth_pct for p in projections[:5]] == [15.0] * 5
    assert [p.growth_pct for p in projections[5:]] == [10.0] * 5
    # Year 1 FCF = 1000 x 1.15
    assert projections[0].fcf == pytest.approx(1150.0)


def test_present_value_falls_with_distance():
    core = _project_and_discount(1000.0, 15.0, 10.0, 3.5, 11.0)
    factors = [p.discount_factor for p in core["projections"]]
    assert factors == sorted(factors, reverse=True)


def test_higher_discount_rate_lowers_value():
    low = _project_and_discount(1000.0, 15.0, 10.0, 3.5, 9.0)["sum_pv"]
    high = _project_and_discount(1000.0, 15.0, 10.0, 3.5, 13.0)["sum_pv"]
    assert high < low


@pytest.mark.asyncio
async def test_sensitivity_grid_omits_impossible_combinations():
    """Terminal growth at or above the discount rate has no valid value."""
    d = await _dcf()
    grid = d.sensitivity["intrinsic_by_discount_and_terminal_growth"]
    assert grid
    for dr_key, row in grid.items():
        dr = float(dr_key.split("_")[1])
        for tg_key in row:
            assert float(tg_key.split("_")[1]) < dr


def test_capm_falls_back_to_market_beta_when_unknown():
    from app.core.config import settings
    assert discount_rate_capm(None) == pytest.approx(
        settings.RISK_FREE_RATE_PCT + settings.EQUITY_RISK_PREMIUM_PCT
    )
