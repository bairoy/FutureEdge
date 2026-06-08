import sys
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, mock_open

# Add backend directory to sys.path so app imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings

client = TestClient(app)

@pytest.fixture
def mock_settings():
    with patch.object(settings, "TELEGRAM_CHAT_ID", "123456"):
        with patch.object(settings, "TELEGRAM_BOT_TOKEN", "mock_bot_token"):
            with patch.object(settings, "ZERODHA_API_KEY", "mock_api_key"):
                with patch.object(settings, "ZERODHA_API_SECRET", "mock_api_secret"):
                    yield

def test_telegram_webhook_ignored_without_message_or_callback(mock_settings):
    payload = {}
    response = client.post("/api/v1/telegram/webhook", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}

def test_telegram_webhook_unauthorized_chat_id(mock_settings):
    payload = {
        "message": {
            "text": "/refresh mock_token",
            "chat": {"id": 999999}  # unauthorized chat ID
        }
    }
    response = client.post("/api/v1/telegram/webhook", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "unauthorized"}

@patch("kiteconnect.KiteConnect")
@patch("app.db.redis.redis_client")
@patch("app.services.telegram_service.send_telegram_message")
@patch("app.brokers.base.get_broker")
@patch("app.data.feed.tick_publisher")
def test_telegram_webhook_refresh_command_success(
    mock_tick_publisher,
    mock_get_broker,
    mock_send_tg_message,
    mock_redis,
    mock_kite_connect,
    mock_settings
):
    # Setup mocks
    mock_kite = MagicMock()
    mock_kite.generate_session.return_value = {"access_token": "new_mock_access_token"}
    mock_kite_connect.return_value = mock_kite

    mock_broker = AsyncMock()
    mock_get_broker.return_value = mock_broker

    payload = {
        "message": {
            "text": "/refresh my_special_request_token",
            "chat": {"id": 123456}
        }
    }

    # Mock file writing for broker_token.json
    m_open = mock_open()
    with patch("builtins.open", m_open):
        response = client.post("/api/v1/telegram/webhook", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "token_refreshed"

    # Verify kite.generate_session was called with request_token
    mock_kite.generate_session.assert_called_once_with("my_special_request_token", api_secret="mock_api_secret")

    # Verify redis update
    mock_redis.set.assert_called_once()

    # Verify broker connection/disconnection
    mock_broker.disconnect.assert_called_once()
    mock_broker.connect.assert_called_once()

    # Verify tick publisher reloaded
    mock_tick_publisher.stop.assert_called_once()
    mock_tick_publisher.start.assert_called_once()

@patch("kiteconnect.KiteConnect")
@patch("app.db.redis.redis_client")
@patch("app.services.telegram_service.send_telegram_message")
@patch("app.brokers.base.get_broker")
@patch("app.data.feed.tick_publisher")
def test_telegram_webhook_refresh_command_failed(
    mock_tick_publisher,
    mock_get_broker,
    mock_send_tg_message,
    mock_redis,
    mock_kite_connect,
    mock_settings
):
    # Setup mocks to raise exception
    mock_kite = MagicMock()
    mock_kite.generate_session.side_effect = Exception("API connection timed out")
    mock_kite_connect.return_value = mock_kite

    payload = {
        "message": {
            "text": "/refresh bad_token",
            "chat": {"id": 123456}
        }
    }

    response = client.post("/api/v1/telegram/webhook", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "token_refresh_failed"
    assert "API connection timed out" in response.json()["error"]
