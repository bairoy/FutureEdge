import sys
import os
import pytest

# Add backend directory to sys.path so app imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from unittest.mock import patch
from app.main import health


@pytest.mark.asyncio
@patch("app.main.check_postgres")
@patch("app.main.check_redis")
@patch("app.main.check_qdrant")
async def test_health_all_ok(mock_qdrant, mock_redis, mock_postgres):
    mock_postgres.return_value = True
    mock_redis.return_value = True
    mock_qdrant.return_value = True

    response = await health()
    assert response["status"] == "ok"
    assert response["dependencies"]["postgres"] == "ok"
    assert response["dependencies"]["redis"] == "ok"
    assert response["dependencies"]["qdrant"] == "ok"


@pytest.mark.asyncio
@patch("app.main.check_postgres")
@patch("app.main.check_redis")
@patch("app.main.check_qdrant")
async def test_health_degraded(mock_qdrant, mock_redis, mock_postgres):
    mock_postgres.return_value = False
    mock_redis.return_value = True
    mock_qdrant.return_value = True

    response = await health()
    assert response["status"] == "degraded"
    assert response["dependencies"]["postgres"] == "down"
    assert response["dependencies"]["redis"] == "ok"
    assert response["dependencies"]["qdrant"] == "ok"
