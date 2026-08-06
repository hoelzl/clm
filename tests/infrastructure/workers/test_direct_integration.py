"""Integration tests for direct worker execution.

These tests verify that workers can run directly as subprocesses
and process actual jobs end-to-end.
"""

import tempfile
import time
from importlib.util import find_spec
from pathlib import Path

import pytest

from clm.core.messaging.notebook_classes import NotebookPayload
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.pool_manager import WorkerPoolManager
from clm.infrastructure.workers.worker_executor import WorkerConfig

# A minimal jupytext percent-format slide source — CLM's actual notebook input
# format. Deliberately trivial so the kernel round-trip is fast.
NOTEBOOK_SOURCE = """# %% [markdown] lang="en" tags=["slide"]
#
# # Test Notebook

# %% tags=["keep"]
print("Hello, World!")
"""


# A heartbeat far enough in the past that no staleness threshold can call it
# fresh, and unambiguous regardless of the UTC-vs-local skew in the health
# checks (finding C8).
ANCIENT_TIMESTAMP = "1970-01-01 00:00:00"

# Image tags CLM's DrawIO worker may be published under: the CI-built test tag
# first, then the published image. Mirrors ``tests/e2e/test_e2e_lifecycle.py``.
DRAWIO_IMAGE_CANDIDATES = [
    "clm-drawio-converter:test",
    "docker.io/mhoelzl/clm-drawio-converter:latest",
]


def _docker_daemon_available() -> bool:
    """Return True when a Docker daemon answers a ping."""
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


