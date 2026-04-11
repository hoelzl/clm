# Worker Cleanup Reliability — Implementation Plan

**Companion to:** `docs/proposals/WORKER_CLEANUP_RELIABILITY.md`
**Status:** Fix 1 landed in commit `ebf9f1e` (2026-04-11). Fix 2 landed
in commit `80228aa` (2026-04-11) — psutil-based kernel-descendant reap
via a `_ReapingKernelManager` subclass; see "Fix 2 design correction"
below. Fix 3 landed in commit `58a8fb5` (2026-04-11) — orphan-row reap
at `pool_stopped` via `JobQueue.mark_orphaned_jobs_failed`. Fix 4
landed 2026-04-11 — env-aware pool-size cap via `compute_pool_size_cap`
inside `get_worker_config`. Fix 5 next (`clm workers reap` upgrade).
**Author:** Claude Code, 2026-04-11.

This is a handover document for the worker-cleanup reliability work. The
original proposal captures the forensic incident and a first-draft fix plan.
This file captures **what the actual code currently looks like** (so a fresh
session does not have to re-discover it), the **revised fix plan** after the
audit, and a **status checklist** for resuming work mid-stream.

---

## 30-second problem statement

On Windows, `clm build` leaks Jupyter kernel subprocesses any time a worker
is killed mid-job. The observed symptom is hundreds of orphaned `python.exe`
processes, each holding ~80 MB, eventually wedging WMI and Windows Terminal.
The original forensic writeup is in
`docs/proposals/WORKER_CLEANUP_RELIABILITY.md`.

---

## What the original proposal missed

The proposal names `TrackingExecutePreprocessor` teardown as the primary
cause. After reading the code, that is **a real secondary leak, not the
primary one**. The primary leak is simpler and more fundamental.

### Actual root cause: Windows pool teardown orphans every in-flight kernel

Key code references:

- `src/clm/infrastructure/workers/worker_executor.py:570-612`
  — `DirectWorkerExecutor.stop_worker` on Windows calls
  `process.terminate()`, which is `TerminateProcess` — non-trappable, no
  finally blocks, no signal handlers.
- `src/clm/infrastructure/workers/worker_base.py:245`
  — the worker's `SIGTERM` handler never fires on Windows because
  `TerminateProcess` bypasses Python entirely.
- `src/clm/infrastructure/workers/worker_executor.py:535-541`
  — `CREATE_NEW_PROCESS_GROUP` is used for Popen, but that flag only
  controls CTRL+C routing. It is not a JobObject and does not create a
  process tree.
- `src/clm/cli/commands/build.py:958-968` — every `KeyboardInterrupt` and
  every build exception routes through a `finally:` that calls
  `stop_managed_workers` — so any interrupted build leaks in-flight
  kernels, by design.

**Jupyter kernels are grandchildren of CLM.** The call chain is CLM → worker
subprocess → Jupyter kernel subprocess. Upstream audit (jupyter_client
8.8.0): `LocalProvisioner.launch_kernel` just calls `subprocess.Popen`,
`LocalProvisioner.kill` falls through to `self.process.kill()` which is
`TerminateProcess` on Windows. Neither jupyter_client nor CLM uses
JobObjects. Windows does not automatically clean up process trees. Therefore
killing the worker orphans the kernel.

This matches the observed fingerprint: the 4 orphan rows in
`cheeky-chasing-kite` all have `started_at IS NOT NULL AND completed_at IS
NULL` — they were mid-job when the worker was killed, and their kernels are
the orphaned `python.exe` processes in the forensics.

---

## What is already defended

To avoid repeating work already done, here is what the code already has
that works:

### Notebook processor cleanup (partially defended)

`src/clm/workers/notebook/notebook_processor.py:691-805`:

- `_cleanup_kernel_resources` runs in `try/finally` after every `preprocess()`
  (line 791-793).
- A fresh `TrackingExecutePreprocessor` is created per retry (line 764), so
  failed cleanups do not compound.
- `km.shutdown_kernel(now=True)` and `km.cleanup_resources()` are called.

**What is still broken here:** `shutdown_kernel(now=True)` ultimately calls
`TerminateProcess` on Windows, which kills only the kernel pid — not any
grandchildren the kernel itself spawned (e.g., a cell that uses
`multiprocessing` or `subprocess`). This is a secondary leak that Fix 2
addresses.

### Existing cleanup test is deceptive

`tests/workers/notebook/test_notebook_processor.py:1368`
`test_cleanup_called_on_kernel_death` uses a `MagicMock` with `km=None,
kc=None`, so `_cleanup_kernel_resources` returns early without doing
anything. The test only asserts **that the finally block runs** — it does
not assert **that kernels actually die**. It has been giving false
confidence. Rewrite is in Fix 2.

### psutil is already available

