from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from loguru import logger

from app.auth.dependencies import require_viewer
from app.db.models.user import User
from app.graph.backtester import run_backtest, run_walk_forward_backtest

router = APIRouter(prefix="/api/v1", tags=["Backtest"])


class BacktestRequest(BaseModel):
    symbol: str
    period: str = "1mo"
    interval: str = "5m"
    initial_capital: float = 100000.0
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 3.0
    size_pct: float = 10.0
    slippage_pct: float = 0.05   # 0.05% realistic slippage for NSE liquid stocks
    execution_delay_candles: int = 0
    trailing_stop_enabled: bool = True
    trailing_stop_trigger_pct: float = 2.0
    trailing_stop_sl_pct: float = 1.5


class WalkForwardRequest(BaseModel):
    symbol: str
    period: str = "6mo"
    interval: str = "5m"
    train_pct: float = 0.70
    n_folds: int = 4
    initial_capital: float = 100000.0
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 3.0
    size_pct: float = 10.0
    slippage_pct: float = 0.05
    execution_delay_candles: int = 0
    trailing_stop_enabled: bool = True
    trailing_stop_trigger_pct: float = 2.0
    trailing_stop_sl_pct: float = 1.5


@router.post("/backtest", summary="Run a historical backtest for a specific symbol")
async def execute_backtest(
    request: BacktestRequest,
    current_user: User = Depends(require_viewer),
):
    """
    Run a historical strategy backtest on yfinance data.
    Allows traders and managers to optimize strategies before going live.
    """
    logger.info(
        f"Backtest triggered | user={current_user.email} | "
        f"symbol={request.symbol} | period={request.period} | interval={request.interval}"
    )

    try:
        result = await run_backtest(
            symbol=request.symbol,
            period=request.period,
            interval=request.interval,
            initial_capital=request.initial_capital,
            stop_loss_pct=request.stop_loss_pct,
            take_profit_pct=request.take_profit_pct,
            size_pct=request.size_pct,
            slippage_pct=request.slippage_pct,
            execution_delay_candles=request.execution_delay_candles,
            trailing_stop_enabled=request.trailing_stop_enabled,
            trailing_stop_trigger_pct=request.trailing_stop_trigger_pct,
            trailing_stop_sl_pct=request.trailing_stop_sl_pct,
        )
        return result
    except Exception as e:
        logger.exception(f"Backtest failed: {e}")
        raise HTTPException(status_code=500, detail=f"Backtest run failed: {str(e)}")


@router.post("/backtest/walk-forward", summary="Run a rolling walk-forward backtest for validation")
async def execute_walk_forward_backtest(
    request: WalkForwardRequest,
    current_user: User = Depends(require_viewer),
):
    """
    Run out-of-sample walk-forward validation across multiple rolling folds.
    Isolates parameters to prevent overfitting.
    """
    logger.info(
        f"Walk-forward backtest triggered | user={current_user.email} | "
        f"symbol={request.symbol} | period={request.period} | interval={request.interval}"
    )

    try:
        result = await run_walk_forward_backtest(
            symbol=request.symbol,
            period=request.period,
            interval=request.interval,
            train_pct=request.train_pct,
            n_folds=request.n_folds,
            initial_capital=request.initial_capital,
            stop_loss_pct=request.stop_loss_pct,
            take_profit_pct=request.take_profit_pct,
            size_pct=request.size_pct,
            slippage_pct=request.slippage_pct,
            execution_delay_candles=request.execution_delay_candles,
            trailing_stop_enabled=request.trailing_stop_enabled,
            trailing_stop_trigger_pct=request.trailing_stop_trigger_pct,
            trailing_stop_sl_pct=request.trailing_stop_sl_pct,
        )
        return result
    except Exception as e:
        logger.exception(f"Walk-forward backtest failed: {e}")
        raise HTTPException(status_code=500, detail=f"Walk-forward backtest run failed: {str(e)}")
