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

        assert call_args[0][0] == "test-image:latest"
        assert call_args[1]["name"] == "clx-test-worker-0"
        assert call_args[1]["detach"] is True
        assert call_args[1]["mem_limit"] == "2g"
        assert call_args[1]["network"] == "custom-network"
        assert call_args[1]["environment"]["WORKER_TYPE"] == "test"
        assert call_args[1]["environment"]["USE_SQLITE_QUEUE"] == "true"


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

        # Mock worker registration (thread-safe with correct worker types)
        worker_id_counter = [1]
        counter_lock = threading.Lock()

        def mock_wait(executor_id, timeout=10):
            # Get worker type from container mapping
            worker_type = container_to_type.get(executor_id, "unknown")

            with counter_lock:
                conn = manager.job_queue._get_conn()
                conn.execute(
                    "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                    (worker_type, executor_id, "idle"),
                )
                conn.commit()
                worker_id = worker_id_counter[0]
                worker_id_counter[0] += 1
            return worker_id

        manager._wait_for_worker_registration = mock_wait

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
    """Test that volumes are mounted with correct paths."""
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

        # Verify volumes
        call_args = mock_client.containers.run.call_args
        volumes = call_args[1]["volumes"]

        # Workspace should be mounted
        assert str(workspace_path.absolute()) in volumes
        assert volumes[str(workspace_path.absolute())]["bind"] == "/workspace"

        # Database directory (not file) should be mounted
        db_dir = str(db_path.parent.absolute())
        assert db_dir in volumes
        assert volumes[db_dir]["bind"] == "/db"


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

        # Mock registration with 0.5s delay to simulate real worker startup
        registration_times = []

        def mock_wait(executor_id, timeout=10):
            import time

            start = time.time()
            time.sleep(0.5)  # Simulate registration delay
            registration_times.append(time.time() - start)

            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ("test", executor_id, "idle"),
            )
            conn.commit()
            return len(registration_times)

        manager._wait_for_worker_registration = mock_wait

        # Measure startup time
        start_time = time.time()
        manager.start_pools()
        duration = time.time() - start_time

        # Verify all workers started
        total_workers = sum(c.count for c in configs)
        assert len(registration_times) == total_workers

        # With parallel execution (max 10 concurrent), 8 workers should complete in ~0.5s
        # (all in one batch). Sequential would take 8 × 0.5s = 4s
        # Allow some overhead for thread management
        assert duration < 2.0, f"Parallel startup took {duration:.2f}s, expected < 2.0s"

        # Verify sequential would have taken much longer
        sequential_time = total_workers * 0.5  # 4.0s
        speedup = sequential_time / duration

        # Should be at least 2x faster (conservative estimate)
        assert speedup >= 2.0, f"Speedup was only {speedup:.1f}x, expected >= 2x"


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
