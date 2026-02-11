# Valkey Job Scheduling Backend: Evaluation Report

**Date**: 2025-11-26
**Status**: Evaluation Complete
**Recommendation**: Not recommended for current phase

## Executive Summary

This document evaluates adding Valkey (Redis fork) as an alternative job scheduling backend while keeping SQLite for persistence. The analysis concludes that while **technically feasible**, this change offers **limited benefit** for CLM's current use case and is not recommended at this time.

---

## 1. Current Architecture Overview

The system currently uses SQLite in a dual role:

| Database | Purpose | Tables |
|----------|---------|--------|
| `clm_jobs.db` | Job scheduling | `jobs`, `workers`, `worker_events`, `results_cache` |
| `clm_cache.db` | Persistent result cache | `processed_files`, `processing_issues` |

**Key characteristics:**
- Workers poll for jobs via `get_next_job()` with atomic `SELECT ... UPDATE` transactions
- Backend polls job status via batch queries (`get_job_statuses_batch()`)
- Worker health tracked via heartbeat column (30-second threshold)
- Dead worker recovery runs every 5 seconds

---

## 2. Pros of a Valkey Scheduling Backend

### 2.1 Performance Advantages

| Feature | SQLite (Current) | Valkey |
|---------|------------------|--------|
| Job claim latency | ~1-5ms (disk I/O) | ~0.1-0.5ms (memory) |
| Pub/Sub notifications | Polling (0.1-0.5s delay) | Instant push notifications |
| Concurrent writers | WAL mode helps, but single-writer | True concurrent writes |
| Batch status queries | O(n) rows read | O(1) MGET operation |

**Estimated improvement**: 2-10x faster job dispatch for high-concurrency workloads (>50 concurrent jobs).

### 2.2 Scalability Benefits

1. **Distributed scheduling**: Multiple CLM instances could share a job queue
2. **Horizontal scaling**: Add more workers without SQLite lock contention
3. **Better memory utilization**: Hot data in RAM vs. SQLite page cache

### 2.3 Advanced Features

- **Blocking pop** (`BLPOP`): Workers wait for jobs without polling
- **TTL-based worker health**: No heartbeat table needed; use key expiration
- **Atomic job claiming**: `RPOPLPUSH` provides atomic dequeue + claim
- **Priority queues**: Multiple lists or sorted sets with scores

### 2.4 Operational Benefits

- **Real-time visibility**: `MONITOR` command for debugging
- **Built-in metrics**: `INFO` command provides queue depths, memory usage
- **Cluster mode**: Valkey Cluster for high availability (future-proofing)

---

## 3. Cons of a Valkey Scheduling Backend

### 3.1 Added Complexity

| Aspect | Current (SQLite) | With Valkey |
|--------|------------------|-------------|
| External dependencies | None (SQLite is bundled) | Valkey server required |
| Network considerations | Local file only | TCP connections, timeouts, reconnection |
| Data durability | Automatic (fsync) | Requires AOF/RDB configuration |
| Schema migrations | Simple SQL | Redis/Valkey data structure evolution |

### 3.2 Operational Overhead

1. **Deployment complexity**: Another service to deploy, monitor, and maintain
2. **Failure modes**: Network partitions, Valkey restarts, connection pool exhaustion
3. **Data loss risk**: Default Valkey is not durable; requires AOF with `appendfsync always`
4. **Memory management**: Must size Valkey appropriately; OOM kills jobs

### 3.3 Limited Benefit for Current Use Case

**CLM is typically single-instance**, processing one course at a time. The current architecture handles this well:
- ~50 concurrent jobs (configurable via `CLM_MAX_CONCURRENCY`)
- Job processing time dominates (notebook execution, diagram rendering)
- SQLite with WAL mode handles the load adequately

**The bottleneck is worker execution time, not job dispatch latency.**

### 3.4 Testing Burden

- Need Valkey running for integration tests
- Test matrix doubles (SQLite backend + Valkey backend)
- CI/CD pipeline complexity increases

---

## 4. Performance and Scalability Trade-offs

### 4.1 Quantitative Estimates

| Metric | SQLite | Valkey | Improvement |
|--------|--------|--------|-------------|
| Job submit latency | 1-5ms | 0.2-0.5ms | 5-10x |
| Job poll interval | 100-500ms | 0ms (BLPOP) | Eliminated |
| Max jobs/second throughput | ~200-500 | ~10,000+ | 20-50x |
| Worker count scalability | ~10-20 | ~100+ | 5-10x |

