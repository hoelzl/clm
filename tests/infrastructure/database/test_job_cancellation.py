"""Tests for job cancellation functionality in JobQueue."""

from pathlib import Path

import pytest

from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def job_queue(tmp_path):
    """Create a job queue with initialized database."""
    db_path = tmp_path / "test_jobs.db"
    init_database(db_path)
    queue = JobQueue(db_path)
    yield queue
    queue.close()


class TestJobCancellation:
    """Tests for job cancellation methods."""

    def test_cancel_pending_job(self, job_queue):
        """Test cancelling a pending job."""
        # Add a job
        job_id = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/input.ipynb",
            output_file="/path/to/output.html",
            content_hash="abc123",
            payload={"test": "data"},
        )

        # Verify job is pending
        job = job_queue.get_job(job_id)
        assert job.status == "pending"

        # Cancel jobs for this file
        cancelled_ids = job_queue.cancel_jobs_for_file("/path/to/input.ipynb")

        # Verify cancellation
        assert cancelled_ids == [job_id]

        # Verify job status
        job = job_queue.get_job(job_id)
        assert job.status == "cancelled"
        assert job.cancelled_at is not None

    def test_cancel_with_superseding_id(self, job_queue):
        """Test cancelling jobs with a superseding correlation ID."""
        # Add a job
        job_id = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/input.ipynb",
            output_file="/path/to/output.html",
            content_hash="abc123",
            payload={"test": "data"},
            correlation_id="old-correlation-id",
        )

        # Cancel with superseding ID
        cancelled_ids = job_queue.cancel_jobs_for_file(
            "/path/to/input.ipynb", cancelled_by="new-correlation-id"
        )

        # Verify cancellation
        assert cancelled_ids == [job_id]

        # Verify cancelled_by is recorded
        job = job_queue.get_job(job_id)
        assert job.cancelled_by == "new-correlation-id"

    def test_cancel_multiple_jobs_for_same_file(self, job_queue):
        """Test cancelling multiple jobs for the same input file."""
        input_file = "/path/to/input.ipynb"

        # Add multiple jobs for the same file
        job_ids = []
        for i in range(3):
            job_id = job_queue.add_job(
                job_type="notebook",
                input_file=input_file,
                output_file=f"/path/to/output{i}.html",
                content_hash=f"abc{i}",
                payload={"test": "data"},
            )
            job_ids.append(job_id)

        # Cancel all jobs for this file
        cancelled_ids = job_queue.cancel_jobs_for_file(input_file)

        # Verify all jobs are cancelled
        assert sorted(cancelled_ids) == sorted(job_ids)

        # Verify all jobs have cancelled status
        for job_id in job_ids:
            job = job_queue.get_job(job_id)
            assert job.status == "cancelled"

    def test_cancel_does_not_affect_other_files(self, job_queue):
        """Test that cancellation doesn't affect jobs for other files."""
        # Add jobs for different files
        job_id_1 = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/input1.ipynb",
            output_file="/path/to/output1.html",
            content_hash="abc1",
            payload={"test": "data"},
        )

        job_id_2 = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/input2.ipynb",
            output_file="/path/to/output2.html",
            content_hash="abc2",
            payload={"test": "data"},
        )

        # Cancel only the first file's jobs
        cancelled_ids = job_queue.cancel_jobs_for_file("/path/to/input1.ipynb")

        # Verify only first job is cancelled
        assert cancelled_ids == [job_id_1]

        # Verify second job is still pending
        job_2 = job_queue.get_job(job_id_2)
        assert job_2.status == "pending"

    def test_cancel_no_jobs_returns_empty_list(self, job_queue):
        """Test cancelling when no jobs exist returns empty list."""
        cancelled_ids = job_queue.cancel_jobs_for_file("/nonexistent/file.ipynb")
        assert cancelled_ids == []


