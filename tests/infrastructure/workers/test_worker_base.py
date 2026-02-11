"""Tests for worker_base module."""

import signal
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from clm.infrastructure.database.job_queue import Job, JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.worker_base import Worker


class MockWorker(Worker):
    """Mock worker implementation for testing."""

    def __init__(self, worker_id: int, db_path: Path, poll_interval: float = 0.1):
        super().__init__(worker_id, "test", db_path, poll_interval)
        self.processed_jobs = []
        self.should_fail = False
        self.process_delay = 0.0

    def process_job(self, job: Job):
        """Process a test job."""
        time.sleep(self.process_delay)
        self.processed_jobs.append(job.id)

        if self.should_fail:
            raise ValueError(f"Simulated failure for job {job.id}")


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
def worker_id(db_path):
    """Register a test worker and return its ID."""
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
        ("test", "test-container", "idle"),
    )
    worker_id = cursor.lastrowid
    conn.commit()
    queue.close()
    return worker_id


def test_worker_initialization(worker_id, db_path):
    """Test worker initialization."""
    worker = MockWorker(worker_id, db_path)

    assert worker.worker_id == worker_id
    assert worker.worker_type == "test"
    assert worker.db_path == db_path
    assert worker.poll_interval == 0.1
    assert worker.running is True
    assert worker.job_queue is not None


def test_worker_custom_poll_interval(worker_id, db_path):
    """Test worker with custom poll interval."""
    worker = MockWorker(worker_id, db_path, poll_interval=0.5)
    assert worker.poll_interval == 0.5


def test_worker_processes_single_job(worker_id, db_path):
    """Test worker processes a single job successfully."""
    # Add a job
    queue = JobQueue(db_path)
    job_id = queue.add_job(
        job_type="test",
        input_file="input.txt",
        output_file="output.txt",
        content_hash="hash123",
        payload={"data": "test"},
    )
    queue.close()

    # Create worker and run in thread
    worker = MockWorker(worker_id, db_path)
    thread = threading.Thread(target=worker.run)
    thread.start()

    # Wait for job to be processed
    time.sleep(0.5)
    worker.stop()
    thread.join(timeout=2)

    # Verify job was processed
    assert job_id in worker.processed_jobs

    # Verify job status in database
    queue = JobQueue(db_path)
    job = queue.get_job(job_id)
    assert job.status == "completed"
    queue.close()


def test_worker_processes_multiple_jobs(worker_id, db_path):
    """Test worker processes multiple jobs."""
    # Add multiple jobs
    queue = JobQueue(db_path)
    job_ids = []
    for i in range(3):
        job_id = queue.add_job(
            job_type="test",
            input_file=f"input{i}.txt",
            output_file=f"output{i}.txt",
            content_hash=f"hash{i}",
            payload={"data": f"test{i}"},
        )
        job_ids.append(job_id)
    queue.close()

    # Create worker and run in thread
    worker = MockWorker(worker_id, db_path)
    thread = threading.Thread(target=worker.run)
    thread.start()

    # Wait for all jobs to be processed
    time.sleep(1.0)
    worker.stop()
    thread.join(timeout=2)

    # Verify all jobs were processed
    assert len(worker.processed_jobs) == 3
    for job_id in job_ids:
        assert job_id in worker.processed_jobs


def test_worker_handles_job_failure(worker_id, db_path):
    """Test worker handles job processing failures."""
    # Add a job
    queue = JobQueue(db_path)
    job_id = queue.add_job(
        job_type="test",
        input_file="input.txt",
        output_file="output.txt",
        content_hash="hash123",
        payload={"data": "test"},
    )
    queue.close()

    # Create worker that fails
    worker = MockWorker(worker_id, db_path)
    worker.should_fail = True

    thread = threading.Thread(target=worker.run)
    thread.start()

    # Wait for job to be processed
    time.sleep(0.5)
    worker.stop()
    thread.join(timeout=2)

    # Verify job was attempted
    assert job_id in worker.processed_jobs

    # Verify job status is failed
    queue = JobQueue(db_path)
    job = queue.get_job(job_id)
    assert job.status == "failed"
    assert "Simulated failure" in job.error
    queue.close()