`src/clm/infrastructure/workers/worker_executor.py:665` already does
conditional `import psutil` for process lookup. It is not a hard dependency
but is used optionally. Fix 2 promotes it to a hard dependency.

### `clm workers cleanup` exists but does nothing useful

`src/clm/cli/commands/workers.py:128-215` defines `clm workers cleanup`, but
it only deletes database rows. It does not kill OS processes, walk process
trees, or cross-reference worktrees. Fix 5 upgrades this.

### Default worker count is 1, not 18

`src/clm/infrastructure/config.py:318` and the shipped default `.toml` at
line 1078 both set `default_worker_count = 1`. The 18-worker configuration
seen in the incident is a **PythonCourses project-level override** in its
own spec files. This changes Fix 4's emphasis: the cap should protect
against oversized project overrides, not fix clm's own default.

---

## Revised fix plan (priority order)

### Fix 1 — Windows JobObject in `DirectWorkerExecutor`  [DONE — 2026-04-11, `ebf9f1e`]

**Goal:** Make it impossible for Windows `TerminateProcess` on a worker to
orphan the worker's descendants. Create a Windows JobObject with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` per `DirectWorkerExecutor`, assign
every worker subprocess to it immediately after Popen, close the handle in
`cleanup()` (and `__del__` as a safety net). Windows then guarantees that
closing the job handle terminates every process in the job — including
kernels that inherited into the job — even if CLM crashes.

**Files:**
- New: `src/clm/infrastructure/workers/windows_job_object.py`
  — ctypes wrapper exposing `WorkerJobObject` (no-op on non-Windows).
- Modified: `src/clm/infrastructure/workers/worker_executor.py`
  — `DirectWorkerExecutor.__init__` creates the job; `start_worker` calls
  `assign` after Popen; `cleanup` calls `close` at the end.
- New: `tests/infrastructure/workers/test_windows_job_object.py`
  — integration test that spawns a grandchild chain, closes the job,
  asserts via psutil that both die.

**Why this is priority 1:** It fixes the decisive Windows leak and survives
atexit-path failures (`_atexit_cleanup_all_pools`), `taskkill /F`, and
crashes — Windows itself enforces the cleanup.

### Fix 2 — kernel-descendant reap via `_ReapingKernelManager`  [DONE — 2026-04-11]

**Goal:** Even with Fix 1, in-process cleanup should reliably kill kernel
descendants while the worker is still alive (normal happy path, between
notebook jobs). Without this, a long-running worker accumulates orphan
`python.exe` processes from every notebook cell that spawned a
subprocess, because `jupyter_client`'s `LocalProvisioner.kill` is
`TerminateProcess` on Windows — it kills the kernel pid and nothing else.

#### Fix 2 design correction

The original plan put the reap in `_cleanup_kernel_resources` at
`notebook_processor.py:691`, "after capturing `km.provisioner.pid` before
calling `shutdown_kernel`". **That was impossible to implement as
written.** By the time `_cleanup_kernel_resources` runs, nbclient's
`setup_kernel` context manager (inside `ExecutePreprocessor.preprocess`)
has already called `shutdown_kernel` and set `ep.km = None` and
`ep.kc = None` in its finally block — so there is nothing left to
snapshot. An empirical check confirmed this:

```
After preprocess: ep.km = None, ep.kc = None
```

The correct intercept point is **inside** `shutdown_kernel` itself, not
after. So Fix 2 landed as a custom `_ReapingKernelManager(AsyncKernelManager)`
whose `shutdown_kernel` override snapshots descendants first, runs the
normal shutdown, then reaps survivors in a `finally` block. The subclass
is wired into `TrackingExecutePreprocessor` via the
`kernel_manager_class` traitlet, so nbclient's `create_kernel_manager`
uses it automatically for every kernel.

The `_cleanup_kernel_resources` method was kept intact as a
defence-in-depth safety net for the very narrow window where `setup_kernel`
itself crashes before running its finally (e.g., a failure during
`start_new_kernel_client`). Its docstring was updated to reflect this.

#### Actual changes

- **psutil promoted to a hard dependency** in `pyproject.toml` (`psutil>=5.9.0`)
  — already present transitively as 7.2.2. The conditional
  `import psutil` + `/proc` fallback in `worker_executor.is_worker_running`
  at `worker_executor.py:680-714` was replaced with a plain top-level
  import. The unused `glob` import was removed.
- **`reap_kernel_descendants`** helper (module-level in
  `notebook_processor.py`) handles the terminate → wait → kill sequence
  and logs WARNING when anything actually had to be killed. That warning
  is the diagnostic signal the team has been missing.
- **`_ReapingKernelManager(AsyncKernelManager)`** override of
  `shutdown_kernel` snapshots descendants before calling super, then
  invokes `reap_kernel_descendants` on the snapshot.
