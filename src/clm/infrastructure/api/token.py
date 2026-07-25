"""The Worker API's shared secret: its name and how one is minted.

Deliberately dependency-free. Both sides of the Worker API need this, and the
two sides do not have the same packages installed: the host runs FastAPI,
while worker container images carry only what a worker needs. Putting the
constant here — rather than next to the server-side enforcement in
:mod:`clm.infrastructure.api.auth`, which imports FastAPI — is what lets
:mod:`clm.infrastructure.api.client` learn the variable name inside a
container.

``tests/infrastructure/workers/test_worker_import_surface.py`` pins that
property, because the failure mode is invisible outside a container: a worker
that cannot import its client dies before claiming a job, and the build just
shows jobs stuck in ``pending``.
"""

from __future__ import annotations

import secrets

#: Environment variable carrying the Worker API token.
#:
#: Read on both sides: on the host it *pins* the token, which coordinator mode
#: requires (see :class:`~clm.infrastructure.api.server.WorkerApiServer`); in a
#: worker it is the token to present. ``DockerWorkerExecutor`` injects it into
#: every container it starts, alongside ``CLM_API_URL``.
TOKEN_ENV_VAR = "CLM_API_TOKEN"

#: Number of random bytes behind a generated token.
_TOKEN_BYTES = 32


def generate_token() -> str:
    """Return a fresh, URL-safe Worker API token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)
