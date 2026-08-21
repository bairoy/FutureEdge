"""
app/data/market_risk.py
==========================
Beta and the CAPM discount rate for the Stage 3 DCF.

WHY BETA IS COMPUTED HERE RATHER THAN FETCHED:
------------------------------------------------
No free source publishes a beta we can rely on — Screener does not carry it,
and the sites that do rarely say what window they used. Since the raw material
(price history for the stock and the index) is already free through yfinance,
which this project uses elsewhere, computing it is both cheaper and more
honest than sourcing a number of unknown provenance.

WHY TWO WINDOWS, NOT ONE:
---------------------------
The method asks for beta from more than one source, and warns that estimates
vary meaningfully with the time window — a low beta on a genuinely volatile,
story-driven stock is misleading rather than more accurate.

So this computes two: 2-year weekly and 5-year monthly. The SPREAD between
them is the finding. Two windows that agree mean the estimate is stable; two
that disagree are the warning the method wants, and the caller surfaces that
rather than quietly averaging it away.

HOW BETA IS CALCULATED:
-------------------------
    beta = covariance(stock returns, index returns) / variance(index returns)

against NIFTY 50 (^NSEI), on the same return frequency as the window.

USAGE:
------
    from app.data.market_risk import get_beta, discount_rate_capm

    est = await get_beta("RELIANCE")
    est.beta            # the value to use (the more conservative of the two)
    est.disagreement    # spread between windows — worth showing the reader

    dr = discount_rate_capm(est.beta)   # risk-free + beta x ERP
"""

import asyncio
from dataclasses import dataclass, field, asdict

from loguru import logger

from app.core.config import settings
from app.db.redis import redis_client

# Beta moves slowly and each estimate is two yfinance downloads — cache hard.
CACHE_SECONDS = 7 * 24 * 3600

NIFTY_TICKER = "^NSEI"

# (label, yfinance period, yfinance interval)
BETA_WINDOWS = [
    ("2y_weekly", "2y", "1wk"),
    ("5y_monthly", "5y", "1mo"),
]

# Above this spread the two windows are telling different stories, and the
# caller should say so rather than present one number as settled.
DISAGREEMENT_THRESHOLD = 0.35


@dataclass
class BetaEstimate:
    symbol: str
    beta: float | None = None                 # the value to use
    estimates: dict[str, float] = field(default_factory=dict)   # window label -> beta
    disagreement: float | None = None         # max - min across windows
    note: str = ""


def _to_yf(symbol: str) -> str:
    """NSE symbols carry a .NS suffix on Yahoo — same convention as data/feed.py."""
    return symbol if symbol.endswith((".NS", ".BO")) or symbol.startswith("^") else f"{symbol}.NS"


def _beta_for_window(symbol: str, period: str, interval: str) -> float | None:
    """
    One beta estimate. Returns None rather than raising — a missing window
    degrades the estimate, it does not fail the valuation.
    """
    import yfinance as yf

    data = yf.download(
        [_to_yf(symbol), NIFTY_TICKER],
        period=period, interval=interval,
        progress=False, auto_adjust=True, group_by="column",
    )
    if data is None or data.empty:
        return None

    close = data["Close"] if "Close" in data else data
    close = close.dropna()
    if len(close) < 20:      # too few points for a meaningful covariance
        return None

    returns = close.pct_change().dropna()
    stock_col, index_col = _to_yf(symbol), NIFTY_TICKER
    if stock_col not in returns or index_col not in returns:
        return None

    index_var = returns[index_col].var()
    if not index_var:
        return None

    covariance = returns[stock_col].cov(returns[index_col])
    return float(covariance / index_var)


async def get_beta(symbol: str, force_refresh: bool = False) -> BetaEstimate:
    """
    Beta against NIFTY 50, computed over two windows and cached for a week.

    The reported `beta` is the HIGHER of the two estimates, not the average.
    A higher beta means a higher discount rate and so a lower intrinsic value —
    the conservative direction. Where the windows disagree, taking the flattering
    one is how a volatile stock ends up looking cheap.
    """
    cache_key = f"futureedge:beta:{symbol}"

    if not force_refresh:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                import json
                return BetaEstimate(**json.loads(cached))
        except Exception as e:
            logger.warning(f"Beta cache read failed for {symbol}: {e}")

    loop = asyncio.get_event_loop()
    estimates: dict[str, float] = {}

    for label, period, interval in BETA_WINDOWS:
        try:
            value = await loop.run_in_executor(None, _beta_for_window, symbol, period, interval)
            if value is not None:
                estimates[label] = round(value, 3)
        except Exception as e:
            logger.warning(f"Beta window {label} failed for {symbol}: {e}")

    if not estimates:
        return BetaEstimate(symbol=symbol, note="Beta could not be computed — no usable price history")

    values = list(estimates.values())
    disagreement = round(max(values) - min(values), 3)
    beta = max(values)

    note = f"Beta from {len(estimates)} window(s): " + ", ".join(f"{k}={v}" for k, v in estimates.items()) + "."
    if disagreement >= DISAGREEMENT_THRESHOLD:
        note += (f". The windows disagree by {disagreement:.2f} — the estimate is unstable, "
                 f"so treat the discount rate as a range rather than a number.")
    note += " Using the higher estimate (conservative: raises the discount rate, lowers value)."

    estimate = BetaEstimate(
        symbol=symbol, beta=beta, estimates=estimates,
        disagreement=disagreement, note=note,
    )

    try:
        import json
        await redis_client.setex(cache_key, CACHE_SECONDS, json.dumps(asdict(estimate)))
    except Exception as e:
        logger.warning(f"Beta cache write failed for {symbol}: {e}")

    logger.info(f"Beta | {symbol} | {estimates} | using {beta}")
    return estimate


def discount_rate_capm(beta: float | None) -> float:
    """
    CAPM: risk-free + beta x equity risk premium, as a percentage.

    Falls back to beta = 1.0 (the market's own risk) when beta is unknown —
    stated rather than silently assumed, since the caller surfaces it.
    """
    effective_beta = 1.0 if beta is None else beta
    return settings.RISK_FREE_RATE_PCT + effective_beta * settings.EQUITY_RISK_PREMIUM_PCT
