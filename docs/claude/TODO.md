# CLM TODO List

This file tracks known issues and planned improvements for the CLM project.

## Bugs / Technical Debt

### Worker Process Leaks on Windows (Kernel Teardown + Pool Sizing)

**Status**: 🔴 Open (2026-04-11) — forensic analysis complete, fixes proposed

**Proposal**: `docs/proposals/WORKER_CLEANUP_RELIABILITY.md`

Notebook worker Jupyter kernels are not reliably reaped on Windows when a
cell raises `RuntimeError`, leaving orphaned `python.exe` subprocesses after
every failing job. Compounded by default pool sizes of 18 workers per
`clm build`, iterative AI-driven sessions have accumulated 300+ orphaned
workers (~12 GB RAM) over a few days, eventually wedging Windows Terminal
and the WMI `winmgmt` service. Five prioritized fixes proposed — see the
proposal doc for evidence, root-cause analysis, and an implementation plan.

**Key evidence**:
- `cheeky-chasing-kite` worktree's `clm_jobs.db` has 4 orphaned job rows
  (`started_at` set, `completed_at` NULL) matching 4 failed cells in
  `slides_010v_custom_api_libraries.py` — direct proof that kernel cleanup
  doesn't run on `RuntimeError` from cell execution.
- Every worktree pool session reported `pool_stopped` cleanly, yet
  processes still leaked → the leak is below the pool-manager's visibility.

---

### ~~Flaky Test: `test_reconnect_loop_aborts_when_watchdog_stopped`~~ (FIXED)

**Status**: ✅ FIXED (2026-04-20)

**Location**: `tests/recordings/test_obs.py::TestObsClientWatchdog::test_reconnect_loop_aborts_when_watchdog_stopped`

**Symptom**: Under xdist load (32 workers) the assertion
`connection_state == 'disconnected'` occasionally observed
`'reconnecting'`.

**Root Cause**: Two independent bugs combined.

1. **Implementation race in `ObsClient`.** `_enter_reconnect_loop()`
   called `_set_state("reconnecting")` unconditionally at the top of
   the loop, with no check against `_watchdog_stop`. `disconnect()`
   does `_stop_watchdog()` (signals the event, `join(timeout=2.0)`),
   then `_disconnect_clients()`, then `_set_state("disconnected")`. If
   the watchdog thread was pre-empted between `_disconnect_clients()`
   and `_set_state("reconnecting")` — or the 2s join timed out under
   load — the watchdog's "reconnecting" write could land *after* the
   caller's "disconnected" write, leaving the wrong terminal state.

2. **Fixed `time.sleep(0.1)` precondition in the test.** The test used
   a fixed sleep to give the watchdog time to enter the reconnect
   loop. Under xdist this window wasn't always long enough, so
   `disconnect()` could fire before the race conditions above were
   actually exercised.

**Fix Applied**:

- `_set_state()` now ignores `"reconnecting"` transitions when
  `_watchdog_stop.is_set()` — once `disconnect()` has signalled the
  stop, the caller owns the terminal state.
- The test polls for `connection_state == "reconnecting"` (via a new
  `_poll_until` helper) instead of sleeping a fixed amount.
- The sibling `test_probe_failure_triggers_reconnect_and_state_transitions`
  was also refactored to use `_poll_until` for consistency.

Cross-reference: `worker test polling` feedback memory — fixed-time
sleeps / immediate assertions on background-thread state are the
recurring root cause.

---

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

Modified `DockerWorkerExecutor.start_worker()` in `src/clm/infrastructure/workers/worker_executor.py`:

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
- `src/clm/infrastructure/workers/worker_executor.py` (fix applied here)

---

### ~~Fix Flaky Test: `test_worker_tracks_statistics`~~ (FIXED)

**Status**: ✅ FIXED

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
- `src/clm/cli/main.py`: Signal handlers registered in `build()`, no-op handlers after success
- `src/clm/cli/build_reporter.py`: `_build_finished` flag to suppress late error reports

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

### MCP Tool: `course_authoring_rules`

**Status**: Planned (Phase 5 in handover)

**Documentation**: `docs/claude/mcp-slide-tooling-handover.md` (Phase 5)

Serve per-course authoring rules (student profile, voiceover policy, slide
conventions) via the MCP server. Takes a course spec slug or slide file path,
returns merged common + course-specific rules from `.authoring.md` companion
files in the PythonCourses `course-specs/` directory.

Independent of Phase 4 (slide IDs/voiceover separation) — can be implemented
at any time.

---

See `docs/developer-guide/architecture.md` for potential future enhancements.

---

**Last Updated**: 2026-04-20 (Fixed watchdog reconnect flake — `_set_state` guard + poll)
