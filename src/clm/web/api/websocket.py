"""WebSocket endpoint for real-time updates.

**Why this file has an auth check at all.** WebSockets are exempt from CORS,
so a cross-origin page can open one where it could not have made the
equivalent ``fetch``. Two things stop that here:

- the origin and Host guards in :mod:`clm.infrastructure.web_security`, which
  apply to the handshake as ASGI middleware — this is the containment the
  whole dashboard gets; and
- the Studio bearer token, required *before* :meth:`WebSocket.accept` whenever
  ``clm serve --spec`` configured one.

The second exists because ``/ws`` carries the ``studio`` channel, which
broadcasts deck-change and sync-progress events for the course being served.
Without a check here, anyone who can reach the port could subscribe and
receive them, walking straight around the bearer token that
:mod:`clm.web.studio.auth` calls "the real access gate". Checking after
``accept()`` would be too late in the sense that matters: the connection would
already exist and the client would already be able to send.

**How the browser presents the token.** The ``WebSocket`` constructor cannot
set an ``Authorization`` header, so the Studio PWA passes it as a
subprotocol — ``clm-token.<token>`` — which is the standard workaround and,
unlike ``?token=``, keeps the secret out of the server's access log. A
non-browser client may use ``Authorization: Bearer`` instead. The accepted
subprotocol is echoed back, because a browser fails the connection if the
server accepts without selecting one of the offered protocols.
"""

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from clm.web.services.monitor_service import MonitorService
from clm.web.studio.auth import tokens_match

logger = logging.getLogger(__name__)

#: Channels a client may subscribe to. An unknown name used to be accepted
#: verbatim, which made the subscription set attacker-controlled and unbounded;
#: worse, it hid typos, since a client subscribed to ``"jobss"`` simply never
#: heard anything. ``studio`` is only ever broadcast on when ``--spec`` is set.
KNOWN_CHANNELS = frozenset({"status", "workers", "jobs", "studio"})

#: Prefix of the ``Sec-WebSocket-Protocol`` value carrying the Studio token.
TOKEN_SUBPROTOCOL_PREFIX = "clm-token."

#: Close code for a refused handshake (RFC 6455 policy violation), matching
#: what the origin/Host guards use so an unauthorized client learns no more
#: from one refusal than from the other.
WS_POLICY_VIOLATION = 1008


class WebSocketManager:
    """Manage WebSocket connections and broadcast updates."""

    def __init__(self):
        """Initialize WebSocket manager."""
        self.active_connections: set[WebSocket] = set()
        self.subscriptions: dict[WebSocket, set[str]] = {}

    async def connect(self, websocket: WebSocket, subprotocol: str | None = None):
        """Accept new WebSocket connection.

        Args:
            websocket: WebSocket connection
            subprotocol: Subprotocol to select in the handshake response. Must
                echo one the client offered, or the browser drops the
                connection immediately after the server accepts it.
        """
        await websocket.accept(subprotocol=subprotocol)
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

    async def subscribe(self, websocket: WebSocket, channels: list[str]) -> list[str]:
        """Subscribe connection to the known channels among ``channels``.

        Args:
            websocket: WebSocket connection
            channels: Requested channel names. Anything outside
                :data:`KNOWN_CHANNELS` is dropped rather than stored.

        Returns:
            The channel names actually subscribed to, in the order requested.
        """
        accepted = [c for c in channels if c in KNOWN_CHANNELS]
        rejected = [c for c in channels if c not in KNOWN_CHANNELS]
        if rejected:
            logger.warning("Ignoring subscription to unknown channel(s): %s", rejected)
        if websocket in self.subscriptions:
            self.subscriptions[websocket].update(accepted)
            logger.debug(f"Client subscribed to: {accepted}")
        return accepted

    async def broadcast(self, message: dict, channel: str | None = None):
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


def offered_subprotocols(websocket: WebSocket) -> list[str]:
    """Return the subprotocols the client offered, in order.

    Starlette exposes the raw header rather than a parsed list, and the header
    is comma-separated with optional whitespace.
    """
    raw = websocket.headers.get("sec-websocket-protocol", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _token_subprotocol(websocket: WebSocket) -> str | None:
    """Return the offered ``clm-token.*`` subprotocol, if the client sent one.

    Echoed back on accept even when no token is required: RFC 6455 says a
    client that offered protocols and got none selected must fail the
    connection, so a Studio PWA pointed at a plain ``clm serve`` would
    otherwise reconnect forever against a server that kept "accepting" it.
    """
    for offered in offered_subprotocols(websocket):
        if offered.startswith(TOKEN_SUBPROTOCOL_PREFIX):
            return offered
    return None


def _presented_tokens(websocket: WebSocket) -> list[str]:
    """Return every credential the handshake carries, best-first.

    Both are returned rather than the first one found: a debugging client that
    copies the PWA's subprotocol list *and* sets ``Authorization`` should not
    be refused because the stale half was checked and the valid half ignored.
    """
    candidates: list[str] = []
    subprotocol = _token_subprotocol(websocket)
    if subprotocol:
        token = subprotocol[len(TOKEN_SUBPROTOCOL_PREFIX) :]
        if token:
            candidates.append(token)

    auth = websocket.headers.get("authorization", "")
    scheme, _, param = auth.partition(" ")
    if scheme.lower() == "bearer" and param.strip():
        candidates.append(param.strip())
    return candidates


async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates.

    Clients can subscribe to the channels in :data:`KNOWN_CHANNELS`. When the
    server was started with ``--spec``, the handshake must carry the Studio
    token (see the module docstring) or it is closed without being accepted.
    """
    state = websocket.app.state
    # Gate on Studio being *enabled*, not on a token being configured — the
    # same condition `studio.routes.require_token` uses. Keying off the token
    # alone would fail open in the one state that matters: a Studio app built
    # without a token, whose disk watcher is broadcasting on `studio` anyway.
    studio_enabled = getattr(state, "studio_service", None) is not None
    subprotocol = _token_subprotocol(websocket)

    if studio_enabled:
        expected = getattr(state, "studio_token", None)
        presented = _presented_tokens(websocket)
        if not expected or not any(tokens_match(token, expected) for token in presented):
            logger.warning(
                "Refused a /ws handshake with %s Studio token.",
                "an invalid" if presented else "no",
            )
            # Close without accepting: the connection never exists, so the
            # client cannot send on it. uvicorn turns this into an HTTP 403.
            await websocket.close(code=WS_POLICY_VIOLATION)
            return

    await ws_manager.connect(websocket, subprotocol=subprotocol)

    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_json()

            # Handle subscription
            if data.get("action") == "subscribe":
                channels = data.get("channels", [])
                accepted = await ws_manager.subscribe(websocket, channels)
                # Report what was actually subscribed to, not what was asked
                # for: echoing the request back made a typo look like a
                # success and then go quiet forever.
                await websocket.send_json({"type": "subscribed", "channels": accepted})

            # Handle ping
            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        ws_manager.disconnect(websocket)
