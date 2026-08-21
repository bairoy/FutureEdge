"""
app/agents/fundamental_agent.py
==================================
Fundamental Analysis Agent — the "Investing Mode" counterpart to signal_agent.py.

WHAT THIS DOES:
---------------
Takes raw scraped data from app.data.fundamentals_scraper and turns it into
a quality scorecard: ratios with red/yellow/green flags, a DuPont breakdown
of ROE, growth trends, and a verdict (INVESTMENT_GRADE / NEUTRAL / AVOID /
NOT_RATED).

This answers "is this an investable business?" and nothing else. It issues no
position size — Investing mode is advisory, places no orders, and leaves sizing
to the reader. It is also price-blind: whether the stock is worth buying today
is decided against the DCF band, separately.

ALIGNMENT WITH ZERODHA VARSITY:
---------------------------------
Varsity's Fundamental Analysis module frames this as: equity research via
financial statements -> ratio analysis (profitability, leverage, valuation)
-> intrinsic value assessment -> separating "investment grade" companies
from "junk" via a checklist of common traits. That's exactly the pipeline
below. This agent does NOT replace judgment — it's the same checklist
approach Varsity teaches, automated and applied consistently across your
watchlist instead of done by hand once per stock.

WHAT THE LLM IS AND ISN'T USED FOR:
--------------------------------------
The LLM only narrates the numbers this agent has already computed — it does
not independently judge the company. This mirrors llm_reasoner.py's existing
principle for trade rationale: explain, don't decide. If the LLM is
unavailable or disabled, a template-based rationale is used instead (see
_template_rationale below) so the scorecard is never blocked on an API call.

USAGE:
------
    from app.agents.fundamental_agent import generate_scorecard

    scorecard = await generate_scorecard("RELIANCE")
    print(scorecard.verdict, scorecard.confidence, scorecard.not_rated_reason)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from loguru import logger

from app.core.config import settings
from app.data.fundamentals_scraper import get_fundamentals, FundamentalRaw


# ============================================================
# OUTPUT SHAPE
# ============================================================

class Verdict(str, Enum):
    INVESTMENT_GRADE = "INVESTMENT_GRADE"
    NEUTRAL = "NEUTRAL"
    AVOID = "AVOID"
    # Not a bad verdict — an explicit refusal to issue one. Research desks
    # carry the same state (Not Rated / Under Review). Emitting a score from
    # data we know to be wrong or absent is worse than emitting nothing.
    NOT_RATED = "NOT_RATED"


class FlagColor(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


@dataclass
class RatioFlag:
    label: str
    value: float | None
    color: FlagColor
    note: str = ""


@dataclass
class FundamentalScorecard:
    symbol: str
    generated_at: datetime
    data_as_of: str  # most recent period label found in the source data, e.g. "Mar 2026"

    verdict: Verdict
    confidence: str  # "HIGH" | "MEDIUM" | "LOW" — based on data completeness + flag agreement

    # Set only when verdict is NOT_RATED, e.g. "SECTOR_UNSUPPORTED".
    not_rated_reason: str | None = None

    profitability: list[RatioFlag] = field(default_factory=list)
    leverage: list[RatioFlag] = field(default_factory=list)
    growth: list[RatioFlag] = field(default_factory=list)
    valuation: list[RatioFlag] = field(default_factory=list)

    dupont_net_margin: float | None = None
    dupont_asset_turnover: float | None = None
    dupont_equity_multiplier: float | None = None
    dupont_roe_check: float | None = None  # should ~match reported ROE; large gap = data issue

    red_flags: list[str] = field(default_factory=list)    # Screener's own "Cons" + computed threshold breaches
    green_flags: list[str] = field(default_factory=list)   # Screener's own "Pros" + computed threshold passes

    rationale: str = ""


# ============================================================
# CONFIG — thresholds, kept simple and adjustable
# Cross-check these against your own ratio notes and adjust freely;
# these are standard textbook/Varsity-level defaults, not tuned.
# ============================================================

THRESHOLDS = {
    # Varsity puts investable-grade ROE above 18%, and the 10-point checklist
    # (Stage 2, wired in later) raises that to 25%. These were 15/10, which
    # passed companies the source method would have rejected.
    "roe_good": 18.0, "roe_ok": 12.0,
    "roce_good": 18.0, "roce_ok": 12.0,
    "debt_equity_good": 0.5, "debt_equity_ok": 1.0,
    "interest_coverage_good": 6.0, "interest_coverage_ok": 3.0,
    "sales_growth_5y_good": 10.0, "sales_growth_5y_ok": 5.0,
    "profit_growth_5y_good": 10.0, "profit_growth_5y_ok": 5.0,
    # Varsity: avoid beyond 25-30x. This used to flag nothing below 60x.
    "pe_good": 25.0, "pe_ok": 30.0,
}

# Applied by the Stage 2 checklist in a later phase — kept next to its
# sibling so the two ROE bars stay visibly different on purpose.
ROE_CHECKLIST_THRESHOLD = 25.0


# ============================================================
# MAIN ENTRY POINT
# ============================================================

async def generate_scorecard(symbol: str, consolidated: bool = True) -> FundamentalScorecard:
    raw = await get_fundamentals(symbol, consolidated=consolidated)

    # Banks and NBFCs are refused, not scored. Screener serves them a different
    # P&L schema, so every ratio below reads None — but the deeper problem is
    # that Debt/Equity and interest coverage are meaningless for a lender:
    # borrowing IS the raw material. A low score here would be an artefact of
    # the model, not a finding about the company.
    if raw.sector_schema == "FINANCIAL":
        logger.info(f"{symbol} uses Screener's financial-sector schema — returning NOT_RATED")
        return _not_rated(
            symbol,
            raw,
            reason="SECTOR_UNSUPPORTED",
            explanation=(
                f"{symbol} is a bank or NBFC. This scorecard's leverage and coverage "
                f"ratios do not carry their usual meaning for lenders, so no verdict "
                f"is issued rather than a misleading one."
            ),
        )

    profitability, dupont = _score_profitability(raw)
    leverage = _score_leverage(raw)
    growth = _score_growth(raw)
    valuation = _score_valuation(raw)

    red_flags = list(raw.cons)   # Screener's own machine-generated cons
    green_flags = list(raw.pros)  # Screener's own machine-generated pros

    for group in (profitability, leverage, growth, valuation):
        for f in group:
            if f.color == FlagColor.RED and f.note:
                red_flags.append(f.note)
            elif f.color == FlagColor.GREEN and f.note:
                green_flags.append(f.note)

    verdict, confidence = _decide_verdict(
        profitability, leverage, growth, valuation, red_flags, green_flags, raw
    )

    data_as_of = _latest_period(raw)

    scorecard = FundamentalScorecard(
        symbol=symbol,
        generated_at=datetime.now(timezone.utc),
        data_as_of=data_as_of or "unknown",
        verdict=verdict,
        confidence=confidence,
        profitability=profitability,
        leverage=leverage,
        growth=growth,
        valuation=valuation,
        dupont_net_margin=dupont.get("net_margin"),
        dupont_asset_turnover=dupont.get("asset_turnover"),
        dupont_equity_multiplier=dupont.get("equity_multiplier"),
        dupont_roe_check=dupont.get("roe_check"),
        red_flags=red_flags,
        green_flags=green_flags,
    )

    scorecard.rationale = await _generate_rationale(scorecard, raw)
    return scorecard


def _not_rated(symbol: str, raw: FundamentalRaw, reason: str, explanation: str) -> FundamentalScorecard:
    """Build an explicit no-verdict scorecard. Carries the reason, not just a blank."""
    return FundamentalScorecard(
        symbol=symbol,
        generated_at=datetime.now(timezone.utc),
        data_as_of=_latest_period(raw) or "unknown",
        verdict=Verdict.NOT_RATED,
        confidence="LOW",
        not_rated_reason=reason,
        rationale=explanation,
    )


# ============================================================
# SCORING — profitability (incl. DuPont)
# ============================================================

def _latest_period(raw: FundamentalRaw) -> str | None:
    """Latest period in the annual P&L — used for display. May be "TTM"."""
    periods = list(raw.annual_pnl.keys())
    return periods[-1] if periods else None


def _latest_common_period(raw: FundamentalRaw) -> str | None:
    """
    Latest period present in BOTH the annual P&L and the balance sheet.

    This exists because _latest_period() returns "TTM" for most companies —
    Screener's P&L carries a trailing-twelve-months column, the balance sheet
    does not. Every balance-sheet lookup keyed on "TTM" missed, so DuPont never
    computed and Debt/Equity was never produced, silently, for every symbol.
    """
    for period in reversed(list(raw.annual_pnl.keys())):
        if period in raw.balance_sheet:
            return period
    return None


def _prior_balance_sheet_period(raw: FundamentalRaw, period: str) -> str | None:
    """The balance-sheet period immediately before `period`, for averaging."""
    periods = list(raw.balance_sheet.keys())
    if period not in periods:
        return None
    idx = periods.index(period)
    return periods[idx - 1] if idx > 0 else None


def _total_equity(bs_period: dict[str, float]) -> float | None:
    """Shareholders' equity = Equity Capital + Reserves, as Screener splits it."""
    equity_capital = bs_period.get("Equity Capital")
    reserves = bs_period.get("Reserves")
    if equity_capital is None and reserves is None:
        return None
    total = (equity_capital or 0.0) + (reserves or 0.0)
    return total or None


