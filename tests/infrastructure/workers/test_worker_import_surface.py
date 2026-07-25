"""Guard: the worker import path must not require host-only dependencies.

Worker container images install CLM without the server-side extras — no
FastAPI, no uvicorn, no Starlette. Nothing in the ordinary test suite notices
when a host-only import leaks into a module the workers load, because the
developer machine and CI both have those packages installed. The failure only
appears inside a container, on the ``docker`` job, which is not a required
check.

That is not hypothetical: adding the Worker API's bearer token (S2) first put
the token's env-var name in ``clm.infrastructure.api.auth``, which imports
FastAPI — and ``client.py`` imports that name. Every worker container then died
with ``ModuleNotFoundError: No module named 'fastapi'`` before it could claim
a single job, visible only as jobs stuck in ``pending``. The constant now lives
in ``clm.infrastructure.api.token``, which depends on nothing.

The check runs in a subprocess with an import hook that refuses the host-only
packages, which is the closest in-process analogue of the container's
environment.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

#: Packages present on a host but not in a worker image.
HOST_ONLY_PACKAGES = ("fastapi", "uvicorn", "starlette")

#: Modules a worker container imports on its way to claiming a job. Entry
#: points first, then the API-mode plumbing underneath them.
WORKER_MODULES = [
    "clm.workers.notebook.notebook_worker",
    "clm.workers.plantuml.plantuml_worker",
    "clm.workers.drawio.drawio_worker",
    "clm.infrastructure.workers.worker_base",
    "clm.infrastructure.api.client",
    "clm.infrastructure.api.token",
    "clm.infrastructure.api.job_queue_adapter",
    "clm.infrastructure.api.api_executed_notebook_cache",
    "clm.infrastructure.notebook_serialization",
]

_PROBE = """
import builtins
import sys

BLOCKED = {blocked!r}
_real_import = builtins.__import__


def _guarded(name, *args, **kwargs):
    if name.split(".")[0] in BLOCKED:
        raise ImportError(
            f"{{name}} is not installed in worker images; "
            f"import it lazily inside the function that needs it"
        )
    return _real_import(name, *args, **kwargs)


builtins.__import__ = _guarded
import {module}  # noqa: E402,F401
"""


@pytest.mark.parametrize("module", WORKER_MODULES)
def test_worker_module_imports_without_host_only_packages(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(blocked=set(HOST_ONLY_PACKAGES), module=module)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{module} pulls in a host-only package at import time, so it cannot "
        f"load in a worker container:\n{result.stderr}"
    )


def test_the_guard_itself_bites() -> None:
    """A module that genuinely needs FastAPI must fail the probe.

    Without this, a broken import hook would make every assertion above pass
    vacuously — the same shape of problem as the stale skip guards in Phase 1.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _PROBE.format(
                blocked=set(HOST_ONLY_PACKAGES), module="clm.infrastructure.api.worker_routes"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "not installed in worker images" in result.stderr
