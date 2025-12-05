"""REST API for Docker worker communication.

This module provides a REST API that Docker containers use to communicate
with the CLX job queue, bypassing the SQLite WAL mode issues on Windows.

Note: Server components (WorkerApiServer, worker_routes) are NOT imported
at package level because they require uvicorn/fastapi which aren't installed
in worker containers. Import them directly:
    from clx.infrastructure.api.server import WorkerApiServer
"""

# Client-only imports (no uvicorn/fastapi dependency)
from clx.infrastructure.api.client import JobInfo, WorkerApiClient, WorkerApiError
from clx.infrastructure.api.job_queue_adapter import ApiJobQueue

__all__ = [
    "ApiJobQueue",
    "JobInfo",
    "WorkerApiClient",
    "WorkerApiError",
]
