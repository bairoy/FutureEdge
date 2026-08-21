"""
tests/graph/test_investing_branch.py
======================================
Tests for the Investing-mode graph branch (plan §4B).

The properties pinned here are structural, and each one is cheap to break by
editing the graph later:

1. Mode routing defaults to TRADING for anything missing or unrecognised
   -> existing callers and pre-existing checkpoints must keep working
2. The Investing branch runs data_fetch -> 3 siblings -> thesis, and never
   reaches human_review or execution
3. Three parallel nodes can all append to `missing_data` without raising
   InvalidUpdateError (the operator.add reducer contract)
4. execution_node REFUSES an INVESTING-mode state
   -> required by CLAUDE.md: this is a money-moving path, so the guard on it
      needs a test. It is what makes "advisory only" a property of the system
      rather than of the current graph topology.
5. The guard fires before state["consensus"] is read (the investing branch
   never populates it, so reading first would KeyError instead of refusing)
"""

import sys
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.graph.builder import create_graph, route_by_analysis_mode
from app.graph.state import MarketContext, PortfolioSnapshot
from app.data.fundamentals_scraper import FundamentalRaw
from app.data.market_risk import BetaEstimate


@pytest.fixture(autouse=True)
def _no_network_beta():
    """
    Stage 3 fetches beta from yfinance. These tests are about graph wiring, not
    about beta, and a live download here makes the suite slow and flaky.
    """
    with patch("app.agents.valuation_agent.get_beta",
               AsyncMock(return_value=BetaEstimate(
                   symbol="TESTCO", beta=1.0, estimates={"2y_weekly": 1.0},
                   disagreement=0.0, note="test"))):
        yield


def _fake_raw() -> FundamentalRaw:
    return FundamentalRaw(
        symbol="TESTCO",
        fetched_at=datetime.now(timezone.utc),
        consolidated=True,
        top_ratios={"Stock P/E": 20.0, "Face Value": 10.0},
        annual_pnl={"Mar 2026": {"Sales": 1000.0, "Operating Profit": 200.0}},
        balance_sheet={"Mar 2026": {"Equity Capital": 100.0, "Reserves": 900.0}},
        cash_flow={"Mar 2024": {"Free Cash Flow": 90.0},
                   "Mar 2025": {"Free Cash Flow": 100.0},
                   "Mar 2026": {"Free Cash Flow": 110.0}},
        sector_schema="GENERAL",
        shares_outstanding=1e8,
    )


def _investing_state(symbol="TESTCO") -> dict:
    return {
        "analysis_mode": "INVESTING",
        "symbol": symbol,
        "market_context": MarketContext(symbol=symbol, current_price=0.0),
        "portfolio": PortfolioSnapshot(
            total_equity=0.0, margin_used=0.0, margin_available=0.0, unrealized_pnl=0.0
        ),
        "run_id": "test1234",
        "user_id": "tester",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hitl_required": False,
        "hitl_status": "NOT_APPLICABLE",
        "missing_data": [],
        "logs": [],
        "completed_nodes": [],
    }


# ============================================================
# 1 — routing
# ============================================================

@pytest.mark.parametrize("state,expected", [
    ({}, "TRADING"),                                  # no analysis_mode at all
    ({"analysis_mode": None}, "TRADING"),
    ({"analysis_mode": "TRADING"}, "TRADING"),
    ({"analysis_mode": "INVESTING"}, "INVESTING"),
    ({"analysis_mode": "investing"}, "INVESTING"),    # case-insensitive
    ({"analysis_mode": "nonsense"}, "TRADING"),       # unknown -> safe default
])
def test_route_by_analysis_mode(state, expected):
    """
    Anything unrecognised must fall to TRADING, not raise and not silently
    pick INVESTING. Callers written before this field existed pass no mode.
    """
    assert route_by_analysis_mode(state) == expected


