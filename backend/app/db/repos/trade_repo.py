"""
app/db/repos/trade_repo.py
===========================
All database read/write operations for the Trade table.

MULTI-USER SUPPORT:
-------------------
Every method that fetches trades now filters by user_id.
This ensures:
  - "Show me MY trades"     → only your trades, not others'
  - "My Kelly calculation"  → uses only your win/loss history
  - "My PnL"                → your profit/loss, not someone else's

USAGE:
------
    from app.db.repos.trade_repo import TradeRepo
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # Save a new trade (called by execution_agent)
        trade = await TradeRepo.save_trade(
            session, proposal, run_id="abc123", user_id="uuid-here"
        )

        # Get a user's trade history for Kelly calculation
        trades = await TradeRepo.get_recent_closed_trades(
            session, symbol="RELIANCE", user_id="uuid-here", limit=50
        )
"""

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.db.models.trade import Trade
from app.graph.state import TradeProposal


class TradeRepo:
    """
    All DB operations for the trades table.
    Static methods — no instantiation needed.
    """

    # --------------------------------------------------------
    # SAVE A NEW TRADE
    # --------------------------------------------------------

    @staticmethod
    async def save_trade(
        session:           AsyncSession,
        proposal:          TradeProposal,
        run_id:            str,
        user_id:           str,               # NEW: which user owns this trade
        quantity:          int  = 0,           # actual number of shares traded
        broker:            str  = "mock",
        broker_order_id:   str | None  = None,
        actual_fill_price: float | None = None,
        workflow_run_id:   str | None  = None,
        status:            str | None  = None,  # NEW: optional custom status (e.g. REJECTED)
    ) -> Trade:
        """
        Write a new trade record to the database.

        Called by execution_agent.py immediately after a trade is
        placed (or simulated in mock mode).

        The user_id links this trade to the user who triggered the
        workflow cycle that produced it.

        Returns the Trade ORM object with its generated ID set.
        """

        # Slippage = how far the actual fill was from expected price.
        # Positive means we paid more than expected (bad for LONG).
        slippage = None
        if actual_fill_price is not None:
            slippage = round(actual_fill_price - proposal.entry_price, 4)

        # Resolve status
        if status is None:
            status = "OPEN" if proposal.direction != "NONE" else "NONE"

        trade = Trade(
            user_id          = user_id,
            run_id           = run_id,
            workflow_run_id  = workflow_run_id,
            symbol           = proposal.symbol,
            direction        = proposal.direction,
            size             = proposal.size,
            quantity         = quantity,
            entry_price      = proposal.entry_price,
            stop_loss        = proposal.stop_loss,
            take_profit      = proposal.take_profit,
            risk_score       = proposal.risk_score,
            status           = status,

            hitl_required  = proposal.human_approved is not None,
            human_approved = proposal.human_approved,
            human_notes    = proposal.human_notes,

            # Store all agent votes as JSON — full audit trail
            agent_consensus = [
                {
                    "agent":      vote.agent,
                    "decision":   vote.decision,
                    "confidence": vote.confidence,
                    "reasoning":  vote.reasoning,
                }
                for vote in proposal.agent_consensus
            ],

            broker            = broker,
            broker_order_id   = broker_order_id,
            actual_fill_price = actual_fill_price,
            slippage          = slippage,
        )

        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        logger.info(
            f"Trade saved | id={trade.id} | user_id={user_id} | "
            f"{trade.direction} {trade.symbol} | status={trade.status}"
        )

        return trade

    # --------------------------------------------------------
    # CLOSE A TRADE
    # --------------------------------------------------------

    @staticmethod
    async def close_trade(
        session:    AsyncSession,
        trade_id:   str,
        user_id:    str,      # security check — only owner can close
        exit_price: float,
    ) -> Trade | None:
        """
        Mark a trade as CLOSED and calculate realized PnL.

        The user_id check ensures a user cannot close another
        user's trade — even if they know the trade ID.

        PnL formula:
          LONG  → (exit_price - entry_price) × shares
          SHORT → (entry_price - exit_price) × shares
        """

        result = await session.execute(
            select(Trade).where(
                Trade.id      == trade_id,
                Trade.user_id == user_id,    # ownership check
            )
        )
        trade = result.scalar_one_or_none()

        if trade is None:
            logger.warning(
                f"Trade {trade_id} not found for user {user_id}"
            )
            return None

        trade.exit_price = exit_price
        trade.closed_at = datetime.now(timezone.utc)
        trade.status     = "CLOSED"

        # Calculate profit or loss using QUANTITY (shares), not SIZE (rupees)
        if trade.quantity > 0:
            if trade.direction == "LONG":
                trade.realized_pnl = round(
                    (exit_price - trade.entry_price) * trade.quantity, 4
                )
            elif trade.direction == "SHORT":
                trade.realized_pnl = round(
                    (trade.entry_price - exit_price) * trade.quantity, 4
                )
            else:
                trade.realized_pnl = 0.0
        else:
            # Fallback for legacy trades without quantity
            trade.realized_pnl = 0.0

        # Percentage return = (exit - entry) / entry × 100
        if trade.entry_price > 0:
            if trade.direction == "LONG":
                trade.pnl_pct = round(
                    ((exit_price - trade.entry_price) / trade.entry_price) * 100,
                    4,
                )
            elif trade.direction == "SHORT":
                trade.pnl_pct = round(
                    ((trade.entry_price - exit_price) / trade.entry_price) * 100,
                    4,
                )

        await session.commit()
        await session.refresh(trade)

        logger.info(
            f"Trade closed | id={trade.id} | user_id={user_id} | "
            f"PnL=₹{trade.realized_pnl:.2f} ({trade.pnl_pct:.2f}%)"
        )

        return trade

    # --------------------------------------------------------
    # GET USER'S RECENT CLOSED TRADES  (for Kelly criterion)
    # --------------------------------------------------------

    @staticmethod
    async def get_recent_closed_trades(
        session: AsyncSession,
        symbol:  str,
        user_id: str,     # filter by this user only
        limit:   int = 50,
    ) -> list[Trade]:
        """
        Fetch the last N closed trades for THIS user and symbol.

        Used by risk_agent.py to calculate:
          - win rate  : percentage of trades that made money
          - avg win   : average profit on winning trades
          - avg loss  : average loss magnitude on losing trades

        These feed into the Kelly criterion for position sizing.

        IMPORTANT: filtered by user_id — each user's Kelly
        calculation is based on their own trading history only.
        A new user starts with conservative defaults (2% size).
        """

        result = await session.execute(
            select(Trade)
            .where(
                Trade.user_id    == user_id,
                Trade.symbol     == symbol,
                Trade.status     == "CLOSED",
                Trade.realized_pnl.is_not(None),
            )
            .order_by(Trade.closed_at.desc())
            .limit(limit)
        )

        return list(result.scalars().all())

    # --------------------------------------------------------
    # GET USER'S ALL TRADES (for dashboard history page)
    # --------------------------------------------------------

    @staticmethod
    async def get_user_trades(
        session: AsyncSession,
        user_id: str,
        limit:   int = 100,
        offset:  int = 0,
    ) -> list[Trade]:
        """
        Fetch all trades for a user (paginated).
        Used by the trade history table on the frontend dashboard.
        """

        result = await session.execute(
            select(Trade)
            .where(Trade.user_id == user_id)
            .order_by(Trade.opened_at.desc())
            .limit(limit)
            .offset(offset)
        )

        return list(result.scalars().all())

    # --------------------------------------------------------
    # CALCULATE WIN/LOSS STATS  (for Kelly criterion)
    # --------------------------------------------------------

    @staticmethod
    def calculate_win_stats(trades: list[Trade]) -> dict:
        """
        Given a list of closed trades, calculate:
          win_rate : fraction that were profitable (0.0 to 1.0)
          avg_win  : average profit on winning trades (in Rupees)
          avg_loss : average loss magnitude on losing trades

        Returns conservative defaults if fewer than 10 trades.
        We need at least 10 trades for a statistically meaningful
        Kelly calculation — less than that and the math is unreliable.
        """

        if len(trades) < 10:
            # Not enough history — use safe conservative defaults.
            # 2% position size via Kelly with 50% win rate.
            return {
                "win_rate":    0.5,
                "avg_win":     1.0,
                "avg_loss":    1.0,
                "sample_size": len(trades),
                "note":        "using defaults — insufficient trade history",
            }

        winners = [t for t in trades if (t.realized_pnl or 0) > 0]
        losers  = [t for t in trades if (t.realized_pnl or 0) <= 0]

        win_rate = len(winners) / len(trades)

        avg_win  = (
            sum(t.realized_pnl for t in winners) / len(winners)
            if winners else 1.0
        )

        avg_loss = (
            abs(sum(t.realized_pnl for t in losers) / len(losers))
            if losers else 1.0
        )

        return {
            "win_rate":    round(win_rate, 4),
            "avg_win":     round(avg_win,  2),
            "avg_loss":    round(avg_loss, 2),
            "sample_size": len(trades),
        }