# REST API for Docker Worker Communication

## Overview

This document describes the design for a REST API that enables Docker containers to communicate with the CLX job queue without requiring direct SQLite access. This solves the Windows Docker SQLite WAL mode incompatibility issue.

## Problem Statement

SQLite WAL mode requires shared memory (`-shm` files) for readers to see uncommitted writes. On Docker Desktop for Windows, bind-mounted volumes don't properly share these memory-mapped files, causing:
- Container writes to database successfully
- Host cannot see those writes
- Worker registration and job completion go unnoticed

WAL mode cannot be disabled—it's essential for performance and concurrent access.

## Solution Architecture

```
┌─────────────────────────────────────┐
│         Docker Container            │
│  ┌───────────────────────────────┐  │
│  │      Worker Process           │  │
│  │   (notebook/plantuml/drawio)  │  │
│  └───────────────┬───────────────┘  │
│                  │ HTTP               │
└──────────────────┼──────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│   CLX Worker API Server (Host)       │
│   http://host.docker.internal:8765   │
│  ┌────────────────────────────────┐  │
│  │  FastAPI Router (/api/worker)  │  │
│  └───────────────┬────────────────┘  │
│                  │                    │
│  ┌───────────────▼────────────────┐  │
│  │         JobQueue               │  │
│  │      (Direct SQLite)           │  │
│  └───────────────┬────────────────┘  │
│                  │                    │
└──────────────────┼────────────────────┘
                   │
                   ▼
            ┌──────────────┐
            │  SQLite DB   │
            │  (WAL mode)  │
            └──────────────┘
```

## API Endpoints

### Worker Registration

```
POST /api/worker/register
```

**Request:**
```json
{
  "worker_type": "notebook",
  "container_id": "abc123def456",
  "parent_pid": 12345
}
```

**Response:**
```json
{
  "worker_id": 42,
  "registered_at": "2024-01-15T10:30:00Z"
}
```

### Get Next Job (Claim)

```
POST /api/worker/jobs/claim
```

**Request:**
```json
{
  "worker_id": 42,
  "job_type": "notebook"
}
```

**Response (job available):**
```json
{
  "job": {
    "id": 123,
    "job_type": "notebook",
    "input_file": "/workspace/course/topic/notebook.ipynb",
    "output_file": "/workspace/output/notebook.html",
    "content_hash": "sha256:abc123...",
    "payload": {
      "output_format": "html",
      "output_kind": "code-along",
      "lang": "en"
    },
    "correlation_id": "build-2024-01-15-001"
  }
}
```

**Response (no jobs):**
```json
{
  "job": null
}
```

### Update Job Status

```
POST /api/worker/jobs/{job_id}/status
```

**Request (success):**
```json
{
  "worker_id": 42,
  "status": "completed",
  "result": {
    "warnings": [
      {"code": "MISSING_CELL_TAG", "message": "Cell 5 missing slide tag"}
    ]
  }
}
```

**Request (failure):**
```json
{
  "worker_id": 42,
  "status": "failed",
  "error": {
    "error_message": "Kernel died unexpectedly",
    "error_class": "KernelError",
    "traceback": "...",
    "is_transient": true,
    "is_fatal": false
  }
}
```

**Response:**
```json
{
  "acknowledged": true
}
```

### Heartbeat

```
POST /api/worker/heartbeat
```

**Request:**
```json
{
  "worker_id": 42
}
```

**Response:**
```json
{
  "acknowledged": true,
  "timestamp": "2024-01-15T10:35:00Z"
}
```

### Check Job Cancellation

```
GET /api/worker/jobs/{job_id}/cancelled
```

**Response:**
```json
{
  "cancelled": false
}
```

Or:
```json
{
  "cancelled": true,
  "cancelled_at": "2024-01-15T10:32:00Z",
  "cancelled_by": "watch-mode"
}
```

### Worker Shutdown (Optional)

```
POST /api/worker/unregister
```

**Request:**
```json
{
  "worker_id": 42,
  "reason": "graceful_shutdown"
}
```

**Response:**
```json
{
  "acknowledged": true
}
```

## Implementation Plan

### Phase 1: API Server Module

Create `src/clx/infrastructure/api/worker_routes.py`:

