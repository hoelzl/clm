"""Regression tests for the Worker API's authentication and bind policy (S2).

The review found the Worker API bound ``0.0.0.0:8765`` with zero ``Depends``
on any route, and its executed-notebook endpoint round-tripped pickles. Two
attack positions followed: anyone on the LAN, and any web page the developer
had open (a ``no-cors`` POST with a simple content type needs no preflight,
and the handler ignored content type).

Each test here corresponds to one of those, and each fails against the
pre-fix code:

- routes answered without a token          → :class:`TestEveryRouteRequiresToken`
- the default bind reached every interface → :class:`TestBindPolicy`
- a cross-origin POST could not be blocked → :class:`TestBrowserVector`
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clm.infrastructure.api import binding
from clm.infrastructure.api import server as server_module
from clm.infrastructure.api.binding import (
    HOST_ENV_VAR,
    LOOPBACK_HOST,
    classify_hosts,
    resolve_bind_hosts,
)
from clm.infrastructure.api.server import WorkerApiServer
from clm.infrastructure.api.token import TOKEN_ENV_VAR
from clm.infrastructure.database.schema import init_database
from tests.infrastructure.api.conftest import auth_headers


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "workers.db"
    init_database(path)
    return path


@pytest.fixture(autouse=True)
def no_ambient_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own env out of these tests.

    ``CLM_API_TOKEN`` / ``CLM_WORKER_API_HOST`` are read by the constructor,
    so a machine that has them set for a real coordinator deployment would
    otherwise silently change what these tests assert.
    """
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(HOST_ENV_VAR, raising=False)
    # Discovery talks to the Docker daemon; pin it so results do not depend on
    # whether Docker happens to be running on the machine under test.
    monkeypatch.setattr(binding, "docker_gateway_hosts", lambda: [])


@pytest.fixture
def server(db_path: Path) -> WorkerApiServer:
    return WorkerApiServer(db_path)


#: Every route the worker router exposes, as (method, path, kwargs). The point
#: is coverage of the *router*, not of each handler's happy path — an
#: authenticated call would need seeded state, an unauthenticated one must be
#: rejected before any handler runs.
ROUTES = [
    ("POST", "/api/worker/register", {"json": {"worker_type": "notebook"}}),
    ("POST", "/api/worker/jobs/claim", {"json": {"worker_id": 1, "job_type": "notebook"}}),
    ("POST", "/api/worker/jobs/1/status", {"json": {"worker_id": 1, "status": "completed"}}),
    ("POST", "/api/worker/heartbeat", {"json": {"worker_id": 1}}),
    ("GET", "/api/worker/jobs/1/cancelled", {}),
    ("POST", "/api/worker/unregister", {"json": {"worker_id": 1}}),
    ("POST", "/api/worker/activate", {"json": {"worker_id": 1}}),
    (
        "GET",
        "/api/worker/cache/executed_notebook",
        {
            "params": {
                "input_file": "f.py",
                "content_hash": "h",
                "language": "en",
                "prog_lang": "python",
            }
        },
    ),
    (
        "POST",
        "/api/worker/cache/executed_notebook",
        {
            "params": {
                "input_file": "f.py",
                "content_hash": "h",
                "language": "en",
                "prog_lang": "python",
            },
            "content": b"anything",
        },
    ),
    (
        "POST",
        "/api/worker/cache/add",
        {"json": {"output_file": "o.html", "content_hash": "h", "result_metadata": {}}},
    ),
]


class TestEveryRouteRequiresToken:
    @pytest.mark.parametrize("method,path,kwargs", ROUTES, ids=[r[1] + ":" + r[0] for r in ROUTES])
    def test_no_token_is_401(
        self, server: WorkerApiServer, method: str, path: str, kwargs: dict
    ) -> None:
        with TestClient(server._create_app()) as client:
            response = client.request(method, path, **kwargs)
        assert response.status_code == 401, (
            f"{method} {path} answered {response.status_code} without a token"
        )

    @pytest.mark.parametrize("method,path,kwargs", ROUTES, ids=[r[1] + ":" + r[0] for r in ROUTES])
    def test_wrong_token_is_401(
        self, server: WorkerApiServer, method: str, path: str, kwargs: dict
    ) -> None:
        with TestClient(server._create_app()) as client:
            response = client.request(
                method, path, headers={"Authorization": "Bearer not-the-token"}, **kwargs
            )
        assert response.status_code == 401

    @pytest.mark.parametrize("method,path,kwargs", ROUTES, ids=[r[1] + ":" + r[0] for r in ROUTES])
    def test_correct_token_gets_past_the_gate(
        self, server: WorkerApiServer, method: str, path: str, kwargs: dict
    ) -> None:
        """The handler may still 400/404 on unseeded state — but not 401."""
        with TestClient(server._create_app(), raise_server_exceptions=False) as client:
            response = client.request(method, path, headers=auth_headers(server), **kwargs)
        assert response.status_code != 401

    def test_query_parameter_token_is_not_accepted(self, server: WorkerApiServer) -> None:
        """``?token=`` would land in access logs and process listings.

        The Studio's auth accepts one for its QR deep link; the Worker API has
        no such flow and must not.
        """
        with TestClient(server._create_app()) as client:
            response = client.post(
                "/api/worker/register",
                params={"token": server.token},
                json={"worker_type": "notebook"},
            )
        assert response.status_code == 401

    def test_health_stays_open_and_leaks_nothing(
        self, server: WorkerApiServer, db_path: Path
    ) -> None:
        """A liveness probe that needs no token, and says nothing sensitive."""
        with TestClient(server._create_app()) as client:
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert str(db_path) not in str(body)
        assert server.token not in str(body)


