# E2E Test Logging Guide

This document explains how to use the comprehensive logging system for e2e tests.

## Quick Start

Simply run your e2e tests normally, and you'll see live progress updates:

```bash
pytest -m e2e
```

## What You'll See

During test execution, you'll see:

1. **Job Submission** - When jobs are added to the queue:
   ```
   [12:34:56] INFO Job #1 submitted: notebook for lecture_001.ipynb [correlation_id: abc123]
   ```

2. **Worker Activity** - When workers pick up and process jobs:
   ```
   [12:34:57] INFO Worker 1 picked up Job #1 [notebook] for lecture_001.ipynb
   ```

3. **Progress Updates** - Periodic updates every 5 seconds (configurable):
   ```
   [12:35:02] INFO Progress: 8/15 jobs completed | 7 active | 0 failed (53%)
   ```

4. **Worker Details** - Showing what each worker is doing:
   ```
   [12:35:03] INFO Progress: 10/15 jobs completed | 5 active | 0 failed (67%)
                └─ Worker-1: Processing notebook job #12 (5.2s elapsed) [diagram.ipynb]
                └─ Worker-2: Processing drawio job #13 (3.8s elapsed) [intro.drawio]
   ```

5. **Long-Running Job Warnings** - Automatic alerts for slow jobs:
   ```
   [12:35:30] WARNING Job #12 has been processing for 30s [worker: 3, file: diagram.drawio]
   ```

6. **Job Completion** - With timing information:
   ```
   [12:35:07] INFO Job #1 completed in 2.45s [worker: 1, file: lecture_001.ipynb]
   ```

7. **Final Summary** - At the end of all jobs:
   ```
   [12:35:10] INFO ✓ All 15 jobs completed successfully in 14.2s (8 notebook, 5 drawio, 2 plantuml)
   ```

## Configuration

Control logging behavior with environment variables:

### Log Level

Set the verbosity of logs:

```bash
# Show all logs including DEBUG messages
CLM_LOG_LEVEL=DEBUG pytest -m e2e

# Show only warnings and errors (quieter)
CLM_LOG_LEVEL=WARNING pytest -m e2e

# Default is INFO
CLM_LOG_LEVEL=INFO pytest -m e2e
```

### Progress Update Interval

Change how often progress updates are shown (in seconds):

```bash
# Update every 2 seconds (more frequent)
CLM_E2E_PROGRESS_INTERVAL=2 pytest -m e2e

# Update every 10 seconds (less frequent)
CLM_E2E_PROGRESS_INTERVAL=10 pytest -m e2e

# Default is 5 seconds
```

### Long Job Warning Threshold

Set when to warn about long-running jobs (in seconds):

```bash
# Warn after 10 seconds
CLM_E2E_LONG_JOB_THRESHOLD=10 pytest -m e2e

# Warn after 60 seconds
CLM_E2E_LONG_JOB_THRESHOLD=60 pytest -m e2e

# Default is 30 seconds
```

### Worker Details

Control whether to show per-worker activity:

```bash
# Hide worker details (cleaner output)
CLM_E2E_SHOW_WORKER_DETAILS=false pytest -m e2e

# Show worker details (default)
CLM_E2E_SHOW_WORKER_DETAILS=true pytest -m e2e
```

### Combined Example

```bash
# Verbose logging with frequent updates and early warnings
CLM_LOG_LEVEL=DEBUG \
CLM_E2E_PROGRESS_INTERVAL=2 \
CLM_E2E_LONG_JOB_THRESHOLD=10 \
pytest -m e2e

# Quiet logging for CI/CD
CLM_LOG_LEVEL=WARNING \
CLM_E2E_SHOW_WORKER_DETAILS=false \
pytest -m e2e
```

## Pytest Command-Line Options

You can also control logging with pytest's built-in options:

```bash
# Adjust pytest's log level (overrides CLM_LOG_LEVEL for pytest output)
pytest -m e2e --log-cli-level=DEBUG

# Disable live logging entirely (not recommended for e2e tests)
pytest -m e2e --log-cli-level=CRITICAL

# Show captured output even for passing tests
pytest -m e2e -v
```

## Parallelism, the `serial` marker, and keeping the commit gate fast

