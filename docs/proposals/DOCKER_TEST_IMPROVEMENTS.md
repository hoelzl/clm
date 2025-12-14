# Proposal: Docker Worker Test Improvements

## Problem Statement

A critical bug in Docker worker path handling went undetected by the existing test suite. Docker workers received absolute host paths (e.g., `C:\Users\tc\...`) but files were mounted at `/workspace` in the container. Workers also tried to read input files from disk instead of using payload data.

This document analyzes why existing tests failed to catch the issue and proposes improvements.

## Root Cause Analysis

### Why Tests Didn't Catch the Bug

1. **No Unit Tests for Path Conversion Function**
   - The `convert_host_path_to_container()` function in `worker_base.py` has zero test coverage
   - This is the core function that handles Windows/Unix to container path translation

2. **Docker Integration Tests Are Optional**
   - Tests marked `@pytest.mark.docker` are skipped when Docker daemon isn't available
   - CI environments may not have Docker, causing these tests to be silently skipped
   - Developers see green tests but Docker functionality is untested

3. **Mock Workers Don't Simulate Container Filesystem**
   - Mock workers in `tests/fixtures/mock_workers.py` use direct file paths
   - They don't validate that paths would be accessible inside a container
   - Path conversion issues are invisible to mocked tests

4. **Notebook Worker Tests Use Extensive Mocking**
   - `test_notebook_worker.py` mocks `NotebookProcessor`, `create_output_spec`, etc.
   - Actual file I/O code paths are never exercised
   - The bug exists in the unmocked code that reads/writes files

5. **Direct Integration Tests Don't Cover Docker Mode**
   - `test_direct_integration.py` has comprehensive tests for subprocess workers
   - No equivalent tests exist for Docker worker job execution
   - Docker lifecycle tests only verify containers start, not that jobs succeed

6. **E2E Tests Are Conditional**
   - `test_e2e_lifecycle.py::test_e2e_managed_workers_docker_mode()` skips if Docker unavailable
   - No mandatory Docker tests in CI pipeline

## Proposed Test Improvements

### 1. Unit Tests for Path Conversion (Priority: Critical)

Create `tests/infrastructure/workers/test_path_conversion.py`:

```python
"""Unit tests for Docker path conversion utilities."""

import pytest
from pathlib import Path, PurePosixPath, PureWindowsPath

from clx.infrastructure.workers.worker_base import (
    convert_host_path_to_container,
    CONTAINER_WORKSPACE,
)


class TestConvertHostPathToContainer:
    """Tests for convert_host_path_to_container function."""

    def test_converts_windows_path_to_container_path(self):
        """Should convert Windows absolute path to container path."""
        host_path = r"C:\Users\tc\workspace\output\file.ipynb"
        host_workspace = r"C:\Users\tc\workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_converts_unix_path_to_container_path(self):
        """Should convert Unix absolute path to container path."""
        host_path = "/home/user/workspace/output/file.ipynb"
        host_workspace = "/home/user/workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_handles_nested_subdirectories(self):
        """Should preserve nested directory structure."""
        host_path = r"C:\workspace\public\De\Course\Slides\file.ipynb"
        host_workspace = r"C:\workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/public/De/Course/Slides/file.ipynb")

    def test_raises_error_when_path_not_under_workspace(self):
        """Should raise ValueError when path is outside workspace."""
        host_path = r"C:\other\location\file.ipynb"
        host_workspace = r"C:\workspace"

        with pytest.raises(ValueError, match="not under workspace"):
            convert_host_path_to_container(host_path, host_workspace)

    def test_handles_windows_path_with_forward_slashes(self):
        """Should handle Windows paths that use forward slashes."""
        host_path = "C:/Users/tc/workspace/output/file.ipynb"
        host_workspace = "C:/Users/tc/workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_handles_trailing_slashes_in_workspace(self):
        """Should handle workspace paths with trailing slashes."""
        host_path = r"C:\workspace\output\file.ipynb"
        host_workspace = r"C:\workspace\"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_container_workspace_constant(self):
        """Should use correct container workspace path."""
        assert CONTAINER_WORKSPACE == "/workspace"
```

