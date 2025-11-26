"""Unit tests for lifecycle_manager module.

This module tests the WorkerLifecycleManager class including:
- Initialization and configuration
- Worker starting and stopping logic
- Worker reuse and discovery
- Configuration adjustment
- Worker info collection
"""

import gc
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clx.infrastructure.config import WorkersManagementConfig
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
from clx.infrastructure.workers.state_manager import WorkerInfo
from clx.infrastructure.workers.worker_executor import WorkerConfig


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Cleanup
    gc.collect()

    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass

    try:
        path.unlink(missing_ok=True)
        for suffix in ["-wal", "-shm"]:
            wal_file = Path(str(path) + suffix)
            wal_file.unlink(missing_ok=True)
    except Exception:
        pass


@pytest.fixture
def workspace_path():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config():
    """Create a mock WorkersManagementConfig."""
    config = MagicMock(spec=WorkersManagementConfig)
    config.auto_start = True
    config.auto_stop = True
    config.reuse_workers = False
    config.network_name = "test-network"

    # Mock worker config for notebook
    notebook_config = WorkerConfig(
        worker_type="notebook",
        execution_mode="direct",
        count=1,
    )
    plantuml_config = WorkerConfig(
        worker_type="plantuml",
        execution_mode="direct",
        count=0,
    )
    drawio_config = WorkerConfig(
        worker_type="drawio",
        execution_mode="direct",
        count=0,
    )

    config.get_worker_config.side_effect = lambda t: {
        "notebook": notebook_config,
        "plantuml": plantuml_config,
        "drawio": drawio_config,
    }[t]

    config.get_all_worker_configs.return_value = [notebook_config]

    return config