# ============================================================
# 2 + 3 — the branch runs, and parallel writes merge
# ============================================================

@pytest.mark.asyncio
async def test_investing_branch_runs_all_nodes_and_stops_at_thesis():
    graph = create_graph().compile()
    with patch("app.data.fundamentals_scraper.get_fundamentals",
               AsyncMock(return_value=_fake_raw())):
        out = await graph.ainvoke(_investing_state())

    assert out["completed_nodes"] == [
        "data_fetch", "business_agent", "financial_agent", "valuation_agent", "thesis_agent",
    ]
    assert out["investment_thesis"] is not None


@pytest.mark.asyncio
async def test_investing_branch_never_reaches_execution_or_human_review():
    """
    The branch has no edge to either. If someone later wires the investing
    tail into the shared trading tail, this fails.
    """
    graph = create_graph().compile()
    with patch("app.data.fundamentals_scraper.get_fundamentals",
               AsyncMock(return_value=_fake_raw())):
        out = await graph.ainvoke(_investing_state())

    assert "execution" not in out["completed_nodes"]
    assert "human_review" not in out["completed_nodes"]
    assert out.get("executed_trade") is None


@pytest.mark.asyncio
async def test_parallel_stages_append_to_missing_data_concurrently():
    """
    The reducer contract. Stage agents write `missing_data` in the SAME
    superstep — without operator.add on that key, LangGraph raises
    InvalidUpdateError on the concurrent write instead of merging.

    Asserted on distinct stages rather than a fixed count: which stages report
    something missing changes as they are implemented, but two different nodes
    landing entries in one superstep is the property that must hold.
    """
    graph = create_graph().compile()
    with patch("app.data.fundamentals_scraper.get_fundamentals",
               AsyncMock(return_value=_fake_raw())):
        out = await graph.ainvoke(_investing_state())

    stages = {m.stage for m in out["missing_data"]}
    assert {"BUSINESS", "FINANCIAL"} <= stages
    assert len(out["missing_data"]) > 1


@pytest.mark.asyncio
async def test_valuation_stage_produces_a_band():
    """Stage 3 is wired in and runs on what data_fetch left behind."""
    graph = create_graph().compile()
    with patch("app.data.fundamentals_scraper.get_fundamentals",
               AsyncMock(return_value=_fake_raw())):
        out = await graph.ainvoke(_investing_state())

    report = out["valuation_report"]
    assert report.complete is True
    assert report.mos_buy_price < report.lower_band < report.intrinsic < report.upper_band


@pytest.mark.asyncio
async def test_data_fetch_derives_shared_metrics_once():
    """
    data_fetch is the fan-out point: Stages 2 and 3 read the same derived
    numbers rather than each deriving their own, which is what lets them be
    siblings instead of a chain.
    """
    graph = create_graph().compile()
    with patch("app.data.fundamentals_scraper.get_fundamentals",
               AsyncMock(return_value=_fake_raw())):
        out = await graph.ainvoke(_investing_state())

    derived = out["derived_metrics"]
    assert derived["fcf_3yr_avg"] == pytest.approx(100.0)   # (90 + 100 + 110) / 3
    assert derived["shares_outstanding"] == pytest.approx(1e8)


@pytest.mark.asyncio
async def test_incomplete_stages_produce_not_rated():
    """Completeness gates the verb — no verdict off stub stages."""
    graph = create_graph().compile()
    with patch("app.data.fundamentals_scraper.get_fundamentals",
               AsyncMock(return_value=_fake_raw())):
        out = await graph.ainvoke(_investing_state())

    thesis = out["investment_thesis"]
    assert thesis.quality_grade == "NOT_RATED"
    assert thesis.not_rated_reason == "INSUFFICIENT_DATA"


# ============================================================
# 4 + 5 — the execution guard (money-moving path)
# ============================================================

