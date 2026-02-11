"""Mock-based tests for worker lifecycle management.

These tests verify the worker lifecycle logic using mock workers instead of
real worker processes. This enables fast, deterministic testing of:
- Worker registration and discovery
- Job queue coordination
- Worker pool management
- Health checking and cleanup

These tests run much faster than the real integration tests and are suitable
for CI/CD pipelines.
"""

import sqlite3
import time
from pathlib import Path

import pytest

from tests.fixtures.mock_workers import MockWorker, MockWorkerConfig, MockWorkerPool


class TestMockWorkerBasics:
    """Test basic mock worker functionality."""

    def test_mock_worker_starts_and_registers(self, mock_db_path):
        """Test that a mock worker starts and registers in the database."""
        config = MockWorkerConfig(worker_type="notebook")
        worker = MockWorker(config, mock_db_path, worker_id=0)

        try:
            worker.start()
            time.sleep(0.2)  # Give worker time to register

            # Verify worker is registered in database
            conn = sqlite3.connect(str(mock_db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM workers WHERE container_id = ?", (worker.container_id,)
            )
            row = cursor.fetchone()
            conn.close()

            assert row is not None, "Worker should be registered in database"
            assert row["worker_type"] == "notebook"
            assert row["status"] in ("idle", "busy")
            assert row["execution_mode"] == "mock"
        finally:
            worker.stop()

    def test_mock_worker_stops_and_marks_dead(self, mock_db_path):
        """Test that a mock worker marks itself as dead when stopped."""
        config = MockWorkerConfig(worker_type="notebook")
        worker = MockWorker(config, mock_db_path, worker_id=0)

        worker.start()
        time.sleep(0.2)
        worker.stop()
        time.sleep(0.1)

        # Verify worker is marked as dead
        conn = sqlite3.connect(str(mock_db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT status FROM workers WHERE container_id = ?", (worker.container_id,)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["status"] == "dead"

    def test_mock_worker_processes_job(self, mock_db_path, mock_workspace_path):
        """Test that a mock worker processes a job from the queue."""
        # Create a job
        conn = sqlite3.connect(str(mock_db_path))
        output_file = mock_workspace_path / "output" / "test_output.ipynb"
        conn.execute(
            """INSERT INTO jobs (job_type, input_file, output_file, content_hash, payload, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (
                "notebook",
                str(mock_workspace_path / "test.ipynb"),
                str(output_file),
                "testhash123",
                "{}",
            ),
        )
        conn.commit()
        conn.close()

        # Start mock worker
        config = MockWorkerConfig(worker_type="notebook", processing_delay=0.05)
        worker = MockWorker(config, mock_db_path, worker_id=0)

        try:
            worker.start()
            time.sleep(0.5)  # Give worker time to process job

            # Verify job is completed
            conn = sqlite3.connect(str(mock_db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT status FROM jobs")
            row = cursor.fetchone()
            conn.close()

            assert row["status"] == "completed"
            assert output_file.exists(), "Output file should be created"
            assert worker.stats.jobs_processed == 1
        finally:
            worker.stop()


class TestMockWorkerPool:
    """Test mock worker pool functionality."""

    def test_pool_starts_multiple_workers(self, mock_db_path):
        """Test that a pool can start multiple workers."""
        pool = MockWorkerPool(mock_db_path)

        try:
            workers = pool.start_workers("notebook", count=3)
            time.sleep(0.5)  # Increased from 0.2 to give workers more time to register

            assert len(workers) == 3
            assert len(pool.running_workers) == 3

            # Verify all workers registered
            conn = sqlite3.connect(str(mock_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM workers WHERE status != 'dead'")
            count = cursor.fetchone()[0]
            conn.close()

            assert count == 3
        finally:
            pool.stop_all()

    def test_pool_stops_all_workers(self, mock_db_path):
        """Test that stop_all stops all workers."""
        pool = MockWorkerPool(mock_db_path)

        workers = pool.start_workers("notebook", count=3)
        time.sleep(0.2)
        pool.stop_all()
        time.sleep(0.2)

        assert len(pool.running_workers) == 0

        # Verify all workers marked as dead
        conn = sqlite3.connect(str(mock_db_path))
        cursor = conn.execute("SELECT COUNT(*) FROM workers WHERE status = 'dead'")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 3

    def test_pool_handles_multiple_worker_types(self, mock_db_path):
        """Test that a pool can manage multiple worker types."""
        pool = MockWorkerPool(mock_db_path)

        try:
            notebook_workers = pool.start_workers("notebook", count=2)
            plantuml_workers = pool.start_workers("plantuml", count=1)
            drawio_workers = pool.start_workers("drawio", count=1)
            time.sleep(0.2)

            assert len(notebook_workers) == 2
            assert len(plantuml_workers) == 1
            assert len(drawio_workers) == 1
            assert len(pool.running_workers) == 4

            # Verify worker types in database
            conn = sqlite3.connect(str(mock_db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT worker_type, COUNT(*) as count FROM workers GROUP BY worker_type"
            )
            rows = {row["worker_type"]: row["count"] for row in cursor.fetchall()}
            conn.close()

            assert rows.get("notebook", 0) == 2
            assert rows.get("plantuml", 0) == 1
            assert rows.get("drawio", 0) == 1
        finally:
            pool.stop_all()


class TestMockWorkerJobProcessing:
    """Test job processing with mock workers."""

    def test_multiple_workers_process_multiple_jobs(self, mock_db_path, mock_workspace_path):
        """Test that multiple workers can process multiple jobs concurrently."""
        pool = MockWorkerPool(mock_db_path)

        # Create 10 jobs
        conn = sqlite3.connect(str(mock_db_path))
        for i in range(10):
            output_file = mock_workspace_path / "output" / f"test_output_{i}.ipynb"
            conn.execute(
                """INSERT INTO jobs (job_type, input_file, output_file, content_hash, payload, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                ("notebook", f"test_{i}.ipynb", str(output_file), f"hash_{i}", "{}"),
            )
        conn.commit()
        conn.close()

        try:
            # Start 3 workers with fast processing
            pool.start_workers("notebook", count=3, processing_delay=0.02)

            # Wait for all jobs to complete
            completed = pool.wait_for_jobs(timeout=5.0)
            assert completed, "All jobs should complete within timeout"

            # Verify all jobs completed
            conn = sqlite3.connect(str(mock_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'completed'")
            count = cursor.fetchone()[0]
            conn.close()

            assert count == 10

            # Verify stats
            stats = pool.get_stats()
            assert stats["jobs_processed"] == 10
            assert stats["jobs_failed"] == 0
        finally:
            pool.stop_all()

    def test_worker_handles_job_failures(self, mock_db_path, mock_workspace_path):
        """Test that workers handle job failures correctly."""
        pool = MockWorkerPool(mock_db_path)

        # Create 10 jobs
        conn = sqlite3.connect(str(mock_db_path))
        for i in range(10):
            output_file = mock_workspace_path / "output" / f"test_output_{i}.ipynb"
            conn.execute(
                """INSERT INTO jobs (job_type, input_file, output_file, content_hash, payload, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                ("notebook", f"test_{i}.ipynb", str(output_file), f"hash_{i}", "{}"),
            )
        conn.commit()
        conn.close()

        try:
            # Start workers with 50% fail rate
            pool.start_workers("notebook", count=2, processing_delay=0.02, fail_rate=0.5)

            # Wait for all jobs to complete
            completed = pool.wait_for_jobs(timeout=5.0)
            assert completed, "All jobs should be processed within timeout"

            # Verify stats show both successes and failures
            stats = pool.get_stats()
            total_processed = stats["jobs_processed"] + stats["jobs_failed"]
            assert total_processed == 10
            # With 50% fail rate, we expect some failures
            assert stats["jobs_failed"] > 0, "Some jobs should have failed"
        finally:
            pool.stop_all()


class TestMockWorkerDiscovery:
    """Test worker discovery with mock workers."""

    def test_discover_healthy_workers(self, mock_db_path):
        """Test discovering healthy workers."""
        from clm.infrastructure.workers.discovery import WorkerDiscovery

        pool = MockWorkerPool(mock_db_path)

        try:
            pool.start_workers("notebook", count=2)
            pool.start_workers("plantuml", count=1)
            time.sleep(0.2)

            discovery = WorkerDiscovery(mock_db_path)
            workers = discovery.discover_workers()

            # Should find 3 workers
            assert len(workers) == 3

            # Check worker types
            worker_types = {w.worker_type for w in workers}
            assert "notebook" in worker_types
            assert "plantuml" in worker_types

            # All should be healthy (mock workers stay registered)
            notebook_workers = [w for w in workers if w.worker_type == "notebook"]
            assert len(notebook_workers) == 2
        finally:
            pool.stop_all()

    def test_discover_workers_by_type(self, mock_db_path):
        """Test discovering workers filtered by type."""
        from clm.infrastructure.workers.discovery import WorkerDiscovery

        pool = MockWorkerPool(mock_db_path)

        try:
            pool.start_workers("notebook", count=3)
            pool.start_workers("plantuml", count=2)
            time.sleep(0.5)  # Increased from 0.2 to give workers more time to register

            discovery = WorkerDiscovery(mock_db_path)

            # Discover only notebook workers
            notebook_workers = discovery.discover_workers(worker_type="notebook")
            assert len(notebook_workers) == 3

            # Discover only plantuml workers
            plantuml_workers = discovery.discover_workers(worker_type="plantuml")
            assert len(plantuml_workers) == 2
        finally:
            pool.stop_all()


class TestMockWorkerCleanup:
    """Test worker cleanup functionality with mock workers."""

    def test_cleanup_dead_workers(self, mock_db_path):
        """Test that dead workers can be cleaned up."""
        pool = MockWorkerPool(mock_db_path)

        # Start and stop workers
        pool.start_workers("notebook", count=3)
        time.sleep(0.2)
        pool.stop_all()
        time.sleep(0.2)

        # Verify workers are marked as dead
        conn = sqlite3.connect(str(mock_db_path))
        cursor = conn.execute("SELECT COUNT(*) FROM workers WHERE status = 'dead'")
        dead_count = cursor.fetchone()[0]
        conn.close()

        assert dead_count == 3

    def test_pool_stats_after_processing(self, mock_db_path, mock_workspace_path):
        """Test pool stats are accurate after processing jobs."""
        pool = MockWorkerPool(mock_db_path)

        # Create 5 jobs
        conn = sqlite3.connect(str(mock_db_path))
        for i in range(5):
            output_file = mock_workspace_path / "output" / f"test_output_{i}.ipynb"
            conn.execute(
                """INSERT INTO jobs (job_type, input_file, output_file, content_hash, payload, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                ("notebook", f"test_{i}.ipynb", str(output_file), f"hash_{i}", "{}"),
            )
        conn.commit()
        conn.close()

        try:
            pool.start_workers("notebook", count=2, processing_delay=0.02)
            pool.wait_for_jobs(timeout=5.0)

            stats = pool.get_stats()
            assert stats["total_workers"] == 2
            assert stats["running_workers"] == 2
            assert stats["jobs_processed"] == 5
            assert stats["jobs_failed"] == 0
            assert stats["total_processing_time"] > 0
        finally:
            pool.stop_all()