class TestIsJobCancelled:
    """Tests for is_job_cancelled method."""

    def test_is_job_cancelled_pending(self, job_queue):
        """Test is_job_cancelled returns False for pending job."""
        job_id = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/input.ipynb",
            output_file="/path/to/output.html",
            content_hash="abc123",
            payload={"test": "data"},
        )

        assert job_queue.is_job_cancelled(job_id) is False

    def test_is_job_cancelled_cancelled(self, job_queue):
        """Test is_job_cancelled returns True for cancelled job."""
        job_id = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/input.ipynb",
            output_file="/path/to/output.html",
            content_hash="abc123",
            payload={"test": "data"},
        )

        # Cancel the job
        job_queue.cancel_jobs_for_file("/path/to/input.ipynb")

        assert job_queue.is_job_cancelled(job_id) is True

    def test_is_job_cancelled_nonexistent(self, job_queue):
        """Test is_job_cancelled returns False for nonexistent job."""
        # Use a job ID that doesn't exist
        result = job_queue.is_job_cancelled(99999)
        assert result is False or result is None  # Either is acceptable


class TestCancellationIntegration:
    """Integration tests for cancellation workflow."""

    def test_cancel_then_submit_new_job(self, job_queue):
        """Test workflow of cancelling old jobs and submitting new one."""
        input_file = "/path/to/input.ipynb"

        # Add initial job
        old_job_id = job_queue.add_job(
            job_type="notebook",
            input_file=input_file,
            output_file="/path/to/output.html",
            content_hash="old-hash",
            payload={"test": "old"},
            correlation_id="old-correlation",
        )

        # Cancel and submit new job (simulating watch mode behavior)
        new_correlation_id = "new-correlation"
        cancelled_ids = job_queue.cancel_jobs_for_file(input_file, cancelled_by=new_correlation_id)

        new_job_id = job_queue.add_job(
            job_type="notebook",
            input_file=input_file,
            output_file="/path/to/output.html",
            content_hash="new-hash",
            payload={"test": "new"},
            correlation_id=new_correlation_id,
        )

        # Verify old job is cancelled
        assert old_job_id in cancelled_ids
        old_job = job_queue.get_job(old_job_id)
        assert old_job.status == "cancelled"
        assert old_job.cancelled_by == new_correlation_id

        # Verify new job is pending
        new_job = job_queue.get_job(new_job_id)
        assert new_job.status == "pending"

    def test_cancel_jobs_in_order(self, job_queue):
        """Test that jobs are cancelled in order."""
        input_file = "/path/to/input.ipynb"

        # Add jobs
        job_ids = []
        for i in range(5):
            job_id = job_queue.add_job(
                job_type="notebook",
                input_file=input_file,
                output_file=f"/path/to/output{i}.html",
                content_hash=f"hash{i}",
                payload={"test": f"data{i}"},
            )
            job_ids.append(job_id)

        # Cancel all
        cancelled_ids = job_queue.cancel_jobs_for_file(input_file)

        # Should be in order
        assert cancelled_ids == job_ids

    def test_only_pending_jobs_cancelled(self, job_queue):
        """Test that only pending jobs are cancelled, not already-cancelled ones."""
        input_file = "/path/to/input.ipynb"

        # Add first job and cancel it
        job_id_1 = job_queue.add_job(
            job_type="notebook",
            input_file=input_file,
            output_file="/path/to/output1.html",
            content_hash="hash1",
            payload={"test": "data1"},
        )
        job_queue.cancel_jobs_for_file(input_file)

        # Add second job (should be pending)
        job_id_2 = job_queue.add_job(
            job_type="notebook",
            input_file=input_file,
            output_file="/path/to/output2.html",
            content_hash="hash2",
            payload={"test": "data2"},
        )

        # Cancel again - should only affect job_id_2
        cancelled_ids = job_queue.cancel_jobs_for_file(input_file)
        assert cancelled_ids == [job_id_2]

        # Both should be cancelled now
        assert job_queue.is_job_cancelled(job_id_1) is True
        assert job_queue.is_job_cancelled(job_id_2) is True
