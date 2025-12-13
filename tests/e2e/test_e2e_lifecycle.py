"""End-to-end tests for worker lifecycle management in course conversion workflows.

These tests verify the complete integration of worker lifecycle management
with course conversion, testing:
- Auto-start and auto-stop of managed workers
- Worker reuse across multiple builds
- Configuration-driven worker management

Test markers:
- @pytest.mark.e2e: All E2E tests (run actual workers and course conversion)
- @pytest.mark.docker: Tests requiring Docker daemon (marked separately)

Run selectively:
- pytest -m "e2e and not docker"     # E2E tests without Docker
- pytest tests/e2e/test_e2e_lifecycle.py  # All lifecycle E2E tests
"""

import logging
import os
import tempfile
from importlib.util import find_spec
from pathlib import Path

import pytest

from clx.infrastructure.backends.sqlite_backend import SqliteBackend
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.config_loader import load_worker_config
from clx.infrastructure.workers.discovery import WorkerDiscovery
from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

logger = logging.getLogger(__name__)


# Check if worker modules are available
def check_worker_module_available(module_name: str) -> bool:
    """Check if a worker module can be imported."""
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


# Check availability of worker modules
NOTEBOOK_WORKER_AVAILABLE = check_worker_module_available("clx.workers.notebook")

# Skip all tests if notebook worker is not available
pytestmark = pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE,
    reason="Notebook worker module not available - these are E2E tests requiring workers",
)


