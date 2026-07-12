"""Tests for job queue management."""

import gc
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

from clm.infrastructure.database.job_queue import Job, JobQueue
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def job_queue():
    """Create a temporary job queue for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
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
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass

    # Delete database and WAL files with retry logic for Windows
    for attempt in range(3):
        try:
            db_path.unlink(missing_ok=True)
            # Also remove WAL and SHM files
            for suffix in ["-wal", "-shm"]:
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
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={"lang": "python"},
    )

    assert job_id > 0, "Job ID should be positive"


def test_get_next_job(job_queue):
    """Test retrieving next job from queue."""
    # Add a job
    job_id = job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={"lang": "python"},
    )

    # Get next job
    job = job_queue.get_next_job("notebook")

    assert job is not None, "Should retrieve a job"
    assert job.id == job_id, "Should retrieve the correct job"
    assert job.job_type == "notebook"
    assert job.status == "processing"
    assert job.attempts == 1


def test_get_next_job_by_type(job_queue):
    """Test that get_next_job filters by type."""
    # Add jobs of different types
    job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={},
    )

    job_queue.add_job(
        job_type="drawio",
        input_file="test.drawio",
        output_file="test.png",
        content_hash="def456",
        payload={},
    )

    # Get drawio job
    job = job_queue.get_next_job("drawio")

    assert job is not None
    assert job.job_type == "drawio"


def test_get_next_job_execution_mode_filtering(job_queue):
    """Mode-tagged jobs are only claimed by workers of the same mode.

    A job tagged 'docker' must never be handed to a Direct worker — the
    scenario behind the spurious NoSuchKernel(xcpp20) failures, where a
    concurrent Direct-mode build's workers stole a Docker build's C++ jobs.
    """
    docker_job_id = job_queue.add_job(
        job_type="notebook",
        input_file="deck.cpp",
        output_file="deck.html",
        content_hash="abc123",
        payload={},
        execution_mode="docker",
    )

    # A Direct worker asking for notebook jobs must NOT get the docker job
    assert job_queue.get_next_job("notebook", execution_mode="direct") is None

    # A Docker worker gets it
    job = job_queue.get_next_job("notebook", execution_mode="docker")
    assert job is not None
    assert job.id == docker_job_id


def test_get_next_job_untagged_claimable_by_any_mode(job_queue):
    """Untagged jobs (legacy / mode-agnostic) go to workers of any mode."""
    job_queue.add_job(
        job_type="notebook",
        input_file="a.py",
        output_file="a.html",
        content_hash="h1",
        payload={},
    )
    job_queue.add_job(
        job_type="notebook",
        input_file="b.py",
        output_file="b.html",
        content_hash="h2",
        payload={},
    )

    assert job_queue.get_next_job("notebook", execution_mode="direct") is not None
    assert job_queue.get_next_job("notebook", execution_mode="docker") is not None


def test_get_next_job_without_mode_claims_tagged_jobs(job_queue):
    """A claimer that passes no mode keeps the legacy claim-anything behaviour."""
    job_queue.add_job(
        job_type="notebook",
        input_file="deck.cpp",
        output_file="deck.html",
        content_hash="abc123",
        payload={},
        execution_mode="docker",
    )

    assert job_queue.get_next_job("notebook") is not None


def _register_worker(job_queue, session_id, container_id, execution_mode="direct"):
    """Insert a worker row so get_next_job can resolve its owning session.

    Session-ownership claiming (issue #620) keys off the claiming worker's own
    ``workers.session_id``, so these tests must materialise a worker row.
    """
    conn = job_queue._get_conn()
    cursor = conn.execute(
        """
        INSERT INTO workers (worker_type, container_id, status, session_id, execution_mode)
        VALUES ('notebook', ?, 'idle', ?, ?)
        """,
        (container_id, session_id, execution_mode),
    )
    worker_id = cursor.lastrowid
    assert worker_id is not None
    return worker_id


def test_add_job_stamps_session_id(job_queue):
    """add_job records the owning build session on the job row (issue #620)."""
    job_id = job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={},
        session_id="session-A",
    )

    job = job_queue.get_job(job_id)
    assert job is not None
    assert job.session_id == "session-A"


def test_get_next_job_session_ownership(job_queue):
    """A worker claims only jobs stamped with its own build session (issue #620).

    A killed or concurrent build's still-pending jobs belong to a different
    session and reference a workspace this worker cannot address; letting it
    claim them makes it fail an innocent slide file with "is not in the subpath
    of".
    """
    job_a = job_queue.add_job(
        job_type="notebook",
        input_file="a.py",
        output_file="a.ipynb",
        content_hash="a",
        payload={},
        session_id="session-A",
    )

    # A worker owned by session B must NOT claim session A's job.
    worker_b = _register_worker(job_queue, "session-B", "container-b")
    assert job_queue.get_next_job("notebook", worker_id=worker_b) is None

    # A worker owned by session A claims it.
    worker_a = _register_worker(job_queue, "session-A", "container-a")
    claimed = job_queue.get_next_job("notebook", worker_id=worker_a)
    assert claimed is not None
    assert claimed.id == job_a


def test_get_next_job_untagged_session_claimable_by_any_worker(job_queue):
    """A job with no owning session (legacy / tests) is claimable by any worker."""
    job_queue.add_job(
        job_type="notebook",
        input_file="a.py",
        output_file="a.ipynb",
        content_hash="a",
        payload={},
        # No session_id — legacy / unowned job.
    )

    worker_b = _register_worker(job_queue, "session-B", "container-b")
    assert job_queue.get_next_job("notebook", worker_id=worker_b) is not None


def test_get_next_job_sessionless_claimer_is_unrestricted(job_queue):
    """A claimer with no resolvable session claims any job, session-owned or not.

    worker_id=None (no worker row) and a worker row whose session_id is NULL
    both fall back to pre-#620 claim-anything behaviour, so a build whose
    workers happen to be unstamped can never deadlock on its own jobs.
    """
    job_queue.add_job(
        job_type="notebook",
        input_file="a.py",
        output_file="a.ipynb",
        content_hash="a",
        payload={},
        session_id="session-A",
    )
    job_queue.add_job(
        job_type="notebook",
        input_file="b.py",
        output_file="b.ipynb",
        content_hash="b",
        payload={},
        session_id="session-A",
    )

    # No worker row at all → unrestricted.
    assert job_queue.get_next_job("notebook") is not None
    # A worker row with a NULL session → also unrestricted.
    legacy_worker = _register_worker(job_queue, None, "container-legacy")
    assert job_queue.get_next_job("notebook", worker_id=legacy_worker) is not None


def test_get_next_job_worker_claims_own_and_null_but_not_foreign(job_queue):
    """A session-owned worker sees its own + NULL jobs, never another session's."""
    own = job_queue.add_job(
        job_type="notebook",
        input_file="own.py",
        output_file="own.ipynb",
        content_hash="own",
        payload={},
        session_id="session-A",
    )
    legacy = job_queue.add_job(
        job_type="notebook",
        input_file="legacy.py",
        output_file="legacy.ipynb",
        content_hash="legacy",
        payload={},
    )
    foreign = job_queue.add_job(
        job_type="notebook",
        input_file="foreign.py",
        output_file="foreign.ipynb",
        content_hash="foreign",
        payload={},
        session_id="session-B",
    )

    worker_a = _register_worker(job_queue, "session-A", "container-a")
    claimed_ids = set()
    while (job := job_queue.get_next_job("notebook", worker_id=worker_a)) is not None:
        claimed_ids.add(job.id)

    assert own in claimed_ids
    assert legacy in claimed_ids
    assert foreign not in claimed_ids


def test_get_next_job_priority(job_queue):
    """Test that jobs are retrieved by priority."""
    # Add low priority job
    job_queue.add_job(
        job_type="notebook",
        input_file="test1.py",
        output_file="test1.ipynb",
        content_hash="abc123",
        payload={},
        priority=0,
    )

    # Add high priority job
    high_priority_id = job_queue.add_job(
        job_type="notebook",
        input_file="test2.py",
        output_file="test2.ipynb",
        content_hash="def456",
        payload={},
        priority=10,
    )

    # Should get high priority job first
    job = job_queue.get_next_job("notebook")

    assert job is not None
    assert job.id == high_priority_id


def test_no_job_available(job_queue):
    """Test get_next_job when no jobs are available."""
    job = job_queue.get_next_job("notebook")
    assert job is None, "Should return None when no jobs available"


def test_update_job_status_completed(job_queue):
    """Test updating job status to completed."""
    job_id = job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={},
    )

    job = job_queue.get_next_job("notebook")
    job_queue.update_job_status(job.id, "completed")

    # Verify status updated
    updated_job = job_queue.get_job(job.id)
    assert updated_job.status == "completed"
    assert updated_job.completed_at is not None
    assert updated_job.error is None


