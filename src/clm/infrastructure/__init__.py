"""Infrastructure module for job orchestration and worker management.

This module provides the infrastructure for running course processing operations,
including backend implementations, job queues, messaging, and worker management.
"""

from clm.infrastructure.backend import Backend
from clm.infrastructure.operation import Operation

__all__ = [
    "Backend",
    "Operation",
]
