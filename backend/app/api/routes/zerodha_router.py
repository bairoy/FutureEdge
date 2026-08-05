import os
from urllib.parse import quote, parse_qs
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.redis import redis_client, KEY_ZERODHA_ACCESS_TOKEN
from app.auth.dependencies import get_db, require_viewer, require_risk_manager
from app.db.models.user import User
from app.services.oauth_state import issue_state, consume_state
from app.services.token_manager import encrypt_token

router = APIRouter()


def update_env_file(key: str, value: str):
    """
    Helper to find and update a key in the .env file.
    Tries multiple potential locations to support local dev and Docker.
    """
    paths = [
        ".env",                     # Current working dir
        "../.env",                  # Parent of current working dir
        "../../.env",               # Two levels up
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.env")), # Root from route
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.env")),  # Backend root from route
    ]
    
    env_path = None
    for p in paths:
        if os.path.exists(p):
            env_path = p
            break
            
    if not env_path:
        env_path = ".env"  # Default fallback
        
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
            
    updated = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            updated = True
        else:
            new_lines.append(line)
            
    if not updated:
        new_lines.append(f"\n{key}={value}\n")
        
    with open(env_path, "w") as f:
        f.writelines(new_lines)
        
    logger.info(f"Updated {key} in {env_path}")


@router.get("/auth/zerodha/login-url")
async def get_login_url(
    current_user: User = Depends(require_risk_manager),
):
    """
    Returns the official Zerodha login/consent page URL.

    Mints a single-use CSRF `state` bound to the calling user and threads it
    through the login URL so `zerodha_callback` can prove the callback it
    receives belongs to a login *we* started. See `services/oauth_state.py`
    for why the callback relies on this instead of a JWT dependency.

    Kite round-trips custom query params via `redirect_params`, so the state
    comes back to us appended to the registered redirect URL.
    """
    state = await issue_state(user_id=str(current_user.id))

    login_url = (
        f"https://kite.trade/connect/login?v=3&api_key={settings.ZERODHA_API_KEY}"
        f"&redirect_params={quote(f'state={state}', safe='')}"
    )
    return {
        "login_url": login_url,
        "is_configured": bool(settings.ZERODHA_API_KEY and settings.ZERODHA_API_SECRET)
    }


@router.get("/auth/zerodha/status")
async def get_zerodha_status(
    current_user: User = Depends(require_risk_manager),
):
    """
    Returns status of the active broker and whether we are connected.
    """
    from app.brokers.base import get_broker
    broker = get_broker()
    is_zerodha = settings.ACTIVE_BROKER.lower() == "zerodha"
    connected = False
    
    if is_zerodha:
        try:
            connected = await broker.is_connected()
        except Exception as e:
            logger.warning(f"Failed to check broker connection: {e}")
            connected = False
            
    return {
        "active_broker": settings.ACTIVE_BROKER,
        "is_zerodha": is_zerodha,
        "connected": connected
    }


class BrokerSelectRequest(BaseModel):
    broker: str  # "mock" or "zerodha"


@router.post("/auth/broker/select", summary="Set the active broker dynamically")
async def select_broker(
    req: BrokerSelectRequest,
    current_user: User = Depends(require_risk_manager),
):
    broker_name = req.broker.lower()
    if broker_name not in ["mock", "zerodha"]:
        raise HTTPException(status_code=400, detail="Invalid broker. Must be 'mock' or 'zerodha'.")
    
    settings.ACTIVE_BROKER = broker_name
    
    try:
        update_env_file("ACTIVE_BROKER", broker_name)
        logger.info(f"Updated ACTIVE_BROKER in .env to: {broker_name}")
    except Exception as e:
        logger.error(f"Failed to update ACTIVE_BROKER in .env file: {e}")

    logger.info(f"Dynamically switched ACTIVE_BROKER to: {broker_name}")
    
    from app.brokers.base import get_broker
    broker = get_broker()
    is_zerodha = broker_name == "zerodha"
    connected = False
    if is_zerodha:
        try:
            connected = await broker.is_connected()
        except Exception as e:
            logger.warning(f"Failed to check broker connection: {e}")
            connected = False
            
    return {
        "active_broker": settings.ACTIVE_BROKER,
        "is_zerodha": is_zerodha,
        "connected": connected
    }