### 4.2 When Valkey Helps

- **High job volume**: >1,000 jobs per build
- **Multiple CLM instances**: Distributed course processing
- **Rapid iteration**: Watch mode with fast-changing files
- **CI/CD pipelines**: Many parallel builds

### 4.3 When Valkey Doesn't Help

- **Single-instance usage**: Most CLM deployments
- **Long-running jobs**: Notebook execution (seconds to minutes)
- **Small courses**: <100 files
- **Development/testing**: Simplicity wins

### 4.4 Assessment

**For CLM's current use case, the SQLite backend is sufficient.** Valkey would provide marginal performance gains that are overshadowed by actual job processing time.

---

## 5. Code Maintenance Burden

### 5.1 New Code Required

| Component | Estimated LOC | Complexity |
|-----------|---------------|------------|
| `ValkeyBackend` class | 300-400 | Medium |
| Valkey job queue utilities | 200-300 | Medium |
| Worker polling adapter | 150-200 | Low |
| Connection pooling/retry | 100-150 | Medium |
| Docker fallback logic | 100-150 | Medium |
| Tests (unit + integration) | 500-700 | High |
| **Total** | **~1,400-1,900** | |

### 5.2 Modified Code

| Component | Changes |
|-----------|---------|
| `WorkerBase` | Abstract job polling interface |
| `Backend` protocol | Minor (already abstract) |
| CLI commands | Backend selection flag |
| Configuration | Valkey connection settings |

### 5.3 Maintenance Considerations

1. **Two codepaths**: Every job scheduling change must work with both backends
2. **Feature parity**: Both backends must support job cancellation, worker health, etc.
3. **Documentation**: Usage guides for both backends
4. **Debugging**: Different tools and approaches per backend

**Estimated ongoing maintenance**: +15-25% effort for job scheduling features

---

## 6. Dependency Management Strategy

### 6.1 Option A: Optional External Valkey

```python
# pyproject.toml
[project.optional-dependencies]
valkey = ["valkey>=6.0", "hiredis>=2.0"]
```

**Pros**: Clean separation, no container management
**Cons**: User must install/manage Valkey

### 6.2 Option B: Docker Fallback (Recommended)

```python
class ValkeyBackend(LocalOpsBackend):
    def __init__(self, valkey_url: str | None = None):
        if valkey_url:
            self._connect(valkey_url)
        elif self._detect_valkey():
            self._connect("redis://localhost:6379")
        else:
            self._start_valkey_container()
```

**Implementation approach:**

```python
def _detect_valkey(self) -> bool:
    """Check if Valkey is available locally."""
    try:
        import valkey
        client = valkey.Valkey(host='localhost', port=6379, socket_timeout=1)
        client.ping()
        return True
    except Exception:
        return False

def _start_valkey_container(self) -> None:
    """Start Valkey in Docker if not available."""
    import docker
    client = docker.from_env()

    # Check for existing container
    try:
        container = client.containers.get("clm-valkey")
        if container.status != "running":
            container.start()
    except docker.errors.NotFound:
        container = client.containers.run(
            "valkey/valkey:8.0-alpine",
            name="clm-valkey",
            ports={"6379/tcp": 6379},
            detach=True,
            remove=True,  # Cleanup on stop
        )

    # Wait for Valkey to be ready
    self._wait_for_valkey()
```

**Pros**:
- Works out-of-the-box with Docker installed
- No system-level Valkey installation required
- Container is lightweight (~30MB)

**Cons**:
- Docker dependency (already present for workers)
- Startup latency (~1-2 seconds)
- Container lifecycle management

### 6.3 Option C: Embedded Valkey Alternative

Use an embedded key-value store like `diskcache` or `lmdb` for pub/sub semantics without external dependencies. However, this loses most Valkey benefits.

### 6.4 Recommended Strategy

**Docker Fallback (Option B)** with graceful degradation:

1. Check `VALKEY_URL` environment variable
2. If not set, probe `localhost:6379`
3. If not available, start Docker container
4. If Docker unavailable, fall back to SQLite with warning

This provides a seamless experience while allowing production deployments to use dedicated Valkey instances.

---