def test_update_job_status_failed(job_queue):
    """Test updating job status to failed."""
    job_id = job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={},
    )

    job = job_queue.get_next_job("notebook")
    job_queue.update_job_status(job.id, "failed", error="Test error")

    # Verify status updated
    updated_job = job_queue.get_job(job.id)
    assert updated_job.status == "failed"
    assert updated_job.error == "Test error"


def test_job_stats(job_queue):
    """Test getting job statistics."""
    # Add jobs in different states
    job_id1 = job_queue.add_job(
        job_type="notebook",
        input_file="test1.py",
        output_file="test1.ipynb",
        content_hash="abc123",
        payload={},
    )

    job_id2 = job_queue.add_job(
        job_type="notebook",
        input_file="test2.py",
        output_file="test2.ipynb",
        content_hash="def456",
        payload={},
    )

    # Process one job
    job = job_queue.get_next_job("notebook")
    job_queue.update_job_status(job.id, "completed")

    # Get stats
    stats = job_queue.get_job_stats()

    assert stats["pending"] == 1
    assert stats["processing"] == 0
    assert stats["completed"] == 1
    assert stats["failed"] == 0


def test_cache_operations(job_queue):
    """Test cache check and add operations."""
    output_file = "test.ipynb"
    content_hash = "abc123"
    metadata = {"format": "notebook"}

    # Check cache (should be empty)
    result = job_queue.check_cache(output_file, content_hash)
    assert result is None

    # Add to cache
    job_queue.add_to_cache(output_file, content_hash, metadata)

    # Check cache again (should find it)
    result = job_queue.check_cache(output_file, content_hash)
    assert result is not None
    assert result["format"] == "notebook"