@pytest.mark.asyncio
async def test_execution_node_refuses_investing_mode():
    """
    Defence in depth. The graph gives the investing branch no edge to
    execution, so reaching here in INVESTING mode means the graph was
    rewired — not that a trade was approved.
    """
    from app.agents.execution_agent import execution_node

    result = await execution_node({
        "analysis_mode": "INVESTING",
        "run_id": "test1234",
        "user_id": "tester",
        "consensus": None,
    })

    assert result["execution_error"] == "INVESTING_MODE_NO_EXECUTION"
    assert result["executed_trade"] is None


@pytest.mark.asyncio
async def test_execution_guard_fires_before_consensus_is_read():
    """
    The investing branch never populates `consensus`. If the guard ran after
    `state["consensus"]`, this would raise KeyError instead of refusing —
    an unhandled exception on the order path rather than a clean block.
    """
    from app.agents.execution_agent import execution_node

    result = await execution_node({
        "analysis_mode": "INVESTING",
        "run_id": "test1234",
    })   # no "consensus" key at all

    assert result["execution_error"] == "INVESTING_MODE_NO_EXECUTION"


@pytest.mark.asyncio
async def test_execution_guard_does_not_block_trading_mode():
    """Guard against the mode check swallowing normal trades."""
    from app.agents.execution_agent import execution_node

    with patch("app.services.kill_switch_service.is_trading_halted",
               AsyncMock(return_value=True)):
        result = await execution_node({
            "analysis_mode": "TRADING",
            "run_id": "test1234",
            "user_id": "tester",
            "consensus": None,
        })

    # Blocked by the kill switch, NOT by the mode guard — proving a TRADING
    # state gets past layer -1 and into the real safety layers.
    assert result["execution_error"] != "INVESTING_MODE_NO_EXECUTION"


# ============================================================
# Fan-in reconciliation
#
# Stage 2's checks 9 and 10 are the same questions as Stage 1's Q12 and Q18.
# Stage 2 runs in PARALLEL with Stage 1 and cannot see its answers, so the two
# were reporting "not published in Screener's tables" while the very same run
# held the segment mix and the subsidiary list. thesis_agent is the first node
# that sees both.
# ============================================================

from app.agents.investing_nodes import _reconcile_stage1_checks   # noqa: E402
from app.graph.state import BusinessReport, FinancialReport        # noqa: E402


def _financial_with_open_checks():
    return FinancialReport(
        checks=[
            {"n": 1, "name": "Gross Profit Margin", "status": "PASS",
             "value": 50.7, "detail": "", "source": "screener"},
            {"n": 9, "name": "Business diversity", "status": "NOT_COMPUTABLE",
             "value": None, "detail": "answered by Stage 1 — reconciled at fan-in", "source": "stage1"},
            {"n": 10, "name": "Subsidiaries", "status": "NOT_COMPUTABLE",
             "value": None, "detail": "answered by Stage 1 — reconciled at fan-in", "source": "stage1"},
        ],
        completeness=1 / 3,
    )


def _business_answering(*question_numbers):
    return BusinessReport(answers=[
        {"n": n, "status": "ANSWERED", "source": "WEB", "answer": f"answer to Q{n}"}
        for n in question_numbers
    ])


def test_stage1_answers_fill_the_open_stage2_checks():
    updated, missing = _reconcile_stage1_checks(_business_answering(12, 18), _financial_with_open_checks())

    by_n = {c["n"]: c for c in updated.checks}
    assert by_n[9]["status"] == "FLAG"
    assert by_n[10]["status"] == "FLAG"
    assert "answer to Q12" in by_n[9]["detail"]
    assert missing == []


def test_reconciliation_records_where_the_answer_came_from():
    """A reader must be able to tell a filing-sourced answer from a web-sourced
    one after it has been folded into a financial check."""
    updated, _ = _reconcile_stage1_checks(_business_answering(12, 18), _financial_with_open_checks())
    assert {c["source"] for c in updated.checks if c["n"] in (9, 10)} == {
        "stage1:q12:web", "stage1:q18:web"
    }


