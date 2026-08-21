"""
app/agents/valuation_agent.py
================================
Stage 3 of the fundamental analysis method — intrinsic value by discounted
cash flow, following the ten steps in `fundamental_analysis_steps.md`.

WHY THIS RETURNS A BAND AND A GRID, NEVER A NUMBER:
------------------------------------------------------
A DCF looks precise and is not. Measured on a representative case, terminal
value was 56% of total intrinsic value, and the assumptions dominate the data:
moving the discount rate 11% -> 13% moved value -22.7%, terminal growth
3% -> 4.5% moved it +14.0%, while a 10% error in the cash flow data moved it
10%. Reporting a single figure would be false precision about the wrong term.

So every result carries the ±10% band, the margin-of-safety price, a
sensitivity grid, and the reverse DCF — what the market price already assumes.
The last one is often the most useful output: if the current price requires
free cash flow the company has never produced, that is the finding.

WHAT IT DOES NOT DO:
----------------------
It does not say buy or sell. It says where the price sits relative to the
band it computed, and on what assumptions. The stance is derived elsewhere,
from this plus the quality verdict plus whether the position is held.

USAGE:
------
    from app.agents.valuation_agent import run_dcf

    dcf = await run_dcf(raw, derived)
    dcf.intrinsic, dcf.mos_buy_price, dcf.terminal_share_of_value
"""

from dataclasses import dataclass, field

from loguru import logger

from app.core.config import settings
from app.data.fundamentals_scraper import FundamentalRaw
from app.data.market_risk import get_beta, discount_rate_capm

PROJECTION_YEARS = 10
STAGE1_YEARS = 5
CRORE = 1e7

# Above this, the answer is mostly an assumption about the far future rather
# than a reading of the business.
TERMINAL_SHARE_WARNING = 0.70

# The method treats sustained FCF growth above ~20% as an aggressive scenario,
# not a base case.
AGGRESSIVE_GROWTH_PCT = 20.0


@dataclass
class YearProjection:
    year: int
    growth_pct: float
    fcf: float           # ₹ Cr
    discount_factor: float
    present_value: float  # ₹ Cr


@dataclass
class DCFResult:
    symbol: str
    complete: bool = False
    not_computable_reason: str | None = None

    # Step 1
    base_fcf: float | None = None
    base_fcf_ex_outlier: float | None = None
    fcf_to_profit_crosscheck: float | None = None

    # Steps 2-3
    stage1_growth_pct: float = 0.0
    stage2_growth_pct: float = 0.0
    terminal_growth_pct: float = 0.0
    discount_rate_pct: float = 0.0
    beta: float | None = None
    beta_note: str = ""

    # Steps 4-6
    projections: list[YearProjection] = field(default_factory=list)
    terminal_value_gordon: float | None = None
    terminal_value_exit_multiple: float | None = None
    sum_pv: float | None = None
    terminal_share_of_value: float | None = None

    # Step 7
    net_debt: float | None = None
    shares_outstanding: float | None = None
    intrinsic: float | None = None

    # Step 8
    upper_band: float | None = None
    lower_band: float | None = None
    mos_buy_price: float | None = None
    current_price: float | None = None
    price_vs_band: str | None = None      # UNDERVALUED | FAIRLY_VALUED | OVERVALUED

    # Steps 9-10
    reverse_dcf_implied_fcf: float | None = None
    reverse_dcf_note: str = ""
    sensitivity: dict = field(default_factory=dict)

    assumptions: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ============================================================
# ENTRY POINT
# ============================================================

