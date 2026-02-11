"""Unit tests for websocket module.

Tests the WebSocketManager class functionality:
- Connection management
- Subscription handling
- Broadcasting to subscribed clients
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.web.api.websocket import WebSocketManager


class TestWebSocketManagerConnection:
    """Test connection management."""

    @pytest.mark.asyncio
    async def test_connect_accepts_websocket(self):
        """Connect should accept the websocket."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        await manager.connect(mock_ws)

        mock_ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_adds_to_active_connections(self):
        """Connect should add websocket to active connections."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        await manager.connect(mock_ws)

        assert mock_ws in manager.active_connections

    @pytest.mark.asyncio
    async def test_connect_initializes_empty_subscriptions(self):
        """Connect should initialize empty subscription set."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        await manager.connect(mock_ws)

        assert mock_ws in manager.subscriptions
        assert manager.subscriptions[mock_ws] == set()

    @pytest.mark.asyncio
    async def test_connect_multiple_clients(self):
        """Should handle multiple client connections."""
        manager = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        await manager.connect(ws1)
        await manager.connect(ws2)
        await manager.connect(ws3)

        assert len(manager.active_connections) == 3
        assert ws1 in manager.active_connections
        assert ws2 in manager.active_connections
        assert ws3 in manager.active_connections


class TestWebSocketManagerDisconnection:
    """Test disconnection handling."""

    @pytest.mark.asyncio
    async def test_disconnect_removes_from_active_connections(self):
        """Disconnect should remove websocket from active connections."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        await manager.connect(mock_ws)
        manager.disconnect(mock_ws)

        assert mock_ws not in manager.active_connections

    @pytest.mark.asyncio
    async def test_disconnect_removes_subscriptions(self):
        """Disconnect should remove websocket subscriptions."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        await manager.connect(mock_ws)
        await manager.subscribe(mock_ws, ["status", "workers"])
        manager.disconnect(mock_ws)

        assert mock_ws not in manager.subscriptions

    def test_disconnect_nonexistent_websocket_is_safe(self):
        """Disconnect should handle websockets that aren't connected."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        # Should not raise
        manager.disconnect(mock_ws)

        assert mock_ws not in manager.active_connections


class TestWebSocketManagerSubscription:
    """Test subscription management."""

    @pytest.mark.asyncio
    async def test_subscribe_adds_channels(self):
        """Subscribe should add channels to websocket subscriptions."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        await manager.connect(mock_ws)
        await manager.subscribe(mock_ws, ["status", "workers"])

        assert "status" in manager.subscriptions[mock_ws]
        assert "workers" in manager.subscriptions[mock_ws]

    @pytest.mark.asyncio
    async def test_subscribe_multiple_times_adds_channels(self):
        """Multiple subscribe calls should accumulate channels."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        await manager.connect(mock_ws)
        await manager.subscribe(mock_ws, ["status"])
        await manager.subscribe(mock_ws, ["workers", "jobs"])

        subscribed = manager.subscriptions[mock_ws]
        assert "status" in subscribed
        assert "workers" in subscribed
        assert "jobs" in subscribed

    @pytest.mark.asyncio
    async def test_subscribe_unconnected_websocket_is_safe(self):
        """Subscribe should handle websockets that aren't connected."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()

        # Should not raise (websocket not in subscriptions dict)
        await manager.subscribe(mock_ws, ["status"])

        # Should not have created a subscription entry
        assert mock_ws not in manager.subscriptions


