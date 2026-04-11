# Worker Cleanup Reliability — Implementation Plan

**Companion to:** `docs/proposals/WORKER_CLEANUP_RELIABILITY.md`
**Status:** Fix 1 landed in commit `ebf9f1e` (2026-04-11). Fix 2 next
(psutil fallback in `_cleanup_kernel_resources`).
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

### Fix 2 — psutil fallback in `_cleanup_kernel_resources`  [PENDING]

**Goal:** Even with Fix 1, in-process cleanup should reliably kill kernel
descendants while the worker is still alive (normal happy path). Augment
`_cleanup_kernel_resources` at `notebook_processor.py:691`:

1. Capture `km.provisioner.pid` before calling `shutdown_kernel`.
2. Run the existing graceful shutdown.
3. Use `psutil.Process(pid).children(recursive=True)` + `terminate()` +
   `psutil.wait_procs(timeout=2)` + `kill()` on survivors.
4. Log at WARNING when psutil actually had to kill anything — that warning
   is the diagnostic signal that the team has been missing.

**Also:** promote psutil to a hard dependency in `pyproject.toml:30-45`
and delete the conditional imports + fallback paths.

**Test rewrite:** Replace `test_cleanup_called_on_kernel_death` with a
real-kernel test that executes a cell spawning a grandchild via
`subprocess.Popen`, raises from the next cell, and asserts via psutil that
both the kernel and the grandchild are dead after `_cleanup_kernel_resources`.

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
- [ ] Fix 2: `_cleanup_kernel_resources` augmented with psutil fallback
- [ ] Fix 2: psutil promoted to hard dep in pyproject.toml
- [ ] Fix 2: existing `test_cleanup_called_on_kernel_death` replaced with
      real-kernel test
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

---

## How to resume from here

1. Read `docs/proposals/WORKER_CLEANUP_RELIABILITY.md` for incident forensics.
2. Read this file for code reality check + revised plan.
3. Check the "Implementation status checklist" above to see what is done.
4. `git show ebf9f1e` to see exactly what Fix 1 looked like when it
   landed; `git log --oneline` to check whether Fix 2 or later has been
   committed since.

### Fix 2 (next) — the target files

- `src/clm/workers/notebook/notebook_processor.py` — augment
  `_cleanup_kernel_resources` at line 691. Capture `km.provisioner.pid`
  before calling `km.shutdown_kernel(now=True)`, then use
  `psutil.Process(pid).children(recursive=True)` + `terminate` +
  `wait_procs(timeout=2)` + `kill` to reap anything that survived. Log
  WARNING if psutil actually had to kill anything.
- `pyproject.toml` — promote `psutil` from optional to a hard dependency
  in the `dependencies = [...]` list (lines ~30-45). Delete the
  conditional `import psutil` dance at `worker_executor.py:665` once
  promoted; it can become a plain top-level import.
- `tests/workers/notebook/test_notebook_processor.py` — replace
  `test_cleanup_called_on_kernel_death` at line 1368. The current test
  passes a `MagicMock` with `km=None, kc=None` so `_cleanup_kernel_resources`
  returns early. Replace with a real-kernel test that starts a kernel,
  executes a cell spawning a `subprocess.Popen` grandchild, raises from
  the next cell, and asserts via psutil that both the kernel and the
  grandchild are dead after cleanup returns.

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