def _average(current: float | None, prior: float | None) -> float | None:
    """
    Balance-sheet averaging: (opening + closing) / 2.

    DuPont mixes a flow (P&L, earned across the year) with a stock (balance
    sheet, a single date). Using the closing balance alone understates turnover
    and leverage for a growing company — the source method specifies averages.
    Falls back to the closing figure when there is no prior year.
    """
    if current is None:
        return None
    if prior is None:
        return current
    return (current + prior) / 2.0


def _score_profitability(raw: FundamentalRaw) -> tuple[list[RatioFlag], dict]:
    flags: list[RatioFlag] = []
    dupont: dict = {}

    roe = raw.top_ratios.get("ROE") or raw.top_ratios.get("ROE %")
    roce = raw.top_ratios.get("ROCE") or raw.top_ratios.get("ROCE %")

    flags.append(_threshold_flag("ROE %", roe, THRESHOLDS["roe_good"], THRESHOLDS["roe_ok"]))
    flags.append(_threshold_flag("ROCE %", roce, THRESHOLDS["roce_good"], THRESHOLDS["roce_ok"]))

    # DuPont: ROE = Net Margin x Asset Turnover x Equity Multiplier.
    # Keyed on the latest period present in BOTH statements (not "TTM", which
    # exists only in the P&L), and using AVERAGE balance-sheet figures.
    latest = _latest_common_period(raw)
    if latest:
        prior = _prior_balance_sheet_period(raw, latest)
        pnl = raw.annual_pnl.get(latest, {})
        bs = raw.balance_sheet.get(latest, {})
        bs_prior = raw.balance_sheet.get(prior, {}) if prior else {}

        sales = pnl.get("Sales")
        net_profit = pnl.get("Net Profit")

        avg_assets = _average(bs.get("Total Assets"), bs_prior.get("Total Assets"))
        avg_equity = _average(_total_equity(bs), _total_equity(bs_prior))

        if sales and net_profit is not None and avg_assets and avg_equity:
            net_margin = (net_profit / sales) * 100
            asset_turnover = sales / avg_assets
            equity_multiplier = avg_assets / avg_equity
            roe_check = (net_margin / 100) * asset_turnover * equity_multiplier * 100

            dupont = {
                "net_margin": round(net_margin, 2),
                "asset_turnover": round(asset_turnover, 3),
                "equity_multiplier": round(equity_multiplier, 2),
                "roe_check": round(roe_check, 2),
                "period": latest,
                "averaged": prior is not None,
            }

            if roe and abs(roe - roe_check) > 5.0:
                logger.warning(
                    f"DuPont ROE check diverges from reported ROE for {raw.symbol}: "
                    f"reported={roe}, computed={roe_check:.2f} — verify parsed line items"
                )

    return flags, dupont