The fast test suite runs on the **pre-push** git hook (not pre-commit), so a
commit pays only ruff + mypy (~3–5s) and the ~72s suite gates `git push` instead.
Both hooks install from one `pre-commit install` (`default_install_hook_types` in
`.pre-commit-config.yaml`). Run the suite manually any time with `pytest`, or as
the hook would with `uv run pre-commit run --hook-stage pre-push pytest`.

The suite runs in parallel via `pytest-xdist` (`-n auto`, capped to **16
workers** in the pre-push test hook by `scripts/run_pytest_hook.py` — see the
long comment there for the cap history and why 16 is safe). The default scheduler
is `--dist loadgroup` (set in `pyproject.toml` `addopts`): it load-balances as
usual but keeps any tests sharing an `xdist_group` on the **same** worker.

Four levers keep the per-commit fast suite both quick and flake-free:

**1. `serial` — pin contention-prone tests to one worker, by resource class.**
A mock worker pool (`tests/infrastructure/workers/test_lifecycle_mock.py`) polls
committed SQLite registration state; under many concurrent xdist workers its
threads get starved and registration appears to stall (issue #163). The `serial`
marker pins such tests onto a single `xdist_group` so they run one-at-a-time on
one worker while the rest of the suite stays fully parallel.

An optional argument names the **resource class** so that *different* heavy
families don't serialize behind each other: same-class tests share one group
(one worker, one-at-a-time); different classes get different groups that run on
*different* workers concurrently. Current classes: `workerpool` (worker-thread /
registration tests *and* real direct-worker integration suites — see below),
`subproc` (CPython/mitmdump subprocess spawns), `port` (real socket binds).
Bare `serial` (no arg) is a default catch-all group.

The `workerpool` class also covers the **real-worker integration suites**
(`test_lifecycle_integration.py`, `test_direct_integration.py`, and the two
`clm build` subprocess tests in `test_cli_subprocess.py`): spawning a direct
worker costs a full interpreter start + clm import (~1.4 s cold), and boot
latency scales with concurrent boots (measured on a 64-core box: 16 parallel
≈ 4 s, 48 parallel ≈ 10 s). Under `-n auto` dozens of such tests can land at
once and stretch boots past the 15 s registration poll — a rotating
TimeoutError flake CI never sees (4 xdist workers there). Serializing the
boot herd keeps them deterministic on any core count.

```python
import pytest

pytestmark = pytest.mark.serial("subproc")   # whole module
# or per-test:  @pytest.mark.serial("port")
# or unclassified default group:  @pytest.mark.serial
```

`tests/conftest.py` maps the marker (and its optional class arg) onto the
`xdist_group` via `tests/xdist_group_helpers.serial_group_name`;
`tests/test_serial_xdist_groups.py` is the meta-test that guards the mapping and
the split. **Reach for `serial` when a test contends for a global resource** (a
fixed port, a shared daemon, a registration table) — it is the cheap, surgical
alternative to widening timeouts, and a no-op under `-n0`. Give it a resource
class so it only serializes against tests that share the *same* resource.

**2. `integration` — keep real-subprocess long-poles off every commit.** A test
that spawns a real OS subprocess (a Jupyter kernel, a mitmdump proxy) is slow
*and* a flakiness surface that grows with the worker count. Mark it
`integration` so it runs in CI's dedicated integration step but is excluded from
the per-commit fast suite (both the default `addopts` filter and the pre-commit
hook exclude `integration`). Current residents:
`tests/infrastructure/test_http_replay_mitm.py` (the mitmproxy replay-transport
integration smoke tests — real `mitmdump` subprocess; the sole transport's
integration coverage) and the two `test_reaping_kernel_manager_kills_grandchild_*` tests
(real `ipykernel`). Note `slow` is the *wrong* marker for this — CI excludes
`slow` everywhere, so a `slow` test runs nowhere automatically.

**3. Event-driven waits — never busy-poll an async state.** When a test waits
for a background thread to drive a state transition, block on an event/callback,
not a `while ...: time.sleep()` loop. A busy-poll burns CPU that competes with
the very thread it is waiting on, so it gets *slower and flakier* as the worker
count rises. Reference patterns: `tests/recordings/test_session.py`'s
`_wait_for_state` attaches to the session's `on_state_change` callback and
blocks on a `threading.Condition`; the `JobManager` helpers wait on an
`EventBus`-fed `threading.Event`. Both keep a generous wall-clock ceiling purely
as a backstop. (For a timer-*expiring* transient state, widen the state's own
lifetime past the wait ceiling rather than the ceiling itself — see the
`retake_window_seconds` note in `test_session.py`.)

> **Polling a transient state is itself the trap.** When the state you wait
> for is *self-expiring* (a worker's `"busy"` status that lasts only one job, a
> session's auto-expiring `ARMED_AFTER_TAKE`), a generous poll ceiling does not
> help — a CPU-starved poll thread can be descheduled across the whole window
> and miss it. Widen the **state's lifetime** instead: gate the work on an
> `Event` the test releases so the state persists until observed (see
> `MockWorker.gate_job` in `tests/infrastructure/workers/test_worker_base.py`).

**4. `flaky` — a scoped, loud safety net, never a global rerun.** A handful of
families stay CPU-starvation-sensitive even after the structural fixes (the
worker-registration `#163` family, the threaded `test_worker_base.py` tests).
Mark *those* with `@pytest.mark.flaky(reruns=2, reruns_delay=1,
only_rerun=[...])` (pytest-rerunfailures) so a contention loss is retried once
or twice instead of forcing a manual re-run. The rules that keep this from
masking real bugs:

- **No global `--reruns`.** Reruns are opt-in per test via the marker only; a
  blanket rerun across the whole suite hides regressions and is forbidden.
- **`only_rerun` scopes the retry** to the contention exception signature
  (`OSError`, `PermissionError`, `AssertionError`, `OperationalError`,
  `TimeoutError`). A deterministic logic regression fails on every retry, so it
  still goes red — only *intermittent* failures are absorbed.
- **`-rR` (in `addopts`) makes every retry visible.** A test that reruns often
  is a signal to fix its root cause, not to widen the net. `flaky` is the
  last-resort lever; reach for levers 1–3 first.

There is also a session-wide env override
`CLM_HEARTBEAT_SLOW_WRITE_THRESHOLD_SECONDS` (set to 30 in `tests/conftest.py`):
the production 50ms heartbeat self-disable threshold legitimately trips under
xdist load, so the suite relaxes it in one place instead of every heartbeat
test re-patching the constant. Never raise the **production** default — relax it
only in tests, via this env var.

### Worker-global state is restored after every test (#694)

Under xdist, every test in a worker shares one Python process, so a test that
mutates process-global state without restoring it poisons its successors —
possibly hundreds of tests later, which makes the flake nearly impossible to
connect to its cause from the failure alone. The 2026-07-26 nightly failed on
exactly that: a config test reloaded the global `ClmConfig` singleton under a
monkeypatched `CLM_LOGGING__LOG_LEVEL=ERROR` (monkeypatch reverts the env var,
**not** the singleton), a later in-process `clm build` applied the poisoned
level via `setup_logging` → `getLogger("clm").setLevel(ERROR)`, and a
`caplog`-based assertion ~300 tests downstream lost its WARNING and failed.

The autouse `_restore_worker_global_state` fixture in `tests/conftest.py`
snapshots the clm logger chain (level/disabled/propagate) and the config
singleton before each test and restores both after it.
`tests/test_global_state_isolation.py` pins the property: one test pollutes
on purpose, the next proves the pollution cannot cross the boundary.

Two takeaways when writing tests:

- **Never mutate process-global state without restore.** `monkeypatch` covers
  env vars and attributes, but not cached singletons — if a test reloads
  `get_config()`, that is exactly the case the fixture now guards, but new
  globals need the same discipline.
- **Guard `caplog` assertions with `caplog.set_level(level, logger=...)`.**
  A bare `caplog.records` assertion inherits whatever effective level the
  logger happens to have; nearly all caplog-using files in this suite set the
  level explicitly, and the fixture is the second line of defense, not a
  license to skip the guard.

### Per-test timeout

`[tool.pytest.ini_options] timeout` (pyproject) defaults to **120s** — the same
ceiling CI enforces on the fast suite (`--timeout=120`). The slowest legitimate
fast test measures ~5.5s (16-worker dev box) / ~11s (64-worker contention), so
120s is a generous hang **backstop**, not a performance gate: it converts a
contention hang on the pre-push gate from a 10-minute stall (the old 600s) into
a prompt, attributable failure the scoped `flaky` retry can absorb. The heavier
suites need more, so `tests/conftest.py` bumps their per-test timeout at
collection time to match CI — `integration` → 240s, `e2e`/`slow`/`docker` →
600s — so running them **locally** (a non-default `-m` selection, e.g.
`pytest -m e2e`) never false-kills against the fast default. Override a single
test with `@pytest.mark.timeout(N)`; it takes precedence over all of the above.

> The HTTP-replay / cassette tests need the `replay` extra (`pyyaml`,
> `filelock`). It is included in the auto-synced `dev` dependency group, so
> `uv sync` / `uv run pytest` always has it; without it those tests
> `importorskip`-skip rather than run.

## The claimed-wired rule: claims about behavior need an executing test

Two failures in the Mobile Deck Studio (issues #696/#697, PR #704) share one
root cause, and it was not a coding mistake:

- The tier-2 preview was signed off as "P4 ✅ implemented" while its in-page
  consumer had been **unreachable from the day it was written** — gated on a
  contract (`cell_type === "markdown"`) the API never produced. Nothing
  noticed, because nothing executed the frontend path.
- Sanitizer docstrings stated security properties ("the filter can only ever
  be more restrictive") that were false, and the suite passed while the bug
  that contradicted them was live.

So, as a standing rule:

1. **A build record, phase sign-off, or design doc that claims a path is
   wired must cite the test that executes it.** "Wired" means the test drives
   the real call chain — for a frontend feature, the predicate *and* the
   consumer *and* the payload contract, not just the server endpoint. If no
   executing test exists, the record says "scaffolded", not "done".
2. **A security-relevant claim in a docstring or comment** ("X can never
   happen", "this is strictly more restrictive", "active content is removed")
   **must cite the test that proves it** — and the test must be able to fail
   for the reason the claim names. (An earlier version of "active content is
   removed" passed vacuously: the tag set it relied on was a library default,
   so the assertion held while the claim was false as written.)
3. **Test both sides of a contract.** The tier-2 gate is pinned by executing
   the JS predicate under `node` *and* by asserting the payload the real
   service emits; either half alone would have missed the original bug — a
   predicate can be right about a contract that changed, and a contract can be
   right with nobody consulting it.
4. **Static source checks are a legitimate last resort** (e.g. pinning that a
   call site exists when executing it would require stubbing a DOM), but name
   them as static checks in the test's name or docstring, so the next reader
   knows what is *not* proven.

For security boundaries, property-style tests beat case-by-case regression
tests: the sanitizer's prefix×scheme cross-product caught a normalization
desync that two rounds of individually written cases had missed.
`tests/web/studio/test_tier2_preview.py` is the reference example for all of
the above.

### Two implementations of one contract need a parity test

When the same rule is written twice — because one copy runs at write time and
the other audits what was written, or because one lives where the other cannot
be imported — the duplication is the defect, and hand-mirrored test *pairs* do
not contain it.

Issue #875 is the worked example. The cassette secret filter
(`cassette_format`) decides what to redact when recording; `clm cassette scan`
(`cassette_doctor`) decides what to report about already-committed files. Their
contract is *the audit flags a body iff re-recording would rewrite it* — break
it one way and the audit's exit-1 gate becomes unsatisfiable, break it the other
and it returns a **false all-clear**. The two walks were byte-identical on the
key test and **wrong together**, so every mirrored test pair passed. What
finally held them was one suite —
`tests/infrastructure/test_cassette_scanner_recorder_parity.py` — that pushes a
shared payload table through *both* and asserts only that they agree.

Three things that suite had to learn, all of which generalise:

1. **Assert agreement, not outcomes** — the parity test must not encode what
   either side does, or it becomes a second copy of the thing it is checking.
2. **Pin the direction separately.** Parity alone is satisfied by both sides
   ignoring a case. A handful of tests must assert the *result* (here: the
   plaintext is gone from what the recorder writes).
3. **Do not normalise the inputs.** The first version passed `text.encode()` to
   one side and `text` to the other, so an entire limb — byte decoding — went
   untested, and the BOM bug that lived there survived the suite that existed to
   catch it.

### Assert the contract, not the platform's implementation limits

A test whose outcome depends on *where* an implementation limit falls will pass
on the machine you wrote it on and fail in CI. Measured on a deeply nested JSON
body (PR #876): at depth 1200 the recursive walk overflows on Windows and Linux
3.13 but **completes** on Linux 3.12, and the C-implemented `json` scanner has
its own ceiling distinct from `sys.getrecursionlimit()`, so the parse and the
walk can give out independently, in either order.

Both behaviours were correct. Two CI round-trips were spent asserting one of
them. Write the invariant that holds on every side of the limit — usually a
relational property (*the two components agree*, *no secret survives*, *the walk
continues*) — and reproduce the other regime locally with
`sys.setrecursionlimit(...)` rather than pushing to find out. The same shape
applies to path-length limits, `os.cpu_count()`-derived budgets, float
formatting and locale-dependent ordering.

Corollary for the code: guard the parse and the traversal **together**. Splitting
that guard is what made the behaviour platform-dependent in the first place.

## Troubleshooting

### "I don't see any logs during test execution"

Make sure pytest's live logging is enabled. It should be configured by default in `pyproject.toml`, but you can verify:

```bash
# Force enable live logging
pytest -m e2e --log-cli-level=INFO
```

### "Too much output - I only want to see failures"

Use a higher log level:

```bash
CLM_LOG_LEVEL=ERROR pytest -m e2e
```

Or disable progress tracking entirely by setting the interval very high:

```bash
CLM_E2E_PROGRESS_INTERVAL=999999 pytest -m e2e
```

### "I want to see what files are being processed"

The input file name is included in all log messages. Just look for patterns like:

- `"Job #X submitted: ... for <filename>"`
- `"Worker X picked up Job #Y [...] for <filename>"`
- `"Job #X completed ... [file: <filename>]"`

### "Tests are hanging - how do I know which job is stuck?"

The long-running job warnings will automatically alert you:

```
[12:35:30] WARNING Job #12 has been processing for 30s [worker: 3, file: diagram.drawio]
```

You can also enable worker details to see current activity:

```bash
CLM_E2E_SHOW_WORKER_DETAILS=true pytest -m e2e
```

## Continuous Integration (CI)

CLM uses GitHub Actions for continuous integration. The CI workflow runs on every push and pull request.

### What Tests Run on CI?

Every PR runs **four** suites, as parallel jobs on a `python-version × suite`
matrix (3.12 and 3.13):

| Suite | Selector |
|---|---|
| `unit` | `-m "not slow and not integration and not e2e and not docker"` |
| `integration` | `-m "integration and not docker and not slow"` |
| `e2e` | `-m "e2e and not docker and not slow"` |
| `slow` | `-m "slow and not docker"` |

The four selectors partition cleanly — nothing runs twice. All eight jobs plus
`Lint and type check` are **required** status checks.

A ninth job builds the Docker images and runs `-m "docker"`. It is **not**
required: the image builds fetch base images plus deno / ijava / dotnet / the
DrawIO `.deb` from four external hosts, and that has produced a ~12% infra
failure rate (Docker Hub timeouts, partial `curl` transfers) unrelated to any
change. Consequence worth internalising: **a green PR proves nothing about the
Docker tier**, and a Docker regression can reach `master`. Before merging
anything that un-skips a test module or touches worker/image wiring, check what
it does under `-m docker`.

> **Adding or renaming a suite?** Required checks are matched by job *name*, so
> a matrix change must be paired with an update to the "Require CI green"
> ruleset in the same breath. A required check that never reports leaves every
> PR stuck on "Expected — waiting for status".

#### Why `slow` is on PR CI and not nightly-only

It used to be excluded everywhere — from all three CI steps *and* the local
default — so ~37 tests ran nowhere at all, including the only proof that a
cached notebook replays byte-identically to a direct execution
(`tests/workers/notebook/test_cache_equivalence.py`), the
worker-reuse-across-builds e2e tests, and all 18 real-subprocess CLI tests.

It was never excluded for cost: the tier is 37 tests in ~78 s at `-n 4`. As a
*parallel* job it adds machine-minutes but no wall clock — the `unit` job runs
~4.5 min and the Docker job ~6.5 min.

Marker choice, restated: `integration` keeps a test off the per-commit gate but
on every PR. `slow` now also runs on every PR, so the two differ only in which
job they land in — pick by what the test *is*, not by where you want it to run.

### The corpus gates: bundled, pinned-public, and private

Three tiers exercise the sync/lens engines against real deck corpora:

1. **Bundled fixtures** (`tests/data/doc_corpus/`) — five pairs, fast suite,
   always present.
2. **Pinned public corpus** (`tests/slides/test_public_corpus.py`,
   `integration`) — the [ClmTestCourse](https://github.com/hoelzl/ClmTestCourse)
   repo at the commit pinned in `tests/slides/public_corpus_pin.py`, fetched
   by `python scripts/fetch_test_corpus.py` into the gitignored
   `.clm-test-corpus/` (CI does this for the integration suite). Because the
   corpus is pinned, the gates assert **exact** numbers, not ceilings — any
   drift is a CLM behavior change. Bump the pin and the expected numbers
   together, deliberately; regenerate the corpus itself with
   `scripts/curate_test_course.py` (#682).
3. **Private full corpus** (`TestRealCorpus*` in `test_doc_lens_corpus.py` /
   `test_sync_diff_corpus.py`, `integration + slow`) — the maintainer's live
   PythonCourses checkout via `CLM_SYNC_CORPUS_DIR`; local/release-time only.
   Its ceilings carry a corpus-revision context line
   (`tests/slides/corpus_revision.py`) so a breach says whether CLM or the
   corpus moved.

### The nightly full-suite run

`.github/workflows/nightly.yml` is a **flake and rot detector**, not a coverage
backstop. It runs the whole suite against *unchanged* `master`: `-m "not docker"`
in one job, and the Docker tier (images built from scratch) in another.

That is the one thing PR CI structurally cannot do. A PR run tells you "this
change is fine"; thirty runs of identical code tell you "this test is 3% flaky",
which is how a contention regression like issue #163 surfaces before it wastes
someone's afternoon. It also re-runs the Docker tier (a required PR check since
#679, but skipped for docs-only changes) against unchanged master, so an
environment-induced Docker breakage surfaces within a day even when no code PR
happens to trigger it.

(Dependency drift, the other usual nightly justification, barely applies here:
CI installs from `uv.lock` with `UV_EXCLUDE_NEWER` pinned, so nothing moves
underneath us.)

**Failures file a GitHub issue** labelled `nightly-failure` — or comment on the
existing open one, so an outage produces one issue rather than one per night.
That routing is the point of the job: a nightly nobody reads manufactures the
feeling of coverage without providing any. The mechanism lives in the
`.github/actions/report-failure` composite action. `workflow_dispatch` is
enabled, so the run and its failure route can be exercised on demand.

When triaging a nightly failure, check first whether the same test failed on
earlier nights. `master` did not change between runs, so a first-time failure
with no corresponding merge is a **flake**, not a regression.

But *"different tests fail each night"* is not the same as *"each is its own
small flake"*. The 2026-07-31 and 2026-08-08 nightlies looked like five
unrelated one-off failures across two files; they were one deterministic bug
(pytest's live-log handler destroying Click's `CliRunner` capture) latent in
~90 modules, with a second bug deciding at random how many of them were exposed
on any given night. When a nightly failure looks arbitrary, check whether the
failing tests share a *mechanism* before concluding they share nothing.

Two invariants came out of that and are pinned by tests — if either fails, read
`docs/claude/design/test-flakiness-root-causes.md` before changing anything:

- **CLI output survives log records.** `tests/conftest.py` neutralises pytest's
  live-log handler for records emitted inside a `CliRunner` isolation, because
  that handler suspends *and resumes* global capture and resuming rebinds
  `sys.stdout`/`sys.stderr` away from Click. Pinned by
  `tests/test_clirunner_capture_integrity.py`.
- **Nothing strips the root logger's handlers.** pytest attaches its handlers
  there once for the whole run loop, so a test that clears the root logger
  disables them for every later test in that worker. Pinned by
  `tests/test_global_state_isolation.py`.

### CI Environment Setup

The GitHub Actions runner includes:

- ✅ **PlantUML**: Java 17 + PlantUML JAR downloaded from GitHub releases
- ✅ **DrawIO**: DrawIO desktop app installed from GitHub releases
- ✅ **Xvfb**: Virtual X server for headless DrawIO rendering
- ✅ **Docker**: Pre-installed on ubuntu-latest runners (not used in current tests)
- ✅ **Worker modules**: Notebook, PlantUML, and DrawIO workers from clm.workers package

### Test Matrix

Tests run on multiple Python versions:
- Python 3.12
- Python 3.13

### Code Coverage

Code coverage is collected across all test runs and uploaded to Codecov (Python 3.12 only).

### Running Tests Locally Like CI

To reproduce the CI environment locally:

```bash
# Install all dependencies (includes worker modules in clm.workers)
pip install -e ".[all]"

# Set up PlantUML
wget -O plantuml.jar https://github.com/plantuml/plantuml/releases/download/v1.2024.6/plantuml-1.2024.6.jar
export PLANTUML_JAR=$PWD/plantuml.jar

# Run tests in CI order
pytest -m "not slow and not integration and not e2e and not docker"
pytest -m "integration and not docker"
pytest -m "e2e and not docker"
```

### CI Workflow File

The CI configuration is in `.github/workflows/ci.yml`. It includes:
- Dependency caching for faster builds
- Parallel test execution across Python versions
- Linting and type checking (separate job)
- Code coverage reporting

## Architecture

The logging system consists of:

1. **ProgressTracker** - Centralized monitoring of job lifecycle
2. **JobQueue Logging** - Logs at job submission and status changes
3. **Worker Logging** - Logs when workers pick up and complete jobs
4. **Backend Logging** - Logs during job orchestration
5. **Correlation IDs** - Trace jobs end-to-end across components

All components log the input file name to make it easy to correlate logs with your test data.

## Example Test Run

Here's what a typical e2e test run looks like:

```
$ pytest -m e2e

============================================ test session starts =============================================
collected 3 items / 1 deselected / 2 selected

clm/tests/test_e2e_course_conversion.py::test_course_structure_validation
[12:34:56] INFO E2E test logging configured: level=INFO, progress_interval=5.0s, long_job_threshold=30.0s
[12:34:56] INFO Initialized SQLite backend with database: /tmp/test_db.db
[12:34:56] INFO Job #1 submitted: notebook for lecture_001.ipynb [correlation_id: test-001]
[12:34:56] INFO Job #2 submitted: notebook for lecture_002.ipynb [correlation_id: test-002]
[12:34:56] INFO Job #3 submitted: drawio for diagram_001.drawio [correlation_id: test-003]
[12:34:56] INFO Waiting for 3 job(s) to complete...
[12:34:57] INFO Worker 1 picked up Job #1 [notebook] for lecture_001.ipynb
[12:34:57] INFO Worker 2 picked up Job #2 [notebook] for lecture_002.ipynb
[12:34:57] INFO Worker 3 picked up Job #3 [drawio] for diagram_001.drawio
[12:35:01] INFO Progress: 1/3 jobs completed | 2 active | 0 failed (33%)
[12:35:02] INFO Job #1 completed in 5.23s [worker: 1, file: lecture_001.ipynb]
[12:35:03] INFO Job #2 completed in 5.81s [worker: 2, file: lecture_002.ipynb]
[12:35:06] INFO Progress: 2/3 jobs completed | 1 active | 0 failed (67%)
[12:35:08] INFO Job #3 completed in 10.12s [worker: 3, file: diagram_001.drawio]
[12:35:08] INFO ✓ All 3 jobs completed successfully in 12.4s (2 notebook, 1 drawio)
[12:35:08] INFO All jobs completed successfully
PASSED

clm/tests/test_e2e_course_conversion.py::test_full_course_conversion_native_workers
[12:35:10] INFO Job #4 submitted: notebook for advanced_001.ipynb [correlation_id: test-004]
...
PASSED

============================================== 2 passed in 45.23s ============================================
```