def _error_page(title: str, message: str, status_code: int) -> HTMLResponse:
    """Render the shared dark-themed failure card used by the callback route."""
    html = f"""
        <html>
            <head>
                <title>{title}</title>
                <style>
                    body {{ background-color: #0b0f19; color: #ef4444; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                    .card {{ background-color: #111827; border: 1px solid #1f2937; border-radius: 1rem; padding: 2.5rem; text-align: center; max-width: 400px; }}
                    h1 {{ margin-bottom: 1rem; }}
                    p {{ color: #9ca3af; margin-bottom: 1.5rem; }}
                    .btn {{ background-color: #ef4444; color: white; text-decoration: none; padding: 0.75rem 1.5rem; border-radius: 0.5rem; font-weight: bold; }}
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>{title}</h1>
                    <p>{message}</p>
                    <a href="{settings.FRONTEND_URL}/dashboard" class="btn">Back to Dashboard</a>
                </div>
            </body>
        </html>
    """
    return HTMLResponse(content=html, status_code=status_code)


def _extract_state(request: Request) -> str | None:
    """
    Pull the CSRF state off the callback URL.

    Kite echoes custom params supplied via `redirect_params`, and depending on
    the app's redirect configuration they arrive either flattened onto the
    query string (`?state=...`) or still packed inside `redirect_params`.
    Accept both rather than depending on one Kite behaviour.
    """
    state = request.query_params.get("state")
    if state:
        return state

    packed = request.query_params.get("redirect_params")
    if packed:
        return parse_qs(packed).get("state", [None])[0]

    return None


@router.get("/auth/zerodha/callback", response_class=HTMLResponse)
async def zerodha_callback(request: Request):
    """
    OAuth-style callback redirect route.
    Zerodha redirects here with ?request_token=XXXX.
    Exchanges request_token for access_token, saves it, and redirects back to frontend.

    NOTE ON AUTH: this route deliberately has no `Depends(require_*)` — it is
    reached by a browser redirect from Zerodha, which carries no Authorization
    header. The single-use `state` minted by `login-url` (an admin-only route)
    is what authenticates it. Verify state BEFORE `generate_session()`, so an
    unsolicited callback never spends our API secret.
    """
    state = _extract_state(request)
    issuing_user_id = await consume_state(state)
    if issuing_user_id is None:
        logger.warning(
            "Rejected Zerodha callback: missing/invalid/expired OAuth state "
            f"(state_present={bool(state)})"
        )
        return _error_page(
            "Authentication Failed",
            "This login link is invalid, already used, or expired. "
            "Start the connection again from the dashboard.",
            403,
        )

    logger.info(f"Zerodha callback state verified | issued_by_user={issuing_user_id}")

    request_token = request.query_params.get("request_token")
    if not request_token:
        return _error_page(
            "Authentication Failed",
            "No request token was provided in the callback query parameters.",
            400,
        )

    try:
        from kiteconnect import KiteConnect

        # 1. Exchange request_token for access_token
        kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
        data = kite.generate_session(request_token, api_secret=settings.ZERODHA_API_SECRET)
        access_token = data["access_token"]

        # The token never leaves this function in plaintext: both storage
        # sinks below get the AES-256-GCM blob, and brokers/zerodha.py
        # decrypts on read. Read access to Redis or the JSON file is no
        # longer equivalent to full broker access.
        encrypted_token = encrypt_token(access_token)

        # 2. Save access token to Redis (for fast, non-restart updates)
        await redis_client.set(KEY_ZERODHA_ACCESS_TOKEN, encrypted_token)

        # 3. Save to local JSON config (for persistence without triggering uvicorn reload loops)
        import json
        try:
            with open("broker_token.json", "w") as f:
                json.dump({"ZERODHA_ACCESS_TOKEN": encrypted_token}, f)
            logger.info("Saved encrypted Zerodha token to broker_token.json")
        except Exception as je:
            logger.warning(f"Failed to save Zerodha token to broker_token.json: {je}")

        # 4. Attempt to reconnect the broker immediately
        from app.brokers.base import get_broker
        broker = get_broker()
        await broker.disconnect()
        await broker.connect()

        # Reconnect live tick publisher to use the new token
        from app.data.feed import tick_publisher
        try:
            tick_publisher.stop()
            tick_publisher.start()
            logger.info("Live tick publisher restarted with new Zerodha session")
        except Exception as fe:
            logger.warning(f"Failed to restart live tick publisher: {fe}")

        # 5. Return success HTML with JavaScript auto-redirect back to frontend dashboard
        success_html = """
        <html>
            <head>
                <title>Authentication Successful</title>
                <style>
                    body { background-color: #0b0f19; color: #f3f4f6; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background-color: #111827; border: 1px solid #1f2937; border-radius: 1rem; padding: 2.5rem; text-align: center; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5); max-width: 400px; }
                    h1 { color: #10b981; margin-bottom: 1rem; }
                    p { color: #9ca3af; margin-bottom: 1.5rem; line-height: 1.5; }
                    .btn { background-color: #2563eb; color: white; text-decoration: none; padding: 0.75rem 1.5rem; border-radius: 0.5rem; font-weight: bold; display: inline-block; }
                </style>
                <script>
                    setTimeout(function() {
                        window.location.href = "http://localhost:3000/dashboard?zerodha=success";
                    }, 2500);
                </script>
            </head>
            <body>
                <div class="card">
                    <h1>Connection Successful!</h1>
                    <p>Your Zerodha account has been authenticated. Live trading sessions are now enabled.</p>
                    <p>Redirecting you back to your dashboard...</p>
                    <a href="http://localhost:3000/dashboard?zerodha=success" class="btn">Return to Dashboard</a>
                </div>
            </body>
        </html>
        """
        return HTMLResponse(content=success_html.replace("http://localhost:3000", settings.FRONTEND_URL))

    except Exception as e:
        # The exception text stays server-side. This handler wraps the
        # session exchange and the token writes, so `e` can carry request/
        # response fragments — not something to render into a page that
        # sits in browser history.
        logger.exception(f"Failed to generate Zerodha access token: {e}")
        return _error_page(
            "Authentication Failed",
            "An error occurred while exchanging the request token. "
            "Check the backend logs for details.",
            500,
        )