# ============================================================
# SCORING — leverage
# ============================================================

def _score_leverage(raw: FundamentalRaw) -> list[RatioFlag]:
    flags: list[RatioFlag] = []
    latest = _latest_common_period(raw)
    if not latest:
        return flags

    bs = raw.balance_sheet.get(latest, {})
    pnl = raw.annual_pnl.get(latest, {})

    borrowings = bs.get("Borrowings")
    total_equity = _total_equity(bs)

    if borrowings is not None and total_equity:
        flags.append(_threshold_flag(
            "Debt/Equity", borrowings / total_equity,
            THRESHOLDS["debt_equity_good"], THRESHOLDS["debt_equity_ok"],
            lower_is_better=True,
        ))

    # Interest coverage is EBIT / Interest, and Screener's "Operating Profit"
    # is EBITDA — it sits above the Depreciation line. Dividing EBITDA by
    # interest (as this used to) overstates coverage by the whole D&A charge,
    # most for exactly the capital-heavy businesses where coverage matters.
    ebitda = pnl.get("Operating Profit")
    depreciation = pnl.get("Depreciation")
    interest = pnl.get("Interest")

    if interest == 0:
        # Debt-free is the best possible answer here, not a missing one.
        flags.append(RatioFlag(
            "Interest Coverage", None, FlagColor.GREEN,
            "No interest cost in the latest year — effectively debt-free",
        ))
    elif ebitda is not None and depreciation is not None and interest:
        flags.append(_threshold_flag(
            "Interest Coverage", (ebitda - depreciation) / interest,
            THRESHOLDS["interest_coverage_good"], THRESHOLDS["interest_coverage_ok"],
        ))

    return flags