class TestWorkerLifecycleManagerInit:
    """Test WorkerLifecycleManager initialization."""

    def test_init_creates_session_id(self, db_path, workspace_path, mock_config):
        """Should generate session ID if not provided."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

        assert manager.session_id is not None
        assert manager.session_id.startswith("session-")

    def test_init_uses_provided_session_id(self, db_path, workspace_path, mock_config):
        """Should use provided session ID."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
                session_id="custom-session",
            )

        assert manager.session_id == "custom-session"

    def test_init_creates_event_logger(self, db_path, workspace_path, mock_config):
        """Should create event logger."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

        assert manager.event_logger is not None

    def test_init_creates_discovery(self, db_path, workspace_path, mock_config):
        """Should create worker discovery."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

        assert manager.discovery is not None

    def test_init_creates_state_manager(self, db_path, workspace_path, mock_config):
        """Should create state manager."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

        assert manager.state_manager is not None

    def test_init_creates_direct_executor(self, db_path, workspace_path, mock_config):
        """Should create direct worker executor."""
        with patch(
            "clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"
        ) as mock_direct:
            WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            mock_direct.assert_called_once()

    def test_init_tries_docker_executor(self, db_path, workspace_path, mock_config):
        """Should try to create Docker executor if Docker available."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            with patch(
                "clx.infrastructure.workers.lifecycle_manager.DockerWorkerExecutor"
            ) as mock_docker:
                with patch("docker.from_env") as mock_docker_env:
                    mock_docker_env.return_value = MagicMock()
                    WorkerLifecycleManager(
                        config=mock_config,
                        db_path=db_path,
                        workspace_path=workspace_path,
                    )

                    mock_docker.assert_called_once()

    def test_init_handles_docker_unavailable(self, db_path, workspace_path, mock_config):
        """Should handle Docker not being available."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            with patch("docker.from_env", side_effect=Exception("Docker not available")):
                # Should not raise
                manager = WorkerLifecycleManager(
                    config=mock_config,
                    db_path=db_path,
                    workspace_path=workspace_path,
                )

                assert manager is not None


class TestShouldStartWorkers:
    """Test should_start_workers method."""

    def test_returns_false_when_auto_start_disabled(self, db_path, workspace_path, mock_config):
        """Should return False when auto_start is disabled."""
        mock_config.auto_start = False

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

        assert manager.should_start_workers() is False

    def test_returns_true_when_reuse_disabled(self, db_path, workspace_path, mock_config):
        """Should return True when reuse is disabled."""
        mock_config.auto_start = True
        mock_config.reuse_workers = False

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

        assert manager.should_start_workers() is True

    def test_returns_true_when_insufficient_workers(self, db_path, workspace_path, mock_config):
        """Should return True when fewer healthy workers than required."""
        mock_config.auto_start = True
        mock_config.reuse_workers = True

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.discovery.count_healthy_workers = MagicMock(return_value=0)

        assert manager.should_start_workers() is True

    def test_returns_false_when_sufficient_workers(self, db_path, workspace_path, mock_config):
        """Should return False when enough healthy workers."""
        mock_config.auto_start = True
        mock_config.reuse_workers = True

        # All worker types have 0 required count except notebook which needs 1
        notebook_config = WorkerConfig(worker_type="notebook", execution_mode="direct", count=1)
        plantuml_config = WorkerConfig(worker_type="plantuml", execution_mode="direct", count=0)
        drawio_config = WorkerConfig(worker_type="drawio", execution_mode="direct", count=0)

        mock_config.get_worker_config.side_effect = lambda t: {
            "notebook": notebook_config,
            "plantuml": plantuml_config,
            "drawio": drawio_config,
        }[t]

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            # 1 healthy worker meets 1 required
            manager.discovery.count_healthy_workers = MagicMock(return_value=1)

        assert manager.should_start_workers() is False


class TestStartManagedWorkers:
    """Test start_managed_workers method."""

    def test_start_managed_workers_creates_pool_manager(self, db_path, workspace_path, mock_config):
        """Should create pool manager when starting workers."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            with patch(
                "clx.infrastructure.workers.lifecycle_manager.WorkerPoolManager"
            ) as mock_pool:
                mock_pool_instance = MagicMock()
                mock_pool_instance.workers = {}
                mock_pool.return_value = mock_pool_instance

                manager = WorkerLifecycleManager(
                    config=mock_config,
                    db_path=db_path,
                    workspace_path=workspace_path,
                )
                manager.start_managed_workers()

                mock_pool.assert_called_once()

    def test_start_managed_workers_starts_pools(self, db_path, workspace_path, mock_config):
        """Should call start_pools on pool manager."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            with patch(
                "clx.infrastructure.workers.lifecycle_manager.WorkerPoolManager"
            ) as mock_pool:
                mock_pool_instance = MagicMock()
                mock_pool_instance.workers = {}
                mock_pool.return_value = mock_pool_instance

                manager = WorkerLifecycleManager(
                    config=mock_config,
                    db_path=db_path,
                    workspace_path=workspace_path,
                )
                manager.start_managed_workers()

                mock_pool_instance.start_pools.assert_called_once()

    def test_start_managed_workers_logs_events(self, db_path, workspace_path, mock_config):
        """Should log pool starting and started events."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            with patch(
                "clx.infrastructure.workers.lifecycle_manager.WorkerPoolManager"
            ) as mock_pool:
                mock_pool_instance = MagicMock()
                mock_pool_instance.workers = {}
                mock_pool.return_value = mock_pool_instance

                manager = WorkerLifecycleManager(
                    config=mock_config,
                    db_path=db_path,
                    workspace_path=workspace_path,
                )
                manager.event_logger = MagicMock()
                manager.start_managed_workers()

                manager.event_logger.log_pool_starting.assert_called_once()
                manager.event_logger.log_pool_started.assert_called_once()

    def test_start_managed_workers_with_reuse_adjusts_configs(
        self, db_path, workspace_path, mock_config
    ):
        """Should adjust configs when reuse is enabled."""
        mock_config.reuse_workers = True

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager._adjust_configs_for_reuse = MagicMock(return_value=[])
            manager._collect_reused_worker_info = MagicMock(return_value=[])

            manager.start_managed_workers()

            manager._adjust_configs_for_reuse.assert_called_once()

    def test_start_managed_workers_returns_reused_info_when_no_workers_needed(
        self, db_path, workspace_path, mock_config
    ):
        """Should return reused worker info when no new workers needed."""
        mock_config.reuse_workers = True

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager._adjust_configs_for_reuse = MagicMock(return_value=[])
            reused_info = [
                WorkerInfo(
                    worker_type="notebook",
                    execution_mode="direct",
                    executor_id="test-1",
                    db_worker_id=1,
                    started_at="2024-01-01T00:00:00",
                    config={},
                )
            ]
            manager._collect_reused_worker_info = MagicMock(return_value=reused_info)

            result = manager.start_managed_workers()

            assert result == reused_info


