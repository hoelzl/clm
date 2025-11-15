# CLX Architecture Simplification Proposal

## Executive Summary

This proposal outlines a simplified architecture for the CLX project that maintains all current functionality while significantly reducing complexity. The key change is replacing RabbitMQ with SQLite as the orchestration mechanism, enabling direct file system access and eliminating message serialization overhead.

## Current Architecture: Problems Identified

### 1. Overcomplicated Communication Layer
- **RabbitMQ is overkill** for a single-host application
- Requires running separate broker infrastructure
- Complex message serialization/deserialization
- Large files (e.g., trained ML models) cannot be efficiently transferred
- Correlation ID tracking system adds complexity

### 2. Fragmented Package Structure
- 4 separate packages: `clx`, `clx-cli`, `clx-common`, `clx-faststream-backend`
- Interdependencies make development and testing harder
- Adds cognitive overhead for understanding the system

### 3. Heavy Monitoring Infrastructure
- Prometheus, Grafana, Loki, and RabbitMQ Exporter
- Great for production distributed systems, but overkill for a development/educational tool
- Adds startup time and resource consumption
- Complex to configure and maintain

### 4. Indirect File Access
- Files must be read, serialized into messages, sent over RabbitMQ, deserialized
- Then processed and results serialized back
- Workarounds needed for large files

## Proposed Architecture: SQLite-Orchestrated Worker Pools

### Core Design Principles

1. **Simplicity First**: Use the simplest solution that meets requirements
2. **Direct File Access**: Workers read/write files directly via bind mounts
3. **Process Isolation**: Maintain Docker containers for kernel isolation
4. **Single Package**: Consolidate into one cohesive package
5. **Observable**: Easy to monitor and debug with standard SQL tools

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                         CLX Core                             │
│  ┌────────────┐     ┌──────────────┐    ┌──────────────┐   │
│  │   CLI      │────▶│   Course     │───▶│  Job Queue   │   │
│  │  (Click)   │     │  Processor   │    │   Manager    │   │
│  └────────────┘     └──────────────┘    └──────┬───────┘   │
│                                                  │           │
│                      ┌───────────────────────────┘           │
│                      ▼                                       │
│         ┌────────────────────────┐                          │
│         │   SQLite Database      │                          │
│         │  - Jobs Queue          │                          │
│         │  - Results Cache       │                          │
│         │  - Worker Status       │                          │
│         └────────┬───────────────┘                          │
│                  │                                           │
│                  │  Polling                                  │
│         ┌────────▼────────────────────────┐                 │
│         │   Worker Pool Manager           │                 │
│         │  - Manages 3 worker pools       │                 │
│         │  - Health monitoring            │                 │
│         │  - Auto-restart on hang         │                 │
│         └────────┬────────────────────────┘                 │
└──────────────────┼─────────────────────────────────────────┘
                   │
       ┌───────────┴──────────┬─────────────────┐
       ▼                      ▼                  ▼
┌──────────────┐      ┌──────────────┐   ┌──────────────┐
│  Notebook    │      │   DrawIO     │   │   PlantUML   │
│  Workers     │      │   Workers    │   │   Workers    │
│  (Pool of N) │      │  (Pool of N) │   │  (Pool of N) │
└──────┬───────┘      └──────┬───────┘   └──────┬───────┘
       │                     │                   │
       └──────────────┬──────┴───────────────────┘
                      ▼
              ┌───────────────┐
              │  Shared Files │
              │  (Bind Mount) │
              └───────────────┘
```

### Key Components

#### 1. SQLite Database (Replaces RabbitMQ)

**Jobs Table** (Replaces Message Queue):
```sql
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,           -- 'notebook', 'drawio', 'plantuml'
    status TEXT NOT NULL,              -- 'pending', 'processing', 'completed', 'failed'
    priority INTEGER DEFAULT 0,        -- For future prioritization

    -- File information
    input_file TEXT NOT NULL,
    output_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,

    -- Job-specific parameters (JSON)
    payload TEXT NOT NULL,

    -- Timing and tracking
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    worker_id INTEGER,

    -- Error handling
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error TEXT,

    FOREIGN KEY (worker_id) REFERENCES workers(id)
);

