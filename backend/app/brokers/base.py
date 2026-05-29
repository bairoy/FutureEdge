"""
app/brokers/base.py
====================

Abstract broker interface + broker factory.

PURPOSE:
--------
This module defines the standard interface that ALL brokers
must implement.

Your trading engine talks ONLY to BrokerBase.

This allows you to switch between:
    - Mock broker
    - Zerodha
    - Binance
    - Interactive Brokers
    - Future brokers

without changing the strategy engine.

ARCHITECTURE:
-------------
Strategy / Agents
        ↓
BrokerBase interface
        ↓
--------------------------------
| MockBroker                  |
| ZerodhaBroker               |
| Future brokers...           |
--------------------------------
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.core.config import settings


# ============================================================
# STANDARDIZED ORDER RESPONSE
# ============================================================

@dataclass
class OrderResult:
    """
    Standardized order result returned by all brokers.

    Different brokers return different response formats.
    We normalize everything into this structure.
    """

    success: bool
    order_id: str
    fill_price: float
    quantity: float
    status: str

    # optional fields
    error_message: str | None = None
    raw_response: dict[str, Any] | None = None


# ============================================================
# ABSTRACT BROKER INTERFACE
# ============================================================

class BrokerBase(ABC):
    """
    Abstract broker interface.

    Every broker MUST implement all methods below.
    """

    # --------------------------------------------------------
    # CONNECTION MANAGEMENT
    # --------------------------------------------------------

    @abstractmethod
    async def connect(self) -> bool:
        """
        Connect to broker API.

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Disconnect from broker API.
        """
        pass

    @abstractmethod
    async def is_connected(self) -> bool:
        """
        Check whether broker is connected.
        """
        pass

    # --------------------------------------------------------
    # ACCOUNT INFO
    # --------------------------------------------------------

    @abstractmethod
    async def get_account(self) -> dict:
        """
        Get account information.

        Example:
        {
            "total_equity": 100000,
            "margin_used": 5000,
            "margin_available": 95000,
            "unrealized_pnl": 1200,
        }
        """
        pass

    @abstractmethod
    async def get_positions(self) -> list[dict]:
        """
        Get open positions.

        Example:
        [
            {
                "symbol": "RELIANCE",
                "quantity": 10,
                "avg_price": 2500,
                "pnl": 350,
                "notional": 25000,
            }
        ]
        """
        pass

    # --------------------------------------------------------
    # ORDER MANAGEMENT
    # --------------------------------------------------------

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        order_type: str = "MARKET",
        price: float | None = None,
    ) -> OrderResult:
        """
        Place an order.

        Args:
            symbol:
                Trading symbol

            direction:
                LONG or SHORT

            quantity:
                Number of shares/contracts

            order_type:
                MARKET or LIMIT

            price:
                Optional limit price

        Returns:
            OrderResult
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Returns:
            True if cancelled successfully
        """
        pass

    # --------------------------------------------------------
    # MARKET DATA
    # --------------------------------------------------------

    @abstractmethod
    async def get_ltp(self, symbol: str) -> float:
        """
        Get latest traded price (LTP).

        Example:
            2485.50
        """
        pass


_broker_instance = None

def get_broker() -> BrokerBase:
    """
    Return active broker implementation
    based on ACTIVE_BROKER in .env

    Example:
        ACTIVE_BROKER=mock
        ACTIVE_BROKER=zerodha
    """
    global _broker_instance
    if _broker_instance is not None:
        return _broker_instance

    broker_name = settings.ACTIVE_BROKER.lower()

    # --------------------------------------------------------
    # MOCK BROKER
    # --------------------------------------------------------

    if broker_name == "mock":
        from app.brokers.mock import MockBroker
        _broker_instance = MockBroker()

    # --------------------------------------------------------
    # ZERODHA BROKER
    # --------------------------------------------------------

    elif broker_name == "zerodha":
        from app.brokers.zerodha import ZerodhaBroker
        _broker_instance = ZerodhaBroker()

    # --------------------------------------------------------
    # UNKNOWN BROKER
    # --------------------------------------------------------

    else:
        raise ValueError(
            f"Unsupported broker: {broker_name}"
        )

    return _broker_instance