def test_cache_access_tracking(job_queue):
    """Test that cache tracks access count."""
    output_file = "test.ipynb"
    content_hash = "abc123"
    metadata = {"format": "notebook"}

    # Add to cache
    job_queue.add_to_cache(output_file, content_hash, metadata)

    # Access multiple times
    for i in range(3):
        job_queue.check_cache(output_file, content_hash)

    # Verify access count (check database directly)
    conn = job_queue._get_conn()
    cursor = conn.execute(
        "SELECT access_count FROM results_cache WHERE output_file = ? AND content_hash = ?",
        (output_file, content_hash),
    )
    row = cursor.fetchone()
    assert row[0] == 3


def test_prune_old_cache_versions_keeps_newest_per_output_file(job_queue):
    """results_cache is trimmed to the newest N rows per output_file (#580).

    A changed deck's content hash yields a *new* results_cache row (the table is
    ``UNIQUE(output_file, content_hash)`` written with ``INSERT OR REPLACE``),
    and nothing in the build path used to remove the superseded rows.
    ``prune_old_cache_versions`` keeps only the newest ``retain_count`` per
    output file and drops the rest, without touching unrelated outputs.
    """
    conn = job_queue._get_conn()

    # Three successive content hashes for the same output (deck edited twice),
    # plus one row for a second output that must be left intact.
    for i, content_hash in enumerate(["h_old", "h_mid", "h_new"]):
        job_queue.add_to_cache("deck_a.html", content_hash, {"v": i})
        # Stamp created_at so "newest" is unambiguous regardless of clock
        # granularity; the real code orders by created_at DESC, id DESC.
        conn.execute(
            "UPDATE results_cache SET created_at = ? WHERE output_file = ? AND content_hash = ?",
            (f"2026-01-0{i + 1} 00:00:00", "deck_a.html", content_hash),
        )
    job_queue.add_to_cache("deck_b.html", "b_only", {"v": 0})

    deleted = job_queue.prune_old_cache_versions(retain_count=1)

    assert deleted == 2  # h_old and h_mid dropped; h_new + b_only kept

    # deck_a keeps only its newest hash; the superseded ones are gone.
    assert job_queue.check_cache("deck_a.html", "h_new") is not None
    assert job_queue.check_cache("deck_a.html", "h_mid") is None
    assert job_queue.check_cache("deck_a.html", "h_old") is None
    # The unrelated output is untouched.
    assert job_queue.check_cache("deck_b.html", "b_only") is not None


