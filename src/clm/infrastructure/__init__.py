"""Infrastructure module for job orchestration and worker management.

This module provides the infrastructure for running course processing operations,
including backend implementations, job queues, messaging, and worker management.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.infrastructure.backend import Backend
    from clm.infrastructure.operation import Operation

# Convenience exports, resolved lazily (PEP 562) so that importing
# lightweight submodules (e.g. ``clm.infrastructure.logging``) does not
# pull in the backend/job-queue stack — and so that the circular chain
# backend -> clm.core -> course_file -> operation -> backend cannot
# trigger at package-init time.
_LAZY_EXPORTS = {
    "Backend": ("clm.infrastructure.backend", "Backend"),
    "Operation": ("clm.infrastructure.operation", "Operation"),
}

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
