"""
tests/api/test_hitl_resume_race.py
====================================
Exactly one resume may execute a given HITL-pending trade (Plan, Phase 2).

THE RACE:
----------
resume_workflow used to read the run, check `status != "HITL_PENDING"`, then —
much later — write `status = "COMPLETED"`. The trade is executed by
`graph.ainvoke()` *between* those two points, and that call is slow: it runs
the execution agent and a broker round trip.

So two resumes arriving inside that window (a double-clicked Approve, a retried
request, two risk managers acting at once) both read HITL_PENDING, both passed
the check, and both placed the order. Two real orders, one intended trade.

THE FIX UNDER TEST:
--------------------
A conditional UPDATE claims the row (HITL_PENDING → HITL_RESOLVING) and commits
*before* the graph runs. Only the request whose UPDATE matched a row proceeds;
the loser gets a clean 400. The database arbitrates, and no lock is held across
the broker call.

These tests drive resume_workflow directly with a fake AsyncSession, because
the point of interest is the ordering of claim-vs-execute, not SQLAlchemy.
"""

import sys
import os
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

from app.api.routes import workflow_router


class FakeRun:
    """Stand-in for the WorkflowRun ORM row."""

    def __init__(self):
        self.run_id = "abc12345"
        self.status = "HITL_PENDING"
        self.hitl_required = True
        self.hitl_reviewed_by = None
        self.hitl_decided_at = None
        self.completed_at = None
        self.completed_nodes = None
        self.error_message = None


class FakeSession:
    """
    Minimal AsyncSession that models the one behaviour under test: the
    conditional UPDATE matches a row only while the run is still HITL_PENDING.

    A single shared instance is used by both concurrent callers so they contend
    over the same state, exactly as two requests would over one DB row.
    """

    def __init__(self, run: FakeRun):
        self.run = run
        self.claim_attempts = 0

    async def execute(self, stmt):
        result = MagicMock()

        # A SELECT returns the row; an UPDATE reports how many rows it matched.
        if stmt.__visit_name__ == "update":
            self.claim_attempts += 1
            if self.run.status == "HITL_PENDING":
                # Apply every SET value, as a real UPDATE would — the claim
                # also stamps the reviewer, and a fake that dropped those
                # columns would let a regression there pass unnoticed.
                # WHERE-clause params are suffixed (status_1, run_id_1) so
                # they do not collide with the SET names.
                for column, value in stmt.compile().params.items():
                    if hasattr(self.run, column):
                        setattr(self.run, column, value)
                result.rowcount = 1
            else:
                result.rowcount = 0
        else:
            result.scalar_one_or_none.return_value = self.run

        return result

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


def _request(decision="APPROVE"):
    req = MagicMock()
    req.thread_id = "abc12345"
    req.decision = decision
    req.notes = None
    req.quantity = None
    req.position_rupees = None
    return req


def _user():
    user = MagicMock()
    user.id = "user-1"
    user.email = "risk@futureedge.test"
    user.role = "risk_manager"
    return user


def _graph_that_executes(execution_counter, delay=0.05):
    """
    A graph whose ainvoke is slow — this is the window the race lived in.
    Each call counts as one trade execution.
    """
    async def _ainvoke(*_args, **_kwargs):
        execution_counter.append(1)
        await asyncio.sleep(delay)
        return {
            "completed_nodes": ["execution"],
            "execution_error": None,
            "hitl_status": "APPROVED",
            "executed_trade": {"symbol": "RELIANCE"},
            "logs": [],
        }

    graph = MagicMock()
    graph.ainvoke = _ainvoke
    return graph


# ============================================================
# THE PLAN'S ACCEPTANCE CRITERION
# ============================================================