# ============================================================
# SCORING — growth
# ============================================================

def _score_growth(raw: FundamentalRaw) -> list[RatioFlag]:
    flags: list[RatioFlag] = []
    sales_growth = raw.growth.get("Compounded Sales Growth", {})
    profit_growth = raw.growth.get("Compounded Profit Growth", {})

    sales_5y = sales_growth.get("5 Years:")
    profit_5y = profit_growth.get("5 Years:")

    flags.append(_threshold_flag(
        "5yr Sales Growth %", sales_5y,
        THRESHOLDS["sales_growth_5y_good"], THRESHOLDS["sales_growth_5y_ok"],
    ))
    flags.append(_threshold_flag(
        "5yr Profit Growth %", profit_5y,
        THRESHOLDS["profit_growth_5y_good"], THRESHOLDS["profit_growth_5y_ok"],
    ))

    return flags


# ============================================================
# SCORING — valuation
# ============================================================

def _score_valuation(raw: FundamentalRaw) -> list[RatioFlag]:
    """
    NOTE — known simplification: this compares P/E only against the
    company's own reasonableness bands, not a live sector/Nifty median.
    Wiring in a real sector-median comparison needs either Screener's
    peer-comparison table (not yet parsed by the scraper) or a separate
    index P/E lookup. Flagged here rather than silently assumed away —
    treat the valuation section as the least reliable of the four until
    that's added.
    """
    flags: list[RatioFlag] = []
    pe = raw.top_ratios.get("Stock P/E")

    if pe is not None:
        good, ok = THRESHOLDS["pe_good"], THRESHOLDS["pe_ok"]
        if pe <= 0:
            flags.append(RatioFlag("P/E", pe, FlagColor.RED, "Negative or zero P/E — company reporting losses"))
        elif pe <= good:
            flags.append(RatioFlag("P/E", pe, FlagColor.GREEN))
        elif pe <= ok:
            flags.append(RatioFlag(
                "P/E", pe, FlagColor.YELLOW,
                f"P/E of {pe:.1f} is at the top of the {good:.0f}-{ok:.0f}x band the method treats as the limit",
            ))
        else:
            flags.append(RatioFlag(
                "P/E", pe, FlagColor.RED,
                f"P/E of {pe:.1f} is beyond the {good:.0f}-{ok:.0f}x band — the method says avoid at this multiple",
            ))

    return flags


# ============================================================
# FLAG HELPER
# ============================================================

def _threshold_flag(
    label: str, value: float | None, good_threshold: float, ok_threshold: float,
    lower_is_better: bool = False,
) -> RatioFlag:
    if value is None:
        return RatioFlag(label, None, FlagColor.YELLOW, f"{label} unavailable in source data")

    if lower_is_better:
        if value <= good_threshold:
            return RatioFlag(label, value, FlagColor.GREEN)
        elif value <= ok_threshold:
            return RatioFlag(label, value, FlagColor.YELLOW)
        else:
            return RatioFlag(label, value, FlagColor.RED, f"{label} of {value:.2f} exceeds comfort threshold ({ok_threshold})")
    else:
        if value >= good_threshold:
            return RatioFlag(label, value, FlagColor.GREEN)
        elif value >= ok_threshold:
            return RatioFlag(label, value, FlagColor.YELLOW)
        else:
            return RatioFlag(label, value, FlagColor.RED, f"{label} of {value:.2f} is below comfort threshold ({ok_threshold})")


# ============================================================
# VERDICT
# ============================================================

