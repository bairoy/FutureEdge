"""
app/agents/financial_agent.py
================================
Stage 2 of the fundamental analysis method — the 10-point financial checklist
from `fundamental_analysis_steps.md`.

WHAT THIS IS FOR:
------------------
Stage 1 asks "do I understand this business". Stage 3 asks "what is it worth".
This one asks the question in between: "do the financial statements hold up?"
It is the most mechanical of the three, and deliberately so — every check is a
calculation over statements already fetched by data_fetch, with a stated
threshold and a stated source.

WHAT A CHECK RETURNS, AND WHY THERE ARE FOUR STATUSES:
--------------------------------------------------------
    PASS            — computed, and it clears the bar
    FAIL            — computed, and it does not
    FLAG            — computed, but something about it needs a human to look
    NOT_COMPUTABLE  — the input does not exist in any source we have

NOT_COMPUTABLE is the important one. Marking a check that way rather than
quietly scoring 7 out of 7 is what makes `completeness` mean something, and
completeness is what gates whether a verdict gets issued at all.

Two checks are answered elsewhere and reconciled at the fan-in, NOT here:
business diversity (9) and subsidiaries (10) are the same questions as Stage 1's
Q12 and Q18, and Stage 1 answers them from the annual report and the web. This
agent runs in PARALLEL with Stage 1 and cannot see its output, so it leaves both
open and `thesis_agent` — the first node that sees both reports — fills them in.
Neither emits missing_data from here; thesis_agent records that only if Stage 1
came up empty too.

WHAT THE LLM IS AND ISN'T USED FOR:
--------------------------------------
Same principle as llm_reasoner.py and fundamental_agent.py: the model narrates
the checks that have already been computed. It does not run a check, override
a status, or introduce a number. If it is unavailable the template summary is
used, so Stage 2 is never blocked on an API call.

USAGE:
------
    from app.agents.financial_agent import run_checklist

    report = run_checklist(raw, derived)
    report.completeness   # 0.0-1.0, fraction of the 10 actually computed
"""

from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from app.core.config import settings
from app.data.fundamentals_scraper import FundamentalRaw


# ============================================================
# OUTPUT SHAPE
# ============================================================

class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    FLAG = "FLAG"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"


@dataclass
class Check:
    n: int
    name: str
    status: CheckStatus
    value: float | None = None
    detail: str = ""
    source: str = "screener"     # screener | derived | manual | none


@dataclass
class ChecklistResult:
    checks: list[Check] = field(default_factory=list)
    completeness: float = 0.0
    warnings: list[str] = field(default_factory=list)   # the calculation guards
    summary: str = ""

    @property
    def computed(self) -> list[Check]:
        return [c for c in self.checks if c.status is not CheckStatus.NOT_COMPUTABLE]


# ============================================================
# THRESHOLDS — from fundamental_analysis_steps.md, Stage 2
# ============================================================

T = {
    "gross_margin_min": 20.0,       # check 1
    "growth_gap_pp": 5.0,           # check 2 — sales vs profit CAGR divergence
    "eps_dilution_pp": 5.0,         # check 3 — EPS CAGR lagging profit CAGR
    "debt_equity_max": 0.5,         # check 4
    # Below this D/E, borrowings are immaterial and a percentage trend on them
    # is noise — a debt-free company going from 20 Cr to 190 Cr of borrowings
    # reads as "+863%" while remaining debt-free. Same low-base distortion the
    # guards catch for profit CAGR; it has to be applied here explicitly.
    "debt_materiality_de": 0.10,
    "roe_min": 25.0,                # check 8 — the checklist bar, above the
                                    #           general >18% used elsewhere
    "cfo_years": 5,                 # check 7
    "one_off_other_income_pct": 25.0,   # guard: Other Income as % of PBT
    "low_base_ratio": 0.25,             # guard: base year vs series median
}

_LOOKBACKS = ("10 Years:", "5 Years:", "3 Years:")


# ============================================================
# ENTRY POINT
# ============================================================