class TestStopManagedWorkers:
    """Test stop_managed_workers method."""

    def test_stop_returns_early_when_auto_stop_disabled(self, db_path, workspace_path, mock_config):
        """Should return early when auto_stop is disabled."""
        mock_config.auto_stop = False

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = MagicMock()

            workers = [
                WorkerInfo(
                    worker_type="notebook",
                    execution_mode="direct",
                    executor_id="test-1",
                    db_worker_id=1,
                    started_at="2024-01-01T00:00:00",
                    config={},
                )
            ]
            manager.stop_managed_workers(workers)

            # Pool manager should not be called
            manager.pool_manager.stop_pools.assert_not_called()

    def test_stop_returns_early_when_no_workers(self, db_path, workspace_path, mock_config):
        """Should return early when no workers to stop."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = MagicMock()

            manager.stop_managed_workers([])

            manager.pool_manager.stop_pools.assert_not_called()

    def test_stop_returns_early_when_no_pool_manager(self, db_path, workspace_path, mock_config):
        """Should return early when no pool manager."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = None

            workers = [
                WorkerInfo(
                    worker_type="notebook",
                    execution_mode="direct",
                    executor_id="test-1",
                    db_worker_id=1,
                    started_at="2024-01-01T00:00:00",
                    config={},
                )
            ]
            # Should not raise
            manager.stop_managed_workers(workers)

    def test_stop_calls_stop_pools(self, db_path, workspace_path, mock_config):
        """Should call stop_pools on pool manager."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = MagicMock()
            manager.event_logger = MagicMock()

            workers = [
                WorkerInfo(
                    worker_type="notebook",
                    execution_mode="direct",
                    executor_id="test-1",
                    db_worker_id=1,
                    started_at="2024-01-01T00:00:00",
                    config={},
                )
            ]
            manager.stop_managed_workers(workers)

            manager.pool_manager.stop_pools.assert_called_once()

    def test_stop_logs_events(self, db_path, workspace_path, mock_config):
        """Should log pool stopping and stopped events."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = MagicMock()
            manager.event_logger = MagicMock()

            workers = [
                WorkerInfo(
                    worker_type="notebook",
                    execution_mode="direct",
                    executor_id="test-1",
                    db_worker_id=1,
                    started_at="2024-01-01T00:00:00",
                    config={},
                )
            ]
            manager.stop_managed_workers(workers)

            manager.event_logger.log_pool_stopping.assert_called_once()
            manager.event_logger.log_pool_stopped.assert_called_once()

    def test_stop_clears_managed_workers_if_matching(self, db_path, workspace_path, mock_config):
        """Should clear managed_workers if they match stopped workers."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = MagicMock()
            manager.event_logger = MagicMock()

            workers = [
                WorkerInfo(
                    worker_type="notebook",
                    execution_mode="direct",
                    executor_id="test-1",
                    db_worker_id=1,
                    started_at="2024-01-01T00:00:00",
                    config={},
                )
            ]
            manager.managed_workers = workers

            manager.stop_managed_workers(workers)

            assert len(manager.managed_workers) == 0


class TestStopPersistentWorkers:
    """Test stop_persistent_workers method."""

    def test_stop_persistent_calls_stop_pools(self, db_path, workspace_path, mock_config):
        """Should call stop_pools when pool_manager exists."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = MagicMock()

            workers = [
                WorkerInfo(
                    worker_type="notebook",
                    execution_mode="direct",
                    executor_id="test-1",
                    db_worker_id=1,
                    started_at="2024-01-01T00:00:00",
                    config={},
                )
            ]
            manager.stop_persistent_workers(workers)

            manager.pool_manager.stop_pools.assert_called_once()

    def test_stop_persistent_handles_no_pool_manager(self, db_path, workspace_path, mock_config):
        """Should handle when pool_manager is None."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = None

            workers = [
                WorkerInfo(
                    worker_type="notebook",
                    execution_mode="direct",
                    executor_id="test-1",
                    db_worker_id=1,
                    started_at="2024-01-01T00:00:00",
                    config={},
                )
            ]
            # Should not raise
            manager.stop_persistent_workers(workers)


class TestStartPersistentWorkers:
    """Test start_persistent_workers method."""

    def test_start_persistent_creates_pool_manager(self, db_path, workspace_path, mock_config):
        """Should create pool manager when starting persistent workers."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            with patch(
                "clx.infrastructure.workers.lifecycle_manager.WorkerPoolManager"
            ) as mock_pool:
                mock_pool_instance = MagicMock()
                mock_pool_instance.workers = {}
                mock_pool.return_value = mock_pool_instance

                manager = WorkerLifecycleManager(
                    config=mock_config,
                    db_path=db_path,
                    workspace_path=workspace_path,
                )
                manager.start_persistent_workers()

                mock_pool.assert_called_once()

    def test_start_persistent_logs_events(self, db_path, workspace_path, mock_config):
        """Should log pool starting and started events."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            with patch(
                "clx.infrastructure.workers.lifecycle_manager.WorkerPoolManager"
            ) as mock_pool:
                mock_pool_instance = MagicMock()
                mock_pool_instance.workers = {}
                mock_pool.return_value = mock_pool_instance

                manager = WorkerLifecycleManager(
                    config=mock_config,
                    db_path=db_path,
                    workspace_path=workspace_path,
                )
                manager.event_logger = MagicMock()
                manager.start_persistent_workers()

                manager.event_logger.log_pool_starting.assert_called_once()
                manager.event_logger.log_pool_started.assert_called_once()


class TestCleanupAllWorkers:
    """Test cleanup_all_workers method."""

    def test_cleanup_discovers_all_workers(self, db_path, workspace_path, mock_config):
        """Should discover all workers during cleanup."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.discovery = MagicMock()
            manager.discovery.discover_workers.return_value = []

            manager.cleanup_all_workers()

            manager.discovery.discover_workers.assert_called_once()

    def test_cleanup_logs_found_workers(self, db_path, workspace_path, mock_config):
        """Should log information about found workers."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            # Create mock discovered workers
            mock_worker = MagicMock()
            mock_worker.db_id = 1
            mock_worker.worker_type = "notebook"
            mock_worker.status = "idle"

            manager.discovery = MagicMock()
            manager.discovery.discover_workers.return_value = [mock_worker]

            # Should not raise
            manager.cleanup_all_workers()


class TestAdjustConfigsForReuse:
    """Test _adjust_configs_for_reuse method."""

    def test_adjust_reduces_count_based_on_healthy(self, db_path, workspace_path, mock_config):
        """Should reduce count based on healthy workers."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.discovery.count_healthy_workers = MagicMock(return_value=1)

            configs = [WorkerConfig(worker_type="notebook", execution_mode="direct", count=2)]
            result = manager._adjust_configs_for_reuse(configs)

            assert len(result) == 1
            assert result[0].count == 1  # 2 needed - 1 healthy = 1 to start

    def test_adjust_returns_empty_when_all_workers_healthy(
        self, db_path, workspace_path, mock_config
    ):
        """Should return empty list when all workers are healthy."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.discovery.count_healthy_workers = MagicMock(return_value=2)

            configs = [WorkerConfig(worker_type="notebook", execution_mode="direct", count=2)]
            result = manager._adjust_configs_for_reuse(configs)

            assert len(result) == 0

    def test_adjust_handles_more_healthy_than_needed(self, db_path, workspace_path, mock_config):
        """Should handle when healthy count exceeds needed."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.discovery.count_healthy_workers = MagicMock(return_value=5)

            configs = [WorkerConfig(worker_type="notebook", execution_mode="direct", count=2)]
            result = manager._adjust_configs_for_reuse(configs)

            # Should not start any workers
            assert len(result) == 0


