import sys
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# Add backend directory to sys.path so app imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.jobs.position_reconciler import PositionReconciler


@pytest.mark.asyncio
@patch("app.jobs.position_reconciler.get_broker")
@patch("app.jobs.position_reconciler.AsyncSessionLocal")
@patch("app.jobs.position_reconciler.redis_client")
async def test_reconcile_no_discrepancy(mock_redis, mock_session_cls, mock_get_broker):
    # Setup mock broker with a LONG position of 10 INFY
    mock_broker = AsyncMock()
    mock_broker.is_connected.return_value = True
    mock_broker.get_positions.return_value = [
        {"symbol": "INFY", "quantity": 10}
    ]
    mock_get_broker.return_value = mock_broker

    # Setup mock session returning 10 INFY open trade
    mock_session = MagicMock()
    mock_session_context = MagicMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_cls.return_value = mock_session_context

    mock_trade = MagicMock()
    mock_trade.symbol = "INFY"
    mock_trade.direction = "LONG"
    mock_trade.quantity = 10

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_trade]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_session.execute = AsyncMock(return_value=mock_result)

    # Run reconciler
    reconciler = PositionReconciler()
    with patch("app.jobs.position_reconciler.settings") as mock_settings:
        mock_settings.RECONCILE_AUTO_HALT = True
        with patch("app.services.kill_switch_service.activate_kill_switch") as mock_halt:
            await reconciler.reconcile()
            mock_halt.assert_not_called()
            mock_redis.publish.assert_not_called()


@pytest.mark.asyncio
@patch("app.jobs.position_reconciler.get_broker")
@patch("app.jobs.position_reconciler.AsyncSessionLocal")
@patch("app.jobs.position_reconciler.redis_client")
async def test_reconcile_with_discrepancy(mock_redis, mock_session_cls, mock_get_broker):
    # Setup mock broker with a LONG position of 10 INFY
    mock_broker = AsyncMock()
    mock_broker.is_connected.return_value = True
    mock_broker.get_positions.return_value = [
        {"symbol": "INFY", "quantity": 10}
    ]
    mock_get_broker.return_value = mock_broker

    # Setup mock session returning no open trades (so 0 expected INFY vs 10 actual)
    mock_session = MagicMock()
    mock_session_context = MagicMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_cls.return_value = mock_session_context

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_session.execute = AsyncMock(return_value=mock_result)

    # Run reconciler
    reconciler = PositionReconciler()
    with patch("app.jobs.position_reconciler.settings") as mock_settings:
        mock_settings.RECONCILE_AUTO_HALT = True
        with patch("app.services.kill_switch_service.activate_kill_switch") as mock_halt:
            await reconciler.reconcile()
            mock_halt.assert_called_once()
            mock_redis.publish.assert_called_once()
