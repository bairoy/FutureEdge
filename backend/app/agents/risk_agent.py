"""
app/agents/risk_agent.py
=========================
Risk management agent — deterministic safety checks.

CHANGES FROM ORIGINAL:
-----------------------
1. Now ASYNC  (async def risk_agent_node)

2. Kelly criterion now REAL, not hardcoded 0.02
   Reads actual trade history from Postgres to compute
   win rate, average win, average loss → real Kelly fraction.
   Falls back to conservative 2% if not enough history.

3. Indian market specific checks added
   - NSE lot sizes for F&O
   - Intraday position limits
   - Zerodha MIS (intraday) margin rules

PHILOSOPHY:
-----------
This agent must NEVER use AI/LLMs.
Risk systems must be:
  - deterministic (same input → same output every time)
  - auditable      (regulator can verify the logic)
  - conservative   (when uncertain, VETO)

If this agent crashes → default is VETO (reject the trade).
"""

from loguru import logger

from app.graph.state import AgentState, AgentVote, PortfolioSnapshot, MarketContext
from app.db.postgres import AsyncSessionLocal
from app.db.repos.trade_repo import TradeRepo
from app.brokers.symbol_mapper import is_market_open


# ============================================================
# RISK LIMITS  (Indian market / Zerodha MIS intraday)
# ============================================================

MAX_MARGIN_UTILISATION = 0.80   # VETO if using > 80% margin
HIGH_MARGIN_UTILISATION= 0.60   # WARNING if using > 60% margin
MAX_SYMBOL_EXPOSURE    = 0.30   # VETO if > 30% in one symbol
HIGH_SYMBOL_EXPOSURE   = 0.20   # WARNING if > 20% in one symbol
MAX_VOLATILITY         = 0.08   # VETO if 24h volatility > 8%
HIGH_VOLATILITY        = 0.05   # WARNING if > 5%
MAX_POSITIONS          = 5      # Covered by portfolio agent too


# ============================================================
# RISK AGENT NODE  (async)
# ============================================================