- **`TrackingExecutePreprocessor.kernel_manager_class = _ReapingKernelManager`**
  wires the subclass into every `preprocess` call.
- **Two new real-kernel tests** in
  `tests/workers/notebook/test_notebook_processor.py::TestKernelCleanup`
  replace the old mock-based `test_cleanup_called_on_kernel_death`:
  - `test_reaping_kernel_manager_kills_grandchild_on_success` — spawns a
    subprocess grandchild from a cell, runs `preprocess` to completion,
    asserts the grandchild is dead via `psutil.pid_exists`.
  - `test_reaping_kernel_manager_kills_grandchild_on_cell_error` — same
    shape, but with a second cell that raises so preprocess propagates
    `CellExecutionError`. Confirms the reap still runs on the error path
    (it lives in nbclient's finally block).

Both pass end-to-end on Windows with a real kernel. Without the
`_ReapingKernelManager` hook, a sleeping `python.exe` grandchild survives
for its full 120-second sleep.

### Fix 3 — Orphan-row warning at pool_stopped  [DONE — 2026-04-11]

**Goal:** Surface silent job-row orphans so `clm status` is honest about
incomplete jobs.

Before Fix 3, a worker dying mid-job (Windows pool teardown, OOM-kill,
user Ctrl-C) would leave its `jobs` row stuck in `status='processing'`
with `started_at` set but `completed_at` null — forever. `clm status`
would quietly under-report failures and the next build would
potentially miss them entirely.

#### What Fix 3 actually does

The logic lives in two places:

1. **`JobQueue.mark_orphaned_jobs_failed`** (new method in
   `src/clm/infrastructure/database/job_queue.py`). A single atomic
   `BEGIN IMMEDIATE` transaction that selects every row matching

   ```sql
   WHERE started_at IS NOT NULL
     AND completed_at IS NULL
     AND cancelled_at IS NULL
     AND status IN ('processing', 'pending')
   ```

   and updates them to
   `status='failed', error='worker died mid-job (orphaned at pool shutdown)',
   completed_at=CURRENT_TIMESTAMP`. Returns a list of
   `{id, input_file, status, worker_id}` dicts for the caller to log.
   The canonical error string is exposed as
   `JobQueue.ORPHAN_ERROR_MESSAGE` so tests and downstream tooling can
   recognise it without regexing a free-form sentence.

2. **`WorkerLifecycleManager.stop_managed_workers`** (modified in
   `src/clm/infrastructure/workers/lifecycle_manager.py`). Between
   `pool_manager.stop_pools()` and `log_pool_stopped()`, it calls
   `self.event_logger.job_queue.mark_orphaned_jobs_failed()`. If any
   orphans come back, it emits a `logger.warning` naming each orphan
   (id + input file) so operators see it on stderr, and passes
   `orphan_count` / `orphan_job_ids` as kwargs to `log_pool_stopped`.
   The scan is wrapped in `try/except Exception` so a DB hiccup can
   never break pool teardown — downstream cleanup (log + clear
   `managed_workers`) always runs.

3. **`WorkerEventLogger.log_pool_stopped`** (extended in
   `src/clm/infrastructure/workers/event_logger.py`). Accepts the new
   `orphan_count=0` and `orphan_job_ids=None` kwargs and stamps them
   into the `worker_events.metadata` JSON so dashboards can
   distinguish clean from dirty shutdowns. The human-readable message
   suffix `"; N orphan job(s) marked failed"` is only added when
   `orphan_count > 0`, keeping clean-shutdown log lines unchanged.

#### Tests

Direct `JobQueue` tests in
`tests/infrastructure/database/test_job_queue.py`:

- `test_mark_orphaned_jobs_failed_returns_empty_when_no_orphans` —
  clean shutdown happy path.
- `test_mark_orphaned_jobs_failed_reaps_processing_job` — mid-flight
  job is marked failed with the canonical error and a `completed_at`
  timestamp; returned dict matches the row.
- `test_mark_orphaned_jobs_failed_ignores_completed_jobs` — completed
  rows must never be touched.
- `test_mark_orphaned_jobs_failed_ignores_cancelled_jobs` — even if
  `started_at` is forced non-null, `cancelled_at` guards the row.
- `test_mark_orphaned_jobs_failed_reaps_multiple_orphans` — all
  in-flight jobs are reaped in a single atomic pass.
- `test_mark_orphaned_jobs_failed_ignores_pending_without_started_at`
  — a truly untouched pending job is not an orphan.

End-to-end `WorkerLifecycleManager` tests in
`tests/infrastructure/workers/test_lifecycle_manager.py::TestStopManagedWorkers`:

- `test_stop_does_not_warn_when_no_orphans` — clean shutdown emits no
  orphan warning (checks `caplog`).
- `test_stop_warns_and_marks_failed_when_orphan_rows_exist` — seeded
  in-flight row ends up failed in the DB and a WARNING naming the
  input file is logged.
- `test_stop_passes_orphan_metadata_to_log_pool_stopped` — orphan
  count and IDs reach `log_pool_stopped` kwargs (verified by spying
  on the real `event_logger`).
- `test_stop_survives_orphan_scan_failure` — if
  `mark_orphaned_jobs_failed` raises, `stop_pools`,
  `log_pool_stopped`, and `managed_workers` clearing still happen and
  a scan-failure warning is emitted.

### Fix 4 — Env-aware pool-size cap  [DONE — 2026-04-11]

**Goal:** Protect against oversized project-level worker counts (like
PythonCourses' 18 workers) on dev laptops, so that a spec file tuned
for a build farm does not saturate the machine an operator happens to
run the build on.

#### Fix 4 design correction

The original plan pointed at `PoolManager._build_configs` — that
method does not exist. The actual place where `WorkerConfig.count` is
finalised is `WorkersManagementConfig.get_worker_config` in
`src/clm/infrastructure/config.py` (around line 410, where
`type_config.count` merges with `default_worker_count`). Per the Fix 2
/ Fix 3 design lesson, I verified this empirically before coding:
every caller of worker counts (lifecycle_manager's
`should_start_workers`, `_adjust_configs_for_reuse`,
`_collect_reused_worker_info`, and the pool-start path) goes through
`get_worker_config`, so clamping at that single intercept point is
both sufficient and minimally invasive.

#### What Fix 4 actually does

1. **New helper module**
   `src/clm/infrastructure/workers/pool_size_cap.py`:
   - `_compute_cpu_cap()` → `max(1, (os.cpu_count() or 2) // 2)`
   - `_compute_mem_cap()` → `max(1, floor(total_gb / 2))`, with an
     explicit fallback to `1` if `psutil.virtual_memory()` raises
   - `_read_env_cap()` → reads `CLM_MAX_WORKERS` tolerantly (empty /
     non-integer / ≤ 0 all treated as "unset" and logged if invalid)
   - `compute_pool_size_cap(requested, *, explicit_cap=None)` →
     `PoolSizeCapResult(effective, requested, cpu_cap, mem_cap,
     explicit_cap, was_clamped)`. Returns the clamped count plus every
     individual cap so the caller can format a precise warning.
   - `PoolSizeCapResult.format_reason()` renders the canonical log
     line `"Spec requested 18 workers; capping to 6 (cpu_cap=8,
     mem_cap=6, explicit_cap=None)"`.
   - The helper is *pure*: no logging inside, so unit tests do not
     need `caplog` scaffolding. The caller
     (`get_worker_config`) logs at `WARNING` when
     `result.was_clamped`.

2. **New config field**
   `WorkersManagementConfig.max_workers_cap: int | None = None`
   (`ge=1, le=64`). Default `None` means "auto-detect CPU/RAM caps
   only".

3. **`get_worker_config` clamping** — calls
   `compute_pool_size_cap(requested, explicit_cap=self.max_workers_cap)`
   and logs at `WARNING` when the spec was oversized. Existing
   callers (`should_start_workers`,
   `_adjust_configs_for_reuse`, `_collect_reused_worker_info`, the
   pool-start path) all pick up the clamped count automatically.

4. **`config_loader.load_worker_config` plumbing** — accepts either
   `max_workers` (CLI-style) or `max_workers_cap` (config-style) in
   `cli_overrides`. A value of `0` or negative is treated as
   "clear the cap", matching the `CLM_MAX_WORKERS` env-var semantics
   so operators have a single way to express "no cap" via either
   channel.

5. **`clm build --max-workers N` CLI flag** — new click option near
   `--notebook-workers` in `build.py` (new field on `BuildConfig`,
   threaded through the `build` and `main_build` signatures, fed
   into `cli_overrides["max_workers"]`). Help text explains it is
   also settable via `CLM_MAX_WORKERS`.

6. **`clm info commands` updated** — new row in the build options
   table documents `--max-workers`. Required by
   `CLAUDE.md`'s "Info Topics Maintenance Rule".

#### Tests

`tests/infrastructure/workers/test_pool_size_cap.py` (new file, 19
unit tests for the helper):

- Happy path (no clamping, cpu_cap wins, mem_cap wins,
  explicit_cap wins, requested wins when smallest, effective ≥ 1
  even with zero caps).
- `CLM_MAX_WORKERS` env var (read when no explicit cap; explicit
  cap beats env; zero / negative / garbage / empty / explicit zero
  all ignored correctly).
- Machine-cap fallbacks (`os.cpu_count() is None`, one-core VM,
  16 GB RAM → 8 workers, 512 MB VM → 1 worker, `psutil.virtual_memory`
  raising → safe default of 1 with WARNING).
- `format_reason` carries all four cap values for operator
  diagnostics.

`tests/infrastructure/workers/test_config_loader.py::TestMaxWorkersCapOverride`
(6 new tests): CLI override, config-style alias, CLI takes precedence
over alias, zero clears, negative clears, absent leaves existing cap
alone. Plus one new logging assertion in `TestLogging`.

`tests/infrastructure/test_config.py::TestWorkerManagementConfig` (5
new tests): `get_worker_config` clamps to explicit cap; cpu cap;
mem cap; passes through unclamped small requests; emits a WARNING
naming worker type, requested count, and all cap values when
clamping.

All new tests use `monkeypatch` to pin the machine caps, so the
suite is deterministic across CI VMs, dev laptops, and beefy build
machines alike.

### Fix 5 — `clm workers reap` that actually kills processes  [PENDING]

**Goal:** Self-service recovery. Upgrade `clm workers cleanup` (or add new
`reap`) at `src/clm/cli/commands/workers.py` to:

1. Scan for stale workers and orphan job rows.
2. Use `psutil.process_iter(['cmdline', 'environ'])` to find surviving
   `python -m clm.workers.*` processes from dead pool sessions.
3. For each survivor, walk its process tree and kill the whole subtree.
4. Optionally cross-walk `CLM_WORKTREE_ROOTS` for multi-worktree recovery.

The `workers` table is wiped on `pool_stopped`
(`pool_manager.py:1025-1030`), so this cannot rely on DB state alone — it
must also do a psutil-based scan.

---

## Implementation status checklist

- [x] Fix 1: `windows_job_object.py` created
- [x] Fix 1: `worker_executor.py` updated (`__init__`, `start_worker`, `cleanup`)
- [x] Fix 1: integration test added (`test_windows_job_object.py`)
- [x] Fix 1: tests pass on Windows (6 pass, including `test_closing_job_kills_grandchildren`)
- [x] Fix 1: ruff + mypy clean
- [x] Fix 1: 340 existing worker + notebook tests still pass (in isolation)
- [x] Fix 1: committed as `ebf9f1e` with `--no-verify` (pre-commit hook is
      currently broken for worktrees; manual `ruff check`, `ruff format
      --check`, `mypy`, and the fast worker/notebook suites were all run
      by hand before commit)
- [x] Fix 2: `_ReapingKernelManager` subclass snapshots + reaps kernel
      descendants from inside `shutdown_kernel` (the original
      "`_cleanup_kernel_resources` augmentation" plan was impossible — see
      "Fix 2 design correction" above)
- [x] Fix 2: psutil promoted to hard dep in pyproject.toml; conditional
      import + `/proc` fallback in `worker_executor.is_worker_running` replaced
      with a plain top-level `import psutil`
- [x] Fix 2: old mock-based `test_cleanup_called_on_kernel_death` replaced
      with two real-kernel tests
      (`test_reaping_kernel_manager_kills_grandchild_on_success` and
      `_on_cell_error`) that spawn a subprocess grandchild from a cell and
      assert it is dead via `psutil.pid_exists` after preprocess returns
- [x] Fix 2: 341 fast worker + notebook tests pass; 6 Fix 1 regression
      tests still green; ruff + mypy clean
- [x] Fix 3: `JobQueue.mark_orphaned_jobs_failed` implemented with
      atomic `BEGIN IMMEDIATE` SELECT + UPDATE, returns the reaped rows
- [x] Fix 3: `lifecycle_manager.stop_managed_workers` runs the orphan
      scan between `stop_pools()` and `log_pool_stopped()`, logs a
      visible WARNING naming each orphan, and passes `orphan_count` /
      `orphan_job_ids` into the `pool_stopped` event metadata
- [x] Fix 3: `WorkerEventLogger.log_pool_stopped` extended with
      `orphan_count` / `orphan_job_ids` kwargs (backward-compatible)
- [x] Fix 3: 6 new direct `JobQueue` tests + 4 new end-to-end
      `TestStopManagedWorkers` tests; all pass; existing tests still green
- [x] Fix 3: 374 fast worker + notebook + job_queue tests pass; ruff +
      mypy clean
- [x] Fix 4: new `pool_size_cap.py` helper with `compute_pool_size_cap`
      applying `min(requested, cpu_cap, mem_cap, explicit_cap)` with
      tolerant `CLM_MAX_WORKERS` env-var reading and psutil fallbacks;
      19 unit tests cover every branch
- [x] Fix 4: `max_workers_cap` field added to `WorkersManagementConfig`,
      clamping wired into `get_worker_config` with a `WARNING` log line
      formatted from `PoolSizeCapResult.format_reason`
- [x] Fix 4: `config_loader.load_worker_config` accepts `max_workers` and
      `max_workers_cap` CLI overrides; 0 or negative clears the cap
- [x] Fix 4: `--max-workers` CLI flag added to `clm build` near
      `--notebook-workers`, threaded through `BuildConfig`, `main_build`,
      and `build`; documented in `clm info commands`
- [x] Fix 4: 30 new tests (19 helper unit + 6 config_loader plumbing +
      5 `get_worker_config` integration); 437 fast tests pass; ruff +
      mypy clean; Fix 1/2/3 regression guard tests still green
- [ ] Fix 5: `clm workers reap` scans processes and kills trees

---

## Open questions to verify during integration

1. **Does the Jupyter kernel launcher ever set `CREATE_BREAKAWAY_FROM_JOB`?**
   If yes, Fix 1 is silently defeated. Verify by running a notebook job
   inside a pool that has Fix 1, then inspecting the job's process list
   via `QueryInformationJobObject` — the kernel pid should appear in the
   job. Faster verification: the Fix 1 grandchild test should fail in that
   case.

2. **Can `_atexit_cleanup_all_pools` be simplified on Windows after Fix 1?**
   The JobObject makes it redundant on Windows. The atexit handler is
   complex and has historically hidden errors. Consider making it a no-op
   on Windows once Fix 1 is proven.

3. **Does `CREATE_NEW_PROCESS_GROUP` interact with JobObject assignment?**
   They are orthogonal (one affects CTRL+C routing, the other affects
   lifecycle), but sanity-check with the integration test.

4. **What about Docker mode?** Docker workers are containers, which Docker
   itself tracks and reaps. Fix 1 targets `DirectWorkerExecutor` only.
   Docker mode is handled separately by `DockerWorkerExecutor.stop_worker`
   which does `container.stop()` + `container.remove()`. No change needed.

5. **Parallel-test flake seen during Fix 1 pre-commit.**
   `tests/infrastructure/workers/test_worker_base.py::test_worker_updates_status`
   failed once under `pytest -n auto` and passed in isolation. This is the
   same class of xdist parallelism issue as the pre-existing uvicorn
   port-8765 binding warnings. It is not caused by Fix 1. If it recurs
   during Fix 2 or later work, treat it as pre-existing flake rather than
   a regression, and rerun the specific test in isolation to confirm.

---

## Files touched during this work

(Updated as implementation proceeds.)

- `docs/proposals/WORKER_CLEANUP_IMPLEMENTATION_PLAN.md` — this file.
- **Fix 1 (2026-04-11, commit `ebf9f1e`):**
  - New: `src/clm/infrastructure/workers/windows_job_object.py`
    (full ctypes wrapper for CreateJobObjectW /
    SetInformationJobObject / OpenProcess / AssignProcessToJobObject /
    CloseHandle, plus a cross-platform `WorkerJobObject` facade).
  - Modified: `src/clm/infrastructure/workers/worker_executor.py`
    - Added import of `WorkerJobObject`.
    - `DirectWorkerExecutor.__init__`: instantiates `self._job_object`.
    - `DirectWorkerExecutor.start_worker`: calls `self._job_object.assign(process)`
      immediately after Popen returns.
    - `DirectWorkerExecutor.cleanup`: calls `self._job_object.close()` at the
      end, after stopping individual workers.
  - New: `tests/infrastructure/workers/test_windows_job_object.py`
    (6 tests; the critical one is `test_closing_job_kills_grandchildren`
    which reproduces the kernel-leak shape with a three-level process chain
    and asserts the whole tree dies when the job closes).
- **Fix 2 (2026-04-11):**
  - Modified: `pyproject.toml`
    - Added `psutil>=5.9.0` to `[project] dependencies` (was pulled in
      transitively; now explicit).
  - Modified: `src/clm/workers/notebook/notebook_processor.py`
    - Added top-level `import psutil` and
      `from jupyter_client.manager import AsyncKernelManager`.
    - New module-level `reap_kernel_descendants(kernel_pid, descendants,
      log_prefix)` helper implementing the terminate → wait → kill
      sequence with WARNING-level diagnostic logging.
    - New `_ReapingKernelManager(AsyncKernelManager)` class whose
      `shutdown_kernel` override snapshots descendants before calling
      super, then invokes `reap_kernel_descendants` on the snapshot inside
      a `finally` block.
    - `TrackingExecutePreprocessor.kernel_manager_class =
      _ReapingKernelManager` so every kernel nbclient creates goes through
      the reaping subclass.
    - `_cleanup_kernel_resources` kept intact as a defence-in-depth
      safety net for the narrow window where nbclient's `setup_kernel`
      finally does not run; docstring clarified.
  - Modified: `src/clm/infrastructure/workers/worker_executor.py`
    - Promoted conditional `import psutil` (with `/proc` fallback) to a
      plain top-level import; `is_worker_running` now just iterates
      `psutil.process_iter`. Removed the unused `glob` import.
  - Modified: `tests/workers/notebook/test_notebook_processor.py`
    - Added top-level `import time` and `import psutil`.
    - Imported `TrackingExecutePreprocessor`.
    - Replaced `TestKernelCleanup.test_cleanup_called_on_kernel_death`
      (which used `MagicMock(km=None, kc=None)` and gave false confidence)
      with two real-kernel regression tests:
      - `test_reaping_kernel_manager_kills_grandchild_on_success`
      - `test_reaping_kernel_manager_kills_grandchild_on_cell_error`
      Both spawn a `subprocess.Popen` grandchild from a cell, run
      `ep.preprocess` on a real kernel, and assert the grandchild is
      dead via `psutil.pid_exists` after preprocess returns.
- **Fix 3 (2026-04-11):**
  - Modified: `src/clm/infrastructure/database/job_queue.py`
    - New `JobQueue.ORPHAN_ERROR_MESSAGE` class constant with the
      canonical orphan error string.
    - New `JobQueue.mark_orphaned_jobs_failed()` method that runs an
      atomic `BEGIN IMMEDIATE` SELECT + UPDATE pass to reap in-flight
      rows (started_at set, completed_at null, cancelled_at null,
      status in ('processing', 'pending')) and returns a list of
      `{id, input_file, status, worker_id}` dicts for logging.
  - Modified: `src/clm/infrastructure/workers/event_logger.py`
    - `WorkerEventLogger.log_pool_stopped` now accepts
      `orphan_count=0` and `orphan_job_ids=None` kwargs and stamps
      them into the `worker_events.metadata` JSON. Human-readable
      message suffix only appears when `orphan_count > 0`, keeping
      clean-shutdown log lines unchanged.
  - Modified: `src/clm/infrastructure/workers/lifecycle_manager.py`
    - `stop_managed_workers` runs
      `self.event_logger.job_queue.mark_orphaned_jobs_failed()`
      between `pool_manager.stop_pools()` and
      `log_pool_stopped()`, wrapped in `try/except Exception` so a
      DB hiccup can never break pool teardown. On non-empty result
      it emits a visible WARNING naming each orphan (id + input
      file) and passes `orphan_count` / `orphan_job_ids` into the
      `pool_stopped` event metadata.
  - Modified: `tests/infrastructure/database/test_job_queue.py`
    - Six new direct unit tests for `mark_orphaned_jobs_failed`
      covering: no orphans; single processing orphan; ignoring
      completed rows; ignoring cancelled rows (even with forced
      `started_at`); multiple orphans in one pass; untouched pending
      jobs.
  - Modified: `tests/infrastructure/workers/test_lifecycle_manager.py`
    - Four new end-to-end tests in `TestStopManagedWorkers`:
      `test_stop_does_not_warn_when_no_orphans`,
      `test_stop_warns_and_marks_failed_when_orphan_rows_exist`,
      `test_stop_passes_orphan_metadata_to_log_pool_stopped`, and
      `test_stop_survives_orphan_scan_failure`. These use a real
      `WorkerEventLogger` (backed by the temp-DB fixture) and only
      mock `pool_manager`, so the orphan reap is exercised against
      real SQLite state.
- **Fix 4 (2026-04-11):**
  - New: `src/clm/infrastructure/workers/pool_size_cap.py`
    - Pure helper module. Exposes `PoolSizeCapResult` (frozen
      dataclass) and `compute_pool_size_cap(requested, *,
      explicit_cap=None)`. Internal helpers
      `_compute_cpu_cap`, `_compute_mem_cap`, and `_read_env_cap`
      isolate the environment probes so tests can pin them deterministically.
      `_compute_mem_cap` wraps `psutil.virtual_memory()` in a
      `try/except` and falls back to `1` with a WARNING on failure.
  - Modified: `src/clm/infrastructure/config.py`
    - New `max_workers_cap: int | None` field on
      `WorkersManagementConfig` with `ge=1, le=64` and a docstring
      referencing the helper module.
    - `get_worker_config` now imports `compute_pool_size_cap`,
      clamps the requested count, and logs a WARNING when
      `result.was_clamped`.
  - Modified: `src/clm/infrastructure/workers/config_loader.py`
    - `load_worker_config` accepts `max_workers` (CLI-style) or
      `max_workers_cap` (config-style) and writes to
      `config.max_workers_cap`. A value of 0 or negative clears the
      cap (matches `CLM_MAX_WORKERS` env-var semantics). Logs INFO
      on both set and clear.
  - Modified: `src/clm/cli/commands/build.py`
    - New `max_workers: int | None` field on `BuildConfig`.
    - New `--max-workers` click option after `--drawio-workers`.
    - Threaded `max_workers` through the `build` command, the
      `main_build` async worker, the `BuildConfig(...)` constructor
      call, and the `configure_workers` CLI-override block.
  - Modified: `src/clm/cli/info_topics/commands.md`
    - New row documents `--max-workers` with the same rationale as
      the CLI help text. Required by CLAUDE.md's "Info Topics
      Maintenance Rule" because this is a new CLI flag.
  - New: `tests/infrastructure/workers/test_pool_size_cap.py`
    - 19 unit tests covering: no clamping; each individual cap
      winning; requested-wins; effective ≥ 1 floor; env-var reading
      (positive, zero, negative, garbage, empty, explicit zero
      fallthrough); cpu/mem fallbacks; `format_reason` content.
  - Modified: `tests/infrastructure/workers/test_config_loader.py`
    - New `TestMaxWorkersCapOverride` class with 6 tests for the
      CLI → config plumbing and a new `test_logs_max_workers_cap_override`
      assertion in `TestLogging`. `mock_base_config` fixture now
      initialises `max_workers_cap = None`.
  - Modified: `tests/infrastructure/test_config.py`
    - 5 new tests in `TestWorkerManagementConfig`:
      `test_get_worker_config_clamps_to_explicit_cap`,
      `test_get_worker_config_clamps_to_cpu_cap`,
      `test_get_worker_config_clamps_to_mem_cap`,
      `test_get_worker_config_pass_through_when_under_caps`, and
      `test_get_worker_config_logs_warning_when_clamped`. All pin
      `_compute_cpu_cap` / `_compute_mem_cap` via `monkeypatch` so
      the suite is deterministic regardless of host hardware.

---

## How to resume from here

1. Read `docs/proposals/WORKER_CLEANUP_RELIABILITY.md` for incident forensics.
2. Read this file for code reality check + revised plan.
3. Check the "Implementation status checklist" above to see what is done.
4. `git show ebf9f1e` to see exactly what Fix 1 looked like when it
   landed; `git log --oneline` to check whether Fix 2 or later has been
   committed since.

### Fix 5 (next) — `clm workers reap` that actually kills processes

- `src/clm/cli/commands/workers.py` — upgrade the existing
  `clm workers cleanup` subcommand (currently only deletes DB rows)
  or add a new `reap` subcommand that:
  1. Scans for stale workers and orphan job rows via
     `JobQueue.mark_orphaned_jobs_failed` (introduced in Fix 3).
  2. Uses `psutil.process_iter(['cmdline', 'environ'])` to find
     surviving `python -m clm.workers.*` processes from dead pool
     sessions.
  3. For each survivor, walks its process tree and kills the whole
     subtree via the same helper as Fix 2's
     `reap_kernel_descendants`.
  4. Optionally cross-walks `CLM_WORKTREE_ROOTS` for multi-worktree
     recovery.
- The workers table is wiped on `pool_stopped`
  (`pool_manager.py` around line 1025-1030), so this cannot rely on
  DB state alone — it must also do a psutil-based process scan.

### Fix 2 / Fix 3 design lesson (for Fix 4+)

Before writing code at a planned intercept point, verify the actual
runtime state at that point. Fix 2 caught the premise error just in
time: the original plan assumed `_cleanup_kernel_resources` runs while
the kernel is still alive, but nbclient's `setup_kernel` finalizer
calls `shutdown_kernel` and clears `km`/`kc` *before* `preprocess`
returns. The right intercept was inside `shutdown_kernel` itself via a
custom `AsyncKernelManager` subclass wired in through the
`kernel_manager_class` traitlet. Fix 3 got the same treatment before
coding — confirmed that `stop_managed_workers` runs synchronously
after `stop_pools()` has fully torn down workers, so the orphan SELECT
does not race a worker's own commit. For any similar "reap X before Y"
or "observe X after Y" design, run a two-line empirical check of the Y
lifecycle before writing code.

### Useful commands

```bash
# Fix 1 tests (regression guard — keep green while working on Fix 2+)
uv run pytest tests/infrastructure/workers/test_windows_job_object.py -v

# Fast worker + notebook suite (~25s on Windows; matches the pre-commit
# hook scope)
uv run pytest tests/infrastructure/workers/ tests/workers/notebook/test_notebook_processor.py -q

# Lint + type-check the changed files (the hook does these automatically
# on Linux, but is broken for worktrees on Windows — run manually there)
uv run ruff check src/clm/workers/notebook/notebook_processor.py tests/workers/notebook/test_notebook_processor.py
uv run mypy src/clm/workers/notebook/notebook_processor.py
```

### Commit workflow reminder

Pre-commit hooks are currently broken for Windows worktrees. Run the lint +
type-check + fast-test commands above by hand, then commit with
`--no-verify`. The user has explicitly authorized this workaround for the
duration of the worker-cleanup work.
