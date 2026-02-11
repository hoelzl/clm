# Worker Pre-Registration Performance Optimization

**Date**: 2025-12-16
**Status**: Implemented

## Executive Summary

Investigation revealed that the primary startup bottleneck in CLM is the worker registration wait mechanism, which adds 2-10 seconds before notebook processing begins. This document describes the problem, proposed solution, and implementation plan.

## Problem Analysis

### Previous Registration Flow (Before This Change)

1. Parent process (CLM) starts worker subprocess
2. Worker subprocess starts Python, imports modules (~500ms-1s for notebook worker)
3. Worker calls `register_worker_with_retry()` which:
   - Connects to SQLite database
   - INSERTs row into `workers` table
   - Returns auto-generated `worker_id`
4. Parent process polls database in `_wait_for_worker_registration()` with **10-second timeout**
5. Once worker is registered, parent considers it "ready"

### Bottleneck Impact

- **Per-worker delay**: 2-10 seconds (depending on system load and worker type)
- **Notebook worker**: Heaviest imports (numpy, jupyter) = longest delay
- **Sequential nature**: Even with parallel subprocess starts, each waits for registration

### Secondary Bottlenecks Identified

| Bottleneck | Location | Impact |
|------------|----------|--------|
| Worker registration wait | `pool_manager.py:485-524` | 2-10s (CRITICAL) |
| Stale worker cleanup | `pool_manager.py:214-334` | 1-5s |
| Eager Docker client init | `lifecycle_manager.py:88-102` | 0.5-1s |
| Sequential topic scanning | `course.py:419-487` | 0.5-2s |

## Solution: Worker Pre-Registration

### New Flow

1. Parent INSERTs worker row with `status='created'` and UUID **before** starting subprocess
2. Parent gets `worker_id` immediately
3. Parent passes `worker_id` to subprocess via `CLM_WORKER_ID` environment variable
4. Parent proceeds immediately (no waiting)
5. Worker subprocess starts, updates status from `created` to `idle` when ready
6. Jobs queue up and workers claim them as they become ready

### Database Schema Changes

Add new status value to workers table:
```sql
status TEXT NOT NULL CHECK(status IN ('created', 'idle', 'busy', 'hung', 'dead'))
```

- `created`: Parent created row, worker not yet started
- `idle`: Worker running and ready for jobs
- `busy`: Worker processing a job
- `hung`: Worker not responding (detected via heartbeat)
- `dead`: Worker terminated

### Worker Identification

- Use UUID assigned by parent instead of PID/hostname
- UUID passed via `CLM_WORKER_ID` environment variable
- Eliminates race condition on container_id prediction

### Health Check Changes

"Worker is active" = row exists AND status ≠ `created`

```python
# Before
cursor.execute("SELECT COUNT(*) FROM workers WHERE status IN ('idle', 'busy')")

# After
cursor.execute("SELECT COUNT(*) FROM workers WHERE status IN ('idle', 'busy')")
# (unchanged - 'created' workers already excluded)
```

## Mitigations

### 1. Stuck `created` Worker Cleanup

Workers that remain in `created` status for too long are considered failed to start.

```python
# In cleanup_stale_workers()
CREATED_TIMEOUT = 30  # seconds

# Find workers stuck in 'created' status
cursor.execute("""
    SELECT id, container_id, started_at
    FROM workers
    WHERE status = 'created'
    AND started_at < datetime('now', '-30 seconds')
""")
for worker_id, container_id, started_at in cursor.fetchall():
    logger.warning(f"Worker {worker_id} stuck in 'created' status, removing")
    cursor.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
```

### 2. Orphaned Worker Detection

If parent process dies, clean up its workers immediately:

```python
# Workers where parent PID no longer exists
import os

cursor.execute("SELECT id, parent_pid FROM workers WHERE status = 'created'")
for worker_id, parent_pid in cursor.fetchall():
    if parent_pid and not _process_exists(parent_pid):
        logger.warning(f"Worker {worker_id} orphaned (parent {parent_pid} dead), removing")
        cursor.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
```

### 3. First Worker Ready Notification

Optional: Log when the first worker transitions to `idle` for user visibility.

```python
# In worker startup
self._log_event("worker_ready", f"Worker {self.worker_id} ready to accept jobs")
```

### 4. Startup Failure Events

Workers that fail to start should log to `worker_events` table:

```python
# Already exists in worker_base.py
self._log_event("worker_failed", f"Worker failed to start: {error}")
```

