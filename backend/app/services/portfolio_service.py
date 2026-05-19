"""
Portfolio Service

Responsibilities:
-----------------
1. Fetch current portfolio state
2. Build PortfolioSnapshot
3. Provide portfolio visibility
"""

# ============================================================
# IMPORTS
# ============================================================

from sqlalchemy import text

from app.db.postgres import (
    AsyncSessionLocal
)

from graph.state import (
    PortfolioSnapshot
)


# ============================================================
# FETCH PORTFOLIO
# ============================================================

async def fetch_portfolio() -> PortfolioSnapshot:
    """
    Fetch latest portfolio snapshot.
    """

    async with AsyncSessionLocal() as session:

        result = await session.execute(

            text(
                """
                SELECT *
                FROM portfolio_state
                ORDER BY timestamp DESC
                LIMIT 1
                """
            )
        )

        row = result.mappings().first()


    # ========================================================
    # EXISTING PORTFOLIO
    # ========================================================

    if row:

        return PortfolioSnapshot(

            total_equity=float(
                row["total_equity"]
            ),

            margin_used=float(
                row["margin_used"]
            ),

            margin_available=float(
                row["margin_available"]
            ),

            unrealized_pnl=float(
                row["unrealized_pnl"]
            ),

            open_positions=(
                row["open_positions"]
                or []
            )
        )


    # ========================================================
    # DEFAULT PORTFOLIO
    # ========================================================

    """
    Used when system starts fresh.
    """

    return PortfolioSnapshot(

        total_equity=100000.0,

        margin_used=0.0,

        margin_available=100000.0,

        unrealized_pnl=0.0,

        open_positions=[]
    )