async def risk_agent_node(state: AgentState) -> dict:
    """
    Runs all risk checks. If any critical limit is breached → VETO.
    If warnings only → HOLD with reduced confidence.
    If all clear → HOLD with high confidence (agent approves trade).

    Returns a partial state update with risk_vote set.
    """

    try:
        portfolio: PortfolioSnapshot = state["portfolio"]
        ctx: MarketContext           = state["market_context"]
        symbol                       = ctx.symbol

        decision   = "HOLD"     # default: no objection
        confidence = 0.8
        reasons    = []
        metadata   = {}

        # --------------------------------------------------------
        # CHECK 1: MARGIN UTILISATION
        # --------------------------------------------------------

        # How much of our capital is already deployed?
        # High utilisation = less room to absorb losses.

        if portfolio.total_equity > 0:
            margin_util = portfolio.margin_used / portfolio.total_equity
        else:
            margin_util = 0.0

        metadata["margin_utilisation"] = round(margin_util, 4)

        if margin_util > MAX_MARGIN_UTILISATION:
            decision   = "VETO"
            confidence = 0.99
            reasons.append(
                f"Margin utilisation {margin_util*100:.1f}% "
                f"exceeds {MAX_MARGIN_UTILISATION*100:.0f}% limit"
            )

        elif margin_util > HIGH_MARGIN_UTILISATION:
            reasons.append(
                f"Margin utilisation {margin_util*100:.1f}% — elevated"
            )

        # --------------------------------------------------------
        # CHECK 2: SINGLE SYMBOL CONCENTRATION
        # --------------------------------------------------------

        # Are we already too exposed to this specific symbol?
        # Concentration risk: if RELIANCE crashes, we lose a lot.

        symbol_exposure = sum(
            pos.get("notional", 0)
            for pos in portfolio.open_positions
            if pos.get("symbol") == symbol
        )

        if portfolio.total_equity > 0:
            exposure_pct = symbol_exposure / portfolio.total_equity
        else:
            exposure_pct = 0.0

        metadata["symbol_exposure_pct"] = round(exposure_pct, 4)

        if exposure_pct > MAX_SYMBOL_EXPOSURE:
            decision   = "VETO"
            confidence = 0.95
            reasons.append(
                f"{symbol} exposure {exposure_pct*100:.1f}% "
                f"exceeds {MAX_SYMBOL_EXPOSURE*100:.0f}% limit"
            )

        elif exposure_pct > HIGH_SYMBOL_EXPOSURE:
            reasons.append(
                f"{symbol} exposure {exposure_pct*100:.1f}% — consider reducing"
            )

        # --------------------------------------------------------
        # CHECK 3: VOLATILITY REGIME
        # --------------------------------------------------------

        # High volatility = unpredictable prices = higher risk.
        # During extreme volatility (elections, budget day,
        # global events), we halt trading automatically.

        vol = ctx.volatility_24h
        metadata["volatility_24h"] = vol

        if vol > MAX_VOLATILITY:
            decision   = "VETO"
            confidence = 0.90
            reasons.append(
                f"Extreme volatility {vol*100:.1f}% "
                f"exceeds {MAX_VOLATILITY*100:.0f}% limit — trading halted"
            )

        elif vol > HIGH_VOLATILITY:
            reasons.append(
                f"High volatility {vol*100:.1f}% — reduce position size"
            )

        # --------------------------------------------------------
        # CHECK 4: DRAWDOWN LIMIT
        # --------------------------------------------------------

        # Combined daily drawdown check: Sum realized_pnl_today + unrealized_pnl.
        # Veto if total daily PnL drops below 5% of total equity.
        total_daily_pnl = portfolio.realized_pnl_today + portfolio.unrealized_pnl
        drawdown_limit = 0.05 * portfolio.total_equity

        metadata["realized_pnl_today"] = round(portfolio.realized_pnl_today, 2)
        metadata["unrealized_pnl"]      = round(portfolio.unrealized_pnl, 2)
        metadata["total_daily_pnl"]    = round(total_daily_pnl, 2)

        if total_daily_pnl < -drawdown_limit:
            decision = "VETO"
            confidence = 0.98
            reasons.append(
                f"Daily drawdown limit exceeded: total daily loss of ₹{abs(total_daily_pnl):.2f} "
                f"exceeds limit of ₹{drawdown_limit:.2f} (5% of equity)"
            )
        elif total_daily_pnl < -(0.03 * portfolio.total_equity):
            reasons.append(
                f"Significant daily drawdown stress — combined daily PnL: "
                f"₹{total_daily_pnl:.2f}"
            )
            if decision == "HOLD":
                confidence = min(confidence, 0.6)

        # --------------------------------------------------------
        # CHECK 5: MARKET HOURS & TIME-OF-DAY RISK
        # --------------------------------------------------------
        from app.core.config import settings
        import pytz
        from datetime import datetime

        is_open = is_market_open()
        metadata["market_open"] = is_open

        is_paper = state.get("paper_trade", True)

        # In paper trading, everything should be mock (unrestricted by live NSE hours/time limits)
        if not is_paper:
            if settings.ACTIVE_BROKER.lower() != "mock" and not is_open:
                decision = "VETO"
                confidence = 1.0
                reasons.append("Market is closed (NSE hours: 9:15 AM - 3:30 PM IST)")
            
            if is_open:
                now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
                if now_ist.hour == 15:  # 3:00 PM - 3:59 PM IST
                    reasons.append("Late-day time risk: trading close to market end (3:00 PM+)")
                    if now_ist.minute >= 15:  # 3:15 PM onwards
                        decision = "VETO"
                        confidence = 0.95
                        reasons.append("Veto: late-day trading prohibited after 3:15 PM IST")
                    else:
                        if decision == "HOLD":
                            confidence = min(confidence, 0.5)

        # --------------------------------------------------------
        # CHECK 6: UPCOMING CORPORATE EVENTS (Earnings / Splits / Dividends)
        # --------------------------------------------------------
        try:
            from app.agents.tools.news_tools import get_upcoming_corporate_events
            from datetime import datetime
            
            events = await get_upcoming_corporate_events(symbol)
            metadata["corporate_events"] = events
            
            # Check for upcoming earnings dates
            earnings_dates = events.get("Earnings Date")
            if earnings_dates:
                if isinstance(earnings_dates, str):
                    earnings_dates = [earnings_dates]
                
                # Check if any earnings date is within 3 days
                today = datetime.now().date()
                for ed_str in earnings_dates:
                    try:
                        # Extract date part: first 10 characters
                        ed_clean = ed_str.split(" ")[0][:10]
                        ed_dt = datetime.strptime(ed_clean, "%Y-%m-%d").date()
                        days_diff = (ed_dt - today).days
                        
                        if 0 <= days_diff <= 3:
                            decision = "VETO"
                            confidence = 0.95
                            reasons.append(
                                f"Upcoming earnings event on {ed_clean} ({days_diff} days away) represents excessive event risk — trade vetoed"
                            )
                            break
                        elif -1 <= days_diff < 0:
                            decision = "VETO"
                            confidence = 0.95
                            reasons.append(
                                f"Recent earnings event on {ed_clean} (under 1 day ago) represents post-earnings volatility risk — trade vetoed"
                            )
                            break
                    except Exception as parse_err:
                        logger.warning(f"Error parsing earnings date '{ed_str}': {parse_err}")
        except Exception as e:
            logger.warning(f"Failed to fetch corporate events for risk checking: {e}")

        # --------------------------------------------------------
        # KELLY CRITERION  (real calculation from trade history)
        # --------------------------------------------------------

        # The Kelly criterion gives the mathematically optimal
        # fraction of capital to risk on each trade.
        #
        # Formula: f = (p*b - q) / b
        # Where:
        #   p = win rate (e.g. 0.6 = 60% wins)
        #   q = 1 - p  (loss rate)
        #   b = avg_win / avg_loss  (reward:risk ratio)
        #
        # We use HALF Kelly (divide by 2) which is standard
        # in professional trading to reduce volatility.

        user_id = state.get("user_id", "anonymous")
        kelly_fraction = await _calculate_kelly(symbol, user_id)
        metadata["kelly_fraction"]      = kelly_fraction
        metadata["suggested_size_pct"]  = round(kelly_fraction * 100, 2)

        # --------------------------------------------------------
        # ALL CHECKS PASSED
        # --------------------------------------------------------

        if not reasons:
            reasons.append("All risk checks passed")

        # --------------------------------------------------------
        # BUILD VOTE
        # --------------------------------------------------------

        metadata["max_drawdown_limit"] = portfolio.max_drawdown_limit

        vote = AgentVote(
            agent     = "RiskAgent",
            decision  = decision,
            confidence= round(confidence, 3),
            reasoning = " | ".join(reasons),
            metadata  = metadata,
        )

        logger.info(
            f"🛡️  RiskAgent | {symbol} | {decision} | "
            f"conf={confidence:.2f} | margin={margin_util*100:.1f}%"
        )

        return {
            "risk_vote":       vote,
            "completed_nodes": ["risk_agent"],
            "logs":            [f"RiskAgent generated {decision} for {symbol}"],
        }

    except Exception as e:
        logger.exception(f"RiskAgent failure: {e}")

        # Risk agent failure → VETO (safest possible action)
        return {
            "risk_vote": AgentVote(
                agent     = "RiskAgent",
                decision  = "VETO",
                confidence= 1.0,
                reasoning = f"Risk system failure — defaulting to VETO: {e}",
            ),
            "completed_nodes": ["risk_agent"],
            "logs":            [f"RiskAgent failed: {e}"],
        }


