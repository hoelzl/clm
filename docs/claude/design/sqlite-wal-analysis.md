# SQLite WAL Mode Analysis: Architectural Solutions

## Executive Summary

This document analyzes three approaches to solving SQLite concurrency issues in the CLM job orchestration system:
1. **Current Approach**: Defensive transaction rollback (test_failure_analysis.md)
2. **Approach 1**: WAL mode with sidecar processes for Docker workers
3. **Approach 2**: PostgreSQL for Docker deployments

**Recommendation**: **Approach 1 (WAL + Sidecars)** with a phased implementation plan.

---

## Current Problem Analysis

### The Core Issue

SQLite in **DELETE journal mode** (current implementation) has fundamental concurrency limitations:
- **Write serialization**: Only one writer at a time across ALL processes
- **Reader-writer blocking**: Active read transactions block writes
- **Cross-process coordination**: Coordination happens through file locks, which are slow

### Why This Manifests Now

The CLM architecture has multiple concurrent processes:
- **Main process**: Submits jobs (writes to `jobs` table)
- **3-4 worker processes**: Each worker:
  - Polls for jobs (reads from `jobs`)
  - Updates heartbeat every 1-5 seconds (writes to `workers`)
  - Updates job status (writes to `jobs`)
  - Updates statistics (writes to `workers`)

**Contention points**:
- Worker heartbeats alone generate 3-4 writes/second baseline
- During active processing: 10-20 writes/second easily
- Reads can hold locks for unpredictable durations

### Current Workarounds Attempted

From recent commits (c1be194, cca78e6, b313b43, 6746f54, 167c5ac):

1. **Autocommit mode** (`isolation_level=None`)
2. **30-second timeout** on connections
3. **Defensive rollbacks** before every write
4. **Retry logic** for "readonly database" errors

**Why these fail**: They're treating symptoms, not the root cause. DELETE mode fundamentally cannot handle this write concurrency.

---

## Approach 1: WAL Mode + Sidecars

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Host System (WAL Mode)                   │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐                                            │
│  │ SQLite DB    │                                            │
│  │ (WAL mode)   │                                            │
│  └──────┬───────┘                                            │
│         │                                                     │
│         ├──────────┬─────────────┬────────────┬─────────┐   │
│         │          │             │            │         │   │
│    ┌────▼────┐ ┌──▼──────┐  ┌───▼─────┐  ┌───▼──────┐  │   │
│    │Main CLI │ │ Direct  │  │ Direct  │  │ Direct   │  │   │
│    │Process  │ │ Worker  │  │ Worker  │  │ Worker   │  │   │
│    │         │ │ (nb)    │  │ (puml)  │  │ (drawio) │  │   │
│    └─────────┘ └─────────┘  └─────────┘  └──────────┘  │   │
│                                                          │   │
│    ┌─────────────────────────────────────────────────┐  │   │
│    │         Docker Sidecar Daemon (REST API)        │  │   │
│    │  - Listens on localhost:9999                    │  │   │
│    │  - Handles DB operations for containers         │  │   │
│    └──────────────┬──────────────────────────────────┘  │   │
│                   │                                      │   │
├───────────────────┼──────────────────────────────────────┼───┤
│                   │         Docker Network               │   │
│                   │                                      │   │
│    ┌──────────────▼──────┐  ┌──────────────────┐       │   │
│    │ Docker Worker (nb)  │  │ Docker Worker    │       │   │
│    │ - HTTP client       │  │ (plantuml)       │  ...  │   │
│    │ - Calls sidecar API │  │ - HTTP client    │       │   │
│    └─────────────────────┘  └──────────────────┘       │   │
│                                                          │   │
└──────────────────────────────────────────────────────────────┘
```

### Implementation Details

#### 1. Enable WAL Mode

**Change**: `src/clm/infrastructure/database/schema.py`

```python
def init_database(db_path: Path, enable_wal: bool = True) -> sqlite3.Connection:
    """Initialize database with schema.

    Args:
        db_path: Path to SQLite database file
        enable_wal: Whether to enable WAL mode (default: True)
                    Set to False for Docker volume mounts on Windows
    """
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)

    # Use WAL mode for better concurrency (if enabled)
    if enable_wal:
        conn.execute("PRAGMA journal_mode=WAL")
        # WAL mode tuning for high write concurrency
        conn.execute("PRAGMA synchronous=NORMAL")  # Safer than OFF, faster than FULL
        conn.execute("PRAGMA wal_autocheckpoint=1000")  # Checkpoint every 1000 pages
        conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout
    else:
        # Fallback to DELETE mode with optimizations
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")

    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys=ON")

    # Execute schema...