def test_worker_updates_heartbeat(worker_id, db_path):
    """Test worker updates heartbeat regularly during operation."""
    queue = JobQueue(db_path)

    # Get initial heartbeat
    conn = queue._get_conn()
    cursor = conn.execute("SELECT last_heartbeat FROM workers WHERE id = ?", (worker_id,))
    initial_heartbeat = cursor.fetchone()[0]
    queue.close()

    # Sleep briefly to ensure time passes
    time.sleep(0.1)

    # Run worker briefly - but we need to check heartbeat BEFORE it stops
    # because workers now deregister (delete themselves) on graceful shutdown
    worker = MockWorker(worker_id, db_path)
    thread = threading.Thread(target=worker.run)
    thread.start()

    # Wait a bit for heartbeat to be updated during operation
    time.sleep(0.3)

    # Check heartbeat was updated while worker is still running
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute("SELECT last_heartbeat FROM workers WHERE id = ?", (worker_id,))
    row = cursor.fetchone()
    queue.close()

    # Worker should still exist at this point
    assert row is not None, "Worker should still exist before stopping"
    final_heartbeat = row[0]
    assert final_heartbeat >= initial_heartbeat

    # Now stop the worker
    worker.stop()
    thread.join(timeout=2)


def test_worker_updates_status(worker_id, db_path):
    """Test worker updates status between idle and busy."""
    # Add a job with a delay
    queue = JobQueue(db_path)
    job_id = queue.add_job(
        job_type="test",
        input_file="input.txt",
        output_file="output.txt",
        content_hash="hash123",
        payload={"data": "test"},
    )
    queue.close()

    # Create worker with processing delay
    worker = MockWorker(worker_id, db_path)
    worker.process_delay = 0.3

    thread = threading.Thread(target=worker.run)
    thread.start()

    # Check status changes to busy during processing
    time.sleep(0.15)  # Give worker time to pick up job

    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute("SELECT status FROM workers WHERE id = ?", (worker_id,))
    row = cursor.fetchone()
    queue.close()

    assert row is not None, "Worker should exist during processing"
    status_during = row[0]

    # Wait for job completion, then check status before stopping
    time.sleep(0.3)

    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute("SELECT status FROM workers WHERE id = ?", (worker_id,))
    row = cursor.fetchone()
    queue.close()

    # Worker should be idle after job completion but before shutdown
    assert row is not None, "Worker should exist after job completion"
    status_after_job = row[0]

    # Now stop the worker
    worker.stop()
    thread.join(timeout=2)

    # After graceful shutdown, worker deregisters (deletes itself from DB)
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute("SELECT status FROM workers WHERE id = ?", (worker_id,))
    row_after_stop = cursor.fetchone()
    queue.close()

    assert status_during == "busy"
    assert status_after_job == "idle"
    # Worker should be deleted after graceful shutdown
    assert row_after_stop is None, "Worker should be deregistered after graceful shutdown"


def test_worker_tracks_statistics(worker_id, db_path):
    """Test worker tracks job statistics during operation."""
    # Add jobs
    queue = JobQueue(db_path)
    for i in range(3):
        queue.add_job(
            job_type="test",
            input_file=f"input{i}.txt",
            output_file=f"output{i}.txt",
            content_hash=f"hash{i}",
            payload={"data": f"test{i}"},
        )
    queue.close()

    # Run worker
    worker = MockWorker(worker_id, db_path)
    thread = threading.Thread(target=worker.run)
    thread.start()

    # Wait for jobs to be processed
    time.sleep(1.0)

    # Check statistics BEFORE stopping (worker deregisters on shutdown)
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "SELECT jobs_processed, jobs_failed, avg_processing_time FROM workers WHERE id = ?",
        (worker_id,),
    )
    row = cursor.fetchone()
    queue.close()

    assert row is not None, "Worker should exist before stopping"
    jobs_processed, jobs_failed, avg_time = row

    # Now stop the worker
    worker.stop()
    thread.join(timeout=2)

    assert jobs_processed == 3
    assert jobs_failed == 0
    assert avg_time is not None
    # avg_time can be 0.0 on fast machines where jobs complete in < 1ms
    assert avg_time >= 0


