"""Tests for ``clm.infrastructure.api.server``.

Covers ``WorkerApiServer`` lifecycle (``start``, ``stop``,
``is_running``, URL properties, health route) and the module-level
singleton helpers (``start_worker_api_server``,
``stop_worker_api_server``, ``get_worker_api_server``).

Server start paths are tested in two regimes:

- **Fake uvicorn** — we monkeypatch ``uvicorn.Server`` so we can
  synchronously inject start/stop behavior, including the "failed to
  start" timeout path and the clean shutdown path, without opening a
  real socket.
- **Real uvicorn** — one integration-style test that binds the server
  to port 0 and exercises the health endpoint, proving the full
  threading/startup path works end to end.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from clm.infrastructure.api import server as server_module
from clm.infrastructure.api.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    WorkerApiServer,
    get_worker_api_server,
    start_worker_api_server,
    stop_worker_api_server,
)
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "workers.db"
    init_database(path)
    return path


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the module-level singleton is cleared around every test."""
    server_module._server_instance = None
    yield
    if server_module._server_instance is not None:
        server_module._server_instance.stop(timeout=2.0)
        server_module._server_instance = None


def _free_port() -> int:
    """Bind to port 0, read the assigned port, release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Construction, URLs, FastAPI app setup
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults_to_standard_host_port(self, db_path: Path) -> None:
        server = WorkerApiServer(db_path)
        assert server.db_path == db_path
        assert server.host == DEFAULT_HOST
        assert server.port == DEFAULT_PORT

    def test_custom_host_and_port(self, db_path: Path) -> None:
        server = WorkerApiServer(db_path, host="127.0.0.1", port=9999)
        assert server.host == "127.0.0.1"
        assert server.port == 9999

    def test_url_and_docker_url(self, db_path: Path) -> None:
        server = WorkerApiServer(db_path, host="0.0.0.0", port=8888)
        assert server.url == "http://0.0.0.0:8888"
        assert server.docker_url == "http://host.docker.internal:8888"


class TestCreateApp:
    """_create_app returns a FastAPI app wired with health + worker routes."""

    def test_health_endpoint(self, db_path: Path) -> None:
        server = WorkerApiServer(db_path)
        app = server._create_app()

        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["api_version"] == "1.0"
        assert data["database"] == str(db_path)


# ---------------------------------------------------------------------------
# Fake-uvicorn lifecycle tests
# ---------------------------------------------------------------------------


class FakeUvicornServer:
    """Tiny stand-in for ``uvicorn.Server`` that mimics its control surface.

    ``run()`` blocks until ``should_exit`` is set; this lets the test
    exercise the real threading code in ``WorkerApiServer._run_server``
    without opening a socket. Tests can flip instance flags to drive the
    "failed to start" path as well.
    """

    def __init__(self, config: object, *, fail_to_start: bool = False) -> None:
        self.config = config
        self.should_exit = False
        self.fail_to_start = fail_to_start
        self.ran = False

    def run(self) -> None:
        self.ran = True
        if self.fail_to_start:
            # Simulate crashing during startup, before the parent's
            # ``_started`` event is already set.
            return
        while not self.should_exit:
            time.sleep(0.01)


class TestStartAndStop:
    """start() spins up the thread; stop() winds it down."""

    def test_start_and_stop_cycle(self, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(server_module.uvicorn, "Server", FakeUvicornServer)

        server = WorkerApiServer(db_path, host="127.0.0.1", port=_free_port())

        assert server.start(timeout=2.0) is True
        assert server.is_running is True
        assert server._thread is not None
        assert server._thread.is_alive()

        server.stop(timeout=2.0)

        assert server.is_running is False
        assert server._thread is None
        assert server._server is None

    def test_start_is_idempotent(self, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(server_module.uvicorn, "Server", FakeUvicornServer)

        server = WorkerApiServer(db_path, host="127.0.0.1", port=_free_port())
        assert server.start(timeout=2.0) is True

        # Second start should be a no-op and return True.
        first_thread = server._thread
        assert server.start(timeout=2.0) is True
        assert server._thread is first_thread

        server.stop(timeout=2.0)

    def test_stop_when_never_started_is_noop(self, db_path: Path) -> None:
        server = WorkerApiServer(db_path)
        # No exception, no state change.
        server.stop(timeout=1.0)
        assert server._server is None

    def test_start_timeout_returns_false(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the started-event is never set, start() returns False."""

        class NeverStartsServer(FakeUvicornServer):
            pass

        monkeypatch.setattr(server_module.uvicorn, "Server", NeverStartsServer)

        # Patch _run_server to *not* set the started event.
        server = WorkerApiServer(db_path, host="127.0.0.1", port=_free_port())

        original_run = server._run_server

        def run_without_signaling() -> None:
            # Mimic the real _run_server up to the point where it would
            # set _started, but skip the set() to force the timeout.
            server._app = server._create_app()
            while not server._shutdown_requested.is_set():
                time.sleep(0.01)

        server._run_server = run_without_signaling  # type: ignore[method-assign]
        try:
            assert server.start(timeout=0.1) is False
        finally:
            server._shutdown_requested.set()
            if server._thread is not None:
                server._thread.join(timeout=1.0)
            server._run_server = original_run  # type: ignore[method-assign]