async def run_dcf(raw: FundamentalRaw, derived: dict | None = None) -> DCFResult:
    """Run the full ten-step DCF. Never raises — an unusable input returns a
    result with `complete=False` and a stated reason."""
    derived = derived or {}
    result = DCFResult(symbol=raw.symbol)

    if raw.sector_schema == "FINANCIAL":
        result.not_computable_reason = (
            "Bank/NBFC — a free-cash-flow DCF does not apply to lenders, whose "
            "borrowing is operating raw material rather than financing."
        )
        return result

    # ---- Step 1: base free cash flow ----
    fcf_series: dict[str, float] = derived.get("fcf_series") or {
        p: v["Free Cash Flow"] for p, v in raw.cash_flow.items() if "Free Cash Flow" in v
    }
    fcf_series = {p: v for p, v in fcf_series.items() if p.upper() != "TTM"}

    if len(fcf_series) < 3:
        result.not_computable_reason = "Fewer than 3 years of free cash flow available"
        return result

    recent = list(fcf_series.values())[-3:]
    result.base_fcf = sum(recent) / len(recent)

    # An unusually heavy capex year (or a one-off loss) drags the 3-year
    # average. Show the alternative rather than silently choosing one.
    median = sorted(recent)[1]
    if median and abs(recent[-1] - median) / abs(median) > 0.5:
        others = recent[:-1]
        result.base_fcf_ex_outlier = sum(others) / len(others)
        result.warnings.append(
            f"Most recent FCF ({recent[-1]:,.0f} Cr) is far from the 3-year median "
            f"({median:,.0f} Cr). Base excluding it: {result.base_fcf_ex_outlier:,.0f} Cr."
        )

    if result.base_fcf <= 0:
        result.not_computable_reason = (
            f"Base free cash flow is negative ({result.base_fcf:,.0f} Cr) — "
            f"a growth DCF cannot value it."
        )
        return result

    # Sanity check: what the company's own historical FCF-to-profit conversion
    # applied to TTM profit would imply. A wide gap means one of the two is odd.
    result.fcf_to_profit_crosscheck = _fcf_to_profit_crosscheck(raw, fcf_series)
    if result.fcf_to_profit_crosscheck:
        gap = abs(result.fcf_to_profit_crosscheck - result.base_fcf) / result.base_fcf
        if gap > 0.4:
            result.warnings.append(
                f"Cross-check disagrees: historical FCF/profit conversion applied to TTM profit "
                f"implies {result.fcf_to_profit_crosscheck:,.0f} Cr vs a {result.base_fcf:,.0f} Cr "
                f"3-year average ({gap:.0%} apart)."
            )

    # ---- Steps 2-3: growth and discount rate ----
    result.stage1_growth_pct = settings.DCF_STAGE1_GROWTH_PCT
    result.stage2_growth_pct = settings.DCF_STAGE2_GROWTH_PCT
    result.terminal_growth_pct = settings.DCF_TERMINAL_GROWTH_PCT

    beta_estimate = await get_beta(raw.symbol)
    result.beta = beta_estimate.beta
    result.beta_note = beta_estimate.note
    result.discount_rate_pct = discount_rate_capm(result.beta)

    if result.beta is None:
        result.warnings.append("Beta unavailable — discount rate assumes beta = 1.0 (market risk).")
    if beta_estimate.disagreement and beta_estimate.disagreement >= 0.35:
        result.warnings.append(beta_estimate.note)
    if max(result.stage1_growth_pct, result.stage2_growth_pct) > AGGRESSIVE_GROWTH_PCT:
        result.warnings.append(
            f"Stage-1 growth above {AGGRESSIVE_GROWTH_PCT:.0f}% is an aggressive scenario, "
            f"not a base case — very few companies sustain it."
        )
    if result.terminal_growth_pct >= result.discount_rate_pct:
        result.not_computable_reason = (
            f"Terminal growth ({result.terminal_growth_pct}%) is not below the discount rate "
            f"({result.discount_rate_pct:.1f}%) — the Gordon formula diverges."
        )
        return result

    # ---- Steps 4-6: project, terminal value, discount back ----
    core = _project_and_discount(
        result.base_fcf, result.stage1_growth_pct, result.stage2_growth_pct,
        result.terminal_growth_pct, result.discount_rate_pct,
    )
    result.projections = core["projections"]
    result.terminal_value_gordon = core["terminal_value"]
    result.sum_pv = core["sum_pv"]
    result.terminal_share_of_value = core["terminal_share"]

    year10_fcf = result.projections[-1].fcf
    result.terminal_value_exit_multiple = year10_fcf * settings.DCF_EXIT_MULTIPLE

    if result.terminal_share_of_value and result.terminal_share_of_value > TERMINAL_SHARE_WARNING:
        result.warnings.append(
            f"Terminal value is {result.terminal_share_of_value:.0%} of the total — the answer is "
            f"mostly an assumption about the far future, not a reading of the business."
        )

    gordon_vs_exit = abs(result.terminal_value_gordon - result.terminal_value_exit_multiple)
    if result.terminal_value_gordon and gordon_vs_exit / result.terminal_value_gordon > 0.4:
        result.warnings.append(
            f"The two terminal-value methods diverge: Gordon growth {result.terminal_value_gordon:,.0f} Cr "
            f"vs {settings.DCF_EXIT_MULTIPLE:.0f}x exit multiple {result.terminal_value_exit_multiple:,.0f} Cr."
        )

    # ---- Step 7: net debt and intrinsic price ----
    result.net_debt = _net_debt(raw, derived)
    result.shares_outstanding = derived.get("shares_outstanding") or raw.shares_outstanding

    if not result.shares_outstanding:
        result.not_computable_reason = "Share count unavailable — cannot convert equity value to a per-share price"
        return result
    if result.net_debt is None:
        result.warnings.append("Net debt unavailable — intrinsic value shown before any debt adjustment.")
        result.net_debt = 0.0

    equity_value_cr = result.sum_pv - result.net_debt
    result.intrinsic = (equity_value_cr * CRORE) / result.shares_outstanding

    # ---- Step 8: band and margin of safety ----
    result.upper_band = result.intrinsic * 1.10
    result.lower_band = result.intrinsic * 0.90
    result.mos_buy_price = result.lower_band * 0.70

    result.current_price = raw.top_ratios.get("Current Price")
    if result.current_price:
        if result.current_price < result.lower_band:
            result.price_vs_band = "UNDERVALUED"
        elif result.current_price <= result.upper_band:
            result.price_vs_band = "FAIRLY_VALUED"
        else:
            result.price_vs_band = "OVERVALUED"

    # ---- Step 9: reverse DCF ----
    if result.current_price:
        result.reverse_dcf_implied_fcf, result.reverse_dcf_note = _reverse_dcf(
            result, core["pv_multiple"], raw
        )

    # ---- Step 10: sensitivity ----
    result.sensitivity = _sensitivity_grid(result)

    result.assumptions = {
        "base_fcf_cr": round(result.base_fcf, 1),
        "stage1_growth_pct": result.stage1_growth_pct,
        "stage2_growth_pct": result.stage2_growth_pct,
        "terminal_growth_pct": result.terminal_growth_pct,
        "discount_rate_pct": round(result.discount_rate_pct, 2),
        "beta": result.beta,
        "risk_free_rate_pct": settings.RISK_FREE_RATE_PCT,
        "risk_free_reviewed": settings.RISK_FREE_RATE_REVIEWED,
        "equity_risk_premium_pct": settings.EQUITY_RISK_PREMIUM_PCT,
        "net_debt_cr": round(result.net_debt, 1),
        "shares_outstanding": result.shares_outstanding,
    }
    result.complete = True

    logger.info(
        f"DCF | {raw.symbol} | intrinsic={result.intrinsic:,.0f} | "
        f"band={result.lower_band:,.0f}-{result.upper_band:,.0f} | mos={result.mos_buy_price:,.0f} | "
        f"price={result.current_price} ({result.price_vs_band}) | "
        f"dr={result.discount_rate_pct:.2f}% | terminal={result.terminal_share_of_value:.0%}"
    )
    return result