def test_worker_tracks_failed_statistics(worker_id, db_path):
    """Test worker tracks failed job statistics during operation."""
    # Add jobs
    queue = JobQueue(db_path)
    for i in range(2):
        queue.add_job(
            job_type="test",
            input_file=f"input{i}.txt",
            output_file=f"output{i}.txt",
            content_hash=f"hash{i}",
            payload={"data": f"test{i}"},
        )
    queue.close()

    # Run worker that fails
    worker = MockWorker(worker_id, db_path)
    worker.should_fail = True

    thread = threading.Thread(target=worker.run)
    thread.start()

    # Wait for jobs to be processed
    time.sleep(1.0)

    # Check statistics BEFORE stopping (worker deregisters on shutdown)
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "SELECT jobs_processed, jobs_failed FROM workers WHERE id = ?", (worker_id,)
    )
    row = cursor.fetchone()
    queue.close()

    assert row is not None, "Worker should exist before stopping"
    jobs_processed, jobs_failed = row

    # Now stop the worker
    worker.stop()
    thread.join(timeout=2)

    assert jobs_processed == 0
    assert jobs_failed == 2


def test_worker_stops_gracefully(worker_id, db_path):
    """Test worker stops gracefully."""
    worker = MockWorker(worker_id, db_path)

    thread = threading.Thread(target=worker.run)
    thread.start()

    # Let it run briefly
    time.sleep(0.2)

    # Stop it
    worker.stop()
    thread.join(timeout=2)

    # Verify it stopped
    assert not thread.is_alive()
    assert worker.running is False


def test_worker_handles_no_jobs(worker_id, db_path):
    """Test worker handles having no jobs to process."""
    worker = MockWorker(worker_id, db_path)

    thread = threading.Thread(target=worker.run)
    thread.start()

    # Let it run with no jobs
    time.sleep(0.3)

    worker.stop()
    thread.join(timeout=2)

    # Verify no jobs were processed
    assert len(worker.processed_jobs) == 0


def test_worker_only_processes_own_type(worker_id, db_path):
    """Test worker only processes jobs of its type."""
    # Add jobs of different types
    queue = JobQueue(db_path)
    test_job = queue.add_job(
        job_type="test",
        input_file="input1.txt",
        output_file="output1.txt",
        content_hash="hash1",
        payload={"data": "test"},
    )
    other_job = queue.add_job(
        job_type="other",
        input_file="input2.txt",
        output_file="output2.txt",
        content_hash="hash2",
        payload={"data": "other"},
    )
    queue.close()

    # Run worker
    worker = MockWorker(worker_id, db_path)
    thread = threading.Thread(target=worker.run)
    thread.start()

    time.sleep(0.5)
    worker.stop()
    thread.join(timeout=2)

    # Verify only test job was processed
    assert test_job in worker.processed_jobs
    assert other_job not in worker.processed_jobs

    # Verify other job is still pending
    queue = JobQueue(db_path)
    job = queue.get_job(other_job)
    assert job.status == "pending"
    queue.close()