class BrokerPositionExitRequest(BaseModel):
    symbol: str


@router.post("/api/v1/broker/positions/exit", summary="Exit/close an active position directly on the broker")
async def exit_broker_position(
    req: BrokerPositionExitRequest,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """
    Exits/closes a position directly at the broker level.
    Determines the current position direction and quantity,
    places a counter-order, and closes any corresponding DB trade records.
    """
    import json
    from sqlalchemy import select
    symbol = req.symbol.upper()
    
    # 1. Fetch current positions from the active broker to determine direction and quantity
    from app.brokers.base import get_broker
    broker = get_broker()
    
    try:
        is_connected = await broker.is_connected()
        if not is_connected:
            await broker.connect()
            
        positions = await broker.get_positions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch active positions from broker: {e}")
        
    # Find position for the target symbol
    target_pos = None
    for pos in positions:
        if pos.get("symbol", "").upper() == symbol:
            target_pos = pos
            break
            
    if not target_pos or target_pos.get("quantity", 0) == 0:
        # Check if there's an open trade in the DB we should close anyway
        from app.db.repos.trade_repo import TradeRepo
        from app.db.models.trade import Trade
        
        result = await db.execute(
            select(Trade).where(
                Trade.user_id == current_user.id,
                Trade.symbol == symbol,
                Trade.status == "OPEN",
                Trade.broker == settings.ACTIVE_BROKER
            )
        )
        open_trades = result.scalars().all()
        for t in open_trades:
            await TradeRepo.close_trade(db, t.id, current_user.id, t.entry_price)
            
        return {"success": True, "message": f"No active position on broker for {symbol}. Closed open database records."}
        
    qty = abs(target_pos["quantity"])
    direction = "LONG" if target_pos["quantity"] > 0 else "SHORT"
    exit_direction = "SHORT" if direction == "LONG" else "LONG"
    
    # Get current price
    try:
        current_price = await broker.get_ltp(symbol)
    except Exception:
        current_price = target_pos.get("avg_price", 0.0)
        
    if current_price <= 0:
        current_price = target_pos.get("avg_price", 0.0)
        
    # Place exit order (opposite of current direction)
    actual_exit = current_price
    try:
        price_buffer = current_price * 0.0005
        limit_price = round(current_price - price_buffer if exit_direction == "SHORT" else current_price + price_buffer, 2)
        
        order_result = await broker.place_order(
            symbol=symbol,
            direction=exit_direction,
            quantity=float(qty),
            order_type="LIMIT",
            price=limit_price,
        )
        if order_result.success:
            actual_exit = order_result.fill_price or current_price
        else:
            raise Exception(order_result.error_message)
    except Exception as e:
        logger.error(f"Manual broker-level exit failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to place exit order on broker: {e}")
        
    # Close any corresponding trade records in DB
    from app.db.repos.trade_repo import TradeRepo
    from app.db.models.trade import Trade
    
    result = await db.execute(
        select(Trade).where(
            Trade.user_id == current_user.id,
            Trade.symbol == symbol,
            Trade.status == "OPEN",
            Trade.broker == settings.ACTIVE_BROKER
        )
    )
    open_trades = result.scalars().all()
    closed_trades = []
    
    for t in open_trades:
        closed = await TradeRepo.close_trade(db, t.id, current_user.id, actual_exit)
        if closed:
            closed_trades.append(closed.id)
            
            # Publish to Redis
            from app.db.redis import redis_client, CHANNEL_TRADE_EXECUTED
            try:
                await redis_client.publish(
                    CHANNEL_TRADE_EXECUTED,
                    json.dumps({
                        "event":     "TRADE_CLOSED",
                        "reason":    "MANUAL_EXIT",
                        "trade_id":  t.id,
                        "user_id":   t.user_id,
                        "symbol":    t.symbol,
                        "direction": t.direction,
                        "entry":     t.entry_price,
                        "exit":      actual_exit,
                        "pnl":       closed.realized_pnl,
                        "pnl_pct":   closed.pnl_pct,
                    }),
                )
            except Exception:
                pass
                
    return {
        "success": True,
        "message": f"Exited {direction} position of {qty} shares for {symbol} @ ₹{actual_exit:.2f}",
        "closed_trade_ids": closed_trades
    }


@router.get("/api/v1/broker/portfolio")
async def get_broker_portfolio(
    mock: bool = False,
    paper: bool = False,
    current_user: User = Depends(require_viewer),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns live account margins and positions from the active broker.
    If mock=True is passed, or if the active broker is not connected/fails,
    it falls back to mock portfolio details for easy local UI testing.
    If paper=True is passed, it calculates simulated paper trading margins and positions from the database.
    """
    from app.brokers.base import get_broker
    from app.brokers.mock import MockBroker
    from sqlalchemy import select

    if paper:
        from app.brokers.paper import calculate_paper_portfolio_data
        return await calculate_paper_portfolio_data(db, current_user.id)

    if mock:
        try:
            mock_broker = MockBroker()
            account = await mock_broker.get_account()
            positions = await mock_broker.get_positions()
            
            # Enrich mock positions with trade_id
            from app.db.models.trade import Trade
            result = await db.execute(
                select(Trade).where(
                    Trade.user_id == current_user.id,
                    Trade.status == "OPEN",
                    Trade.broker == "mock"
                )
            )
            open_db_trades = result.scalars().all()
            symbol_to_trade = {t.symbol.upper(): t for t in open_db_trades}
            for pos in positions:
                sym = pos.get("symbol", "").upper()
                trade = symbol_to_trade.get(sym)
                if trade:
                    pos["trade_id"] = trade.id

            return {
                "connected": False,
                "mock_data": True,
                "account": account,
                "positions": positions
            }
        except Exception as me:
            logger.error(f"Failed to fetch mock broker portfolio: {me}")
            return {
                "connected": False,
                "mock_data": False,
                "account": {"total_equity": 0.0, "margin_used": 0.0, "margin_available": 0.0, "unrealized_pnl": 0.0},
                "positions": [],
                "error": str(me)
            }

    broker = get_broker()
    try:
        # Check connection status first
        is_connected = await broker.is_connected()
        if not is_connected:
            await broker.connect()
            
        account = await broker.get_account()
        positions = await broker.get_positions()
        
        is_mock = settings.ACTIVE_BROKER.lower() == "mock"
        
        # Enrich positions with trade_id from database
        from app.db.models.trade import Trade
        active_broker_name = "mock" if is_mock else settings.ACTIVE_BROKER.lower()
        result = await db.execute(
            select(Trade).where(
                Trade.user_id == current_user.id,
                Trade.status == "OPEN",
                Trade.broker == active_broker_name
            )
        )
        open_db_trades = result.scalars().all()
        symbol_to_trade = {t.symbol.upper(): t for t in open_db_trades}
        
        for pos in positions:
            sym = pos.get("symbol", "").upper()
            trade = symbol_to_trade.get(sym)
            if trade:
                pos["trade_id"] = trade.id

        return {
            "connected": not is_mock,
            "mock_data": is_mock,
            "account": account,
            "positions": positions
        }
    except Exception as e:
        logger.error(f"Failed to fetch broker portfolio: {e} - falling back to mock portfolio for testing")
        try:
            mock_broker = MockBroker()
            account = await mock_broker.get_account()
            positions = await mock_broker.get_positions()
            return {
                "connected": False,
                "mock_data": True,
                "account": account,
                "positions": positions,
                "error": str(e)
            }
        except Exception as me:
            logger.error(f"Failed to load mock broker fallback: {me}")
            return {
                "connected": False,
                "mock_data": False,
                "account": {
                    "total_equity": 0.0,
                    "margin_used": 0.0,
                    "margin_available": 0.0,
                    "unrealized_pnl": 0.0
                },
                "positions": [],
                "error": str(e)
            }