@pytest.mark.asyncio
async def test_two_concurrent_resumes_execute_exactly_once():
    """
    Plan, Phase 2: "firing two concurrent POST /workflow/resume requests for
    the same thread_id results in exactly one execution, and the second gets a
    clean 400, not a duplicate order."
    """
    run = FakeRun()
    session = FakeSession(run)
    executions: list[int] = []

    with patch.object(
        workflow_router, "get_workflow_graph",
        return_value=_graph_that_executes(executions),
    ):
        results = await asyncio.gather(
            workflow_router.resume_workflow(_request(), _user(), session),
            workflow_router.resume_workflow(_request(), _user(), session),
            return_exceptions=True,
        )

    assert len(executions) == 1, (
        f"the trade executed {len(executions)} times — concurrent resumes "
        f"placed duplicate orders"
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, HTTPException)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 400
    assert "not pending HITL review" in failures[0].detail


@pytest.mark.asyncio
async def test_claim_happens_before_the_graph_runs():
    """
    The ordering IS the fix. If the claim were written after ainvoke, the race
    window would still be open even though a single-threaded test passes.
    """
    run = FakeRun()
    session = FakeSession(run)
    status_when_graph_ran = {}

    async def _ainvoke(*_args, **_kwargs):
        status_when_graph_ran["status"] = run.status
        return {"completed_nodes": [], "execution_error": None, "hitl_status": "APPROVED"}

    graph = MagicMock()
    graph.ainvoke = _ainvoke

    with patch.object(workflow_router, "get_workflow_graph", return_value=graph):
        await workflow_router.resume_workflow(_request(), _user(), session)

    assert status_when_graph_ran["status"] == "HITL_RESOLVING", (
        "the run must already be claimed before the trade executes"
    )
    assert run.status == "COMPLETED"


# ============================================================
# SEQUENTIAL / STATE GUARDS
# ============================================================

@pytest.mark.asyncio
async def test_second_resume_after_completion_is_rejected():
    """A retried request minutes later must not re-execute."""
    run = FakeRun()
    session = FakeSession(run)
    executions: list[int] = []

    with patch.object(
        workflow_router, "get_workflow_graph",
        return_value=_graph_that_executes(executions, delay=0),
    ):
        await workflow_router.resume_workflow(_request(), _user(), session)

        with pytest.raises(HTTPException) as exc:
            await workflow_router.resume_workflow(_request(), _user(), session)

    assert exc.value.status_code == 400
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_graph_failure_marks_run_failed_not_pending():
    """
    On failure the run must NOT return to HITL_PENDING. The exception may have
    been raised after the broker order was placed, so making it resumable again
    risks the duplicate execution this whole fix exists to prevent.
    """
    run = FakeRun()
    session = FakeSession(run)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=Exception("checkpointer unavailable"))

    with patch.object(workflow_router, "get_workflow_graph", return_value=graph):
        with pytest.raises(HTTPException) as exc:
            await workflow_router.resume_workflow(_request(), _user(), session)

    assert exc.value.status_code == 500
    assert run.status == "FAILED", "a failed resume must not be silently retryable"
    assert run.status != "HITL_PENDING"


@pytest.mark.asyncio
async def test_reviewer_is_recorded_at_claim_time():
    """
    hitl_reviewed_by is stamped by the claim, so the audit trail survives even
    if the graph run later crashes.
    """
    run = FakeRun()
    session = FakeSession(run)

    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=Exception("boom"))

    with patch.object(workflow_router, "get_workflow_graph", return_value=graph):
        with pytest.raises(HTTPException):
            await workflow_router.resume_workflow(_request(), _user(), session)

    assert run.hitl_reviewed_by == "user-1"
    assert run.hitl_decided_at is not None


@pytest.mark.asyncio
async def test_invalid_decision_never_claims_the_run():
    """A malformed decision must leave the run resumable."""
    run = FakeRun()
    session = FakeSession(run)

    with pytest.raises(HTTPException) as exc:
        await workflow_router.resume_workflow(_request(decision="MAYBE"), _user(), session)

    assert exc.value.status_code == 422
    assert run.status == "HITL_PENDING"
    assert session.claim_attempts == 0