def test_prune_old_cache_versions_respects_retain_count(job_queue):
    """retain_count controls how many versions survive per output_file (#580)."""
    conn = job_queue._get_conn()
    for i in range(5):
        job_queue.add_to_cache("deck.html", f"h{i}", {"v": i})
        conn.execute(
            "UPDATE results_cache SET created_at = ? WHERE content_hash = ?",
            (f"2026-01-0{i + 1} 00:00:00", f"h{i}"),
        )

    deleted = job_queue.prune_old_cache_versions(retain_count=2)

    assert deleted == 3
    # The two newest survive; older versions are pruned.
    assert job_queue.check_cache("deck.html", "h4") is not None
    assert job_queue.check_cache("deck.html", "h3") is not None
    assert job_queue.check_cache("deck.html", "h2") is None


def test_prune_old_cache_versions_warm_tree_is_noop(job_queue):
    """A warm rebuild (one row per output) prunes nothing (#580)."""
    job_queue.add_to_cache("a.html", "ha", {})
    job_queue.add_to_cache("b.html", "hb", {})

    assert job_queue.prune_old_cache_versions(retain_count=1) == 0
    assert job_queue.check_cache("a.html", "ha") is not None
    assert job_queue.check_cache("b.html", "hb") is not None


def test_cleanup_all_trims_results_cache_only_when_opted_in(job_queue):
    """cleanup_all sweeps results_cache only when cache_versions is given (#580)."""
    for i in range(3):
        job_queue.add_to_cache("deck.html", f"h{i}", {"v": i})

    # Without the opt-in, the results cache is left untouched (pre-#580 behaviour).
    result = job_queue.cleanup_all()
    assert "cache_versions" not in result
    assert job_queue.check_cache("deck.html", "h0") is not None

    # With cache_versions, the sweep trims to the newest N and reports the count.
    result = job_queue.cleanup_all(cache_versions=1)
    assert result["cache_versions"] == 2
    # id DESC tiebreaker keeps the last-inserted hash.
    assert job_queue.check_cache("deck.html", "h2") is not None
    assert job_queue.check_cache("deck.html", "h0") is None


def test_max_attempts(job_queue):
    """Test that jobs stop being retrieved after max attempts."""
    job_id = job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={},
    )

    # Try to process 3 times (max_attempts = 3)
    for i in range(3):
        job = job_queue.get_next_job("notebook")
        assert job is not None, f"Should get job on attempt {i + 1}"
        # Mark as pending to retry
        job_queue.update_job_status(job.id, "pending")

    # Fourth attempt should return None (exceeded max attempts)
    job = job_queue.get_next_job("notebook")
    assert job is None, "Should not get job after max attempts"


def test_get_jobs_by_status(job_queue):
    """Test retrieving jobs by status."""
    # Add multiple jobs
    for i in range(5):
        job_queue.add_job(
            job_type="notebook",
            input_file=f"test{i}.py",
            output_file=f"test{i}.ipynb",
            content_hash=f"hash{i}",
            payload={},
        )

    # Get pending jobs
    pending_jobs = job_queue.get_jobs_by_status("pending", limit=10)
    assert len(pending_jobs) == 5

    # Process one
    job = job_queue.get_next_job("notebook")
    job_queue.update_job_status(job.id, "completed")

    # Check counts
    pending_jobs = job_queue.get_jobs_by_status("pending")
    assert len(pending_jobs) == 4

    completed_jobs = job_queue.get_jobs_by_status("completed")
    assert len(completed_jobs) == 1


def test_reset_hung_jobs(job_queue):
    """Test resetting hung jobs."""
    job_id = job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={},
    )

    # Get job (marks as processing)
    job = job_queue.get_next_job("notebook")

    # Manually set started_at to past time
    conn = job_queue._get_conn()
    conn.execute(
        "UPDATE jobs SET started_at = datetime('now', '-700 seconds') WHERE id = ?", (job.id,)
    )
    conn.commit()

    # Reset hung jobs (timeout = 600 seconds)
    reset_count = job_queue.reset_hung_jobs(timeout_seconds=600)
    assert reset_count == 1

    # Job should be pending again
    updated_job = job_queue.get_job(job.id)
    assert updated_job.status == "pending"
    assert updated_job.worker_id is None