### 2. Docker Worker Integration Tests (Priority: High)

Create `tests/infrastructure/workers/test_docker_integration.py`:

```python
"""Integration tests for Docker worker execution.

These tests verify that Docker workers can:
1. Start successfully
2. Process jobs with correct path handling
3. Write output files to mounted volumes
4. Handle payload data correctly

Requires Docker daemon to be running.
"""

import os
import pytest
from pathlib import Path

from clx.infrastructure.workers.worker_executor import DockerWorkerExecutor
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.database.schema import init_database


@pytest.fixture
def docker_available():
    """Check if Docker is available and skip if not."""
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return True
    except Exception as e:
        pytest.skip(f"Docker not available: {e}")


@pytest.fixture
def workspace_with_files(tmp_path):
    """Create a workspace with test files."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create output directory structure
    output_dir = workspace / "output" / "public" / "En"
    output_dir.mkdir(parents=True)

    return workspace


@pytest.mark.docker
@pytest.mark.integration
class TestDockerWorkerPathHandling:
    """Tests for Docker worker path handling."""

    def test_worker_receives_host_workspace_env_var(
        self, docker_available, workspace_with_files, tmp_path
    ):
        """Docker container should receive CLX_HOST_WORKSPACE environment variable."""
        db_path = tmp_path / "jobs.db"
        init_database(db_path)

        executor = DockerWorkerExecutor(
            workspace_path=workspace_with_files,
            db_path=db_path,
            log_level="DEBUG",
        )

        # Start a container and verify env var is set
        container = executor._start_container("notebook", "test-worker-1")
        try:
            env_vars = container.attrs["Config"]["Env"]
            workspace_var = next(
                (v for v in env_vars if v.startswith("CLX_HOST_WORKSPACE=")),
                None
            )
            assert workspace_var is not None
            assert str(workspace_with_files.absolute()) in workspace_var
        finally:
            container.stop()
            container.remove()

    def test_worker_can_write_to_converted_output_path(
        self, docker_available, workspace_with_files, tmp_path
    ):
        """Docker worker should write output to correct container path."""
        db_path = tmp_path / "jobs.db"
        init_database(db_path)

        job_queue = JobQueue(db_path)

        # Create a job with host-style output path
        output_file = workspace_with_files / "output" / "test.txt"
        job_id = job_queue.add_job(
            job_type="notebook",
            input_file=str(workspace_with_files / "input.ipynb"),
            output_file=str(output_file),
            content_hash="test123",
            payload={"data": '{"cells": [], "metadata": {}}'},
        )

        # Process job with Docker worker
        # ... (implementation depends on test infrastructure)

        # Verify output file was created
        assert output_file.exists()


@pytest.mark.docker
@pytest.mark.integration
class TestDockerWorkerPayloadHandling:
    """Tests for Docker worker payload data handling."""

    def test_worker_uses_payload_data_not_disk(
        self, docker_available, workspace_with_files, tmp_path
    ):
        """Worker should read notebook content from payload, not from disk."""
        # Create job with payload data but NO input file on disk
        # Worker should succeed because it reads from payload
        pass  # Implementation

    def test_worker_fails_gracefully_when_payload_missing_data(
        self, docker_available, workspace_with_files, tmp_path
    ):
        """Worker should raise clear error when payload has no data field."""
        pass  # Implementation
```

### 3. Mandatory Docker Tests in CI (Priority: High)

Update CI configuration to require Docker tests:

```yaml
# .github/workflows/test.yml (example)
jobs:
  test-docker:
    runs-on: ubuntu-latest
    services:
      docker:
        image: docker:dind
        options: --privileged
    steps:
      - uses: actions/checkout@v4
      - name: Run Docker integration tests
        run: |
          pytest -m docker --fail-on-skip
```