def run_checklist(raw: FundamentalRaw, derived: dict | None = None) -> ChecklistResult:
    """
    Run all ten checks. Never raises on missing data — a check that cannot be
    computed reports itself as NOT_COMPUTABLE and the run continues.
    """
    derived = derived or {}

    if raw.sector_schema == "FINANCIAL":
        # Same reasoning as fundamental_agent: for a lender, borrowing is the
        # raw material, so leverage and coverage checks do not carry their
        # usual meaning. Refuse the whole checklist rather than score 3 of 10.
        return ChecklistResult(
            checks=[Check(n, name, CheckStatus.NOT_COMPUTABLE, None,
                          "Bank/NBFC — this checklist does not apply to lenders", "none")
                    for n, name in enumerate(_CHECK_NAMES, start=1)],
            completeness=0.0,
            summary="Not applicable: this is a bank or NBFC.",
        )

    warnings = _calculation_guards(raw, derived)

    checks = [
        _check_01_gross_margin(raw),
        _check_02_growth_alignment(raw),
        _check_03_eps_consistency(raw),
        _check_04_debt_level(raw),
        _check_05_inventory(raw),
        _check_06_receivables(raw),
        _check_07_cash_flow(raw),
        _check_08_roe(raw),
        _check_09_business_diversity(raw),
        _check_10_subsidiaries(raw),
    ]

    result = ChecklistResult(
        checks=checks,
        completeness=len([c for c in checks if c.status is not CheckStatus.NOT_COMPUTABLE]) / len(checks),
        warnings=warnings,
    )
    result.summary = _template_summary(result)

    logger.info(
        f"Stage 2 checklist | {raw.symbol} | completeness={result.completeness:.0%} | "
        f"pass={sum(1 for c in checks if c.status is CheckStatus.PASS)} "
        f"fail={sum(1 for c in checks if c.status is CheckStatus.FAIL)} "
        f"flag={sum(1 for c in checks if c.status is CheckStatus.FLAG)} "
        f"warnings={len(warnings)}"
    )
    return result


_CHECK_NAMES = [
    "Gross Profit Margin", "Revenue vs Profit growth", "EPS consistency",
    "Debt level", "Inventory", "Sales vs Receivables", "Cash Flow from Operations",
    "Return on Equity", "Business diversity", "Subsidiaries",
]


# ============================================================
# SERIES HELPERS
# ============================================================

def _annual_periods(raw: FundamentalRaw) -> list[str]:
    """Annual P&L periods, most recent last, with the TTM column removed.

    TTM is a trailing-twelve-months figure, not a reporting year — including it
    in a year-over-year series double-counts the most recent period."""
    return [p for p in raw.annual_pnl if p.upper() != "TTM"]


def _series(table: dict[str, dict[str, float]], row: str, periods: list[str]) -> dict[str, float]:
    return {p: table[p][row] for p in periods if p in table and row in table[p]}


def _trend(values: list[float]) -> float | None:
    """Change from first to last, as a percentage of the first."""
    if len(values) < 2 or not values[0]:
        return None
    return ((values[-1] - values[0]) / abs(values[0])) * 100


# ============================================================
# THE TEN CHECKS
# ============================================================