def test_clear_old_completed_jobs(job_queue):
    """Test clearing old completed jobs."""
    # Add and complete a job
    job_id = job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={},
    )

    job = job_queue.get_next_job("notebook")
    job_queue.update_job_status(job.id, "completed")

    # Manually set completed_at to old date
    conn = job_queue._get_conn()
    conn.execute(
        "UPDATE jobs SET completed_at = datetime('now', '-10 days') WHERE id = ?", (job.id,)
    )
    conn.commit()

    # Clear old jobs (keep for 7 days)
    deleted_count = job_queue.clear_old_completed_jobs(days=7)
    assert deleted_count == 1

    # Job should be gone
    deleted_job = job_queue.get_job(job_id)
    assert deleted_job is None


def test_cancel_pending_jobs_all(job_queue):
    """Test cancelling all pending jobs."""
    for i in range(3):
        job_queue.add_job(
            job_type="notebook",
            input_file=f"test{i}.py",
            output_file=f"test{i}.ipynb",
            content_hash=f"hash{i}",
            payload={},
        )

    cancelled = job_queue.cancel_pending_jobs()
    assert len(cancelled) == 3

    # All should now be cancelled
    pending = job_queue.get_jobs_by_status("pending")
    assert len(pending) == 0
    for job_id in cancelled:
        job = job_queue.get_job(job_id)
        assert job.status == "cancelled"
        assert job.cancelled_at is not None
        assert job.cancelled_by == "user"


def test_cancel_pending_jobs_by_type(job_queue):
    """Test cancelling pending jobs filtered by type."""
    job_queue.add_job(
        job_type="notebook",
        input_file="nb.py",
        output_file="nb.ipynb",
        content_hash="hash1",
        payload={},
    )
    job_queue.add_job(
        job_type="drawio",
        input_file="diag.drawio",
        output_file="diag.png",
        content_hash="hash2",
        payload={},
    )

    cancelled = job_queue.cancel_pending_jobs(job_type="notebook")
    assert len(cancelled) == 1

    # Drawio job should still be pending
    pending = job_queue.get_jobs_by_status("pending")
    assert len(pending) == 1
    assert pending[0].job_type == "drawio"


def test_cancel_pending_jobs_by_age(job_queue):
    """Test cancelling pending jobs filtered by age."""
    job_queue.add_job(
        job_type="notebook",
        input_file="old.py",
        output_file="old.ipynb",
        content_hash="hash1",
        payload={},
    )
    job_queue.add_job(
        job_type="notebook",
        input_file="new.py",
        output_file="new.ipynb",
        content_hash="hash2",
        payload={},
    )

    # Make the first job old
    conn = job_queue._get_conn()
    conn.execute(
        "UPDATE jobs SET created_at = datetime('now', '-700 seconds') WHERE input_file = 'old.py'"
    )

    # Cancel jobs older than 600 seconds
    cancelled = job_queue.cancel_pending_jobs(min_age_seconds=600)
    assert len(cancelled) == 1

    # The new job should still be pending
    pending = job_queue.get_jobs_by_status("pending")
    assert len(pending) == 1
    assert pending[0].input_file == "new.py"


def test_cancel_pending_jobs_skips_non_pending(job_queue):
    """Test that cancel_pending_jobs only affects pending jobs."""
    job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="hash1",
        payload={},
    )

    # Move to processing
    job = job_queue.get_next_job("notebook")
    assert job is not None

    # Try to cancel - should find nothing
    cancelled = job_queue.cancel_pending_jobs()
    assert len(cancelled) == 0


def test_cancel_pending_jobs_empty_queue(job_queue):
    """Test cancelling when no pending jobs exist."""
    cancelled = job_queue.cancel_pending_jobs()
    assert len(cancelled) == 0