### 4. Enhanced Mock Workers (Priority: Medium)

Update `tests/fixtures/mock_workers.py` to validate paths:

```python
class PathValidatingMockWorker:
    """Mock worker that validates path accessibility."""

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path

    def process_job(self, job: dict):
        """Process job with path validation."""
        output_path = Path(job["output_file"])

        # Validate output path is under workspace
        try:
            output_path.relative_to(self.workspace_path)
        except ValueError:
            raise ValueError(
                f"Output path {output_path} is not under workspace {self.workspace_path}. "
                "This would fail in Docker mode."
            )

        # Simulate container filesystem by only allowing /workspace paths
        if os.name == 'nt':  # Windows
            # Check for Windows absolute path that wouldn't work in container
            if output_path.drive:
                raise ValueError(
                    f"Absolute Windows path {output_path} would fail in Docker container. "
                    "Path should be converted to container format."
                )
```

### 5. Notebook Worker Tests Without Mocking File I/O (Priority: Medium)

Add tests that exercise actual file operations:

```python
# tests/workers/notebook/test_notebook_worker_integration.py

@pytest.mark.integration
class TestNotebookWorkerFileIO:
    """Integration tests for notebook worker file operations."""

    def test_reads_notebook_from_payload_data(self, tmp_path):
        """Worker should read notebook content from job payload."""
        # Create worker
        # Submit job with payload containing notebook data
        # Verify worker uses payload, not disk read
        pass

    def test_writes_output_to_correct_path(self, tmp_path):
        """Worker should write output to job.output_file path."""
        pass

    def test_creates_output_directory_if_missing(self, tmp_path):
        """Worker should create parent directories for output file."""
        pass
```

### 6. Test Markers and Skip Handling (Priority: Medium)

Update pytest configuration to track skipped Docker tests:

```python
# conftest.py

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "docker: mark test as requiring Docker daemon"
    )

def pytest_collection_modifyitems(config, items):
    """Report Docker test status."""
    docker_tests = [item for item in items if "docker" in item.keywords]
    if docker_tests:
        try:
            import docker
            docker.from_env().ping()
        except Exception:
            print(f"\nWARNING: {len(docker_tests)} Docker tests will be skipped!")
            print("Run with Docker available for full test coverage.\n")
```

## Implementation Plan

### Phase 1: Critical (Immediate)
1. Add unit tests for `convert_host_path_to_container()`
2. Add basic Docker path handling integration test

### Phase 2: High Priority (This Sprint)
3. Update CI to require Docker tests or explicitly report skips
4. Add Docker worker job execution integration tests

### Phase 3: Medium Priority (Next Sprint)
5. Enhance mock workers with path validation
6. Add notebook worker file I/O integration tests
7. Improve test skip reporting

## Success Metrics

1. **Path conversion function**: 100% branch coverage
2. **Docker integration tests**: At least 5 tests covering:
   - Environment variable passing
   - Path conversion in container
   - Output file writing
   - Payload data handling
   - Error handling
3. **CI visibility**: Docker test status clearly reported (pass/fail/skip)
4. **No silent skips**: Docker test skips are visible in CI output

## Risk Mitigation

- **Docker unavailable in CI**: Use Docker-in-Docker or container-based CI runners
- **Slow Docker tests**: Mark with `@pytest.mark.slow` and run in separate job
- **Flaky container tests**: Add retry logic and proper cleanup fixtures
- **Cross-platform issues**: Test on Windows, Linux, and macOS CI runners

## Appendix: Files to Create/Modify

### New Files
- `tests/infrastructure/workers/test_path_conversion.py`
- `tests/infrastructure/workers/test_docker_integration.py`
- `tests/workers/notebook/test_notebook_worker_integration.py`

### Modified Files
- `tests/fixtures/mock_workers.py` - Add path validation
- `tests/conftest.py` - Add Docker skip reporting
- `.github/workflows/test.yml` - Require Docker tests in CI
- `pyproject.toml` - Add test markers configuration