# ============================================================
# MECHANICS
# ============================================================

def _project_and_discount(base_fcf, stage1_pct, stage2_pct, terminal_pct, discount_pct) -> dict:
    """
    Steps 4-6. Also returns `pv_multiple` — the sum of present values per unit
    of base FCF, which is what makes the reverse DCF a division rather than a
    numerical search: the whole model is linear in base free cash flow.
    """
    dr = discount_pct / 100.0
    projections: list[YearProjection] = []
    fcf = base_fcf
    pv_sum = 0.0
    pv_per_unit = 0.0

    for year in range(1, PROJECTION_YEARS + 1):
        growth = stage1_pct if year <= STAGE1_YEARS else stage2_pct
        fcf = fcf * (1 + growth / 100.0)
        factor = 1 / ((1 + dr) ** year)
        pv = fcf * factor
        pv_sum += pv
        pv_per_unit += (fcf / base_fcf) * factor
        projections.append(YearProjection(year, growth, fcf, factor, pv))

    year10 = projections[-1].fcf
    tg = terminal_pct / 100.0
    terminal_value = year10 * (1 + tg) / (dr - tg)
    terminal_factor = 1 / ((1 + dr) ** PROJECTION_YEARS)
    terminal_pv = terminal_value * terminal_factor

    sum_pv = pv_sum + terminal_pv
    pv_per_unit += (year10 / base_fcf) * (1 + tg) / (dr - tg) * terminal_factor

    return {
        "projections": projections,
        "terminal_value": terminal_value,
        "sum_pv": sum_pv,
        "terminal_share": terminal_pv / sum_pv if sum_pv else None,
        "pv_multiple": pv_per_unit,
    }


