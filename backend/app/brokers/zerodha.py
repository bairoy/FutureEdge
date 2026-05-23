"""
app/brokers/zerodha.py
=======================
Zerodha Kite Connect broker adapter for NSE/BSE trading.

SETUP REQUIRED:
---------------
1. Create an app at https://kite.trade/
2. Get your API key and API secret
3. Add to .env:
       ZERODHA_API_KEY=your_key
       ZERODHA_API_SECRET=your_secret
       ZERODHA_ACCESS_TOKEN=your_daily_token

HOW ZERODHA AUTHENTICATION WORKS:
-----------------------------------
Zerodha uses a 2-step daily login:
  Step 1: User visits login URL, approves on Zerodha site
  Step 2: Zerodha redirects with a "request_token"
  Step 3: You exchange request_token → access_token

Access tokens expire at 6 AM IST every day.
For production, you need a daily token refresh mechanism.

We use kiteconnect library (pip install kiteconnect).

INSTRUMENT TOKENS:
------------------
Zerodha identifies instruments by integer tokens, not symbols.
Common tokens for Indian markets:
  NIFTY 50  → 256265
  NIFTY BANK → 260105
  RELIANCE  → 738561
  INFY      → 408065
  TCS       → 2953217

Download full list: kite.instruments("NSE")
"""

from loguru import logger

from app.brokers.base import BrokerBase, OrderResult
from app.core.config import settings


