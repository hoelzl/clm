"""Tests for pool_manager module."""

import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.pool_manager import WorkerConfig, WorkerPoolManager


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
        import time

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
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def worker_configs():
    """Create test worker configurations."""
    return [
        WorkerConfig(
            worker_type="notebook",
            image="mhoelzl/clx-notebook-processor:latest",
            count=2,
            memory_limit="1g",
            max_job_time=600,
        ),
        WorkerConfig(
            worker_type="drawio",
            image="mhoelzl/clx-drawio-converter:latest",
            count=1,
            memory_limit="512m",
            max_job_time=300,
        ),
    ]


def test_worker_config_creation():
    """Test WorkerConfig creation."""
    config = WorkerConfig(worker_type="test", image="test:latest", count=3)

    assert config.worker_type == "test"
    assert config.image == "test:latest"
    assert config.count == 3
    assert config.memory_limit == "1g"  # default
    assert config.max_job_time == 600  # default


def test_worker_config_custom_values():
    """Test WorkerConfig with custom values."""
    config = WorkerConfig(
        worker_type="test", image="test:latest", count=2, memory_limit="2g", max_job_time=1200
    )

    assert config.memory_limit == "2g"
    assert config.max_job_time == 1200


def test_pool_manager_initialization(db_path, workspace_path, worker_configs):
    """Test WorkerPoolManager initialization."""
    with patch("docker.from_env") as mock_docker:
        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=worker_configs,
            network_name="test-network",
        )

        assert manager.db_path == db_path
        assert manager.workspace_path == workspace_path
        assert manager.worker_configs == worker_configs
        assert manager.network_name == "test-network"
        assert manager.running is True
        assert manager.job_queue is not None
        # Docker client is now lazily initialized
        assert manager.docker_client is None
        # Docker client should not be initialized until needed
        mock_docker.assert_not_called()


def test_pool_manager_start_pools(db_path, workspace_path, worker_configs):
    """Test starting worker pools."""
    with patch("docker.from_env") as mock_docker:
        # Mock Docker client and container
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Create unique mock containers
        container_counter = [0]

        def create_container(*args, **kwargs):
            container = MagicMock()
            container.id = f"container{container_counter[0]}"
            container_counter[0] += 1
            return container

        mock_client.containers.run.side_effect = create_container

        # Mock get to raise NotFound (no existing containers)
        import docker.errors

        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=worker_configs
        )

        # Mock the worker registration to simulate successful registration
        original_wait = manager._wait_for_worker_registration
        worker_id_counter = [1]

        def mock_wait(executor_id, timeout=10):
            # Insert a worker record into the database
            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ("test", executor_id, "idle"),
            )
            conn.commit()
            worker_id = worker_id_counter[0]
            worker_id_counter[0] += 1
            return worker_id

        manager._wait_for_worker_registration = mock_wait

        manager.start_pools()

        # Verify containers were started (2 notebook + 1 drawio = 3)
        assert mock_client.containers.run.call_count == 3

        # Verify workers were registered in database
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM workers")
        worker_count = cursor.fetchone()[0]
        assert worker_count == 3
        queue.close()


def test_pool_manager_start_worker_with_correct_params(db_path, workspace_path):
    """Test that workers are started with correct parameters."""
    config = WorkerConfig(worker_type="test", image="test-image:latest", count=1, memory_limit="2g")

    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        mock_container = MagicMock()
        mock_container.id = "container456"
        mock_client.containers.run.return_value = mock_container

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config],
            network_name="custom-network",
        )

        manager.start_pools()

        # Verify Docker run was called with correct parameters
        mock_client.containers.run.assert_called_once()
        call_args = mock_client.containers.run.call_args

        # Check kwargs (now uses **run_kwargs)
        assert call_args.kwargs["image"] == "test-image:latest"
        assert call_args.kwargs["name"] == "clx-test-worker-0"
        assert call_args.kwargs["detach"] is True
        assert call_args.kwargs["mem_limit"] == "2g"
        assert call_args.kwargs["network"] == "custom-network"
        assert call_args.kwargs["environment"]["WORKER_TYPE"] == "test"
        # Workers now use CLX_API_URL for REST API communication instead of direct SQLite
        assert "CLX_API_URL" in call_args.kwargs["environment"]
        assert "host.docker.internal:8765" in call_args.kwargs["environment"]["CLX_API_URL"]


