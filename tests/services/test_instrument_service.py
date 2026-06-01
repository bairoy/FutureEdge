"""
tests/services/test_instrument_service.py
=========================================
Unit tests for the instrument search and preloading service.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json

from app.services.instrument_service import (
    load_instruments,
    search_instruments,
    get_instrument_token,
    _mock_instruments,
)


@pytest.mark.asyncio
async def test_search_instruments_empty_query():
    """Empty or too short queries should return empty results."""
    assert await search_instruments("") == []
    assert await search_instruments("A") == []


@pytest.mark.asyncio
@patch("app.services.instrument_service.load_instruments")
async def test_search_instruments_relevance_sorting(mock_load):
    """Verify that exact matches sort first, then prefix, then partial."""
    mock_load.return_value = [
        {"tradingsymbol": "TATASTEEL", "name": "TATA STEEL LTD"},
        {"tradingsymbol": "TATAMOTORS", "name": "TATA MOTORS LTD"},
        {"tradingsymbol": "SBIN", "name": "TATA SBIN PARTNERS"},
        {"tradingsymbol": "TATA", "name": "TATA GROUP INDEX"},
    ]

    results = await search_instruments("TATA")
    assert len(results) == 4
    assert results[0]["tradingsymbol"] == "TATA"
    assert results[1]["tradingsymbol"] in ("TATASTEEL", "TATAMOTORS")
    assert results[2]["tradingsymbol"] in ("TATASTEEL", "TATAMOTORS")
    assert results[3]["tradingsymbol"] == "SBIN"


@pytest.mark.asyncio
@patch("app.services.instrument_service.redis_client")
@patch("app.services.instrument_service._load_from_zerodha")
@patch("app.services.instrument_service.settings")
async def test_load_instruments_cache_hit(mock_settings, mock_load_zerodha, mock_redis):
    """If cache is present, load from cache and do not call broker."""
    mock_redis.get = AsyncMock(return_value=json.dumps([{"tradingsymbol": "INFY", "instrument_token": 123}]))
    mock_settings.ACTIVE_BROKER = "zerodha"

    res = await load_instruments("NSE")
    assert len(res) == 1
    assert res[0]["tradingsymbol"] == "INFY"
    mock_redis.get.assert_called_once()
    mock_load_zerodha.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.instrument_service.redis_client")
@patch("app.services.instrument_service._fetch_public_instruments")
@patch("app.services.instrument_service.settings")
async def test_load_instruments_fallback_to_public(mock_settings, mock_fetch_public, mock_redis):
    """When broker is not connected, it must fall back to downloading public instruments."""
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_settings.ACTIVE_BROKER = "zerodha"
    
    mock_broker_inst = MagicMock()
    mock_broker_inst.connect = AsyncMock()
    mock_broker_inst.is_connected = AsyncMock(return_value=False)
    
    mock_fetch_public.return_value = [{"tradingsymbol": "RELIANCE", "instrument_token": 738561}]

    with patch("app.brokers.zerodha.ZerodhaBroker", return_value=mock_broker_inst):
        res = await load_instruments("NSE")
        assert len(res) == 1
        assert res[0]["tradingsymbol"] == "RELIANCE"
        mock_fetch_public.assert_called_once_with("NSE")


@pytest.mark.asyncio
@patch("app.services.instrument_service.redis_client")
@patch("app.services.instrument_service._fetch_public_instruments")
@patch("app.services.instrument_service.settings")
async def test_load_instruments_fallback_to_mock(mock_settings, mock_fetch_public, mock_redis):
    """When public download also fails, it must use the mock fallback list."""
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_settings.ACTIVE_BROKER = "mock"
    mock_fetch_public.side_effect = Exception("Network offline")

    res = await load_instruments("NSE")
    # Verify it returned the mock list
    assert len(res) == len(_mock_instruments())
    assert res[0]["tradingsymbol"] == "NIFTY 50"


@pytest.mark.asyncio
@patch("app.services.instrument_service.load_instruments")
async def test_get_instrument_token(mock_load):
    """Verify dynamic token resolution by symbol name."""
    mock_load.return_value = [
        {"tradingsymbol": "INFY", "instrument_token": 408065},
        {"tradingsymbol": "RELIANCE", "instrument_token": 738561},
    ]

    assert await get_instrument_token("INFY") == 408065
    assert await get_instrument_token("RELIANCE") == 738561
    assert await get_instrument_token("TATASTEEL") is None


@pytest.mark.asyncio
@patch("app.services.instrument_service.load_instruments")
async def test_get_instrument_token_index_fallback(mock_load):
    """Verify index token fallback resolution without calling load_instruments."""
    mock_load.return_value = []
    
    assert await get_instrument_token("NIFTY 50") == 256265
    assert await get_instrument_token("BANKNIFTY") == 260105
    assert await get_instrument_token("NIFTY FIN SERVICE") == 257801
    assert await get_instrument_token("INDIAVIX") == 264337
    mock_load.assert_not_called()
