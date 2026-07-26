"""``/ws`` is inside the Studio token gate, not outside it.

The hole S6 named: ``/ws`` carries the ``studio`` channel, which broadcasts
deck-change and sync-progress events for the course being served. WebSockets
are CORS-exempt, so before this change anyone who could reach the port could
open one, subscribe to ``studio``, and read those events — walking around the
bearer token that :mod:`clm.web.studio.auth` calls "the real access gate".

The assertions here are all of the form "the server never accepted", not "the
server closed afterwards": once a connection is accepted it exists, and the
client can send on it. ``ws_manager.active_connections`` is the sentinel —
:meth:`WebSocketManager.connect` is the only caller of ``accept()``, so an
empty set proves the refusal preceded it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from clm.web.api.websocket import TOKEN_SUBPROTOCOL_PREFIX, WS_POLICY_VIOLATION, ws_manager

from .conftest import Course, make_app

TOKEN = "test-studio-token"


@pytest.fixture()
def client(course: Course) -> TestClient:
    app = make_app(course.spec_path, course.slides_dir.parent / "jobs.db", TOKEN)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_leaked_connections():
    ws_manager.active_connections.clear()
    ws_manager.subscriptions.clear()
    yield
    ws_manager.active_connections.clear()
    ws_manager.subscriptions.clear()


def _refused(client: TestClient, **kwargs) -> None:
    """Assert ``/ws`` refuses this handshake without ever accepting it."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", **kwargs):
            pass
    assert exc.value.code == WS_POLICY_VIOLATION
    assert ws_manager.active_connections == set()


class TestHandshakeIsGated:
    def test_no_token_is_refused(self, client: TestClient):
        _refused(client)

    def test_wrong_token_in_subprotocol_is_refused(self, client: TestClient):
        _refused(client, subprotocols=[f"{TOKEN_SUBPROTOCOL_PREFIX}nope"])

    def test_wrong_token_in_authorization_header_is_refused(self, client: TestClient):
        _refused(client, headers={"Authorization": "Bearer nope"})

    def test_empty_subprotocol_token_is_refused(self, client: TestClient):
        """``clm-token.`` with nothing after it must not read as "no check"."""
        _refused(client, subprotocols=[TOKEN_SUBPROTOCOL_PREFIX])

    def test_token_prefix_of_the_real_one_is_refused(self, client: TestClient):
        """A length-truncated guess must not pass a constant-time compare."""
        _refused(client, subprotocols=[f"{TOKEN_SUBPROTOCOL_PREFIX}{TOKEN[:-1]}"])

    def test_non_ascii_token_is_refused_not_crashed(self, client: TestClient):
        """``secrets.compare_digest`` raises TypeError on non-ASCII ``str``.

        Sent as raw bytes because that is what a hostile client does and what
        httpx will let us express: the header arrives latin-1 decoded, so a
        byte above 0x7F would otherwise turn a bad token into a 500 raised
        from inside the auth check itself.
        """
        _refused(client, headers={b"Authorization": b"Bearer p\xe4ssword"})

    def test_query_parameter_token_is_refused(self, client: TestClient):
        """``?token=`` is deliberately not honoured on the handshake.

        It works for the REST routes because the QR deep link needs it, but a
        WebSocket reconnects on a timer — putting the token in the URL would
        write it into the access log over and over. The PWA uses a subprotocol.
        """
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws?token={TOKEN}"):
                pass
        assert exc.value.code == WS_POLICY_VIOLATION
        assert ws_manager.active_connections == set()