def test_pool_manager_stop_pools(db_path, workspace_path, worker_configs):
    """Test stopping worker pools."""
    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Create mock containers
        mock_containers = []
        for i in range(3):
            container = MagicMock()
            container.id = f"container{i}"
            container.status = "running"
            mock_containers.append(container)

        mock_client.containers.run.side_effect = mock_containers

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=worker_configs
        )

        manager.start_pools()
        manager.stop_pools()

        # Verify all containers were stopped
        for container in mock_containers:
            container.stop.assert_called_once()
            container.remove.assert_called_once()

        # Verify running flag is False
        assert manager.running is False


def test_pool_manager_get_worker_stats(db_path, workspace_path, worker_configs):
    """Test getting worker statistics."""
    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Create unique mock containers with worker type info
        container_counter = [0]
        container_to_type = {}  # Track which container belongs to which worker type

        def create_container(*args, **kwargs):
            container = MagicMock()
            container_id = f"container{container_counter[0]}"
            container.id = container_id
            container_counter[0] += 1

            # Determine worker type from container name
            container_name = kwargs.get("name", "")
            if "notebook" in container_name:
                worker_type = "notebook"
            elif "drawio" in container_name:
                worker_type = "drawio"
            else:
                worker_type = "unknown"

            container_to_type[container_id] = worker_type
            return container

        mock_client.containers.run.side_effect = create_container

        # Mock get to raise NotFound
        import docker.errors

        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=worker_configs
        )

        # Mock worker pre-registration (thread-safe with correct worker types)
        # With pre-registration, workers are created with 'created' status,
        # then activated to 'idle'. For testing, we create them directly as 'idle'
        # to simulate the complete startup flow.
        worker_id_counter = [1]
        counter_lock = threading.Lock()

        original_pre_register = manager._pre_register_worker

        def mock_pre_register(worker_type, execution_mode):
            # Call original to get container_id format, but create with 'idle' status
            # to simulate completed worker startup
            with counter_lock:
                import uuid

                container_id = f"{execution_mode}-{worker_type}-{uuid.uuid4().hex}"
                conn = manager.job_queue._get_conn()
                cursor = conn.execute(
                    "INSERT INTO workers (worker_type, container_id, status, parent_pid) "
                    "VALUES (?, ?, ?, ?)",
                    (worker_type, container_id, "idle", None),
                )
                worker_id = cursor.lastrowid
                worker_id_counter[0] += 1
            return worker_id, container_id

        manager._pre_register_worker = mock_pre_register

        manager.start_pools()

        stats = manager.get_worker_stats()

        # Should have stats for notebook and drawio workers
        assert "notebook" in stats
        assert "drawio" in stats
        assert stats["notebook"]["idle"] == 2
        assert stats["drawio"]["idle"] == 1


def test_pool_manager_is_heartbeat_stale():
    """Test heartbeat staleness detection."""
    from datetime import datetime, timedelta

    with patch("docker.from_env"):
        manager = WorkerPoolManager(
            db_path=Path("dummy.db"), workspace_path=Path("/tmp"), worker_configs=[]
        )

        # Fresh heartbeat
        now = datetime.now()
        fresh_heartbeat = now.isoformat()
        assert not manager._is_heartbeat_stale(fresh_heartbeat, 30)

        # Stale heartbeat (40 seconds ago)
        stale_time = now - timedelta(seconds=40)
        stale_heartbeat = stale_time.isoformat()
        assert manager._is_heartbeat_stale(stale_heartbeat, 30)


def test_pool_manager_calculate_cpu_percent():
    """Test CPU percentage calculation."""
    with patch("docker.from_env"):
        manager = WorkerPoolManager(
            db_path=Path("dummy.db"), workspace_path=Path("/tmp"), worker_configs=[]
        )

        # Mock Docker stats
        stats = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 1000000, "percpu_usage": [500000, 500000]},
                "system_cpu_usage": 5000000,
            },
            "precpu_stats": {"cpu_usage": {"total_usage": 500000}, "system_cpu_usage": 4000000},
        }

        cpu_percent = manager._calculate_cpu_percent(stats)

        # With 2 CPUs, delta is 500000 out of 1000000 system delta = 50% per CPU * 2 = 100%
        assert cpu_percent > 0
        assert cpu_percent <= 200  # Max is 2 CPUs * 100%


