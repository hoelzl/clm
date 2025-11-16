"""End-to-end tests for worker lifecycle management in course conversion workflows.

These tests verify the complete integration of worker lifecycle management
with course conversion, testing:
- Auto-start and auto-stop of managed workers
- Worker reuse across multiple builds
- Persistent workers (start-services, build, stop-services)
- Configuration-driven worker management

Test markers:
- @pytest.mark.e2e: All E2E tests
- @pytest.mark.integration: Tests requiring actual workers
- @pytest.mark.docker: Tests requiring Docker daemon (marked separately)

Run selectively:
- pytest -m "e2e and not docker"     # E2E tests without Docker
- pytest tests/e2e/test_e2e_lifecycle.py  # All lifecycle E2E tests
"""

import logging
import tempfile
import time
from pathlib import Path
from importlib.util import find_spec

import pytest

from clx.infrastructure.database.schema import init_database
from clx.infrastructure.backends.sqlite_backend import SqliteBackend
from clx.infrastructure.workers.config_loader import load_worker_config
from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
from clx.infrastructure.workers.state_manager import WorkerStateManager
from clx.infrastructure.workers.discovery import WorkerDiscovery


logger = logging.getLogger(__name__)


# Check if worker modules are available
def check_worker_module_available(module_name: str) -> bool:
    """Check if a worker module can be imported."""
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


# Check availability of worker modules
NOTEBOOK_WORKER_AVAILABLE = check_worker_module_available('nb')

# Skip all tests if notebook worker is not available
pytestmark = pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE,
    reason="Notebook worker module not available - these are E2E tests requiring workers"
)


@pytest.fixture
async def db_path_fixture():
    """Create a temporary database for E2E tests."""
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
async def workspace_path_fixture(tmp_path):
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
async def state_file_fixture(tmp_path):
    """Create a temporary state file path."""
    return tmp_path / "worker-state.json"


@pytest.mark.e2e
@pytest.mark.integration
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
        "notebook_workers": 2,  # Fixed: was "notebook_count", should be "notebook_workers"
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
    # We configured 2 notebook workers + 1 plantuml + 1 drawio = 4 workers
    assert len(started_workers) == 4, "Should start 2 notebook + 1 plantuml + 1 drawio = 4 workers"

    # Verify correct worker types were started
    worker_types = {w.worker_type for w in started_workers}
    assert worker_types == {"notebook", "plantuml", "drawio"}, "Should start all needed worker types"

    # Verify we have 2 notebook workers
    notebook_workers = [w for w in started_workers if w.worker_type == "notebook"]
    assert len(notebook_workers) == 2, "Should start 2 notebook workers"

    # Wait for workers to register
    import asyncio
    await asyncio.sleep(2)

    # Verify workers are healthy
    discovery = WorkerDiscovery(db_path_fixture)
    healthy_workers = discovery.discover_workers()
    assert len(healthy_workers) == 4, "All 4 workers should be healthy"

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

    # Verify workers were stopped (should be marked as 'dead', not healthy)
    discovery = WorkerDiscovery(db_path_fixture)
    all_workers = discovery.discover_workers()
    healthy_workers = [w for w in all_workers if w.is_healthy]
    assert len(healthy_workers) == 0, "All workers should be stopped (no healthy workers)"

    # Verify all workers are marked as dead
    dead_workers = [w for w in all_workers if w.status == 'dead']
    assert len(dead_workers) == 4, "All 4 workers should be marked as dead"


@pytest.mark.e2e
@pytest.mark.integration
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
        "notebook_count": 2,
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

        # Should reuse same workers (same IDs)
        assert worker1_ids == worker2_ids, "Should reuse existing workers"

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


@pytest.mark.e2e
@pytest.mark.integration
async def test_e2e_persistent_workers_workflow(
    e2e_course_1,
    e2e_course_2,
    db_path_fixture,
    workspace_path_fixture,
    state_file_fixture,
):
    """E2E: Persistent workers workflow (start-services, build, build, stop-services).

    This test simulates the persistent worker workflow:
    1. clx start-services: Start workers and save state
    2. clx build course1: Process first course
    3. clx build course2: Process second course
    4. clx stop-services: Stop workers and clear state
    """
    course1 = e2e_course_1
    course2 = e2e_course_2

    # Create configuration for persistent workers
    cli_overrides = {
        "default_execution_mode": "direct",
        "notebook_count": 2,
    }
    config = load_worker_config(cli_overrides)

    # Create state manager
    state_manager = WorkerStateManager(state_file_fixture)

    # Step 1: Start persistent workers (simulating `clx start-services`)
    logger.info("=== Starting persistent workers ===")
    lifecycle_manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
    )

    workers = lifecycle_manager.start_persistent_workers()
    # With PlantUML and DrawIO installed, system starts all worker types:
    # 2 notebook + 1 plantuml + 1 drawio = 4 workers
    assert len(workers) >= 2, f"Should start at least 2 notebook workers, got {len(workers)} total workers"

    # Count notebook workers specifically
    notebook_workers = [w for w in workers if w.worker_type == "notebook"]
    assert len(notebook_workers) == 2, f"Should start exactly 2 notebook workers, got {len(notebook_workers)}"

    # Save state
    state_manager.save_worker_state(
        workers=workers,
        db_path=db_path_fixture,
    )

    import asyncio
    await asyncio.sleep(2)

    # Verify state file exists
    assert state_file_fixture.exists(), "State file should exist"

    try:
        # Step 2: Process first course (simulating `clx build course1`)
        logger.info("=== Building course 1 ===")
        backend1 = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=120,
        )

        async with backend1:
            await course1.process_all(backend1)

        # Verify workers are still running
        discovery = WorkerDiscovery(db_path_fixture)
        healthy_workers = discovery.discover_workers()
        assert len(healthy_workers) >= 2, f"At least 2 workers should still be running, found {len(healthy_workers)}"

        # Step 3: Process second course (simulating `clx build course2`)
        logger.info("=== Building course 2 ===")
        backend2 = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=120,
        )

        async with backend2:
            await course2.process_all(backend2)

        # Verify workers are still running
        healthy_workers = discovery.discover_workers()
        assert len(healthy_workers) >= 2, f"At least 2 workers should still be running, found {len(healthy_workers)}"

    finally:
        # Step 4: Stop persistent workers (simulating `clx stop-services`)
        logger.info("=== Stopping persistent workers ===")
        state = state_manager.load_worker_state()
        assert state is not None, "State should be loadable"

        lifecycle_manager.stop_persistent_workers(state.workers)
        state_manager.clear_worker_state()

        # Verify state file was cleared
        assert not state_file_fixture.exists(), "State file should be cleared"


