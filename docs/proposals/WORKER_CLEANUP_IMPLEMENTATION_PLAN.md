# Worker Cleanup Reliability — Implementation Plan

**Companion to:** `docs/proposals/WORKER_CLEANUP_RELIABILITY.md`
**Status:** Fix 1 landed in commit `ebf9f1e` (2026-04-11). Fix 2 landed
in commit `80228aa` (2026-04-11) — psutil-based kernel-descendant reap
via a `_ReapingKernelManager` subclass; see "Fix 2 design correction"
below. Fix 3 next (orphan-row warning at `pool_stopped`).
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

### Fix 3 — Orphan-row warning at pool_stopped  [PENDING]

**Goal:** Surface silent job-row orphans so `clm status` is honest about
incomplete jobs.

In `lifecycle_manager.stop_managed_workers` (around `lifecycle_manager.py:237`),
run:

```sql
SELECT id, input_file FROM jobs
WHERE started_at IS NOT NULL
  AND completed_at IS NULL
  AND cancelled_at IS NULL
  AND status IN ('processing', 'pending')
```

If rows exist:
- Mark them `failed` with a synthetic error (`worker died mid-job`) and
  `completed_at = CURRENT_TIMESTAMP`.
- Print a visible warning with counts and input files.
- Include the orphan count in the `pool_stopped` event metadata so audits
  can distinguish clean from dirty shutdowns.

### Fix 4 — Env-aware pool-size cap  [PENDING]

**Goal:** Protect against oversized project-level worker counts (like
PythonCourses' 18 workers) on dev laptops.

In `PoolManager._build_configs` (or wherever `WorkerConfig.count` is
finalized):

```python
cpu_cap = max(1, (os.cpu_count() or 2) // 2)
mem_cap = max(1, int(psutil.virtual_memory().total / (1024**3) // 2))
env_cap = int(os.environ.get("CLM_MAX_WORKERS") or 0) or None
effective = min(requested, cpu_cap, mem_cap, *([env_cap] if env_cap else []))
```

Log when clamping kicks in: `"Spec requested 18 workers; capping to 6
(cpu_cap=8, mem_cap=6)."`. Add `--max-workers` CLI flag on the `build`
command near `--notebook-workers` at `build.py:1071`.

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
- [ ] Fix 3: orphan-row detection + warning in `lifecycle_manager.stop_managed_workers`
- [ ] Fix 3: failed-row update for orphans
- [ ] Fix 4: env-aware cap in `PoolManager`
- [ ] Fix 4: `--max-workers` CLI flag
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

---

## How to resume from here

1. Read `docs/proposals/WORKER_CLEANUP_RELIABILITY.md` for incident forensics.
2. Read this file for code reality check + revised plan.
3. Check the "Implementation status checklist" above to see what is done.
4. `git show ebf9f1e` to see exactly what Fix 1 looked like when it
   landed; `git log --oneline` to check whether Fix 2 or later has been
   committed since.

### Fix 3 (next) — orphan-row warning at `pool_stopped`

- `src/clm/infrastructure/workers/lifecycle_manager.py` — in
  `stop_managed_workers` (around line 237), query for rows with
  `started_at IS NOT NULL AND completed_at IS NULL AND cancelled_at IS
  NULL AND status IN ('processing', 'pending')`. If any exist, mark them
  failed with a synthetic `worker died mid-job` error, print a visible
  warning with counts and input files, and add an orphan count to the
  `pool_stopped` event metadata so dirty shutdowns are auditable.

### Fix 2 design lesson (for Fix 3+)

Before implementing Fix 3, verify the actual state of the code at the
intercept point. For Fix 2, the plan assumed
`_cleanup_kernel_resources` runs while the kernel is still alive — it
doesn't, because nbclient's `setup_kernel` finalizer calls
`shutdown_kernel` and clears `km`/`kc` *before* `preprocess` returns.
The right intercept was inside `shutdown_kernel` itself via a custom
`AsyncKernelManager` subclass wired in through the
`kernel_manager_class` traitlet. For any similar "reap X before Y"
design, run a quick empirical check of the Y lifecycle before
writing code.

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