def _check_01_gross_margin(raw: FundamentalRaw) -> Check:
    """
    Gross Profit Margin = (Net Sales - COGS) / Net Sales, target >20%.

    Screener's HTML tables carry no COGS row — only total "Expenses" and "OPM %"
    — which is why this check used to be NOT_COMPUTABLE with operating margin
    offered as a labelled proxy. But the JSON schedules endpoint breaks that
    same Expenses row down, and `Material Cost %` is COGS as a percentage of
    sales, with a dozen years of history.

    Working in percentages is deliberate: gross margin is 100 minus the cost
    percentage, so there are no absolute figures to reconcile between a
    consolidated P&L and a standalone schedule.

    TWO DEFINITIONS ARE REPORTED, NOT ONE:
        materials only          = 100 - Material Cost%
        materials + conversion  = 100 - Material Cost% - Manufacturing Cost%

    The first is how gross margin is conventionally read off Screener and is
    the primary figure; the second is closer to a strict accounting COGS for a
    manufacturer, which capitalises factory conversion cost into inventory.
    They can differ by several points. The method's own general rule applies —
    never silently pick one assumption when more than one is reasonable; show
    the range and say which is primary — so the check tests the primary figure
    and states the stricter one beside it.
    """
    periods = _annual_periods(raw)
    material = {p: v for p, v in (raw.expense_breakdown.get("Material Cost %") or {}).items()
                if p in periods}

    if not material:
        # Schedules unavailable — fall back to the old behaviour rather than
        # testing a >20% gross-margin bar against operating margin, which is
        # strictly smaller and would fail companies that actually pass.
        opm = _series(raw.annual_pnl, "OPM %", periods)
        latest_opm = list(opm.values())[-1] if opm else None
        detail = (
            "Material cost breakdown unavailable from Screener's schedules, so true "
            "gross margin needs the annual report. "
            + (f"Operating margin (a strictly lower proxy) is {latest_opm:.0f}%."
               if latest_opm is not None else "")
        )
        return Check(1, "Gross Profit Margin", CheckStatus.NOT_COMPUTABLE, latest_opm, detail, "none")

    ordered = [material[p] for p in periods if p in material]
    gross = 100.0 - ordered[-1]
    trend = _trend([100.0 - v for v in ordered[-5:]])

    manufacturing = (raw.expense_breakdown.get("Manufacturing Cost %") or {}).get(periods[-1])
    strict = gross - manufacturing if manufacturing is not None else None

    status = (CheckStatus.PASS if gross > T["gross_margin_min"]
              else CheckStatus.FAIL)

    detail = f"Gross margin {gross:.1f}% (100% - material cost {ordered[-1]:.1f}%)"
    if strict is not None:
        detail += f"; {strict:.1f}% after manufacturing cost {manufacturing:.1f}%"
    if trend is not None:
        detail += f"; {trend:+.0f}% over {min(len(ordered), 5)} years"
    detail += f" — bar is {T['gross_margin_min']:.0f}%"

    return Check(1, "Gross Profit Margin", status, round(gross, 1), detail, "screener")


def _check_02_growth_alignment(raw: FundamentalRaw) -> Check:
    """
    Profit growth should track sales growth. A sharp divergence means margins
    are moving, and the reason matters more than the number.
    """
    sales = raw.growth.get("Compounded Sales Growth", {})
    profit = raw.growth.get("Compounded Profit Growth", {})
    if not sales or not profit:
        return Check(2, "Revenue vs Profit growth", CheckStatus.NOT_COMPUTABLE,
                     None, "Compounded growth tables not available", "none")

    gaps = {lb: profit[lb] - sales[lb] for lb in _LOOKBACKS if lb in sales and lb in profit}
    if not gaps:
        return Check(2, "Revenue vs Profit growth", CheckStatus.NOT_COMPUTABLE,
                     None, "No overlapping lookback periods", "none")

    worst_lb = min(gaps, key=lambda k: gaps[k])
    worst = gaps[worst_lb]
    detail = "; ".join(
        f"{lb.rstrip(':')}: sales {sales[lb]:.0f}% vs profit {profit[lb]:.0f}%" for lb in gaps
    )

    if worst < -T["growth_gap_pp"]:
        status = CheckStatus.FLAG
        detail += (f" — profit growth trails sales by {abs(worst):.0f}pp over "
                   f"{worst_lb.rstrip(':')}: margins compressing, find out why")
    elif max(gaps.values()) > T["growth_gap_pp"]:
        status = CheckStatus.FLAG
        detail += " — profit growing well ahead of sales; confirm it is margin expansion, not one-offs"
    else:
        status = CheckStatus.PASS
        detail += " — profit tracks sales"

    return Check(2, "Revenue vs Profit growth", status, worst, detail, "screener")


