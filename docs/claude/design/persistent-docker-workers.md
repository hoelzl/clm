# Persistent Docker Workers Design

**Status:** Proposed (not implemented)
**Created:** 2024-12-13
**Purpose:** Document a future optimization for faster iterative course development

## Background

### The Problem

When developing courses, authors need to run many iterations of the conversion process. Each `clm build` currently:

1. Starts Docker containers for each worker type (notebook, plantuml, drawio)
2. Waits for containers to initialize (significant overhead, especially with 8-32 notebook workers)
3. Processes files
4. Shuts down all containers

For large courses with frequent iteration, container startup overhead becomes a significant bottleneck.

### Historical Context

The original CLM architecture used RabbitMQ as a message queue:
- Workers ran persistently, waiting for messages
- When jobs arrived, RabbitMQ pushed them to available workers
- Workers stayed alive between builds, eliminating startup overhead

This was replaced with the current REST API architecture to solve SQLite WAL mode issues with Docker volume mounts on Windows. The REST API works reliably but introduced transient workers tied to build lifecycle.

## Current Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ clm build                                                   │
│  ├── Start WorkerApiServer (background thread, :8765)       │
│  ├── Start Docker containers (pass CLM_API_URL)             │
│  ├── Add jobs to SQLite queue                               │
│  ├── Workers poll API, process jobs                         │
│  ├── Wait for completion                                    │
│  └── Shutdown: stop containers, stop API server             │
└─────────────────────────────────────────────────────────────┘
```

**Key files:**
- `src/clm/infrastructure/api/server.py` - WorkerApiServer
- `src/clm/infrastructure/workers/pool_manager.py` - starts/stops API server
- `src/clm/infrastructure/workers/worker_executor.py` - launches Docker containers

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ HOST (persistent)                                           │
│  ┌──────────────────┐      ┌──────────────────────────────┐ │
│  │ clm worker-server│◄────►│ SQLite DB (clm_jobs.db)      │ │
│  │ (long-running)   │      └──────────────────────────────┘ │
│  │ :8765            │                                       │
│  └────────▲─────────┘                                       │
│           │ REST API (long-polling)                         │
├───────────┼─────────────────────────────────────────────────┤
│ DOCKER    │ (host.docker.internal:8765)                     │
│  ┌────────┴────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │ notebook-worker │  │ notebook-worker │  │ plantuml     │ │
│  │ (persistent)    │  │ (persistent)    │  │ (persistent) │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│   Started via docker compose, stay running between builds   │
└─────────────────────────────────────────────────────────────┘

Workflow:
1. User starts: clm worker-server (runs persistently)
2. User starts: docker compose up -d (workers connect to API)
3. User runs: clm build course.yaml (adds jobs, workers process them)
4. Repeat step 3 as needed (no container restart)
5. When done: docker compose down && clm worker-server stop
```

## Implementation Plan

### Phase 1: Long-Polling Endpoint

**Goal:** Allow workers to efficiently wait for jobs without busy-polling.

**Changes to `src/clm/infrastructure/api/worker_routes.py`:**

```python
import asyncio
from typing import Optional

# Global event for job availability notification
_job_available_event: asyncio.Event | None = None
_job_available_lock = asyncio.Lock()

async def get_job_available_event() -> asyncio.Event:
    """Get or create the job availability event."""
    global _job_available_event
    async with _job_available_lock:
        if _job_available_event is None:
            _job_available_event = asyncio.Event()
        return _job_available_event

async def notify_job_available():
    """Signal that a new job is available."""
    event = await get_job_available_event()
    event.set()
    # Reset after a short delay to allow multiple waiters to wake
    await asyncio.sleep(0.01)
    event.clear()

@router.post("/api/worker/jobs/claim")
async def claim_job(
    request: ClaimJobRequest,
    timeout: Optional[float] = Query(default=None, ge=0, le=60),
) -> ClaimJobResponse:
    """Claim the next available job, optionally waiting.

    Args:
        request: Worker info and job type
        timeout: If provided, wait up to this many seconds for a job.
                 If None, return immediately (current behavior).
    """
    job_queue: JobQueue = request.app.state.job_queue

    # Try to get a job immediately
    job = job_queue.get_next_job(request.job_type, request.worker_id)
    if job is not None:
        return ClaimJobResponse(job=job)

    # If no timeout, return immediately
    if timeout is None or timeout <= 0:
        return ClaimJobResponse(job=None)

    # Wait for job with timeout
    event = await get_job_available_event()
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        # Event fired, try to claim job
        job = job_queue.get_next_job(request.job_type, request.worker_id)
        return ClaimJobResponse(job=job)
    except asyncio.TimeoutError:
        return ClaimJobResponse(job=None)
```