CREATE INDEX idx_jobs_status ON jobs(status, job_type);
CREATE INDEX idx_jobs_content_hash ON jobs(content_hash);
```

**Results Cache Table**:
```sql
CREATE TABLE results_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    output_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    result_metadata TEXT,              -- JSON with output info
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,

    UNIQUE(output_file, content_hash)
);

CREATE INDEX idx_cache_lookup ON results_cache(output_file, content_hash);
```

**Workers Table** (Health Monitoring):
```sql
CREATE TABLE workers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_type TEXT NOT NULL,         -- 'notebook', 'drawio', 'plantuml'
    container_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,              -- 'idle', 'busy', 'hung', 'dead'

    -- Health monitoring
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cpu_usage REAL,
    memory_usage REAL,

    -- Statistics
    jobs_processed INTEGER DEFAULT 0,
    jobs_failed INTEGER DEFAULT 0,
    avg_processing_time REAL,

    UNIQUE(container_id)
);

CREATE INDEX idx_workers_status ON workers(worker_type, status);
```

#### 2. Worker Pool Manager

**Responsibilities**:
- Start and manage pools of long-lived Docker containers
- Monitor worker health (CPU, memory, responsiveness)
- Detect hung workers (e.g., <1% CPU for 10+ seconds while busy)
- Automatically restart dead or hung workers
- Load balance jobs across available workers

**Pool Configuration**:
```python
# Default: 1 worker per type, configurable based on CPU cores
WORKER_POOLS = {
    'notebook': {
        'image': 'clx-notebook-processor',
        'count': 1,  # Can scale to cpu_count()
        'max_job_time': 600,  # 10 minutes timeout
        'memory_limit': '4g',
    },
    'drawio': {
        'image': 'clx-drawio-converter',
        'count': 1,
        'max_job_time': 60,
        'memory_limit': '1g',
    },
    'plantuml': {
        'image': 'clx-plantuml-converter',
        'count': 1,
        'max_job_time': 30,
        'memory_limit': '512m',
    }
}
```

**Worker Lifecycle**:
1. Start container with bind mount to workspace
2. Container starts worker script that polls database
3. Worker polls: `SELECT * FROM jobs WHERE status='pending' AND job_type=? LIMIT 1`
4. Update job status to 'processing', worker status to 'busy'
5. Read input file, process, write output file
6. Update job status to 'completed', worker status to 'idle'
7. Update heartbeat timestamp
8. Repeat from step 3

#### 3. Simplified Package Structure

**Single Package: `clx`**

```
clx/
├── pyproject.toml
├── src/
│   └── clx/
│       ├── __init__.py
│       ├── cli/                    # Merged from clx-cli
│       │   ├── __init__.py
│       │   ├── main.py
│       │   └── commands/
│       ├── core/                   # Core from clx
│       │   ├── course.py
│       │   ├── course_spec.py
│       │   └── course_files/
│       ├── database/               # Merged from clx-common
│       │   ├── __init__.py
│       │   ├── schema.py
│       │   ├── job_queue.py
│       │   └── cache.py
│       ├── workers/                # Replaces clx-faststream-backend
│       │   ├── __init__.py
│       │   ├── pool_manager.py
│       │   ├── worker_base.py
│       │   └── health_monitor.py
│       └── messaging/              # Merged from clx-common
│           ├── payloads.py
│           └── results.py
├── services/                       # Worker implementations
│   ├── notebook_processor/
│   ├── drawio_converter/
│   └── plantuml_converter/
└── tests/
```

#### 4. Worker Communication Protocol

**Job Execution Flow**:
```python
# Worker-side pseudocode
while True:
    # Poll for job
    job = db.get_next_job(worker_type=MY_TYPE)
    if not job:
        sleep(0.1)  # Avoid busy-waiting
        continue

    # Update status
    db.update_job_status(job.id, 'processing', worker_id=my_id)
    db.update_worker_heartbeat(my_id)

    try:
        # Read input directly from file system
        input_data = read_file(job.input_file)

        # Process (existing logic)
        result = process(input_data, job.payload)

        # Write output directly to file system
        write_file(job.output_file, result)

        # Mark complete
        db.update_job_status(job.id, 'completed')
        db.increment_worker_stats(my_id, success=True)

    except Exception as e:
        # Mark failed
        db.update_job_status(job.id, 'failed', error=str(e))
        db.increment_worker_stats(my_id, success=False)

    finally:
        db.update_worker_status(my_id, 'idle')
        db.update_worker_heartbeat(my_id)