## Benefits

1. **Eliminates 2-10 second wait per worker** - primary benefit
2. **True parallel startup** - workers start simultaneously without registration waits
3. **Cleaner architecture** - decouples "starting workers" from "submitting work"
4. **Eliminates false-positive failures** - no more spurious "failed to register" from slow DB

## Potential Concerns (Addressed)

| Concern | Resolution |
|---------|------------|
| Delayed error visibility | Worker events table logs failures; health checks detect stuck workers |
| Jobs before workers ready | `SqliteBackend._get_available_workers()` waits up to 30s for workers to activate |
| Stuck `created` cleanup | 30-second timeout + parent PID check |
| Database constraints | UUID assigned by parent avoids conflicts |

### Job Submission Wait-for-Activation

When jobs are submitted to `SqliteBackend`, the `_get_available_workers()` method now:

1. First checks for already-activated workers (status='idle' or 'busy' with recent heartbeat)
2. If none found, checks for pre-registered workers (status='created')
3. If pre-registered workers exist, polls every 0.5s for up to 30 seconds waiting for them to activate
4. Only raises "No workers available" error if:
   - No workers exist at all (not even pre-registered), OR
   - Timeout waiting for workers to activate

This ensures seamless operation when workers are pre-registered but haven't fully started yet.

## Implementation Checklist

- [x] Update database schema to add `created` status
- [x] Modify `WorkerPoolManager._start_worker()` to pre-register
- [x] Add UUID generation for worker identification
- [x] Pass `CLM_WORKER_ID` environment variable to subprocess
- [x] Modify `Worker` class to accept pre-assigned worker_id
- [x] Update worker startup to transition `created` → `idle`
- [x] Update `cleanup_stale_workers()` for stuck `created` workers
- [x] Add parent PID orphan detection for `created` workers
- [x] Add `get_or_register_worker()` helper method
- [x] Add schema migration from v6 to v7
- [x] Update tests

## Files Modified

| File | Changes |
|------|---------|
| `src/clm/infrastructure/database/schema.py` | Added `created` status to CHECK constraint, v6→v7 migration |
| `src/clm/infrastructure/workers/pool_manager.py` | Added `_pre_register_worker()`, `_cleanup_stuck_created_workers()`, `_is_process_alive()`, modified `_start_worker()` |
| `src/clm/infrastructure/workers/worker_executor.py` | Added `db_worker_id` parameter to `start_worker()` for Direct and Docker executors |
| `src/clm/infrastructure/workers/worker_base.py` | Added `activate_pre_registered_worker()`, `activate_pre_registered_worker_via_api()`, `get_or_register_worker()` |
| `src/clm/infrastructure/backends/sqlite_backend.py` | Modified `_get_available_workers()` to wait for pre-registered workers to activate |
| `src/clm/infrastructure/api/client.py` | Added `activate()` method for Docker workers |
| `src/clm/infrastructure/api/models.py` | Added `WorkerActivationRequest`, `WorkerActivationResponse` |
| `src/clm/infrastructure/api/worker_routes.py` | Added `/api/worker/activate` endpoint |
| `src/clm/workers/notebook/notebook_worker.py` | Updated `main()` to use `get_or_register_worker()` |
| `src/clm/workers/plantuml/plantuml_worker.py` | Updated `main()` to use `get_or_register_worker()` |
| `src/clm/workers/drawio/drawio_worker.py` | Updated `main()` to use `get_or_register_worker()` |
| `tests/infrastructure/workers/test_pool_manager.py` | Updated tests for new pre-registration flow |
| `docs/developer-guide/worker-lifecycle-management.md` | Added documentation for pre-registration |

## Testing

The implementation was verified with:

1. All 576 infrastructure tests passing
2. Schema migration tests (v1→v7 full chain)
3. Pool manager tests updated to mock new pre-registration flow
4. Parallel startup performance tests updated

## Future Optimizations

The following secondary bottlenecks were identified but not addressed in this change:

| Bottleneck | Location | Impact | Status |
|------------|----------|--------|--------|
| Stale worker cleanup | `pool_manager.py:214-334` | 1-5s | Not addressed |
| Eager Docker client init | `lifecycle_manager.py:88-102` | 0.5-1s | Not addressed |
| Sequential topic scanning | `course.py:419-487` | 0.5-2s | Not addressed |

These can be addressed in future optimization passes if needed.