```

**Benefits of WAL mode**:
- **Concurrent reads and writes**: Readers don't block writers, writers don't block readers
- **Better write throughput**: No need to lock entire database for writes
- **Atomic commits**: Safer than DELETE mode with synchronous=NORMAL

**Limitations**:
- Requires shared memory (doesn't work across Docker volume mounts on Windows/Mac)
- Creates additional files (.wal, .shm) that must stay on same filesystem

#### 2. Sidecar Daemon Architecture

**New module**: `src/clm/infrastructure/sidecar/db_proxy.py`

```python
"""Database proxy sidecar for Docker workers.

This HTTP server provides database operations to Docker containers that cannot
directly access the SQLite database in WAL mode due to shared memory requirements.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import uvicorn
from pathlib import Path

from clm.infrastructure.database.job_queue import JobQueue, Job

app = FastAPI(title="CLM DB Proxy Sidecar")

# Global state
job_queue: Optional[JobQueue] = None


class WorkerHeartbeatRequest(BaseModel):
    worker_id: int


class WorkerStatusRequest(BaseModel):
    worker_id: int
    status: str


class GetJobRequest(BaseModel):
    job_type: str
    worker_id: int


class UpdateJobStatusRequest(BaseModel):
    job_id: int
    status: str
    error: Optional[str] = None


class WorkerStatsRequest(BaseModel):
    worker_id: int
    success: bool
    processing_time: float


@app.on_event("startup")
async def startup():
    global job_queue
    db_path = Path(os.getenv("DB_PATH", "/data/clm_jobs.db"))
    job_queue = JobQueue(db_path)


@app.post("/api/v1/worker/heartbeat")
async def update_heartbeat(req: WorkerHeartbeatRequest):
    """Update worker heartbeat."""
    try:
        conn = job_queue._get_conn()
        conn.execute(
            "UPDATE workers SET last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?",
            (req.worker_id,)
        )
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/worker/status")
async def update_status(req: WorkerStatusRequest):
    """Update worker status."""
    try:
        conn = job_queue._get_conn()
        conn.execute(
            "UPDATE workers SET status = ? WHERE id = ?",
            (req.status, req.worker_id)
        )
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/jobs/get_next")
async def get_next_job(req: GetJobRequest) -> Optional[Dict[str, Any]]:
    """Get next job for worker."""
    try:
        job = job_queue.get_next_job(req.job_type, req.worker_id)
        if job:
            return job.to_dict()
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/jobs/update_status")
async def update_job_status(req: UpdateJobStatusRequest):
    """Update job status."""
    try:
        job_queue.update_job_status(req.job_id, req.status, req.error)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/worker/stats")
async def update_stats(req: WorkerStatsRequest):
    """Update worker stats."""
    # Implementation similar to worker_base.py _update_stats()
    try:
        conn = job_queue._get_conn()
        if req.success:
            conn.execute(
                """
                UPDATE workers
                SET jobs_processed = jobs_processed + 1,
                    avg_processing_time = CASE
                        WHEN avg_processing_time IS NULL THEN ?
                        ELSE (avg_processing_time * jobs_processed + ?) / (jobs_processed + 1)
                    END
                WHERE id = ?
                """,
                (req.processing_time, req.processing_time, req.worker_id)
            )
        else:
            conn.execute(
                "UPDATE workers SET jobs_failed = jobs_failed + 1 WHERE id = ?",
                (req.worker_id,)
            )
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def run_sidecar(db_path: Path, host: str = "0.0.0.0", port: int = 9999):
    """Run the database proxy sidecar."""
    os.environ["DB_PATH"] = str(db_path)
    uvicorn.run(app, host=host, port=port, log_level="info")
```

**Benefits**:
- Centralized DB access control
- Easy to add authentication/authorization later
- Can rate-limit or queue requests if needed
- Logging/monitoring of all DB operations in one place

#### 3. Docker Worker Client

**Modify**: `src/clm/infrastructure/workers/worker_base.py`

```python
class Worker(ABC):
    """Abstract base class for workers."""

    def __init__(
        self,
        worker_id: int,
        worker_type: str,
        db_path: Optional[Path] = None,
        sidecar_url: Optional[str] = None,
        poll_interval: float = 0.1,
        job_timeout: Optional[float] = None
    ):
        """Initialize worker.

        Args:
            worker_id: Unique worker ID
            worker_type: Type of jobs to process
            db_path: Path to SQLite database (for direct mode)
            sidecar_url: URL to sidecar HTTP API (for Docker mode)
            poll_interval: Time to wait between polls
            job_timeout: Maximum time a job can run
        """
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.poll_interval = poll_interval
        self.job_timeout = job_timeout or float('inf')
        self.running = True
        self._last_heartbeat = datetime.now()

        # Choose backend based on environment
        if sidecar_url:
            # Docker mode: use HTTP client
            self.backend = SidecarBackend(sidecar_url)
        elif db_path:
            # Direct mode: use SQLite directly
            self.backend = DirectSQLiteBackend(db_path)
        else:
            raise ValueError("Must provide either db_path or sidecar_url")

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _update_heartbeat(self):
        """Update worker heartbeat."""
        self.backend.update_heartbeat(self.worker_id)
        self._last_heartbeat = datetime.now()

    # Similar abstractions for other operations...


class DirectSQLiteBackend:
    """Backend that accesses SQLite directly."""

    def __init__(self, db_path: Path):
        self.job_queue = JobQueue(db_path)

    def update_heartbeat(self, worker_id: int):
        conn = self.job_queue._get_conn()
        conn.execute(
            "UPDATE workers SET last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?",
            (worker_id,)
        )

    # Other methods...


class SidecarBackend:
    """Backend that uses HTTP sidecar API."""

    def __init__(self, sidecar_url: str):
        self.sidecar_url = sidecar_url.rstrip('/')
        self.session = requests.Session()

    def update_heartbeat(self, worker_id: int):
        resp = self.session.post(
            f"{self.sidecar_url}/api/v1/worker/heartbeat",
            json={"worker_id": worker_id}
        )
        resp.raise_for_status()

    # Other methods...
```

#### 4. Docker Compose Integration

**Update**: `docker-compose.yaml`

```yaml
services:
  # Sidecar daemon (only needed for Docker workers)
  clm-db-sidecar:
    build:
      context: .
      dockerfile: Dockerfile.sidecar
    image: mhoelzl/clm-db-sidecar:0.3.0
    networks:
      - app-network
    environment:
      - LOG_LEVEL=INFO
      - DB_PATH=/data/clm_jobs.db
    volumes:
      - clm-data:/data
    ports:
      - "9999:9999"  # Expose for host if needed
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9999/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  notebook-processor:
    image: mhoelzl/clm-notebook-processor:0.3.0
    depends_on:
      clm-db-sidecar:
        condition: service_healthy
    networks:
      - app-network
    environment:
      - LOG_LEVEL=INFO
      - SIDECAR_URL=http://clm-db-sidecar:9999
    # No volume mount needed - workers use sidecar API
```

**Benefits**:
- Single point of coordination (sidecar)
- No shared volume mount issues
- Containers are truly isolated

### Pros of Approach 1

1. **✅ Best Performance**: WAL mode dramatically improves concurrency
   - Readers don't block writers
   - Writers don't block readers
   - Can handle 10-100x more concurrent operations

2. **✅ Minimal Code Changes**:
   - Direct workers: No changes needed (just enable WAL)
   - Docker workers: Swap DB client for HTTP client
   - Core logic: Unchanged

3. **✅ Operational Simplicity**:
   - Still using SQLite (no PostgreSQL cluster to manage)
   - Single sidecar process (lightweight, <10MB RAM)
   - No external dependencies

4. **✅ Gradual Migration**:
   - Phase 1: Enable WAL for direct workers (immediate benefit)
   - Phase 2: Add sidecar for Docker (when needed)
   - Can keep both modes supported

5. **✅ Testing/Development Friendly**:
   - Direct mode: Fast, simple, no sidecar needed
   - Docker mode: Only when actually using Docker

6. **✅ Backward Compatible**:
   - Can detect Docker environment and auto-choose mode
   - Falls back to DELETE mode if WAL unavailable

### Cons of Approach 1

1. **❌ Additional Component**: Sidecar daemon adds complexity
   - Must be started/stopped
   - Failure point (though can auto-restart)
   - Network latency for Docker workers (~1ms overhead)

2. **❌ Two Code Paths**: Direct vs HTTP backend
   - More code to maintain
   - Must test both paths

3. **❌ Platform Limitations**: WAL mode issues on some platforms
   - Network file systems (NFS): Can corrupt database
   - Some Windows configurations: Shared memory issues
   - Must detect and fall back

4. **❌ File Coordination**: WAL files must stay together
   - .db, .wal, .shm must be on same filesystem
   - Can't move database without checkpointing first

---

## Approach 2: PostgreSQL for Docker

### Architecture

```
┌────────────────────────────────────────────────────────────┐
│                   Deployment Environment                   │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  Direct Workers Mode (Development/Testing)                 │
│  ┌──────────────┐     ┌────────────┐                       │
│  │ SQLite DB    │◄────┤ Direct     │                       │
│  │              │     │ Workers    │                       │
│  └──────────────┘     └────────────┘                       │
│                                                             │
│  Docker Mode (Production)                                  │
│  ┌──────────────┐     ┌────────────┐     ┌────────────┐   │
│  │ PostgreSQL   │◄────┤ Main CLI   │     │ Docker     │   │
│  │ (Docker)     │◄────┤ Process    │     │ Workers    │   │
│  └──────────────┘     └────────────┘     └────────────┘   │
│                                                             │
└────────────────────────────────────────────────────────────┘
```

### Implementation Details

#### 1. Database Abstraction Layer

**New interface**: `src/clm/infrastructure/database/db_interface.py`

```python
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from pathlib import Path

class DatabaseBackend(ABC):
    """Abstract interface for database backends."""

    @abstractmethod
    def add_job(self, job_type: str, input_file: str, output_file: str,
                content_hash: str, payload: Dict[str, Any], priority: int = 0,
                correlation_id: Optional[str] = None) -> int:
        """Add a job to the queue."""
        pass

    @abstractmethod
    def get_next_job(self, job_type: str, worker_id: Optional[int] = None) -> Optional['Job']:
        """Get next pending job."""
        pass

    @abstractmethod
    def update_job_status(self, job_id: int, status: str, error: Optional[str] = None):
        """Update job status."""
        pass

    # ... other methods ...


class SQLiteBackend(DatabaseBackend):
    """SQLite implementation (existing code)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # Existing initialization...

    # Implement interface methods (existing code)...


