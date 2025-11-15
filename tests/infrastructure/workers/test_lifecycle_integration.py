"""Integration tests for worker lifecycle management.

These tests verify the complete worker lifecycle including:
- Starting and stopping managed workers
- Starting and stopping persistent workers
- Worker reuse functionality
- Health checking and discovery
- Configuration loading
"""

import tempfile
import time
from pathlib import Path
from importlib.util import find_spec

import pytest

from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.config_loader import load_worker_config
from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
from clx.infrastructure.workers.state_manager import WorkerStateManager
from clx.infrastructure.workers.discovery import WorkerDiscovery
from clx.infrastructure.workers.worker_executor import WorkerConfig


# Check if worker modules are available
def check_worker_module_available(module_name: str) -> bool:
    """Check if a worker module can be imported."""
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


# Check availability of worker modules
NOTEBOOK_WORKER_AVAILABLE = check_worker_module_available('nb')
DRAWIO_WORKER_AVAILABLE = check_worker_module_available('drawio_converter')
PLANTUML_WORKER_AVAILABLE = check_worker_module_available('plantuml_converter')

# Skip all integration tests if notebook worker is not available
pytestmark = pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE,
    reason="Worker modules not available - these are integration tests requiring full worker setup"
)


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Cleanup
    import sqlite3
    import gc
    gc.collect()

    try:
        conn = sqlite3.connect(path)
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.close()
    except Exception:
        pass

    try:
        path.unlink(missing_ok=True)
        for suffix in ['-wal', '-shm']:
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
def state_file(workspace_path):
    """Create a temporary state file path."""
    return workspace_path / "worker-state.json"


@pytest.mark.integration
class TestManagedWorkerLifecycle:
    """Integration tests for managed worker lifecycle (auto-start/stop)."""

    def test_start_managed_workers_direct(self, db_path, workspace_path):
        """Test starting managed workers in direct mode."""
        # Create configuration (only notebook workers, disable others)
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
            "auto_start": True,
            "auto_stop": True,
            "reuse_workers": False,
        }
        config = load_worker_config(cli_overrides)

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        try:
            # Start managed workers
            workers = manager.start_managed_workers()

            # Verify workers started
            assert len(workers) > 0
            assert workers[0].worker_type == "notebook"
            assert workers[0].execution_mode == "direct"
            assert workers[0].db_worker_id > 0

            # Give workers time to register
            time.sleep(2)

            # Verify workers in database
            discovery = WorkerDiscovery(db_path)
            discovered = discovery.discover_workers()
            assert len(discovered) > 0
            assert discovered[0].worker_type == "notebook"
            assert discovered[0].is_healthy

        finally:
            # Stop workers
            manager.stop_managed_workers(workers)

    def test_start_managed_workers_reuse(self, db_path, workspace_path):
        """Test that managed workers can reuse existing healthy workers."""
        # Create configuration with reuse enabled (only notebook workers)
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
            "auto_start": True,
            "auto_stop": True,
            "reuse_workers": True,
        }
        config = load_worker_config(cli_overrides)

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        try:
            # Start managed workers first time
            workers1 = manager.start_managed_workers()
            time.sleep(2)

            worker1_id = workers1[0].db_worker_id

            # Try to start again (should reuse)
            workers2 = manager.start_managed_workers()

            # Should be same worker
            assert len(workers2) == 1
            assert workers2[0].db_worker_id == worker1_id

        finally:
            # Stop workers
            manager.stop_managed_workers(workers1)

    def test_start_managed_workers_fresh(self, db_path, workspace_path):
        """Test that fresh workers option bypasses reuse."""
        # Create configuration with reuse enabled (only notebook workers)
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
            "auto_start": True,
            "auto_stop": True,
            "reuse_workers": True,
        }
        config = load_worker_config(cli_overrides)

        # Create lifecycle manager
        manager1 = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        try:
            # Start managed workers first time
            workers1 = manager1.start_managed_workers()
            time.sleep(2)

            worker1_id = workers1[0].db_worker_id

            # Create new manager with reuse disabled
            cli_overrides["reuse_workers"] = False
            config_no_reuse = load_worker_config(cli_overrides)

            manager2 = WorkerLifecycleManager(
                config=config_no_reuse,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            # Start fresh workers
            workers2 = manager2.start_managed_workers()
            time.sleep(2)

            # Should be different worker
            assert len(workers2) == 1
            assert workers2[0].db_worker_id != worker1_id

            # Clean up second worker
            manager2.stop_managed_workers(workers2)

        finally:
            # Stop first workers
            manager1.stop_managed_workers(workers1)

    def test_auto_start_behavior(self, db_path, workspace_path):
        """Test that auto_start configuration is respected."""
        # Create configuration with auto_start disabled (only notebook workers)
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
            "auto_start": False,
            "auto_stop": True,
            "reuse_workers": False,
        }
        config = load_worker_config(cli_overrides)

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        # Check should_start_workers returns False
        assert not manager.should_start_workers()