```python
"""REST API routes for worker communication."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clx.infrastructure.database.job_queue import JobQueue

router = APIRouter(prefix="/api/worker", tags=["worker"])

# Pydantic models for request/response
class WorkerRegistration(BaseModel):
    worker_type: str
    container_id: str
    parent_pid: int | None = None

class WorkerRegistrationResponse(BaseModel):
    worker_id: int
    registered_at: str

class JobClaimRequest(BaseModel):
    worker_id: int
    job_type: str

class JobStatusUpdate(BaseModel):
    worker_id: int
    status: str  # 'completed' or 'failed'
    result: dict | None = None
    error: dict | None = None

# ... endpoints implementation
```

### Phase 2: Worker API Client

Create `src/clx/infrastructure/workers/api_client.py`:

```python
"""HTTP client for worker API communication."""

import httpx
from typing import Any

class WorkerApiClient:
    """HTTP client for Docker workers to communicate with host."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def register(self, worker_type: str, container_id: str, parent_pid: int | None = None) -> int:
        """Register worker and return worker_id."""
        response = self.client.post("/api/worker/register", json={
            "worker_type": worker_type,
            "container_id": container_id,
            "parent_pid": parent_pid,
        })
        response.raise_for_status()
        return response.json()["worker_id"]

    def claim_job(self, worker_id: int, job_type: str) -> dict | None:
        """Claim next available job."""
        response = self.client.post("/api/worker/jobs/claim", json={
            "worker_id": worker_id,
            "job_type": job_type,
        })
        response.raise_for_status()
        return response.json().get("job")

    def complete_job(self, job_id: int, worker_id: int, result: dict | None = None):
        """Mark job as completed."""
        response = self.client.post(f"/api/worker/jobs/{job_id}/status", json={
            "worker_id": worker_id,
            "status": "completed",
            "result": result,
        })
        response.raise_for_status()

    def fail_job(self, job_id: int, worker_id: int, error: dict):
        """Mark job as failed."""
        response = self.client.post(f"/api/worker/jobs/{job_id}/status", json={
            "worker_id": worker_id,
            "status": "failed",
            "error": error,
        })
        response.raise_for_status()

    def heartbeat(self, worker_id: int):
        """Send heartbeat."""
        response = self.client.post("/api/worker/heartbeat", json={
            "worker_id": worker_id,
        })
        response.raise_for_status()

    def is_job_cancelled(self, job_id: int) -> bool:
        """Check if job was cancelled."""
        response = self.client.get(f"/api/worker/jobs/{job_id}/cancelled")
        response.raise_for_status()
        return response.json()["cancelled"]
```

### Phase 3: Unified Queue Interface

Create an abstract interface that both SQLite and REST implementations follow:

```python
"""Abstract job queue interface."""

from abc import ABC, abstractmethod
from typing import Any

class JobQueueInterface(ABC):
    """Abstract interface for job queue operations."""

    @abstractmethod
    def register_worker(self, worker_type: str, container_id: str, parent_pid: int | None) -> int:
        """Register a worker and return its ID."""
        ...

    @abstractmethod
    def get_next_job(self, job_type: str, worker_id: int) -> dict | None:
        """Get next available job for worker."""
        ...

    @abstractmethod
    def update_job_status(self, job_id: int, status: str, error: str | None = None, result: str | None = None):
        """Update job status."""
        ...

    @abstractmethod
    def update_heartbeat(self, worker_id: int):
        """Update worker heartbeat."""
        ...

    @abstractmethod
    def is_job_cancelled(self, job_id: int) -> bool:
        """Check if job is cancelled."""
        ...
```

### Phase 4: Worker Base Modification

Modify `WorkerBase` to accept either a `JobQueue` or `WorkerApiClient`:

```python
class WorkerBase(ABC):
    def __init__(
        self,
        db_path: Path | None = None,
        api_url: str | None = None,
        ...
    ):
        if api_url:
            # Docker mode: use REST API
            self.queue = RestJobQueue(api_url)
        else:
            # Direct mode: use SQLite
            self.queue = JobQueue(db_path)
```

### Phase 5: Worker Executor Integration

Modify `worker_executor.py` to:
1. Start the API server when launching Docker workers
2. Pass `CLX_API_URL` environment variable to containers
3. Not mount the database directory (no longer needed)

