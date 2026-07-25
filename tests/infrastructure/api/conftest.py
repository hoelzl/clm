"""Shared helpers for the Worker API tests.

Every ``/api/worker`` route requires a bearer token (S2), so a ``TestClient``
built against a bare app gets 401s that read like unrelated failures. These
helpers make the authenticated case the easy one to write, and leave the
unauthenticated case something a test has to ask for deliberately — which is
exactly what ``test_worker_api_auth.py`` does.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from clm.infrastructure.api.server import WorkerApiServer


def auth_headers(server: WorkerApiServer) -> dict[str, str]:
    """Return the Authorization header carrying ``server``'s token."""
    return {"Authorization": f"Bearer {server.token}"}


def authed_client(server: WorkerApiServer, **kwargs: Any) -> TestClient:
    """Return a ``TestClient`` for ``server`` that presents its token.

    Use as a context manager, exactly like ``TestClient(app)``.
    """
    return TestClient(server._create_app(), headers=auth_headers(server), **kwargs)
