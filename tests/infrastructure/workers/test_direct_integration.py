"""Integration tests for direct worker execution.

These tests verify that workers can run directly as subprocesses
and process actual jobs end-to-end.
"""

import sys
import tempfile
import time
import json
from pathlib import Path
from importlib.util import find_spec

import pytest

from clx.infrastructure.database.schema import init_database
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.workers.pool_manager import WorkerPoolManager
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
    not NOTEBOOK_WORKER_AVAILABLE or not DRAWIO_WORKER_AVAILABLE or not PLANTUML_WORKER_AVAILABLE,
    reason="Worker modules not available - these are true integration tests requiring full worker setup"
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


@pytest.mark.integration
class TestDirectWorkerIntegration:
    """Integration tests for direct worker execution."""

    def test_direct_worker_startup_and_registration(self, db_path, workspace_path):
        """Test that direct workers start up and register in database."""
        config = WorkerConfig(
            worker_type='notebook',
            count=1,
            execution_mode='direct'
        )

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config]
        )

        try:
            manager.start_pools()

            # Give workers time to register
            time.sleep(2)

            # Check database for registered workers
            conn = manager.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT id, worker_type, container_id, status FROM workers"
            )
            workers = cursor.fetchall()

            assert len(workers) == 1
            worker_id, worker_type, container_id, status = workers[0]

            assert worker_type == 'notebook'
            assert container_id.startswith('direct-notebook-')
            assert status in ('idle', 'busy')

        finally:
            manager.stop_pools()

    @pytest.mark.skipif(
        not DRAWIO_WORKER_AVAILABLE,
        reason="DrawIO worker module not available"
    )
    def test_multiple_direct_workers(self, db_path, workspace_path):
        """Test starting multiple direct workers of different types."""
        configs = [
            WorkerConfig(
                worker_type='notebook',
                count=2,
                execution_mode='direct'
            ),
            WorkerConfig(
                worker_type='drawio',
                count=1,
                execution_mode='direct'
            )
        ]

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=configs
        )

        try:
            manager.start_pools()

            # Give workers time to register
            time.sleep(2)

            # Check database
            conn = manager.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT worker_type, COUNT(*) FROM workers GROUP BY worker_type"
            )
            results = {row[0]: row[1] for row in cursor.fetchall()}

            assert results.get('notebook', 0) == 2
            assert results.get('drawio', 0) == 1

        finally:
            manager.stop_pools()

    def test_direct_worker_processes_job(self, db_path, workspace_path):
        """Test that direct worker can process an actual job.

        Note: This test creates a simple test notebook job.
        """
        # Create a test notebook file
        test_notebook = workspace_path / "test.ipynb"
        notebook_content = {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": ["print('Hello, World!')"]
                }
            ],
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3"
                }
            },
            "nbformat": 4,
            "nbformat_minor": 4
        }

        with open(test_notebook, 'w') as f:
            json.dump(notebook_content, f)

        # Create output path
        output_file = workspace_path / "output.ipynb"

        # Add job to queue
        job_queue = JobQueue(db_path)
        job_id = job_queue.add_job(
            job_type='notebook',
            input_file=str(test_notebook),
            output_file=str(output_file),
            content_hash='test-hash-123',
            payload={'kernel': 'python3', 'timeout': 60}
        )

        # Start worker
        config = WorkerConfig(
            worker_type='notebook',
            count=1,
            execution_mode='direct'
        )

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config]
        )

        try:
            manager.start_pools()

            # Wait for job to be processed (max 30 seconds)
            max_wait = 30
            start_time = time.time()
            job_status = 'pending'

            while time.time() - start_time < max_wait:
                conn = job_queue._get_conn()
                cursor = conn.execute(
                    "SELECT status FROM jobs WHERE id = ?",
                    (job_id,)
                )
                row = cursor.fetchone()
                if row:
                    job_status = row[0]
                    if job_status in ('completed', 'failed'):
                        break

                time.sleep(0.5)

            # Verify job was completed
            assert job_status == 'completed', f"Job status: {job_status}"

            # Verify output file exists
            assert output_file.exists(), "Output file not created"

        finally:
            manager.stop_pools()

    def test_direct_worker_health_monitoring(self, db_path, workspace_path):
        """Test that health monitoring works with direct workers."""
        config = WorkerConfig(
            worker_type='notebook',
            count=1,
            execution_mode='direct'
        )

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config]
        )

        try:
            manager.start_pools()

            # Give workers time to register
            time.sleep(2)

            # Start monitoring
            manager.start_monitoring(check_interval=2)

            # Wait for a few monitoring cycles
            time.sleep(6)

            # Check that workers are still healthy
            conn = manager.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT status FROM workers WHERE worker_type = 'notebook'"
            )
            statuses = [row[0] for row in cursor.fetchall()]

            # Worker should be idle (not dead or hung)
            assert 'idle' in statuses or 'busy' in statuses
            assert 'dead' not in statuses
            assert 'hung' not in statuses

        finally:
            manager.stop_pools()

    def test_graceful_shutdown(self, db_path, workspace_path):
        """Test that workers shut down gracefully."""
        config = WorkerConfig(
            worker_type='notebook',
            count=2,
            execution_mode='direct'
        )

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config]
        )

        try:
            manager.start_pools()
            time.sleep(2)

            # Verify workers started
            conn = manager.job_queue._get_conn()
            cursor = conn.execute("SELECT COUNT(*) FROM workers")
            count = cursor.fetchone()[0]
            assert count == 2

        finally:
            # Graceful shutdown
            manager.stop_pools()

        # Verify workers marked as dead
        conn = manager.job_queue._get_conn()
        cursor = conn.execute(
            "SELECT status FROM workers"
        )
        statuses = [row[0] for row in cursor.fetchall()]
        assert all(s == 'dead' for s in statuses)

    @pytest.mark.parametrize("worker_count", [2, 8, 16, 32])
    def test_high_concurrency_notebook_workers(self, db_path, workspace_path, worker_count):
        """Test high concurrency with multiple notebook workers.

        This test verifies that the SQLite WAL mode implementation can handle
        high concurrency workloads with 8, 16, or 32 concurrent notebook workers
        processing multiple jobs simultaneously.

        Args:
            worker_count: Number of concurrent notebook workers (2, 8, 16, or 32)
        """
        # Create test notebook file
        test_notebook = workspace_path / "test.ipynb"
        notebook_content = {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": ["print('Test notebook')"]
                }
            ],
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3"
                }
            },
            "nbformat": 4,
            "nbformat_minor": 4
        }

        with open(test_notebook, 'w') as f:
            json.dump(notebook_content, f)

        # Create job queue
        job_queue = JobQueue(db_path)

        # Submit multiple jobs (2x worker count to ensure concurrency)
        num_jobs = worker_count * 2
        job_ids = []
        output_files = []

        for i in range(num_jobs):
            output_file = workspace_path / f"output_{i}.ipynb"
            output_files.append(output_file)

            job_id = job_queue.add_job(
                job_type='notebook',
                input_file=str(test_notebook),
                output_file=str(output_file),
                content_hash=f'test-hash-{i}',
                payload={'kernel': 'python3', 'timeout': 60}
            )
            job_ids.append(job_id)

        # Start workers
        config = WorkerConfig(
            worker_type='notebook',
            count=worker_count,
            execution_mode='direct'
        )

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[config]
        )

        try:
            manager.start_pools()

            # Give workers time to register
            time.sleep(3)

            # Verify all workers registered
            conn = job_queue._get_conn()
            cursor = conn.execute(
                "SELECT COUNT(*) FROM workers WHERE worker_type = 'notebook' AND status IN ('idle', 'busy')"
            )
            registered_count = cursor.fetchone()[0]
            assert registered_count == worker_count, \
                f"Expected {worker_count} workers, found {registered_count}"

            # Wait for all jobs to complete (max 120 seconds)
            max_wait = 120
            start_time = time.time()
            completed_jobs = set()
            failed_jobs = []

            while time.time() - start_time < max_wait:
                conn = job_queue._get_conn()

                # Check completed jobs
                cursor = conn.execute(
                    "SELECT id FROM jobs WHERE status = 'completed'"
                )
                for row in cursor.fetchall():
                    completed_jobs.add(row[0])

                # Check failed jobs
                cursor = conn.execute(
                    "SELECT id, error FROM jobs WHERE status = 'failed'"
                )
                for row in cursor.fetchall():
                    failed_jobs.append((row[0], row[1]))

                # Break if all jobs are done
                if len(completed_jobs) + len(failed_jobs) == num_jobs:
                    break

                time.sleep(1)

            # Verify no jobs failed
            assert len(failed_jobs) == 0, \
                f"Jobs failed: {failed_jobs}"

            # Verify all jobs completed
            assert len(completed_jobs) == num_jobs, \
                f"Expected {num_jobs} completed jobs, got {len(completed_jobs)}"

            # Verify output files exist
            missing_files = [f for f in output_files if not f.exists()]
            assert len(missing_files) == 0, \
                f"Missing output files: {missing_files}"

            # Verify no database errors (check for "readonly database" or similar errors)
            cursor = conn.execute(
                "SELECT id, error FROM jobs WHERE error LIKE '%database%' OR error LIKE '%readonly%'"
            )
            db_errors = cursor.fetchall()
            assert len(db_errors) == 0, \
                f"Database-related errors found: {db_errors}"

            print(f"\n✓ Successfully processed {num_jobs} jobs with {worker_count} concurrent workers")

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

        Note: This test will skip Docker workers if Docker is not available.
        """
        configs = [
            WorkerConfig(
                worker_type='notebook',
                count=1,
                execution_mode='direct'
            )
        ]

        # Try to add Docker worker if Docker is available
        try:
            import docker
            docker_client = docker.from_env()
            docker_client.ping()

            # Docker is available, add docker worker
            configs.append(
                WorkerConfig(
                    worker_type='drawio',
                    count=1,
                    execution_mode='docker',
                    image='drawio-converter:latest'
                )
            )
            has_docker = True
        except Exception:
            has_docker = False

        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=configs
        )

        try:
            manager.start_pools()
            time.sleep(3)

            # Check database
            conn = manager.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT worker_type, container_id FROM workers"
            )
            workers = cursor.fetchall()

            # Should have at least the direct worker
            assert len(workers) >= 1

            # Check direct worker
            direct_workers = [w for w in workers if w[1].startswith('direct-')]
            assert len(direct_workers) == 1
            assert direct_workers[0][0] == 'notebook'

            if has_docker:
                # Check docker worker
                docker_workers = [w for w in workers if not w[1].startswith('direct-')]
                assert len(docker_workers) == 1
                assert docker_workers[0][0] == 'drawio'

        finally:
            manager.stop_pools()

    def test_stale_worker_cleanup_mixed_mode(self, db_path, workspace_path):
        """Test that stale worker cleanup handles both modes correctly."""
        # Check if Docker is available
        try:
            import docker
            docker_client = docker.from_env()
            docker_client.ping()
        except Exception:
            pytest.skip("Docker daemon not available")

        # Manually insert stale workers of both types
        conn = JobQueue(db_path)._get_conn()

        # Add stale direct worker
        conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ('notebook', 'direct-notebook-0-stale123', 'idle')
        )

        # Add stale docker worker (non-existent container)
        conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ('drawio', 'nonexistent-container-id', 'idle')
        )

        conn.commit()

        # Create manager and cleanup
        manager = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[]
        )

        manager.cleanup_stale_workers()

        # Verify stale workers were removed
        conn = manager.job_queue._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM workers")
        count = cursor.fetchone()[0]
        assert count == 0, "Stale workers should be removed"
