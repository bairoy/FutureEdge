"""
app/agents/investing_nodes.py
================================
The Investing-mode graph nodes: data_fetch, the three stage agents, and
thesis_agent.

WHY THESE LIVE TOGETHER IN ONE FILE (for now):
------------------------------------------------
The project convention is one agent per file in app/agents/. These four are
kept together while they are still structure rather than substance — what
matters at this stage is the CONTRACT between them: which state key each one
owns, and the fact that the three stage agents are siblings rather than a
chain. Splitting them into four near-empty files would hide that. Each moves
to its own file as it is filled in (see the plan, 4C).

THE SHAPE, AND WHY:
---------------------
    data_fetch ──┬── business_agent   (Stage 1, qualitative)
                 ├── financial_agent  (Stage 2, 10-point checklist)
                 └── valuation_agent  (Stage 3, DCF)
                          │
                    thesis_agent

The obvious reading is that Stage 3 depends on Stage 2 — a DCF needs free cash
flow, and free cash flow comes from the financial statements. But both stages
read the SAME statements. So data_fetch emits the raw data plus pure derived
metrics (computation, zero judgment), and Stages 2 and 3 become siblings
interpreting shared numbers rather than parent and child.

Compute once, interpret in parallel. That is what makes the three-way fan-out
legal, and it is why data_fetch is the fan-out point instead of Stage 2.

STATE WRITES — READ THIS BEFORE ADDING A NODE:
------------------------------------------------
Every parallel node writes its OWN top-level state key. Three nodes writing
into one shared dict raises InvalidUpdateError on concurrent writes. The one
key they may all write is `missing_data`, and only because its operator.add
reducer merges concurrent appends.

WHAT THIS BRANCH NEVER DOES:
------------------------------
It never places an order. There is no execution node downstream and no
interrupt() for approval — the branch ends at thesis_agent. Investing mode is
advisory: it answers "is this an investable business, and what is it worth",
and the human executes at their broker themselves.
"""

from loguru import logger

from app.graph.state import (
    AgentState,
    BusinessReport,
    FinancialReport,
    ValuationReport,
    InvestmentThesis,
    MissingDatum,
)


# ============================================================
# DATA FETCH — the fan-out point
# ============================================================

async def data_fetch_node(state: AgentState) -> dict:
    """
    The only node in this branch that touches the network for financials.

    Everything downstream reads what this leaves in state, so a stage agent
    never triggers a fetch of its own — that is what keeps three parallel
    agents from making three requests to Screener for the same page.

    The scrape is Redis-cached for three days (see fundamentals_scraper), so a
    re-run after a manual-input correction costs compute only.
    """
    symbol = state["symbol"]
    run_id = state.get("run_id", "?")

    from app.data.fundamentals_scraper import get_fundamentals
    from dataclasses import asdict

    from app.data.fundamentals_scraper import SymbolNotFound

    try:
        raw = await get_fundamentals(symbol)
    except Exception as e:
        # An unknown ticker is the user's to fix and a failed scrape is ours.
        # The field name is what lets the API turn one into a 404 and the other
        # into an upstream error instead of reporting both as "no data".
        field = "symbol" if isinstance(e, SymbolNotFound) else "fundamentals"
        logger.error(f"Fundamentals fetch failed | symbol={symbol} | run_id={run_id} | {e}")
        return {
            "fundamentals_raw": None,
            "derived_metrics":  None,
            "missing_data":     [MissingDatum(
                stage="DATA", field=field,
                reason=(str(e) if field == "symbol"
                        else f"Could not fetch fundamentals for {symbol}: {e}"),
            )],
            "completed_nodes":  ["data_fetch"],
            "logs":             [f"data_fetch failed for {symbol}: {e}"],
        }

    raw_dict = asdict(raw)
    raw_dict["fetched_at"] = raw.fetched_at.isoformat()

    derived, missing = _derive_metrics(raw)

    logger.info(
        f"data_fetch | symbol={symbol} | schema={raw.sector_schema} | "
        f"periods={len(raw.annual_pnl)} | derived={len(derived)} | missing={len(missing)}"
    )

    return {
        "fundamentals_raw": raw_dict,
        "derived_metrics":  derived,
        "missing_data":     missing,
        "completed_nodes":  ["data_fetch"],
        "logs":             [f"Fetched fundamentals for {symbol} ({raw.sector_schema} schema)"],
    }


