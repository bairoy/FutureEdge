"""
app/api/routes/investing_router.py
=====================================
Investing-mode API — fundamental analysis surfaces.

THIS ROUTER PLACES NO ORDERS, AND HAS NO PATH THAT COULD:
-----------------------------------------------------------
Investing mode is advisory. It answers "is this an investable business, and
what is it worth", and the human executes at their broker themselves. The graph
branch it triggers ends at thesis_agent with no edge to execution, and
execution_node refuses INVESTING mode outright as a second layer.

WHY NOT `POST /workflow/resume` FOR MANUAL DATA:
--------------------------------------------------
The tempting design pauses the workflow when a figure is missing and resumes on
user input. But resume_workflow is one of two documented money-moving paths
with a race-condition history, gated behind require_risk_manager. Routing "the
user typed a gross margin" through it drags a research feature into the most
safety-critical code in the repo.

Instead: analysis always completes, missing figures are reported, the user
supplies them through /manual-input, and a fresh analysis is triggered. Cheap,
because the scrape is Redis-cached — and idempotent, with no checkpoint resume.

ROLES:
--------
    GET  /investing/{symbol}/thesis     -> require_viewer
    POST /investing/{symbol}/analyze    -> require_trader
    POST /investing/manual-input        -> require_trader   (no money at stake:
    GET/POST/DELETE /investing/holdings -> require_trader    NOT risk_manager)
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_db, require_viewer, require_trader
from app.core.config import settings
from app.db.models.user import User
from app.db.models.fundamental_scorecard import FundamentalScorecard
from app.db.models.fundamental_manual_input import FundamentalManualInput
from app.db.models.investing_holding import InvestingHolding
from app.services.stance import compute_stance

router = APIRouter(prefix="/api/v1/investing", tags=["Investing"])


# ============================================================
# SCHEMAS
# ============================================================

class ManualInputRequest(BaseModel):
    symbol: str
    period: str                       # matches Screener's column labels, e.g. "Mar 2026"
    field_name: str                   # snake_case, matching the reported missing field
    value_numeric: float | None = None
    value_text: str | None = None
    source_note: str | None = None    # e.g. "AR FY26 p.142" — provenance is not optional


class HoldingRequest(BaseModel):
    symbol: str
    quantity: int = Field(gt=0)
    avg_buy_price: float = Field(gt=0)
    buy_date: date
    notes: str | None = None


# ============================================================
# ANALYSIS
# ============================================================

@router.post("/{symbol}/analyze", summary="Run a fundamental analysis (places no orders)")
async def analyze(
    symbol: str,
    current_user: User = Depends(require_trader),
    db: AsyncSession = Depends(get_db),
):
    """
    Run the three stages and persist the result.

    Slow on a cold cache (it scrapes, and Stage 1 queries the document store);
    fast afterwards, since fundamentals are Redis-cached for three days.
    """
    from app.graph.builder import run_investing_cycle

    symbol = symbol.upper().strip()
    result = await run_investing_cycle(symbol, user_id=str(current_user.id))
    state = result["state"]

    thesis = state.get("investment_thesis")
    if thesis is None:
        raise HTTPException(status_code=500, detail="Analysis produced no thesis")

    # Nothing was scraped, so every stage ran against no data and the thesis is
    # NOT_RATED by arithmetic rather than by judgement. Storing that would put a
    # scorecard for a company we never read into the table, and show the user
    # "insufficient data" when the real answer is usually "that ticker is wrong".
    if state.get("fundamentals_raw") is None:
        blocking = next(
            (m for m in (state.get("missing_data") or []) if m.field in ("symbol", "fundamentals")),
            None,
        )
        reason = blocking.reason if blocking else f"No fundamentals could be fetched for {symbol}."
        raise HTTPException(
            status_code=404 if (blocking and blocking.field == "symbol") else 502,
            detail=reason,
        )

    business = state.get("business_report")
    financial = state.get("financial_report")
    valuation = state.get("valuation_report")

    row = FundamentalScorecard(
        symbol=symbol,
        run_id=result["thread_id"],
        data_as_of=thesis.data_as_of,
        quality_grade=thesis.quality_grade,
        not_rated_reason=thesis.not_rated_reason,
        completeness=thesis.completeness,
        conviction=thesis.conviction,
        business_report=business.model_dump() if business else {},
        financial_report=financial.model_dump() if financial else {},
        valuation_report=valuation.model_dump() if valuation else {},
        red_flags=thesis.red_flags or [],
        missing_data=[m.model_dump() for m in (state.get("missing_data") or [])],
        narrative=thesis.narrative,
    )
    db.add(row)
    await db.commit()

    logger.info(f"Investing analysis stored | {symbol} | grade={thesis.quality_grade} | run={result['thread_id']}")
    return await _thesis_payload(db, symbol, row, current_user)


@router.get("/{symbol}/thesis", summary="Latest thesis, with a live stance")
async def get_thesis(
    symbol: str,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """
    The stored quality verdict and valuation band, with the stance computed
    NOW against the current price — not as it stood when the analysis ran.
    """
    symbol = symbol.upper().strip()
    row = (await db.execute(
        select(FundamentalScorecard)
        .where(FundamentalScorecard.symbol == symbol)
        .order_by(FundamentalScorecard.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis stored for {symbol}. POST /api/v1/investing/{symbol}/analyze first.",
        )
    return await _thesis_payload(db, symbol, row, current_user)


async def _thesis_payload(db, symbol, row, current_user) -> dict:
    """Assemble the response, deriving the stance live."""
    band = row.valuation_report or {}
    assumptions = band.get("assumptions") or {}

    owned = (await db.execute(
        select(InvestingHolding).where(
            InvestingHolding.user_id == str(current_user.id),
            InvestingHolding.symbol == symbol,
        )
    )).scalar_one_or_none()

    current_price = assumptions.get("current_price")
    stance = compute_stance(
        quality_grade=row.quality_grade,
        current_price=current_price,
        band=band if band.get("complete") else None,
        owned=owned is not None,
    )

    return {
        "symbol": symbol,
        "as_of": row.created_at.isoformat(),
        "data_as_of": row.data_as_of,
        "quality": {
            "grade": row.quality_grade,
            "not_rated_reason": row.not_rated_reason,
            "completeness": row.completeness,
            "conviction": row.conviction,
            "red_flags": row.red_flags,
        },
        "valuation": band,
        "stance": {
            "action": stance.action,
            "price_vs_band": stance.price_vs_band,
            "trigger_price": stance.trigger_price,
            "rule_applied": stance.rule_applied,
            "horizon": stance.horizon,
            "computed_at": stance.computed_at,
            "current_price": current_price,
        },
        "owned": (
            {"quantity": owned.quantity, "avg_buy_price": owned.avg_buy_price,
             "buy_date": owned.buy_date.isoformat()} if owned else None
        ),
        "business": row.business_report,
        "financial": row.financial_report,
        "missing_data": row.missing_data,
        "narrative": row.narrative,
        # Stated on every response: this surface never places an order.
        "advisory_only": True,
    }


@router.get("/watchlist", summary="Investing watchlist symbols")
async def watchlist(current_user: User = Depends(require_viewer)):
    return {
        "symbols": [s.strip() for s in settings.INVESTING_WATCHLIST_SYMBOLS.split(",") if s.strip()],
        "note": (
            "Kept disjoint from the trading watchlist. Banks and NBFCs are excluded — "
            "leverage and coverage ratios do not carry their usual meaning for lenders."
        ),
    }


# ============================================================
# MANUAL INPUT — figures read out of an annual report by hand
# ============================================================

@router.post("/manual-input", summary="Supply a figure the system could not compute")
async def add_manual_input(
    request: ManualInputRequest,
    current_user: User = Depends(require_trader),
    db: AsyncSession = Depends(get_db),
):
    """
    Human-entered values are the HIGHEST-trust tier, not a fallback — they were
    read by a person off a primary source. Which is exactly why provenance is
    required: without it, one typo becomes an unattributable permanent verdict.
    """
    if request.value_numeric is None and not request.value_text:
        raise HTTPException(status_code=400, detail="Provide value_numeric or value_text")

    symbol = request.symbol.upper().strip()
    existing = (await db.execute(
        select(FundamentalManualInput).where(
            FundamentalManualInput.symbol == symbol,
            FundamentalManualInput.period == request.period,
            FundamentalManualInput.field_name == request.field_name,
        )
    )).scalar_one_or_none()

    if existing:
        existing.value_numeric = request.value_numeric
        existing.value_text = request.value_text
        existing.source_note = request.source_note
        existing.entered_by = str(current_user.id)
        existing.updated_at = datetime.now(timezone.utc)
        row = existing
    else:
        row = FundamentalManualInput(
            symbol=symbol, period=request.period, field_name=request.field_name,
            value_numeric=request.value_numeric, value_text=request.value_text,
            source_note=request.source_note, entered_by=str(current_user.id),
        )
        db.add(row)

    await db.commit()
    return {
        "status": "stored",
        "symbol": symbol,
        "field_name": request.field_name,
        "period": request.period,
        # No workflow is resumed. Re-running is cheap because the scrape is cached.
        "next": f"POST /api/v1/investing/{symbol}/analyze to re-run with this value",
    }


@router.get("/{symbol}/manual-inputs", summary="Figures supplied by hand for this symbol")
async def list_manual_inputs(
    symbol: str,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(FundamentalManualInput)
        .where(FundamentalManualInput.symbol == symbol.upper().strip())
        .order_by(FundamentalManualInput.updated_at.desc())
    )).scalars().all()

    return [
        {"period": r.period, "field_name": r.field_name,
         "value": r.value_numeric if r.value_numeric is not None else r.value_text,
         "source_note": r.source_note, "updated_at": r.updated_at.isoformat()}
        for r in rows
    ]


# ============================================================
# HOLDINGS — what you bought by hand, at your own broker
# ============================================================

@router.get("/holdings", summary="Your long-term holdings register")
async def list_holdings(
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(InvestingHolding).where(InvestingHolding.user_id == str(current_user.id))
    )).scalars().all()

    return [
        {"symbol": r.symbol, "quantity": r.quantity, "avg_buy_price": r.avg_buy_price,
         "buy_date": r.buy_date.isoformat(), "notes": r.notes}
        for r in rows
    ]


@router.post("/holdings", summary="Record a position you bought at your broker")
async def upsert_holding(
    request: HoldingRequest,
    current_user: User = Depends(require_trader),
    db: AsyncSession = Depends(get_db),
):
    """
    A record of intent, NOT a reconciliation against the broker — Investing
    mode does not touch the broker at all.

    Without this register the stance matrix collapses: BUY, ADD and HOLD are
    different answers to the same numbers depending only on whether the
    position already exists, and the quarterly re-review has nothing to watch.
    """
    symbol = request.symbol.upper().strip()

    trading = {s.strip().upper() for s in settings.WATCHLIST_SYMBOLS.split(",") if s.strip()}
    if symbol in trading:
        # Holding a symbol in demat while the trading side shorts it intraday
        # as MIS can be treated by the broker as a delivery sell. Warn rather
        # than refuse: the position is real either way, and the user is the
        # one who bought it.
        logger.warning(f"{symbol} is on the trading watchlist AND held long-term — margin conflict risk")

    existing = (await db.execute(
        select(InvestingHolding).where(
            InvestingHolding.user_id == str(current_user.id),
            InvestingHolding.symbol == symbol,
        )
    )).scalar_one_or_none()

    if existing:
        existing.quantity = request.quantity
        existing.avg_buy_price = request.avg_buy_price
        existing.buy_date = request.buy_date
        existing.notes = request.notes
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(InvestingHolding(
            user_id=str(current_user.id), symbol=symbol, quantity=request.quantity,
            avg_buy_price=request.avg_buy_price, buy_date=request.buy_date, notes=request.notes,
        ))

    await db.commit()
    return {
        "status": "stored",
        "symbol": symbol,
        "warning": (
            f"{symbol} is also on the trading watchlist — a long-term holding plus an "
            f"intraday short on the same symbol can be treated as a delivery sell."
            if symbol in trading else None
        ),
    }


@router.delete("/holdings/{symbol}", summary="Remove a holding you have exited")
async def delete_holding(
    symbol: str,
    current_user: User = Depends(require_trader),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(InvestingHolding).where(
            InvestingHolding.user_id == str(current_user.id),
            InvestingHolding.symbol == symbol.upper().strip(),
        )
    )
    await db.commit()
    return {"status": "deleted", "symbol": symbol.upper().strip()}