def _check_03_eps_consistency(raw: FundamentalRaw) -> Check:
    """
    EPS should move with net profit. Where it lags, the share count grew —
    quantify the dilution rather than just noting it.

    Share count is derived from Equity Capital (face value is constant), so a
    bonus issue or split shows up here as growth. That is mechanical, not
    dilution in the sense that matters, and the detail says so rather than
    letting the reader mistake one for the other.
    """
    periods = _annual_periods(raw)
    eps = _series(raw.annual_pnl, "EPS in Rs", periods)
    profit = _series(raw.annual_pnl, "Net Profit", periods)
    equity_capital = _series(raw.balance_sheet, "Equity Capital", periods)

    if len(eps) < 3 or len(profit) < 3:
        return Check(3, "EPS consistency", CheckStatus.NOT_COMPUTABLE,
                     None, "Need at least 3 years of EPS and net profit", "none")

    window = min(len(eps), len(profit), 6)
    eps_trend = _trend(list(eps.values())[-window:])
    profit_trend = _trend(list(profit.values())[-window:])
    share_trend = _trend(list(equity_capital.values())[-window:]) if len(equity_capital) >= 2 else None

    if eps_trend is None or profit_trend is None:
        return Check(3, "EPS consistency", CheckStatus.NOT_COMPUTABLE,
                     None, "Base year is zero — trend undefined", "none")

    gap = eps_trend - profit_trend
    detail = f"Over {window} years: net profit {profit_trend:+.0f}%, EPS {eps_trend:+.0f}%"
    if share_trend is not None:
        detail += f", share count {share_trend:+.0f}%"
        if share_trend > 40:
            detail += " (a jump this size is usually a bonus issue or split, not dilution — verify)"

    if gap < -T["eps_dilution_pp"]:
        status = CheckStatus.FLAG
        detail += f" — EPS lags profit by {abs(gap):.0f}pp, i.e. shareholders were diluted"
    else:
        status = CheckStatus.PASS
        detail += " — EPS tracks profit"

    return Check(3, "EPS consistency", status, gap, detail, "derived")


def _check_04_debt_level(raw: FundamentalRaw) -> Check:
    """Borrowings / Total Equity, plus whether absolute borrowings are rising."""
    periods = _annual_periods(raw)
    borrowings = _series(raw.balance_sheet, "Borrowings", periods)
    equity_capital = _series(raw.balance_sheet, "Equity Capital", periods)
    reserves = _series(raw.balance_sheet, "Reserves", periods)

    if not borrowings or not equity_capital:
        return Check(4, "Debt level", CheckStatus.NOT_COMPUTABLE,
                     None, "Borrowings or equity not in the balance sheet", "none")

    latest = list(borrowings)[-1]
    total_equity = equity_capital.get(latest, 0.0) + reserves.get(latest, 0.0)
    if not total_equity:
        return Check(4, "Debt level", CheckStatus.NOT_COMPUTABLE,
                     None, "Total equity is zero", "none")

    de = borrowings[latest] / total_equity
    borrow_trend = _trend(list(borrowings.values())[-5:])
    detail = f"D/E {de:.2f} as of {latest}"

    # The rising-borrowings test only means something once borrowings are
    # material. Applied unconditionally it fires on every debt-free company,
    # where a tiny absolute increase is an enormous percentage.
    trend_is_material = de >= T["debt_materiality_de"]
    if borrow_trend is not None:
        detail += f"; absolute borrowings {borrow_trend:+.0f}% over 5 years"
        if not trend_is_material:
            detail += " (off an immaterial base — trend not scored)"

    rising = trend_is_material and borrow_trend is not None and borrow_trend > 50

    if de > T["debt_equity_max"] * 2:
        status = CheckStatus.FAIL
        detail += " — highly leveraged"
    elif de > T["debt_equity_max"]:
        status = CheckStatus.FLAG
        detail += " — leverage above the comfort threshold"
    elif rising:
        status = CheckStatus.FLAG
        detail += " — leverage is low but rising fast"
    else:
        status = CheckStatus.PASS

    return Check(4, "Debt level", status, de, detail, "derived")


