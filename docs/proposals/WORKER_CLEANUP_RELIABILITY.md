# Proposal: Worker Cleanup Reliability — Kernel Leaks and Pool Sizing

**Status:** Draft
**Scope:** `clm.workers.notebook` (kernel teardown), `clm.infrastructure.workers`
(pool sizing, leak detection), `pyproject.toml` pytest defaults
**Author:** Reconstructed from a forensic session on 2026-04-11 (Claude Code).

---

## Summary

On the night of 2026-04-10/11, the author's Windows 11 workstation accumulated
**~300 orphaned `python.exe` processes** holding **~12 GB of RAM** and pushing
WindowsTerminal + the WMI `Win32_Process` provider into a wedge state from
which neither tab switching nor keyboard input recovered. The immediate
mitigation was to force-kill the processes and restart the `winmgmt` service.

The forensic work below shows that **CLM's own worker pools reported clean
shutdown** (every `pool_stopped` event recorded 18/18 workers stopped), yet
processes leaked anyway. The leaks come from two sources:

1. **Jupyter-kernel subprocess teardown after `RuntimeError`** in
   `TrackingExecutePreprocessor`. When a cell raises an exception (or the
   kernel dies mid-execution), the kernel child `python.exe` is not
   reliably killed on Windows. The existing test
   `test_cleanup_called_on_kernel_death` exists specifically to guard this
   path — the fact that it exists *and* we still observe orphans strongly
   suggests the fix is incomplete on Windows.
2. **An oversized default pool (18 workers per `clm build`)** combined with
   AI-driven iterative runs that invoke `clm build` many times per session
   (and run `pytest` many times per session with `-n auto`). Even tiny
   per-run leak rates compound rapidly into machine-wrecking accumulations.

This proposal documents the evidence and recommends five prioritized fixes.

---

## Incident

### Observed symptoms

- Windows Terminal stuck: could not switch tabs, could not accept keyboard
  input, UI thread visibly spinning (CPU time on the `WindowsTerminal.exe`
  process grew by ~1 CPU-second per minute while appearing frozen).
- `Get-CimInstance Win32_Process` and `wmic process` both hung
  indefinitely. `tasklist /v` hung. Only `Get-Process` (which uses the fast
  native NT API path, not WMI) remained responsive.
- VS Code's integrated terminal could still spawn new shells — but a fresh
  Windows Terminal process started at 04:22 AM was already at **79 threads
  and 980 handles within 5 minutes**, suggesting it too was heading into the
  same wedge.

### Process scan (via `Get-Process`, counted by start-time bucket)

| Age at scan time (≈04:09 AM) | python.exe count | Provenance |
|---|---:|---|
| < 15 min | 80 | fresh spawns right around the incident |
| 15–60 min | 88 | fresh spawns earlier in the night |
| 2–4 days | 140 | leaked on 2026-04-08 during test-suite work |

Total: **308 `python.exe` processes, ~4.6 GB WS**. This is what the system was
carrying when the wedge began. Subsequent re-scans during mitigation showed
the count climbing to **436** (then to **~12 GB WS** as workers finished
importing the full clm stack), then dropping sharply as we force-killed in
two passes.

The distinctive fingerprint of the leaked processes was a **pair pattern**:

```
PID 80620  (80 MB WS, CPU ≈ 1.4s)  <-- worker / kernel with clm imports loaded
PID 134452 (3.9 MB WS, CPU ≈ 0)    <-- multiprocessing spawn bootstrap helper
```

This is exactly the signature of a Python `multiprocessing.spawn`-style child
on Windows. Every "big" process had a "small" companion started within the
same second. In the youngest batch alone we counted at least 10 such pairs
started at `03:59:28`, plus more at `04:05:06`, `04:08:33`, `04:08:39`, and
`04:12:56` — a bursty pattern consistent with iterative `clm build` runs
rather than a single runaway loop.

---

## Forensic evidence

### 1. The main `clm_jobs.db` reports only 3 pool sessions in the window

Querying the `worker_events` table of `C:\Users\tc\Programming\Python\Projects\clm\clm_jobs.db`:

| Session ID | Pool started | Pool stopped | Workers | Result |
|---|---|---|---|---|
| `session-20260408-190707` | 2026-04-08 17:07:08 UTC | 17:07:59 (51 s) | 3 | clean |
| `session-20260408-214036` | 2026-04-08 19:40:36 UTC | 19:40:52 (16 s) | 3 | clean |
| `session-20260411-030900` | 2026-04-11 01:09:01 UTC | 01:09:16 (15 s) | 3 | clean |