def test_reconciliation_raises_completeness():
    updated, _ = _reconcile_stage1_checks(_business_answering(12, 18), _financial_with_open_checks())
    assert updated.completeness == 1.0
    assert updated.complete is True


def test_reconciled_checks_are_flagged_not_passed():
    """
    Stage 1's answer is prose. Turning "three segments, 86.8/9.4/3.8" into PASS
    or FAIL is a judgement this node has no basis to make, so both are FLAG:
    answered, and needing a human to read them — counted toward completeness,
    kept out of the pass tally.
    """
    updated, _ = _reconcile_stage1_checks(_business_answering(12, 18), _financial_with_open_checks())
    assert not any(c["status"] == "PASS" for c in updated.checks if c["n"] in (9, 10))


def test_unanswered_stage1_questions_become_missing_data():
    """
    The only case where these are genuinely missing. Filling them silently when
    Stage 1 also failed would be exactly the silent drop the whole missing-data
    mechanism exists to prevent.
    """
    _, missing = _reconcile_stage1_checks(_business_answering(), _financial_with_open_checks())
    assert {m.field for m in missing} == {"business_diversity", "subsidiaries"}
    assert all(m.stage == "FINANCIAL" for m in missing)


def test_a_stage1_non_answer_does_not_fill_a_check():
    """Status must be ANSWERED. A retrieved-but-unanswered Q12 is not an answer."""
    business = BusinessReport(answers=[
        {"n": 12, "status": "NOT_FOUND", "source": "WEB", "answer": "NOT ANSWERED: nothing found."},
    ])
    updated, missing = _reconcile_stage1_checks(business, _financial_with_open_checks())
    assert {c["n"]: c["status"] for c in updated.checks}[9] == "NOT_COMPUTABLE"
    assert "business_diversity" in {m.field for m in missing}


def test_already_computed_checks_are_left_alone():
    """Reconciliation fills gaps; it never overwrites a computed result."""
    updated, _ = _reconcile_stage1_checks(_business_answering(12, 18), _financial_with_open_checks())
    check1 = next(c for c in updated.checks if c["n"] == 1)
    assert check1["status"] == "PASS" and check1["value"] == 50.7


# ============================================================
# Unknown symbol vs failed scrape
#
# The API turns one into a 404 ("check the ticker") and the other into a 502
# ("our scrape broke"), and it tells them apart by the MissingDatum field name.
# That makes the field name a contract, not a label — renaming it silently
# turns every typo into an upstream error.
# ============================================================

@pytest.mark.asyncio
async def test_an_unknown_symbol_is_marked_as_a_symbol_problem():
    from app.agents.investing_nodes import data_fetch_node
    from app.data.fundamentals_scraper import SymbolNotFound

    with patch("app.data.fundamentals_scraper.get_fundamentals",
               AsyncMock(side_effect=SymbolNotFound("Screener has no page for 'KPITECH'."))):
        out = await data_fetch_node({"symbol": "KPITECH", "run_id": "t"})

    assert out["fundamentals_raw"] is None
    assert [m.field for m in out["missing_data"]] == ["symbol"]
    # The scraper's own message reaches the user unchanged — it names the fix.
    assert "KPITECH" in out["missing_data"][0].reason


@pytest.mark.asyncio
async def test_a_failed_scrape_is_not_reported_as_an_unknown_symbol():
    """A transient failure is ours, not the user's. Telling them the ticker is
    wrong when it is not sends them off to fix something that works."""
    from app.agents.investing_nodes import data_fetch_node

    with patch("app.data.fundamentals_scraper.get_fundamentals",
               AsyncMock(side_effect=RuntimeError("connection reset"))):
        out = await data_fetch_node({"symbol": "RELIANCE", "run_id": "t"})

    assert [m.field for m in out["missing_data"]] == ["fundamentals"]