class ZerodhaBroker(BrokerBase):
    """
    Zerodha Kite Connect broker adapter.

    Wraps the kiteconnect Python library to implement
    the standard BrokerBase interface.
    """

    def __init__(self):
        self._kite = None           # KiteConnect client
        self._connected = False

    # --------------------------------------------------------
    # CONNECT
    # --------------------------------------------------------

    async def connect(self) -> bool:
        """
        Initialise the Kite client with the access token.

        IMPORTANT: This runs synchronously because kiteconnect
        is not async. It is fast (just setting up an HTTP client)
        so it is acceptable to call it at startup.
        """

        try:
            # kiteconnect is not an async library — we import and
            # call it directly. The actual HTTP calls are fast enough
            # that they do not block the event loop noticeably.
            from kiteconnect import KiteConnect

            self._kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
            self._kite.set_access_token(settings.ZERODHA_ACCESS_TOKEN)

            # Quick connectivity check — fetch profile
            profile = self._kite.profile()

            self._connected = True

            logger.info(
                f"✅ ZerodhaBroker connected | "
                f"user={profile.get('user_name', 'unknown')}"
            )

            return True

        except Exception as e:
            logger.error(f"❌ ZerodhaBroker connection failed: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        self._connected = False
        self._kite = None
        logger.info("👋 ZerodhaBroker disconnected")

    async def is_connected(self) -> bool:
        return self._connected and self._kite is not None

    # --------------------------------------------------------
    # ACCOUNT INFO
    # --------------------------------------------------------

    async def get_account(self) -> dict:
        """
        Fetch margins and fund details from Zerodha.

        Zerodha returns margins separately for equity and commodity.
        We look at equity segment here.
        """

        if not self._connected:
            raise RuntimeError("ZerodhaBroker not connected")

        try:
            margins = self._kite.margins()
            equity  = margins.get("equity", {})

            return {
                "total_equity":     equity.get("net", 0.0),
                "margin_used":      equity.get("utilised", {}).get("debits", 0.0),
                "margin_available": equity.get("available", {}).get("live_balance", 0.0),
                "unrealized_pnl":   0.0,   # fetched separately from positions
            }

        except Exception as e:
            logger.error(f"ZerodhaBroker.get_account failed: {e}")
            return {
                "total_equity": 0.0, "margin_used": 0.0,
                "margin_available": 0.0, "unrealized_pnl": 0.0,
            }

    async def get_positions(self) -> list[dict]:
        """
        Fetch all open intraday (day) positions from Zerodha.

        Zerodha returns day positions (intraday) and net positions.
        For an intraday strategy we care about "day" positions.
        """

        if not self._connected:
            raise RuntimeError("ZerodhaBroker not connected")

        try:
            raw = self._kite.positions()
            day_positions = raw.get("day", [])

            return [
                {
                    "symbol":    pos.get("tradingsymbol", ""),
                    "quantity":  pos.get("quantity", 0),
                    "avg_price": pos.get("average_price", 0.0),
                    "pnl":       pos.get("pnl", 0.0),
                    "notional":  abs(pos.get("quantity", 0)) * pos.get("average_price", 0.0),
                }
                for pos in day_positions
                if pos.get("quantity", 0) != 0   # skip flat positions
            ]

        except Exception as e:
            logger.error(f"ZerodhaBroker.get_positions failed: {e}")
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
        Place an order on NSE via Zerodha Kite.

        DIRECTION MAPPING:
          "LONG"  → BUY  (you expect price to go up)
          "SHORT" → SELL (you expect price to go down)

        ORDER TYPES:
          "MARKET" → executed immediately at current price
          "LIMIT"  → executed only when price hits your limit

        EXCHANGE:
          NSE → National Stock Exchange (equities)
          NFO → NSE Futures & Options segment
        """

        if not self._connected:
            return OrderResult(
                success=False, order_id="", fill_price=0.0,
                quantity=0.0, status="ERROR",
                error_message="Broker not connected"
            )

        # Map our internal direction to Zerodha's transaction_type
        transaction_type = (
            self._kite.TRANSACTION_TYPE_BUY
            if direction == "LONG"
            else self._kite.TRANSACTION_TYPE_SELL
        )

        # Map our order type to Zerodha's format
        kite_order_type = (
            self._kite.ORDER_TYPE_MARKET
            if order_type == "MARKET"
            else self._kite.ORDER_TYPE_LIMIT
        )

        try:
            order_id = self._kite.place_order(
                variety          = self._kite.VARIETY_REGULAR,
                exchange         = self._kite.EXCHANGE_NSE,
                tradingsymbol    = symbol,
                transaction_type = transaction_type,
                quantity         = int(quantity),
                product          = self._kite.PRODUCT_MIS,   # MIS = intraday
                order_type       = kite_order_type,
                price            = price,    # None for MARKET orders
            )

            logger.info(
                f"✅ ZerodhaBroker | ORDER PLACED | "
                f"{direction} {quantity} {symbol} | order_id={order_id}"
            )

            # Fetch the actual fill price after placement
            fill_price = await self.get_ltp(symbol)

            return OrderResult(
                success      = True,
                order_id     = str(order_id),
                fill_price   = fill_price,
                quantity     = quantity,
                status       = "COMPLETE",
                raw_response = {"order_id": order_id, "symbol": symbol},
            )

        except Exception as e:
            logger.error(f"❌ ZerodhaBroker.place_order failed: {e}")
            return OrderResult(
                success       = False,
                order_id      = "",
                fill_price    = 0.0,
                quantity      = 0.0,
                status        = "ERROR",
                error_message = str(e),
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending Zerodha order."""

        if not self._connected:
            return False

        try:
            self._kite.cancel_order(
                variety=self._kite.VARIETY_REGULAR,
                order_id=order_id,
            )
            logger.info(f"✅ ZerodhaBroker | CANCELLED order={order_id}")
            return True

        except Exception as e:
            logger.error(f"❌ ZerodhaBroker.cancel_order failed: {e}")
            return False

    # --------------------------------------------------------
    # LIVE PRICE
    # --------------------------------------------------------

    async def get_ltp(self, symbol: str) -> float:
        """
        Get the Last Traded Price (LTP) for a symbol.

        We pass the full exchange:symbol format to Zerodha.
        Example: "NSE:RELIANCE"
        """

        if not self._connected:
            return 0.0

        try:
            data = self._kite.ltp(f"NSE:{symbol}")
            return data[f"NSE:{symbol}"]["last_price"]

        except Exception as e:
            logger.error(f"ZerodhaBroker.get_ltp failed for {symbol}: {e}")
            return 0.0