class PostgreSQLBackend(DatabaseBackend):
    """PostgreSQL implementation for Docker deployments."""

    def __init__(self, connection_string: str):
        import psycopg2
        from psycopg2.pool import ThreadedConnectionPool

        self.pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=connection_string
        )

    def add_job(self, job_type: str, input_file: str, output_file: str,
                content_hash: str, payload: Dict[str, Any], priority: int = 0,
                correlation_id: Optional[str] = None) -> int:
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (
                        job_type, status, input_file, output_file,
                        content_hash, payload, priority, correlation_id
                    ) VALUES (%s, 'pending', %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (job_type, input_file, output_file, content_hash,
                     json.dumps(payload), priority, correlation_id)
                )
                job_id = cur.fetchone()[0]
                conn.commit()
                return job_id
        finally:
            self.pool.putconn(conn)

    # Implement other methods similarly...
```

#### 2. Schema Translation

**Challenge**: SQLite → PostgreSQL schema differences

```sql
-- SQLite: AUTOINCREMENT, CURRENT_TIMESTAMP
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- PostgreSQL: SERIAL, NOW()
CREATE TABLE jobs (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW()
);

-- SQLite: TEXT type for JSON
payload TEXT NOT NULL

-- PostgreSQL: JSONB type (more efficient)
payload JSONB NOT NULL
```

**Solution**: Maintain two schema files or use ORM (SQLAlchemy)

#### 3. Configuration & Auto-Detection

```python
def create_database_backend(config: Dict[str, Any]) -> DatabaseBackend:
    """Factory function to create appropriate database backend.

    Auto-detects based on environment:
    - If POSTGRES_URL set: Use PostgreSQL
    - If Docker Compose detected: Start PostgreSQL container
    - Otherwise: Use SQLite
    """
    # Check environment variable
    postgres_url = os.getenv("POSTGRES_URL")
    if postgres_url:
        logger.info("Using PostgreSQL backend")
        return PostgreSQLBackend(postgres_url)

    # Check if running in Docker environment
    if os.path.exists("/.dockerenv") or os.getenv("DOCKER_COMPOSE_PROJECT"):
        # Check if PostgreSQL service is available
        if is_postgres_available():
            postgres_url = "postgresql://clm:clm@postgres:5432/clm_jobs"
            logger.info("Detected Docker environment, using PostgreSQL")
            return PostgreSQLBackend(postgres_url)

    # Default to SQLite
    db_path = Path(config.get("db_path", "clm_jobs.db"))
    logger.info(f"Using SQLite backend: {db_path}")
    return SQLiteBackend(db_path)
