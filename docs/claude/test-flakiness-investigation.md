# Test Flakiness Investigation — pytest-xdist Contention

**Date**: 2026-06-25
**Scope**: Why the fast suite still flakes under `pytest-xdist`, and how to reduce
it without materially increasing suite duration or abandoning parallelism.
**Method**: 6-agent static survey (waits, shared-resources, xdist-distribution,
timing/fs, flake-fix history, external levers) + an empirical 3× stress run of the
fast suite at the **uncapped** worker count.

---

## TL;DR

There is **one root cause**: the pre-push fast suite (~6500 tests) oversubscribes a
high-core dev box, starving background threads and subprocess startups in ways CI
(2–4 vCPU) never sees. The team has already shipped the high-value structural fixes
(serial → single `xdist_group` under `--dist loadgroup`, per-worker `CLM_LOG_DIR`,
`GIT_*` scrubbing, event-driven recordings/worker waits, the 16-worker cap, the
`integration` marker). **Those must be preserved, not re-litigated.**

The residual manual-rerun burden comes from a handful of gaps that slipped past that
net. They are fixable at **zero or negative duration cost** — every recommended quick
win either removes contention or makes a wait event-driven, and several *speed up* the
happy path. A narrowly-scoped `rerunfailures` safety net (gated to a `flaky` marker on
the known-contention set, never global) catches the long tail without masking real
regressions.

---

## Empirical evidence (ground truth)

Ran the fast suite **3×** at `uv run pytest` with the default `-n auto` **uncapped** =
**64 workers** (the hook normally caps this to 16). This deliberately reproduces the
oversubscription the maintainer describes.

| Run | Result | Wall-clock |
|-----|--------|-----------|
| 1 | 8730 passed, 6 skipped, 1 xfailed | **169.18s** |
| 2 | **1 failed**, 8729 passed | 132.37s |
| 3 | 8730 passed | 116.10s |

Two facts fall straight out:

1. **Oversubscription is ~1.6–2.3× *slower*** (116–169s vs the documented ~72s at
   `-n 16`) **and** flakier. Raising the worker cap is strictly worse on both axes; the
   16-cap is correct.
2. **A real flake reproduced 1-in-3:**
   `tests/infrastructure/workers/test_worker_base.py::test_worker_updates_status`.

### The reproduced flake — transient-state polling

```python
# tests/infrastructure/workers/test_worker_base.py:296-333
worker.process_delay = 1.0                                   # "busy" lasts ~1s
thread.start()
_wait_until(lambda: _read_worker_status(db_path, worker_id) == "busy")   # ← timed out
_wait_until(lambda: _read_worker_status(db_path, worker_id) == "idle")
```

`MockWorker.process_job` (line 80) is just `time.sleep(self.process_delay)`, so the
worker holds the `"busy"` DB status for **only ~1 second**. The `_wait_until` helper and
its 15s ceiling are *fine*. The bug is that the test **polls for a self-expiring
transient state whose lifetime (1s) is shorter than a plausible starvation gap**. Under
64-way oversubscription the test's polling thread (gw7) was descheduled across the whole
~1s `"busy"` window, every poll landed outside it, and the worker was back to
idle/deregistered before the thread ran again → `TimeoutError` at 15s.

The captured log even shows `Job #1 completed in 7202.58s` — a ~2-hour UTC/local
clock-skew artifact that independently corroborates the timing finding below (a
heartbeat test with a `0 <= value < 60` bound is a latent flake of the same family).

**This is the exact anti-pattern the project already documented** for recordings'
self-expiring `ARMED_AFTER_TAKE` state (PR #180): *widen the state's lifetime, not the
poll ceiling.* The static survey's `waits` agent classified `_wait_until` as GOOD (it
is) and missed this specific instance — which is precisely why the empirical run mattered.

**Fix (event-driven, ~0 duration):** gate `process_job` on a `threading.Event` the test
controls, so `"busy"` persists until observed, then release it:

```python
class MockWorker(Worker):
    def __init__(self, ...):
        ...
        self._release = threading.Event()
        self.gate_job = False

    def process_job(self, job):
        if self.gate_job:
            self._release.wait(timeout=15)   # hold "busy" until the test releases
        else:
            time.sleep(self.process_delay)
        self.processed_jobs.append(job.id)
        ...

# test:
worker.gate_job = True
thread.start()
_wait_until(lambda: _read_worker_status(...) == "busy")   # now reliable — busy persists
worker._release.set()                                      # let it finish
_wait_until(lambda: _read_worker_status(...) in ("idle", None))
```

`"busy"` becomes persistent (no transient window to miss) and is released the instant the
test observes it, so the happy path is *faster* than the current fixed 1s sleep.

---

## Root-cause taxonomy

All findings collapse into these families (those marked ✅ are already mitigated; the
listed items are the *residual* gaps):