Three sessions, three workers each, all reporting `pool_stopped` with 0 leaks.
**From clm's own bookkeeping, the main repo's pools leaked nothing.**

### 2. But PythonCourses worktrees ran much bigger pools

The `clm build` activity that drove the leak was in PythonCourses worktrees.
Each worktree had its own `clm_jobs.db`:

| Worktree | Last pool_stopped | Jobs completed | Workers per pool | Orphans |
|---|---|---:|---:|---:|
| `crystalline-giggling-rainbow` | 2026-04-10 23:48:05 UTC | 640 | **18** | 0 |
| `sprightly-juggling-jellyfish` | 2026-04-11 00:23:50 UTC | 768 (2 sessions) | **18** | 0 |
| `curious-knitting-pizza` | 2026-04-11 01:21:18 UTC | 768 (2 sessions) | **18** | 0 |
| `cheeky-chasing-kite` | 2026-04-10 02:26:09 UTC | 544 (540 ok, **4 failed**) | **18** | **4** |

Two revelations:

**Pools are 6× larger than in the clm repo itself.** PythonCourses spec files
configure 18 workers per pool (roughly 16 notebook + 1 plantuml + 1 drawio).
Each pool run allocates ~1.5 GB of RAM just for the worker processes
themselves, *before* any Jupyter kernels are spawned. A second back-to-back
`clm build` (as happened in both `sprightly-juggling-jellyfish` and
`curious-knitting-pizza`) doubles that temporarily.

**`cheeky-chasing-kite` has 4 orphaned running jobs** —
`started_at IS NOT NULL AND completed_at IS NULL AND cancelled_at IS NULL`
returns 4. All 4 match the 4 failed jobs and all 4 point at
`slides_010v_custom_api_libraries.py`. The failure messages begin with
`"Notebook execution failed: slides_010v_custom_api_libraries.py\n  Cell: #14..."`.
This is **direct evidence that when a notebook cell raises an exception, the
worker writes the `failed` status but leaves the job row's `completed_at`
unset**, and — more importantly — the associated Jupyter kernel subprocess is
not reliably reaped.

### 3. The pyproject.toml default magnifies the damage

`pyproject.toml:249`:

```toml
addopts = "-v -n auto -m 'not slow and not db_only and not integration and not e2e and not docker'"
```

Every `uv run pytest` invocation from inside a Claude session therefore
spawns `N` xdist workers (one per CPU core) by default. Each xdist worker is
an independent `python.exe` that imports the full `clm` package (~80 MB,
~1–2 s) before it runs any tests. For AI-driven work that calls `pytest`
10+ times per session, this produces large bursts of "one-shot" Python
imports. Normally xdist reaps its children cleanly, but when tests fail
with uncaught exceptions, `SystemExit`, or a uvicorn startup error (one was
observed in `session 53cb6eb8-9a0e-...` at 01:08:29 on 2026-04-11), the
xdist workers can end up as orphans.

### 4. WMI wedged as a *symptom* of handle pressure

At the time of the wedge, WindowsTerminal.exe had **1815 open handles** and
**227 threads**. Normal for a fresh WT process is ~500 handles and ~30
threads. The `winmgmt` service was stuck in a state where the SCM reported
`Running` but every new CIM query returned `HRESULT 0x80041033`
(`WBEM_E_SHUTTING_DOWN`). Restarting the service fixed WMI but did not
unstick the old WindowsTerminal.exe — that process had to be killed
manually. The ordering suggests a chain:

```
300+ orphaned python.exe, each holding ~5–15 handles
  → ~3000+ leaked OS handles
  → WMI provider host backing up on process-table enumeration
  → WindowsTerminal blocking on WMI calls for tab titles / jump list
  → UI thread wedged; tabs unswitchable
```

---

## Root cause analysis

### Cause A — Jupyter kernel teardown on exception

**Location:** `src/clm/workers/notebook/notebook_processor.py` (the module
containing `TrackingExecutePreprocessor`).

**Mechanism:** Each notebook job spawns a Jupyter kernel — its own
`python.exe` — via `nbclient` / `TrackingExecutePreprocessor`. When a cell
raises, `nbclient`'s context-manager exit is supposed to shut the kernel
down. On Windows this is fragile for three reasons:

1. `asyncio.subprocess` on Windows uses `ProactorEventLoop` and its process
   teardown path has historical issues with grandchildren (kernels can spawn
   their own subprocesses — e.g. for `%run` cells, `multiprocessing` in user
   code, etc.).