def _check_05_inventory(raw: FundamentalRaw) -> Check:
    """
    Inventory Days against Operating Margin over the same period. Rising
    inventory is only acceptable if margins are improving to match.
    """
    periods = _annual_periods(raw)
    inv = _series(raw.ratios_trend, "Inventory Days", periods)
    opm = _series(raw.annual_pnl, "OPM %", periods)

    if len(inv) < 3:
        return Check(5, "Inventory", CheckStatus.NOT_COMPUTABLE,
                     None, "Inventory Days not available for 3+ years", "none")

    inv_trend = _trend(list(inv.values())[-5:])
    opm_trend = _trend(list(opm.values())[-5:]) if len(opm) >= 2 else None
    latest = list(inv.values())[-1]

    detail = f"Inventory days {latest:.0f}, {inv_trend:+.0f}% over 5 years" if inv_trend is not None else f"Inventory days {latest:.0f}"
    if opm_trend is not None:
        detail += f"; operating margin {opm_trend:+.0f}% over the same period"

    if inv_trend is not None and inv_trend > 20 and (opm_trend is None or opm_trend < inv_trend):
        status = CheckStatus.FLAG
        detail += " — inventory building faster than margins are improving"
    else:
        status = CheckStatus.PASS

    return Check(5, "Inventory", status, latest, detail, "screener")


def _check_06_receivables(raw: FundamentalRaw) -> Check:
    """
    Debtor Days against sales growth. Receivables outrunning sales can mean
    weak collections or channel stuffing.
    """
    periods = _annual_periods(raw)
    debtor = _series(raw.ratios_trend, "Debtor Days", periods)
    sales = _series(raw.annual_pnl, "Sales", periods)

    if len(debtor) < 3:
        return Check(6, "Sales vs Receivables", CheckStatus.NOT_COMPUTABLE,
                     None, "Debtor Days not available for 3+ years", "none")

    debtor_trend = _trend(list(debtor.values())[-5:])
    sales_trend = _trend(list(sales.values())[-5:]) if len(sales) >= 2 else None
    latest = list(debtor.values())[-1]

    detail = f"Debtor days {latest:.0f}"
    if debtor_trend is not None:
        detail += f", {debtor_trend:+.0f}% over 5 years"
    if sales_trend is not None:
        detail += f"; sales {sales_trend:+.0f}% over the same period"

    if debtor_trend is not None and sales_trend is not None and debtor_trend > sales_trend + 20:
        status = CheckStatus.FLAG
        detail += " — receivables growing faster than sales"
    else:
        status = CheckStatus.PASS

    return Check(6, "Sales vs Receivables", status, latest, detail, "screener")


def _check_07_cash_flow(raw: FundamentalRaw) -> Check:
    """CFO must be positive in every one of the last 5 years. A positive but
    declining trend is still worth flagging."""
    periods = _annual_periods(raw)
    cfo = _series(raw.cash_flow, "Cash from Operating Activity", periods)

    if len(cfo) < 3:
        return Check(7, "Cash Flow from Operations", CheckStatus.NOT_COMPUTABLE,
                     None, "CFO not available for 3+ years", "none")

    recent = dict(list(cfo.items())[-T["cfo_years"]:])
    negatives = [p for p, v in recent.items() if v < 0]
    trend = _trend(list(recent.values()))
    detail = f"CFO over {len(recent)} years, latest {list(recent.values())[-1]:,.0f} Cr"

    if negatives:
        status = CheckStatus.FAIL
        detail += f" — negative in {', '.join(negatives)}"
    elif trend is not None and trend < -20:
        status = CheckStatus.FLAG
        detail += f" — positive every year but declining {trend:+.0f}%"
    else:
        status = CheckStatus.PASS
        detail += " — positive every year"

    return Check(7, "Cash Flow from Operations", status, list(recent.values())[-1], detail, "screener")