def _net_debt(raw: FundamentalRaw, derived: dict) -> float | None:
    """
    Net Debt = Total Debt - (Cash & Equivalents + Current Investments).

    Negative net debt (net cash) ADDS to equity value rather than subtracting —
    which falls out of the arithmetic, but is the sign error most easily made
    here, so it is worth stating.
    """
    periods = [p for p in raw.balance_sheet if p.upper() != "TTM"]
    if not periods:
        return None
    latest = periods[-1]

    borrowings = raw.balance_sheet[latest].get("Borrowings")
    if borrowings is None:
        return None

    investments = raw.balance_sheet[latest].get("Investments") or 0.0
    cash = (derived.get("cash_equivalents") or raw.cash_equivalents or {}).get(latest, 0.0)

    return borrowings - (cash + investments)


def _fcf_to_profit_crosscheck(raw: FundamentalRaw, fcf_series: dict) -> float | None:
    """Historical FCF-to-net-profit conversion applied to TTM net profit."""
    periods = [p for p in raw.annual_pnl if p.upper() != "TTM"]
    ratios = [
        fcf_series[p] / raw.annual_pnl[p]["Net Profit"]
        for p in periods
        if p in fcf_series and raw.annual_pnl[p].get("Net Profit")
    ]
    ttm_profit = raw.annual_pnl.get("TTM", {}).get("Net Profit")
    if not ratios or not ttm_profit:
        return None
    return (sum(ratios) / len(ratios)) * ttm_profit


def _reverse_dcf(result: DCFResult, pv_multiple: float, raw: FundamentalRaw) -> tuple[float | None, str]:
    """
    Step 9 — solve backwards: what base free cash flow would today's price
    require, on these same assumptions?

    Because sum_pv is linear in base FCF (sum_pv = base x pv_multiple):

        price x shares = (base_implied x pv_multiple - net_debt) x CRORE_scaling

    Comparing the answer to what the company actually earns is often the most
    useful line in the whole valuation.
    """
    if not pv_multiple or not result.current_price or not result.shares_outstanding:
        return None, ""

    market_equity_cr = (result.current_price * result.shares_outstanding) / CRORE
    implied = (market_equity_cr + result.net_debt) / pv_multiple

    ttm = raw.annual_pnl.get("TTM", {})
    profit, sales = ttm.get("Net Profit"), ttm.get("Sales")

    note = (
        f"At ₹{result.current_price:,.0f} the market is pricing in base free cash flow of "
        f"{implied:,.0f} Cr, against an actual 3-year average of {result.base_fcf:,.0f} Cr"
    )
    if profit:
        note += f" and TTM net profit of {profit:,.0f} Cr"
    if sales:
        note += f" on revenue of {sales:,.0f} Cr"
    note += "."

    if profit and implied > profit:
        note += (" The implied figure exceeds the company's entire TTM net profit — the price "
                 "assumes free cash flow it has never produced.")
    elif implied > result.base_fcf * 2:
        note += " That is more than double what the business has recently generated."

    return implied, note


def _sensitivity_grid(result: DCFResult) -> dict:
    """
    Step 10 — intrinsic value across a plausible range of discount rates and
    terminal growth rates, so the reader sees how much of the answer is choice.
    """
    grid: dict[str, dict[str, float]] = {}
    base_dr = result.discount_rate_pct

    for dr in [round(base_dr + d, 1) for d in (-2, -1, 0, 1, 2)]:
        row: dict[str, float] = {}
        for tg in (2.5, 3.0, 3.5, 4.0, 4.5):
            if tg >= dr:
                continue
            core = _project_and_discount(
                result.base_fcf, result.stage1_growth_pct, result.stage2_growth_pct, tg, dr
            )
            equity_cr = core["sum_pv"] - (result.net_debt or 0.0)
            row[f"tg_{tg}"] = round((equity_cr * CRORE) / result.shares_outstanding, 1)
        grid[f"dr_{dr}"] = row

    return {"intrinsic_by_discount_and_terminal_growth": grid, "base_discount_rate_pct": round(base_dr, 2)}
