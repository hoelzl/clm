"""Tests for pool_manager module."""

import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, call

import pytest

from clx_common.database.schema import init_database
from clx_common.database.job_queue import JobQueue
from clx_common.workers.pool_manager import WorkerPoolManager, WorkerConfig


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Close all connections and clean up WAL files on Windows
    import sqlite3
    import gc
    gc.collect()  # Force garbage collection to close any lingering connections

    # Force SQLite to checkpoint and close WAL files
    try:
        conn = sqlite3.connect(path)
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.close()
    except Exception:
        pass

    # Remove database files
    try:
        path.unlink(missing_ok=True)
        # Also remove WAL and SHM files if they exist
        for suffix in ['-wal', '-shm']:
            wal_file = Path(str(path) + suffix)
            wal_file.unlink(missing_ok=True)
    except PermissionError:
        # On Windows, if file is still locked, wait a moment and retry
        import time
        time.sleep(0.1)
        try:
            path.unlink(missing_ok=True)
            for suffix in ['-wal', '-shm']:
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
            worker_type='notebook',
            image='notebook-processor:latest',
            count=2,
            memory_limit='1g',
            max_job_time=600
        ),
        WorkerConfig(
            worker_type='drawio',
            image='drawio-converter:latest',
            count=1,
            memory_limit='512m',
            max_job_time=300
        )
    ]


def test_worker_config_creation():
    """Test WorkerConfig creation."""
    config = WorkerConfig(
        worker_type='test',
        image='test:latest',
        count=3
    )

    assert config.worker_type == 'test'
    assert config.image == 'test:latest'
    assert config.count == 3
    assert config.memory_limit == '1g'  # default
    assert config.max_job_time == 600  # default


def test_worker_config_custom_values():
    """Test WorkerConfig with custom values."""
    config = WorkerConfig(
        worker_type='test',
        image='test:latest',
        count=2,
        memory_limit='2g',
        max_job_time=1200
    )

    assert config.memory_limit == '2g'
    assert config.max_job_time == 1200


def test_pool_manager_initialization(db_path, workspace_path, worker_configs):
    """Test WorkerPoolManager initialization."""
    with patch('docker.from_env') as mock_docker:
        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=worker_configs,
            network_name='test-network'
        )

        assert manager.db_path == db_path
        assert manager.workspace_path == workspace_path
        assert manager.worker_configs == worker_configs
        assert manager.network_name == 'test-network'
        assert manager.running is True
        assert manager.job_queue is not None
        # Docker client is now lazily initialized
        assert manager.docker_client is None
        # Docker client should not be initialized until needed
        mock_docker.assert_not_called()


def test_pool_manager_start_pools(db_path, workspace_path, worker_configs):
    """Test starting worker pools."""
    with patch('docker.from_env') as mock_docker:
        # Mock Docker client and container
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Create unique mock containers
        container_counter = [0]

        def create_container(*args, **kwargs):
            container = MagicMock()
            container.id = f'container{container_counter[0]}'
            container_counter[0] += 1
            return container

        mock_client.containers.run.side_effect = create_container

        # Mock get to raise NotFound (no existing containers)
        import docker.errors
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=worker_configs
        )

        # Mock the worker registration to simulate successful registration
        original_wait = manager._wait_for_worker_registration
        worker_id_counter = [1]

        def mock_wait(executor_id, timeout=10):
            # Insert a worker record into the database
            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ('test', executor_id, 'idle')
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
    config = WorkerConfig(
        worker_type='test',
        image='test-image:latest',
        count=1,
        memory_limit='2g'
    )

    with patch('docker.from_env') as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        mock_container = MagicMock()
        mock_container.id = 'container456'
        mock_client.containers.run.return_value = mock_container

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config],
            network_name='custom-network'
        )

        manager.start_pools()

        # Verify Docker run was called with correct parameters
        mock_client.containers.run.assert_called_once()
        call_args = mock_client.containers.run.call_args

        assert call_args[0][0] == 'test-image:latest'
        assert call_args[1]['name'] == 'clx-test-worker-0'
        assert call_args[1]['detach'] is True
        assert call_args[1]['mem_limit'] == '2g'
        assert call_args[1]['network'] == 'custom-network'
        assert call_args[1]['environment']['WORKER_TYPE'] == 'test'
        assert call_args[1]['environment']['USE_SQLITE_QUEUE'] == 'true'


def test_pool_manager_stop_pools(db_path, workspace_path, worker_configs):
    """Test stopping worker pools."""
    with patch('docker.from_env') as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Create mock containers
        mock_containers = []
        for i in range(3):
            container = MagicMock()
            container.id = f'container{i}'
            container.status = 'running'
            mock_containers.append(container)

        mock_client.containers.run.side_effect = mock_containers

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=worker_configs
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
    with patch('docker.from_env') as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Create unique mock containers
        container_counter = [0]

        def create_container(*args, **kwargs):
            container = MagicMock()
            container.id = f'container{container_counter[0]}'
            container_counter[0] += 1
            return container

        mock_client.containers.run.side_effect = create_container

        # Mock get to raise NotFound
        import docker.errors
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=worker_configs
        )

        # Mock worker registration
        worker_id_counter = [1]
        def mock_wait(executor_id, timeout=10):
            conn = manager.job_queue._get_conn()
            # Determine worker type from configs
            worker_type = 'notebook' if worker_id_counter[0] <= 2 else 'drawio'
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                (worker_type, executor_id, 'idle')
            )
            conn.commit()
            worker_id = worker_id_counter[0]
            worker_id_counter[0] += 1
            return worker_id

        manager._wait_for_worker_registration = mock_wait

        manager.start_pools()

        stats = manager.get_worker_stats()

        # Should have stats for notebook and drawio workers
        assert 'notebook' in stats
        assert 'drawio' in stats
        assert stats['notebook']['idle'] == 2
        assert stats['drawio']['idle'] == 1