def test_thread_safety(job_queue):
    """Test that job queue is thread-safe."""
    # Add multiple jobs
    for i in range(10):
        job_queue.add_job(
            job_type="notebook",
            input_file=f"test{i}.py",
            output_file=f"test{i}.ipynb",
            content_hash=f"hash{i}",
            payload={},
        )

    processed_jobs = []
    lock = threading.Lock()

    def worker():
        """Worker thread that processes jobs."""
        while True:
            job = job_queue.get_next_job("notebook")
            if job is None:
                break

            with lock:
                processed_jobs.append(job.id)

            time.sleep(0.01)  # Simulate work
            job_queue.update_job_status(job.id, "completed")

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
    assert stats["completed"] == 10


def test_job_to_dict(job_queue):
    """Test Job.to_dict() conversion."""
    job_id = job_queue.add_job(
        job_type="notebook",
        input_file="test.py",
        output_file="test.ipynb",
        content_hash="abc123",
        payload={"lang": "python"},
    )

    job = job_queue.get_next_job("notebook")
    job_dict = job.to_dict()

    assert isinstance(job_dict, dict)
    assert job_dict["id"] == job_id
    assert job_dict["job_type"] == "notebook"
    assert job_dict["status"] == "processing"
    assert "created_at" in job_dict
    assert isinstance(job_dict["created_at"], str)  # Should be ISO format


def test_remove_entries_for_missing_input_files(job_queue, tmp_path):
    """Test removing completed jobs for files that no longer exist."""
    existing_file = tmp_path / "exists.py"
    existing_file.write_text("# exists")
    missing_file = str(tmp_path / "missing.py")

    # Add jobs for both files
    job_queue.add_job(
        job_type="notebook",
        input_file=str(existing_file),
        output_file="out1.ipynb",
        content_hash="hash1",
        payload={},
    )
    job_queue.add_job(
        job_type="notebook",
        input_file=missing_file,
        output_file="out2.ipynb",
        content_hash="hash2",
        payload={},
    )

    # Complete both jobs
    for _ in range(2):
        job = job_queue.get_next_job("notebook")
        job_queue.update_job_status(job.id, "completed")

    # Dry run should report but not delete
    result = job_queue.remove_entries_for_missing_input_files(dry_run=True)
    assert result["jobs"] == 1
    stats = job_queue.get_database_stats()
    assert stats["jobs_count"] == 2  # still both present

    # Actual run should delete the missing file's job
    result = job_queue.remove_entries_for_missing_input_files(dry_run=False)
    assert result["jobs"] == 1
    stats = job_queue.get_database_stats()
    assert stats["jobs_count"] == 1


def test_remove_missing_skips_pending_jobs(job_queue, tmp_path):
    """Test that pending/processing jobs are NOT removed even if file is missing."""
    missing_file = str(tmp_path / "missing.py")

    job_queue.add_job(
        job_type="notebook",
        input_file=missing_file,
        output_file="out.ipynb",
        content_hash="hash1",
        payload={},
    )

    result = job_queue.remove_entries_for_missing_input_files(dry_run=False)
    assert result["jobs"] == 0  # pending job should NOT be removed
    stats = job_queue.get_database_stats()
    assert stats["jobs_count"] == 1


# ============================================================================
# Orphan job reap (Fix 3) — mark_orphaned_jobs_failed
#
# These tests cover the WorkerLifecycleManager.stop_managed_workers pass that
# detects in-flight jobs left behind when a worker dies mid-job. See
# docs/proposals/WORKER_CLEANUP_IMPLEMENTATION_PLAN.md for the incident and
# design context.
# ============================================================================


def _claim(job_queue, worker_id: int = 1) -> Job:
    """Add a pending job and immediately claim it, returning the in-flight Job."""
    job_queue.add_job(
        job_type="notebook",
        input_file=f"test-{worker_id}.py",
        output_file=f"test-{worker_id}.ipynb",
        content_hash=f"hash-{worker_id}",
        payload={},
    )
    claimed = job_queue.get_next_job(worker_id=worker_id, job_type="notebook")
    assert claimed is not None, "get_next_job should claim the pending job we just added"
    return claimed


