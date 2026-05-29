import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from app.core.config import settings
from app.db.redis import redis_client, KEY_ZERODHA_ACCESS_TOKEN

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
async def get_login_url():
    """
    Returns the official Zerodha login/consent page URL.
    """
    login_url = f"https://kite.trade/connect/login?v=3&api_key={settings.ZERODHA_API_KEY}"
    return {
        "login_url": login_url,
        "is_configured": bool(settings.ZERODHA_API_KEY and settings.ZERODHA_API_SECRET)
    }


@router.get("/auth/zerodha/status")
async def get_zerodha_status():
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


@router.get("/auth/zerodha/callback", response_class=HTMLResponse)
async def zerodha_callback(request: Request):
    """
    OAuth-style callback redirect route.
    Zerodha redirects here with ?request_token=XXXX.
    Exchanges request_token for access_token, saves it, and redirects back to frontend.
    """
    request_token = request.query_params.get("request_token")
    if not request_token:
        error_html = """
        <html>
            <head>
                <title>Authentication Error</title>
                <style>
                    body { background-color: #0b0f19; color: #ef4444; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background-color: #111827; border: 1px solid #1f2937; border-radius: 1rem; padding: 2.5rem; text-align: center; max-width: 400px; }
                    h1 { margin-bottom: 1rem; }
                    p { color: #9ca3af; margin-bottom: 1.5rem; }
                    .btn { background-color: #ef4444; color: white; text-decoration: none; padding: 0.75rem 1.5rem; border-radius: 0.5rem; font-weight: bold; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>Authentication Failed</h1>
                    <p>No request token was provided in the callback query parameters.</p>
                    <a href="http://localhost:3000/dashboard" class="btn">Back to Dashboard</a>
                </div>
            </body>
        </html>
        """
        return HTMLResponse(content=error_html, status_code=400)

    try:
        from kiteconnect import KiteConnect

        # 1. Exchange request_token for access_token
        kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
        data = kite.generate_session(request_token, api_secret=settings.ZERODHA_API_SECRET)
        access_token = data["access_token"]

        # 2. Save access token to Redis (for fast, non-restart updates)
        await redis_client.set(KEY_ZERODHA_ACCESS_TOKEN, access_token)

        # 3. Save to local JSON config (for persistence without triggering uvicorn reload loops)
        import json
        try:
            with open("broker_token.json", "w") as f:
                json.dump({"ZERODHA_ACCESS_TOKEN": access_token}, f)
            logger.info("Saved Zerodha token to broker_token.json")
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
        return HTMLResponse(content=success_html)

    except Exception as e:
        logger.exception(f"Failed to generate Zerodha access token: {e}")
        fail_html = f"""
        <html>
            <head>
                <title>Authentication Failed</title>
                <style>
                    body {{ background-color: #0b0f19; color: #ef4444; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                    .card {{ background-color: #111827; border: 1px solid #1f2937; border-radius: 1rem; padding: 2.5rem; text-align: center; max-width: 400px; }}
                    h1 {{ margin-bottom: 1rem; }}
                    p {{ color: #9ca3af; margin-bottom: 1.5rem; line-height: 1.5; }}
                    .btn {{ background-color: #ef4444; color: white; text-decoration: none; padding: 0.75rem 1.5rem; border-radius: 0.5rem; font-weight: bold; }}
                    .error {{ color: #f87171; font-family: monospace; font-size: 0.85rem; background: #1e1b4b; padding: 0.5rem; border-radius: 0.25rem; word-break: break-all; }}
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>Authentication Failed</h1>
                    <p>An error occurred while exchanging the request token.</p>
                    <p class="error">{str(e)}</p>
                    <br/>
                    <a href="http://localhost:3000/dashboard" class="btn">Back to Dashboard</a>
                </div>
            </body>
        </html>
        """
        return HTMLResponse(content=fail_html, status_code=500)


@router.get("/api/v1/broker/portfolio")
async def get_broker_portfolio(mock: bool = False):
    """
    Returns live account margins and positions from the active broker.
    If mock=True is passed, or if the active broker is not connected/fails,
    it falls back to mock portfolio details for easy local UI testing.
    """
    from app.brokers.base import get_broker
    from app.brokers.mock import MockBroker

    if mock:
        try:
            mock_broker = MockBroker()
            account = await mock_broker.get_account()
            positions = await mock_broker.get_positions()
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