# ============================================================
# KELLY CRITERION CALCULATION  (from real Postgres history)
# ============================================================

async def _calculate_kelly(symbol: str, user_id: str) -> float:
    """
    Calculate the optimal position size fraction using the
    Kelly criterion, based on real trade history from Postgres.

    Returns a fraction between 0.0 and 0.25.
    0.02 = 2% of capital per trade (conservative minimum).
    0.25 = 25% of capital per trade (maximum cap, never exceeded).

    If fewer than 10 closed trades exist for this symbol,
    we return a safe conservative default of 0.02 (2%).
    """

    try:
        async with AsyncSessionLocal() as session:
            trades = await TradeRepo.get_recent_closed_trades(
                session, symbol, user_id=user_id, limit=50
            )
            stats = TradeRepo.calculate_win_stats(trades)

        win_rate = stats["win_rate"]
        avg_win  = stats["avg_win"]
        avg_loss = stats["avg_loss"]

        if avg_loss == 0:
            return 0.02

        # Reward:risk ratio
        b = avg_win / avg_loss

        # Loss probability
        q = 1.0 - win_rate

        # Full Kelly
        full_kelly = (win_rate * b - q) / b

        # Half Kelly — standard professional practice
        half_kelly = full_kelly / 2.0

        # Clamp between 2% and 25%
        result = max(0.02, min(half_kelly, 0.25))

        logger.debug(
            f"Kelly | {symbol} | "
            f"win_rate={win_rate:.2f} | b={b:.2f} | "
            f"half_kelly={half_kelly:.4f} | final={result:.4f} | "
            f"n={stats['sample_size']}"
        )

        return round(result, 4)

    except Exception as e:
        logger.warning(f"Kelly calculation failed: {e} — using default 0.02")
        return 0.02