```

#### 4. Docker Compose with PostgreSQL

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: clm_jobs
      POSTGRES_USER: clm
      POSTGRES_PASSWORD: clm
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./sql/init_postgres.sql:/docker-entrypoint-initdb.d/init.sql
    networks:
      - app-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U clm"]
      interval: 10s
      timeout: 5s
      retries: 5

  notebook-processor:
    image: mhoelzl/clm-notebook-processor:0.3.0
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      - POSTGRES_URL=postgresql://clm:clm@postgres:5432/clm_jobs
    networks:
      - app-network

volumes:
  postgres-data:
```

### Pros of Approach 2

1. **✅ Production-Grade Concurrency**: PostgreSQL is battle-tested
   - Handles thousands of concurrent connections
   - MVCC (Multi-Version Concurrency Control)
   - No locking issues

2. **✅ Better Scalability**: Can scale beyond single machine
   - Connection pooling
   - Read replicas possible
   - Horizontal scaling options

3. **✅ Rich Features**:
   - JSONB for efficient JSON queries
   - Advanced indexing
   - Full-text search
   - Triggers, stored procedures

4. **✅ Operational Tools**: Mature ecosystem
   - pgAdmin for management
   - pg_stat for monitoring
   - WAL archiving for backups

5. **✅ No Sidecar Needed**: Direct connection from all workers