class TestStudioWithoutATokenFailsClosed:
    """A Studio app built without a token must not be *more* open than with one.

    ``create_app`` takes ``spec_path`` and ``studio_token`` independently, and
    the lifespan starts the disk watcher on the spec alone — so in this state
    the ``studio`` channel is broadcasting while nothing can authenticate. The
    REST routes already fail closed here (``require_token`` rejects on
    ``not expected``); ``/ws`` gates on the same condition so the two surfaces
    cannot disagree. The CLI never produces this state; ``create_app`` is a
    public constructor that can.
    """

    @pytest.fixture()
    def tokenless(self, course: Course) -> TestClient:
        from clm.web.app import create_app

        app = create_app(
            db_path=course.slides_dir.parent / "jobs.db",
            spec_path=course.spec_path,
            allowed_hosts=["testserver"],
        )
        return TestClient(app)

    def test_rest_rejects(self, tokenless: TestClient):
        assert tokenless.get("/api/studio/decks").status_code == 401

    def test_ws_rejects_too(self, tokenless: TestClient):
        _refused(tokenless)

    def test_no_token_at_all_does_not_authenticate(self, tokenless: TestClient):
        """Presenting *some* token must not match an absent expected one."""
        _refused(tokenless, subprotocols=[f"{TOKEN_SUBPROTOCOL_PREFIX}anything"])


class TestHandshakeIsAccepted:
    def test_token_via_subprotocol_connects(self, client: TestClient):
        offered = f"{TOKEN_SUBPROTOCOL_PREFIX}{TOKEN}"
        with client.websocket_connect("/ws", subprotocols=[offered]) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_accepted_subprotocol_is_echoed(self, client: TestClient):
        """A browser drops the connection if the server selects nothing.

        The PWA offers exactly one subprotocol, so failing to echo it would
        make every Studio connection close immediately after opening.
        """
        offered = f"{TOKEN_SUBPROTOCOL_PREFIX}{TOKEN}"
        with client.websocket_connect("/ws", subprotocols=[offered]) as ws:
            assert ws.accepted_subprotocol == offered

    def test_token_via_authorization_header_connects(self, client: TestClient):
        """Non-browser clients (scripts, tests) can use the ordinary header."""
        with client.websocket_connect("/ws", headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_a_valid_subprotocol_wins_over_a_stale_one(self, client: TestClient):
        """Every offered ``clm-token.*`` is checked, not just the first.

        A phone that re-pairs without dropping its old value offers both.
        """
        with client.websocket_connect(
            "/ws",
            subprotocols=[f"{TOKEN_SUBPROTOCOL_PREFIX}stale", f"{TOKEN_SUBPROTOCOL_PREFIX}{TOKEN}"],
        ) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_a_valid_header_wins_over_a_stale_subprotocol(self, client: TestClient):
        """Checking only the first credential found would refuse a valid one.

        A debugging client that copies the PWA's subprotocol list and *also*
        sets ``Authorization`` should authenticate on the good half.
        """
        with client.websocket_connect(
            "/ws",
            subprotocols=[f"{TOKEN_SUBPROTOCOL_PREFIX}stale"],
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_authenticated_client_may_subscribe_to_studio(self, client: TestClient):
        offered = f"{TOKEN_SUBPROTOCOL_PREFIX}{TOKEN}"
        with client.websocket_connect("/ws", subprotocols=[offered]) as ws:
            ws.send_json({"action": "subscribe", "channels": ["studio"]})
            assert ws.receive_json() == {"type": "subscribed", "channels": ["studio"]}


class TestStudioRoutesAreOriginGuarded:
    """The HTTP half: a cross-origin page must not drive a Studio write."""

    def test_cross_origin_post_is_refused(self, client: TestClient, course: Course):
        r = client.post(
            "/api/studio/deck/render-cell",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Origin": "https://evil.example",
            },
            json={"deck_id": course.deck_id, "body": "# hi", "is_j2": False},
        )
        assert r.status_code == 403
        assert "cross-origin" in r.text

    def test_same_origin_post_still_works(self, client: TestClient, course: Course):
        r = client.post(
            "/api/studio/deck/render-cell",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Origin": "http://testserver",
            },
            json={"deck_id": course.deck_id, "body": "# hi", "is_j2": False},
        )
        assert r.status_code == 200