@pytest.mark.integration
class TestPersistentWorkerLifecycle:
    """Integration tests for persistent worker lifecycle (start/stop-services)."""

    def test_start_persistent_workers(self, db_path, workspace_path, state_file):
        """Test starting persistent workers."""
        # Create configuration
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
        }
        config = load_worker_config(cli_overrides)

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        # Create state manager
        state_manager = WorkerStateManager(state_file)

        try:
            # Start persistent workers
            workers = manager.start_persistent_workers()

            # Verify workers started
            assert len(workers) > 0
            assert workers[0].worker_type == "notebook"
            assert workers[0].db_worker_id > 0

            # Save state
            state_manager.save_worker_state(
                workers=workers,
                db_path=db_path,
            )

            time.sleep(2)

            # Verify state file exists
            assert state_file.exists()

            # Load and verify state
            state = state_manager.load_worker_state()
            assert state is not None
            assert len(state.workers) == len(workers)
            assert state.db_path == str(db_path.absolute())

        finally:
            # Stop workers
            manager.stop_persistent_workers(workers)
            state_manager.clear_worker_state()

    def test_persistent_workers_survive_manager_restart(
        self, db_path, workspace_path, state_file
    ):
        """Test that persistent workers survive lifecycle manager restart."""
        # Create configuration
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
        }
        config = load_worker_config(cli_overrides)

        # Create first lifecycle manager
        manager1 = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        # Create state manager
        state_manager = WorkerStateManager(state_file)

        try:
            # Start persistent workers
            workers = manager1.start_persistent_workers()
            worker_id = workers[0].db_worker_id

            # Save state
            state_manager.save_worker_state(
                workers=workers,
                db_path=db_path,
            )

            time.sleep(2)

            # Create new lifecycle manager (simulating restart)
            manager2 = WorkerLifecycleManager(
                config=config,
                db_path=db_path,
                workspace_path=workspace_path,
            )

            # Verify workers still exist
            discovery = WorkerDiscovery(db_path)
            discovered = discovery.discover_workers()
            assert len(discovered) > 0
            assert discovered[0].db_id == worker_id
            assert discovered[0].is_healthy

        finally:
            # Stop workers
            manager1.stop_persistent_workers(workers)
            state_manager.clear_worker_state()

    def test_stop_persistent_workers(self, db_path, workspace_path, state_file):
        """Test stopping persistent workers."""
        # Create configuration
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
        }
        config = load_worker_config(cli_overrides)

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        # Create state manager
        state_manager = WorkerStateManager(state_file)

        # Start persistent workers
        workers = manager.start_persistent_workers()

        # Save state
        state_manager.save_worker_state(
            workers=workers,
            db_path=db_path,
        )

        time.sleep(2)

        # Stop workers
        manager.stop_persistent_workers(workers)

        # Verify workers stopped
        discovery = WorkerDiscovery(db_path)
        discovered = discovery.discover_workers()

        # Workers should be marked as dead or removed
        healthy_workers = [w for w in discovered if w.is_healthy]
        assert len(healthy_workers) == 0

        # Clear state
        state_manager.clear_worker_state()


