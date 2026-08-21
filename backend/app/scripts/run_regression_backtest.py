"""
app/scripts/run_regression_backtest.py
========================================
CI gate: fail the build if the strategy's risk-adjusted performance regresses.

    python -m app.scripts.run_regression_backtest

Exit code 0 = pass, 1 = regression, 2 = could not run the check.

WHY A COMMITTED FIXTURE INSTEAD OF A LIVE FETCH:
-------------------------------------------------
The obvious version of this — "backtest the last 6 months, require Sharpe >=
0.5" — is worse than no gate at all. The window slides every day, so the same
commit passes on Monday and fails on Thursday because the market moved. A gate
that fails for reasons unrelated to the diff teaches everyone to ignore it.

A fixed *date range* fetched live cannot fix that here either: this is an
intraday strategy (it produces zero trades on daily bars), and yfinance only
serves 1-minute data for roughly the last 30 days — so a pinned historical
intraday window is not fetchable at all.

Hence data/fixtures/regression_candles.json: a frozen slice of real 1-minute
RELIANCE candles, committed to the repo. The input never changes, so a change
in Sharpe means the STRATEGY changed — which is the only signal this gate
exists to give. It also means CI needs no network for this step.

Re-baseline by regenerating the fixture deliberately, in its own commit, with
the new measured numbers recorded below.

WHAT THIS GATE DOES AND DOES NOT TELL YOU:
-------------------------------------------
It catches CODE regressions — an inverted comparison, lookahead bias, broken
exits — by watching for a collapse against a measured baseline.

It does NOT certify that the strategy makes money. On this fixture the
measured Sharpe is NEGATIVE (see BASELINE_SHARPE). The strategy as it stands
loses money on recent RELIANCE intraday data after modelled costs, and no
threshold here changes that. Treat improving it as strategy work; this file
only stops it getting silently worse.
"""

import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

# ── The pinned regression dataset ───────────────────────────────────────────
# RELIANCE, not NIFTY 50: the index produces ZERO trades under this strategy
# (verified on both daily and 1-minute bars), so gating on it would have been
# a check that could never fail.
SYMBOL = "RELIANCE"
INTERVAL = "1m"
FIXTURE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "regression_candles.json"

# Measured on the committed fixture at the time it was frozen.
# 1832 candles (2026-07-30 → 2026-08-05), 213 trades.
BASELINE_SHARPE = -83.57

# Tripwire, not a target — deliberately slack against the baseline so ordinary
# strategy tuning does not fail the build, while a genuine logic break (which
# moves Sharpe by orders of magnitude) trips it. See the module docstring:
# this is NOT a claim that the strategy is profitable.
MIN_SHARPE = -150.0

# Below this many trades the Sharpe figure is statistically meaningless, and
# passing on 3 trades would make the gate decorative.
MIN_TRADES = 5


def _load_fixture() -> list[dict]:
    """Read the frozen candle set. No network, so CI cannot flake on yfinance."""
    logger.info(f"Loading regression fixture: {FIXTURE}")
    return json.loads(FIXTURE.read_text())


async def run() -> int:
    from app.graph.backtester import run_backtest

    try:
        candles = _load_fixture()
    except Exception as e:
        # A missing/corrupt fixture is an infrastructure problem, not a code
        # regression. Exit 2 so the two are distinguishable in CI.
        logger.error(f"Could not load regression fixture: {e}")
        return 2

    if len(candles) < 35:
        logger.error(
            f"Only {len(candles)} candles for the pinned window — "
            f"the backtester needs at least 35. Not treating this as a regression."
        )
        return 2

    result = await run_backtest(
        symbol=SYMBOL,
        period="fixture",       # unused when candles are supplied
        interval=INTERVAL,
        candles=candles,
    )

    metrics = result.get("metrics", {})
    sharpe = metrics.get("sharpe_ratio", 0.0)
    trades = metrics.get("total_trades", 0)

    logger.info(
        f"Regression backtest | {SYMBOL} {INTERVAL} fixture | "
        f"candles={len(candles)} | trades={trades} | "
        f"sharpe={sharpe} | win_rate={metrics.get('win_rate')}% | "
        f"return={metrics.get('return_pct')}% | "
        f"max_dd={metrics.get('max_drawdown_pct')}%"
    )

    if trades < MIN_TRADES:
        logger.error(
            f"Only {trades} trades (need >= {MIN_TRADES}) — the strategy has "
            f"effectively stopped trading on this window. Treating as a regression."
        )
        return 1

    if sharpe < MIN_SHARPE:
        logger.error(
            f"REGRESSION: Sharpe {sharpe} is below the {MIN_SHARPE} floor. "
            f"Something in the signal or execution logic has broken."
        )
        return 1

    logger.info(
        f"Backtest regression check PASSED "
        f"(sharpe {sharpe} >= floor {MIN_SHARPE}; baseline {BASELINE_SHARPE})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