def _check_08_roe(raw: FundamentalRaw) -> Check:
    """
    Latest ROE plus the 3-year and 5-year averages, against the checklist's
    >25% bar. Where the latest is well below the multi-year average, that is
    the thing worth explaining (a capital raise, a one-off loss).
    """
    roe_table = raw.growth.get("Return on Equity", {})
    latest = raw.top_ratios.get("ROE %") or raw.top_ratios.get("ROE")
    avg_3y, avg_5y = roe_table.get("3 Years:"), roe_table.get("5 Years:")

    if latest is None and not roe_table:
        return Check(8, "Return on Equity", CheckStatus.NOT_COMPUTABLE,
                     None, "ROE not available", "none")

    if latest is None:
        latest = roe_table.get("Last Year:")

    parts = [f"latest {latest:.1f}%" if latest is not None else "latest n/a"]
    if avg_3y is not None:
        parts.append(f"3yr avg {avg_3y:.0f}%")
    if avg_5y is not None:
        parts.append(f"5yr avg {avg_5y:.0f}%")
    detail = "ROE " + ", ".join(parts)

    if latest is None:
        status = CheckStatus.NOT_COMPUTABLE
    elif latest >= T["roe_min"]:
        status = CheckStatus.PASS
    elif avg_5y is not None and latest < avg_5y - 5:
        status = CheckStatus.FLAG
        detail += f" — latest is {avg_5y - latest:.0f}pp below the 5-year average; find out why"
    else:
        status = CheckStatus.FAIL
        detail += f" — below the {T['roe_min']:.0f}% bar this checklist sets"

    return Check(8, "Return on Equity", status, latest, detail, "screener")


# Checks 9 and 10 are left OPEN here on purpose, and filled by thesis_agent.
#
# Both are the same questions as Stage 1's Q12 (segment revenue mix) and Q18
# (subsidiary list), which Stage 1 answers from the annual report and the web.
# This agent runs in PARALLEL with Stage 1 and cannot read its output, so
# answering them here would mean retrieving the same documents a second time —
# and risking a second answer that contradicts the first on the same question.
#
# They also emit no missing_data from here. If Stage 1 answered them there is
# nothing missing, and if it did not, thesis_agent is the node that knows.

_RECONCILED_AT_FAN_IN = "answered by Stage 1 — reconciled at fan-in"


def _check_09_business_diversity(raw: FundamentalRaw) -> Check:
    """Segment mix and unrelated acquisitions — Stage 1 Q12 answers this."""
    return Check(9, "Business diversity", CheckStatus.NOT_COMPUTABLE, None,
                 _RECONCILED_AT_FAN_IN, "stage1")


def _check_10_subsidiaries(raw: FundamentalRaw) -> Check:
    """Subsidiary list with ownership % and purpose — Stage 1 Q18 answers this."""
    return Check(10, "Subsidiaries", CheckStatus.NOT_COMPUTABLE, None,
                 _RECONCILED_AT_FAN_IN, "stage1")


# ============================================================
# CALCULATION GUARDS
# ============================================================

