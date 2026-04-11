"""Integration tests for worker lifecycle management.

These tests verify the complete worker lifecycle including:
- Starting and stopping managed workers
- Worker reuse functionality
- Health checking and discovery
- Configuration loading
"""

import tempfile
import time
from importlib.util import find_spec
from pathlib import Path

import pytest

from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.config_loader import load_worker_config
from clm.infrastructure.workers.discovery import DiscoveredWorker, WorkerDiscovery
from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
from clm.infrastructure.workers.worker_executor import WorkerConfig


def _wait_for_healthy_workers(
    db_path: Path,
    expected_count: int,
    *,
    worker_type: str | None = None,
    timeout: float = 15.0,
    interval: float = 0.1,
) -> list[DiscoveredWorker]:
    """Poll ``WorkerDiscovery`` until at least *expected_count* workers are healthy.

    Replaces the ``time.sleep(2)`` "give workers time to register" idiom,
    which is a classic flake pattern under pytest-xdist. Workers are
    pre-registered with status ``created`` and transition to ``idle``
    asynchronously when their subprocess is ready — the duration of that
    transition is non-deterministic under CPU contention.

    A polling wait is both faster (returns as soon as workers are ready)
    and more robust (tolerates slow scheduling).
    """
    deadline = time.monotonic() + timeout
    last: list[DiscoveredWorker] = []
    while True:
        discovery = WorkerDiscovery(db_path)
        try:
            discovered = discovery.discover_workers(worker_type=worker_type)
        finally:
            discovery.close()
        healthy = [w for w in discovered if w.is_healthy]
        last = discovered
        if len(healthy) >= expected_count:
            return discovered
        if time.monotonic() > deadline:
            statuses = [(w.worker_type, w.status, w.is_healthy) for w in last]
            raise TimeoutError(
                f"Expected {expected_count} healthy workers within {timeout}s "
                f"(worker_type={worker_type}); got {len(healthy)} healthy out of "
                f"{len(last)} discovered: {statuses}"
            )
        time.sleep(interval)


# Check if worker modules are available
def check_worker_module_available(module_name: str) -> bool:
    """Check if a worker module can be imported."""
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


# Check availability of worker modules
NOTEBOOK_WORKER_AVAILABLE = check_worker_module_available("clm.workers.notebook")
DRAWIO_WORKER_AVAILABLE = check_worker_module_available("drawio_converter")
PLANTUML_WORKER_AVAILABLE = check_worker_module_available("plantuml_converter")

# Skip all integration tests if notebook worker is not available
pytestmark = pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE,
    reason="Worker modules not available - these are integration tests requiring full worker setup",
)


@pytest.fixture
def db_path():
    """Create a temporary database.

    Uses a dedicated temp directory for the database file rather than placing
    it directly in the system temp directory. This is important for Docker
    tests where we mount the parent directory - mounting the entire system
    temp directory can cause issues with Docker volume sharing on Windows.
    """
    import gc
    import shutil
    import sqlite3

    # Create a dedicated temp directory for this test's database
    # This ensures clean Docker volume mounts
    temp_dir = Path(tempfile.mkdtemp(prefix="clm-test-db-"))
    path = temp_dir / "test.db"

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
        # Remove the entire temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def workspace_path():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


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

            # Wait for at least one worker to become healthy. A fixed
            # time.sleep(2) is a classic flake under xdist load — workers
            # may take longer than 2s to activate under CPU contention.
            discovered = _wait_for_healthy_workers(db_path, expected_count=1)
            assert any(w.worker_type == "notebook" and w.is_healthy for w in discovered)

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
            # Must wait for the first worker to be healthy before the second
            # call: reuse-detection is driven by count_healthy_workers(), and
            # checking before the worker activates would bypass reuse.
            _wait_for_healthy_workers(db_path, expected_count=1)

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
            _wait_for_healthy_workers(db_path, expected_count=1)

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
            # Wait until both the old and new workers are healthy (count >= 2).
            _wait_for_healthy_workers(db_path, expected_count=2)

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

            # Poll until the worker has become healthy rather than waiting
            # a fixed 2s (flake-prone under xdist load).
            discovered = _wait_for_healthy_workers(db_path, expected_count=1)
            assert any(
                w.worker_type == "notebook" and w.is_healthy and w.status in ("idle", "busy")
                for w in discovered
            )

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

            # Poll until the requested worker count is healthy before
            # running the status-filtered query. Busy workers are fine too
            # (the test just wants activation to have happened), so we
            # wait via _wait_for_healthy_workers and then filter.
            _wait_for_healthy_workers(db_path, expected_count=2, worker_type="notebook")

            # Discover idle workers
            discovery = WorkerDiscovery(db_path)
            try:
                idle_workers = discovery.discover_workers(status_filter=["idle"])
            finally:
                discovery.close()

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

    Note: Docker workers use REST API for job queue communication, which solves
    the SQLite WAL mode incompatibility with Docker volume mounts on Windows.
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

        # Find available Docker image (CI-built test tag or locally-built via clm docker build)
        notebook_image = None
        for tag in [
            "clm-notebook-processor:lite-test",
            "docker.io/mhoelzl/clm-notebook-processor:lite",
            "docker.io/mhoelzl/clm-notebook-processor:latest",
        ]:
            try:
                docker_client.images.get(tag)
                notebook_image = tag
                break
            except docker.errors.ImageNotFound:
                continue

        if not notebook_image:
            pytest.skip("Notebook Docker image not available. Run: clm docker build")

        config.notebook.image = notebook_image

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

            # Wait for Docker worker to become healthy (async startup).
            # Docker containers need longer than direct workers to start and
            # activate, so use a 30s timeout.
            discovered = _wait_for_healthy_workers(db_path, expected_count=1, timeout=30.0)
            assert discovered[0].worker_type == "notebook"
            assert discovered[0].is_healthy

        finally:
            # Stop workers
            manager.stop_managed_workers(workers)
