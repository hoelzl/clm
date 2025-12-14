"""Tests for worker_executor module."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.worker_executor import (
    DirectWorkerExecutor,
    DockerWorkerExecutor,
    WorkerConfig,
    WorkerExecutor,
)


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Close all connections and clean up WAL files on Windows
    import gc
    import sqlite3

    gc.collect()  # Force garbage collection to close any lingering connections

    # Force SQLite to checkpoint and close WAL files
    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass

    # Remove database files
    try:
        path.unlink(missing_ok=True)
        # Also remove WAL and SHM files if they exist
        for suffix in ["-wal", "-shm"]:
            wal_file = Path(str(path) + suffix)
            wal_file.unlink(missing_ok=True)
    except PermissionError:
        # On Windows, if file is still locked, wait a moment and retry
        time.sleep(0.1)
        try:
            path.unlink(missing_ok=True)
            for suffix in ["-wal", "-shm"]:
                wal_file = Path(str(path) + suffix)
                wal_file.unlink(missing_ok=True)
        except Exception:
            pass  # Best effort cleanup


@pytest.fixture
def workspace_path():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestWorkerConfig:
    """Tests for WorkerConfig dataclass."""

    def test_docker_config_creation(self):
        """Test creating Docker worker config."""
        config = WorkerConfig(
            worker_type="notebook",
            count=2,
            execution_mode="docker",
            image="notebook-processor:latest",
            memory_limit="1g",
        )

        assert config.worker_type == "notebook"
        assert config.count == 2
        assert config.execution_mode == "docker"
        assert config.image == "notebook-processor:latest"
        assert config.memory_limit == "1g"
        assert config.max_job_time == 600  # default

    def test_direct_config_creation(self):
        """Test creating direct worker config."""
        config = WorkerConfig(worker_type="notebook", count=2, execution_mode="direct")

        assert config.worker_type == "notebook"
        assert config.count == 2
        assert config.execution_mode == "direct"
        assert config.image is None
        assert config.memory_limit == "1g"  # default, though not used in direct mode

    def test_docker_config_requires_image(self):
        """Test that Docker mode requires image."""
        with pytest.raises(ValueError, match="Docker execution mode requires 'image'"):
            WorkerConfig(
                worker_type="notebook",
                count=2,
                execution_mode="docker",
                # Missing image
            )

    def test_invalid_execution_mode(self):
        """Test that invalid execution mode raises error."""
        with pytest.raises(ValueError, match="Invalid execution_mode"):
            WorkerConfig(worker_type="notebook", count=2, execution_mode="invalid")

    def test_default_execution_mode(self):
        """Test that default execution mode is docker."""
        config = WorkerConfig(worker_type="notebook", count=2, image="test:latest")
        assert config.execution_mode == "docker"


class TestDirectWorkerExecutor:
    """Tests for DirectWorkerExecutor."""

    def test_initialization(self, db_path, workspace_path):
        """Test DirectWorkerExecutor initialization."""
        executor = DirectWorkerExecutor(
            db_path=db_path, workspace_path=workspace_path, log_level="INFO"
        )

        assert executor.db_path == db_path
        assert executor.workspace_path == workspace_path
        assert executor.log_level == "INFO"
        assert len(executor.processes) == 0
        assert len(executor.worker_info) == 0

    @patch("subprocess.Popen")
    def test_start_worker(self, mock_popen, db_path, workspace_path):
        """Test starting a direct worker."""
        # Mock process
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")

        worker_id = executor.start_worker("notebook", 0, config)

        # Verify worker_id was generated and returned
        assert worker_id is not None
        assert worker_id.startswith("direct-notebook-0-")

        # Verify subprocess.Popen was called
        assert mock_popen.called
        call_args = mock_popen.call_args

        # Check command
        cmd = call_args[0][0]
        assert cmd[-2:] == ["-m", "clx.workers.notebook"]

        # Check environment variables
        env = call_args[1]["env"]
        assert env["WORKER_TYPE"] == "notebook"
        assert env["WORKER_ID"] == worker_id
        assert env["DB_PATH"] == str(db_path.absolute())
        assert env["WORKSPACE_PATH"] == str(workspace_path.absolute())
        assert env["USE_SQLITE_QUEUE"] == "true"

        # Verify worker is tracked
        assert worker_id in executor.processes
        assert worker_id in executor.worker_info
        assert executor.worker_info[worker_id]["type"] == "notebook"
        assert executor.worker_info[worker_id]["pid"] == 12345

    @patch("subprocess.Popen")
    def test_start_worker_unknown_type(self, mock_popen, db_path, workspace_path):
        """Test that starting unknown worker type fails."""
        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        config = WorkerConfig(worker_type="unknown", count=1, execution_mode="direct")

        worker_id = executor.start_worker("unknown", 0, config)

        # Should return None for unknown worker type
        assert worker_id is None
        assert not mock_popen.called

    @patch("subprocess.Popen")
    def test_stop_worker(self, mock_popen, db_path, workspace_path):
        """Test stopping a direct worker."""
        # Mock process
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None  # Process is running
        mock_popen.return_value = mock_process

        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")

        # Start worker
        worker_id = executor.start_worker("notebook", 0, config)
        assert worker_id in executor.processes

        # Stop worker (use create=True for Windows compatibility where os.killpg doesn't exist)
        with (
            patch("os.killpg", create=True) as mock_killpg,
            patch("os.getpgid", create=True) as mock_getpgid,
        ):
            mock_getpgid.return_value = 12345
            result = executor.stop_worker(worker_id)

        assert result is True
        assert worker_id not in executor.processes
        assert worker_id not in executor.worker_info

        # Verify SIGTERM was sent
        if os.name != "nt":  # Unix only
            assert mock_killpg.called

        # Verify process.wait was called
        assert mock_process.wait.called

    @patch("subprocess.Popen")
    def test_stop_worker_not_found(self, mock_popen, db_path, workspace_path):
        """Test stopping a non-existent worker."""
        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        result = executor.stop_worker("nonexistent-worker-id")
        assert result is False

    @patch("subprocess.Popen")
    def test_is_worker_running(self, mock_popen, db_path, workspace_path):
        """Test checking if worker is running."""
        # Mock process
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None  # Process is running
        mock_popen.return_value = mock_process

        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")

        # Start worker
        worker_id = executor.start_worker("notebook", 0, config)

        # Check if running
        assert executor.is_worker_running(worker_id) is True

        # Simulate process termination
        mock_process.poll.return_value = 0  # Process exited

        # Check again
        assert executor.is_worker_running(worker_id) is False

    def test_is_worker_running_nonexistent(self, db_path, workspace_path):
        """Test checking non-existent worker."""
        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        assert executor.is_worker_running("nonexistent-id") is False

    @patch("subprocess.Popen")
    def test_get_worker_stats(self, mock_popen, db_path, workspace_path):
        """Test getting worker statistics."""
        # Mock process
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None  # Process is running
        mock_popen.return_value = mock_process

        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")

        # Start worker
        worker_id = executor.start_worker("notebook", 0, config)

        # Get stats
        stats = executor.get_worker_stats(worker_id)

        assert stats is not None
        assert "cpu_percent" in stats
        assert "memory_mb" in stats
        assert "is_alive" in stats
        assert "pid" in stats
        assert stats["is_alive"] is True
        assert stats["pid"] == 12345

    def test_get_worker_stats_nonexistent(self, db_path, workspace_path):
        """Test getting stats for non-existent worker."""
        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        stats = executor.get_worker_stats("nonexistent-id")
        assert stats is None

    @patch("subprocess.Popen")
    def test_cleanup(self, mock_popen, db_path, workspace_path):
        """Test cleaning up all workers."""
        # Mock processes
        mock_processes = []
        for i in range(3):
            mock_process = MagicMock()
            mock_process.pid = 12345 + i
            mock_process.poll.return_value = None  # Process is running
            mock_processes.append(mock_process)

        mock_popen.side_effect = mock_processes

        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        config = WorkerConfig(worker_type="notebook", count=3, execution_mode="direct")

        # Start multiple workers
        worker_ids = []
        for i in range(3):
            worker_id = executor.start_worker("notebook", i, config)
            worker_ids.append(worker_id)

        assert len(executor.processes) == 3

        # Cleanup all (use create=True for Windows compatibility where os.killpg doesn't exist)
        with patch("os.killpg", create=True), patch("os.getpgid", create=True):
            executor.cleanup()

        # All workers should be stopped
        assert len(executor.processes) == 0
        assert len(executor.worker_info) == 0

    @patch("subprocess.Popen")
    def test_stop_worker_timeout(self, mock_popen, db_path, workspace_path):
        """Test stopping worker that doesn't respond to SIGTERM."""
        # Mock process that hangs on termination
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None  # Process is running
        # First call to wait times out, second call succeeds
        mock_process.wait.side_effect = [subprocess.TimeoutExpired("cmd", 10), None]
        mock_popen.return_value = mock_process

        executor = DirectWorkerExecutor(db_path=db_path, workspace_path=workspace_path)

        config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")

        # Start worker
        worker_id = executor.start_worker("notebook", 0, config)

        # Try to stop worker (should handle timeout, use create=True for Windows compatibility)
        with (
            patch("os.killpg", create=True) as mock_killpg,
            patch("os.getpgid", create=True) as mock_getpgid,
            patch("sys.platform", "linux"),
        ):
            mock_getpgid.return_value = 12345
            result = executor.stop_worker(worker_id)

        # Should still return True after force kill
        assert result is True

        # Verify SIGKILL was sent after timeout on Unix
        if sys.platform != "win32":
            # Should have been called twice: SIGTERM then SIGKILL
            assert mock_killpg.call_count == 2