```python
def start_docker_worker(self, ...):
    # Ensure API server is running
    api_port = self._ensure_api_server_running()

    # Docker-internal hostname for host access
    api_url = f"http://host.docker.internal:{api_port}"

    container = self.docker_client.containers.run(
        image=image,
        environment={
            "CLX_API_URL": api_url,
            "WORKER_TYPE": worker_type,
        },
        # Mount workspace for file access (notebooks), but NOT database
        volumes={
            str(workspace_path): {"bind": "/workspace", "mode": "rw"},
        },
        ...
    )
```

## File Operations

Workers still need file access for:
- Reading input files (notebooks, PlantUML, DrawIO)
- Writing output files (HTML, PNG, SVG)

This continues to use Docker volume mounts for the workspace directory. Only database communication moves to REST.

## Server Lifecycle

### Decision: Auto-start with `clx build`

The API server starts automatically when Docker mode is detected:

1. `PoolManager.start_pools()` checks `needs_docker = any(c.execution_mode == "docker" ...)`
2. If Docker needed, start the Worker API server on fixed port **8765**
3. Server runs in background thread for the duration of the build
4. Server shuts down when `PoolManager` is cleaned up

### Why This Works

- `clx build` always knows the execution mode before starting workers (via config/CLI)
- Workers can retry connecting if server isn't ready yet (startup race)
- Fixed port (8765) allows containers to use `http://host.docker.internal:8765`
- No manual server start required - "just works"

### For Long-Running Workers (`clx start-services`)

The same approach works:
1. `clx start-services --workers docker` detects Docker mode
2. Starts API server before launching containers
3. Server stays alive as long as workers are running
4. `clx stop-services` shuts down server and workers together

### Implementation Location

Modify `PoolManager.start_pools()`:

```python
def start_pools(self):
    needs_docker = any(c.execution_mode == "docker" for c in self.worker_configs)
    if needs_docker:
        self._ensure_network_exists()
        self._start_worker_api_server()  # NEW: Start REST API
    ...
```

### Worker Startup Resilience

Workers should handle API server not being immediately ready:

```python
# In worker startup
for attempt in range(max_retries):
    try:
        worker_id = api_client.register(...)
        break
    except httpx.ConnectError:
        if attempt < max_retries - 1:
            time.sleep(0.5 * (2 ** attempt))  # Exponential backoff
        else:
            raise
```

## Error Handling

### Network Errors
- Workers should retry with exponential backoff
- After N failures, worker marks itself as unhealthy

### Server Unavailable
- Workers exit gracefully if server is unreachable at startup
- During operation, retry transient failures

### Timeout Handling
- Server should respond within 5 seconds
- Long-polling for job claims not needed (workers poll at 0.1s intervals)

## Security Considerations

For local development (primary use case):
- API binds to `127.0.0.1` only
- No authentication required (same-machine trust)

For future remote workers:
- Add API key authentication
- Enable HTTPS
- Firewall rules for API port

## Testing Strategy

1. **Unit tests**: Mock HTTP client, test API routes
2. **Integration tests**: Real API server, test full flow
3. **Docker tests**: Full Docker worker with REST communication

## Dependencies

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
worker-api = ["httpx>=0.25"]  # For worker client
```

Note: `fastapi` and `uvicorn` already required for `[web]` extra.

## Migration Path

1. Implement REST API alongside existing SQLite path
2. Direct execution mode continues using SQLite (no change)
3. Docker mode uses REST API
4. Both paths share the same JobQueue backend on the host

## Timeline Estimate

- Phase 1 (API Server): ~2 hours
- Phase 2 (Client): ~1 hour
- Phase 3 (Interface): ~1 hour
- Phase 4 (Worker Base): ~2 hours
- Phase 5 (Executor): ~2 hours
- Testing: ~3 hours

Total: ~11 hours of implementation work

## Resolved Questions

1. **Should the API server be part of `clx serve` or separate?**
   - **Decision**: Auto-start in background thread when Docker mode detected
   - Integrated into `PoolManager.start_pools()`
   - No manual server start required

2. **Port selection: fixed (8765) or dynamic?**
   - **Decision**: Fixed port 8765
   - Required for Docker containers to reliably connect to `host.docker.internal:8765`
   - Simple and predictable

3. **Should we support remote workers (authentication)?**
   - **Decision**: No authentication for now
   - Server binds to 127.0.0.1 (localhost only)
   - Remote workers not a current priority
   - Can add later if needed
