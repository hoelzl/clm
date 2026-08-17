# Test-suite flakiness: root causes and remediation

**Status**: implemented — see §7 for what shipped and the one deliberate
deviation from the original plan (step 2)
**Date**: 2026-08-09
**Scope**: the nightly full-suite failures of 2026-08-08 (issue #821) and
2026-07-31 (issue #752), plus the standing flake landscape they sit in.

---

## 1. Summary

Two independent defects explain every nightly failure investigated.

**Defect A — pytest live logging destroys Click `CliRunner` output capture.**
When live logging is on, the *first* log record emitted inside a
`CliRunner.invoke()` permanently rebinds `sys.stdout` / `sys.stderr` away from
the runner's isolation for the rest of that invocation. Everything the command
writes after that point vanishes from `result.output` and reappears in pytest's
own "Captured stdout/stderr call" section. Every assertion on `result.output`
downstream of a log record is therefore a coin flip. This accounts for **all
four `test_cache_explain` failures on 2026-07-31 and both `test_release_cli`
failures on 2026-08-08.**

**Defect B — a real-worker e2e test oversubscribes the CI runner.**
`test_e2e_managed_workers_reuse_across_builds` starts ten worker processes with
a hardcoded 120-second completion budget, is not in the `serial("workerpool")`
group, and runs concurrently with three other xdist workers on a 4-core runner.
It lost that race on 2026-07-31 (`JobsPendingTimeoutError`).

Defect A is the important one. It is **not** rare — it is a large, deterministic
bug wearing a probabilistic mask. A third factor (§4) decides, per run, how many
of the ~90 `CliRunner` test modules are exposed: measured locally, between **0
and 34 tests** fail from the same single cause depending only on scheduling
luck. The nightly has been showing us the thin tail of that distribution.

---

## 2. Evidence

| Run | Date | Failures | Cause |
|---|---|---|---|
| [31237944432](https://github.com/hoelzl/clm/actions/runs/31237944432) | 2026-08-08 | `test_release_cli.py::test_add_rejects_unknown_topic`, `::test_sync_promotes_green_topics_and_refuses_failed_ones` | Defect A |
| [30604138776](https://github.com/hoelzl/clm/actions/runs/30604138776) | 2026-07-31 | 4 × `test_cache_explain.py` | Defect A |
| " | " | `test_e2e_lifecycle.py::test_e2e_managed_workers_reuse_across_builds` | Defect B |
| [31293303092](https://github.com/hoelzl/clm/actions/runs/31293303092) | 2026-08-09 | none | same code, different scheduling |

The 2026-08-08 failure text is the whole story in one line:

```
assert 'NOT promoted' in "Note: the source build manifest is partial …\n
                          Channel 'jan': skeleton copy (1 files)\n
                          copy        intro (1 files)\n
                          skip-failed flaky (0 files)\n"
--- Captured stdout call ---
Copied 2 file(s): 1 newly frozen, 0 re-frozen, 0 already frozen (skipped).
--- Captured stderr call ---
WARNING:clm.release.sync:release sync: topic 'flaky' failed in the source build …
Warning: 1 released topic(s) NOT promoted — they failed in the source build: flaky. …
```

Map those lines onto `src/clm/cli/commands/release.py`:

| Line | Emitted at | Landed in |
|---|---|---|
| "Note: … partial" | `release.py:1021` | `result.output` ✅ |
| "Channel 'jan': skeleton copy" | `release.py:1033` | `result.output` ✅ |
| "copy intro" / "skip-failed flaky" | `release.py:1035` | `result.output` ✅ |
| *(`apply_sync()` runs — `sync.py:388` logs a WARNING)* | `release.py:1054` | — |
| "Copied 2 file(s)…" | `release.py:1069` | pytest's stdout ❌ |
| "Warning: … NOT promoted" | `release.py:1082` | pytest's stderr ❌ |

There is a clean temporal boundary at the log record. Nothing about the two
`click.echo` call sites differs; only *when* they run relative to
`logger.warning`.

The same shape produced the 2026-07-31 failures, where the escaping payload was
an entire JSON document:

```
E   ValueError: substring not found      # result.output.index("{")
--- Captured stdout call ---
{ "source_file": …, "components": { … } }
```

---

## 3. Defect A — root cause

### 3.1 Mechanism

Three pieces interlock.

**1. `CliRunner` isolates at the Python level only.** `CliRunner.isolation()`
(`click/testing.py`) replaces `sys.stdout` / `sys.stderr` with
`_NamedTextIOWrapper` objects over a `StreamMixer`; `result.output` is that
mixer's interleaved buffer. Click 8.4's default capture mode is `"sys"` — it
does not touch file descriptors 1 and 2 (and `capture="fd"` is unavailable on
Windows). So the isolation holds only as long as nobody else reassigns
`sys.stdout` / `sys.stderr`.

**2. pytest's live-log handler reassigns exactly those.**
`_LiveLoggingStreamHandler.emit()` (`_pytest/logging.py:925-932`) wraps every
record in `CaptureManager.global_and_fixture_disabled()` so the line reaches the
real terminal rather than the capture buffer. That context manager suspends
global capture and then **resumes** it — and `resume` does
`setattr(sys, "stdout", self.tmpfile)`, i.e. it installs *pytest's* capture
object. It has no idea Click put something else there, and Click's `isolation()`
only restores `sys.stdout` in its `finally`, long after the damage.

**3. The handler is attached for the entire run loop.**
`LoggingPlugin.pytest_runtestloop` (`_pytest/logging.py:790-801`) enters
`catching_logs(self.log_cli_handler, …)` around *all* tests, not per test.

Direct proof, with an instrumented command:

```
PROBE before: sys.stdout=_NamedTextIOWrapper  id=2050466181440
   log.warning("the log record")
PROBE after:  sys.stdout=EncodedFile          id=2050461795264

assert 'AFTER-stdout' in 'BEFORE-stdout\nBEFORE-stderr\n'   # FAILED
```

`_NamedTextIOWrapper` is Click's; `EncodedFile` is pytest's. Everything written
after the record is lost to `result.output` — for stdout *and* stderr, and for
`ClickException.show()`, which is why `test_add_rejects_unknown_topic` sees
`result.output == ''`.

### 3.2 Why live logging is on at all

`tests/conftest.py:679-683`:

```python
if os.environ.get("CLM_ENABLE_TEST_LOGGING"):
    config.option.log_cli = True
    config.option.log_cli_level = os.environ.get("CLM_LOG_LEVEL", "INFO")
```

That env var is set by:

- `nightly.yml` — for the **whole** suite (`-m "not docker"`), plus an explicit
  `--log-cli-level=INFO`;
- `ci.yml` — for the **integration**, **e2e**, **slow** and **docker** tiers only.

The PR **unit** tier (`ci.yml:183-186`) sets neither the env var nor
`--log-cli-level`. That is the whole reason this is a nightly-only phenomenon:
the ~90 `CliRunner` test modules live in the unit tier, where live logging is
off and the defect cannot fire. Nothing on a PR can catch it.

Note that removing the conftest branch alone is insufficient —
`_log_cli_enabled()` (`_pytest/logging.py:759-772`) also returns true for a bare
`--log-cli-level=INFO`, which both workflows pass on the command line.

### 3.3 The live logging in question is invisible anyway

Under xdist the worker's terminal reporter output is swallowed. Counting live-log
section headers in the two nightly logs:

| Job | Parallelism | `live log` sections |
|---|---|---|
| Full suite, no docker | `-n auto` | **0** |
| Docker tier | `-n0` | 35 |

So in the job where the defect fires, live logging produces no output at all. It
is pure cost: it buys nothing and breaks `CliRunner`.

---

## 4. Why it is *flaky* rather than *always red*

This is the part worth internalising, because it also predicts what happens when
the obvious fixes land.

`clm.cli.commands.shared.setup_logging()` — `src/clm/cli/commands/shared.py:47-51`:

```python
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    handler.close()
    root_logger.removeHandler(handler)
```

It clears **and closes** every handler on the root logger, including handlers it
does not own. It is called from `src/clm/cli/commands/build.py:715`, i.e. from
any in-process `clm build`.

Because pytest attaches its live-log handler once for the whole run loop (§3.1,
point 3), the first test on an xdist worker that reaches `setup_logging()`
**permanently removes pytest's live-log handler from that worker process** — and
with it, the capture-suspension. Every later test on that worker is immune.

Measured directly (root-logger handlers after each test, `-n0`):

```
tests/cli/test_build_command.py::TestFindEnvFile::test_finds_env_in_same_directory
    ['LogCaptureHandler', 'StreamHandler', '_FileHandler', '_LiveLoggingStreamHandler']
tests/cli/test_build_command.py::TestInitializePathsAndCourse::test_spec_parsing_error_json_mode_exits
    ['LogCaptureHandler', 'ResilientRotatingFileHandler']
```

The consequence, confirmed by bisection:

| Invocation (with `CLM_ENABLE_TEST_LOGGING=1`) | Failures |
|---|---|
| `pytest tests/cli/test_outline.py -n0` | 7 |
| `pytest tests/cli/test_build_command.py tests/cli/test_outline.py -n0` | **0** |
| `pytest tests/cli/test_assign_ids_refusals.py tests/cli/test_outline.py -n0` | 7 |
| `pytest tests/release -n0` | 6 |
| `pytest tests/cli tests/release tests/voiceover tests/recordings -n0` | **0** |
| fast suite, `-n auto` (28 workers) | 25, then 34 |
| fast suite, `-n4` (CI's shape) | 5 |

`test_build_command.py` immunises everything that follows it. Which tests follow
which is decided at run time: `addopts` uses `-n auto --dist loadgroup`, whose
scheduler hands ungrouped tests to whichever worker is free. Fewer workers ⇒ each
worker is immunised earlier ⇒ fewer exposed tests, which is why CI (4 cores)
shows 2–5 and a 28-worker local run shows 25–34.

**Sequencing consequence.** `setup_logging()`'s handler-nuking is itself a bug —
it is hostile to embedders, it breaks `caplog` for the remainder of the test that
triggers it, and closing another component's handler is never correct. But fixing
it *first* would remove the accidental immunity and take the nightly from 2
failures to ~35. Fix the capture interaction before fixing the handler nuking.

### 4.1 The band-aids already in the tree

This has been worked around without being named:

- `tests/cli/test_cache_explain.py:58-59` —
  `json.loads(result.output[result.output.index("{"):])`, commented *"Locate the
  JSON object in the output (logging lines may precede it)"*. That comment is a
  direct observation of Defect A. The hack does not survive the failure it was
  written for: when the whole payload escapes, `index("{")` raises
  `ValueError: substring not found` — which is precisely how 2026-07-31 failed.
- `tests/cli/test_assign_ids_refusals.py:60` — the same brace-slicing.

Both should be deleted once the cause is gone.

---

## 5. Defect B — e2e worker-pool contention

`tests/e2e/test_e2e_lifecycle.py::test_e2e_managed_workers_reuse_across_builds`
failed on 2026-07-31 with:

```
JobsPendingTimeoutError: Jobs did not complete within 120 seconds. 1 job(s) still pending.
```

Four contributing factors, all fixable:

1. **It starts ten real processes.** `notebook_count=8` plus one plantuml and one
   drawio worker (`test_e2e_lifecycle.py:236-246`), commented *"for faster
   parallel processing"*. The test asserts worker *reuse* — pool size is
   irrelevant to what it proves.
2. **The CPU/RAM clamp that would have saved it is disabled in tests.** The
   autouse `_neutralise_pool_size_cap` fixture pins `_compute_cpu_cap` /
   `_compute_mem_cap` to 128, deliberately (a spec-driven pool size should not be
   silently shrunk mid-test). So on a 4-core runner the pool really is 10.
3. **It is not serialised.** It carries `e2e` + `slow` but not
   `@pytest.mark.serial("workerpool")`. The `workerpool` group exists and is used
   — but only by the *mock* pool tests
   (`test_lifecycle_mock.py:44`, `test_mock_worker_claiming_parity.py:36`). The
   real-worker tests, which are far heavier, are outside it. So up to three other
   xdist workers run their own load alongside.
4. **The budget ignores the CI knob.** `max_wait_for_completion_duration=120` is
   hardcoded at `test_e2e_lifecycle.py:190, 276, 301`, while
   `test_e2e_course_conversion.py:497` reads `CLM_E2E_TIMEOUT`. Both workflows set
   `CLM_E2E_TIMEOUT` (600 in CI, 900 nightly) — and the lifecycle tests silently
   ignore it. The one lever CI has to widen this budget is not connected.

---

## 6. The rest of the flake landscape

For context, what is already handled and should not be re-litigated:

- **`serial` marker → xdist load groups** (`tests/xdist_group_helpers.py`) —
  resource-class-scoped serialisation (`serial`, `serial("subproc")`,
  `serial("port")`, `serial("workerpool")`). Sound design; §5.3 is a coverage gap
  in *applying* it, not a flaw in it.
- **Scoped `flaky` retries** — three modules use
  `@pytest.mark.flaky(only_rerun=[…])`, with `-rR` in `addopts` making every retry
  visible and a documented ban on global `--reruns`
  (`docs/developer-guide/testing.md:221-236`). Correct policy; keep it.
- **Worker-global state restoration** — `_restore_worker_global_state`
  (`tests/conftest.py:903`) snapshots the `clm` logger chain and the config
  singleton per test (issue #694). It restores *levels*, `disabled` and
  `propagate` — it does **not** restore root-logger *handlers*, which is the hole
  §4 falls through. Extending it is one of the candidate fixes.
- **Ambient-env hermeticity** — `FORCE_COLOR`/`CLICOLOR_FORCE` popped at conftest
  import (commit b5c0c1f1), `CLM_*_DB_PATH` and `CLM_SYNC_ENGINE` isolated.
  Defect A is the same *class* of problem — ambient configuration leaking into
  CLI output assertions — one layer down.

Also noted, harmless but dead: `configure_test_logging` sets
`request.config.option.log_cli = True` at fixture time (`tests/conftest.py:856`),
which `LoggingPlugin` has already read at configure time. It has no effect.

---

## 7. Remediation

Ordered. Steps 1–2 are the fix; 3–5 harden it; 6 is independent.

### Step 1 — never enable live logging where it cannot be read *(primary)*

In `tests/conftest.py::pytest_configure`, gate the live-logging branch on not
being an xdist worker, and neutralise the command-line route as well:

```python
_under_xdist = hasattr(config, "workerinput")
if os.environ.get("CLM_ENABLE_TEST_LOGGING") and not _under_xdist:
    config.option.log_cli = True
    config.option.log_cli_level = os.environ.get("CLM_LOG_LEVEL", "INFO")
    …
else:
    config.option.log_cli = False
    if _under_xdist:
        # `--log-cli-level` alone re-enables live logging; see
        # _pytest/logging.py::_log_cli_enabled.
        config.option.log_cli_level = None
```

Costs nothing (§3.3: zero live-log output under xdist today) and removes the
defect from every parallel run, nightly included.

**One compensation is required, and shipped with it.** The live handler's
`catching_logs(level=INFO)` pulled the *root* logger down to INFO for the whole
run, which is how third-party records (`urllib3`, `docker`, `asyncio` — the ones
that made the 2026-07-31 e2e failure report readable) reached `caplog` and the
"Captured log call" section. Dropping the live handler would have quietly
dropped those too, so the xdist branch sets `config.option.log_level` explicitly
instead. Report sections keep exactly the content they had.

### Step 2 — make `CliRunner` invocations immune regardless *(defence in depth)*

Step 1 leaves `-n0` runs exposed (the docker tier, and local debugging).

**Deviation from the original plan.** This was going to be a `cli_runner`
fixture plus a migration of all 92 modules off bare `CliRunner()`, policed by a
guard test. That was rejected during implementation: it is a large mechanical
diff, it only protects call sites someone remembers to convert, and a new test
written the obvious way (`CliRunner()`) would silently reacquire the bug.

What shipped instead — `tests/conftest.py::_install_clirunner_live_log_guard` —
attacks the same seam one level down. It wraps
`_LiveLoggingStreamHandler.emit` once, at `pytest_configure`, and drops the
handler's `capture_manager` for exactly those records emitted while a Click
isolation owns `sys.stdout`/`sys.stderr`:

```python
def emit(self, record):
    if self.capture_manager is not None and _inside_click_runner():
        saved, self.capture_manager = self.capture_manager, None
        try:
            original_emit(self, record)
        finally:
            self.capture_manager = saved
    else:
        original_emit(self, record)
```

`emit` then leaves both streams alone. The line is still written, because the
handler's stream is the terminal reporter, which holds its own file object and
never consults `sys.stdout`. Outside an isolation, nothing changes.

Detection is `isinstance(sys.stdout, click.testing._NamedTextIOWrapper)` — a
private type, so it is resolved defensively with a name-based fallback, and
`tests/test_clirunner_capture_integrity.py::test_isolation_detection_recognises_a_live_runner`
fails loudly if Click ever renames it.

Zero test churn, covers bare `CliRunner()`, covers tests not yet written, and
works identically under `-n0` and xdist.

### Step 3 — a canary that would have caught this

`tests/test_clirunner_capture_integrity.py`. It attaches its *own* live-log
handler for the duration of each test rather than depending on how the suite was
invoked, so it holds under `-n auto` (where step 1 deliberately leaves live
logging off) exactly as under `-n0`. Four properties: writes on both sides of a
record survive; `ClickException.show()` survives (the 2026-08-08 shape, where
`result.output` came back `''`); the guard does not leak past the invocation;
and the isolation detector actually fires.

Verified to fail for the right reason — with the guard removed, the first two
fail and the other two still pass, which is correct: they pin different
properties.

### Step 4 — stop `setup_logging()` nuking foreign handlers

`setup_logging()` now retires only the handlers it installed itself, tracked in
a module-level `_installed_handlers`. This is a production correctness fix — the
old code tore down *and closed* handlers belonging to any embedder, including
the MCP and web servers — and it removes the accidental immunity that was hiding
Defect A.

**Landed after steps 1–2, deliberately**: on its own it would have converted a
2-failure nightly into a ~35-failure one.

`_restore_worker_global_state` also snapshots and restores
`logging.getLogger().handlers` now, so no future test can strip pytest's
handlers for its successors. Handlers are detached and re-attached, never
closed. Pinned by
`tests/test_global_state_isolation.py::TestSetupLoggingLeavesForeignHandlersAlone`
(a foreign handler survives; it is not closed; clm's own handlers still do not
accumulate across repeat calls).

### Step 5 — delete the band-aids

Both brace-slicing sites now parse `result.stdout` directly — the JSON payload is
the command's contract, and reading the stdout stream alone keeps anything the
command writes to stderr out of the parse. Their existence was the trail this
defect left; removing them is how we know it is gone. Verified passing both with
and without `CLM_ENABLE_TEST_LOGGING`.

### Step 6 — Defect B

1. All three non-docker real-pool tests in `tests/e2e/test_e2e_lifecycle.py` now
   carry `@pytest.mark.serial("workerpool")`, joining the mock tests already in
   that group.
2. `notebook_count` dropped from 8 to 2 in the reuse test (and the healthy-worker
   wait from 10 to 4). The test proves reuse, not throughput; any pool size ≥ 1
   demonstrates it. Runtime fell from ~85s to 43s locally.
3. The hardcoded `120` at all three sites is replaced by `_completion_timeout()`,
   which reads `CLM_E2E_TIMEOUT` with the same `<= 0 → 1200` contract
   `test_e2e_course_conversion.py:497` uses, so the workflows' 600/900 finally
   apply.
4. Not done, deliberately: `@pytest.mark.flaky(reruns=2,
   only_rerun=["JobsPendingTimeoutError"])`. Hold it in reserve — per
   `testing.md`, a retry is a last resort after the cause is addressed, and 1–3
   address the cause. Reach for it only if the nightly shows this test failing
   again.

---

## 8. Verification

The fix is provable, not hopeful — Defect A reproduced deterministically, so
"it passes now" is a measurement rather than an absence of evidence. Measured on
Windows, `.venv`, after the changes:

| Command | Before | After |
|---|---|---|
| `CLM_ENABLE_TEST_LOGGING=1 pytest tests/cli/test_outline.py -n0` | 7 failed | 40 passed |
| `CLM_ENABLE_TEST_LOGGING=1 pytest tests/release -n0` | 6 failed | 118 passed |
| `CLM_ENABLE_TEST_LOGGING=1 pytest tests/cli/test_cache_explain.py -n0` | 8 failed | 10 passed |
| `CLM_ENABLE_TEST_LOGGING=1 CLM_LOG_LEVEL=INFO pytest -q` (`-n auto`) | 25, then 34 failed | 9457 passed |
| same, `-n4` (CI's shape) | 5 failed | 9457 passed |
| `CLM_ENABLE_TEST_LOGGING=1 pytest tests/cli tests/release tests/voiceover tests/recordings -n0` | 0 failed (immunised — the misleading baseline) | 3378 passed |
| `pytest tests/e2e/test_e2e_lifecycle.py -m "not docker" -n0` | 3 passed in ~124s | 3 passed in 82s |

The canary was also verified to fail for the right reason: with the guard
removed, `test_output_survives_a_log_record` and
`test_click_exception_after_a_log_record_still_reports` fail while the other two
still pass.

**On closing the detection gap.** The original plan was to give the PR unit tier
the nightly's env vars, so the unit/nightly asymmetry (§3.2) could not re-open
the hole. Step 1 makes that ineffective: live logging is now off under xdist
regardless of the env var, so the unit tier would never exercise the path. The
detection therefore lives in `tests/test_clirunner_capture_integrity.py`, which
attaches its own live-log handler and so pins the invariant on every run, in
every tier, at any parallelism. That is strictly better than a CI env tweak — it
cannot be silently disabled by a workflow edit.

---

## 9. Open questions

- Should `CLM_ENABLE_TEST_LOGGING` keep enabling *live* logging at all, or switch
  to `log_file` (which needs no capture suspension and survives xdist)? Step 1
  narrows this to `-n0` runs only, where live logging's real value — watching a
  hanging e2e test — actually applies. Left as is; revisit if the `-n0` docker
  tier ever grows `CliRunner` tests, since the guard rather than the gate is what
  protects it there.
- **Resolved during implementation, recorded so it is not re-investigated**: the
  worry that in-process `clm build` calls rewrite the developer's real log file
  is unfounded. `tests/conftest.py::_isolate_clm_log_dir` already points every
  worker at its own temp `CLM_LOG_DIR` for the whole session.
- Defect B's fixes remove the oversubscription, but the `serial` groups are
  applied by hand and nothing checks that a *new* real-worker test joins one.
  A collection-time assertion ("a test that calls `start_managed_workers` must
  carry `serial('workerpool')`") would close that, at the cost of a somewhat
  brittle static check. Not attempted.

## 10. Defect C — direct-worker boot thundering herd under `-n auto` (2026-08-17)

**Symptom.** On a 64-core Windows dev box, `pytest -m "not docker"` failed a
*rotating* set of 3–8 tests run-to-run, always the same family:
`test_lifecycle_integration.py`, `test_direct_integration.py`, and the
`test_cli_subprocess.py::test_cli_progress_messages` build test. Two shapes:
`TimeoutError: Worker ... did not reach idle within 15s` and one
`subprocess.TimeoutExpired` (120 s) on `clm build`. CI (4-core runners) never
saw it, and `-n 0` was always green. First observed during the 1.26.0 release
gate; shipped anyway (CI green, content unrelated — see the PR #846
discussion) and root-caused after.

**Root cause — not a defect in the code under test.** A direct worker boot =
full interpreter start + clm import (~1.34 s import cost, dominated by
nbconvert/jupytext) + DB init + registration. Boot latency scales roughly
linearly with *concurrent boots* (measured with a probe harness spawning real
`python -m clm.workers.notebook` workers against a scratch jobs DB):

| concurrent boots | boot latency (min–max) |
|---|---|
| 1 (idle box) | 1.4–1.9 s |
| 16 | 3.7–4.0 s |
| 48 | 8.4–10.3 s |

`-n auto` on 64 cores = up to 64 xdist workers; the real-worker tests are
`integration`-marked but `-m "not docker"` includes them, and when dozens land
in the same instant the herd pushes boots past the tests' 15 s registration
poll. Rotating set = which tests happened to coincide. CI's 4 xdist workers
cap the herd at ~4 concurrent boots — hence never flaky there.

**Fix — the established `serial` doctrine, applied to the missing family.**
Module-wide `serial("workerpool")` on the two real-worker integration files
(they already carried `integration`) plus `integration` + `serial("workerpool")`
on the two `clm build` subprocess tests in `test_cli_subprocess.py`
(`test_build_simple_course_subprocess`, `test_cli_progress_messages`), which
spawn the same worker herd inside a real `clm build`. Side benefit: those two
move out of the fast pre-push suite (they were the pre-push flake) while
remaining covered by `-m "not docker"` and CI's integration tier. Verified: 5/5
repro-loop runs green (previously ~40% failing), while a full fast suite ran
concurrently on the same box; fast suite 9712 passed.

**Comorbid diagnosability defect (fixed in passing).** The same investigation
found why the failed tests had *nothing to show*: worker logs were empty (0
bytes) even in passing runs. `src/clm/core/utils/__init__.py` called
`logging.basicConfig(level=WARNING)` **at import time** (legacy from before
1.0), so every worker entry point's own `basicConfig(level=INFO)` — which only
adds a handler when the root logger has none — was a silent no-op. Workers ran
INFO-silent in production (direct and Docker), and the executor's redirected
stderr log files stayed empty. Fixed by making the whole tree import-pure:
the `basicConfig` calls moved from module level into each entry point's
`main()` (`notebook_worker`, `drawio_worker`, `plantuml_worker`,
`jupyterlite_worker`), `notebook_processor.py` (imported in-process by
`clm.build.engine`) lost its module-level config entirely, and
`clm/core/utils/__init__.py` no longer configures anything. Worker logs now
carry real boot/registration/job lines (pinned by
`tests/infrastructure/workers/test_worker_logging_bootstrap.py`, which spawns a
real worker and asserts its INFO boot lines reach the executor log file). The
conftest failure diagnostic (`_dump_worker_logs`) now also harvests
`CLM_LOG_DIR/workers/`, so a future registration-timeout failure prints what
the worker actually said before stalling.

**Lesson.** A timeout that is generous on CI and under serial execution is not
generous under herd parallelism on big dev boxes: budget for boot-time
scaling, or serialize the herd. And empty log files are themselves a bug —
"the diagnostics are missing" is a defect to fix, not background noise.