def _derive_metrics(raw) -> tuple[dict, list[MissingDatum]]:
    """
    Pure computation over the scraped statements — no judgment, no thresholds.

    Kept deliberately free of interpretation: the moment this decides whether a
    number is GOOD, Stages 2 and 3 stop being independent readings of the same
    data and start inheriting one opinion.
    """
    derived: dict = {}
    missing: list[MissingDatum] = []

    # Free cash flow. Screener publishes this directly, so there is no need to
    # derive CFO - capex and no risk of subtracting the whole investing-
    # activities line (which includes non-capex items) by mistake.
    fcf_series = {
        period: values["Free Cash Flow"]
        for period, values in raw.cash_flow.items()
        if "Free Cash Flow" in values
    }
    if fcf_series:
        derived["fcf_series"] = fcf_series
        recent = list(fcf_series.values())[-3:]
        derived["fcf_3yr_avg"] = sum(recent) / len(recent)
    else:
        missing.append(MissingDatum(
            stage="DATA", field="free_cash_flow",
            reason="No Free Cash Flow row in the cash flow statement",
        ))

    derived["cash_equivalents"] = raw.cash_equivalents
    derived["sector_schema"] = raw.sector_schema

    if raw.shares_outstanding:
        derived["shares_outstanding"] = raw.shares_outstanding
    else:
        missing.append(MissingDatum(
            stage="DATA", field="shares_outstanding",
            reason="Could not derive share count — Equity Capital or Face Value missing",
        ))

    # Growth rates as Screener already compounds them, kept as-is. Stage 2
    # re-checks these for one-off items and low-base distortion; that check is
    # judgment and belongs there, not here.
    derived["compounded_sales_growth"] = raw.growth.get("Compounded Sales Growth", {})
    derived["compounded_profit_growth"] = raw.growth.get("Compounded Profit Growth", {})

    return derived, missing


# ============================================================
# STAGE AGENTS — siblings, one state key each
# ============================================================
#
# All three are structure-only until 4C. They are wired in now, rather than
# added later, so the parallel-write contract and the routing are proven
# against a graph that actually runs — and so filling them in never has to
# touch the graph shape.


async def business_agent_node(state: AgentState) -> dict:
    """
    Stage 1 — the 18 qualitative questions (app/agents/business_agent.py),
    answered from documents ingested out of band into Qdrant.

    Unanswered questions become `missing_data`, so the reader sees exactly
    which ones need the annual report opened by hand — usually a sign that
    ingestion did not run for this symbol rather than that the answer is
    unavailable.
    """
    symbol = state["symbol"]
    raw_dict = state.get("fundamentals_raw")
    raw = _rehydrate_raw(raw_dict) if raw_dict else None

    from app.agents.business_agent import run_business_checklist

    try:
        result = await run_business_checklist(symbol, raw)
    except Exception as e:
        logger.warning(f"Stage 1 failed for {symbol}: {e}")
        return {
            "business_report": BusinessReport(gate="CLEAR", complete=False),
            "missing_data": [MissingDatum(
                stage="BUSINESS", field="qualitative_checklist",
                reason=f"Stage 1 could not run: {e}",
            )],
            "completed_nodes": ["business_agent"],
            "logs": [f"business_agent failed for {symbol}: {e}"],
        }

    # Only genuinely unanswered questions. A question the web answered is not
    # missing data, and listing it would send the reader off to type in a figure
    # the system already has.
    missing = [
        MissingDatum(
            stage="BUSINESS", field=f"q{a.n}",
            reason=(f"{a.question} — {a.status.replace('_', ' ').lower()}"
                    + (" (searched the web too)" if a.source == "WEB" else "")),
        )
        for a in result.answers
        if a.status in ("NOT_FOUND", "NEEDS_EXTERNAL")
    ]

    report = BusinessReport(
        answers=[
            {"n": a.n, "question": a.question, "kind": a.kind, "status": a.status,
             "answer": a.answer, "citations": a.citations, "is_opinion": a.is_opinion,
             # Which tier answered it, and the URLs when that tier was the web.
             # Stored per answer so a reader can always tell an audited filing
             # from a page a model found.
             "source": a.source, "sources": a.sources}
            for a in result.answers
        ],
        red_flags=result.red_flags,
        gate=result.gate,
        complete=result.completeness >= 1.0,
    )

    return {
        "business_report": report,
        "missing_data":    missing,
        "completed_nodes": ["business_agent"],
        "logs": [
            f"Stage 1 for {symbol}: answered {result.answered}/18 "
            f"({sum(1 for a in result.answers if a.source == 'WEB' and a.status == 'ANSWERED')} "
            f"from web search), {len(result.red_flags)} red flag(s), gate {result.gate}"
        ],
    }