def _find_docker_image(candidates: list[str]) -> str | None:
    """Return the first locally-present image tag from *candidates*."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
    except Exception:
        return None

    for tag in candidates:
        try:
            client.images.get(tag)
            return tag
        except Exception:
            continue
    return None


def _submit_notebook_job(
    job_queue: JobQueue,
    input_file: Path,
    output_file: Path,
    source: str = NOTEBOOK_SOURCE,
) -> int:
    """Submit a notebook job carrying a *valid* ``NotebookPayload``.

    These tests used to hand-roll ``payload={"kernel": "python3", "timeout": 60}``,
    which has not been a valid notebook payload since ``NotebookPayload`` gained
    its required ``kind``/``prog_lang``/``language``/``format`` descriptors. The
    job failed with a ``ValidationError`` — invisible, because the whole module
    was skipped (stale ``find_spec`` guards). Build the real payload object so
    the test cannot drift away from the worker's contract again.
    """
    payload = NotebookPayload(
        data=source,
        input_file=str(input_file),
        input_file_name=input_file.name,
        output_file=str(output_file),
        correlation_id=f"direct-integration-{output_file.stem}",
        kind="completed",
        prog_lang="python",
        language="en",
        format="notebook",
    )
    return job_queue.add_job(
        job_type="notebook",
        input_file=str(input_file),
        output_file=str(output_file),
        content_hash=payload.content_hash(),
        payload=payload.model_dump(mode="json"),
    )


def _wait_for_registered_workers(
    manager: WorkerPoolManager,
    expected_count: int,
    *,
    worker_type: str | None = None,
    timeout: float = 15.0,
    interval: float = 0.1,
) -> int:
    """Poll the ``workers`` table until ``expected_count`` rows with a valid
    (non-``created``) status are present.

    Replaces the ``time.sleep(2)`` "give workers time to register" idiom in
    these integration tests. Under xdist -n auto, 2s is not always enough for
    a subprocess to activate from ``created`` → ``idle``; polling is fast
    when it succeeds and deterministic when it fails.
    """
    deadline = time.monotonic() + timeout
    while True:
        conn = manager.job_queue._get_conn()
        query = "SELECT COUNT(*) FROM workers WHERE status IN ('idle', 'busy')"
        params: tuple = ()
        if worker_type is not None:
            query += " AND worker_type = ?"
            params = (worker_type,)
        cursor = conn.execute(query, params)
        count = cursor.fetchone()[0]
        if count >= expected_count:
            return count
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Expected {expected_count} active workers within {timeout}s "
                f"(worker_type={worker_type}); got {count}"
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
DRAWIO_WORKER_AVAILABLE = check_worker_module_available("clm.workers.drawio")
PLANTUML_WORKER_AVAILABLE = check_worker_module_available("clm.workers.plantuml")

# Skip all integration tests if notebook worker is not available
pytestmark = pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE or not DRAWIO_WORKER_AVAILABLE or not PLANTUML_WORKER_AVAILABLE,
    reason="Worker modules not available - these are true integration tests requiring full worker setup",
)


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Cleanup
    import gc
    import sqlite3

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


@pytest.mark.integration
class TestDirectWorkerIntegration:
    """Integration tests for direct worker execution."""

    def test_direct_worker_startup_and_registration(self, db_path, workspace_path):
        """Test that direct workers start up and register in database."""
        config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=[config]
        )

        try:
            manager.start_pools()

            # Poll until the worker activates from 'created' -> 'idle'.
            _wait_for_registered_workers(manager, expected_count=1)

            # Check database for registered workers
            conn = manager.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT id, worker_type, container_id, status FROM workers "
                "WHERE status IN ('idle', 'busy')"
            )
            workers = cursor.fetchall()

            assert len(workers) == 1
            worker_id, worker_type, container_id, status = workers[0]

            assert worker_type == "notebook"
            assert container_id.startswith("direct-notebook-")
            assert status in ("idle", "busy")

        finally:
            manager.stop_pools()

    @pytest.mark.skipif(not DRAWIO_WORKER_AVAILABLE, reason="DrawIO worker module not available")
    def test_multiple_direct_workers(self, db_path, workspace_path):
        """Test starting multiple direct workers of different types."""
        configs = [
            WorkerConfig(worker_type="notebook", count=2, execution_mode="direct"),
            WorkerConfig(worker_type="drawio", count=1, execution_mode="direct"),
        ]

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=configs
        )

        try:
            manager.start_pools()

            # Wait for all 3 workers to activate (idle/busy), not just be pre-
            # registered as 'created'. Fixed time.sleep(2) was a latent flake.
            _wait_for_registered_workers(manager, expected_count=3)

            # Check database
            conn = manager.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT worker_type, COUNT(*) FROM workers "
                "WHERE status IN ('idle', 'busy') "
                "GROUP BY worker_type"
            )
            results = {row[0]: row[1] for row in cursor.fetchall()}

            assert results.get("notebook", 0) == 2
            assert results.get("drawio", 0) == 1

        finally:
            manager.stop_pools()

    def test_direct_worker_processes_job(self, db_path, workspace_path):
        """Test that direct worker can process an actual job.

        Note: This test creates a simple test notebook job.
        """
        # Create a test notebook file
        test_notebook = workspace_path / "test.py"
        test_notebook.write_text(NOTEBOOK_SOURCE, encoding="utf-8")

        # Create output path
        output_file = workspace_path / "output.ipynb"

        # Add job to queue
        job_queue = JobQueue(db_path)
        job_id = _submit_notebook_job(job_queue, test_notebook, output_file)

        # Start worker
        config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=[config]
        )

        try:
            manager.start_pools()

            # Wait for job to be processed. A cold kernel start can take a
            # while on a loaded machine, so this is generous rather than tight.
            max_wait = 120
            start_time = time.time()
            job_status = "pending"
            job_error: str | None = None

            while time.time() - start_time < max_wait:
                conn = job_queue._get_conn()
                cursor = conn.execute("SELECT status, error FROM jobs WHERE id = ?", (job_id,))
                row = cursor.fetchone()
                if row:
                    job_status, job_error = row[0], row[1]
                    if job_status in ("completed", "failed"):
                        break

                time.sleep(0.5)

            # Verify job was completed. Surface the recorded error — without it
            # a payload-contract regression reads as a bare "failed != completed".
            assert job_status == "completed", f"Job status: {job_status}; error: {job_error}"

            # Verify output file exists
            assert output_file.exists(), "Output file not created"

        finally:
            manager.stop_pools()

    def test_direct_worker_health_monitoring(self, db_path, workspace_path):
        """Test that health monitoring works with direct workers."""
        config = WorkerConfig(worker_type="notebook", count=1, execution_mode="direct")

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=[config]
        )

        try:
            manager.start_pools()

            # Wait until the worker has activated — a fixed time.sleep(2)
            # can miss the activation under xdist load.
            _wait_for_registered_workers(manager, expected_count=1)

            # Start monitoring
            manager.start_monitoring(check_interval=2)

            # Wait for a few monitoring cycles
            time.sleep(6)

            # Check that workers are still healthy
            conn = manager.job_queue._get_conn()
            cursor = conn.execute("SELECT status FROM workers WHERE worker_type = 'notebook'")
            statuses = [row[0] for row in cursor.fetchall()]

            # Worker should be idle (not dead or hung)
            assert "idle" in statuses or "busy" in statuses
            assert "dead" not in statuses
            assert "hung" not in statuses

        finally:
            manager.stop_pools()

    def test_graceful_shutdown(self, db_path, workspace_path):
        """Test that workers shut down gracefully."""
        config = WorkerConfig(worker_type="notebook", count=2, execution_mode="direct")

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=[config]
        )

        try:
            manager.start_pools()
            _wait_for_registered_workers(manager, expected_count=2)

            # Verify workers started
            conn = manager.job_queue._get_conn()
            cursor = conn.execute("SELECT COUNT(*) FROM workers WHERE status IN ('idle', 'busy')")
            count = cursor.fetchone()[0]
            assert count == 2

        finally:
            # Graceful shutdown
            manager.stop_pools()

        # Verify workers marked as dead
        conn = manager.job_queue._get_conn()
        cursor = conn.execute("SELECT status FROM workers")
        statuses = [row[0] for row in cursor.fetchall()]
        assert all(s == "dead" for s in statuses)

    @pytest.mark.slow
    @pytest.mark.parametrize("worker_count", [2, 8, 16, 32])
    def test_high_concurrency_notebook_workers(self, db_path, workspace_path, worker_count):
        """Test high concurrency with multiple notebook workers.

        This test verifies that the SQLite WAL mode implementation can handle
        high concurrency workloads with 8, 16, or 32 concurrent notebook workers
        processing multiple jobs simultaneously.

        Args:
            worker_count: Number of concurrent notebook workers (2, 8, 16, or 32)
        """
        # Create job queue
        job_queue = JobQueue(db_path)

        # Submit multiple jobs (2x worker count to ensure concurrency).
        # Every job gets *distinct* content: identical sources would be served
        # from the worker's executed-notebook cache after the first job, which
        # would quietly turn this concurrency test into a cache-lookup test.
        num_jobs = worker_count * 2
        job_ids = []
        output_files = []

        for i in range(num_jobs):
            output_file = workspace_path / f"output_{i}.ipynb"
            output_files.append(output_file)

            test_notebook = workspace_path / f"test_{i}.py"
            source = NOTEBOOK_SOURCE + f'\nprint("job {i}")\n'
            test_notebook.write_text(source, encoding="utf-8")

            job_ids.append(
                _submit_notebook_job(job_queue, test_notebook, output_file, source=source)
            )

        # Start workers
        config = WorkerConfig(worker_type="notebook", count=worker_count, execution_mode="direct")

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=[config]
        )

        try:
            manager.start_pools()

            # Wait for all workers to activate. Fixed time.sleep(3) was flaky
            # at 32 workers on loaded CI — polling scales up with expected
            # count and uses a 30s ceiling which is plenty for even 32 workers.
            _wait_for_registered_workers(
                manager,
                expected_count=worker_count,
                worker_type="notebook",
                timeout=30.0,
            )

            # Verify all workers registered
            conn = job_queue._get_conn()
            cursor = conn.execute(
                "SELECT COUNT(*) FROM workers WHERE worker_type = 'notebook' AND status IN ('idle', 'busy')"
            )
            registered_count = cursor.fetchone()[0]
            assert registered_count == worker_count, (
                f"Expected {worker_count} workers, found {registered_count}"
            )

            # Wait for all jobs to complete (max 120 seconds)
            max_wait = 120
            start_time = time.time()
            completed_jobs = set()
            failed_jobs = []

            while time.time() - start_time < max_wait:
                conn = job_queue._get_conn()

                # Check completed jobs
                cursor = conn.execute("SELECT id FROM jobs WHERE status = 'completed'")
                for row in cursor.fetchall():
                    completed_jobs.add(row[0])

                # Check failed jobs
                cursor = conn.execute("SELECT id, error FROM jobs WHERE status = 'failed'")
                for row in cursor.fetchall():
                    failed_jobs.append((row[0], row[1]))

                # Break if all jobs are done
                if len(completed_jobs) + len(failed_jobs) == num_jobs:
                    break

                time.sleep(1)

            # Verify no jobs failed
            assert len(failed_jobs) == 0, f"Jobs failed: {failed_jobs}"

            # Verify all jobs completed
            assert len(completed_jobs) == num_jobs, (
                f"Expected {num_jobs} completed jobs, got {len(completed_jobs)}"
            )

            # Verify output files exist
            missing_files = [f for f in output_files if not f.exists()]
            assert len(missing_files) == 0, f"Missing output files: {missing_files}"

            # Verify no database errors (check for "readonly database" or similar errors)
            cursor = conn.execute(
                "SELECT id, error FROM jobs WHERE error LIKE '%database%' OR error LIKE '%readonly%'"
            )
            db_errors = cursor.fetchall()
            assert len(db_errors) == 0, f"Database-related errors found: {db_errors}"

            print(
                f"\n✓ Successfully processed {num_jobs} jobs with {worker_count} concurrent workers"
            )

        finally:
            # Graceful shutdown
            manager.stop_pools()


@pytest.mark.integration
@pytest.mark.docker
class TestMixedModeIntegration:
    """Integration tests for mixed Docker + Direct workers.

    Marked with @pytest.mark.docker because tests may use Docker workers.
    """

    def test_mixed_worker_modes(self, db_path, workspace_path):
        """Test running both Docker and direct workers simultaneously.

        The image tag used to be hard-coded to ``drawio-converter:latest``,
        which has not been a tag CLM builds since the images gained their
        ``clm-`` prefix. The docker client then tried to *pull* it and the test
        died on ``ImageNotFound``. Resolve the tag the same way the e2e
        lifecycle test does — and skip outright when no image is present rather
        than degrading to a direct-only run, since mixed mode is the subject.
        """
        drawio_image = _find_docker_image(DRAWIO_IMAGE_CANDIDATES)
        if drawio_image is None:
            pytest.skip(
                f"No DrawIO worker image available (looked for "
                f"{', '.join(DRAWIO_IMAGE_CANDIDATES)}). Run: clm docker build"
            )

        configs = [
            WorkerConfig(worker_type="notebook", count=1, execution_mode="direct"),
            WorkerConfig(
                worker_type="drawio",
                count=1,
                execution_mode="docker",
                image=drawio_image,
            ),
        ]

        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=configs
        )

        try:
            manager.start_pools()
            # Docker workers take longer to activate than direct ones.
            _wait_for_registered_workers(manager, expected_count=2, timeout=60.0)

            # Check database
            conn = manager.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT worker_type, container_id FROM workers WHERE status IN ('idle', 'busy')"
            )
            workers = cursor.fetchall()

            direct_workers = [w for w in workers if w[1].startswith("direct-")]
            assert len(direct_workers) == 1
            assert direct_workers[0][0] == "notebook"

            docker_workers = [w for w in workers if not w[1].startswith("direct-")]
            assert len(docker_workers) == 1
            assert docker_workers[0][0] == "drawio"

        finally:
            manager.stop_pools()

    def test_stale_worker_cleanup_mixed_mode(self, db_path, workspace_path):
        """Test that stale worker cleanup handles both modes correctly."""
        if not _docker_daemon_available():
            pytest.skip("Docker daemon not available")

        # Manually insert stale workers of both types.
        #
        # ``last_heartbeat`` must be set explicitly: the column defaults to
        # CURRENT_TIMESTAMP, so rows inserted without it are *fresh*, not
        # stale. ``WorkerDiscovery`` has no executor for a hand-inserted direct
        # worker and falls back to heartbeat age, so the "stale" direct worker
        # was judged healthy and kept — the assertion below then failed on a
        # test that had never actually created the state it names.
        conn = JobQueue(db_path)._get_conn()

        # Add stale direct worker
        conn.execute(
            "INSERT INTO workers (worker_type, container_id, status, last_heartbeat, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "notebook",
                "direct-notebook-0-stale123",
                "idle",
                ANCIENT_TIMESTAMP,
                ANCIENT_TIMESTAMP,
            ),
        )

        # Add stale docker worker (non-existent container)
        conn.execute(
            "INSERT INTO workers (worker_type, container_id, status, last_heartbeat, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("drawio", "nonexistent-container-id", "idle", ANCIENT_TIMESTAMP, ANCIENT_TIMESTAMP),
        )

        conn.commit()

        # Create manager and cleanup
        manager = WorkerPoolManager(
            db_path=db_path, workspace_path=workspace_path, worker_configs=[]
        )

        manager.cleanup_stale_workers()

        # Verify stale workers were removed
        conn = manager.job_queue._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM workers")
        count = cursor.fetchone()[0]
        assert count == 0, "Stale workers should be removed"
