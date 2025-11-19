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
CLX_E2E_LOG_LEVEL=DEBUG pytest -m e2e

# Show only warnings and errors (quieter)
CLX_E2E_LOG_LEVEL=WARNING pytest -m e2e

# Default is INFO
CLX_E2E_LOG_LEVEL=INFO pytest -m e2e
```

### Progress Update Interval

Change how often progress updates are shown (in seconds):

```bash
# Update every 2 seconds (more frequent)
CLX_E2E_PROGRESS_INTERVAL=2 pytest -m e2e

# Update every 10 seconds (less frequent)
CLX_E2E_PROGRESS_INTERVAL=10 pytest -m e2e

# Default is 5 seconds
```

### Long Job Warning Threshold

Set when to warn about long-running jobs (in seconds):

```bash
# Warn after 10 seconds
CLX_E2E_LONG_JOB_THRESHOLD=10 pytest -m e2e

# Warn after 60 seconds
CLX_E2E_LONG_JOB_THRESHOLD=60 pytest -m e2e

# Default is 30 seconds
```

### Worker Details

Control whether to show per-worker activity:

```bash
# Hide worker details (cleaner output)
CLX_E2E_SHOW_WORKER_DETAILS=false pytest -m e2e

# Show worker details (default)
CLX_E2E_SHOW_WORKER_DETAILS=true pytest -m e2e
```

### Combined Example

```bash
# Verbose logging with frequent updates and early warnings
CLX_E2E_LOG_LEVEL=DEBUG \
CLX_E2E_PROGRESS_INTERVAL=2 \
CLX_E2E_LONG_JOB_THRESHOLD=10 \
pytest -m e2e

# Quiet logging for CI/CD
CLX_E2E_LOG_LEVEL=WARNING \
CLX_E2E_SHOW_WORKER_DETAILS=false \
pytest -m e2e
```

## Pytest Command-Line Options

You can also control logging with pytest's built-in options:

```bash
# Adjust pytest's log level (overrides CLX_E2E_LOG_LEVEL for pytest output)
pytest -m e2e --log-cli-level=DEBUG

# Disable live logging entirely (not recommended for e2e tests)
pytest -m e2e --log-cli-level=CRITICAL

# Show captured output even for passing tests
pytest -m e2e -v
```

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
CLX_E2E_LOG_LEVEL=ERROR pytest -m e2e
```

Or disable progress tracking entirely by setting the interval very high:

```bash
CLX_E2E_PROGRESS_INTERVAL=999999 pytest -m e2e
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
CLX_E2E_SHOW_WORKER_DETAILS=true pytest -m e2e
```

## Continuous Integration (CI)

CLX uses GitHub Actions for continuous integration. The CI workflow runs on every push and pull request.

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
- ✅ **Worker modules**: Notebook, PlantUML, and DrawIO workers from clx.workers package

### Test Matrix

Tests run on multiple Python versions:
- Python 3.11
- Python 3.12
- Python 3.13

### Code Coverage

Code coverage is collected across all test runs and uploaded to Codecov (Python 3.12 only).

### Running Tests Locally Like CI

To reproduce the CI environment locally:

```bash
# Install all dependencies (includes worker modules in clx.workers)
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

clx/tests/test_e2e_course_conversion.py::test_course_structure_validation
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

clx/tests/test_e2e_course_conversion.py::test_full_course_conversion_native_workers
[12:35:10] INFO Job #4 submitted: notebook for advanced_001.ipynb [correlation_id: test-004]
...
PASSED

============================================== 2 passed in 45.23s ============================================
```