def _calculation_guards(raw: FundamentalRaw, derived: dict) -> list[str]:
    """
    The four cautions the method attaches to Stage 2. These do not fail a
    check — they warn that a number a check relies on may not mean what it
    appears to, which is a different thing and belongs beside the result
    rather than inside it.
    """
    warnings: list[str] = []
    periods = _annual_periods(raw)

    # 1. One-off items: an outsized "Other Income" can carry a whole year's
    #    profit growth, making a multi-year CAGR meaningless.
    other = _series(raw.annual_pnl, "Other Income", periods)
    pbt = _series(raw.annual_pnl, "Profit before tax", periods)
    for period in list(other)[-5:]:
        if period in pbt and pbt[period]:
            share = (other[period] / pbt[period]) * 100
            if share > T["one_off_other_income_pct"]:
                warnings.append(
                    f"{period}: Other Income is {share:.0f}% of profit before tax — "
                    f"check for a one-off before trusting any growth rate spanning this year"
                )

    # 2. Low or negative base year: a large CAGR off a near-zero start is not
    #    comparable to normal growth.
    profit = _series(raw.annual_pnl, "Net Profit", periods)
    values = list(profit.values())
    if len(values) >= 5:
        median = sorted(values)[len(values) // 2]
        for label, count in (("5-year", 5), ("10-year", 10)):
            if len(values) >= count:
                base = values[-count]
                if base <= 0:
                    warnings.append(f"{label} profit growth starts from a negative base — the CAGR is not meaningful")
                elif median and base < median * T["low_base_ratio"]:
                    warnings.append(
                        f"{label} profit growth starts from an unusually low base "
                        f"({base:,.0f} Cr vs {median:,.0f} Cr median) — treat the CAGR with caution"
                    )

    # 3. EBITDA is not free cash flow, and the gap is widest exactly where it
    #    matters — a company reinvesting heavily to stay competitive.
    op = _series(raw.annual_pnl, "Operating Profit", periods)
    fcf = derived.get("fcf_series") or _series(raw.cash_flow, "Free Cash Flow", periods)
    op_trend = _trend(list(op.values())[-5:]) if len(op) >= 2 else None
    fcf_trend = _trend(list(fcf.values())[-5:]) if len(fcf) >= 2 else None
    if op_trend is not None and fcf_trend is not None and op_trend - fcf_trend > 50:
        warnings.append(
            f"Operating profit is up {op_trend:+.0f}% over 5 years while free cash flow is "
            f"{fcf_trend:+.0f}% — heavy reinvestment; do not read EBITDA as cash generation"
        )

    # 4. Free cash flow must come from itemised capex, not the whole investing
    #    line. Screener publishes FCF directly, so flag only if it is absent.
    if not fcf:
        warnings.append(
            "No Free Cash Flow row — if deriving it, subtract itemised capex only, "
            "not the whole investing-activities line (that includes financial investments)"
        )

    return warnings


# ============================================================
# NARRATION — explain the checks, do not re-run them
# ============================================================

def _template_summary(result: ChecklistResult) -> str:
    computed = result.computed
    passes = [c for c in computed if c.status is CheckStatus.PASS]
    fails = [c for c in computed if c.status is CheckStatus.FAIL]
    flags = [c for c in computed if c.status is CheckStatus.FLAG]

    parts = [
        f"{len(passes)} of {len(computed)} computable checks pass "
        f"({len(result.checks) - len(computed)} of 10 need the annual report)."
    ]
    if fails:
        parts.append("Fails: " + "; ".join(c.name for c in fails) + ".")
    if flags:
        parts.append("Needs a look: " + "; ".join(c.name for c in flags) + ".")
    if result.warnings:
        parts.append(f"{len(result.warnings)} calculation caution(s) attached.")
    return " ".join(parts)


async def narrate_checklist(symbol: str, result: ChecklistResult) -> str:
    """
    Turn the computed checklist into plain language via the local model
    (Ollama through LOCAL_MODEL_BASE_URL, OpenAI-compatible).

    The model is given only what has already been computed and is told not to
    add to it. On any failure — model down, timeout, disabled — the template
    summary is returned, so Stage 2 never blocks on this.
    """
    if not settings.LLM_REASONING_ENABLED or not settings.LOCAL_MODEL_BASE_URL:
        return result.summary

    lines = [
        f"{c.n}. {c.name}: {c.status.value}"
        + (f" — {c.detail}" if c.detail else "")
        for c in result.checks
    ]
    prompt = (
        f"Financial checklist for {symbol}, already computed. Do not add ratios, "
        f"numbers or judgments that are not listed.\n\n"
        + "\n".join(lines)
        + ("\n\nCalculation cautions:\n" + "\n".join(f"- {w}" for w in result.warnings)
           if result.warnings else "")
        + "\n\nWrite 3-4 sentences for a long-term investor explaining what these results "
          "mean together. Say plainly which checks could not be computed and why that "
          "limits the conclusion. Do not recommend buying or selling."
    )

    try:
        import asyncio
        from openai import OpenAI

        def _call():
            client = OpenAI(
                api_key=settings.LOCAL_MODEL_API_KEY or "ollama",
                base_url=settings.LOCAL_MODEL_BASE_URL,
                timeout=30.0,
            )
            resp = client.chat.completions.create(
                model=settings.LOCAL_MODEL_NAME,
                messages=[
                    {"role": "system", "content":
                        "You narrate pre-computed financial checklists for Indian equities. "
                        "You explain the numbers given to you. You never introduce new numbers "
                        "and never issue a buy or sell recommendation."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=350,
                temperature=0.2,
            )
            return resp.choices[0].message.content.strip()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _call)

    except Exception as e:
        logger.warning(f"Stage 2 narration failed for {symbol} (using template): {e}")
        return result.summary
