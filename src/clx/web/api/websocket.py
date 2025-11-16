"""WebSocket endpoint for real-time updates."""

import asyncio
import json
import logging
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect
from clx.web.services.monitor_service import MonitorService

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manage WebSocket connections and broadcast updates."""

    def __init__(self):
        """Initialize WebSocket manager."""
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: dict[WebSocket, Set[str]] = {}

    async def connect(self, websocket: WebSocket):
        """Accept new WebSocket connection.

        Args:
            websocket: WebSocket connection
        """
        await websocket.accept()
        self.active_connections.add(websocket)
        self.subscriptions[websocket] = set()
        logger.info(f"WebSocket client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection.

        Args:
            websocket: WebSocket connection
        """
        self.active_connections.discard(websocket)
        self.subscriptions.pop(websocket, None)
        logger.info(f"WebSocket client disconnected. Total: {len(self.active_connections)}")

    async def subscribe(self, websocket: WebSocket, channels: list[str]):
        """Subscribe connection to channels.

        Args:
            websocket: WebSocket connection
            channels: List of channel names (workers, jobs, status)
        """
        if websocket in self.subscriptions:
            self.subscriptions[websocket].update(channels)
            logger.debug(f"Client subscribed to: {channels}")

    async def broadcast(self, message: dict, channel: str = None):
        """Broadcast message to subscribed clients.

        Args:
            message: Message to broadcast
            channel: Optional channel filter
        """
        disconnected = set()

        for connection in self.active_connections:
            # Check if client is subscribed to this channel
            if channel and channel not in self.subscriptions.get(connection, set()):
                continue

            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to client: {e}")
                disconnected.add(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def send_periodic_updates(self, monitor_service: MonitorService):
        """Send periodic status updates to all connected clients.

        Args:
            monitor_service: Monitor service instance
        """
        while True:
            await asyncio.sleep(2)  # Update every 2 seconds

            if not self.active_connections:
                continue

            try:
                # Get current status
                status = monitor_service.get_status()

                # Broadcast to subscribed clients
                await self.broadcast(
                    {
                        "type": "status_update",
                        "data": json.loads(status.model_dump_json()),
                    },
                    channel="status",
                )
            except Exception as e:
                logger.error(f"Error sending periodic update: {e}", exc_info=True)


# Global WebSocket manager instance
ws_manager = WebSocketManager()


async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates.

    Clients can subscribe to channels: status, workers, jobs
    """
    await ws_manager.connect(websocket)

    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_json()

            # Handle subscription
            if data.get("action") == "subscribe":
                channels = data.get("channels", [])
                await ws_manager.subscribe(websocket, channels)
                await websocket.send_json(
                    {"type": "subscribed", "channels": channels}
                )

            # Handle ping
            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        ws_manager.disconnect(websocket)