## 7. Robustness Analysis

### 7.1 Failure Modes Comparison

| Failure | SQLite Behavior | Valkey Behavior |
|---------|-----------------|-----------------|
| Process crash | Jobs in `processing` stay stuck until timeout | Same (but faster recovery via TTL) |
| Database corruption | Recoverable via WAL journal | AOF can be corrupted; requires backup |
| Disk full | Operations fail gracefully | Valkey rejects writes (configurable) |
| Network failure | N/A (local file) | Connection errors, reconnection needed |
| OOM | OS kills process | Valkey evicts data (configurable policy) |

### 7.2 Data Durability

| Configuration | SQLite | Valkey |
|---------------|--------|--------|
| Default | Durable (fsync on commit) | Not durable (data in RAM only) |
| Safe | `PRAGMA synchronous=FULL` | `appendfsync always` (slower) |
| Fast | `PRAGMA synchronous=OFF` | `appendfsync no` (risk data loss) |

**Recommendation**: For job scheduling, eventual persistence is acceptable. Use `appendfsync everysec` for balance.

### 7.3 Recovery Mechanisms

**Current SQLite approach:**
- Dead worker detection via heartbeat timeout
- Periodic cleanup (`_cleanup_dead_worker_jobs()`)
- Manual `reset_hung_jobs()` for stuck jobs

**Valkey approach:**
- TTL-based job ownership (automatic expiration)
- Pub/Sub for instant worker status changes
- `WATCH`/`MULTI` for optimistic locking

### 7.4 Robustness Verdict

**SQLite is more robust for single-instance deployments** due to:
- No network dependency
- Automatic durability
- Simpler failure modes

**Valkey is more robust for distributed deployments** due to:
- Faster failure detection
- Better concurrent access
- Built-in clustering (future)

---

## 8. Three Necessary Architectural Changes

If the decision is made to proceed with Valkey integration, the following architectural changes are required:

### 8.1 Change 1: Abstract Job Queue Interface

**Current state**: `JobQueue` class is tightly coupled to SQLite

**Required change**: Extract interface and create implementations

```python
# src/clm/infrastructure/database/job_queue_protocol.py
from typing import Protocol

class JobQueueProtocol(Protocol):
    """Abstract interface for job queue implementations."""

    def add_job(
        self,
        job_type: str,
        input_file: str,
        output_file: str,
        content_hash: str,
        payload: dict[str, Any],
        priority: int = 0,
        correlation_id: str | None = None,
    ) -> int: ...

    def get_next_job(self, job_type: str, worker_id: int | None = None) -> Job | None: ...

    def update_job_status(self, job_id: int, status: str, error: str | None = None): ...

    def get_job_statuses_batch(self, job_ids: list[int]) -> dict[int, tuple[str, str | None]]: ...

    def cancel_jobs_for_file(self, input_file: str, cancelled_by: str | None = None) -> list[int]: ...

    def is_job_cancelled(self, job_id: int) -> bool: ...

# Implementations:
# - SqliteJobQueue (existing, renamed)
# - ValkeyJobQueue (new)
```

**Impact**:
- Refactor `JobQueue` → `SqliteJobQueue`
- Workers use protocol, not concrete class
- Backend instantiates appropriate implementation

### 8.2 Change 2: Worker Registration Abstraction

**Current state**: Workers register directly in SQLite `workers` table

**Required change**: Abstract worker registry

```python
# src/clm/infrastructure/workers/worker_registry.py
from abc import ABC, abstractmethod

class WorkerRegistry(ABC):
    """Abstract interface for worker registration and health tracking."""

    @abstractmethod
    def register_worker(self, worker_id: str, worker_type: str) -> int: ...

    @abstractmethod
    def update_heartbeat(self, worker_id: int): ...

    @abstractmethod
    def get_available_workers(self, worker_type: str) -> int: ...

    @abstractmethod
    def mark_worker_dead(self, worker_id: int): ...

    @abstractmethod
    def cleanup_dead_workers(self) -> list[int]: ...

# Valkey implementation uses:
# - HSET for worker info
# - EXPIRE for automatic TTL-based heartbeat
# - SCAN for listing workers
```

**Impact**:
- Extract worker registration from `JobQueue`
- Valkey uses key expiration instead of heartbeat polling
- Simpler dead worker detection

