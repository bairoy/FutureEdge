import sys
import os
from unittest.mock import patch

# Add backend directory to sys.path so app imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.logging import setup_logging, serialize


def test_serialize():
    class MockDate:
        def strftime(self, fmt):
            return "2026-05-29T12:00:00.000"

    class MockFile:
        name = "test.py"

    class MockLevel:
        name = "INFO"

    record = {
        "time": MockDate(),
        "level": MockLevel(),
        "message": "test message",
        "file": MockFile(),
        "line": 42,
        "name": "app.core",
        "function": "test_serialize",
        "extra": {"run_id": "123"},
    }

    res = serialize(record)
    assert "timestamp" in res
    assert "test message" in res
    assert "123" in res


def test_setup_logging_dev():
    with patch("app.core.logging.settings") as mock_settings:
        mock_settings.APP_ENV = "development"
        mock_settings.LOG_LEVEL = "INFO"
        # Should execute without throwing any exception
        setup_logging()


def test_setup_logging_prod():
    with patch("app.core.logging.settings") as mock_settings:
        mock_settings.APP_ENV = "production"
        mock_settings.LOG_LEVEL = "INFO"
        # Should execute without throwing any exception
        setup_logging()