def test_worker_deregisters_on_shutdown(worker_id, db_path):
    """Test worker deregisters (deletes itself from DB) on graceful shutdown."""
    worker = MockWorker(worker_id, db_path)

    # Verify worker exists before running
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute("SELECT id FROM workers WHERE id = ?", (worker_id,))
    row_before = cursor.fetchone()
    queue.close()
    assert row_before is not None, "Worker should exist before running"

    thread = threading.Thread(target=worker.run)
    thread.start()

    time.sleep(0.2)
    worker.stop()
    thread.join(timeout=2)

    # Check worker is deregistered (deleted from DB) after graceful shutdown
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute("SELECT id FROM workers WHERE id = ?", (worker_id,))
    row_after = cursor.fetchone()
    queue.close()

    assert row_after is None, "Worker should be deregistered after graceful shutdown"


class TestParentProcessDeathDetection:
    """Tests for parent process death detection functionality."""

    def test_worker_stores_parent_pid(self, worker_id, db_path):
        """Test worker stores parent PID at initialization."""
        import os

        worker = MockWorker(worker_id, db_path)

        assert hasattr(worker, "parent_pid")
        assert worker.parent_pid == os.getppid()

    def test_worker_has_parent_check_interval(self, worker_id, db_path):
        """Test worker has configurable parent check interval."""
        worker = MockWorker(worker_id, db_path)

        assert hasattr(worker, "parent_check_interval")
        assert worker.parent_check_interval == Worker.DEFAULT_PARENT_CHECK_INTERVAL

    def test_is_parent_alive_returns_true_for_alive_parent(self, worker_id, db_path):
        """Test _is_parent_alive returns True when parent is running."""
        worker = MockWorker(worker_id, db_path)

        # Parent process should be alive (pytest process)
        assert worker._is_parent_alive() is True

    def test_is_parent_alive_returns_false_for_dead_parent(self, worker_id, db_path):
        """Test _is_parent_alive returns False when parent PID doesn't exist."""
        worker = MockWorker(worker_id, db_path)

        # Set parent_pid to a PID that doesn't exist
        worker.parent_pid = 99999999  # Very unlikely to exist

        assert worker._is_parent_alive() is False

    def test_should_check_parent_throttled(self, worker_id, db_path):
        """Test _should_check_parent is throttled by interval."""
        worker = MockWorker(worker_id, db_path)
        worker.parent_check_interval = 1.0  # 1 second interval

        # Should return True immediately after initialization
        # (because _last_parent_check is set to now() in __init__)
        assert worker._should_check_parent() is False

        # Fast-forward the last check time
        from datetime import datetime, timedelta

        worker._last_parent_check = datetime.now() - timedelta(seconds=2)

        # Now should return True
        assert worker._should_check_parent() is True

    def test_check_parent_and_exit_if_dead_stops_worker(self, worker_id, db_path):
        """Test _check_parent_and_exit_if_dead stops worker when parent is dead."""
        worker = MockWorker(worker_id, db_path)

        # Set parent_pid to non-existent process
        worker.parent_pid = 99999999

        # Force the check to happen
        from datetime import datetime, timedelta

        worker._last_parent_check = datetime.now() - timedelta(seconds=10)

        # Call the check
        result = worker._check_parent_and_exit_if_dead()

        assert result is True
        assert worker.running is False

    def test_check_parent_and_exit_if_dead_does_nothing_when_alive(self, worker_id, db_path):
        """Test _check_parent_and_exit_if_dead does nothing when parent is alive."""
        worker = MockWorker(worker_id, db_path)

        # Force the check to happen
        from datetime import datetime, timedelta

        worker._last_parent_check = datetime.now() - timedelta(seconds=10)

        # Call the check - parent should be alive (pytest process)
        result = worker._check_parent_and_exit_if_dead()

        assert result is False
        assert worker.running is True

    def test_worker_exits_when_parent_dies_during_run(self, worker_id, db_path):
        """Test worker exits its run loop when it detects parent death."""
        worker = MockWorker(worker_id, db_path)
        worker.parent_check_interval = 0.1  # Check frequently

        # Set parent_pid to non-existent process
        worker.parent_pid = 99999999

        # Run worker in thread
        thread = threading.Thread(target=worker.run)
        thread.start()

        # Worker should stop quickly due to parent death detection
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert worker.running is False

    def test_register_worker_with_retry_includes_parent_pid(self, db_path):
        """Test register_worker_with_retry stores parent_pid in database."""
        import os

        # Set worker ID environment variable
        with patch.dict(os.environ, {"WORKER_ID": "test-worker-123"}):
            worker_id = Worker.register_worker_with_retry(db_path, "test")

        # Check parent_pid was stored
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute("SELECT parent_pid FROM workers WHERE id = ?", (worker_id,))
        stored_parent_pid = cursor.fetchone()[0]
        queue.close()

        assert stored_parent_pid == os.getppid()


