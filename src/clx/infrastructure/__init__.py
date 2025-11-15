"""Infrastructure module for job orchestration and worker management.

This module provides the infrastructure for running course processing operations,
including backend implementations, job queues, messaging, and worker management.
"""

from clx.infrastructure.backend import Backend
from clx.infrastructure.operation import Operation

__all__ = [
    "Backend",
    "Operation",
]
