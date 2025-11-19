"""Worker pool management for CLX.

This package provides the worker infrastructure for processing jobs from
the SQLite queue, including base worker classes and pool management.
"""

from clx.infrastructure.workers.pool_manager import WorkerConfig, WorkerPoolManager
from clx.infrastructure.workers.worker_base import Worker

__all__ = ["Worker", "WorkerPoolManager", "WorkerConfig"]
