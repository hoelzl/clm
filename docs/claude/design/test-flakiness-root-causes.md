# Test-suite flakiness: root causes and remediation

**Status**: analysis complete, remediation proposed (not implemented)
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

### Step 2 — make `CliRunner` invocations immune regardless *(defence in depth)*

Step 1 leaves `-n0` runs exposed (the docker tier, and local debugging). Add a
`cli_runner` fixture in `tests/conftest.py` that neutralises the capture
suspension for the duration of `invoke()` only:

```python
@pytest.fixture
def cli_runner():
    """A CliRunner whose captured output survives log records (Defect A).

    pytest's live-log handler suspends *and resumes* global capture around every
    record, and resuming rebinds sys.stdout/sys.stderr to pytest's own capture
    objects — clobbering Click's isolation mid-invocation. Dropping the handler's
    capture_manager for the duration of the call makes emit() a no-op against the
    streams; the line still reaches the terminal reporter, which holds its own
    file object.
    """
```

implemented as a `CliRunner` subclass overriding `invoke()` to set
`handler.capture_manager = None` on any `_LiveLoggingStreamHandler` currently on
the root logger, restoring it afterwards. Then migrate the 92 test modules off
bare `CliRunner()` and add a guard test that fails on new bare uses.

### Step 3 — a canary that would have caught this

A test that, under forced live logging, invokes a command emitting
`echo → log record → echo` and asserts both echoes are in `result.output`. Home:
next to `tests/test_global_state_isolation.py`, which pins the analogous
invariant for issue #694.

### Step 4 — stop `setup_logging()` nuking foreign handlers

Change `src/clm/cli/commands/shared.py:47-51` to remove only the handlers it
previously installed (tracked in a module-level list) instead of clearing the
root logger. This is a production correctness fix — the current code breaks any
embedder's logging, including the MCP and web servers — and it removes the
accidental immunity that hides Defect A.

**Land this after steps 1–2, never before**: on its own it converts a 2-failure
nightly into a ~35-failure one.

Optionally also extend `_restore_worker_global_state` to snapshot and restore
`logging.getLogger().handlers`, so no future test can strip pytest's handlers for
its successors.

### Step 5 — delete the band-aids

`tests/cli/test_cache_explain.py:58-59` and
`tests/cli/test_assign_ids_refusals.py:60`: parse `result.stdout` directly. Their
existence is the trail this defect left; removing them is how we know it is gone.

### Step 6 — Defect B

1. Add `@pytest.mark.serial("workerpool")` to the real-pool tests in
   `tests/e2e/test_e2e_lifecycle.py`, joining the mock tests already in that
   group.
2. Drop `notebook_count` from 8 to 2. The test proves reuse, not throughput.
3. Replace the hardcoded `120` at lines 190/276/301 with the `CLM_E2E_TIMEOUT`
   read that `test_e2e_course_conversion.py:497` already uses, so the workflows'
   600/900 actually apply.
4. Only if it still flakes: `@pytest.mark.flaky(reruns=2,
   only_rerun=["JobsPendingTimeoutError"])`, per the existing scoped-retry policy.

---

## 8. Verification

The fix is provable, not hopeful — Defect A reproduces deterministically:

```bash
# Before: fails. After steps 1-2: passes.
CLM_ENABLE_TEST_LOGGING=1 pytest tests/cli/test_outline.py -n0

# Before: 25-34 failures (varies per run). After: 0, stably.
CLM_ENABLE_TEST_LOGGING=1 CLM_LOG_LEVEL=INFO pytest -q

# The nightly's exact shape.
CLM_ENABLE_TEST_LOGGING=1 CLM_LOG_LEVEL=INFO pytest -m "not docker" --log-cli-level=INFO
```

Add a run of the second command to whatever gate covers the unit tier, so the
unit/nightly configuration asymmetry (§3.2) cannot re-open the hole. The cheapest
version: give the PR unit tier the same env vars the nightly uses. Once steps 1–2
land that is free, and it closes the "nothing on a PR can catch it" gap
permanently.

---

## 9. Open questions

- Should `CLM_ENABLE_TEST_LOGGING` keep enabling *live* logging at all, or switch
  to `log_file` (which needs no capture suspension and survives xdist)? Live
  logging's real value is watching a hanging e2e test in a `-n0` run; a log file
  serves the post-mortem case better and is inert with respect to capture.
- `setup_logging()` is also called on every in-process `clm build` inside tests,
  which means each such test rewrites the developer's real log file under
  `get_log_file_path()`. Out of scope here, worth a separate look.