2. Killing a Jupyter kernel on Windows requires `ProcessTerminate`
   (`os.kill(pid, signal.SIGTERM)` is a no-op on Windows — it is delivered as
   a synthetic WM_CLOSE that a headless kernel ignores).
3. If `preprocess()` raises before `KernelManager.finish_kernel()` is called,
   the cleanup is skipped entirely unless a `try/finally` chain guarantees it.

**Evidence the fix is incomplete:** The existence of
`tests/workers/notebook/test_notebook_processor.py::test_cleanup_called_on_kernel_death`
shows the team has been aware of the risk. The 4 orphaned jobs in
`cheeky-chasing-kite` show that the current guard is not sufficient under
real-world cell execution failures (cell #14 or cell #21 raising inside
`slides_010v_custom_api_libraries.py` — typical "import failed" or "API key
missing" failures).

### Cause B — oversized default pool + iterative usage

**Mechanism:** 18 workers × 80 MB import footprint = ~1.5 GB of steady-state
Python. A single iteration of "run clm build, tweak spec, run clm build again"
briefly *doubles* this (the new pool starts while the old pool is still in
its cleanup phase). With a handful of Claude tabs each doing a few iterations
per hour, steady-state memory pressure on the machine is already several GB
before any leaks are counted.

**Why pool size is the magnifier, not the leak:** 18 workers per run is fine
on CI (spin up, run, tear down, the host disappears). On a long-lived dev
workstation it creates a huge blast radius for even a 1% cleanup failure
rate, because every surviving orphan is permanent until the machine reboots.

### Cause C (secondary) — silent job-row orphans hide the leak

Even when the DB records `pool_stopped` cleanly, individual job rows can
remain in the `started, not completed, not cancelled` state (the 4 in
`cheeky-chasing-kite`). This state is effectively a silent leak indicator,
but nothing currently surfaces it. A developer running `clm build` and
seeing `build completed` has no reason to suspect 4 kernel processes are
still alive on their machine.

---

## Proposed fixes

Listed roughly in priority order (highest leverage first).

### Fix 1 — Harden `TrackingExecutePreprocessor` kernel teardown on Windows

**What:** Audit the cleanup path in
`src/clm/workers/notebook/notebook_processor.py` to guarantee the Jupyter
kernel subprocess is killed in all exit scenarios — normal completion,
`RuntimeError` from `preprocess()`, asyncio cancellation, and
`KeyboardInterrupt`.

**Concretely:**

- Capture the kernel `pid` as soon as it is known.
- Wrap the entire `preprocess()` call in a `try/finally` that always calls
  a new `_force_kill_kernel()` method.
- In `_force_kill_kernel()`:
  - First, call the normal `KernelManager.finish_kernel()` / `shutdown_kernel()`.
  - Then `psutil.Process(pid).children(recursive=True)` and kill any
    survivors with `terminate()` → `wait(timeout=2)` → `kill()`.
  - Finally, call `psutil.Process(pid).terminate()` and `kill()` on the
    kernel itself, ignoring `NoSuchProcess`.
- Log a warning at `WARNING` level whenever the second or third stage
  actually had to terminate anything (means the normal path failed — useful
  diagnostic).

**Tests to add:**

- Extend `test_cleanup_called_on_kernel_death` to actually verify that
  **no child python.exe remains** after the preprocessor exits (check via
  `psutil` snapshot before/after). The current test presumably only verifies
  that a cleanup hook was *called*, not that it was *effective*.
- New test: simulate a kernel that ignores `shutdown_kernel()` (spawn a
  subprocess that traps SIGTERM) and verify the force-kill path still works.
- New test: a cell that itself spawns a child via `subprocess.Popen` and
  then raises — verify the grandchild is also reaped.

**Why this is priority 1:** It fixes the actual leak at its source. Every
other fix just limits the blast radius.

### Fix 2 — Make pool size CPU-aware and memory-aware by default

**What:** Change the default worker counts so a fresh `clm build` on a dev
laptop doesn't allocate 1.5 GB of Python imports before processing anything.

**Concretely:**

- Add a `max_pool_size` calculation to `PoolManager` (or the place that
  materializes the pool counts from the spec):
  ```python
  def compute_effective_pool_size(requested: int) -> int:
      cpu_cap = max(1, (os.cpu_count() or 2) // 2)
      mem_gb = psutil.virtual_memory().total / (1024**3)
      mem_cap = max(1, int(mem_gb // 2))  # 2 GB budget per worker
      return min(requested, cpu_cap, mem_cap)
  ```
- Log the cap when it clamps:
  `"Spec requested 18 workers; capping to 6 (cpu_cap=8, mem_cap=6)."`
- Allow override via `CLM_POOL_MAX` or a CLI flag `--max-workers`.

**Non-goal:** Do not change the *requested* counts in existing spec files.
Those reflect a CI/bulk-build preference that is correct in that context.
The clamp only kicks in when the environment can't sustain the request.

**Why this matters:** Even with Fix 1 perfect, 18 parallel kernel
subprocesses on a 4-core laptop is a bad default. This is the difference
between "I can keep working while `clm build` runs" and "my machine is
unusable for 3 minutes."

### Fix 3 — Surface silent job-row orphans as a build-time warning

**What:** At the end of every `clm build`, before `pool_stopped` is
committed, scan `jobs` for rows matching
`started_at IS NOT NULL AND completed_at IS NULL AND cancelled_at IS NULL`
from the current session. If any are found:

- Log a warning with their IDs and input files.
- Optionally, attempt to find their `worker_id` in the `workers` table and
  force-kill via `psutil`.
- Print a visible message to the user:
  `"⚠ 4 notebook jobs started but never completed. Possible kernel leak. Run 'clm workers reap' to clean up."`

**Also add:** a schema constraint or periodic cleanup task that marks
long-stale (`> 10 minutes` old, still `started`, no heartbeat) rows as
`failed` with a synthetic error.

**Why this matters:** This turns an invisible failure mode into a visible
one. The 4 orphans in `cheeky-chasing-kite` existed for **26 hours** before
I found them. A warning at end-of-build would have flagged them
immediately.

### Fix 4 — Add `clm workers reap` subcommand

**What:** A CLI subcommand that:

1. Enumerates every `clm_jobs.db` under a configured set of roots
   (cwd, `$CLM_WORKTREE_ROOTS`, a default hunt list including
   `~/.claude/worktrees` and the main project dir).
2. For each DB, reads the `workers` table for any `workers` with a
   `last_heartbeat` older than N seconds, and any orphaned job rows.
3. Cross-references their recorded PIDs with current `psutil.pids()`.
4. Offers to kill any survivors (confirm first, `--force` to skip).
5. Updates the DB rows to `cancelled` with a reason.

**Supporting change:** The `workers` table in `clm_jobs.db` is empty after
a session ends (we verified). Either keep workers registered until a
pool_stopped event and write heartbeats, or keep a separate
`historical_workers` table that survives after pool_stopped so reap can find
old workers.

**Why this matters:** Self-service recovery. A user who sees their machine
slow down can run `clm workers reap` and be back to normal in seconds,
without needing to know about `taskkill` or `Get-Process`.

### Fix 5 — Ship a saner pytest profile for AI-driven runs

**What:** Split the pytest configuration so AI sessions can opt into a
"lean" profile without losing the CI profile.

**Concretely, in `pyproject.toml`:**

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
# ... existing config ...
addopts = "-v -n auto -m 'not slow and not db_only and not integration and not e2e and not docker'"

# New: a lean profile for iterative / AI-driven runs
# Enable with:  uv run pytest -c clm-pytest-lean.toml
# (or set PYTEST_ADDOPTS="-n 0 ..." in the environment)
```

**Also add** an environment variable convention: if
`CLM_AI_DEV_MODE=1` is set, `conftest.py` can detect it and apply
`-n 0` overrides. Document this in `CLAUDE.md`:

> AI agents running iterative `pytest` in this repo should set
> `CLM_AI_DEV_MODE=1` or pass `-n 0` explicitly. `-n auto` is appropriate
> for CI and final pre-commit runs, but iterative runs with `-n auto`
> compound any uncaught cleanup failures into hundreds of orphaned
> Python processes on Windows.

**Why this matters:** It documents the trap, even if we can't fully
prevent it. If we don't tell future Claude sessions about this, they will
hit it again.

---

## Priority and effort estimation

| Fix | Blocking impact | Engineering effort | Testability |
|---|---|---|---|
| 1 — Harden kernel teardown | **Highest** (root cause) | Medium | High (psutil-based test) |
| 2 — Pool size cap | High | Low | Medium (env-dependent) |
| 3 — Orphan warning at build time | Medium | Low | High (unit test) |
| 4 — `clm workers reap` | Medium (recovery) | Medium–High | Medium |
| 5 — Pytest profile | Low (documentation) | Trivial | N/A |

Fix 1 alone addresses the root cause. Fixes 2 and 3 are force multipliers
that turn a "catastrophic" leak into "a nuisance warning." Fixes 4 and 5
are quality-of-life improvements.

---

## Open questions / things I couldn't verify

1. **Does the 3:33–3:39 AM spawn burst correspond to a worktree I didn't
   find?** The worktree DBs I found only cover pool sessions up to 01:21 UTC
   (03:21 local). Something spawned ~80 workers between 03:33 and 03:39
   local. It might have been a fifth worktree whose DB was never committed
   (WMI wedge interrupted the write), or a non-worktree `clm build`, or a
   pytest run. **Action:** when reviewing this proposal, search
   `~/.claude/projects/C--Users-tc-Programming-Python-Courses-Own-PythonCourses--claude-worktrees-*.jsonl`
   for `clm build` or `pytest` tool invocations with a timestamp in the
   03:30–03:40 UTC window.

2. **Is `TrackingExecutePreprocessor` the only kernel-spawning path?**
   `nbconvert` and `papermill` both use similar patterns. If any other
   module spawns kernels directly, Fix 1 needs to cover those too.

3. **Do plantuml and drawio workers leak similarly?** The observed pair
   pattern was consistent with `multiprocessing.spawn` children of the
   notebook worker. I have no evidence for or against leaks from plantuml
   / drawio workers. Worth a quick audit before considering Fix 1 complete.

4. **Is the `workers` table really meant to be empty after pool_stopped?**
   The query on the main `clm_jobs.db` returned 0 rows for `workers` even
   though `worker_events` contained the full history. If the design is
   "clear on shutdown," Fix 4 needs the `historical_workers` table
   mentioned above. If the design is "keep heartbeats," then the clearing
   is itself a bug.

---

## Appendix: reproduction and verification

### Reproduction (to confirm Fix 1 works)

```python
# tests/workers/notebook/test_windows_kernel_teardown.py
import pytest
import psutil

@pytest.mark.windows_only
def test_kernel_is_reaped_after_cell_exception(tmp_path):
    # Build a notebook whose cell #1 raises
    nb_path = tmp_path / "boom.ipynb"
    nb_path.write_text(make_notebook([('code', 'raise RuntimeError("boom")')]))

    before = {p.pid for p in psutil.process_iter(['name']) if p.info['name'] == 'python.exe'}

    processor = NotebookProcessor(SpeakerOutput(format="html"))
    with pytest.raises(RuntimeError, match="boom"):
        processor.process(nb_path, ...)

    # Give Windows a moment to reap
    import time; time.sleep(0.5)

    after = {p.pid for p in psutil.process_iter(['name']) if p.info['name'] == 'python.exe'}
    leaked = after - before
    assert not leaked, f"Kernel processes leaked: {leaked}"
```

### Verification (to confirm Fix 3 surfaces orphans)

Run `clm build` against a spec that contains a deliberately broken notebook
(one whose cell raises). Expect:

- The build reports the failed job.
- **AND** the build prints a warning naming the orphaned job row and its
  `input_file`.
- The exit code is non-zero (or at least the build log prominently flags
  the issue).

### Recovery reference (what worked during the incident)

1. `Get-Process python | Where-Object { $_.StartTime -lt (Get-Date).AddDays(-1) } | Stop-Process -Force` — killed 140 leaked workers from 2+ days earlier.
2. `Get-Process python | Stop-Process -Force` — killed all remaining python.exe (296 processes, ~10 GB RAM freed).
3. `Restart-Service winmgmt -Force` (elevated) — restored WMI responsiveness.
4. `Stop-Process -Id <stuck-WindowsTerminal-pid> -Force` — dismissed the wedged WT process; new tabs responsive again.

`Get-Process` (native NT API) remained functional throughout. `Get-CimInstance`,
`wmic`, and `tasklist /v` (all WMI-backed) were unusable until step 3.

---

## Related files

- `src/clm/workers/notebook/notebook_processor.py` — contains
  `TrackingExecutePreprocessor`; primary target of Fix 1.
- `tests/workers/notebook/test_notebook_processor.py` — contains
  `test_cleanup_called_on_kernel_death`; extend per Fix 1's test plan.
- `src/clm/infrastructure/workers/pool_manager.py` — target of Fix 2.
- `src/clm/infrastructure/database/job_queue.py` — target of Fix 3's
  orphan-detection query.
- `src/clm/cli/commands/` — home for the new `workers reap` subcommand
  (Fix 4).
- `pyproject.toml:249` — current `addopts` line that Fix 5 amends or
  documents around.