class TestWorkerPreRegistration:
    """Tests for worker pre-registration functionality."""

    def test_activate_pre_registered_worker_success(self, db_path):
        """Test activating a pre-registered worker in 'created' status."""
        # Pre-register a worker with 'created' status
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("notebook", "test-container", "created"),
        )
        pre_registered_id = cursor.lastrowid
        queue.close()

        # Activate the worker
        result = Worker.activate_pre_registered_worker(db_path, pre_registered_id, "notebook")

        assert result == pre_registered_id

        # Verify status changed to 'idle'
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute("SELECT status FROM workers WHERE id = ?", (pre_registered_id,))
        status = cursor.fetchone()[0]
        queue.close()

        assert status == "idle"

    def test_activate_pre_registered_worker_not_found(self, db_path):
        """Test activating a non-existent worker raises error."""
        with pytest.raises(ValueError, match="does not exist in database"):
            Worker.activate_pre_registered_worker(db_path, 99999, "notebook")

    def test_activate_pre_registered_worker_wrong_status(self, db_path):
        """Test activating a worker not in 'created' status raises error."""
        # Create a worker in 'idle' status
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("notebook", "test-container", "idle"),
        )
        worker_id = cursor.lastrowid
        queue.close()

        with pytest.raises(ValueError, match="has status 'idle', expected 'created'"):
            Worker.activate_pre_registered_worker(db_path, worker_id, "notebook")

    def test_get_or_register_worker_with_pre_assigned_id_sqlite(self, db_path):
        """Test get_or_register_worker activates pre-registered worker in SQLite mode."""
        import os

        # Pre-register a worker
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("notebook", "test-container", "created"),
        )
        pre_registered_id = cursor.lastrowid
        queue.close()

        # Set pre-assigned ID environment variable
        with patch.dict(os.environ, {"CLM_WORKER_ID": str(pre_registered_id)}):
            result = Worker.get_or_register_worker(db_path, None, "notebook")

        assert result == pre_registered_id

        # Verify status is now 'idle'
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute("SELECT status FROM workers WHERE id = ?", (pre_registered_id,))
        status = cursor.fetchone()[0]
        queue.close()

        assert status == "idle"

    def test_get_or_register_worker_fallback_to_registration(self, db_path):
        """Test get_or_register_worker falls back to registration without pre-assigned ID."""
        import os

        # Ensure no pre-assigned ID
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLM_WORKER_ID", None)
            result = Worker.get_or_register_worker(db_path, None, "notebook")

        assert result is not None
        assert isinstance(result, int)

        # Verify worker was created
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute("SELECT worker_type, status FROM workers WHERE id = ?", (result,))
        row = cursor.fetchone()
        queue.close()

        assert row is not None
        assert row[0] == "notebook"
        assert row[1] == "idle"

    def test_get_or_register_worker_raises_without_paths(self):
        """Test get_or_register_worker raises error without db_path or api_url."""
        import os

        with patch.dict(os.environ, {"CLM_WORKER_ID": "1"}):
            with pytest.raises(ValueError, match="Neither db_path nor api_url provided"):
                Worker.get_or_register_worker(None, None, "notebook")