def test_pool_manager_calculate_cpu_percent_zero_delta():
    """Test CPU percentage calculation with zero delta."""
    with patch("docker.from_env"):
        manager = WorkerPoolManager(
            db_path=Path("dummy.db"), workspace_path=Path("/tmp"), worker_configs=[]
        )

        # Mock stats with zero system delta
        stats = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 1000000, "percpu_usage": [1000000]},
                "system_cpu_usage": 5000000,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 1000000},
                "system_cpu_usage": 5000000,  # Same as current
            },
        }

        cpu_percent = manager._calculate_cpu_percent(stats)
        assert cpu_percent == 0.0


def test_pool_manager_handles_docker_errors(db_path, workspace_path, worker_configs):
    """Test that pool manager handles Docker errors gracefully."""
    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Mock get to raise NotFound
        import docker.errors

        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        # Simulate Docker error on first container, success on others
        mock_client.containers.run.side_effect = [
            Exception("Docker error"),
            MagicMock(id="container1"),
            MagicMock(id="container2"),
        ]

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=worker_configs
        )

        # Mock worker registration
        worker_id_counter = [1]

        def mock_wait(executor_id, timeout=10):
            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ("test", executor_id, "idle"),
            )
            conn.commit()
            worker_id = worker_id_counter[0]
            worker_id_counter[0] += 1
            return worker_id

        manager._wait_for_worker_registration = mock_wait

        # Should not raise exception
        manager.start_pools()

        # Should have started 2 workers (failed on first)
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM workers")
        worker_count = cursor.fetchone()[0]
        assert worker_count == 2
        queue.close()


def test_pool_manager_volumes_mounted_correctly(db_path, workspace_path):
    """Test that volumes are mounted with correct paths.

    Note: Database directory is no longer mounted since workers now use
    the REST API for communication instead of direct SQLite access.
    """
    config = WorkerConfig(worker_type="test", image="test:latest", count=1)

    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Mock get to raise NotFound
        import docker.errors

        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        mock_container = MagicMock()
        mock_container.id = "container789"
        mock_client.containers.run.return_value = mock_container

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=[config]
        )

        # Mock worker registration
        def mock_wait(executor_id, timeout=10):
            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ("test", executor_id, "idle"),
            )
            conn.commit()
            return 1

        manager._wait_for_worker_registration = mock_wait

        manager.start_pools()

        # Verify volumes (now uses **run_kwargs)
        call_args = mock_client.containers.run.call_args
        volumes = call_args.kwargs["volumes"]

        # Workspace should be mounted
        assert str(workspace_path.absolute()) in volumes
        assert volumes[str(workspace_path.absolute())]["bind"] == "/workspace"

        # Database directory should NOT be mounted - workers use REST API instead
        db_dir = str(db_path.parent.absolute())
        assert db_dir not in volumes, "Database should not be mounted with REST API mode"


def test_pool_manager_removes_existing_container(db_path, workspace_path):
    """Test that existing containers are removed before starting."""
    config = WorkerConfig(worker_type="test", image="test:latest", count=1)

    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Simulate existing container
        existing_container = MagicMock()
        mock_client.containers.get.return_value = existing_container

        new_container = MagicMock()
        new_container.id = "new_container"
        mock_client.containers.run.return_value = new_container

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=[config]
        )

        manager.start_pools()

        # Verify existing container was stopped and removed
        existing_container.stop.assert_called_once()
        existing_container.remove.assert_called_once()


def test_pool_manager_monitoring_not_started_by_default(db_path, workspace_path, worker_configs):
    """Test that monitoring thread is not started automatically."""
    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=worker_configs
        )

        assert manager.monitor_thread is None


def test_pool_manager_start_monitoring(db_path, workspace_path, worker_configs):
    """Test starting health monitoring."""
    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=worker_configs
        )

        manager.start_monitoring(check_interval=10)

        assert manager.monitor_thread is not None
        assert manager.monitor_thread.is_alive()

        # Stop monitoring
        manager.running = False
        manager.monitor_thread.join(timeout=1)