@pytest.fixture
async def db_path_fixture():
    """Create a temporary database for E2E tests."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Cleanup - the caller is responsible for closing any JobQueue/connections
    # before this fixture tears down
    try:
        path.unlink(missing_ok=True)
        for suffix in ["-wal", "-shm"]:
            wal_file = Path(str(path) + suffix)
            wal_file.unlink(missing_ok=True)
    except Exception:
        pass


@pytest.fixture
async def workspace_path_fixture(tmp_path):
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.mark.e2e
@pytest.mark.slow
async def test_e2e_managed_workers_auto_lifecycle(
    e2e_course_1,
    db_path_fixture,
    workspace_path_fixture,
):
    """E2E: Course conversion with auto-start and auto-stop of managed workers.

    This test simulates the behavior of `clx build` with auto-management:
    1. Workers start automatically before course processing
    2. Course is processed
    3. Workers stop automatically after course processing
    """
    course = e2e_course_1

    # Create configuration with auto-start and auto-stop
    cli_overrides = {
        "default_execution_mode": "direct",
        "notebook_workers": 8,  # Use 8 workers for faster parallel processing of multiple notebooks
        "auto_start": True,
        "auto_stop": True,
        "fresh_workers": True,  # Fixed: use "fresh_workers" instead of "reuse_workers": False
    }
    config = load_worker_config(cli_overrides)

    # Create lifecycle manager
    lifecycle_manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
    )

    # Start managed workers
    logger.info("Starting managed workers...")
    started_workers = lifecycle_manager.start_managed_workers()
    # We configured 8 notebook workers + 1 plantuml + 1 drawio = 10 workers
    assert len(started_workers) == 10, (
        "Should start 8 notebook + 1 plantuml + 1 drawio = 10 workers"
    )

    # Verify correct worker types were started
    worker_types = {w.worker_type for w in started_workers}
    assert worker_types == {
        "notebook",
        "plantuml",
        "drawio",
    }, "Should start all needed worker types"

    # Verify we have 8 notebook workers
    notebook_workers = [w for w in started_workers if w.worker_type == "notebook"]
    assert len(notebook_workers) == 8, "Should start 8 notebook workers"

    # Wait for workers to register
    import asyncio

    await asyncio.sleep(2)

    # Verify workers are healthy
    discovery = WorkerDiscovery(db_path_fixture)
    healthy_workers = discovery.discover_workers()
    assert len(healthy_workers) == 10, "All 10 workers should be healthy"

    try:
        # Create backend
        backend = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=120,
        )

        # Process course
        async with backend:
            logger.info("Processing course...")
            await course.process_all(backend)
            logger.info("Course processing completed")

        # Verify notebooks were processed
        notebooks = course.notebooks
        assert len(notebooks) > 0, "Course should have notebooks"

    finally:
        # Stop managed workers (simulating auto_stop)
        logger.info("Stopping managed workers...")
        lifecycle_manager.stop_managed_workers(started_workers)

        # Close database connections in lifecycle manager and discovery
        lifecycle_manager.close()
        discovery.close()

    # Verify workers were stopped and removed from database
    # (graceful shutdown deletes workers from database rather than marking them as dead)
    with WorkerDiscovery(db_path_fixture) as final_discovery:
        all_workers = final_discovery.discover_workers()
        assert len(all_workers) == 0, (
            "All workers should be removed from database after graceful shutdown"
        )


@pytest.mark.e2e
@pytest.mark.slow
async def test_e2e_managed_workers_reuse_across_builds(
    e2e_course_1,
    e2e_course_2,
    db_path_fixture,
    workspace_path_fixture,
):
    """E2E: Multiple course builds reusing the same workers.

    This test simulates multiple `clx build` invocations with worker reuse:
    1. First build: Workers start
    2. Second build: Workers are reused (not restarted)
    3. Workers stop after final build
    """
    course1 = e2e_course_1
    course2 = e2e_course_2

    # Create configuration with worker reuse
    cli_overrides = {
        "default_execution_mode": "direct",
        "notebook_count": 8,  # Use 8 workers for faster parallel processing of multiple notebooks
        "plantuml_count": 1,  # Need plantuml worker for test data
        "drawio_count": 1,  # Need drawio worker for test data
        "auto_start": True,
        "auto_stop": False,  # Don't auto-stop between builds
        "reuse_workers": True,
    }
    config = load_worker_config(cli_overrides)

    # First build
    logger.info("=== First build ===")
    lifecycle_manager1 = WorkerLifecycleManager(
        config=config,
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
    )

    started_workers1 = lifecycle_manager1.start_managed_workers()
    worker1_ids = [w.db_worker_id for w in started_workers1]

    import asyncio

    await asyncio.sleep(2)

    # Initialize variables for cleanup in finally block
    lifecycle_manager2 = None
    started_workers2 = None

    try:
        backend1 = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=120,
        )

        async with backend1:
            await course1.process_all(backend1)

        # Second build (reusing workers)
        logger.info("=== Second build (reusing workers) ===")
        lifecycle_manager2 = WorkerLifecycleManager(
            config=config,
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
        )

        started_workers2 = lifecycle_manager2.start_managed_workers()
        worker2_ids = [w.db_worker_id for w in started_workers2]

        # Should reuse same workers (same IDs, order may differ)
        assert set(worker1_ids) == set(worker2_ids), "Should reuse existing workers (same IDs)"
        assert len(worker1_ids) == len(worker2_ids), "Should reuse same number of workers"

        backend2 = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=120,
        )

        async with backend2:
            await course2.process_all(backend2)

    finally:
        # Stop workers after all builds
        if lifecycle_manager2 is not None and started_workers2 is not None:
            lifecycle_manager2.stop_managed_workers(started_workers2)
            lifecycle_manager2.close()
        # Close lifecycle_manager1 database connections
        lifecycle_manager1.close()


@pytest.mark.e2e
async def test_e2e_worker_health_monitoring_during_build(
    e2e_course_1,
    db_path_fixture,
    workspace_path_fixture,
):
    """E2E: Worker health monitoring during course conversion.

    This test verifies that workers remain healthy throughout
    the course conversion process.
    """
    course = e2e_course_1

    # Create configuration
    cli_overrides = {
        "default_execution_mode": "direct",
        "notebook_count": 8,  # Use 8 workers for faster parallel processing of multiple notebooks
        "plantuml_count": 1,  # Need plantuml worker for test data
        "drawio_count": 1,  # Need drawio worker for test data
        "auto_start": True,
        "auto_stop": True,
    }
    config = load_worker_config(cli_overrides)

    # Create lifecycle manager
    lifecycle_manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
    )

    # Start managed workers
    started_workers = lifecycle_manager.start_managed_workers()

    import asyncio

    await asyncio.sleep(2)

    discovery = WorkerDiscovery(db_path_fixture)

    try:
        # Verify workers are healthy before processing
        healthy_workers = discovery.discover_workers()
        assert len(healthy_workers) == 10, (
            "All 10 workers should be healthy initially (8 notebook + 1 plantuml + 1 drawio)"
        )

        # Process course
        # Get timeout from environment variable (default: 120s for tests with workers, increased to 600s in CI)
        timeout = float(os.environ.get("CLX_E2E_TIMEOUT", "120"))
        if timeout <= 0:
            timeout = 1200.0  # Fall back to backend default (20 minutes)

        backend = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=timeout,
        )

        async with backend:
            await course.process_all(backend)

        # Verify workers are still healthy after processing
        healthy_workers = discovery.discover_workers()
        assert len(healthy_workers) == 10, (
            "All 10 workers should be healthy after processing (8 notebook + 1 plantuml + 1 drawio)"
        )

    finally:
        # Stop workers
        lifecycle_manager.stop_managed_workers(started_workers)

        # Close database connections
        discovery.close()
        lifecycle_manager.close()


@pytest.mark.e2e
@pytest.mark.docker
async def test_e2e_managed_workers_docker_mode(
    e2e_course_1,
    db_path_fixture,
    workspace_path_fixture,
):
    """E2E: Course conversion with Docker workers (auto-start/stop).

    This test requires Docker daemon to be running and is marked with @pytest.mark.docker.
    Uses e2e_course_1 (full course with notebooks, plantuml, and drawio files) since
    all three Docker images are built locally in CI.
    """
    # Check if Docker is available
    try:
        import docker

        docker_client = docker.from_env()
        docker_client.ping()
    except Exception:
        pytest.skip("Docker daemon not available")

    course = e2e_course_1

    # Create configuration for Docker mode with all three worker types
    cli_overrides = {
        "default_execution_mode": "docker",
        "notebook_count": 2,
        "plantuml_count": 1,
        "drawio_count": 1,
        "auto_start": True,
        "auto_stop": True,
        "reuse_workers": False,
    }
    config = load_worker_config(cli_overrides)

    # Override with locally-built Docker images for testing
    config.notebook.image = "clx-notebook-processor:lite-test"
    config.plantuml.image = "clx-plantuml-converter:test"
    config.drawio.image = "clx-drawio-converter:test"

    # Create lifecycle manager
    lifecycle_manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
    )

    # Start managed workers (notebook, plantuml, and drawio workers)
    logger.info("Starting Docker workers...")
    started_workers = lifecycle_manager.start_managed_workers()
    assert len(started_workers) == 4, (
        "Should start 4 Docker workers (2 notebook + 1 plantuml + 1 drawio)"
    )

    import asyncio

    await asyncio.sleep(3)  # Docker containers take longer to start

    # Verify workers are healthy
    discovery = WorkerDiscovery(db_path_fixture)
    healthy_workers = discovery.discover_workers()
    assert len(healthy_workers) == 4, "All 4 Docker workers should be healthy"

    try:
        # Create backend
        backend = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=180,  # Docker may be slower
        )

        # Process course
        async with backend:
            logger.info("Processing course with Docker workers...")
            await course.process_all(backend)
            logger.info("Course processing completed")

    finally:
        # Stop Docker workers
        logger.info("Stopping Docker workers...")
        lifecycle_manager.stop_managed_workers(started_workers)

        # Close database connections
        discovery.close()
        lifecycle_manager.close()

    # Verify workers were stopped
    with WorkerDiscovery(db_path_fixture) as final_discovery:
        healthy_workers = final_discovery.discover_workers()
        assert len(healthy_workers) == 0, "All Docker workers should be stopped"