def test_mark_orphaned_jobs_failed_returns_empty_when_no_orphans(job_queue):
    """Clean shutdown (no in-flight jobs) returns an empty list and changes nothing."""
    # Add some completed + pending jobs, none of them orphans.
    job_queue.add_job(
        job_type="notebook",
        input_file="pending.py",
        output_file="pending.ipynb",
        content_hash="hash-pending",
        payload={},
    )
    claimed = _claim(job_queue, worker_id=2)
    job_queue.update_job_status(claimed.id, "completed")

    orphans = job_queue.mark_orphaned_jobs_failed()

    assert orphans == []
    # Sanity: the completed job remained completed, the pending job stayed pending.
    stats = job_queue.get_job_stats()
    assert stats["pending"] == 1
    assert stats["completed"] == 1
    assert stats["failed"] == 0


def test_mark_orphaned_jobs_failed_reaps_processing_job(job_queue):
    """A processing job with started_at but no completed_at is marked failed."""
    claimed = _claim(job_queue, worker_id=3)
    # Deliberately do NOT complete or cancel — simulate mid-job worker death.

    orphans = job_queue.mark_orphaned_jobs_failed()

    # Returned row describes the orphan accurately, with the pre-update status.
    assert len(orphans) == 1
    assert orphans[0]["id"] == claimed.id
    assert orphans[0]["input_file"] == claimed.input_file
    assert orphans[0]["status"] == "processing"
    assert orphans[0]["worker_id"] == 3

    # DB row is now in a terminal failed state with the canonical error and a
    # completed_at timestamp so ``clm status`` reports it correctly.
    row = job_queue.get_job(claimed.id)
    assert row.status == "failed"
    assert row.error == JobQueue.ORPHAN_ERROR_MESSAGE
    assert row.completed_at is not None


def test_mark_orphaned_jobs_failed_ignores_completed_jobs(job_queue):
    """Completed jobs must never be touched by the orphan reap."""
    claimed = _claim(job_queue, worker_id=4)
    job_queue.update_job_status(claimed.id, "completed")

    orphans = job_queue.mark_orphaned_jobs_failed()

    assert orphans == []
    row = job_queue.get_job(claimed.id)
    assert row.status == "completed"
    assert row.error is None


def test_mark_orphaned_jobs_failed_ignores_cancelled_jobs(job_queue):
    """Cancelled jobs must never be reclassified as failed orphans."""
    # Add and cancel a pending job via the public API — this sets cancelled_at.
    job_queue.add_job(
        job_type="notebook",
        input_file="to-cancel.py",
        output_file="to-cancel.ipynb",
        content_hash="hash-cancel",
        payload={},
    )
    cancelled_ids = job_queue.cancel_pending_jobs(job_type="notebook")
    assert len(cancelled_ids) == 1

    # Even if we force started_at to be non-null (simulating a racy cancel),
    # cancelled_at guards the row from being reclassified.
    conn = job_queue._get_conn()
    conn.execute(
        "UPDATE jobs SET started_at = CURRENT_TIMESTAMP WHERE id = ?",
        (cancelled_ids[0],),
    )

    orphans = job_queue.mark_orphaned_jobs_failed()

    assert orphans == []
    row = job_queue.get_job(cancelled_ids[0])
    assert row.status == "cancelled"


def test_mark_orphaned_jobs_failed_reaps_multiple_orphans(job_queue):
    """Multiple mid-flight jobs are all reaped in a single atomic pass."""
    c1 = _claim(job_queue, worker_id=5)
    c2 = _claim(job_queue, worker_id=6)
    c3 = _claim(job_queue, worker_id=7)

    orphans = job_queue.mark_orphaned_jobs_failed()

    assert {o["id"] for o in orphans} == {c1.id, c2.id, c3.id}
    for row_id in (c1.id, c2.id, c3.id):
        row = job_queue.get_job(row_id)
        assert row.status == "failed"
        assert row.error == JobQueue.ORPHAN_ERROR_MESSAGE
        assert row.completed_at is not None


def test_mark_orphaned_jobs_failed_ignores_pending_without_started_at(job_queue):
    """A genuinely untouched pending job (no started_at) is not an orphan."""
    job_queue.add_job(
        job_type="notebook",
        input_file="untouched.py",
        output_file="untouched.ipynb",
        content_hash="hash-untouched",
        payload={},
    )

    orphans = job_queue.mark_orphaned_jobs_failed()

    assert orphans == []
    stats = job_queue.get_job_stats()
    assert stats["pending"] == 1
    assert stats["failed"] == 0
