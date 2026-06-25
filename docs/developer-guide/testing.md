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
registration tests), `subproc` (CPython/mitmdump subprocess spawns), `port`
(real socket binds). Bare `serial` (no arg) is a default catch-all group.

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

> The HTTP-replay / cassette tests need the `replay` extra (`pyyaml`,
> `filelock`). It is included in the auto-synced `dev` dependency group, so
> `uv sync` / `uv run pytest` always has it; without it those tests
> `importorskip`-skip rather than run.

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

The CI pipeline runs three test suites in order:

1. **Unit Tests**: Fast tests without external dependencies
   ```bash
   pytest -m "not slow and not integration and not e2e and not docker"
   ```

2. **Integration Tests**: Tests with workers and full setup (excluding Docker)
   ```bash
   pytest -m "integration and not docker"
   ```

3. **E2E Tests**: Full end-to-end course conversion tests (excluding Docker)
   ```bash
   pytest -m "e2e and not docker"
   ```

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
