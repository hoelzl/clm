"""Shared fixtures for worker infrastructure tests.

The WorkerPoolManager starts a real uvicorn Worker API server on a fixed port
(8765) whenever any of its workers run in Docker mode. That is fine in
production but catastrophic under pytest-xdist for the *mocked* tests:
multiple parallel test processes all race to bind the same port, producing
flaky ``OSError: [WinError 10048]`` failures and stray ``SystemExit``
exceptions in the uvicorn server thread, and the extra CPU/IO load starves
unrelated timing-sensitive tests (e.g. the threaded worker tests in
test_worker_base.py).

The autouse fixture below replaces ``start_worker_api_server`` with a
harmless ``MagicMock`` — but only for tests that *don't* exercise real
Docker. Tests carrying ``@pytest.mark.docker`` (the live container
integration suite) need the real uvicorn server so the in-container worker
can call back to the host, so the fixture short-circuits for them.
``tests/infrastructure/api/test_worker_routes.py`` has its own directory
and uses FastAPI's in-process TestClient, so it is unaffected either way.

Because the server is faked, the module-level accessor
``get_worker_api_endpoint`` would return ``None`` — and ``DockerWorkerExecutor``
refuses to start a container it can give neither a URL nor a token. The fixture
therefore stands in both, so their *absence* stays a real failure the dedicated
test in ``test_worker_executor.py`` asserts, rather than the background state of
every mocked test.
"""

from unittest.mock import MagicMock

import pytest

#: Stand-in for the per-build Worker API token. Tests that assert the token
#: reaches a container compare against this.
FAKE_API_TOKEN = "test-worker-api-token"

#: Stand-in for the running server's container-facing URL. Deliberately *not*
#: on the default port: the executor must take the port from the server that
#: actually bound it, so a test asserting this URL fails if anyone reintroduces
#: a hardcoded ``DEFAULT_PORT``.
FAKE_API_URL = "http://host.docker.internal:54321"


@pytest.fixture(autouse=True)
def _mock_worker_api_server(request, monkeypatch):
    """Prevent pool_manager tests from binding the real Worker API port.

    We patch the name in ``clm.infrastructure.api.server``: since #802 A12
    pool_manager imports ``start_worker_api_server`` lazily at *call* time
    (the server stack lives in the ``[docker]`` extra), so the patched
    module attribute is exactly what it resolves. The returned mock
    satisfies the attributes the pool manager touches (``is_running``,
    ``docker_url``, ``stop()``).

    Tests marked ``@pytest.mark.docker`` opt out: they run real
    containers that call back into a real uvicorn server, so swapping it
    for a MagicMock would silently fail every Docker integration job.
    """
    if "docker" in request.keywords:
        yield None
        return

    fake_server = MagicMock(name="FakeWorkerApiServer")
    fake_server.is_running = True
    fake_server.docker_url = FAKE_API_URL
    fake_server.url = "http://127.0.0.1:54321"
    fake_server.token = FAKE_API_TOKEN

    def _fake_start(db_path, timeout: float = 5.0, **kwargs):
        return fake_server

    monkeypatch.setattr(
        "clm.infrastructure.api.server.start_worker_api_server",
        _fake_start,
    )
    # ``DockerWorkerExecutor.start_worker`` imports this at call time from
    # ``clm.infrastructure.api.server``, so patch it there.
    monkeypatch.setattr(
        "clm.infrastructure.api.server.get_worker_api_endpoint",
        lambda: (FAKE_API_URL, FAKE_API_TOKEN),
    )
    yield fake_server
