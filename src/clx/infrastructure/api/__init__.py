"""REST API for Docker worker communication.

This module provides a REST API that Docker containers use to communicate
with the CLX job queue, bypassing the SQLite WAL mode issues on Windows.
"""

from clx.infrastructure.api.client import JobInfo, WorkerApiClient, WorkerApiError
from clx.infrastructure.api.job_queue_adapter import ApiJobQueue
from clx.infrastructure.api.server import (
    WorkerApiServer,
    get_worker_api_server,
    start_worker_api_server,
    stop_worker_api_server,
)
from clx.infrastructure.api.worker_routes import router as worker_router

__all__ = [
    "ApiJobQueue",
    "JobInfo",
    "WorkerApiClient",
    "WorkerApiError",
    "WorkerApiServer",
    "get_worker_api_server",
    "start_worker_api_server",
    "stop_worker_api_server",
    "worker_router",
]