1. **CPU starvation of background threads under oversubscription.** ✅ Largely fixed via
   event-driven waits + committed-state polling. **Residual:** transient-state polls like
   the flake above; the ~20 watch-mode debounce tests that sync on a bare
   `asyncio.sleep` margin.
2. **Concurrent heavyweight subprocess/port startup.** Partially fixed (lifecycle_mock is
   `serial`; the real-uvicorn and real-kernel long-poles are `integration`). **Residual:**
   `test_http_replay_mitm_manager.py` spawns 17 CPython cold-starts fully parallel (no
   marker); `test_server.py` fake-uvicorn tests still do a real `_free_port()` TOCTOU bind.
3. **Load-perturbed timing assertions.** **Residual:** the heartbeat 50ms slow-write
   relaxation is copy-pasted into two files (a new heartbeat test silently re-acquires the
   flake); one heartbeat-timestamp `< 60s` wall-clock upper bound.
4. **Windows filesystem races amplified by parallelism.** ✅ e2e copytree dodges the
   volatile-spec scandir race. **Residual:** `test_outline.py` writes transient specs into
   the *shared committed tree* (the fragile ignore-glob is the only guard); the e2e
   `os.link` copytree fallback isn't idempotent.
5. **Leaked global singletons.** ✅ Loki-sink teardown, per-worker log dir, HTTP-replay
   env restore. Watch for new globals (the tell is a *blocked background thread*, not CPU
   contention).

---

## Prioritized remediation plan

### Quick wins (low effort, ~zero/negative duration, high payoff) — ship first

0. **Fix the reproduced `test_worker_updates_status` transient-busy poll** (above). Gate
   `process_job` on a `threading.Event`. *Confirmed-current flake — highest priority.*
   Effort S, duration ↓.
1. **Mark `tests/infrastructure/test_http_replay_mitm_manager.py` `serial`.** A module-top
   `pytestmark = pytest.mark.serial` stops 17 simultaneous CPython cold-starts from
   stealing CPU from timing-sensitive neighbors. Same mechanism as the #163 lifecycle_mock
   fix. Effort S, duration ≈ noise. *(Enlarging the serial bucket is the trigger for M-1.)*
2. **Drop the fake-uvicorn `_free_port()` TOCTOU bind in `test_server.py`.** The
   fake-uvicorn lifecycle tests (`TestStartAndStop` / `TestIsRunning` /
   `TestStopWarnsOnStubbornThread`) never serve the port — replace `_free_port()` with a
   constant dummy port; keep the one real-bind test (already `integration+serial`). Removes
   a worker-count-scaling ephemeral-port race. Effort S, duration ↓.
   *Verify first:* `FakeUvicornServer.run()` truly never binds.
3. **Make the watch-mode debounce tests event-driven.** ~20 tests in
   `test_watch_mode.py` / `test_watch_only_sections.py` sync on a fixed ~50ms margin over
   the production debounce. Await the scheduled task (`handler._pending_tasks`) or poll the
   AsyncMock with a generous ceiling. Effort M, duration ↓ (~3s of fixed sleeps removed).
4. **Centralize + env-override the heartbeat `SLOW_WRITE_THRESHOLD`.** Read it from
   `CLM_HEARTBEAT_SLOW_WRITE_THRESHOLD` (default `0.050` in prod), set 30s once in a
   session-autouse fixture. Replaces the two copy-pasted relaxations and future-proofs new
   heartbeat tests. **Never raise the production 50ms default.** Effort S, duration 0.
5. **Fix the heartbeat-timestamp `< 60s` upper bound** to express the real invariant
   (`abs(value) < 3600`, or a monotonic-scaled ceiling). The lower bound is the real guard;
   the UTC/local bug it catches is a ~7200s magnitude error (see the `7202.58s` log above).
   Effort S, duration 0.

### Medium-term