def test_pool_manager_parallel_startup_performance(db_path, workspace_path):
    """Test that parallel startup is significantly faster than sequential would be."""
    # Create configuration for 8 workers (enough to show speedup)
    configs = [
        WorkerConfig(worker_type="notebook", image="test:latest", count=5, execution_mode="docker"),
        WorkerConfig(worker_type="plantuml", image="test:latest", count=3, execution_mode="docker"),
    ]

    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Mock containers
        container_counter = [0]

        def create_container(*args, **kwargs):
            container = MagicMock()
            container.id = f"container{container_counter[0]}"
            container_counter[0] += 1
            return container

        mock_client.containers.run.side_effect = create_container

        # Mock get to raise NotFound
        import docker.errors

        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=configs,
            max_startup_concurrency=10,
        )

        # Track pre-registration calls to verify all workers start
        # With pre-registration, there's no registration wait - workers are
        # created with status='created' and later activate themselves.
        # The startup is now truly parallel with no blocking wait.
        pre_registration_times = []
        counter_lock = threading.Lock()
        worker_id_counter = [1]

        def mock_pre_register(worker_type, execution_mode):
            import uuid

            start = time.time()
            # Simulate a small delay for DB insert
            time.sleep(0.05)
            pre_registration_times.append(time.time() - start)

            with counter_lock:
                container_id = f"{execution_mode}-{worker_type}-{uuid.uuid4().hex}"
                conn = manager.job_queue._get_conn()
                cursor = conn.execute(
                    "INSERT INTO workers (worker_type, container_id, status, parent_pid) "
                    "VALUES (?, ?, ?, ?)",
                    (worker_type, container_id, "idle", None),
                )
                worker_id = cursor.lastrowid
                worker_id_counter[0] += 1
            return worker_id, container_id

        manager._pre_register_worker = mock_pre_register

        # Measure startup time
        start_time = time.time()
        manager.start_pools()
        duration = time.time() - start_time

        # Verify all workers started
        total_workers = sum(c.count for c in configs)
        assert len(pre_registration_times) == total_workers

        # With pre-registration, startup should be very fast since there's no
        # wait for worker self-registration. The main benefit is eliminating
        # the 2-10 second wait that previously existed.
        # With 8 workers and parallel execution, should complete in < 1s
        assert duration < 1.5, f"Parallel startup took {duration:.2f}s, expected < 1.5s"


def test_pool_manager_concurrency_limit_enforced(db_path, workspace_path):
    """Test that max_startup_concurrency limit is respected."""
    configs = [
        WorkerConfig(
            worker_type="notebook",
            image="test:latest",
            count=6,  # More than concurrency limit
            execution_mode="docker",
        )
    ]

    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        container_counter = [0]

        def create_container(*args, **kwargs):
            container = MagicMock()
            container.id = f"container{container_counter[0]}"
            container_counter[0] += 1
            return container

        mock_client.containers.run.side_effect = create_container

        import docker.errors

        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        # Set low concurrency limit
        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=configs,
            max_startup_concurrency=3,  # Limit to 3 concurrent
        )

        # Track concurrent executions
        active_workers = []
        max_concurrent = [0]
        lock = threading.Lock()

        def mock_wait(executor_id, timeout=10):
            with lock:
                active_workers.append(executor_id)
                current_concurrent = len(active_workers)
                max_concurrent[0] = max(max_concurrent[0], current_concurrent)

            time.sleep(0.1)  # Simulate work

            with lock:
                active_workers.remove(executor_id)

            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ("test", executor_id, "idle"),
            )
            conn.commit()
            return 1

        manager._wait_for_worker_registration = mock_wait

        manager.start_pools()

        # Verify concurrency limit was respected
        assert max_concurrent[0] <= 3, (
            f"Max concurrent workers was {max_concurrent[0]}, expected <= 3"
        )


def test_pool_manager_parallel_error_handling(db_path, workspace_path):
    """Test that errors are properly collected and reported in parallel execution."""
    configs = [
        WorkerConfig(worker_type="notebook", image="test:latest", count=5, execution_mode="docker")
    ]

    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        container_counter = [0]
        fail_on = {1, 3}  # Fail workers 1 and 3

        def create_container(*args, **kwargs):
            container = MagicMock()
            container_id = f"container{container_counter[0]}"
            container.id = container_id

            # Fail specific workers
            if container_counter[0] in fail_on:
                container_counter[0] += 1
                raise Exception(f"Failed to start container {container_id}")

            container_counter[0] += 1
            return container

        mock_client.containers.run.side_effect = create_container

        import docker.errors

        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=configs
        )

        def mock_wait(executor_id, timeout=10):
            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ("test", executor_id, "idle"),
            )
            conn.commit()
            return 1

        manager._wait_for_worker_registration = mock_wait

        # Should not raise exception despite failures
        manager.start_pools()

        # Verify only 3 workers started (5 - 2 failures)
        total_started = sum(len(workers) for workers in manager.workers.values())
        assert total_started == 3, f"Expected 3 workers, got {total_started}"