**Changes to job submission (notify workers):**

```python
# When adding a job to the queue, notify waiting workers
async def add_job_and_notify(job_queue: JobQueue, job: Job):
    job_queue.add_job(job)
    await notify_job_available()
```

**Estimated complexity:** ~50-100 lines

### Phase 2: Persistent Server Command

**Goal:** New CLI command to run API server independently of builds.

**New file: `src/clm/cli/worker_server.py`:**

```python
import click
import signal
import sys
from pathlib import Path

from clm.infrastructure.api.server import WorkerApiServer, DEFAULT_PORT
from clm.infrastructure.database.job_queue import get_default_db_path

@click.command()
@click.option('--port', default=DEFAULT_PORT, help='Port to listen on')
@click.option('--db-path', type=click.Path(), default=None,
              help='Database path (default: standard location)')
def worker_server(port: int, db_path: str | None):
    """Run the Worker API server for persistent Docker workers.

    Start this server before running 'docker compose up' to enable
    persistent workers that survive between builds.

    Example workflow:
        clm worker-server &          # Start API server
        docker compose up -d          # Start persistent workers
        clm build course.yaml         # Build (workers already running)
        clm build course.yaml         # Rebuild (instant worker startup)
        docker compose down           # Stop workers when done
    """
    db = Path(db_path) if db_path else get_default_db_path()

    click.echo(f"Starting Worker API server on port {port}...")
    click.echo(f"Database: {db}")
    click.echo(f"Docker URL: http://host.docker.internal:{port}")
    click.echo("Press Ctrl+C to stop")

    server = WorkerApiServer(db, port=port)
    server.start()

    def signal_handler(sig, frame):
        click.echo("\nShutting down...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Block until interrupted
    signal.pause()
```

**Register in CLI:**

```python
# In src/clm/cli/__init__.py
from clm.cli.worker_server import worker_server
cli.add_command(worker_server)
```

**Estimated complexity:** ~50 lines

### Phase 3: External Worker Detection in Build

**Goal:** `clm build` detects if API server is already running and skips starting Docker workers.

**Changes to `src/clm/infrastructure/workers/pool_manager.py`:**

```python
import httpx

def _check_external_api_server(self) -> bool:
    """Check if an external Worker API server is running."""
    try:
        response = httpx.get(
            f"http://localhost:{DEFAULT_PORT}/health",
            timeout=1.0
        )
        if response.status_code == 200:
            logger.info(
                f"External Worker API server detected on port {DEFAULT_PORT}. "
                "Using external workers instead of starting containers."
            )
            return True
    except httpx.RequestError:
        pass
    return False

def start_pools(self):
    """Start worker pools, using external workers if available."""
    if self._check_external_api_server():
        self._using_external_workers = True
        # Don't start Docker containers, just wait for jobs to complete
        return

    # Current behavior: start transient workers
    self._using_external_workers = False
    self._start_worker_api_server()
    # ... rest of current implementation
```

**Estimated complexity:** ~30 lines

### Phase 4: Docker Compose Configuration

**New `docker-compose.yaml`:**