@pytest.mark.e2e
@pytest.mark.integration
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
        "notebook_count": 2,
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
        # With PlantUML and DrawIO installed: 2 notebook + 1 plantuml + 1 drawio = 4 workers
        assert len(healthy_workers) >= 2, \
            f"At least 2 workers should be healthy initially, found {len(healthy_workers)}"

        # Process course
        backend = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=120,
        )

        async with backend:
            await course.process_all(backend)

        # Verify workers are still healthy after processing
        healthy_workers = discovery.discover_workers()
        assert len(healthy_workers) >= 2, \
            f"At least 2 workers should be healthy after processing, found {len(healthy_workers)}"

    finally:
        # Stop workers
        lifecycle_manager.stop_managed_workers(started_workers)


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.docker
async def test_e2e_managed_workers_docker_mode(
    e2e_course_1,
    db_path_fixture,
    workspace_path_fixture,
):
    """E2E: Course conversion with Docker workers (auto-start/stop).

    This test requires Docker daemon to be running and is marked with @pytest.mark.docker.
    """
    # Check if Docker is available
    try:
        import docker
        docker_client = docker.from_env()
        docker_client.ping()
    except Exception:
        pytest.skip("Docker daemon not available")

    course = e2e_course_1

    # Create configuration for Docker mode
    cli_overrides = {
        "default_execution_mode": "docker",
        "notebook_count": 2,
        "auto_start": True,
        "auto_stop": True,
        "reuse_workers": False,
    }
    config = load_worker_config(cli_overrides)

    # Override with Docker image
    config.notebook.image = "mhoelzl/clx-notebook-processor:0.3.0"

    # Create lifecycle manager
    lifecycle_manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
    )

    # Start managed workers
    logger.info("Starting Docker workers...")
    started_workers = lifecycle_manager.start_managed_workers()
    assert len(started_workers) == 2, "Should start 2 Docker workers"

    import asyncio
    await asyncio.sleep(3)  # Docker containers take longer to start

    # Verify workers are healthy
    discovery = WorkerDiscovery(db_path_fixture)
    healthy_workers = discovery.discover_workers()
    assert len(healthy_workers) == 2, "Both Docker workers should be healthy"

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

    # Verify workers were stopped
    healthy_workers = discovery.discover_workers()
    assert len(healthy_workers) == 0, "All Docker workers should be stopped"


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.docker
async def test_e2e_persistent_workers_docker_workflow(
    e2e_course_1,
    db_path_fixture,
    workspace_path_fixture,
    state_file_fixture,
):
    """E2E: Persistent Docker workers workflow.

    This test requires Docker daemon and is marked with @pytest.mark.docker.
    """
    # Check if Docker is available
    try:
        import docker
        docker_client = docker.from_env()
        docker_client.ping()
    except Exception:
        pytest.skip("Docker daemon not available")

    course = e2e_course_1

    # Create configuration for Docker mode
    cli_overrides = {
        "default_execution_mode": "docker",
        "notebook_count": 2,
    }
    config = load_worker_config(cli_overrides)

    # Override with Docker image
    config.notebook.image = "mhoelzl/clx-notebook-processor:0.3.0"

    # Create lifecycle manager
    lifecycle_manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
    )

    # Create state manager
    state_manager = WorkerStateManager(state_file_fixture)

    # Start persistent Docker workers
    logger.info("Starting persistent Docker workers...")
    workers = lifecycle_manager.start_persistent_workers()
    assert len(workers) == 2, "Should start 2 Docker workers"

    # Save state
    state_manager.save_worker_state(
        workers=workers,
        db_path=db_path_fixture,
    )

    import asyncio
    await asyncio.sleep(3)

    try:
        # Process course
        logger.info("Building course with persistent Docker workers...")
        backend = SqliteBackend(
            db_path=db_path_fixture,
            workspace_path=workspace_path_fixture,
            ignore_db=True,
            max_wait_for_completion_duration=180,
        )

        async with backend:
            await course.process_all(backend)

        # Verify workers are still running
        discovery = WorkerDiscovery(db_path_fixture)
        healthy_workers = discovery.discover_workers()
        assert len(healthy_workers) == 2, "Docker workers should still be running"

    finally:
        # Stop persistent Docker workers
        logger.info("Stopping persistent Docker workers...")
        state = state_manager.load_worker_state()
        lifecycle_manager.stop_persistent_workers(state.workers)
        state_manager.clear_worker_state()