async def financial_agent_node(state: AgentState) -> dict:
    """
    Stage 2 — the 10-point financial checklist (app/agents/financial_agent.py).

    Rebuilds FundamentalRaw from the dict data_fetch left in state rather than
    re-fetching: three sibling nodes each calling the scraper would be three
    requests to Screener for one page.

    Checks that cannot be computed become `missing_data` entries, which is how
    the reader learns exactly which annual-report figures to supply and how the
    completeness gate on the final verdict gets its number.
    """
    symbol = state["symbol"]
    raw_dict = state.get("fundamentals_raw")

    if not raw_dict:
        return {
            "financial_report": FinancialReport(completeness=0.0, complete=False),
            "missing_data": [MissingDatum(
                stage="FINANCIAL", field="ten_point_checklist",
                reason="No fundamentals available — data_fetch did not produce any.",
            )],
            "completed_nodes": ["financial_agent"],
            "logs": [f"financial_agent: no data for {symbol}"],
        }

    from app.agents.financial_agent import run_checklist, narrate_checklist, CheckStatus

    raw = _rehydrate_raw(raw_dict)
    result = run_checklist(raw, state.get("derived_metrics") or {})
    result.summary = await narrate_checklist(symbol, result)

    # Checks 9 and 10 are deliberately open here — Stage 1 answers those same
    # questions, and thesis_agent reconciles them once both reports exist. It
    # records them as missing only if Stage 1 came up empty too; reporting them
    # from here would send the reader off to type in a figure the run already has.
    missing = [
        MissingDatum(stage="FINANCIAL", field=_field_key(c.name), reason=c.detail)
        for c in result.checks
        if c.status is CheckStatus.NOT_COMPUTABLE and c.n not in _RECONCILED_CHECKS
    ]

    report = FinancialReport(
        checks=[
            {"n": c.n, "name": c.name, "status": c.status.value,
             "value": c.value, "detail": c.detail, "source": c.source}
            for c in result.checks
        ],
        completeness=result.completeness,
        complete=result.completeness >= 1.0,
    )
    # Calculation cautions ride along as checks so they surface in the UI
    # beside the results they qualify, rather than in a separate panel nobody
    # reads. They carry no pass/fail of their own.
    for i, warning in enumerate(result.warnings, start=1):
        report.checks.append({
            "n": 100 + i, "name": "Calculation caution", "status": "FLAG",
            "value": None, "detail": warning, "source": "derived",
        })

    return {
        "financial_report": report,
        "missing_data":     missing,
        "completed_nodes":  ["financial_agent"],
        "logs":             [f"Stage 2 for {symbol}: {result.summary}"],
    }


# Stage 2 checks that Stage 1 answers instead — see financial_agent's
# _RECONCILED_AT_FAN_IN and thesis_agent_node below.
_RECONCILED_CHECKS = {9, 10}


def _field_key(check_name: str) -> str:
    """Check name -> snake_case field key, so a missing_data entry lines up
    with the field name the manual-input table is keyed on."""
    return check_name.lower().replace(" ", "_").replace("/", "_")


def _rehydrate_raw(raw_dict: dict):
    """
    Rebuild the FundamentalRaw dataclass from the plain dict held in state.

    Returns a FundamentalRaw. Imported inside the function rather than at
    module scope to keep the scraper (and its Redis client) off the import
    path of the graph builder.

    State has to stay JSON-serialisable for the LangGraph checkpointer, so
    data_fetch stores a dict. Unknown keys are dropped so a checkpoint written
    by an older build still loads.
    """
    from dataclasses import fields as dc_fields
    from datetime import datetime as _dt
    from app.data.fundamentals_scraper import FundamentalRaw

    known = {f.name for f in dc_fields(FundamentalRaw)}
    payload = {k: v for k, v in raw_dict.items() if k in known}
    if isinstance(payload.get("fetched_at"), str):
        payload["fetched_at"] = _dt.fromisoformat(payload["fetched_at"])
    return FundamentalRaw(**payload)