class TestWebSocketManagerBroadcast:
    """Test broadcast functionality."""

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_connections(self):
        """Broadcast without channel should send to all connections."""
        manager = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        await manager.connect(ws1)
        await manager.connect(ws2)

        message = {"type": "test", "data": "hello"}
        await manager.broadcast(message)

        ws1.send_json.assert_awaited_once_with(message)
        ws2.send_json.assert_awaited_once_with(message)

    @pytest.mark.asyncio
    async def test_broadcast_with_channel_filters_by_subscription(self):
        """Broadcast with channel should only send to subscribed clients."""
        manager = WebSocketManager()
        ws_subscribed = AsyncMock()
        ws_not_subscribed = AsyncMock()

        await manager.connect(ws_subscribed)
        await manager.connect(ws_not_subscribed)

        await manager.subscribe(ws_subscribed, ["status"])

        message = {"type": "status_update"}
        await manager.broadcast(message, channel="status")

        ws_subscribed.send_json.assert_awaited_once_with(message)
        ws_not_subscribed.send_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broadcast_handles_send_errors(self):
        """Broadcast should handle errors when sending to clients."""
        manager = WebSocketManager()
        ws_good = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = Exception("Connection closed")

        await manager.connect(ws_good)
        await manager.connect(ws_bad)

        message = {"type": "test"}
        await manager.broadcast(message)

        # Good websocket should receive message
        ws_good.send_json.assert_awaited_once_with(message)

        # Bad websocket should be disconnected
        assert ws_bad not in manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_to_empty_connections(self):
        """Broadcast should handle empty connections list."""
        manager = WebSocketManager()

        message = {"type": "test"}
        # Should not raise
        await manager.broadcast(message)


class TestWebSocketManagerPeriodicUpdates:
    """Test periodic status updates."""

    @pytest.mark.asyncio
    async def test_periodic_updates_skips_when_no_connections(self):
        """Periodic updates should skip when no clients connected."""
        manager = WebSocketManager()
        mock_service = MagicMock()

        # Run for a short time
        task = asyncio.create_task(manager.send_periodic_updates(mock_service))
        await asyncio.sleep(0.1)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # Service should not be called if no connections
        mock_service.get_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_periodic_updates_sends_status_to_subscribed(self):
        """Periodic updates should send status to subscribed clients."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()
        mock_service = MagicMock()

        # Setup mock status response
        from clm.web.models import DatabaseInfoResponse, QueueStatsResponse, StatusResponse

        mock_status = StatusResponse(
            status="healthy",
            timestamp="2025-01-01T00:00:00",
            database=DatabaseInfoResponse(
                path="/test.db",
                accessible=True,
                exists=True,
            ),
            workers={},
            queue=QueueStatsResponse(
                pending=0,
                processing=0,
                completed_last_hour=0,
                failed_last_hour=0,
            ),
        )
        mock_service.get_status.return_value = mock_status

        # Connect and subscribe
        await manager.connect(mock_ws)
        await manager.subscribe(mock_ws, ["status"])

        # Patch sleep to avoid waiting
        with patch("clm.web.api.websocket.asyncio.sleep", new_callable=AsyncMock):
            task = asyncio.create_task(manager.send_periodic_updates(mock_service))
            await asyncio.sleep(0)  # Let task start
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_periodic_updates_handles_errors(self):
        """Periodic updates should handle errors gracefully."""
        manager = WebSocketManager()
        mock_ws = AsyncMock()
        mock_service = MagicMock()
        mock_service.get_status.side_effect = Exception("Database error")

        await manager.connect(mock_ws)
        await manager.subscribe(mock_ws, ["status"])

        # Patch sleep to avoid waiting
        with patch("clm.web.api.websocket.asyncio.sleep", new_callable=AsyncMock):
            task = asyncio.create_task(manager.send_periodic_updates(mock_service))
            await asyncio.sleep(0)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should not have crashed - error was caught


class TestGlobalManagerInstance:
    """Test the global ws_manager instance."""

    def test_ws_manager_exists(self):
        """Global ws_manager should be available."""
        from clm.web.api.websocket import ws_manager

        assert ws_manager is not None
        assert isinstance(ws_manager, WebSocketManager)

    def test_ws_manager_starts_empty(self):
        """Fresh import should have empty connections."""
        # Create a new manager to test initial state
        manager = WebSocketManager()

        assert len(manager.active_connections) == 0
        assert len(manager.subscriptions) == 0
