# CLX TODO List

This file tracks known issues and planned improvements for the CLX project.

## Bugs / Technical Debt

### ~~Docker Worker Registration Timeout in Tests~~ (FIXED)

**Status**: ✅ FIXED (2025-12-04)

**Location**: `tests/e2e/test_e2e_lifecycle.py` and `tests/infrastructure/workers/test_lifecycle_integration.py`

**Previously Failing Tests**:
- `test_e2e_managed_workers_docker_mode`
- `test_e2e_persistent_workers_docker_workflow`
- `test_start_managed_workers_docker`
- `test_start_persistent_workers_docker`

**Root Cause** (Verified 2025-12-04):

The issue was **MSYS/Git Bash path conversion on Windows**. When running Docker commands from Git Bash on Windows, paths that look like Unix paths (e.g., `/db/test.db`) are automatically converted to Windows paths (e.g., `C:/Program Files/Git/db/test.db`). This affected the `DB_PATH` environment variable passed to containers, causing workers to look for a non-existent database file.

**Fix Applied**:

Modified `DockerWorkerExecutor.start_worker()` in `src/clx/infrastructure/workers/worker_executor.py`:

1. **Double-slash path prefix on Windows**: Use `//db/filename` instead of `/db/filename` for container paths on Windows. MSYS treats `//` as a UNC path prefix and does not convert it.

2. **Added `PYTHONUNBUFFERED=1`**: Enable immediate log output from containers for easier debugging.

**Key Changes**:
```python
# Use double-slash prefix for container paths to prevent MSYS/Git Bash
# path conversion on Windows.
db_path_in_container = f"//db/{db_filename}" if sys.platform == "win32" else f"/db/{db_filename}"

environment={
    "DB_PATH": db_path_in_container,
    "PYTHONUNBUFFERED": "1",  # Enable immediate log output
    ...
}
```

**Verification**:
- Manually tested worker registration with the fix: Worker successfully registered in database
- All 20 unit tests in `test_worker_executor.py` pass

**Related Files**:
- `src/clx/infrastructure/workers/worker_executor.py` (fix applied here)

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

## In Progress Features

### Notebook Error Context Tracking (Phase 2 Pending)

**Status**: Phase 1 Complete, Phase 2 (Execution-Time Tracking) Pending

**Documentation**: `docs/claude/design/notebook-error-context-tracking.md`

**Completed Work**:
- Fixed line_number extraction to handle "Line: N" format
- Fixed code_snippet extraction to stop at "Error:" line
- Added CellContext dataclass and _current_cell attribute
- Updated _enhance_notebook_error to prioritize tracked cell context
- Created comprehensive TDD test suite (17 tests)

**Remaining Work**:
- Implement execution-time cell tracking (hook into ExecutePreprocessor)
- Run Docker integration tests for C++ error formats
- Verify with actual failing course content

**Related Commits**: `1ce3630`, `56d88f4`, `8a7bc87`, `1011059`, `ea81ef7`

---

## Future Enhancements

See `docs/developer-guide/architecture.md` for potential future enhancements.

---

**Last Updated**: 2025-12-14 (Added Notebook Error Context Tracking feature status)