class TestTokenSource:
    def test_generated_per_build_and_unguessable(self, db_path: Path) -> None:
        first = WorkerApiServer(db_path).token
        second = WorkerApiServer(db_path).token
        assert first != second
        assert len(first) >= 32

    def test_env_pins_the_token(self, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TOKEN_ENV_VAR, "pinned-secret")
        assert WorkerApiServer(db_path).token == "pinned-secret"

    def test_explicit_argument_wins_over_env(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TOKEN_ENV_VAR, "from-env")
        assert WorkerApiServer(db_path, token="explicit").token == "explicit"


class TestBindPolicy:
    def test_default_is_loopback_not_all_interfaces(self, server: WorkerApiServer) -> None:
        assert server.bind_hosts == [LOOPBACK_HOST]
        assert "0.0.0.0" not in server.bind_hosts
        assert server.coordinator_mode is False

    def test_docker_gateways_are_added_to_the_default(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Linux containers reach the host via the bridge gateway, not loopback."""
        monkeypatch.setattr(binding, "docker_gateway_hosts", lambda: ["172.17.0.1"])
        server = WorkerApiServer(db_path)
        assert server.bind_hosts == [LOOPBACK_HOST, "172.17.0.1"]
        # A bridge gateway is host-local, so this is still not coordinator mode
        # and still needs no pinned token.
        assert server.coordinator_mode is False

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
    def test_wide_bind_without_token_is_refused(self, db_path: Path, host: str) -> None:
        with pytest.raises(ValueError, match="without a configured token"):
            WorkerApiServer(db_path, host=host)

    def test_wide_bind_with_token_is_coordinator_mode(self, db_path: Path) -> None:
        server = WorkerApiServer(db_path, host="0.0.0.0", token="shared-secret")
        assert server.bind_hosts == ["0.0.0.0"]
        assert server.coordinator_mode is True

    def test_env_opt_in_also_requires_a_token(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HOST_ENV_VAR, "0.0.0.0")
        with pytest.raises(ValueError, match="without a configured token"):
            WorkerApiServer(db_path)

        monkeypatch.setenv(TOKEN_ENV_VAR, "shared-secret")
        assert WorkerApiServer(db_path).coordinator_mode is True

    def test_explicit_loopback_is_not_coordinator_mode(self, db_path: Path) -> None:
        server = WorkerApiServer(db_path, host="127.0.0.1")
        assert server.coordinator_mode is False


class TestHostClassification:
    """``classify_hosts`` is the predicate the refusal rests on."""

    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
    def test_loopback_forms_are_local(self, host: str) -> None:
        assert classify_hosts([host], gateways=[]) == []

    def test_known_gateway_is_local(self) -> None:
        assert classify_hosts(["172.17.0.1"], gateways=["172.17.0.1"]) == []

    def test_unknown_address_is_exposed(self) -> None:
        assert classify_hosts(["172.17.0.1"], gateways=["172.18.0.1"]) == ["172.17.0.1"]

    def test_explicit_host_is_used_alone(self) -> None:
        """An operator who names an address means that address — we do not
        silently add loopback back in, because that would widen what they
        asked for."""
        assert resolve_bind_hosts("10.0.0.5", gateways=["172.17.0.1"]) == ["10.0.0.5"]


class TestBrowserVector:
    """The cross-origin case the review called out by name."""

    def test_simple_cross_origin_post_is_rejected(self, server: WorkerApiServer) -> None:
        """A page can send this request; it cannot send an Authorization header.

        ``text/plain`` plus no custom headers is a CORS "simple request", so a
        browser sends it without a preflight and the page never sees the
        response. Before the token it still *executed*, which is all an
        attacker needed for the cache-poisoning path.
        """
        with TestClient(server._create_app()) as client:
            response = client.post(
                "/api/worker/cache/executed_notebook",
                params={
                    "input_file": "f.py",
                    "content_hash": "h",
                    "language": "en",
                    "prog_lang": "python",
                },
                content=b"payload",
                headers={
                    "Content-Type": "text/plain",
                    "Origin": "https://evil.example",
                },
            )
        assert response.status_code == 401

    def test_no_cors_headers_are_offered(self, server: WorkerApiServer) -> None:
        """No CORS middleware means a browser cannot be talked into sending
        the Authorization header cross-origin: the preflight has no answer."""
        with TestClient(server._create_app()) as client:
            response = client.options(
                "/api/worker/register",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
        assert "access-control-allow-origin" not in response.headers


class TestLinuxGatewayWarning:
    """A loopback-only bind is complete on Docker Desktop and broken on Linux.

    Containers on Linux arrive via the bridge gateway. If discovery finds none
    — unreadable Docker socket, missing SDK, remote daemon — every job sits
    ``pending`` with nothing in the host log to explain it, so the server has
    to say so itself.
    """

    def test_warns_when_linux_has_no_gateway(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(server_module.sys, "platform", "linux")
        server = WorkerApiServer(db_path, port=0)

        with caplog.at_level("WARNING"):
            server._warn_if_containers_cannot_reach_us([object()])  # type: ignore[list-item]

        assert any("jobs will stay pending" in rec.message for rec in caplog.records)

    def test_silent_when_a_gateway_was_bound(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(server_module.sys, "platform", "linux")
        server = WorkerApiServer(db_path, port=0)

        with caplog.at_level("WARNING"):
            # Two sockets == loopback plus a gateway.
            server._warn_if_containers_cannot_reach_us([object(), object()])  # type: ignore[list-item]

        assert not caplog.records

    def test_silent_on_docker_desktop_platforms(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(server_module.sys, "platform", "win32")
        server = WorkerApiServer(db_path, port=0)

        with caplog.at_level("WARNING"):
            server._warn_if_containers_cannot_reach_us([object()])  # type: ignore[list-item]

        assert not caplog.records