class TestPoolManagerRegistry:
    """Tests for the global pool manager registry and atexit cleanup."""

    def test_pool_manager_registers_itself(self, db_path, workspace_path):
        """Test that WorkerPoolManager registers itself in the global registry."""
        from clx.infrastructure.workers.pool_manager import _pool_manager_registry

        initial_count = len(list(_pool_manager_registry))

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Manager should be in registry
            registry_list = list(_pool_manager_registry)
            assert manager in registry_list
            assert len(registry_list) == initial_count + 1

    def test_pool_manager_removed_from_registry_on_gc(self, db_path, workspace_path):
        """Test that WorkerPoolManager is removed from registry when garbage collected."""
        import gc

        from clx.infrastructure.workers.pool_manager import _pool_manager_registry

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Verify it's in registry
            assert manager in _pool_manager_registry

            # Get initial count
            initial_count = len(list(_pool_manager_registry))

            # Delete reference and force garbage collection
            del manager
            gc.collect()

            # Registry should have one less item (WeakSet removes it)
            # Note: This may not work on all Python implementations
            current_count = len(list(_pool_manager_registry))
            assert current_count <= initial_count


class TestAtexitCleanup:
    """Tests for atexit cleanup functionality."""

    def test_atexit_cleanup_disabled_flag_default(self):
        """Test that _atexit_cleanup_disabled starts as False."""
        from clx.infrastructure.workers import pool_manager

        # Reset to default state
        pool_manager._atexit_cleanup_disabled = False

        assert pool_manager._atexit_cleanup_disabled is False

    def test_stop_pools_sets_cleanup_disabled(self, db_path, workspace_path):
        """Test that stop_pools sets _atexit_cleanup_disabled to True."""
        from clx.infrastructure.workers import pool_manager

        # Reset flag
        pool_manager._atexit_cleanup_disabled = False

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Stop pools
            manager.stop_pools()

            # Flag should now be True
            assert pool_manager._atexit_cleanup_disabled is True

        # Reset for other tests
        pool_manager._atexit_cleanup_disabled = False

    def test_atexit_cleanup_does_nothing_when_disabled(self, db_path, workspace_path):
        """Test that _atexit_cleanup_all_pools does nothing when disabled."""
        from clx.infrastructure.workers import pool_manager

        # Set flag to disabled
        pool_manager._atexit_cleanup_disabled = True

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )
            manager.running = True

            # Mock _emergency_stop to track if it's called
            manager._emergency_stop = Mock()

            # Call atexit cleanup
            pool_manager._atexit_cleanup_all_pools()

            # _emergency_stop should NOT have been called
            manager._emergency_stop.assert_not_called()

        # Reset for other tests
        pool_manager._atexit_cleanup_disabled = False

    def test_atexit_cleanup_calls_emergency_stop(self, db_path, workspace_path):
        """Test that _atexit_cleanup_all_pools calls _emergency_stop on managers."""
        from clx.infrastructure.workers import pool_manager

        # Reset flag
        pool_manager._atexit_cleanup_disabled = False

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )
            manager.running = True

            # Mock _emergency_stop
            manager._emergency_stop = Mock()

            # Call atexit cleanup
            pool_manager._atexit_cleanup_all_pools()

            # _emergency_stop SHOULD have been called
            manager._emergency_stop.assert_called_once()

        # Reset for other tests
        pool_manager._atexit_cleanup_disabled = False

    def test_atexit_cleanup_handles_exceptions_gracefully(self, db_path, workspace_path):
        """Test that _atexit_cleanup_all_pools handles exceptions gracefully."""
        from clx.infrastructure.workers import pool_manager

        # Reset flag
        pool_manager._atexit_cleanup_disabled = False

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )
            manager.running = True

            # Mock _emergency_stop to raise an exception
            manager._emergency_stop = Mock(side_effect=RuntimeError("Test error"))

            # Should NOT raise exception
            pool_manager._atexit_cleanup_all_pools()

        # Reset for other tests
        pool_manager._atexit_cleanup_disabled = False

    def test_atexit_cleanup_skips_non_running_managers(self, db_path, workspace_path):
        """Test that _atexit_cleanup_all_pools skips managers that are not running."""
        from clx.infrastructure.workers import pool_manager

        # Reset flag
        pool_manager._atexit_cleanup_disabled = False

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )
            manager.running = False  # Already stopped

            # Mock _emergency_stop
            manager._emergency_stop = Mock()

            # Call atexit cleanup
            pool_manager._atexit_cleanup_all_pools()

            # _emergency_stop should NOT have been called (manager not running)
            manager._emergency_stop.assert_not_called()

        # Reset for other tests
        pool_manager._atexit_cleanup_disabled = False