```yaml
name: clm-workers

# Persistent Docker workers for CLM course development
#
# Prerequisites:
#   1. Start the API server: clm worker-server
#   2. Start workers: docker compose up -d
#
# Usage:
#   clm build course.yaml   # Workers already running, instant startup
#
# Scaling:
#   docker compose up -d --scale notebook-processor=8

services:
  notebook-processor:
    image: mhoelzl/clm-notebook-processor:${NOTEBOOK_VARIANT:-0.5.0}
    environment:
      - CLM_API_URL=http://host.docker.internal:8765
      - LOG_LEVEL=INFO
      - PYTHONUNBUFFERED=1
    extra_hosts:
      - "host.docker.internal:host-gateway"  # Linux support
    deploy:
      mode: replicated
      replicas: 1

  drawio-converter:
    image: mhoelzl/clm-drawio-converter:0.5.0
    environment:
      - CLM_API_URL=http://host.docker.internal:8765
      - DISPLAY=:99
      - LOG_LEVEL=INFO
      - PYTHONUNBUFFERED=1
    extra_hosts:
      - "host.docker.internal:host-gateway"
    init: true
    deploy:
      mode: replicated
      replicas: 1

  plantuml-converter:
    image: mhoelzl/clm-plantuml-converter:0.5.0
    environment:
      - CLM_API_URL=http://host.docker.internal:8765
      - LOG_LEVEL=INFO
      - PYTHONUNBUFFERED=1
    extra_hosts:
      - "host.docker.internal:host-gateway"
    deploy:
      mode: replicated
      replicas: 1
```

### Phase 5: Worker Long-Poll Loop

**Changes to `src/clm/infrastructure/workers/worker_base.py`:**

```python
async def _run_loop(self):
    """Main worker loop with long-polling support."""
    while not self._should_stop.is_set():
        try:
            # Use long-polling timeout when in API mode
            timeout = 30.0 if self._api_mode else None
            job = self.job_queue.get_next_job(
                self.worker_type,
                self.worker_id,
                timeout=timeout  # New parameter
            )

            if job is not None:
                await self._process_job(job)
            elif not self._api_mode:
                # Direct mode: sleep before retry
                await asyncio.sleep(self._poll_interval)
            # API mode with long-poll: immediately retry (server waited)

        except Exception as e:
            logger.error(f"Error in worker loop: {e}")
            await asyncio.sleep(1.0)
```

## Testing Strategy

### Unit Tests

1. **Long-polling endpoint tests:**
   - Returns immediately when job available
   - Waits and returns job when one arrives during wait
   - Returns None after timeout with no job
   - Multiple workers waiting, one gets job

2. **External worker detection tests:**
   - Detects running server correctly
   - Falls back to transient workers when no server

### Integration Tests

1. **End-to-end with docker compose:**
   - Start API server
   - Start workers via compose
   - Run build, verify jobs processed
   - Run second build, verify faster startup

2. **Graceful shutdown:**
   - Workers handle server shutdown gracefully
   - No zombie containers

## Performance Expectations

| Scenario | Current | With Persistent Workers |
|----------|---------|------------------------|
| First build | 30-60s startup | Same (cold start) |
| Subsequent builds | 30-60s startup | <5s (workers ready) |
| 10 iterations | 10 × startup overhead | 1 × startup overhead |

For a course requiring 20 iterations during development with 32 notebook workers, estimated time savings: **10-20 minutes per development session**.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Workers become stale/stuck | Health checks + automatic restart |
| Memory leaks in long-running workers | Periodic worker recycling (e.g., after N jobs) |
| Port conflicts | Configurable port, clear error messages |
| Orphaned containers | docker compose down in shutdown hooks |

## Future Enhancements

1. **Auto-start workers:** `clm build --persistent` starts server + compose if not running
2. **Worker health dashboard:** Extend `clm serve` to show persistent worker status
3. **Warm worker pool:** Pre-start workers when `clm worker-server` starts
4. **WebSocket notifications:** Replace long-polling for lower latency (diminishing returns)

## Decision

**Deferred.** The current priority is ensuring reliable operation of transient Docker workers started by `clm build`. This optimization can be revisited when:

1. Transient Docker workers work reliably on all platforms
2. Users report iteration speed as a significant pain point
3. Development resources are available for the ~300-400 lines of implementation

## Related Documents

- `docs/claude/docker-investigation.md` - SQLite WAL issues that led to REST API
- `docs/developer-guide/architecture.md` - Current system architecture
- `docker/BUILDING.md` - Docker image build instructions
