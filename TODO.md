# CLX TODO List

This file tracks known issues and planned improvements for the CLX project.

## Bugs / Technical Debt

### Docker Worker Registration Timeout in Tests

**Location**: `tests/e2e/test_e2e_lifecycle.py` and `tests/infrastructure/workers/test_lifecycle_integration.py`

**Failing Tests**:
- `test_e2e_managed_workers_docker_mode`
- `test_e2e_persistent_workers_docker_workflow`
- `test_start_managed_workers_docker`
- `test_start_persistent_workers_docker`

**Issue**: Docker workers start successfully (containers are created and running) but fail to register in the SQLite database within the timeout period.

**Error Output**:
```
ERROR    clx.infrastructure.workers.pool_manager:pool_manager.py:486 Worker notebook-0 (executor_id: cf7597853f5e...) failed to register in database.
ERROR    clx.infrastructure.workers.pool_manager:pool_manager.py:493 Check container logs with: docker logs clx-notebook-worker-0
```

**Root Cause Analysis**:
1. The container starts successfully (HTTP 204 from Docker API)
2. The worker inside the container fails to register within the default timeout
3. This could be due to:
   - Container startup overhead (loading Python, dependencies)
   - Database path or network connectivity issues between container and host
   - Worker configuration issues inside the Docker image
   - Race condition in database registration

**Debugging Steps**:
1. Check container logs: `docker logs clx-notebook-worker-0`
2. Verify database path is mounted correctly in container
3. Verify Docker image version matches expected (mhoelzl/clx-notebook-processor:0.3.0)
4. Check if database file permissions allow write access from container

**Impact**: These tests are NOT related to the shared image storage feature. They are pre-existing infrastructure tests for Docker worker lifecycle management.

**Priority**: Medium (infrastructure tests, Docker mode still works in production)

**Related Files**:
- `src/clx/infrastructure/workers/pool_manager.py`
- `src/clx/infrastructure/workers/worker_executor.py`
- `tests/e2e/test_e2e_lifecycle.py`
- `tests/infrastructure/workers/test_lifecycle_integration.py`

---

### Fix Flaky Test: `test_worker_tracks_statistics`

**Location**: `tests/infrastructure/workers/test_worker_base.py:326`

**Issue**: The test `test_worker_tracks_statistics` is timing-sensitive and occasionally fails with:
```
assert avg_time > 0
E   assert 0.0 > 0
```

**Root Cause**: The mock worker processes jobs nearly instantaneously (no real work), so `avg_processing_time` can be 0.0 due to floating-point precision or the time measurement being too fast.

**Proposed Fix Options**:
1. Add a small artificial delay in the mock worker's `process_job()` method (e.g., `time.sleep(0.001)`)
2. Change the assertion to `assert avg_time >= 0` if zero is acceptable
3. Mock the time measurement to ensure non-zero processing time
4. Use `pytest.approx` with appropriate tolerance

**Priority**: Low (test infrastructure, not affecting production code)

**Related Files**:
- `tests/infrastructure/workers/test_worker_base.py`
- `src/clx/infrastructure/workers/worker_base.py`

---

### ~~Fix Spurious "Aborted!" Message After Successful Builds~~ (FIXED)

**Status**: ✅ FIXED (2025-11-26)

**Fix Applied**: Three-part fix for signal handling and late error reports:

1. **Moved signal handler registration from `main()` (async) to `build()` (sync)**:
   - Signal handlers are now registered in `build()` BEFORE `asyncio.run()` starts
   - This ensures handlers remain active during ALL of `asyncio.run()` including cleanup

2. **Install no-op signal handlers after successful build**:
   - After a successful build, install "ignore signal" handlers instead of restoring defaults
   - This prevents "Aborted!" from being printed when late signals arrive during:
     - Click's cleanup after `build()` returns
     - Python's atexit handlers during interpreter shutdown
     - Worker subprocess cleanup

3. **Suppress late error/warning reports after build finishes** (in BuildReporter):
   - Added `_build_finished` flag that is set when `finish_build()` is called
   - `report_error()` and `report_warning()` check this flag and return early if set
   - This prevents spurious errors from worker shutdown (due to process termination
     interrupting ongoing work) from appearing after the build summary

**Key Changes**:
- `src/clx/cli/main.py`: Signal handlers registered in `build()`, no-op handlers after success
- `src/clx/cli/build_reporter.py`: `_build_finished` flag to suppress late error reports

**Original Issue**: After a successful build completes, users sometimes saw "Aborted!" printed to the terminal along with spurious error messages. This was caused by timing interactions between signal handlers, asyncio cleanup, Click's exception handling, worker subprocess termination signals, and late-arriving error reports from interrupted workers.

---

## Future Enhancements

See `docs/developer-guide/architecture.md` for potential future enhancements.

---

**Last Updated**: 2025-11-26 (Added Docker worker registration issue, fixed shared image file staging)