class TestDockerWorkerExecutor:
    """Tests for DockerWorkerExecutor."""

    @patch("docker.DockerClient")
    def test_initialization(self, mock_docker, db_path, workspace_path):
        """Test DockerWorkerExecutor initialization."""
        mock_client = MagicMock()

        executor = DockerWorkerExecutor(
            docker_client=mock_client,
            db_path=db_path,
            workspace_path=workspace_path,
            network_name="test-network",
            log_level="INFO",
        )

        assert executor.docker_client == mock_client
        assert executor.db_path == db_path
        assert executor.workspace_path == workspace_path
        assert executor.network_name == "test-network"
        assert executor.log_level == "INFO"
        assert len(executor.containers) == 0

    @patch("docker.DockerClient")
    @patch("docker.errors.NotFound")
    def test_start_worker(self, mock_not_found, mock_docker, db_path, workspace_path):
        """Test starting a Docker worker."""
        import docker.errors

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_client.containers.run.return_value = mock_container
        # Raise NotFound when checking for existing container
        mock_client.containers.get.side_effect = docker.errors.NotFound("Container not found")

        executor = DockerWorkerExecutor(
            docker_client=mock_client, db_path=db_path, workspace_path=workspace_path
        )

        config = WorkerConfig(
            worker_type="notebook",
            count=1,
            execution_mode="docker",
            image="notebook-processor:latest",
            memory_limit="1g",
        )

        worker_id = executor.start_worker("notebook", 0, config)

        # Verify container ID was returned
        assert worker_id == "abc123def456"

        # Verify docker run was called
        assert mock_client.containers.run.called
        call_args = mock_client.containers.run.call_args

        # Check image (now passed as kwargs since we use **run_kwargs)
        assert call_args.kwargs["image"] == "notebook-processor:latest"

        # Check environment
        env = call_args.kwargs["environment"]
        assert env["WORKER_TYPE"] == "notebook"
        # Workers now use CLX_API_URL for REST API communication instead of direct SQLite
        assert "CLX_API_URL" in env
        assert "host.docker.internal:8765" in env["CLX_API_URL"]

        # When network_name is None (default), network key should not be in kwargs
        assert "network" not in call_args.kwargs

        # Verify container is tracked
        assert worker_id in executor.containers

    @patch("docker.DockerClient")
    @patch("docker.errors.NotFound")
    def test_stop_worker(self, mock_not_found, mock_docker, db_path, workspace_path):
        """Test stopping a Docker worker."""
        import docker.errors

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_container.status = "running"
        mock_client.containers.run.return_value = mock_container
        mock_client.containers.get.side_effect = docker.errors.NotFound("Container not found")

        executor = DockerWorkerExecutor(
            docker_client=mock_client, db_path=db_path, workspace_path=workspace_path
        )

        config = WorkerConfig(
            worker_type="notebook",
            count=1,
            execution_mode="docker",
            image="notebook-processor:latest",
        )

        # Start worker
        worker_id = executor.start_worker("notebook", 0, config)

        # Stop worker
        result = executor.stop_worker(worker_id)

        assert result is True
        assert worker_id not in executor.containers

        # Verify stop and remove were called
        assert mock_container.stop.called
        assert mock_container.remove.called

    @patch("docker.DockerClient")
    @patch("docker.errors.NotFound")
    def test_is_worker_running(self, mock_not_found, mock_docker, db_path, workspace_path):
        """Test checking if Docker worker is running."""
        import docker.errors

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_container.status = "running"
        mock_client.containers.get.side_effect = docker.errors.NotFound("Container not found")
        mock_client.containers.run.return_value = mock_container

        executor = DockerWorkerExecutor(
            docker_client=mock_client, db_path=db_path, workspace_path=workspace_path
        )

        config = WorkerConfig(
            worker_type="notebook",
            count=1,
            execution_mode="docker",
            image="notebook-processor:latest",
        )

        # Start worker
        worker_id = executor.start_worker("notebook", 0, config)

        # Check if running
        assert executor.is_worker_running(worker_id) is True

        # Simulate container stopped
        mock_container.status = "exited"
        assert executor.is_worker_running(worker_id) is False