class TestIsRunning:
    def test_false_before_start(self, db_path: Path) -> None:
        server = WorkerApiServer(db_path)
        assert server.is_running is False

    def test_false_after_shutdown_requested(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even if the thread lives briefly, ``is_running`` flips false
        as soon as ``_shutdown_requested`` is set."""
        monkeypatch.setattr(server_module.uvicorn, "Server", FakeUvicornServer)

        server = WorkerApiServer(db_path, host="127.0.0.1", port=_free_port())
        server.start(timeout=2.0)
        try:
            assert server.is_running is True
            server._shutdown_requested.set()
            assert server.is_running is False
        finally:
            server.stop(timeout=2.0)


class TestStopWarnsOnStubbornThread:
    """If the worker thread refuses to die, stop() still clears state."""

    def test_unresponsive_thread_is_logged(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(server_module.uvicorn, "Server", FakeUvicornServer)

        server = WorkerApiServer(db_path, host="127.0.0.1", port=_free_port())
        server.start(timeout=2.0)

        # Swap the thread with one that ignores the shutdown signal.
        fake_thread = MagicMock(spec=threading.Thread)
        fake_thread.is_alive.return_value = True
        server._thread = fake_thread

        with caplog.at_level("WARNING"):
            server.stop(timeout=0.1)

        assert any("did not stop cleanly" in rec.message for rec in caplog.records), (
            "Expected a warning about unresponsive thread"
        )


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def test_get_worker_api_server_returns_none_initially(self) -> None:
        assert get_worker_api_server() is None

    def test_get_worker_api_server_lazy_creation(self, db_path: Path) -> None:
        server = get_worker_api_server(db_path)
        assert server is not None
        assert server.db_path == db_path

        # A second call without db_path returns the same instance.
        second = get_worker_api_server()
        assert second is server

    def test_start_worker_api_server_creates_and_returns(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server_module.uvicorn, "Server", FakeUvicornServer)

        server = start_worker_api_server(db_path, timeout=2.0)
        try:
            assert server is not None
            assert server.is_running is True
            assert get_worker_api_server() is server
        finally:
            stop_worker_api_server(timeout=2.0)

    def test_start_worker_api_server_idempotent_when_running(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server_module.uvicorn, "Server", FakeUvicornServer)

        first = start_worker_api_server(db_path, timeout=2.0)
        try:
            # Second call returns the existing running instance.
            second = start_worker_api_server(db_path, timeout=2.0)
            assert second is first
        finally:
            stop_worker_api_server(timeout=2.0)

    def test_start_worker_api_server_raises_on_failure(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force start() to return False by swapping the instance method.
        def never_start(self: WorkerApiServer, timeout: float = 5.0) -> bool:
            return False

        monkeypatch.setattr(WorkerApiServer, "start", never_start)

        with pytest.raises(RuntimeError, match="Failed to start Worker API server"):
            start_worker_api_server(db_path, timeout=0.1)

    def test_stop_worker_api_server_when_never_started(self) -> None:
        # No exception when global instance is None.
        stop_worker_api_server(timeout=1.0)


# ---------------------------------------------------------------------------
# Real-uvicorn integration smoke test
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestRealUvicornStartup:
    """Bind an actual uvicorn server; hit /health; ensure clean shutdown.

    This is the one place that exercises the real threading path
    end-to-end. It's marked ``slow`` so the fast suite skips it.
    """

    def test_real_server_serves_health(self, db_path: Path) -> None:
        port = _free_port()
        server = WorkerApiServer(db_path, host="127.0.0.1", port=port)

        assert server.start(timeout=5.0) is True
        try:
            # Poll for readiness — uvicorn's port binding happens shortly
            # after the started event is set.
            response = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    response = httpx.get(f"{server.url}/health", timeout=1.0)
                    break
                except httpx.ConnectError:
                    time.sleep(0.05)

            assert response is not None, "Server did not accept connections"
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
        finally:
            server.stop(timeout=5.0)

        assert server.is_running is False