### 8.3 Change 3: Backend Configuration System

**Current state**: Backend is instantiated directly with hardcoded SQLite

**Required change**: Factory pattern with configuration

```python
# src/clm/infrastructure/backend_factory.py
from enum import Enum
from typing import TypedDict

class BackendType(Enum):
    SQLITE = "sqlite"
    VALKEY = "valkey"

class BackendConfig(TypedDict, total=False):
    type: BackendType
    # SQLite options
    db_path: Path
    # Valkey options
    valkey_url: str
    auto_start_container: bool

def create_backend(config: BackendConfig) -> Backend:
    """Factory function to create appropriate backend."""
    backend_type = config.get("type", BackendType.SQLITE)

    if backend_type == BackendType.SQLITE:
        return SqliteBackend(
            db_path=config.get("db_path", Path("clm_jobs.db")),
            workspace_path=config.get("workspace_path", Path.cwd()),
        )
    elif backend_type == BackendType.VALKEY:
        return ValkeyBackend(
            valkey_url=config.get("valkey_url"),
            auto_start_container=config.get("auto_start_container", True),
            workspace_path=config.get("workspace_path", Path.cwd()),
        )
    else:
        raise ValueError(f"Unknown backend type: {backend_type}")
```

**CLI integration:**

```python
@click.option(
    "--backend",
    type=click.Choice(["sqlite", "valkey"]),
    default="sqlite",
    help="Job scheduling backend to use"
)
@click.option(
    "--valkey-url",
    envvar="VALKEY_URL",
    help="Valkey connection URL (auto-starts container if not specified)"
)
def build(backend: str, valkey_url: str | None, ...):
    config = {"type": BackendType(backend)}
    if valkey_url:
        config["valkey_url"] = valkey_url

    backend_instance = create_backend(config)
```

**Impact**:
- Clean separation of backend selection
- Environment variable support (`VALKEY_URL`)
- CLI flag for explicit selection
- Auto-container behavior is opt-in

---

## 9. Recommendation Summary

### Decision Matrix

| Factor | Weight | SQLite | Valkey |
|--------|--------|--------|--------|
| Current needs | High | ✅ Sufficient | ➖ Overkill |
| Implementation effort | High | ✅ Done | ❌ ~2 weeks |
| Maintenance burden | Medium | ✅ Low | ❌ +15-25% |
| Future scalability | Low | ➖ Limited | ✅ Excellent |
| Operational simplicity | High | ✅ Zero deps | ❌ Extra service |
| Performance | Low | ✅ Adequate | ✅ Better |

### Verdict: Not Recommended for Current Phase

**Rationale:**
1. **Job dispatch latency is not the bottleneck** — worker execution time dominates
2. **Single-instance usage** — no immediate need for distributed scheduling
3. **Maintenance overhead** — two backends to support, test, and document
4. **Diminishing returns** — effort vs. benefit ratio is unfavorable

### When to Reconsider

Consider adding Valkey if:
1. CLM is deployed as a multi-instance service
2. Job volume exceeds ~1,000 jobs per build regularly
3. Watch mode performance becomes a user complaint
4. Distributed CI/CD integration is needed

### Alternative Quick Wins

If job dispatch performance needs improvement without adding Valkey:

1. **Reduce poll interval** to 50ms (currently 100ms for workers)
2. **Add memory caching** in `JobQueue` for recent job statuses
3. **Use SQLite in-memory database** for job queue (persist only cache)

These changes provide 2-5x improvement with minimal code changes.

---

## 10. Conclusion

Adding Valkey as a job scheduling backend is architecturally sound but **premature optimization** for CLM's current use case. The SQLite-based architecture handles the workload adequately, and the implementation/maintenance cost outweighs the benefits.

If the decision is made to proceed, the three architectural changes outlined (abstract job queue interface, worker registry abstraction, and backend factory pattern) provide a clean path forward with minimal disruption to the existing codebase.

---

## Appendix: Key Files Referenced

- `src/clm/infrastructure/backend.py` — Backend abstract base class
- `src/clm/infrastructure/backends/sqlite_backend.py` — Current SQLite implementation
- `src/clm/infrastructure/database/job_queue.py` — Job queue operations
- `src/clm/infrastructure/database/schema.py` — Database schema
- `src/clm/infrastructure/workers/worker_base.py` — Worker base class