@pytest.mark.integration
class TestWorkerDiscovery:
    """Integration tests for worker discovery and health checking."""

    def test_discover_healthy_workers(self, db_path, workspace_path):
        """Test discovering healthy workers."""
        # Create configuration
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
        }
        config = load_worker_config(cli_overrides)

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        try:
            # Start workers
            workers = manager.start_managed_workers()
            time.sleep(2)

            # Discover workers
            discovery = WorkerDiscovery(db_path)
            discovered = discovery.discover_workers()

            # Verify discovery
            assert len(discovered) > 0
            assert discovered[0].worker_type == "notebook"
            assert discovered[0].is_healthy
            assert discovered[0].status in ("idle", "busy")

        finally:
            manager.stop_managed_workers(workers)

    def test_discover_workers_by_status(self, db_path, workspace_path):
        """Test discovering workers filtered by status."""
        # Create configuration
        cli_overrides = {
            "default_execution_mode": "direct",
            "notebook_count": 2,
        }
        config = load_worker_config(cli_overrides)

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        try:
            # Start workers
            workers = manager.start_managed_workers()
            time.sleep(2)

            # Discover idle workers
            discovery = WorkerDiscovery(db_path)
            idle_workers = discovery.discover_workers(status_filter=["idle"])

            # Should find idle workers
            assert len(idle_workers) > 0
            assert all(w.status == "idle" for w in idle_workers)

        finally:
            manager.stop_managed_workers(workers)


@pytest.mark.integration
@pytest.mark.docker
class TestDockerWorkerLifecycle:
    """Integration tests for Docker worker lifecycle.

    These tests require Docker daemon to be running and are marked with @pytest.mark.docker.
    """

    def test_start_managed_workers_docker(self, db_path, workspace_path):
        """Test starting managed workers in Docker mode."""
        # Check if Docker is available
        try:
            import docker
            docker_client = docker.from_env()
            docker_client.ping()
        except Exception:
            pytest.skip("Docker daemon not available")

        # Create configuration
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

        # Override with Docker image
        config.notebook.image = "mhoelzl/clx-notebook-processor:0.3.0"

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        try:
            # Start managed workers
            workers = manager.start_managed_workers()

            # Verify workers started
            assert len(workers) > 0
            assert workers[0].worker_type == "notebook"
            assert workers[0].execution_mode == "docker"
            assert not workers[0].executor_id.startswith("direct-")

            # Give workers time to register
            time.sleep(3)

            # Verify workers in database
            discovery = WorkerDiscovery(db_path)
            discovered = discovery.discover_workers()
            assert len(discovered) > 0
            assert discovered[0].worker_type == "notebook"
            assert discovered[0].is_healthy

        finally:
            # Stop workers
            manager.stop_managed_workers(workers)

    def test_start_persistent_workers_docker(self, db_path, workspace_path, state_file):
        """Test starting persistent workers in Docker mode."""
        # Check if Docker is available
        try:
            import docker
            docker_client = docker.from_env()
            docker_client.ping()
        except Exception:
            pytest.skip("Docker daemon not available")

        # Create configuration
        cli_overrides = {
            "default_execution_mode": "docker",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
        }
        config = load_worker_config(cli_overrides)

        # Override with Docker image
        config.notebook.image = "mhoelzl/clx-notebook-processor:0.3.0"

        # Create lifecycle manager
        manager = WorkerLifecycleManager(
            config=config,
            db_path=db_path,
            workspace_path=workspace_path,
        )

        # Create state manager
        state_manager = WorkerStateManager(state_file)

        try:
            # Start persistent workers
            workers = manager.start_persistent_workers()

            # Verify workers started
            assert len(workers) > 0
            assert workers[0].execution_mode == "docker"

            # Save state
            state_manager.save_worker_state(
                workers=workers,
                db_path=db_path,
            )

            time.sleep(3)

            # Verify workers in database
            discovery = WorkerDiscovery(db_path)
            discovered = discovery.discover_workers()
            assert len(discovered) > 0
            assert discovered[0].is_healthy

        finally:
            # Stop workers
            manager.stop_persistent_workers(workers)
            state_manager.clear_worker_state()
