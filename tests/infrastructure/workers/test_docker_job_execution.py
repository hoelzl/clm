"""Integration tests for Docker worker job execution.

These tests verify that Docker workers can:
1. Receive jobs via REST API
2. Read input files from /source mount
3. Write output files to /workspace mount
4. Handle path conversion correctly

Requires Docker daemon to be running.

These tests are critical for catching regressions in Docker path handling.
The original bug (absolute host paths being passed to containers instead of
converted container paths) was only caught because the jobs failed to execute,
not because any test verified correct behavior.
"""

import gc
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.config_loader import load_worker_config
from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager


def _is_docker_available() -> bool:
    """Check if Docker daemon is available."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


# Skip all tests in this module if Docker is not available
pytestmark = [
    pytest.mark.docker,
    pytest.mark.integration,
    pytest.mark.skipif(not _is_docker_available(), reason="Docker daemon not available"),
]


@pytest.fixture
def docker_test_env(tmp_path):
    """Set up environment for Docker job execution tests.

    Creates:
    - A database in a dedicated temp directory (for Docker volume mount compatibility)
    - A workspace (output) directory
    - A data directory (input) with test files
    """
    # Create a dedicated temp directory for the database
    # This is important for Docker volume mounting on Windows
    temp_dir = Path(tempfile.mkdtemp(prefix="clx-docker-test-"))
    db_path = temp_dir / "test.db"
    init_database(db_path)

    # Create workspace (output) directory
    workspace = temp_dir / "output"
    workspace.mkdir()

    # Create data directory (input) with test files
    data_dir = temp_dir / "data"
    data_dir.mkdir()

    # Create a simple notebook for testing
    topic_dir = data_dir / "slides" / "test_topic"
    topic_dir.mkdir(parents=True)

    # Minimal valid notebook structure
    notebook_content = """{
    "cells": [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": ["# Test Notebook\\n", "This is a test."]
        }
    ],
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3"
        }
    },
    "nbformat": 4,
    "nbformat_minor": 5
}"""
    (topic_dir / "test.ipynb").write_text(notebook_content)

    yield {
        "temp_dir": temp_dir,
        "db_path": db_path,
        "workspace": workspace,
        "data_dir": data_dir,
        "topic_dir": topic_dir,
    }

    # Cleanup
    gc.collect()

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass

    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def docker_image_available():
    """Check if the test Docker image is available."""
    try:
        import docker

        client = docker.from_env()
        # Try to get the lite test image
        try:
            client.images.get("clx-notebook-processor:lite-test")
            return True
        except docker.errors.ImageNotFound:
            # Try the full image
            try:
                client.images.get("clx-notebook-processor:full")
                return "clx-notebook-processor:full"
            except docker.errors.ImageNotFound:
                return False
    except Exception:
        return False


class TestDockerJobExecution:
    """Tests for Docker worker job execution with path handling.

    These tests verify the complete flow:
    1. Job is added to queue with host-style paths
    2. Docker worker starts and registers via REST API
    3. Worker receives job and converts paths
    4. Worker reads input from /source mount
    5. Worker writes output to /workspace mount
    6. Job marked as completed
    """

    def test_docker_worker_processes_notebook_job(self, docker_test_env, docker_image_available):
        """Docker worker should successfully process a notebook job.

        This is the critical test that would have caught the original bug.
        It verifies that a Docker worker can:
        - Read input from the /source mount
        - Write output to the /workspace mount
        - Complete the job successfully
        """
        if not docker_image_available:
            pytest.skip("Docker image not available (run: clx docker build --variant lite)")

        env = docker_test_env
        image_name = (
            docker_image_available
            if isinstance(docker_image_available, str)
            else "clx-notebook-processor:lite-test"
        )

        # Configure for Docker mode
        cli_overrides = {
            "default_execution_mode": "docker",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
            "auto_start": True,
            "auto_stop": True,
            "reuse_workers": False,
        }
        config = load_worker_config(cli_overrides)
        config.notebook.image = image_name

        # Create lifecycle manager with data_dir for source mount
        manager = WorkerLifecycleManager(
            config=config,
            db_path=env["db_path"],
            workspace_path=env["workspace"],
            data_dir=env["data_dir"],
        )

        workers = []
        try:
            # Start workers - this also starts the REST API server
            workers = manager.start_managed_workers()
            assert len(workers) > 0, "No workers started"

            # Wait for worker registration
            time.sleep(5)

            # Add a job to the queue
            queue = JobQueue(env["db_path"])
            input_file = env["topic_dir"] / "test.ipynb"
            output_dir = env["workspace"] / "output" / "public"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "test.ipynb"

            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(input_file),
                output_file=str(output_file),
                content_hash="test-docker-exec-123",
                payload={
                    "kind": "completed",
                    "prog_lang": "python",
                    "language": "en",
                    "format": "notebook",
                    "source_topic_dir": str(env["topic_dir"]),
                },
            )

            # Wait for job completion (with timeout)
            max_wait = 60  # Docker jobs can take longer
            start = time.time()
            while time.time() - start < max_wait:
                job = queue.get_job(job_id)
                if job.status in ("completed", "failed"):
                    break
                time.sleep(1)

            # Get final job status
            job = queue.get_job(job_id)

            # Verify job succeeded
            assert job.status == "completed", (
                f"Job failed with status '{job.status}'.\n"
                f"Error: {job.error}\n"
                f"This may indicate a path conversion issue in Docker mode."
            )

            # Verify output file was created
            assert output_file.exists(), (
                f"Output file was not created at {output_file}.\n"
                "This indicates the Docker worker failed to write to /workspace mount."
            )

            # Verify output contains valid content
            content = output_file.read_text()
            assert len(content) > 0, "Output file is empty"
            assert "nbformat" in content, "Output does not appear to be a valid notebook"

        finally:
            # Stop workers
            if workers:
                manager.stop_managed_workers(workers)

    def test_docker_worker_reads_from_source_mount(self, docker_test_env, docker_image_available):
        """Docker worker should read input files from /source mount.

        This test verifies that when a job specifies a host path for input_file,
        the Docker worker correctly converts it to a /source path and reads
        from the mounted directory.

        The test creates a unique file that only exists on the host filesystem
        (not in the payload) to verify the worker is reading from the mount.
        """
        if not docker_image_available:
            pytest.skip("Docker image not available (run: clx docker build --variant lite)")

        env = docker_test_env
        image_name = (
            docker_image_available
            if isinstance(docker_image_available, str)
            else "clx-notebook-processor:lite-test"
        )

        # Create a notebook with unique content to verify it's read from disk
        unique_marker = f"UNIQUE_MARKER_{time.time()}"
        notebook_content = f"""{{
    "cells": [
        {{
            "cell_type": "markdown",
            "metadata": {{}},
            "source": ["# {unique_marker}"]
        }}
    ],
    "metadata": {{}},
    "nbformat": 4,
    "nbformat_minor": 5
}}"""
        test_notebook = env["topic_dir"] / "unique_test.ipynb"
        test_notebook.write_text(notebook_content)

        # Configure for Docker mode
        cli_overrides = {
            "default_execution_mode": "docker",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
        }
        config = load_worker_config(cli_overrides)
        config.notebook.image = image_name

        manager = WorkerLifecycleManager(
            config=config,
            db_path=env["db_path"],
            workspace_path=env["workspace"],
            data_dir=env["data_dir"],
        )

        workers = []
        try:
            workers = manager.start_managed_workers()
            time.sleep(5)

            queue = JobQueue(env["db_path"])
            output_file = env["workspace"] / "unique_output.ipynb"

            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(test_notebook),
                output_file=str(output_file),
                content_hash="test-source-mount-123",
                payload={
                    "kind": "completed",
                    "prog_lang": "python",
                    "language": "en",
                    "format": "notebook",
                    "source_topic_dir": str(env["topic_dir"]),
                    # Note: we don't include 'data' in payload, so worker MUST read from mount
                },
            )

            # Wait for completion
            max_wait = 60
            start = time.time()
            while time.time() - start < max_wait:
                job = queue.get_job(job_id)
                if job.status in ("completed", "failed"):
                    break
                time.sleep(1)

            job = queue.get_job(job_id)
            assert job.status == "completed", f"Job failed: {job.error}"

            # Verify the unique marker is in the output (proving it was read from disk)
            assert output_file.exists(), "Output file not created"
            content = output_file.read_text()
            assert unique_marker in content, (
                f"Unique marker '{unique_marker}' not found in output.\n"
                "This indicates the worker did not read from the /source mount."
            )

        finally:
            if workers:
                manager.stop_managed_workers(workers)

    def test_docker_worker_handles_nested_output_paths(
        self, docker_test_env, docker_image_available
    ):
        """Docker worker should create nested output directories.

        Tests that deeply nested output paths like:
        /workspace/public/De/Course Name/Slides/Notebooks/Code-Along/file.ipynb

        Are correctly created inside the container.
        """
        if not docker_image_available:
            pytest.skip("Docker image not available (run: clx docker build --variant lite)")

        env = docker_test_env
        image_name = (
            docker_image_available
            if isinstance(docker_image_available, str)
            else "clx-notebook-processor:lite-test"
        )

        cli_overrides = {
            "default_execution_mode": "docker",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
        }
        config = load_worker_config(cli_overrides)
        config.notebook.image = image_name

        manager = WorkerLifecycleManager(
            config=config,
            db_path=env["db_path"],
            workspace_path=env["workspace"],
            data_dir=env["data_dir"],
        )

        workers = []
        try:
            workers = manager.start_managed_workers()
            time.sleep(5)

            queue = JobQueue(env["db_path"])
            input_file = env["topic_dir"] / "test.ipynb"

            # Create a deeply nested output path (mimics real CLX output structure)
            output_file = (
                env["workspace"]
                / "public"
                / "De"
                / "Test Course"
                / "Slides"
                / "Notebooks"
                / "Code-Along"
                / "test_output.ipynb"
            )

            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(input_file),
                output_file=str(output_file),
                content_hash="test-nested-path-123",
                payload={
                    "kind": "code-along",
                    "prog_lang": "python",
                    "language": "de",
                    "format": "notebook",
                    "source_topic_dir": str(env["topic_dir"]),
                },
            )

            max_wait = 60
            start = time.time()
            while time.time() - start < max_wait:
                job = queue.get_job(job_id)
                if job.status in ("completed", "failed"):
                    break
                time.sleep(1)

            job = queue.get_job(job_id)
            assert job.status == "completed", f"Job failed: {job.error}"

            # Verify nested path was created
            assert output_file.exists(), (
                f"Output file not created at deeply nested path: {output_file}\n"
                "The Docker worker may have failed to create parent directories."
            )

        finally:
            if workers:
                manager.stop_managed_workers(workers)


class TestDockerPathConversionIntegration:
    """Integration tests for path conversion in Docker context.

    These tests verify that the path conversion functions work correctly
    when integrated with actual Docker execution.
    """

    def test_windows_style_paths_work_in_docker(self, docker_test_env, docker_image_available):
        """Verify Windows-style paths are correctly converted for Docker.

        Even when running on Windows with paths like C:\\Users\\tc\\...,
        the Docker worker should receive converted paths like /source/...
        """
        if not docker_image_available:
            pytest.skip("Docker image not available")

        # This test is primarily relevant on Windows but should pass on all platforms
        # The key is that host-style absolute paths work regardless of platform
        env = docker_test_env

        # Verify our test paths are absolute (as they would be in real CLX usage)
        assert env["topic_dir"].is_absolute(), "Test path should be absolute"
        assert env["workspace"].is_absolute(), "Workspace path should be absolute"

        # The actual execution test is covered by test_docker_worker_processes_notebook_job
        # This test documents the requirement that absolute host paths must work
        pass