- **M-1: Split the single `serial` bucket into per-resource load groups**
  (`'serial'`→registration/threads, `'subproc'`→interpreter spawns, `'port'`, …). Under
  `--dist loadgroup` distinct group names run on *different* workers concurrently, so the
  critical path *shortens* as the bucket grows. **Must stay `@pytest.hookimpl(tryfirst=True)`**
  (the #163 ordering gotcha) and add a collect-only meta-test that each group is non-empty.
- **M-2: Move `test_outline.py` transient spec writes under `tmp_path`** (or a per-worker
  subdir) instead of the shared committed tree, removing the copytree/scandir race at its
  source rather than relying on the ignore-glob.
- **M-3: Tighten the fast-suite global timeout** (600s → ~120s; the fast suite excludes
  slow/integration/e2e) with per-test `@pytest.mark.timeout` overrides on the few heavy
  subprocess tests. Converts a contention *hang* (10-min stall → manual Ctrl-C) into a
  prompt, retryable failure. *Measure `--durations` on a CI-class box before setting the value.*
- **M-4: Make the e2e `os.link` copytree fallback idempotent** (probe `os.link` once per
  session, or per-file `try os.link except OSError: shutil.copy2`).

### Structural (defer until justified)

- **S-1: Dynamic RAM/CPU-aware worker cap** in `run_pytest_hook.py` —
  `min(16, cpu, free_GB // PER_WORKER_GB)`, keeping 16 as the hard ceiling and logging the
  chosen count. Helps only on an already-loaded box; adds reproduce-time nondeterminism
  (mitigated by logging). `psutil` is already a dep.
- **S-2: filelock counting semaphore** for bounded heavy-test concurrency (run K heavy
  tests across different workers instead of all-on-one). **Defer** — the serial set is still
  tiny; M-1 is simpler and sufficient until its groups grow long tails.
- **S-3: Two-lane hook** (bulk at high `-n`, heavy set at low `-n`). **Defer** — M-1
  achieves most of the isolation within a single run at lower complexity.

---

## Safety-net policy — scoped `pytest-rerunfailures`

Adopt `pytest-rerunfailures`, **scoped to a `flaky` marker — never a global `reruns`**
(global rerun across all 6500 tests is the dangerous mode that masks regressions):

1. Add `pytest-rerunfailures` to the `dev` group; register a `flaky` marker.
2. Tag **only** the known-contention modules (serial lifecycle_mock / http_replay / server,
   heartbeat slow-write) with
   `@pytest.mark.flaky(reruns=2, reruns_delay=1, only_rerun=['OSError','PermissionError','AssertionError','OperationalError','TimeoutError'])`
   so a rerun fires only on the contention signature; a logic bug raising anything else
   fails immediately.
3. Keep reruns **loud** (`-r aR`) and add anti-masking telemetry: alert if any marked test
   reruns above a small rolling-window threshold. A rising rerun rate is the signal to fix
   the root cause, not to add more reruns.
4. **Reject** the `flaky` plugin (collides on `@pytest.mark.flaky`, reruns quietly, lacks
   the `only_rerun` exception scoping that is the core anti-masking control).

Net duration cost ≈ 0 on green (reruns execute only after a marked test fails); a flake
that previously cost a full ~72s manual re-run now costs one extra ~1–5s test execution.
This is a *safety net*, not a substitute for the quick wins.

---

## Do-not-regress (grounded in the flake-fix history)

1. **Don't lower the 16-worker cap** to chase a flake — `8≈94s` vs `16≈73s` is a real ~22%
   penalty, and the cap is a *frequency reducer*, not a fix. **Never** drop below the point
   where lifecycle_mock stays serialized (that serialization, #163, is the actual fix).
2. **Don't switch `--dist loadgroup` → worksteal/loadscope/loadfile.** worksteal doesn't
   honor `xdist_group` (xdist #890) and would scatter the serial group (re-opening #163);
   loadscope/loadfile group by module/file, splitting the cross-module `serial` mark.
3. **Keep the `serial → xdist_group` mapping at `@pytest.hookimpl(tryfirst=True)`** — else
   xdist's nodeid-suffixing runs first and the group silently scatters.
4. **Don't raise the production heartbeat 50ms threshold** — relax in tests only.
5. **Don't widen a poll ceiling for a self-expiring state** (the flake above;
   `ARMED_AFTER_TAKE`) — widen the *state's lifetime* instead.
6. **Poll committed DB state with a generous 15–30s ceiling**, never a tight in-process
   event ceiling, for worker-registration waits (#163).
7. **Use `integration`, not `slow`, to move long-poles off the commit path** (CI excludes
   `slow` everywhere → the test runs *nowhere*).
8. **Don't reintroduce `shutil.move` in the recordings pipeline** — use `safe_move`.
9. **Don't share a global file/singleton across workers without teardown** — isolate
   per-worker. A *blocked background thread* symptom = leaked global, not CPU contention.
10. **Don't remove the `GIT_*` scrubbing** from `run_pytest_hook.py`.
11. **Don't add a global `reruns` or `pytest-randomly` to the fast gate.** Run `randomly`
    out-of-band (non-gating nightly) if at all.

---

## Open questions / how to verify

1. Confirm `FakeUvicornServer.run()` never binds before swapping `_free_port` (quick win 2).
2. Measure `--durations=20` on a CI-class (2–4 vCPU) box before setting the M-3 timeout.
3. Pick `PER_WORKER_GB` (S-1) from observed peak RSS of the heaviest worker test (~0.5–1 GB).
4. After quick win 1 + M-1, benchmark 3–5 runs at `-n 16` to confirm the serial tail didn't
   grow and the ~73s plateau holds.
5. Once the scoped rerun net is live, watch which marked tests actually rerun and at what
   rate — a rising rate means a root-cause fix is still owed.