async def valuation_agent_node(state: AgentState) -> dict:
    """
    Stage 3 — intrinsic value by DCF (app/agents/valuation_agent.py).

    Reads the statements data_fetch already put in state; the only network
    call it makes of its own is the beta lookup, which is cached for a week.
    """
    symbol = state["symbol"]
    raw_dict = state.get("fundamentals_raw")

    if not raw_dict:
        return {
            "valuation_report": ValuationReport(complete=False),
            "missing_data": [MissingDatum(
                stage="VALUATION", field="dcf",
                reason="No fundamentals available — data_fetch did not produce any.",
            )],
            "completed_nodes": ["valuation_agent"],
            "logs": [f"valuation_agent: no data for {symbol}"],
        }

    from app.agents.valuation_agent import run_dcf

    raw = _rehydrate_raw(raw_dict)
    dcf = await run_dcf(raw, state.get("derived_metrics") or {})

    if not dcf.complete:
        return {
            "valuation_report": ValuationReport(complete=False),
            "missing_data": [MissingDatum(
                stage="VALUATION", field="dcf",
                reason=dcf.not_computable_reason or "DCF could not be computed",
            )],
            "completed_nodes": ["valuation_agent"],
            "logs": [f"Stage 3 for {symbol}: not computable — {dcf.not_computable_reason}"],
        }

    report = ValuationReport(
        intrinsic=dcf.intrinsic,
        upper_band=dcf.upper_band,
        lower_band=dcf.lower_band,
        mos_buy_price=dcf.mos_buy_price,
        assumptions=dcf.assumptions,
        sensitivity=dcf.sensitivity,
        reverse_dcf_implied_fcf=dcf.reverse_dcf_implied_fcf,
        complete=True,
    )
    # The cautions travel with the numbers they qualify. A DCF read without
    # them is exactly the false precision the band exists to prevent.
    report.assumptions["warnings"] = dcf.warnings
    report.assumptions["terminal_share_of_value"] = dcf.terminal_share_of_value
    report.assumptions["price_vs_band"] = dcf.price_vs_band
    report.assumptions["current_price"] = dcf.current_price
    report.assumptions["reverse_dcf_note"] = dcf.reverse_dcf_note
    report.assumptions["beta_note"] = dcf.beta_note

    return {
        "valuation_report": report,
        "completed_nodes":  ["valuation_agent"],
        "logs":             [
            f"Stage 3 for {symbol}: intrinsic ₹{dcf.intrinsic:,.0f} "
            f"(band ₹{dcf.lower_band:,.0f}-{dcf.upper_band:,.0f}, MoS ₹{dcf.mos_buy_price:,.0f}) "
            f"— {dcf.price_vs_band}"
        ],
    }


# ============================================================
# THESIS — assemble, and refuse when the inputs do not support a verdict
# ============================================================

async def thesis_agent_node(state: AgentState) -> dict:
    """
    Terminal node of the Investing branch.

    Stores the quality verdict and (once Stage 3 lands) the valuation band. It
    does NOT store a stance — BUY/HOLD/WATCH depends on the live price and on
    whether the position is held, so it is computed on read. Persisting it
    would mean re-running this whole pipeline every time the price moved.
    """
    symbol = state["symbol"]
    financial = state.get("financial_report")
    business = state.get("business_report")
    missing = state.get("missing_data") or []

    # Fill the Stage 2 checks that Stage 1 actually answered. This is the first
    # node that can see both reports, which is the whole reason it happens here
    # rather than in financial_agent.
    financial, newly_missing = _reconcile_stage1_checks(business, financial)

    completeness = financial.completeness if financial else 0.0

    # Completeness gates the verb. A verdict issued off a handful of computed
    # checks is worse than no verdict — research desks carry Not Rated for
    # exactly this. And a Tier-1 red flag blocks regardless of completeness.
    if business is not None and business.gate == "BLOCK":
        grade, reason = "NOT_RATED", "STAGE1_RED_FLAG"
    elif completeness < 0.7:
        grade, reason = "NOT_RATED", "INSUFFICIENT_DATA"
    else:
        grade, reason = "WATCHLIST", None

    thesis = InvestmentThesis(
        symbol=symbol,
        quality_grade=grade,
        not_rated_reason=reason,
        completeness=completeness,
        conviction=0.0,
        red_flags=business.red_flags if business else [],
        narrative=(
            f"{symbol}: {len(missing)} input(s) still missing; "
            f"Stage 2 completeness {completeness:.0%}."
        ),
        data_as_of=(state.get("fundamentals_raw") or {}).get("fetched_at"),
    )

    logger.info(
        f"thesis_agent | symbol={symbol} | grade={grade} | reason={reason} | "
        f"completeness={completeness:.0%} | missing={len(missing)}"
    )

    return {
        "investment_thesis": thesis,
        # Rewritten with checks 9 and 10 filled. Safe to write a key another
        # node owns: thesis_agent runs AFTER the fan-in, so this is sequential,
        # not the concurrent write that raises InvalidUpdateError.
        "financial_report":  financial,
        "missing_data":      newly_missing,
        "completed_nodes":   ["thesis_agent"],
        "logs":              [f"Thesis for {symbol}: {grade}" + (f" ({reason})" if reason else "")],
    }


