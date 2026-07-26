"""Browser containment for ``clm serve`` (S6 + D4 of the 2026-07-24 review).

Three separate holes are pinned here, because closing any one of them alone
leaves the dashboard reachable:

- **CORS**: the default was ``allow_origins=["*"]`` *with*
  ``allow_credentials=True``, which makes Starlette echo whichever ``Origin``
  asked — strictly worse than a literal ``*``, since it legalises credentialed
  cross-origin reads.
- **Host**: no ``TrustedHostMiddleware``, so a DNS-rebinding page reached the
  app as a genuinely same-origin caller.
- **WebSocket**: ``/ws`` is CORS-exempt by protocol, so a cross-origin page
  could open it where the equivalent ``fetch`` was impossible, and then
  subscribe to any channel name it liked.

The Studio token half of ``/ws`` lives in ``studio/test_ws_auth.py``, which has
the fixtures to build a Studio-enabled app.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from clm.web.api.websocket import KNOWN_CHANNELS, WS_POLICY_VIOLATION, ws_manager


@pytest.fixture()
def app(tmp_path):
    from clm.web.app import create_app

    return create_app(tmp_path / "jobs.db", allowed_hosts=["testserver"])


@pytest.fixture(autouse=True)
def _no_leaked_connections():
    """Fail loudly if a test leaves a connection on the module-global manager.

    ``ws_manager`` is a module global, so a refused handshake that nonetheless
    registered would otherwise leak into the next test instead of failing this
    one — and "did the server accept?" is exactly what these tests assert.
    """
    ws_manager.active_connections.clear()
    ws_manager.subscriptions.clear()
    yield
    ws_manager.active_connections.clear()
    ws_manager.subscriptions.clear()


class TestHostAllowlist:
    """The Host header must name this server — this is the anti-rebinding guard."""

    def test_unknown_host_is_refused(self, app):
        client = TestClient(app, base_url="http://evil.example")
        r = client.get("/api/health")
        assert r.status_code == 400
        assert "Invalid host header" in r.text

    # ``[::1]`` is deliberately absent: Starlette's own TestClient splits the
    # netloc as ``netloc.split(":", 1)`` and dies on a bracketed IPv6 literal,
    # so it cannot express the request. The server side of that case is
    # covered directly in ``tests/infrastructure/test_web_security.py``.
    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
    def test_loopback_hosts_are_accepted(self, tmp_path, host):
        from clm.web.app import create_app

        client = TestClient(create_app(tmp_path / "jobs.db"), base_url=f"http://{host}:8000")
        assert client.get("/api/health").status_code == 200

    def test_operator_named_host_is_accepted(self, tmp_path):
        from clm.web.app import create_app

        app = create_app(tmp_path / "jobs.db", allowed_hosts=["box.tail1234.ts.net"])
        client = TestClient(app, base_url="http://box.tail1234.ts.net")
        assert client.get("/api/health").status_code == 200

    def test_bind_host_is_folded_into_the_allowlist(self, tmp_path):
        """A deliberate ``--host 192.168.1.5`` must not 400 its own address."""
        from clm.web.app import create_app

        app = create_app(tmp_path / "jobs.db", host="192.168.1.5")
        client = TestClient(app, base_url="http://192.168.1.5:8000")
        assert client.get("/api/health").status_code == 200

    def test_wildcard_disables_the_check(self, tmp_path):
        from clm.web.app import create_app

        app = create_app(tmp_path / "jobs.db", allowed_hosts=["*"])
        client = TestClient(app, base_url="http://anything.example")
        assert client.get("/api/health").status_code == 200


class TestWebSocketOriginGuard:
    """WebSockets bypass CORS, so the guard has to sit on the handshake."""

    def test_cross_origin_handshake_is_refused(self, app):
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws", headers={"Origin": "https://evil.example"}):
                pass
        assert exc.value.code == WS_POLICY_VIOLATION
        # The refusal must precede accept(): if the server had accepted, the
        # connection would be registered even though it was closed after.
        assert ws_manager.active_connections == set()

    def test_cross_site_fetch_metadata_is_refused(self, app):
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws", headers={"Sec-Fetch-Site": "cross-site"}):
                pass
        assert exc.value.code == WS_POLICY_VIOLATION
        assert ws_manager.active_connections == set()

    def test_rebound_host_is_refused_even_though_same_origin(self, app):
        """The rebinding case: Origin and Host agree, and both are the attacker's.

        An origin check alone cannot catch this — the page genuinely *is*
        same-origin once DNS points ``evil.example`` at 127.0.0.1. Only the
        host allowlist closes it.
        """
        client = TestClient(app, base_url="http://evil.example")
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws", headers={"Origin": "http://evil.example"}):
                pass
        assert exc.value.code == WS_POLICY_VIOLATION
        assert ws_manager.active_connections == set()

    def test_same_origin_handshake_is_accepted(self, app):
        client = TestClient(app)
        with client.websocket_connect(
            "/ws",
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        ) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}


class TestChannelAllowlist:
    """``subscribe`` used to store whatever name it was handed."""

    def test_unknown_channel_is_not_subscribed(self, app):
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "channels": ["jobs", "../../etc/passwd"]})
            assert ws.receive_json() == {"type": "subscribed", "channels": ["jobs"]}

    def test_reply_reports_what_was_subscribed_not_what_was_asked(self, app):
        """A typo must not read as success and then go silent forever."""
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "channels": ["jobss"]})
            assert ws.receive_json() == {"type": "subscribed", "channels": []}

    def test_known_channels_are_pinned(self):
        """``KNOWN_CHANNELS`` is the subscription control — pin it both ways.

        Widening it is what would let a client subscribe to something new, so
        the set should not change without a test changing with it.
        """
        assert KNOWN_CHANNELS == {"status", "workers", "jobs", "studio"}

    def test_every_broadcast_channel_is_subscribable(self):
        """A channel nothing can subscribe to is a broadcast into the void.

        Complements the pin above by reading the *call sites*: the two lists
        must agree, and the pin alone would not notice a new broadcast.
        """
        import re
        from pathlib import Path

        import clm.web as web_pkg

        broadcast = set()
        for path in Path(web_pkg.__file__).parent.rglob("*.py"):
            # Skip the module that defines KNOWN_CHANNELS: its own
            # `channel="status"` would keep the non-empty guard below satisfied
            # even if the pattern stopped matching everywhere else, quietly
            # turning this test vacuous.
            if path.name == "websocket.py":
                continue
            source = path.read_text(encoding="utf-8")
            broadcast.update(re.findall(r"""channel=["']([a-z_]+)["']""", source))
            # The studio sites go through a module constant rather than a
            # literal, so pick that up too.
            broadcast.update(re.findall(r"""^[A-Z_]*CHANNEL = ["']([a-z_]+)["']""", source, re.M))

        assert broadcast, "found no broadcast channels — did the pattern stop matching?"
        assert broadcast <= KNOWN_CHANNELS, (
            f"not subscribable: {sorted(broadcast - KNOWN_CHANNELS)}"
        )


class TestAllowedOrigins:
    """``--allowed-origin`` must actually authorize a browser on that origin."""

    def test_allowlisted_origin_may_open_ws_cross_site(self, tmp_path):
        from clm.web.app import create_app

        app = create_app(
            tmp_path / "jobs.db",
            allowed_hosts=["testserver"],
            allowed_origins=["https://front.example"],
        )
        client = TestClient(app)
        with client.websocket_connect(
            "/ws",
            headers={"Origin": "https://front.example", "Sec-Fetch-Site": "cross-site"},
        ) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_cors_origin_also_authorizes_driving(self, tmp_path):
        """The folding `--cors-origin` performs is not decorative.

        Without it the operator gets a successful preflight and a 403 on the
        request behind it, which is the confusing dead end the flag exists to
        avoid.
        """
        from clm.web.app import create_app

        app = create_app(
            tmp_path / "jobs.db",
            allowed_hosts=["testserver"],
            cors_origins=["https://front.example"],
        )
        client = TestClient(app)
        with client.websocket_connect(
            "/ws",
            headers={"Origin": "https://front.example", "Sec-Fetch-Site": "cross-site"},
        ) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_unlisted_origin_is_still_refused(self, tmp_path):
        from clm.web.app import create_app

        app = create_app(
            tmp_path / "jobs.db",
            allowed_hosts=["testserver"],
            allowed_origins=["https://front.example"],
        )
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws",
                headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
            ):
                pass
        assert ws_manager.active_connections == set()

    def test_unusable_cors_origin_is_warned_about(self, tmp_path, caplog):
        """A scheme-less origin matches nothing in either consumer."""
        import logging

        from clm.web.app import create_app

        with caplog.at_level(logging.WARNING, logger="clm.web.app"):
            create_app(tmp_path / "jobs.db", cors_origins=["localhost:3000"])

        assert "not a valid origin" in caplog.text

    def test_trailing_slash_cors_origin_is_warned_about(self, tmp_path, caplog):
        """It widens the guard but never matches what a browser sends."""
        import logging

        from clm.web.app import create_app

        with caplog.at_level(logging.WARNING, logger="clm.web.app"):
            create_app(tmp_path / "jobs.db", cors_origins=["https://x.example/"])

        assert "does not match what a browser sends" in caplog.text


