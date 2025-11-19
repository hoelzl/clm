"""Tests for worker_base module."""

import signal
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from clx.infrastructure.database.job_queue import Job, JobQueue
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.worker_base import Worker


class MockWorker(Worker):
    """Mock worker implementation for testing."""

    def __init__(self, worker_id: int, db_path: Path, poll_interval: float = 0.1):
        super().__init__(worker_id, 'test', db_path, poll_interval)
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
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
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
def worker_id(db_path):
    """Register a test worker and return its ID."""
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
        ('test', 'test-container', 'idle')
    )
    worker_id = cursor.lastrowid
    conn.commit()
    queue.close()
    return worker_id


def test_worker_initialization(worker_id, db_path):
    """Test worker initialization."""
    worker = MockWorker(worker_id, db_path)

    assert worker.worker_id == worker_id
    assert worker.worker_type == 'test'
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
        job_type='test',
        input_file='input.txt',
        output_file='output.txt',
        content_hash='hash123',
        payload={'data': 'test'}
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
    assert job.status == 'completed'
    queue.close()


def test_worker_processes_multiple_jobs(worker_id, db_path):
    """Test worker processes multiple jobs."""
    # Add multiple jobs
    queue = JobQueue(db_path)
    job_ids = []
    for i in range(3):
        job_id = queue.add_job(
            job_type='test',
            input_file=f'input{i}.txt',
            output_file=f'output{i}.txt',
            content_hash=f'hash{i}',
            payload={'data': f'test{i}'}
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
        job_type='test',
        input_file='input.txt',
        output_file='output.txt',
        content_hash='hash123',
        payload={'data': 'test'}
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
    assert job.status == 'failed'
    assert 'Simulated failure' in job.error
    queue.close()


def test_worker_updates_heartbeat(worker_id, db_path):
    """Test worker updates heartbeat regularly."""
    queue = JobQueue(db_path)

    # Get initial heartbeat
    conn = queue._get_conn()
    cursor = conn.execute(
        "SELECT last_heartbeat FROM workers WHERE id = ?",
        (worker_id,)
    )
    initial_heartbeat = cursor.fetchone()[0]
    queue.close()

    # Sleep briefly to ensure time passes
    time.sleep(0.1)

    # Run worker briefly
    worker = MockWorker(worker_id, db_path)
    thread = threading.Thread(target=worker.run)
    thread.start()

    time.sleep(0.5)
    worker.stop()
    thread.join(timeout=2)

    # Check heartbeat was updated
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "SELECT last_heartbeat FROM workers WHERE id = ?",
        (worker_id,)
    )
    final_heartbeat = cursor.fetchone()[0]
    queue.close()

    assert final_heartbeat >= initial_heartbeat


def test_worker_updates_status(worker_id, db_path):
    """Test worker updates status between idle and busy."""
    # Add a job with a delay
    queue = JobQueue(db_path)
    job_id = queue.add_job(
        job_type='test',
        input_file='input.txt',
        output_file='output.txt',
        content_hash='hash123',
        payload={'data': 'test'}
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
    cursor = conn.execute(
        "SELECT status FROM workers WHERE id = ?",
        (worker_id,)
    )
    status_during = cursor.fetchone()[0]
    queue.close()

    # Wait for completion
    time.sleep(0.5)
    worker.stop()
    thread.join(timeout=2)

    # Check status is idle after completion
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "SELECT status FROM workers WHERE id = ?",
        (worker_id,)
    )
    status_after = cursor.fetchone()[0]
    queue.close()

    assert status_during == 'busy'
    assert status_after in ('idle', 'dead')  # Could be dead if stopped


def test_worker_tracks_statistics(worker_id, db_path):
    """Test worker tracks job statistics."""
    # Add jobs
    queue = JobQueue(db_path)
    for i in range(3):
        queue.add_job(
            job_type='test',
            input_file=f'input{i}.txt',
            output_file=f'output{i}.txt',
            content_hash=f'hash{i}',
            payload={'data': f'test{i}'}
        )
    queue.close()

    # Run worker
    worker = MockWorker(worker_id, db_path)
    thread = threading.Thread(target=worker.run)
    thread.start()

    time.sleep(1.0)
    worker.stop()
    thread.join(timeout=2)

    # Check statistics
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "SELECT jobs_processed, jobs_failed, avg_processing_time FROM workers WHERE id = ?",
        (worker_id,)
    )
    jobs_processed, jobs_failed, avg_time = cursor.fetchone()
    queue.close()

    assert jobs_processed == 3
    assert jobs_failed == 0
    assert avg_time is not None
    assert avg_time > 0


def test_worker_tracks_failed_statistics(worker_id, db_path):
    """Test worker tracks failed job statistics."""
    # Add jobs
    queue = JobQueue(db_path)
    for i in range(2):
        queue.add_job(
            job_type='test',
            input_file=f'input{i}.txt',
            output_file=f'output{i}.txt',
            content_hash=f'hash{i}',
            payload={'data': f'test{i}'}
        )
    queue.close()

    # Run worker that fails
    worker = MockWorker(worker_id, db_path)
    worker.should_fail = True

    thread = threading.Thread(target=worker.run)
    thread.start()

    time.sleep(1.0)
    worker.stop()
    thread.join(timeout=2)

    # Check statistics
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "SELECT jobs_processed, jobs_failed FROM workers WHERE id = ?",
        (worker_id,)
    )
    jobs_processed, jobs_failed = cursor.fetchone()
    queue.close()

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
        job_type='test',
        input_file='input1.txt',
        output_file='output1.txt',
        content_hash='hash1',
        payload={'data': 'test'}
    )
    other_job = queue.add_job(
        job_type='other',
        input_file='input2.txt',
        output_file='output2.txt',
        content_hash='hash2',
        payload={'data': 'other'}
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
    assert job.status == 'pending'
    queue.close()


def test_worker_sets_status_to_dead_on_shutdown(worker_id, db_path):
    """Test worker sets status to dead on shutdown."""
    worker = MockWorker(worker_id, db_path)

    thread = threading.Thread(target=worker.run)
    thread.start()

    time.sleep(0.2)
    worker.stop()
    thread.join(timeout=2)

    # Check status is dead
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "SELECT status FROM workers WHERE id = ?",
        (worker_id,)
    )
    status = cursor.fetchone()[0]
    queue.close()

    assert status == 'dead'
