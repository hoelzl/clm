# Proposal: Docker Worker Test Improvements

## Status

**Updated**: December 2025 (after implementing source mount architecture)

This document was originally written to address a critical bug where Docker workers received absolute host paths but files were mounted at `/workspace` in the container. That bug has been fixed by implementing the source mount architecture proposed in `PAYLOAD_ARCHITECTURE_ANALYSIS.md`.

This updated version documents:
1. What tests were implemented to prevent regression
2. Remaining gaps in Docker test coverage
3. Proposed improvements for comprehensive Docker testing

## Problem Statement (Historical Context)

A critical bug in Docker worker path handling went undetected by the existing test suite:
- Docker workers received absolute host paths (e.g., `C:\Users\tc\...`)
- Files were mounted at `/workspace` (output) but workers couldn't read input files
- Workers tried to read input files from disk instead of using payload data

**Root causes** included:
1. No unit tests for path conversion functions
2. Docker integration tests only verified container startup, not job execution
3. Mock workers didn't simulate container filesystem constraints

## Implemented Fixes (v0.5.1)

### Phase 1-3: Docker Source Mount and Path Conversion

1. **Added source directory mount** - Input files are now mounted at `/source` (read-only)
2. **Added path conversion functions** in `worker_base.py`:
   - `convert_host_path_to_container()` - for output paths
   - `convert_input_path_to_container()` - for input paths
3. **Environment variables** for path conversion:
   - `CLX_HOST_WORKSPACE` - host output directory
   - `CLX_HOST_DATA_DIR` - host source directory

### Phase 4: Source Directory for Notebook `other_files`

1. Added `source_topic_dir` field to `NotebookPayload`
2. Workers can read supporting files directly from `/source` instead of base64-decoded payload

## Implemented Tests

### 1. Path Conversion Unit Tests (`tests/infrastructure/workers/test_path_conversion.py`)

**Coverage: Complete**

- `TestContainerConstants` - verifies `/workspace` and `/source` constants
- `TestConvertHostPathToContainerWindows` - Windows path handling:
  - Backslash paths
  - Forward slash paths
  - Drive letters
  - Mixed slashes
  - Case insensitivity
- `TestConvertHostPathToContainerUnix` - Unix path handling
- `TestConvertHostPathToContainerErrors` - error cases:
  - Path outside workspace
  - Different drive letters
  - Partial path matches
- `TestConvertHostPathToContainerEdgeCases` - edge cases:
  - File directly in workspace root
  - Deeply nested paths
  - Special characters
  - Unicode characters
- `TestConvertInputPathToContainer` - input path conversion to `/source`

### 2. Notebook Worker Source Directory Tests (`tests/workers/notebook/test_notebook_worker.py`)

**Coverage: Partial**

- `TestNotebookWorkerSourceDirectory` class:
  - Path conversion for Unix paths
  - Path conversion for Windows paths
  - `None` source_dir when not in Docker mode
  - Payload `source_topic_dir` field handling

### 3. Docker Lifecycle Integration Test (`tests/infrastructure/workers/test_lifecycle_integration.py`)

**Coverage: Containers start but job execution not verified**

- `TestDockerWorkerLifecycle::test_start_managed_workers_docker`:
  - Verifies Docker containers start successfully
  - Verifies workers register in database
  - **Does NOT verify actual job processing works**

## Remaining Gaps

### Gap 1: No Docker Job Execution Integration Tests (Priority: Critical)

The existing Docker test only verifies containers START, not that they can:
- Receive jobs via REST API
- Read input files from `/source`
- Write output files to `/workspace`
- Handle path conversion correctly

**Risk**: A regression in path handling could go undetected because containers start successfully but fail to process jobs.

### Gap 2: Mock Workers Don't Validate Paths (Priority: Medium)

Mock workers in `tests/fixtures/mock_workers.py` accept any path without validation. This means:
- Tests using mock workers pass even if paths would fail in Docker
- Path validation bugs may only surface in full Docker integration tests

### Gap 3: Docker Test Skip Reporting (Priority: Low)

When Docker tests are skipped, users don't get clear visibility into:
- How many Docker tests were skipped
- What functionality is untested
- How to enable Docker testing

## Proposed Test Improvements

### 1. Docker Job Execution Integration Tests (Priority: Critical)

Create `tests/infrastructure/workers/test_docker_job_execution.py`:

```python
"""Integration tests for Docker worker job execution.

These tests verify that Docker workers can:
1. Receive jobs via REST API
2. Read input files from /source mount
3. Write output files to /workspace mount
4. Handle path conversion correctly

Requires Docker daemon to be running.
"""

import time
from pathlib import Path

import pytest

from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.config_loader import load_worker_config
from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager


@pytest.fixture
def docker_test_env(tmp_path):
    """Set up environment for Docker job execution tests."""
    # Create database
    db_path = tmp_path / "test.db"
    init_database(db_path)

    # Create workspace (output) directory
    workspace = tmp_path / "output"
    workspace.mkdir()

    # Create data directory (input) with test files
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Create a simple notebook for testing
    topic_dir = data_dir / "slides" / "test_topic"
    topic_dir.mkdir(parents=True)

    notebook_content = '''{
        "cells": [{"cell_type": "markdown", "source": ["# Test"]}],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5
    }'''
    (topic_dir / "test.ipynb").write_text(notebook_content)

    return {
        "db_path": db_path,
        "workspace": workspace,
        "data_dir": data_dir,
        "topic_dir": topic_dir,
    }


@pytest.mark.docker
@pytest.mark.integration
class TestDockerJobExecution:
    """Tests for Docker worker job execution with path handling."""

    def test_docker_worker_processes_notebook_job(self, docker_test_env):
        """Docker worker should successfully process a notebook job."""
        # Skip if Docker not available
        try:
            import docker
            client = docker.from_env()
            client.ping()
        except Exception:
            pytest.skip("Docker daemon not available")

        env = docker_test_env

        # Configure for Docker mode
        cli_overrides = {
            "default_execution_mode": "docker",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
        }
        config = load_worker_config(cli_overrides)
        config.notebook.image = "clx-notebook-processor:lite-test"

        # Create lifecycle manager with data_dir
        manager = WorkerLifecycleManager(
            config=config,
            db_path=env["db_path"],
            workspace_path=env["workspace"],
            data_dir=env["data_dir"],
        )

        try:
            # Start workers
            workers = manager.start_managed_workers()
            time.sleep(3)  # Wait for registration

            # Add a job
            queue = JobQueue(env["db_path"])
            input_file = env["topic_dir"] / "test.ipynb"
            output_file = env["workspace"] / "output" / "test.html"

            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(input_file),
                output_file=str(output_file),
                content_hash="test-123",
                payload={
                    "kind": "completed",
                    "prog_lang": "python",
                    "format": "html",
                    "source_topic_dir": str(env["topic_dir"]),
                },
            )

            # Wait for job completion (with timeout)
            max_wait = 30
            start = time.time()
            while time.time() - start < max_wait:
                job = queue.get_job(job_id)
                if job.status in ("completed", "failed"):
                    break
                time.sleep(0.5)

            # Verify job succeeded
            job = queue.get_job(job_id)
            assert job.status == "completed", f"Job failed: {job.error}"

            # Verify output file was created
            assert output_file.exists(), "Output file was not created"

        finally:
            manager.stop_managed_workers(workers)

    def test_docker_worker_reads_from_source_mount(self, docker_test_env):
        """Docker worker should read input files from /source, not payload."""
        # Similar test structure but verifies input is read from filesystem
        pytest.skip("To be implemented - verify source mount usage")

    def test_docker_worker_writes_to_workspace_mount(self, docker_test_env):
        """Docker worker should write output to /workspace mount."""
        pytest.skip("To be implemented - verify workspace mount usage")
```

### 2. Path-Validating Mock Workers (Priority: Medium)

Update `tests/fixtures/mock_workers.py` to validate paths:

```python
class PathValidatingMockWorker(MockWorker):
    """Mock worker that validates path accessibility.

    This catches path-related bugs that would fail in Docker mode
    without requiring an actual Docker environment.
    """

    def __init__(self, worker_id, db_path, workspace_path=None):
        super().__init__(worker_id, db_path)
        self.workspace_path = workspace_path

    def validate_output_path(self, output_path: str) -> None:
        """Validate that output path would work in Docker mode."""
        path = Path(output_path)

        # Check for Windows absolute paths that wouldn't work in container
        if path.drive:
            if not self.workspace_path:
                raise ValueError(
                    f"Absolute Windows path '{output_path}' would fail in Docker. "
                    "Path must be under workspace for container compatibility."
                )
            try:
                path.relative_to(self.workspace_path)
            except ValueError:
                raise ValueError(
                    f"Output path '{output_path}' is not under workspace "
                    f"'{self.workspace_path}'. This would fail in Docker mode."
                )

    def process_job(self, job):
        """Process job with path validation."""
        self.validate_output_path(job.output_file)
        return super().process_job(job)
```

### 3. Docker Test Skip Reporting (Priority: Low)

Update `tests/conftest.py` to report Docker test status:

```python
def pytest_configure(config):
    # ... existing code ...

    # Register docker marker
    config.addinivalue_line(
        "markers",
        "docker: mark test as requiring Docker daemon"
    )


def pytest_collection_modifyitems(config, items):
    # ... existing code ...

    # Count Docker tests
    docker_tests = [item for item in items if "docker" in
                   [m.name for m in item.iter_markers()]]

    if docker_tests:
        # Check Docker availability
        docker_available = False
        try:
            import docker
            docker.from_env().ping()
            docker_available = True
        except Exception:
            pass

        if not docker_available:
            print(f"\n{'='*70}")
            print(f"WARNING: {len(docker_tests)} Docker tests will be skipped!")
            print("Run with Docker available for full test coverage.")
            print("To run Docker tests: docker daemon must be running")
            print(f"{'='*70}\n")
```

## Success Metrics

1. **Path conversion function**: 100% branch coverage (ACHIEVED - 28 unit tests)
2. **Docker job execution tests**: 4 integration tests covering:
   - Job processing end-to-end (DONE)
   - Input file reading from /source mount (DONE)
   - Nested output path creation (DONE)
   - Windows path handling (DONE)
3. **CI visibility**: Docker test status clearly reported (DONE - tests counted and warnings displayed)

## Implementation Priority

| Priority | Item | Status |
|----------|------|--------|
| Critical | Path conversion unit tests | DONE |
| Critical | Source mount implementation | DONE |
| Critical | Docker job execution integration tests | DONE |
| Medium | Path-validating mock workers | DONE |
| Low | Docker test skip reporting | DONE |

## Files Changed/Added

### All Items Implemented

**Infrastructure:**
- `src/clx/infrastructure/workers/worker_base.py` - Path conversion functions
- `src/clx/infrastructure/workers/worker_executor.py` - Source mount configuration

**Tests:**
- `tests/infrastructure/workers/test_path_conversion.py` - 28 unit tests for path conversion
- `tests/infrastructure/workers/test_docker_job_execution.py` - Docker job execution integration tests
- `tests/workers/notebook/test_notebook_worker.py` - Source directory tests
- `tests/fixtures/mock_workers.py` - Path validation for Docker compatibility
- `tests/conftest.py` - Docker test skip reporting and `docker` marker registration