### Cons of Approach 2

1. **❌ Operational Complexity**:
   - Must run PostgreSQL server (even in development)
   - Configuration, tuning, maintenance
   - Resource overhead (~100MB RAM minimum)

2. **❌ Dual Database Support**:
   - SQLite for development/direct workers
   - PostgreSQL for Docker
   - Must maintain two schemas
   - Must test both backends thoroughly

3. **❌ Dependency Management**:
   - Requires psycopg2 (PostgreSQL driver)
   - PostgreSQL client libraries
   - Platform-specific compilation issues

4. **❌ Development Friction**:
   - Developers must run PostgreSQL locally
   - Slower test execution (network overhead)
   - More complex setup for new contributors

5. **❌ Migration Burden**:
   - Schema differences (AUTOINCREMENT vs SERIAL)
   - SQL dialect differences (CURRENT_TIMESTAMP vs NOW())
   - Data type differences (TEXT vs JSONB)
   - Must write migration scripts

6. **❌ Overkill for Single Machine**:
   - CLM is designed for single-machine course processing
   - Don't need distributed database features
   - Higher resource usage for no clear benefit

---

## Comparison Matrix

| Criterion | Current (Rollbacks) | Approach 1 (WAL+Sidecar) | Approach 2 (PostgreSQL) |
|-----------|--------------------|--------------------------|-----------------------|
| **Performance** | ❌ Poor (high contention) | ✅ Excellent (WAL mode) | ✅ Excellent (MVCC) |
| **Reliability** | ❌ Fails under load | ✅ Stable | ✅ Very stable |
| **Complexity** | ✅ Simple | ⚠️ Moderate (+sidecar) | ❌ High (2 backends) |
| **Operational Overhead** | ✅ None | ⚠️ Low (+1 process) | ❌ High (PG server) |
| **Development UX** | ❌ Flaky tests | ✅ Same as prod | ⚠️ Requires PG |
| **Production UX** | ❌ Unreliable | ✅ Simple | ⚠️ Complex setup |
| **Code Changes** | ⚠️ Workarounds everywhere | ✅ Minimal | ❌ Large refactor |
| **Testing Burden** | ⚠️ High (flaky) | ✅ Medium | ❌ High (2 backends) |
| **Resource Usage** | ✅ Minimal | ✅ Low | ❌ Moderate-High |
| **Scalability** | ❌ Limited | ⚠️ Single machine | ✅ Multi-machine |
| **Dependencies** | ✅ None | ✅ None | ❌ PostgreSQL |
| **Migration Effort** | N/A | ⚠️ 2-3 days | ❌ 1-2 weeks |

**Legend**: ✅ Good | ⚠️ Acceptable | ❌ Poor

---

## Alternative Approaches (Brief)

### 3. Hybrid: WAL + Shared Volume for Docker

**Idea**: Enable WAL mode and share it via Docker volume, rely on Docker's file system layer.

**Verdict**: ❌ **Not Recommended**
- WAL shared memory doesn't work across Docker boundaries
- Corruption risk on Windows/Mac (Docker uses VM)
- File locking issues on networked storage

