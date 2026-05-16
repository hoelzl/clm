"""Shared fixtures for worker infrastructure tests.

The WorkerPoolManager starts a real uvicorn Worker API server on a fixed port
(8765) whenever any of its workers run in Docker mode. That is fine in
production but catastrophic under pytest-xdist: multiple parallel test
processes all race to bind the same port, producing flaky
``OSError: [WinError 10048]`` failures and stray ``SystemExit`` exceptions in
the uvicorn server thread. The extra CPU/IO load also starves unrelated
timing-sensitive tests (e.g. the threaded worker tests in test_worker_base.py).

Tests in this directory do not actually exercise the HTTP surface — Docker is
mocked out — so we replace ``start_worker_api_server`` with a harmless
MagicMock for every test. ``tests/infrastructure/api/test_worker_routes.py``
has its own directory and uses FastAPI's in-process TestClient, so it is
unaffected.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_worker_api_server(monkeypatch):
    """Prevent pool_manager tests from binding the real Worker API port.

    We patch the symbol imported into ``pool_manager`` (not the one in
    ``clm.infrastructure.api.server``) because pool_manager binds the
    name at import time via ``from ... import start_worker_api_server``.
    The returned mock satisfies the attributes the pool manager touches
    (``is_running``, ``docker_url``, ``stop()``).
    """
    fake_server = MagicMock(name="FakeWorkerApiServer")
    fake_server.is_running = True
    fake_server.docker_url = "http://host.docker.internal:8765"
    fake_server.url = "http://0.0.0.0:8765"

    def _fake_start(db_path, timeout: float = 5.0):
        return fake_server

    monkeypatch.setattr(
        "clm.infrastructure.workers.pool_manager.start_worker_api_server",
        _fake_start,
    )
    yield fake_server