def test_pool_manager_is_heartbeat_stale():
    """Test heartbeat staleness detection."""
    from datetime import datetime, timedelta

    with patch('docker.from_env'):
        manager = WorkerPoolManager(
            db_path=Path('dummy.db'),
            workspace_path=Path('/tmp'),
            worker_configs=[]
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
    with patch('docker.from_env'):
        manager = WorkerPoolManager(
            db_path=Path('dummy.db'),
            workspace_path=Path('/tmp'),
            worker_configs=[]
        )

        # Mock Docker stats
        stats = {
            'cpu_stats': {
                'cpu_usage': {'total_usage': 1000000, 'percpu_usage': [500000, 500000]},
                'system_cpu_usage': 5000000
            },
            'precpu_stats': {
                'cpu_usage': {'total_usage': 500000},
                'system_cpu_usage': 4000000
            }
        }

        cpu_percent = manager._calculate_cpu_percent(stats)

        # With 2 CPUs, delta is 500000 out of 1000000 system delta = 50% per CPU * 2 = 100%
        assert cpu_percent > 0
        assert cpu_percent <= 200  # Max is 2 CPUs * 100%


def test_pool_manager_calculate_cpu_percent_zero_delta():
    """Test CPU percentage calculation with zero delta."""
    with patch('docker.from_env'):
        manager = WorkerPoolManager(
            db_path=Path('dummy.db'),
            workspace_path=Path('/tmp'),
            worker_configs=[]
        )

        # Mock stats with zero system delta
        stats = {
            'cpu_stats': {
                'cpu_usage': {'total_usage': 1000000, 'percpu_usage': [1000000]},
                'system_cpu_usage': 5000000
            },
            'precpu_stats': {
                'cpu_usage': {'total_usage': 1000000},
                'system_cpu_usage': 5000000  # Same as current
            }
        }

        cpu_percent = manager._calculate_cpu_percent(stats)
        assert cpu_percent == 0.0


def test_pool_manager_handles_docker_errors(db_path, workspace_path, worker_configs):
    """Test that pool manager handles Docker errors gracefully."""
    with patch('docker.from_env') as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Mock get to raise NotFound
        import docker.errors
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        # Simulate Docker error on first container, success on others
        mock_client.containers.run.side_effect = [
            Exception("Docker error"),
            MagicMock(id='container1'),
            MagicMock(id='container2')
        ]

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=worker_configs
        )

        # Mock worker registration
        worker_id_counter = [1]
        def mock_wait(executor_id, timeout=10):
            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ('test', executor_id, 'idle')
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
    config = WorkerConfig(
        worker_type='test',
        image='test:latest',
        count=1
    )

    with patch('docker.from_env') as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Mock get to raise NotFound
        import docker.errors
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        mock_container = MagicMock()
        mock_container.id = 'container789'
        mock_client.containers.run.return_value = mock_container

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config]
        )

        # Mock worker registration
        def mock_wait(executor_id, timeout=10):
            conn = manager.job_queue._get_conn()
            conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
                ('test', executor_id, 'idle')
            )
            conn.commit()
            return 1

        manager._wait_for_worker_registration = mock_wait

        manager.start_pools()

        # Verify volumes
        call_args = mock_client.containers.run.call_args
        volumes = call_args[1]['volumes']

        # Workspace should be mounted
        assert str(workspace_path.absolute()) in volumes
        assert volumes[str(workspace_path.absolute())]['bind'] == '/workspace'

        # Database directory (not file) should be mounted
        db_dir = str(db_path.parent.absolute())
        assert db_dir in volumes
        assert volumes[db_dir]['bind'] == '/db'


def test_pool_manager_removes_existing_container(db_path, workspace_path):
    """Test that existing containers are removed before starting."""
    config = WorkerConfig(
        worker_type='test',
        image='test:latest',
        count=1
    )

    with patch('docker.from_env') as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Simulate existing container
        existing_container = MagicMock()
        mock_client.containers.get.return_value = existing_container

        new_container = MagicMock()
        new_container.id = 'new_container'
        mock_client.containers.run.return_value = new_container

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config]
        )

        manager.start_pools()

        # Verify existing container was stopped and removed
        existing_container.stop.assert_called_once()
        existing_container.remove.assert_called_once()


def test_pool_manager_monitoring_not_started_by_default(db_path, workspace_path, worker_configs):
    """Test that monitoring thread is not started automatically."""
    with patch('docker.from_env') as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=worker_configs
        )

        assert manager.monitor_thread is None


def test_pool_manager_start_monitoring(db_path, workspace_path, worker_configs):
    """Test starting health monitoring."""
    with patch('docker.from_env') as mock_docker:
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=worker_configs
        )

        manager.start_monitoring(check_interval=10)

        assert manager.monitor_thread is not None
        assert manager.monitor_thread.is_alive()

        # Stop monitoring
        manager.running = False
        manager.monitor_thread.join(timeout=1)
