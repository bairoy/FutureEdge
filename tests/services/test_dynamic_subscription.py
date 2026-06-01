"""
tests/services/test_dynamic_subscription.py
===========================================
Unit and integration tests for dynamic token subscription and caching.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.data.feed import NSETickPublisher, get_latest_tick
from app.db.redis import redis_client


@pytest.mark.asyncio
@patch("app.data.feed.sync_redis")
async def test_publish_tick_caches_by_token(mock_sync_redis_module):
    """Verify that publishing a tick writes to the generic stream AND caches the token-specific key."""
    mock_r = MagicMock()
    
    publisher = NSETickPublisher(instrument_tokens=[408065])
    publisher._redis_sync = mock_r
    
    tick = {
        "instrument_token": 408065,
        "last_price": 1450.25,
        "ohlc": {
            "open": 1440.0,
            "high": 1460.0,
            "low": 1435.0,
            "close": 1445.0
        },
        "volume_traded": 500000,
        "timestamp": datetime(2026, 5, 31, 10, 0, 0)
    }
    
    publisher._publish_tick_sync(tick)
    
    # Check xadd and set were called on the Redis client
    mock_r.xadd.assert_called_once()
    mock_r.set.assert_called_once()
    args, kwargs = mock_r.set.call_args
    assert args[0] == "futureedge:ticks:latest:408065"
    
    cached_data = json.loads(args[1])
    assert cached_data["ltp"] == 1450.25
    assert cached_data["close"] == 1445.0
    assert cached_data["volume"] == 500000


@pytest.mark.asyncio
@patch("app.data.feed.redis_client")
@patch("app.services.instrument_service.get_instrument_token")
async def test_get_latest_tick_from_cache(mock_get_token, mock_redis):
    """get_latest_tick must fetch from token-specific cache if present."""
    mock_get_token.return_value = 738561
    
    mock_tick = {
        "ltp": 2500.0,
        "open": 2490.0,
        "high": 2510.0,
        "low": 2480.0,
        "close": 2500.0,
        "volume": 120000,
        "timestamp": "2026-05-31T10:00:00"
    }
    mock_redis.get = AsyncMock(return_value=json.dumps(mock_tick))
    mock_redis.xrevrange = AsyncMock()
    
    tick = await get_latest_tick("RELIANCE")
    assert tick is not None
    assert tick["ltp"] == 2500.0
    mock_redis.get.assert_called_once_with("futureedge:ticks:latest:738561")
    # Stream read fallback should NOT be called since cache hit succeeded
    mock_redis.xrevrange.assert_not_called()


@pytest.mark.asyncio
async def test_subscribe_tokens_on_the_fly():
    """Verify subscribe_tokens appends to internal list and calls KiteTicker methods if active."""
    publisher = NSETickPublisher(instrument_tokens=[256265])
    mock_ticker = MagicMock()
    publisher._ticker = mock_ticker
    publisher._running = True
    
    # Subscribe to new token
    publisher.subscribe_tokens([738561])
    assert 738561 in publisher._tokens
    mock_ticker.subscribe.assert_called_once_with([738561])
    mock_ticker.set_mode.assert_called_once()


@pytest.mark.asyncio
@patch("app.data.feed.redis_client")
@patch("app.services.instrument_service.get_instrument_token")
async def test_get_latest_tick_invalid_token(mock_get_token, mock_redis):
    """get_latest_tick should return None if the symbol cannot be resolved to a valid token."""
    mock_get_token.return_value = None
    mock_redis.get = AsyncMock(return_value=None)
    
    # Even if there are entries in the stream, since token is None, it should return None
    mock_redis.xrevrange = AsyncMock(return_value=[
        ("12345-0", {"token": "256265", "ltp": "22000.0"})
    ])
    
    tick = await get_latest_tick("INVALID_SYM")
    assert tick is None
