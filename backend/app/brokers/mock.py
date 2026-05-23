"""
app/brokers/mock.py
====================
Mock broker — simulates trades locally, no real money involved.

WHEN TO USE:
------------
- During development and testing
- When ACTIVE_BROKER=mock in your .env
- Before you have Zerodha credentials set up

HOW IT WORKS:
-------------
place_order() immediately returns a simulated "COMPLETE" fill
at exactly the requested price (no slippage in mock mode).

The order ID is a random UUID so it looks realistic.
"""

import uuid
from loguru import logger

from app.brokers.base import BrokerBase, OrderResult


class MockBroker(BrokerBase):
    """
    Simulated broker for paper trading and testing.
    No real API calls are made anywhere in this class.
    """

    def __init__(self):
        self._connected = False

    # --------------------------------------------------------
    # CONNECT / DISCONNECT
    # --------------------------------------------------------

    async def connect(self) -> bool:
        self._connected = True
        logger.info("🤖 MockBroker connected (paper trading mode)")
        return True

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("🤖 MockBroker disconnected")

    async def is_connected(self) -> bool:
        return self._connected

    # --------------------------------------------------------
    # ACCOUNT INFO  (returns realistic fake data)
    # --------------------------------------------------------

    async def get_account(self) -> dict:
        return {
            "total_equity":     100000.0,
            "margin_used":      10000.0,
            "margin_available": 90000.0,
            "unrealized_pnl":   1200.0,
        }

    async def get_positions(self) -> list[dict]:
        # No real positions in mock mode
        return []

    # --------------------------------------------------------
    # ORDER PLACEMENT
    # --------------------------------------------------------

    async def place_order(
        self,
        symbol:     str,
        direction:  str,
        quantity:   float,
        order_type: str = "MARKET",
        price:      float | None = None,
    ) -> OrderResult:
        """
        Simulate placing an order.
        Always succeeds immediately at the requested price.
        """

        order_id = str(uuid.uuid4())[:8].upper()

        logger.info(
            f"🤖 MockBroker | SIMULATED ORDER | "
            f"{direction} {quantity} {symbol} "
            f"@ {price or 'MARKET'} | order_id={order_id}"
        )

        return OrderResult(
            success       = True,
            order_id      = order_id,
            fill_price    = price or 0.0,
            quantity      = quantity,
            status        = "COMPLETE",
            raw_response  = {
                "broker":    "mock",
                "order_id":  order_id,
                "symbol":    symbol,
                "direction": direction,
                "quantity":  quantity,
                "price":     price,
            }
        )

    async def cancel_order(self, order_id: str) -> bool:
        logger.info(f"🤖 MockBroker | CANCELLED order={order_id}")
        return True

    async def get_ltp(self, symbol: str) -> float:
        # Return a fake price
        logger.debug(f"🤖 MockBroker | LTP requested for {symbol} → 100.0")
        return 100.0