# Stage 2 check number -> the Stage 1 question that answers it.
_CHECK_TO_QUESTION = {
    9:  (12, "Business diversity",
         "prefer 1-2 related business lines; flag expansion into unrelated areas"),
    10: (18, "Subsidiaries",
         "too many, or any with unclear purpose, can be a related-party vehicle"),
}


def _reconcile_stage1_checks(business, financial):
    """
    Fill Stage 2's checks 9 and 10 from Stage 1's Q12 and Q18.

    WHY THIS IS NOT DOUBLE-COUNTING:
    ----------------------------------
    They are the same question asked twice by the source method — once in the
    qualitative checklist and once in the financial one. Stage 1 already
    retrieves the annual report and searches the web to answer them. Having
    Stage 2 answer them again would cost a second retrieval per company and
    could produce a second answer contradicting the first.

    WHAT IT DOES NOT DO:
    ----------------------
    It does not grade. Stage 1's answer is prose, and turning "three segments,
    86.8% / 9.4% / 3.8%" into PASS or FAIL is a judgement this node has no
    basis to make — so both are recorded as FLAG: computed, with something a
    human should read. That keeps them out of the pass count while still
    counting toward completeness, which is exactly what they are: answered, not
    scored.

    Returns the updated report plus any MissingDatum for questions Stage 1 could
    not answer either — which is the only case where these are genuinely missing.
    """
    from app.graph.state import FinancialReport

    if financial is None:
        return financial, []

    answers = {a["n"]: a for a in (business.answers if business else [])}
    checks = [dict(c) for c in financial.checks]
    still_missing: list[MissingDatum] = []
    filled = 0

    for check in checks:
        mapping = _CHECK_TO_QUESTION.get(check.get("n"))
        if mapping is None or check.get("status") != "NOT_COMPUTABLE":
            continue

        question_no, name, bar = mapping
        answer = answers.get(question_no)

        if answer and answer.get("status") == "ANSWERED" and answer.get("answer"):
            check["status"] = "FLAG"
            check["detail"] = f"From Stage 1 Q{question_no} — {bar}. {answer['answer']}"
            check["source"] = f"stage1:q{question_no}:{(answer.get('source') or 'DOCUMENTS').lower()}"
            filled += 1
        else:
            still_missing.append(MissingDatum(
                stage="FINANCIAL", field=_field_key(name),
                reason=(f"Stage 1 Q{question_no} could not answer this either — "
                        f"it needs the annual report read by hand."),
            ))

    if not filled and not still_missing:
        return financial, []

    scored = [c for c in checks if c.get("n", 0) < 100]
    updated = FinancialReport(
        checks=checks,
        completeness=(sum(1 for c in scored if c["status"] != "NOT_COMPUTABLE") / len(scored)
                      if scored else 0.0),
        complete=all(c["status"] != "NOT_COMPUTABLE" for c in scored),
    )
    logger.info(
        f"Reconciled Stage 2 checks from Stage 1 | filled={filled} | "
        f"still missing={len(still_missing)} | completeness={updated.completeness:.0%}"
    )
    return updated, still_missing