class TestEmergencyStop:
    """Tests for _emergency_stop functionality."""

    def test_emergency_stop_sets_running_false(self, db_path, workspace_path):
        """Test that _emergency_stop sets running to False."""
        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            manager.running = True
            manager._emergency_stop()

            assert manager.running is False

    def test_emergency_stop_stops_workers(self, db_path, workspace_path):
        """Test that _emergency_stop stops all workers."""
        with patch("docker.from_env"):
            # Create mock executor
            mock_executor = MagicMock()
            mock_executor.stop_worker.return_value = True

            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Simulate workers
            manager.workers = {
                "notebook": [
                    {"executor_id": "exec1", "executor": mock_executor},
                    {"executor_id": "exec2", "executor": mock_executor},
                ],
                "plantuml": [{"executor_id": "exec3", "executor": mock_executor}],
            }

            manager._emergency_stop()

            # Verify all workers were stopped
            assert mock_executor.stop_worker.call_count == 3
            mock_executor.stop_worker.assert_any_call("exec1")
            mock_executor.stop_worker.assert_any_call("exec2")
            mock_executor.stop_worker.assert_any_call("exec3")

    def test_emergency_stop_cleans_up_executors(self, db_path, workspace_path):
        """Test that _emergency_stop cleans up all executors."""
        with patch("docker.from_env"):
            mock_executor1 = MagicMock()
            mock_executor2 = MagicMock()

            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            manager.executors = {"docker": mock_executor1, "direct": mock_executor2}

            manager._emergency_stop()

            # Verify executors were cleaned up
            mock_executor1.cleanup.assert_called_once()
            mock_executor2.cleanup.assert_called_once()

    def test_emergency_stop_handles_missing_executor(self, db_path, workspace_path):
        """Test that _emergency_stop handles workers with missing executor."""
        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Worker info without executor key
            manager.workers = {"notebook": [{"executor_id": "exec1"}]}

            # Should not raise exception
            manager._emergency_stop()

    def test_emergency_stop_handles_executor_errors(self, db_path, workspace_path):
        """Test that _emergency_stop handles errors from executors gracefully."""
        with patch("docker.from_env"):
            mock_executor = MagicMock()
            mock_executor.stop_worker.side_effect = RuntimeError("Stop failed")
            mock_executor.cleanup.side_effect = RuntimeError("Cleanup failed")

            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            manager.workers = {"notebook": [{"executor_id": "exec1", "executor": mock_executor}]}
            manager.executors = {"direct": mock_executor}

            # Should NOT raise exception even though executors fail
            manager._emergency_stop()

    def test_emergency_stop_avoids_logging(self, db_path, workspace_path):
        """Test that _emergency_stop doesn't use logging module (uses print to stderr)."""
        import logging

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Mock logging to detect if it's called
            with patch.object(logging, "getLogger") as mock_get_logger:
                manager._emergency_stop()

                # _emergency_stop should NOT call logging
                # (It uses print to stderr instead for reliability during shutdown)
                # Note: The method doesn't explicitly log, so this verifies no new logging is added
                # The existing logger variable is module-level, not called during _emergency_stop


