"""Tests for job queue management."""

import gc
import sqlite3
import tempfile
import time
import threading
from pathlib import Path

import pytest

from clx.infrastructure.database.schema import init_database
from clx.infrastructure.database.job_queue import JobQueue, Job


@pytest.fixture
def job_queue():
    """Create a temporary job queue for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    init_database(db_path)
    queue = JobQueue(db_path)

    yield queue

    # Proper cleanup for Windows - close connections and checkpoint WAL
    queue.close()

    # Force garbage collection to release any lingering connections
    gc.collect()

    # Checkpoint WAL to consolidate files back into main database
    try:
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.close()
    except Exception:
        pass

    # Delete database and WAL files with retry logic for Windows
    for attempt in range(3):
        try:
            db_path.unlink(missing_ok=True)
            # Also remove WAL and SHM files
            for suffix in ['-wal', '-shm']:
                wal_file = Path(str(db_path) + suffix)
                wal_file.unlink(missing_ok=True)
            break
        except PermissionError:
            if attempt < 2:
                time.sleep(0.1)
            # If still fails on last attempt, just continue (file will be cleaned up by OS eventually)


def test_add_job(job_queue):
    """Test adding a job to the queue."""
    job_id = job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={'lang': 'python'}
    )

    assert job_id > 0, "Job ID should be positive"


def test_get_next_job(job_queue):
    """Test retrieving next job from queue."""
    # Add a job
    job_id = job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={'lang': 'python'}
    )

    # Get next job
    job = job_queue.get_next_job('notebook')

    assert job is not None, "Should retrieve a job"
    assert job.id == job_id, "Should retrieve the correct job"
    assert job.job_type == 'notebook'
    assert job.status == 'processing'
    assert job.attempts == 1


def test_get_next_job_by_type(job_queue):
    """Test that get_next_job filters by type."""
    # Add jobs of different types
    job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={}
    )

    job_queue.add_job(
        job_type='drawio',
        input_file='test.drawio',
        output_file='test.png',
        content_hash='def456',
        payload={}
    )

    # Get drawio job
    job = job_queue.get_next_job('drawio')

    assert job is not None
    assert job.job_type == 'drawio'


def test_get_next_job_priority(job_queue):
    """Test that jobs are retrieved by priority."""
    # Add low priority job
    job_queue.add_job(
        job_type='notebook',
        input_file='test1.py',
        output_file='test1.ipynb',
        content_hash='abc123',
        payload={},
        priority=0
    )

    # Add high priority job
    high_priority_id = job_queue.add_job(
        job_type='notebook',
        input_file='test2.py',
        output_file='test2.ipynb',
        content_hash='def456',
        payload={},
        priority=10
    )

    # Should get high priority job first
    job = job_queue.get_next_job('notebook')

    assert job is not None
    assert job.id == high_priority_id


def test_no_job_available(job_queue):
    """Test get_next_job when no jobs are available."""
    job = job_queue.get_next_job('notebook')
    assert job is None, "Should return None when no jobs available"


def test_update_job_status_completed(job_queue):
    """Test updating job status to completed."""
    job_id = job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={}
    )

    job = job_queue.get_next_job('notebook')
    job_queue.update_job_status(job.id, 'completed')

    # Verify status updated
    updated_job = job_queue.get_job(job.id)
    assert updated_job.status == 'completed'
    assert updated_job.completed_at is not None
    assert updated_job.error is None


def test_update_job_status_failed(job_queue):
    """Test updating job status to failed."""
    job_id = job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={}
    )

    job = job_queue.get_next_job('notebook')
    job_queue.update_job_status(job.id, 'failed', error='Test error')

    # Verify status updated
    updated_job = job_queue.get_job(job.id)
    assert updated_job.status == 'failed'
    assert updated_job.error == 'Test error'


def test_job_stats(job_queue):
    """Test getting job statistics."""
    # Add jobs in different states
    job_id1 = job_queue.add_job(
        job_type='notebook',
        input_file='test1.py',
        output_file='test1.ipynb',
        content_hash='abc123',
        payload={}
    )

    job_id2 = job_queue.add_job(
        job_type='notebook',
        input_file='test2.py',
        output_file='test2.ipynb',
        content_hash='def456',
        payload={}
    )

    # Process one job
    job = job_queue.get_next_job('notebook')
    job_queue.update_job_status(job.id, 'completed')

    # Get stats
    stats = job_queue.get_job_stats()

    assert stats['pending'] == 1
    assert stats['processing'] == 0
    assert stats['completed'] == 1
    assert stats['failed'] == 0


def test_cache_operations(job_queue):
    """Test cache check and add operations."""
    output_file = 'test.ipynb'
    content_hash = 'abc123'
    metadata = {'format': 'notebook'}

    # Check cache (should be empty)
    result = job_queue.check_cache(output_file, content_hash)
    assert result is None

    # Add to cache
    job_queue.add_to_cache(output_file, content_hash, metadata)

    # Check cache again (should find it)
    result = job_queue.check_cache(output_file, content_hash)
    assert result is not None
    assert result['format'] == 'notebook'


def test_cache_access_tracking(job_queue):
    """Test that cache tracks access count."""
    output_file = 'test.ipynb'
    content_hash = 'abc123'
    metadata = {'format': 'notebook'}

    # Add to cache
    job_queue.add_to_cache(output_file, content_hash, metadata)

    # Access multiple times
    for i in range(3):
        job_queue.check_cache(output_file, content_hash)

    # Verify access count (check database directly)
    conn = job_queue._get_conn()
    cursor = conn.execute(
        "SELECT access_count FROM results_cache WHERE output_file = ? AND content_hash = ?",
        (output_file, content_hash)
    )
    row = cursor.fetchone()
    assert row[0] == 3


def test_max_attempts(job_queue):
    """Test that jobs stop being retrieved after max attempts."""
    job_id = job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={}
    )

    # Try to process 3 times (max_attempts = 3)
    for i in range(3):
        job = job_queue.get_next_job('notebook')
        assert job is not None, f"Should get job on attempt {i+1}"
        # Mark as pending to retry
        job_queue.update_job_status(job.id, 'pending')

    # Fourth attempt should return None (exceeded max attempts)
    job = job_queue.get_next_job('notebook')
    assert job is None, "Should not get job after max attempts"


def test_get_jobs_by_status(job_queue):
    """Test retrieving jobs by status."""
    # Add multiple jobs
    for i in range(5):
        job_queue.add_job(
            job_type='notebook',
            input_file=f'test{i}.py',
            output_file=f'test{i}.ipynb',
            content_hash=f'hash{i}',
            payload={}
        )

    # Get pending jobs
    pending_jobs = job_queue.get_jobs_by_status('pending', limit=10)
    assert len(pending_jobs) == 5

    # Process one
    job = job_queue.get_next_job('notebook')
    job_queue.update_job_status(job.id, 'completed')

    # Check counts
    pending_jobs = job_queue.get_jobs_by_status('pending')
    assert len(pending_jobs) == 4

    completed_jobs = job_queue.get_jobs_by_status('completed')
    assert len(completed_jobs) == 1


def test_reset_hung_jobs(job_queue):
    """Test resetting hung jobs."""
    job_id = job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={}
    )

    # Get job (marks as processing)
    job = job_queue.get_next_job('notebook')

    # Manually set started_at to past time
    conn = job_queue._get_conn()
    conn.execute(
        "UPDATE jobs SET started_at = datetime('now', '-700 seconds') WHERE id = ?",
        (job.id,)
    )
    conn.commit()

    # Reset hung jobs (timeout = 600 seconds)
    reset_count = job_queue.reset_hung_jobs(timeout_seconds=600)
    assert reset_count == 1

    # Job should be pending again
    updated_job = job_queue.get_job(job.id)
    assert updated_job.status == 'pending'
    assert updated_job.worker_id is None


def test_clear_old_completed_jobs(job_queue):
    """Test clearing old completed jobs."""
    # Add and complete a job
    job_id = job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={}
    )

    job = job_queue.get_next_job('notebook')
    job_queue.update_job_status(job.id, 'completed')

    # Manually set completed_at to old date
    conn = job_queue._get_conn()
    conn.execute(
        "UPDATE jobs SET completed_at = datetime('now', '-10 days') WHERE id = ?",
        (job.id,)
    )
    conn.commit()

    # Clear old jobs (keep for 7 days)
    deleted_count = job_queue.clear_old_completed_jobs(days=7)
    assert deleted_count == 1

    # Job should be gone
    deleted_job = job_queue.get_job(job_id)
    assert deleted_job is None


def test_thread_safety(job_queue):
    """Test that job queue is thread-safe."""
    # Add multiple jobs
    for i in range(10):
        job_queue.add_job(
            job_type='notebook',
            input_file=f'test{i}.py',
            output_file=f'test{i}.ipynb',
            content_hash=f'hash{i}',
            payload={}
        )

    processed_jobs = []
    lock = threading.Lock()

    def worker():
        """Worker thread that processes jobs."""
        while True:
            job = job_queue.get_next_job('notebook')
            if job is None:
                break

            with lock:
                processed_jobs.append(job.id)

            time.sleep(0.01)  # Simulate work
            job_queue.update_job_status(job.id, 'completed')

    # Start multiple worker threads
    threads = []
    for i in range(3):
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)

    # Wait for all threads to complete
    for t in threads:
        t.join()

    # All jobs should be processed exactly once
    assert len(processed_jobs) == 10
    assert len(set(processed_jobs)) == 10, "No job should be processed twice"

    # All jobs should be completed
    stats = job_queue.get_job_stats()
    assert stats['completed'] == 10


def test_job_to_dict(job_queue):
    """Test Job.to_dict() conversion."""
    job_id = job_queue.add_job(
        job_type='notebook',
        input_file='test.py',
        output_file='test.ipynb',
        content_hash='abc123',
        payload={'lang': 'python'}
    )

    job = job_queue.get_next_job('notebook')
    job_dict = job.to_dict()

    assert isinstance(job_dict, dict)
    assert job_dict['id'] == job_id
    assert job_dict['job_type'] == 'notebook'
    assert job_dict['status'] == 'processing'
    assert 'created_at' in job_dict
    assert isinstance(job_dict['created_at'], str)  # Should be ISO format