class TestCollectWorkerInfo:
    """Test _collect_worker_info method."""

    def test_collect_returns_empty_without_pool_manager(self, db_path, workspace_path, mock_config):
        """Should return empty list when no pool manager."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )
            manager.pool_manager = None

            result = manager._collect_worker_info()

            assert result == []

    def test_collect_returns_worker_info(self, db_path, workspace_path, mock_config):
        """Should collect info from pool manager workers."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            mock_config_obj = MagicMock()
            mock_config_obj.execution_mode = "direct"
            mock_config_obj.image = None
            mock_config_obj.memory_limit = "1g"
            mock_config_obj.max_job_time = 300

            manager.pool_manager = MagicMock()
            manager.pool_manager.workers = {
                "notebook": [
                    {
                        "config": mock_config_obj,
                        "executor_id": "direct-1234",
                        "db_worker_id": 1,
                        "started_at": datetime.now(),
                    }
                ]
            }

            result = manager._collect_worker_info()

            assert len(result) == 1
            assert result[0].worker_type == "notebook"
            assert result[0].execution_mode == "direct"
            assert result[0].db_worker_id == 1


class TestCollectReusedWorkerInfo:
    """Test _collect_reused_worker_info method."""

    def test_collect_reused_returns_healthy_workers(self, db_path, workspace_path, mock_config):
        """Should return info for healthy reused workers."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            # Mock discovered worker
            mock_worker = MagicMock()
            mock_worker.worker_type = "notebook"
            mock_worker.is_docker = False
            mock_worker.executor_id = "direct-1234"
            mock_worker.db_id = 1
            mock_worker.started_at = datetime.now()
            mock_worker.is_healthy = True

            manager.discovery = MagicMock()
            manager.discovery.discover_workers.return_value = [mock_worker]

            result = manager._collect_reused_worker_info()

            assert len(result) == 1
            assert result[0].worker_type == "notebook"
            assert result[0].db_worker_id == 1

    def test_collect_reused_skips_unhealthy(self, db_path, workspace_path, mock_config):
        """Should skip unhealthy workers."""
        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            # Mock unhealthy worker
            mock_worker = MagicMock()
            mock_worker.worker_type = "notebook"
            mock_worker.is_healthy = False

            manager.discovery = MagicMock()
            manager.discovery.discover_workers.return_value = [mock_worker]

            result = manager._collect_reused_worker_info()

            assert len(result) == 0

    def test_collect_reused_respects_count_limit(self, db_path, workspace_path, mock_config):
        """Should limit to config.count workers."""
        # Config only wants 1 notebook worker
        notebook_config = WorkerConfig(worker_type="notebook", execution_mode="direct", count=1)
        mock_config.get_all_worker_configs.return_value = [notebook_config]

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            # Mock multiple healthy workers
            workers = []
            for i in range(3):
                mock_worker = MagicMock()
                mock_worker.worker_type = "notebook"
                mock_worker.is_docker = False
                mock_worker.executor_id = f"direct-{i}"
                mock_worker.db_id = i + 1
                mock_worker.started_at = datetime.now()
                mock_worker.is_healthy = True
                workers.append(mock_worker)

            manager.discovery = MagicMock()
            manager.discovery.discover_workers.return_value = workers

            result = manager._collect_reused_worker_info()

            # Should only return 1 worker (config.count = 1)
            assert len(result) == 1

    def test_collect_reused_skips_zero_count_configs(self, db_path, workspace_path, mock_config):
        """Should skip configs with count=0."""
        zero_config = WorkerConfig(worker_type="notebook", execution_mode="direct", count=0)
        mock_config.get_all_worker_configs.return_value = [zero_config]

        with patch("clx.infrastructure.workers.lifecycle_manager.DirectWorkerExecutor"):
            manager = WorkerLifecycleManager(
                config=mock_config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            manager.discovery = MagicMock()

            result = manager._collect_reused_worker_info()

            # discover_workers should not be called for count=0 config
            manager.discovery.discover_workers.assert_not_called()
            assert len(result) == 0
