# CLM TODO List

This file tracks known issues and planned improvements for the CLM project.

## Bugs / Technical Debt

### ~~Flaky Test: `test_heartbeat_round_trip_smoke`~~ (FIXED)

**Status**: ✅ FIXED (2026-05-25)

**Location**: `tests/infrastructure/database/test_worker_heartbeats.py::test_heartbeat_round_trip_smoke`

**Symptom**: Failed once during a full-suite `uv run pytest` run on 32
xdist workers. Passes cleanly in isolation. The test writes a
short end-to-end-shaped sequence of heartbeats and asserts the final
row reflects the last write (`last_output_excerpt == "done"`,
`current_cell_index == 1`).

**Root Cause**: the test used a single `time.sleep(0.01)` between two
`record_output` calls and then read the row immediately after the
final write. Under 32-worker xdist load that 10 ms window is too
short for the fresh reader connection to observe the latest committed
state — the row can be sampled mid-sequence, leaving the assertion
on intermediate values.

**Fix Applied**:

- Added a module-level `_poll_until` helper mirroring the one in
  `tests/recordings/test_obs.py`.
- Replaced the `time.sleep(0.01)` + immediate `_read_heartbeat` with a
  poll loop that re-reads the row until `current_cell_index == 1` and
  `last_output_excerpt == "done"`, with a 2.0s timeout.
- Verified: 20 consecutive `-n 32` runs of the test, all green.

Cross-reference: `worker test polling` feedback memory — fixed-time
sleeps / immediate assertions on background-thread or cross-connection
state are the recurring root cause.

**Originally discovered during**: HTTP-replay race fix work
(issue #86 / Phases 1-3). Not caused by that change — the heartbeats
module wasn't touched.

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

## Recently Shipped

- **HTTP-replay cassette escape under concurrent LangSmith traffic
  (issue #129)** — shipped 2026-05-25 in
  `src/clm/workers/notebook/notebook_processor.py` as part of
  `_HTTP_REPLAY_BOOTSTRAP_TEMPLATE`. The bootstrap now installs a scoped
  `vcr.patch.reset_patchers` that does not un-patch httpcore inside the
  urllib3 stub's `force_reset()` window. This eliminates the race where
  concurrent foreground httpcore calls (e.g. LLM via httpx) escaped vcr
  when a background thread (e.g. LangSmith trace upload via
  `requests`) was constructing a urllib3 connection. Workaround for an
  upstream vcrpy issue. **TODO: remove the workaround once vcrpy ships
  a scoped `force_reset` upstream** — track `kevin1024/vcrpy`,
  removal checklist in
  `docs/claude/issue-129-vcrpy-force-reset-investigation.md`.

- **Worker Process Leaks on Windows (Kernel Teardown + Pool Sizing)** —
  shipped 2026-04-12 via PR hoelzl/clm#32 (commits `ebf9f1e`, `80228aa`,
  `58a8fb5`, `0c21853`, `d215d6b`). All five proposed fixes landed:
  Windows JobObject in `DirectWorkerExecutor`, `_ReapingKernelManager`
  kernel-descendant reap, orphan job-row reap at `pool_stopped`, env-aware
  pool-size cap, and the new `clm workers reap` subcommand. Archived
  proposal: `docs/proposals/archive/WORKER_CLEANUP_RELIABILITY.md`.
- **Notebook Error Context Tracking** — Phase 2 (TrackingExecutePreprocessor
  for execution-time cell tracking, commit `10daf8f`) and Phase 3 (Docker
  integration test for C++ error context, commit `b1bf24d`) both shipped on
  top of Phase 1 (commit `1ce3630`). Design doc
  `docs/claude/design/notebook-error-context-tracking.md` is at
  PHASE 3 COMPLETE.
- **MCP Tool: `course_authoring_rules`** — shipped as Phase 5A of the MCP
  slide-tooling rollout (see
  `docs/claude/mcp-slide-tooling-handover-archive.md` §Phase 5). Handler
  `handle_course_authoring_rules` is registered in `src/clm/mcp/server.py`
  and implemented in `src/clm/mcp/tools.py`.

---

See `docs/developer-guide/architecture.md` for potential future enhancements.

---

**Last Updated**: 2026-05-25 (Issue #129 vcrpy force_reset workaround shipped; fixed `test_heartbeat_round_trip_smoke` flake — converted fixed-sleep to poll-until-state pattern; moved Worker Cleanup, Notebook Error Context Tracking Phases 2/3, and `course_authoring_rules` to Recently Shipped)