def _decide_verdict(
    profitability, leverage, growth, valuation, red_flags, green_flags, raw: FundamentalRaw
) -> tuple[Verdict, str]:
    """
    Quality verdict only — deliberately no position size.

    Investing mode is advisory: it never places an order, and how much to buy
    is the reader's call. An earlier version returned a suggested allocation
    percentage; it was removed rather than left unused, because a number the
    system cannot act on still reads as advice.

    Note this verdict is also price-blind by design. Whether the stock is worth
    buying *today* is a separate question answered against the DCF band in
    Stage 3 — a great business at a bad price is still a bad purchase.
    """
    all_flags = profitability + leverage + growth + valuation
    scored = [f for f in all_flags if f.value is not None or f.color == FlagColor.GREEN]

    if not scored:
        return Verdict.NOT_RATED, "LOW"

    green_count = sum(1 for f in scored if f.color == FlagColor.GREEN)
    red_count = sum(1 for f in scored if f.color == FlagColor.RED)
    total = len(scored)

    green_ratio = green_count / total
    red_ratio = red_count / total

    # Screener's own pros/cons weigh in too, since they capture things
    # (promoter pledging, auditor changes, related-party issues) our
    # threshold rules above don't compute directly.
    net_screener_signal = len(green_flags) - len(red_flags)

    if red_ratio >= 0.4 or net_screener_signal <= -3:
        verdict = Verdict.AVOID
    elif green_ratio >= 0.6 and red_count == 0 and net_screener_signal >= 0:
        verdict = Verdict.INVESTMENT_GRADE
    else:
        verdict = Verdict.NEUTRAL

    data_completeness = total / max(len(all_flags), 1)
    if data_completeness >= 0.8 and total >= 5:
        confidence = "HIGH"
    elif data_completeness >= 0.5:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return verdict, confidence


# ============================================================
# LLM NARRATION (explain the numbers, don't invent an opinion)
# ============================================================

async def _generate_rationale(scorecard: FundamentalScorecard, raw: FundamentalRaw) -> str:
    if not settings.LLM_REASONING_ENABLED or not (settings.OPENAI_API_KEY or settings.LOCAL_MODEL_API_KEY):
        return _template_rationale(scorecard)

    try:
        import asyncio
        from openai import OpenAI

        prompt = f"""
You are narrating a fundamental analysis scorecard that has ALREADY been computed —
do not introduce ratios, numbers, or opinions that aren't given below.

Symbol: {scorecard.symbol}
Verdict: {scorecard.verdict.value} (confidence: {scorecard.confidence})
Data as of: {scorecard.data_as_of}

DuPont breakdown of ROE: net margin {scorecard.dupont_net_margin}%, asset turnover
{scorecard.dupont_asset_turnover}x, equity multiplier {scorecard.dupont_equity_multiplier}x

Green flags: {', '.join(scorecard.green_flags) or 'none'}
Red flags: {', '.join(scorecard.red_flags) or 'none'}

Write 2-3 sentences for a long-term investor, explaining WHY the verdict follows
from these specific numbers and flags. Do not recommend a specific action beyond
what the verdict already states. Do not invent any figures not listed above.
""".strip()

        def _call():
            api_key = settings.LOCAL_MODEL_API_KEY or settings.OPENAI_API_KEY
            client = OpenAI(api_key=api_key, base_url=settings.LOCAL_MODEL_BASE_URL or None)
            response = client.chat.completions.create(
                model=settings.LOCAL_MODEL_NAME or "gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You narrate pre-computed fundamental analysis scorecards for Indian equities. You explain, you do not independently judge."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=250,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _call)

    except Exception as e:
        logger.warning(f"Fundamental rationale LLM call failed for {scorecard.symbol} (using template): {e}")
        return _template_rationale(scorecard)


def _template_rationale(scorecard: FundamentalScorecard) -> str:
    verdict_text = {
        Verdict.INVESTMENT_GRADE: "shows strong, well-rounded fundamentals",
        Verdict.NEUTRAL: "shows mixed fundamentals — some strengths, some concerns",
        Verdict.AVOID: "shows fundamental weaknesses that warrant caution",
        Verdict.NOT_RATED: "could not be scored by this model",
    }[scorecard.verdict]

    parts = [f"{scorecard.symbol} {verdict_text} as of {scorecard.data_as_of}."]
    if scorecard.green_flags:
        parts.append(f"Positives: {'; '.join(scorecard.green_flags[:2])}.")
    if scorecard.red_flags:
        parts.append(f"Concerns: {'; '.join(scorecard.red_flags[:2])}.")
    return " ".join(parts)
