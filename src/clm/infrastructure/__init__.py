"""Infrastructure module for job orchestration and worker management.

This module provides the infrastructure for running course processing operations,
including backend implementations, job queues, messaging, and worker management.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.core.backend import Backend
    from clm.core.operation import Operation

# Convenience exports, resolved lazily (PEP 562) so that importing
# lightweight submodules (e.g. ``clm.infrastructure.logging``) does not
# pull in the backend/job-queue stack. The Backend/Operation contracts
# moved to clm.core (Phase 8 S2, #802); the names stay importable from
# clm.infrastructure as a compatibility surface (pinned by
# tests/cli/test_cli_startup.py::TestLazyExportsCompatibility).
_LAZY_EXPORTS = {
    "Backend": ("clm.core.backend", "Backend"),
    "Operation": ("clm.core.operation", "Operation"),
}


def _worker_identity_singleton_fallback(worker_type: str) -> str:
    """Lazy-bodied fallback provider for the core identity registry (S4).

    Registered at package import so ANY ``clm.infrastructure.*`` import
    wires the singleton-config resolver into
    :mod:`clm.core.worker_identity`. The body defers the (heavy) worker
    package import until a payload is actually built outside a
    build/cache-explain context — the same import cost the pre-S4 lazy
    imports in the core operations paid.
    """
    from clm.infrastructure.workers.image_identity import singleton_worker_image_identity

    return singleton_worker_image_identity(worker_type)


def _register_worker_identity_fallback() -> None:
    from clm.core.worker_identity import register_fallback_provider

    register_fallback_provider(_worker_identity_singleton_fallback)


_register_worker_identity_fallback()

__all__ = [
    "Backend",
    "Operation",
]


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