### 4. Single Writer Process (Queue Proxy)

**Idea**: Only one process writes to DB, all others send write requests via queue.

**Verdict**: ⚠️ **Viable but Inferior to Approach 1**
- Similar to sidecar but more limited (write-only)
- Doesn't solve reader-writer blocking
- Approach 1's sidecar is more complete solution

### 5. Wait for SQLite "BEGIN CONCURRENT" Feature

**Idea**: Use experimental SQLite feature for better concurrency (post-3.42.0).

**Verdict**: ❌ **Too Experimental**
- Still in development/testing phase
- Not available in standard distributions
- Unknown stability characteristics

---

## Recommendation: Phased Approach 1

### Why Approach 1?

1. **Matches CLM's Scale**: Single-machine processing doesn't need PostgreSQL
2. **Minimal Disruption**: Works with existing architecture
3. **Gradual Migration**: Can adopt incrementally
4. **Best Performance/Complexity Ratio**: WAL gives 90% of PostgreSQL benefits with 10% of complexity

### Implementation Plan

#### Phase 1: WAL Mode for Direct Workers (Week 1)

**Goal**: Immediate relief for development and direct worker deployments

**Changes**:
1. Update `schema.py` to enable WAL mode by default
2. Add environment variable `CLM_JOURNAL_MODE` (default: `WAL`)
3. Auto-detect problematic platforms and fall back to DELETE mode
4. Update tests to handle both modes

**Risk**: Low
**Effort**: 1-2 days
**Benefit**: Eliminates 90% of contention issues in direct mode

#### Phase 2: Sidecar Implementation (Week 2-3)

**Goal**: Enable Docker workers with WAL mode

**Changes**:
1. Implement `db_proxy.py` FastAPI sidecar
2. Create `DirectSQLiteBackend` and `SidecarBackend` in `worker_base.py`
3. Add auto-detection of environment (direct vs Docker)
4. Update Docker Compose with sidecar service
5. Add integration tests for sidecar mode

**Risk**: Moderate (new component)
**Effort**: 3-5 days
**Benefit**: Full WAL benefits for Docker deployments

#### Phase 3: Optimization & Monitoring (Week 4)

**Goal**: Ensure production-ready stability

**Changes**:
1. Add sidecar health checks and auto-restart
2. Implement connection pooling in sidecar
3. Add metrics (requests/sec, latency, error rate)
4. Performance testing under load
5. Documentation and deployment guides

**Risk**: Low
**Effort**: 2-3 days
**Benefit**: Production confidence

### Rollback Plan

If Approach 1 fails or proves too complex:

1. **Immediate**: Revert to DELETE mode (1-line change)
2. **Short-term**: Continue with defensive rollbacks as workaround
3. **Long-term**: Re-evaluate Approach 2 (PostgreSQL) with lessons learned

---

## Decision Points

### When to Choose Approach 1

- ✅ You primarily use direct workers (development, small deployments)
- ✅ You want minimal operational complexity
- ✅ You deploy on platforms that support WAL mode (Linux, modern Windows)
- ✅ You're comfortable with a lightweight sidecar process
- ✅ Single-machine deployments are sufficient

### When to Choose Approach 2

- ✅ You need multi-machine scale (unlikely for CLM)
- ✅ You already run PostgreSQL infrastructure
- ✅ You need advanced database features (complex queries, full-text search)
- ✅ You have dedicated DevOps resources
- ❌ You're okay with significantly higher complexity

### When to Stick with Current Approach

- ❌ **NEVER** - The current approach is fundamentally broken under load
- The defensive rollbacks are a band-aid, not a solution
- Tests are flaky, production would be unreliable

---

## Conclusion

**Recommended Path**: **Implement Approach 1 (WAL + Sidecars) in 3 phases**

**Rationale**:
1. Solves the root cause (concurrency limitations)
2. Appropriate to CLM's scale and use case
3. Minimal operational overhead
4. Gradual migration reduces risk
5. Can fall back to DELETE mode if needed
6. Avoids the complexity of dual-database support

**Next Steps**:
1. Get approval for phased plan
2. Create feature branch: `feature/wal-mode-sidecar`
3. Implement Phase 1 (WAL for direct workers)
4. Test extensively on target platforms
5. Proceed to Phase 2 if Phase 1 succeeds

**Success Metrics**:
- Zero "readonly database" errors in logs
- Sub-100ms p99 latency for job operations
- 100% test pass rate under concurrent load
- No degradation with 4+ concurrent workers