```

### Benefits of New Architecture

#### 1. **Dramatic Simplification**
- **From**: 4 packages + RabbitMQ + Prometheus + Grafana + Loki
- **To**: 1 package + SQLite
- Reduced Docker services from 8 to 3 (just the workers)
- No message serialization/deserialization overhead

#### 2. **Better Performance**
- Direct file system access (no serialization)
- No payload size limits (can handle trained ML models)
- Long-lived workers eliminate container startup overhead
- Efficient polling with minimal CPU usage

#### 3. **Easier Debugging**
- Query SQLite to see job status: `SELECT * FROM jobs WHERE status='failed'`
- View worker health: `SELECT * FROM workers WHERE status='hung'`
- No need to navigate RabbitMQ management UI
- Standard SQL tools work out of the box

#### 4. **Improved Monitoring**
- Simple SQL queries for metrics
- Can add `clx status` command to show:
  - Active jobs
  - Completed jobs
  - Failed jobs
  - Worker health
  - Cache hit rate
- Can still add Prometheus/Grafana later if needed

#### 5. **Easier Testing**
- No need to mock RabbitMQ
- Can use in-memory SQLite for tests
- Simpler integration tests
- Faster test execution

#### 6. **Better Resource Efficiency**
- No RabbitMQ overhead (~200MB RAM)
- No monitoring stack overhead (~500MB RAM)
- Workers are lightweight and efficient
- SQLite is fast and uses minimal resources

### Comparison: Before vs After

| Aspect | Current (RabbitMQ) | Proposed (SQLite) |
|--------|-------------------|-------------------|
| **Packages** | 4 separate packages | 1 unified package |
| **Docker Services** | 8 (3 workers + 5 infrastructure) | 3 (just workers) |
| **Message Queue** | RabbitMQ broker | SQLite database |
| **File Transfer** | Serialized in messages | Direct file system access |
| **Max File Size** | Limited by message size | Unlimited |
| **Worker Startup** | Per-job (slow) | Long-lived pools (fast) |
| **Monitoring** | Prometheus + Grafana | SQL queries + optional CLI |
| **Debugging** | RabbitMQ UI + logs | SQL queries + logs |
| **Infrastructure Setup** | Complex (5+ services) | Simple (SQLite file) |
| **Memory Usage** | ~1.5GB (including monitoring) | ~500MB (workers only) |
| **Startup Time** | ~30 seconds | ~5 seconds |
| **Testing** | Complex (mock broker) | Simple (in-memory DB) |

## Migration Strategy: Incremental Steps

The migration will proceed in phases, each resulting in a fully functional system that can be tested.

### Phase 1: Add SQLite Job Queue Alongside RabbitMQ

**Goal**: Introduce SQLite infrastructure without breaking existing system

**Steps**:
1. Create new database schema with jobs, results_cache, workers tables
2. Add `JobQueue` class that manages SQLite operations
3. Modify operations to write to BOTH RabbitMQ and SQLite
4. Add feature flag: `USE_SQLITE_QUEUE` (default: False)
5. Verify both systems work in parallel

**Testing**:
- Run existing tests
- Verify jobs are written to both RabbitMQ and SQLite
- Check that SQLite schema is correct

**Deliverable**: Working system with dual-queue support

---

### Phase 2: Create Worker Pool Manager

**Goal**: Implement new worker management system

**Steps**:
1. Create `WorkerPoolManager` class
2. Implement worker health monitoring
3. Add container management (start, stop, restart)
4. Implement job polling logic in workers
5. Run alongside existing RabbitMQ workers (different job types?)

**Testing**:
- Test worker startup/shutdown
- Test health monitoring and auto-restart
- Test job polling and execution
- Verify workers can process files correctly

**Deliverable**: Working worker pool system running in parallel

---

### Phase 3: Switch Services to SQLite

**Goal**: Migrate each service from RabbitMQ to SQLite

**Approach**: Migrate one service at a time

1. **Migrate PlantUML** (simplest)
   - Modify PlantUML converter to poll SQLite
   - Update `PlantUmlFile.process()` to use JobQueue
   - Test thoroughly

2. **Migrate DrawIO**
   - Similar to PlantUML
   - Test thoroughly

3. **Migrate Notebook Processor** (most complex)
   - Modify notebook processor to poll SQLite
   - Update `NotebookFile.process()` to use JobQueue
   - Test all output combinations

**Testing**:
- Test each service independently
- Run integration tests with all services
- Verify cache still works correctly

**Deliverable**: All services using SQLite, RabbitMQ no longer needed

---

### Phase 4: Remove RabbitMQ Infrastructure

**Goal**: Clean up obsolete RabbitMQ code

**Steps**:
1. Remove RabbitMQ from docker-compose.yaml
2. Remove `clx-faststream-backend` package
3. Remove RabbitMQ dependencies from pyproject.toml
4. Remove FastStream-related code
5. Remove Prometheus, Grafana, Loki (optional: keep if desired)
6. Update documentation

**Testing**:
- Full regression test suite
- Verify no RabbitMQ references remain
- Test clean startup without broker

**Deliverable**: System without RabbitMQ

---

### Phase 5: Consolidate Packages

**Goal**: Merge all packages into single `clx` package

**Steps**:
1. Merge `clx-common` into `clx`
   - Move messaging classes
   - Update imports
   - Test

2. Merge `clx-cli` into `clx`
   - Move CLI code to `clx.cli`
   - Update entry points
   - Test

3. Clean up package structure
   - Remove unused dependencies
   - Optimize imports
   - Update documentation

**Testing**:
- Full test suite
- Test CLI installation and usage
- Verify all imports work correctly

**Deliverable**: Single unified package

---

### Phase 6: Add Enhanced Monitoring

**Goal**: Add simple, built-in monitoring commands

**Steps**:
1. Add `clx status` command
   - Show worker health
   - Show job queue status
   - Show cache statistics
   - Show recent errors

2. Add `clx workers` command
   - List all workers
   - Show detailed worker stats
   - Allow restarting workers

3. Add `clx jobs` command
   - List jobs by status
   - Show job history
   - Allow canceling jobs

4. Add `clx cache` command
   - Show cache hit rate
   - Show cache size
   - Allow clearing cache

**Testing**:
- Test each command
- Verify output is helpful and readable
- Test with various system states

**Deliverable**: Easy-to-use monitoring tools

---

### Phase 7: Optional Enhancements

**Goal**: Add nice-to-have features

**Possible Enhancements**:
1. **Web UI**: Simple Flask/FastAPI dashboard showing system status
2. **Job Priorities**: Allow high-priority jobs to jump the queue
3. **Remote Workers**: Allow workers on different machines (future distributed support)
4. **Prometheus Exporter**: Simple exporter for users who want Prometheus
5. **Auto-scaling**: Automatically adjust worker count based on queue depth

## SQLite Implementation Findings

### Production Experience: Journal Modes and Cross-Platform Compatibility

After implementing and testing the SQLite-based job queue system, we discovered important limitations and best practices:

#### WAL Mode Limitations with Docker on Windows

**Discovery**: WAL (Write-Ahead Logging) mode, while excellent for concurrent access in native environments, **does not work reliably across Docker volume mounts on Windows**.

**Root Cause**:
- WAL mode requires shared memory files (`-shm` and `-wal` files)
- Docker on Windows crosses OS boundaries (Windows host → Linux VM → Container)
- Two different OS kernels cannot coordinate SQLite's lock files properly
- Results in `sqlite3.OperationalError: disk I/O error`

**Affected Scenario**: Mixed-mode deployments where both direct (host process) and Docker workers access the same database file.

#### Recommended Configuration: DELETE Journal Mode

**Current Implementation**:
```python
# In clx_common/database/schema.py
conn.execute("PRAGMA journal_mode=DELETE")
```

**Why DELETE mode**:
- ✅ Works reliably across Docker volume mounts
- ✅ Cross-platform compatible (Windows, Linux, macOS)
- ✅ Works with both direct and containerized workers
- ✅ Simpler locking mechanism (no shared memory files)
- ⚠️ Slightly lower write concurrency (one writer at a time)

**Performance Impact**: For job queue use cases, the concurrency difference is negligible because job processing time far exceeds database write time.

#### Additional Robustness Measures

**1. Busy Timeout** (30 seconds):
```python
conn = sqlite3.connect(str(db_path), timeout=30.0)
```
Gives SQLite time to wait for locks instead of failing immediately.

**2. Retry Logic with Exponential Backoff**:
```python
# In worker registration
max_retries = 5
retry_delay = 0.5  # 500ms, doubles each retry
# Handles transient lock contention during startup
```

**3. Thread-Local Connections**:
```python
# One connection per thread to avoid connection sharing issues
self._local.conn = sqlite3.connect(...)
```

### Production Deployment Patterns

**Supported Configurations**:
1. ✅ **All-Direct**: All workers as host processes
2. ✅ **All-Docker**: All workers in containers
3. ✅ **Mixed-Mode**: Direct + Docker workers (with DELETE journal mode)
4. ✅ **Windows + Docker**: Works correctly with proper configuration

**Not Recommended**:
- ❌ WAL mode with Docker volume mounts on Windows
- ❌ Network filesystem mounts (NFS, SMB) with WAL mode

### Testing Results

All 7 integration tests pass, including:
- Direct worker startup and registration
- Multiple direct workers
- Docker workers in containers
- Mixed-mode (direct + Docker) deployment
- Health monitoring and graceful shutdown
- Stale worker cleanup

**Test Coverage**: `test_direct_integration.py` validates the complete worker lifecycle across all deployment modes.

## Risk Mitigation

### Identified Risks

1. **SQLite Concurrency**: SQLite has limitations with concurrent writes
   - **Mitigation**: Use DELETE journal mode (not WAL), 30s busy timeout, retry logic
   - **Reality**: Thoroughly tested - works reliably for single-host use case
   - **Evidence**: All integration tests pass including mixed-mode scenarios

2. **Worker Polling Overhead**: Polling database could be inefficient
   - **Mitigation**: Use exponential backoff, SQLite triggers for notifications
   - **Reality**: Negligible overhead with proper sleep intervals

3. **Migration Complexity**: Breaking existing functionality
   - **Mitigation**: Incremental migration with testing at each step
   - **Reality**: Dual-system approach minimizes risk

4. **Lost Features**: Some RabbitMQ features might be missed
   - **Mitigation**: Identify critical features and implement in SQLite
   - **Reality**: Job queue, retry, error handling all possible in SQLite

### Testing Strategy

1. **Unit Tests**: Test each component independently
2. **Integration Tests**: Test end-to-end workflows
3. **Performance Tests**: Ensure new system is as fast or faster
4. **Stress Tests**: Test with large numbers of jobs
5. **Regression Tests**: Ensure all current features still work

## Success Criteria

The migration will be considered successful when:

1. ✅ All existing functionality preserved
2. ✅ Single unified package structure
3. ✅ No RabbitMQ dependency
4. ✅ Direct file system access for all workers
5. ✅ Built-in monitoring commands
6. ✅ Faster startup time
7. ✅ Lower memory usage
8. ✅ Easier to understand and maintain
9. ✅ All tests passing
10. ✅ Documentation updated

## Timeline Estimate

| Phase | Estimated Time | Risk Level |
|-------|---------------|------------|
| Phase 1: Dual Queue | 1-2 days | Low |
| Phase 2: Worker Pools | 2-3 days | Medium |
| Phase 3: Service Migration | 3-4 days | Medium |
| Phase 4: Remove RabbitMQ | 1 day | Low |
| Phase 5: Consolidate Packages | 2-3 days | Medium |
| Phase 6: Monitoring | 1-2 days | Low |
| **Total** | **10-15 days** | |

## Conclusion

This architectural simplification will result in a system that is:
- **Simpler**: 1 package instead of 4, SQLite instead of RabbitMQ
- **More Robust**: Direct file access, no serialization issues
- **Easier to Monitor**: SQL queries and built-in CLI commands
- **Better Performing**: Long-lived workers, no message overhead
- **Easier to Test**: In-memory SQLite, simpler mocking
- **More Maintainable**: Unified codebase, clearer architecture

The incremental migration strategy ensures we maintain a working system at each step, minimizing risk while achieving significant architectural improvements.
