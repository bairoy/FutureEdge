"""
Market Service

Responsibilities:
-----------------
1. Fetch live market data from Redis
2. Build MarketContext object
3. Calculate volatility metrics
4. Prepare agent-ready market state
"""

# ============================================================
# IMPORTS
# ============================================================

from app.db.redis import redis_client

from graph.state import MarketContext


# ============================================================
# FETCH MARKET CONTEXT
# ============================================================

async def fetch_market_context(
    symbol: str
) -> MarketContext:
    """
    Fetch latest market data
    from Redis streams.
    """

    # ========================================================
    # REDIS STREAM KEY
    # ========================================================

    stream_key = f"price.tick.{symbol}"


    # ========================================================
    # FETCH RECENT TICKS
    # ========================================================

    """
    xrevrange():
    fetch newest messages first.
    """

    messages = await redis_client.xrevrange(
        stream_key,
        count=100
    )


    # ========================================================
    # NO DATA SAFETY
    # ========================================================

    if not messages:

        return MarketContext(
            symbol=symbol,
            current_price=0.0
        )


    # ========================================================
    # BUILD OHLCV
    # ========================================================

    ohlcv_1m = []

    prices = []


    for _, data in messages:

        price = float(
            data.get("price", 0)
        )

        prices.append(price)

        ohlcv_1m.append({

            "timestamp": data.get(
                "timestamp"
            ),

            "close": price,

            "volume": float(
                data.get("quantity", 0)
            )
        })


    # ========================================================
    # CURRENT PRICE
    # ========================================================

    current_price = (
        prices[0]
        if prices
        else 0.0
    )


    # ========================================================
    # VOLATILITY CALCULATION
    # ========================================================

    """
    Simplified realized volatility.
    """

    volatility = 0.0

    if len(prices) > 1:

        returns = [

            (
                prices[i]
                - prices[i + 1]
            ) / prices[i + 1]

            for i in range(
                min(len(prices) - 1, 100)
            )
        ]


        if returns:

            volatility = (

                sum(r ** 2 for r in returns)

                / len(returns)
            )


    # ========================================================
    # BUILD MARKET CONTEXT
    # ========================================================

    return MarketContext(

        symbol=symbol,

        current_price=current_price,

        ohlcv_1m=ohlcv_1m[:60],

        ohlcv_5m=[],

        recent_news=[],

        macro_indicators={},

        regime=(
            "TRENDING"
            if volatility > 0.02
            else "RANGING"
        ),

        volatility_24h=round(
            volatility,
            4
        )
    )