"""Worker pool management for CLM.

This package provides the worker infrastructure for processing jobs from
the SQLite queue, including base worker classes and pool management.

Note: WorkerPoolManager and WorkerConfig are NOT imported at package level
to avoid pulling in server dependencies (uvicorn, fastapi) that aren't
needed in worker containers. Import them directly:
    from clm.infrastructure.workers.pool_manager import WorkerPoolManager
"""

from clm.infrastructure.workers.worker_base import Worker

__all__ = ["Worker"]