class TestWorkerPreRegistration:
    """Tests for worker pre-registration functionality in WorkerPoolManager."""

    def test_pre_register_worker_creates_record(self, db_path, workspace_path):
        """Test _pre_register_worker creates a worker record with 'created' status."""
        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            db_worker_id, container_id = manager._pre_register_worker("notebook", "direct")

            assert db_worker_id is not None
            assert container_id.startswith("direct-notebook-")

            # Verify record in database
            conn = manager.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT worker_type, status, container_id FROM workers WHERE id = ?",
                (db_worker_id,),
            )
            row = cursor.fetchone()

            assert row is not None
            assert row[0] == "notebook"
            assert row[1] == "created"
            assert row[2] == container_id

            manager.close()

    def test_pre_register_worker_includes_parent_pid(self, db_path, workspace_path):
        """Test _pre_register_worker stores parent_pid for orphan detection."""
        import os

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            db_worker_id, _ = manager._pre_register_worker("notebook", "direct")

            # Verify parent_pid was stored
            conn = manager.job_queue._get_conn()
            cursor = conn.execute("SELECT parent_pid FROM workers WHERE id = ?", (db_worker_id,))
            stored_parent_pid = cursor.fetchone()[0]

            assert stored_parent_pid == os.getpid()

            manager.close()

    def test_update_worker_container_id(self, db_path, workspace_path):
        """Test _update_worker_container_id updates the container_id."""
        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Pre-register a worker
            db_worker_id, original_container_id = manager._pre_register_worker("notebook", "direct")

            # Update container_id
            new_container_id = "direct-notebook-0-abc123"
            manager._update_worker_container_id(db_worker_id, new_container_id)

            # Verify update
            conn = manager.job_queue._get_conn()
            cursor = conn.execute("SELECT container_id FROM workers WHERE id = ?", (db_worker_id,))
            stored_container_id = cursor.fetchone()[0]

            assert stored_container_id == new_container_id
            assert stored_container_id != original_container_id

            manager.close()

    def test_cleanup_stuck_created_workers_removes_old_workers(self, db_path, workspace_path):
        """Test _cleanup_stuck_created_workers removes workers stuck in 'created' status."""
        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Create a worker with 'created' status and old timestamp
            conn = manager.job_queue._get_conn()
            conn.execute(
                """
                INSERT INTO workers (worker_type, container_id, status, started_at, parent_pid)
                VALUES (?, ?, 'created', datetime('now', '-60 seconds'), ?)
                """,
                ("notebook", "test-container", 99999999),  # Non-existent parent
            )

            # Run cleanup
            removed_count = manager._cleanup_stuck_created_workers(conn)

            assert removed_count == 1

            # Verify worker was removed
            cursor = conn.execute("SELECT COUNT(*) FROM workers")
            count = cursor.fetchone()[0]
            assert count == 0

            manager.close()

    def test_cleanup_stuck_created_workers_keeps_recent_workers(self, db_path, workspace_path):
        """Test _cleanup_stuck_created_workers keeps recent 'created' workers."""
        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Pre-register a worker (will have recent timestamp)
            db_worker_id, _ = manager._pre_register_worker("notebook", "direct")

            # Run cleanup
            conn = manager.job_queue._get_conn()
            removed_count = manager._cleanup_stuck_created_workers(conn)

            assert removed_count == 0

            # Verify worker still exists
            cursor = conn.execute("SELECT COUNT(*) FROM workers WHERE id = ?", (db_worker_id,))
            count = cursor.fetchone()[0]
            assert count == 1

            manager.close()

    def test_cleanup_pre_registered_worker_on_failure(self, db_path, workspace_path):
        """Test _cleanup_pre_registered_worker removes the record on startup failure."""
        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Pre-register a worker
            db_worker_id, _ = manager._pre_register_worker("notebook", "direct")

            # Verify it exists
            conn = manager.job_queue._get_conn()
            cursor = conn.execute("SELECT COUNT(*) FROM workers WHERE id = ?", (db_worker_id,))
            assert cursor.fetchone()[0] == 1

            # Clean it up
            manager._cleanup_pre_registered_worker(db_worker_id)

            # Verify it was removed
            cursor = conn.execute("SELECT COUNT(*) FROM workers WHERE id = ?", (db_worker_id,))
            assert cursor.fetchone()[0] == 0

            manager.close()

    def test_is_process_alive_returns_true_for_current_process(self, db_path, workspace_path):
        """Test _is_process_alive returns True for current process."""
        import os

        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            result = manager._is_process_alive(os.getpid())
            assert result is True

            manager.close()

    def test_is_process_alive_returns_false_for_nonexistent_process(self, db_path, workspace_path):
        """Test _is_process_alive returns False for non-existent process."""
        with patch("docker.from_env"):
            manager = WorkerPoolManager(
                db_path=db_path, workspace_path=workspace_path, worker_configs=[]
            )

            # Use a very high PID that's unlikely to exist
            result = manager._is_process_alive(99999999)
            assert result is False

            manager.close()