class TestNoStudioNoToken:
    def test_ws_is_open_when_no_studio_token_is_configured(self, app):
        """Without ``--spec`` there is no token concept — the guards are the gate.

        This mirrors ``clm recordings serve``: the monitoring surface is
        entirely unauthenticated (every ``/api`` route is a plain GET), so
        token-gating only the WebSocket would be theatre. Pinned so the
        asymmetry is a decision rather than an accident.
        """
        assert app.state.__dict__.get("studio_token") is None
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_an_offered_token_subprotocol_is_still_echoed(self, app):
        """RFC 6455: a client that offered protocols and got none selected fails.

        A Studio PWA cached on a phone keeps offering ``clm-token.<stale>``. If
        a plain ``clm serve`` accepts it while selecting nothing, the browser
        closes the connection the instant it opens — and the client's reconnect
        loop makes that permanent, with no diagnostic on either side.
        """
        offered = "clm-token.whatever"
        client = TestClient(app)
        with client.websocket_connect("/ws", subprotocols=[offered]) as ws:
            assert ws.accepted_subprotocol == offered


class TestSecurityHeadersOnServe:
    """``clm serve`` sends the CSP that backstops the Studio's sanitizer (#705)."""

    def test_api_responses_carry_the_policy(self, app):
        r = TestClient(app).get("/api/health")
        assert r.status_code == 200
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_swagger_ui_is_exempt(self, app):
        """FastAPI's /docs needs CDN assets and inline script; it is static."""
        r = TestClient(app).get("/docs")
        assert r.status_code == 200
        assert "content-security-policy" not in r.headers
