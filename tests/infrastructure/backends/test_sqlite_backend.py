"""Tests for SQLite-based backend."""

import asyncio
import gc
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from attrs import frozen

from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.operation import Operation


@frozen
class MockOperation(Operation):
    """Mock operation for testing."""

    service_name_value: str = "notebook-processor"

    @property
    def service_name(self) -> str:
        return self.service_name_value

    async def execute(self, backend, *args, **kwargs):
        pass


class MockPayload(Payload):
    """Mock payload for testing."""

    correlation_id: str = "test-correlation-id"
    input_file: str = "test.py"
    input_file_name: str = "test.py"
    output_file: str = "output/test.ipynb"
    data: str = "test content"


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    init_database(db_path)

    yield db_path

    # Proper cleanup for Windows - force garbage collection and checkpoint WAL
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


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.mark.asyncio
async def test_sqlite_backend_initialization(temp_db, temp_workspace):
    """Test SqliteBackend initialization."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        assert backend.job_queue is not None
        assert backend.db_path == temp_db
        assert backend.workspace_path == temp_workspace
        assert backend.active_jobs == {}
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_sqlite_backend_context_manager(temp_db, temp_workspace):
    """Test SqliteBackend as async context manager."""
    async with SqliteBackend(
        db_path=temp_db, workspace_path=temp_workspace, skip_worker_check=True
    ) as backend:
        assert backend.job_queue is not None

    # Backend should shut down cleanly


@pytest.mark.asyncio
async def test_execute_operation_adds_job(temp_db, temp_workspace):
    """Test that execute_operation adds a job to the queue."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()

        await backend.execute_operation(operation, payload)

        # Check that job was added
        assert len(backend.active_jobs) == 1

        # Verify job in database
        job_queue = JobQueue(temp_db)
        try:
            job = job_queue.get_next_job("notebook")
            assert job is not None
            assert job.input_file == payload.input_file
            assert job.output_file == payload.output_file
        finally:
            job_queue.close()
    finally:
        backend.active_jobs.clear()  # Avoid 5s shutdown timeout waiting for unprocessed jobs
        await backend.shutdown()


@pytest.mark.asyncio
async def test_execute_operation_multiple_job_types(temp_db, temp_workspace):
    """Test execute_operation with different job types."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        # Test notebook
        await backend.execute_operation(
            MockOperation(service_name_value="notebook-processor"),
            MockPayload(
                correlation_id="cid-1",
                input_file="test.py",
                input_file_name="test.py",
                output_file="test.ipynb",
                data="notebook data",
            ),
        )

        # Test drawio
        await backend.execute_operation(
            MockOperation(service_name_value="drawio-converter"),
            MockPayload(
                correlation_id="cid-2",
                input_file="test.drawio",
                input_file_name="test.drawio",
                output_file="test.png",
                data="drawio data",
            ),
        )

        # Test plantuml
        await backend.execute_operation(
            MockOperation(service_name_value="plantuml-converter"),
            MockPayload(
                correlation_id="cid-3",
                input_file="test.puml",
                input_file_name="test.puml",
                output_file="test.png",
                data="plantuml data",
            ),
        )

        assert len(backend.active_jobs) == 3

        # Verify each job type in database
        job_queue = JobQueue(temp_db)
        try:
            notebook_job = job_queue.get_next_job("notebook")
            drawio_job = job_queue.get_next_job("drawio")
            plantuml_job = job_queue.get_next_job("plantuml")

            assert notebook_job is not None
            assert drawio_job is not None
            assert plantuml_job is not None
        finally:
            job_queue.close()
    finally:
        backend.active_jobs.clear()  # Avoid 5s shutdown timeout waiting for unprocessed jobs
        await backend.shutdown()


@pytest.mark.asyncio
async def test_execute_operation_unknown_service(temp_db, temp_workspace):
    """Test that execute_operation raises error for unknown service."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        operation = MockOperation(service_name_value="unknown-service")
        payload = MockPayload()

        with pytest.raises(ValueError, match="Unknown service"):
            await backend.execute_operation(operation, payload)
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_wait_for_completion_no_jobs(temp_db, temp_workspace):
    """Test wait_for_completion when no jobs are active."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        result = await backend.wait_for_completion()
        assert result is True
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_wait_for_completion_successful_jobs(temp_db, temp_workspace):
    """Test wait_for_completion when jobs complete successfully."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        # Add a job
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()
        await backend.execute_operation(operation, payload)

        # Get the job ID
        job_id = list(backend.active_jobs.keys())[0]

        # Simulate job completion in background
        async def complete_job():
            await asyncio.sleep(0.1)  # Small delay
            job_queue = JobQueue(temp_db)
            try:
                job_queue.update_job_status(job_id, "completed")
            finally:
                job_queue.close()

        # Start background task
        task = asyncio.create_task(complete_job())

        # Wait for completion
        result = await backend.wait_for_completion()

        await task  # Ensure background task completes
        assert result is True
        assert len(backend.active_jobs) == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_wait_for_completion_failed_job(temp_db, temp_workspace):
    """Test wait_for_completion when a job fails."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        # Add a job
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()
        await backend.execute_operation(operation, payload)

        job_id = list(backend.active_jobs.keys())[0]

        # Simulate job failure
        async def fail_job():
            await asyncio.sleep(0.1)
            job_queue = JobQueue(temp_db)
            try:
                job_queue.update_job_status(job_id, "failed", error="Test error")
            finally:
                job_queue.close()

        task = asyncio.create_task(fail_job())

        # Wait for completion (should return False due to failure)
        result = await backend.wait_for_completion()

        await task
        assert result is False
        assert len(backend.active_jobs) == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_wait_for_completion_timeout(temp_db, temp_workspace):
    """Test wait_for_completion timeout behavior."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        max_wait_for_completion_duration=0.5,  # Short timeout
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        # Add a job but don't complete it
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()
        await backend.execute_operation(operation, payload)

        # Should timeout. The typed JobsPendingTimeoutError subclasses
        # TimeoutError, so this assertion keeps matching while the build
        # orchestration can detect the typed variant (issue #143).
        from clm.infrastructure.backend import JobsPendingTimeoutError

        with pytest.raises(JobsPendingTimeoutError, match="did not complete within") as excinfo:
            await backend.wait_for_completion()
        # Pending jobs are attached so the orchestration can record one
        # infrastructure error per stuck job.
        assert len(excinfo.value.pending_jobs) == 1
    finally:
        backend.active_jobs.clear()  # Avoid 5s shutdown timeout waiting for unprocessed jobs
        await backend.shutdown()


@pytest.mark.asyncio
async def test_wait_for_completion_timeout_reports_build_errors(temp_db, temp_workspace):
    """Issue #143 (sub-bug A): a pending-job timeout records one
    infrastructure BuildError per stuck job on the build reporter, so the
    timeout reaches the build summary instead of silently exiting 0."""
    from unittest.mock import MagicMock

    from clm.infrastructure.backend import JobsPendingTimeoutError

    reporter = MagicMock()
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        max_wait_for_completion_duration=0.5,
        skip_worker_check=True,
        build_reporter=reporter,
        enable_progress_tracking=False,
    )

    try:
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()
        await backend.execute_operation(operation, payload)

        with pytest.raises(JobsPendingTimeoutError):
            await backend.wait_for_completion()

        # Exactly one error reported, with the job-timeout signature.
        assert reporter.report_error.call_count == 1
        reported = reporter.report_error.call_args[0][0]
        assert reported.category == "job_timeout"
        assert reported.error_type == "infrastructure"
        assert reported.severity == "error"
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_sqlite_cache_hit(temp_db, temp_workspace):
    """Test that SQLite cache prevents duplicate job submission."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()

        # Add job first time
        await backend.execute_operation(operation, payload)
        job_id = list(backend.active_jobs.keys())[0]

        # Complete the job and add to cache
        job_queue = JobQueue(temp_db)
        try:
            job_queue.update_job_status(job_id, "completed")
            job_queue.add_to_cache(
                payload.output_file, payload.content_hash(), {"format": "notebook"}
            )
        finally:
            job_queue.close()

        # Clear active jobs to reset
        backend.active_jobs.clear()

        # Create output file (cache expects it to exist)
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("cached content")

        # Try to add same job again (should hit cache)
        await backend.execute_operation(operation, payload)

        # Should not have added new job
        assert len(backend.active_jobs) == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_sqlite_cache_bypassed_when_ignore_db_true(temp_db, temp_workspace):
    """`--ignore-cache` (ignore_db=True) must bypass the SQLite job cache, not just the database cache.

    Regression test: previously the job-cache lookup at sqlite_backend.process()
    was not gated on ``ignore_db``, so under ``--ignore-cache`` workers were
    silently skipped whenever the job queue's results_cache held an entry for
    the (output_file, content_hash) pair — even though the user explicitly
    asked to reprocess. That hid stale state during cassette re-records and
    snapshot recaptures (PythonCourses §1 / §5 baseline work, 2026-05-21).
    """
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=True,  # request "reprocess all files"
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()

        # Seed the job-queue cache with an entry matching this payload, and
        # create the output file so the existence check would pass too.
        job_queue = JobQueue(temp_db)
        try:
            job_queue.add_to_cache(
                payload.output_file, payload.content_hash(), {"format": "notebook"}
            )
        finally:
            job_queue.close()
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("stale cached content")

        # Execute the operation under ignore_db=True — should NOT see the
        # cache hit, should submit a fresh job instead.
        await backend.execute_operation(operation, payload)

        assert len(backend.active_jobs) == 1, (
            "ignore_db=True should bypass the job-queue cache and submit a fresh job"
        )
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_database_cache_hit(temp_db, temp_workspace):
    """Test that database manager cache prevents job submission."""
    from clm.infrastructure.messaging.base_classes import Result

    # Mock result class
    class MockResult(Result):
        data: bytes = b"cached data"
        result_type: str = "result"

        def result_bytes(self) -> bytes:
            return self.data

        def output_metadata(self) -> str:
            return "default"

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        # Mock database manager with cached result
        backend.db_manager = Mock()
        mock_result = MockResult(
            correlation_id="test",
            output_file="test.ipynb",
            input_file="test.py",
            content_hash="abc123",
        )
        backend.db_manager.get_result.return_value = mock_result

        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()

        # Create output directory
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Execute operation (should hit cache and not add job)
        await backend.execute_operation(operation, payload)

        # Verify no job was added
        assert len(backend.active_jobs) == 0

        # Verify output file was written from cache
        assert output_path.exists()
        assert output_path.read_bytes() == b"cached data"
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_shutdown_with_pending_jobs(temp_db, temp_workspace):
    """Test shutdown behavior with pending jobs."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    # Add a job but don't complete it
    operation = MockOperation(service_name_value="notebook-processor")
    payload = MockPayload()
    await backend.execute_operation(operation, payload)

    # Patch wait_for to immediately timeout — we only need to verify the behavior,
    # not wait 5s for the actual timeout.
    async def immediate_timeout(coro, timeout):
        coro.close()  # Prevent "coroutine was never awaited" warning
        raise TimeoutError

    with patch.object(asyncio, "wait_for", side_effect=immediate_timeout):
        await backend.shutdown()

    # Job should still be in active_jobs (timeout occurred)
    assert len(backend.active_jobs) > 0


@pytest.mark.asyncio
async def test_multiple_concurrent_operations(temp_db, temp_workspace):
    """Test submitting multiple operations concurrently."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        # Submit multiple operations concurrently
        operations = [
            (
                MockOperation("notebook-processor"),
                MockPayload(
                    correlation_id=f"cid-{i}",
                    input_file=f"test{i}.py",
                    input_file_name=f"test{i}.py",
                    output_file=f"test{i}.ipynb",
                    data=f"content {i}",
                ),
            )
            for i in range(10)
        ]

        await asyncio.gather(
            *[backend.execute_operation(op, payload) for op, payload in operations]
        )

        # All jobs should be tracked
        assert len(backend.active_jobs) == 10

        # All jobs should be in database
        job_queue = JobQueue(temp_db)
        try:
            stats = job_queue.get_job_stats()
            assert stats["pending"] + stats["processing"] == 10
        finally:
            job_queue.close()
    finally:
        backend.active_jobs.clear()  # Avoid 5s shutdown timeout waiting for unprocessed jobs
        await backend.shutdown()


@pytest.mark.asyncio
async def test_poll_interval_respected(temp_db, temp_workspace):
    """Test that polling interval is respected during wait_for_completion."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        poll_interval=0.2,  # 200ms
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()
        await backend.execute_operation(operation, payload)

        job_id = list(backend.active_jobs.keys())[0]

        # Track poll times
        poll_times = []

        # Patch sleep to track when polls occur
        original_sleep = asyncio.sleep

        async def tracked_sleep(duration):
            poll_times.append(asyncio.get_event_loop().time())
            await original_sleep(duration)

        # Complete job after a delay
        async def complete_job():
            await original_sleep(0.5)
            job_queue = JobQueue(temp_db)
            try:
                job_queue.update_job_status(job_id, "completed")
            finally:
                job_queue.close()

        task = asyncio.create_task(complete_job())

        with patch("asyncio.sleep", side_effect=tracked_sleep):
            await backend.wait_for_completion()

        await task

        # Should have polled at least twice (with 0.2s interval over 0.5s)
        assert len(poll_times) >= 2
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_job_not_found_in_database(temp_db, temp_workspace):
    """Test handling when a job is not found in database."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    try:
        # Manually add job to active_jobs without database entry
        backend.active_jobs[999] = {
            "job_type": "notebook",
            "input_file": "test.py",
            "output_file": "test.ipynb",
            "correlation_id": "test-cid",
        }

        # Should complete without error (job marked as completed)
        result = await backend.wait_for_completion()
        assert result is True
        assert len(backend.active_jobs) == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_copy_dir_group_reports_warnings_to_build_reporter(temp_db, temp_workspace):
    """Test that copy_dir_group_to_output reports warnings to build_reporter."""
    from clm.cli.build_data_classes import BuildWarning
    from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

    # Create mock build reporter
    mock_reporter = Mock()
    mock_reporter.report_warning = Mock()

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,
        build_reporter=mock_reporter,
    )

    # Create copy data with missing source directory
    missing_dir = temp_workspace / "missing_subdir"
    output_dir = temp_workspace / "output"
    copy_data = CopyDirGroupData(
        name="test-group",
        source_dirs=(missing_dir,),
        relative_paths=(Path("missing_subdir"),),
        lang="en",
        output_dir=output_dir,
    )

    async with backend:
        warnings = await backend.copy_dir_group_to_output(copy_data)

    # Should have one warning
    assert len(warnings) == 1
    assert warnings[0].category == "missing_directory"
    assert "missing_subdir" in warnings[0].message

    # Build reporter should have been called with the warning
    mock_reporter.report_warning.assert_called_once()
    reported_warning = mock_reporter.report_warning.call_args[0][0]
    assert isinstance(reported_warning, BuildWarning)
    assert reported_warning.category == "missing_directory"


@pytest.mark.asyncio
async def test_copy_dir_group_without_build_reporter(temp_db, temp_workspace):
    """Test that copy_dir_group_to_output works without build_reporter."""
    from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,
        build_reporter=None,  # No build reporter
    )

    # Create copy data with missing source directory
    missing_dir = temp_workspace / "missing_subdir"
    output_dir = temp_workspace / "output"
    copy_data = CopyDirGroupData(
        name="test-group",
        source_dirs=(missing_dir,),
        relative_paths=(Path("missing_subdir"),),
        lang="en",
        output_dir=output_dir,
    )

    async with backend:
        warnings = await backend.copy_dir_group_to_output(copy_data)

    # Should still return warnings (just not reported)
    assert len(warnings) == 1
    assert warnings[0].category == "missing_directory"


@pytest.mark.asyncio
async def test_copy_dir_group_successful_copy_no_warnings(temp_db, temp_workspace):
    """Test that copy_dir_group_to_output returns empty list on success."""
    from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

    # Create mock build reporter
    mock_reporter = Mock()
    mock_reporter.report_warning = Mock()

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,
        build_reporter=mock_reporter,
    )

    # Create actual source directory
    source_dir = temp_workspace / "source"
    source_dir.mkdir()
    (source_dir / "file.txt").write_text("content")

    output_dir = temp_workspace / "output"
    copy_data = CopyDirGroupData(
        name="test-group",
        source_dirs=(source_dir,),
        relative_paths=(Path("source"),),
        lang="en",
        output_dir=output_dir,
    )

    async with backend:
        warnings = await backend.copy_dir_group_to_output(copy_data)

    # Should have no warnings
    assert len(warnings) == 0

    # Build reporter should not have been called
    mock_reporter.report_warning.assert_not_called()

    # Verify copy was successful
    assert (output_dir / "source" / "file.txt").exists()


@pytest.mark.asyncio
async def test_incremental_mode_skips_writing_cached_results(temp_db, temp_workspace):
    """Test that incremental mode skips writing cached results to disk."""
    from clm.infrastructure.messaging.base_classes import Result

    # Mock result class
    class MockResult(Result):
        data: bytes = b"cached data"

        def result_bytes(self) -> bytes:
            return self.data

        def output_metadata(self) -> str:
            return "default"

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        incremental=True,  # Enable incremental mode
        skip_worker_check=True,
    )

    try:
        # Mock database manager with cached result
        backend.db_manager = Mock()
        mock_result = MockResult(
            correlation_id="test",
            output_file="test.ipynb",
            input_file="test.py",
            content_hash="abc123",
        )
        backend.db_manager.get_result.return_value = mock_result

        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()

        # Create output directory but NOT the file
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Execute operation (should hit cache but NOT write file in incremental mode)
        await backend.execute_operation(operation, payload)

        # Verify no job was added (cache hit)
        assert len(backend.active_jobs) == 0

        # Verify output file was NOT written (incremental mode skips writing)
        assert not output_path.exists()
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_non_incremental_mode_writes_cached_results(temp_db, temp_workspace):
    """Test that non-incremental mode writes cached results to disk (baseline)."""
    from clm.infrastructure.messaging.base_classes import Result

    # Mock result class
    class MockResult(Result):
        data: bytes = b"cached data"

        def result_bytes(self) -> bytes:
            return self.data

        def output_metadata(self) -> str:
            return "default"

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        incremental=False,  # Disable incremental mode (default)
        skip_worker_check=True,
    )

    try:
        # Mock database manager with cached result
        backend.db_manager = Mock()
        mock_result = MockResult(
            correlation_id="test",
            output_file="test.ipynb",
            input_file="test.py",
            content_hash="abc123",
        )
        backend.db_manager.get_result.return_value = mock_result

        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload()

        # Create output directory
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Execute operation (should hit cache and write file)
        await backend.execute_operation(operation, payload)

        # Verify no job was added (cache hit)
        assert len(backend.active_jobs) == 0

        # Verify output file WAS written (non-incremental mode writes cache)
        assert output_path.exists()
        assert output_path.read_bytes() == b"cached data"
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_incremental_copy_file_skips_existing(temp_db, temp_workspace):
    """Test that incremental mode skips copying files that already exist."""
    from clm.infrastructure.utils.copy_file_data import CopyFileData

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        incremental=True,  # Enable incremental mode
        skip_worker_check=True,
    )

    # Create source and destination files
    source_file = temp_workspace / "source" / "test.txt"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("new content")

    output_file = temp_workspace / "output" / "test.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("old content")  # Pre-existing file

    copy_data = CopyFileData(
        input_path=source_file,
        output_path=output_file,
        relative_input_path=Path("source/test.txt"),
    )

    async with backend:
        await backend.copy_file_to_output(copy_data)

    # In incremental mode, existing file should NOT be overwritten
    assert output_file.read_text() == "old content"


@pytest.mark.asyncio
async def test_incremental_copy_file_copies_missing(temp_db, temp_workspace):
    """Test that incremental mode copies files that don't exist yet."""
    from clm.infrastructure.utils.copy_file_data import CopyFileData

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        incremental=True,  # Enable incremental mode
        skip_worker_check=True,
    )

    # Create source file only
    source_file = temp_workspace / "source" / "test.txt"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("new content")

    output_file = temp_workspace / "output" / "test.txt"
    # Don't create output file - it should be copied

    copy_data = CopyFileData(
        input_path=source_file,
        output_path=output_file,
        relative_input_path=Path("source/test.txt"),
    )

    async with backend:
        await backend.copy_file_to_output(copy_data)

    # Missing file should be copied even in incremental mode
    assert output_file.exists()
    assert output_file.read_text() == "new content"


@pytest.mark.asyncio
async def test_non_incremental_copy_file_always_copies(temp_db, temp_workspace):
    """Test that non-incremental mode always copies files."""
    from clm.infrastructure.utils.copy_file_data import CopyFileData

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        incremental=False,  # Disable incremental mode
        skip_worker_check=True,
    )

    # Create source and destination files
    source_file = temp_workspace / "source" / "test.txt"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("new content")

    output_file = temp_workspace / "output" / "test.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("old content")  # Pre-existing file

    copy_data = CopyFileData(
        input_path=source_file,
        output_path=output_file,
        relative_input_path=Path("source/test.txt"),
    )

    async with backend:
        await backend.copy_file_to_output(copy_data)

    # In non-incremental mode, file should be overwritten
    assert output_file.read_text() == "new content"


# -----------------------------------------------------------------------
# Tests for the Stage 4 cache-invariant guard (_can_replay_from_cache).
#
# Recording HTML is the producer for the executed_notebooks table. When
# Recording's processed_files entry hits but executed_notebooks is empty
# for the same content, the backend must NOT short-circuit — otherwise
# Stage 4 consumers (Completed/Trainer/Partial HTML) would all fall back
# to direct execution. These tests pin both halves of the invariant.
# -----------------------------------------------------------------------


@pytest.fixture
def temp_cache_db():
    """Create a temporary cache database (clm_cache.db analogue)."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        cache_path = Path(f.name)
    yield cache_path

    gc.collect()
    try:
        conn = sqlite3.connect(cache_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass
    for attempt in range(3):
        try:
            cache_path.unlink(missing_ok=True)
            for suffix in ["-wal", "-shm"]:
                Path(str(cache_path) + suffix).unlink(missing_ok=True)
            break
        except PermissionError:
            if attempt < 2:
                time.sleep(0.1)


def _make_recording_html_payload(input_file: str = "test.py") -> "object":
    """Build a minimal NotebookPayload for Recording HTML."""
    from clm.infrastructure.messaging.notebook_classes import NotebookPayload

    return NotebookPayload(
        data="x = 1",
        input_file=input_file,
        input_file_name=Path(input_file).name,
        output_file="output/test.html",
        correlation_id="test-cid",
        kind="recording",
        prog_lang="python",
        language="en",
        format="html",
    )


def _make_mock_db_manager(cache_db_path: Path, result_to_return) -> Mock:
    """Build a mock DatabaseManager that returns a result and exposes db_path."""
    mgr = Mock()
    mgr.db_path = cache_db_path
    mgr.get_result.return_value = result_to_return
    return mgr


@pytest.mark.asyncio
async def test_recording_html_cache_replay_blocked_when_executed_notebooks_cold(
    temp_db, temp_workspace, temp_cache_db
):
    """Recording HTML must submit a worker job when executed_notebooks is empty.

    Even if processed_files has a matching entry, replaying the cached HTML
    without populating executed_notebooks would leave Stage 4 consumers
    forced to re-execute the notebook. The guard must detect the cold
    execution cache and skip the short-circuit.
    """
    from clm.infrastructure.messaging.base_classes import Result

    class MockResult(Result):
        data: bytes = b"<html>cached</html>"

        def result_bytes(self) -> bytes:
            return self.data

        def output_metadata(self) -> str:
            return "recording:python:en:html"

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        skip_worker_check=True,
    )

    try:
        mock_result = MockResult(
            correlation_id="test",
            output_file="output/test.html",
            input_file="test.py",
            content_hash="abc123",
        )
        backend.db_manager = _make_mock_db_manager(temp_cache_db, mock_result)

        operation = MockOperation(service_name_value="notebook-processor")
        payload = _make_recording_html_payload()

        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        await backend.execute_operation(operation, payload)

        # Guard fired: job was submitted, no cached file was written.
        assert len(backend.active_jobs) == 1
        assert not output_path.exists()
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_recording_html_cache_replay_proceeds_when_executed_notebooks_warm(
    temp_db, temp_workspace, temp_cache_db
):
    """Recording HTML must short-circuit when executed_notebooks has the entry."""
    from nbformat.v4 import new_code_cell, new_notebook

    from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
    from clm.infrastructure.messaging.base_classes import Result

    class MockResult(Result):
        data: bytes = b"<html>cached</html>"

        def result_bytes(self) -> bytes:
            return self.data

        def output_metadata(self) -> str:
            return "recording:python:en:html"

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        skip_worker_check=True,
    )

    try:
        mock_result = MockResult(
            correlation_id="test",
            output_file="output/test.html",
            input_file="test.py",
            content_hash="abc123",
        )
        backend.db_manager = _make_mock_db_manager(temp_cache_db, mock_result)

        operation = MockOperation(service_name_value="notebook-processor")
        payload = _make_recording_html_payload()

        # Pre-populate executed_notebooks for this payload's execution hash.
        nb = new_notebook(cells=[new_code_cell("x = 1")])
        with ExecutedNotebookCache(temp_cache_db) as nb_cache:
            nb_cache.store(
                input_file=payload.input_file,
                content_hash=payload.execution_cache_hash(),
                language=payload.language,
                prog_lang=payload.prog_lang,
                executed_notebook=nb,
            )

        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        await backend.execute_operation(operation, payload)

        # Fast path: no job submitted, cached HTML written to disk.
        assert len(backend.active_jobs) == 0
        assert output_path.exists()
        assert output_path.read_bytes() == b"<html>cached</html>"
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_completed_html_cache_replay_proceeds_without_executed_notebooks_peek(
    temp_db, temp_workspace, temp_cache_db
):
    """Non-Recording notebook payloads must not trigger the executed_notebooks peek.

    Completed HTML is a consumer of executed_notebooks, not a producer.
    Its processed_files entry already represents a finalized HTML output
    that is safe to replay regardless of executed_notebooks state.
    """
    from clm.infrastructure.messaging.base_classes import Result
    from clm.infrastructure.messaging.notebook_classes import NotebookPayload

    class MockResult(Result):
        data: bytes = b"<html>completed</html>"

        def result_bytes(self) -> bytes:
            return self.data

        def output_metadata(self) -> str:
            return "completed:python:en:html"

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        skip_worker_check=True,
    )

    try:
        mock_result = MockResult(
            correlation_id="test",
            output_file="output/test.html",
            input_file="test.py",
            content_hash="abc123",
        )
        backend.db_manager = _make_mock_db_manager(temp_cache_db, mock_result)

        operation = MockOperation(service_name_value="notebook-processor")
        payload = NotebookPayload(
            data="x = 1",
            input_file="test.py",
            input_file_name="test.py",
            output_file="output/test.html",
            correlation_id="test-cid",
            kind="completed",
            prog_lang="python",
            language="en",
            format="html",
        )

        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        await backend.execute_operation(operation, payload)

        # No peek into executed_notebooks; fast path taken.
        assert len(backend.active_jobs) == 0
        assert output_path.exists()
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_non_notebook_payload_cache_replay_proceeds(temp_db, temp_workspace, temp_cache_db):
    """Non-notebook payloads (drawio, plantuml) must short-circuit without peeking.

    The executed_notebooks invariant is a notebook-worker concern only;
    image converter payloads must keep using the existing fast path.
    """
    from clm.infrastructure.messaging.base_classes import Result

    class MockResult(Result):
        data: bytes = b"<png-bytes>"

        def result_bytes(self) -> bytes:
            return self.data

        def output_metadata(self) -> str:
            return "image"

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        skip_worker_check=True,
    )

    try:
        mock_result = MockResult(
            correlation_id="test",
            output_file="output/test.png",
            input_file="test.drawio",
            content_hash="abc123",
        )
        backend.db_manager = _make_mock_db_manager(temp_cache_db, mock_result)

        operation = MockOperation(service_name_value="drawio-converter")
        # Use the plain MockPayload (a non-NotebookPayload Payload subclass).
        payload = MockPayload(
            input_file="test.drawio",
            input_file_name="test.drawio",
            output_file="output/test.png",
        )

        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        await backend.execute_operation(operation, payload)

        # Fast path: no job submitted.
        assert len(backend.active_jobs) == 0
        assert output_path.exists()
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_speaker_alias_triggers_executed_notebooks_check(
    temp_db, temp_workspace, temp_cache_db
):
    """The deprecated 'speaker' kind must be treated like 'recording'.

    Course specs from older versions may still emit 'speaker' even though
    spec parsing normalizes it to 'recording'. The guard handles both names
    so any code path that bypasses normalization still gets the invariant.
    """
    from clm.infrastructure.messaging.base_classes import Result
    from clm.infrastructure.messaging.notebook_classes import NotebookPayload

    class MockResult(Result):
        data: bytes = b"<html>cached</html>"

        def result_bytes(self) -> bytes:
            return self.data

        def output_metadata(self) -> str:
            return "speaker:python:en:html"

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        skip_worker_check=True,
    )

    try:
        mock_result = MockResult(
            correlation_id="test",
            output_file="output/test.html",
            input_file="test.py",
            content_hash="abc123",
        )
        backend.db_manager = _make_mock_db_manager(temp_cache_db, mock_result)

        operation = MockOperation(service_name_value="notebook-processor")
        payload = NotebookPayload(
            data="x = 1",
            input_file="test.py",
            input_file_name="test.py",
            output_file="output/test.html",
            correlation_id="test-cid",
            kind="speaker",  # Deprecated alias.
            prog_lang="python",
            language="en",
            format="html",
        )

        await backend.execute_operation(operation, payload)

        # executed_notebooks is empty: guard fires for 'speaker' too.
        assert len(backend.active_jobs) == 1
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


# ---------------------------------------------------------------------------
# --explain-rebuilds: log WHY a deck missed the cache and is being rebuilt
# ---------------------------------------------------------------------------


def test_format_rebuild_reason_no_entry():
    reason = SqliteBackend._format_rebuild_reason("no_entry", None, MockPayload())
    assert "no cache entry" in reason


def test_format_rebuild_reason_hash_mismatch():
    payload = MockPayload(data="abc")
    reason = SqliteBackend._format_rebuild_reason("hash_mismatch", "deadbeefcafe0000", payload)
    assert "content hash changed" in reason
    assert "deadbeefcafe" in reason  # cached short hash
    assert payload.content_hash()[:12] in reason  # current short hash


def test_format_rebuild_reason_metadata_mismatch():
    payload = MockPayload()
    reason = SqliteBackend._format_rebuild_reason("metadata_mismatch", None, payload)
    assert "output target" in reason
    assert payload.output_metadata() in reason  # "default"


@pytest.mark.asyncio
async def test_explain_rebuild_reports_formatted_reason(temp_db, temp_workspace, temp_cache_db):
    """_explain_rebuild turns a diagnose verdict into a reason and reports it."""
    reporter = Mock()
    db_manager = _make_mock_db_manager(temp_cache_db, None)
    db_manager.diagnose_cache_miss.return_value = ("hash_mismatch", "0123456789abcdef")

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        db_manager=db_manager,
        build_reporter=reporter,
        explain_rebuilds=True,
        skip_worker_check=True,
    )
    try:
        payload = MockPayload(data="content")
        backend._explain_rebuild(payload, "notebook")

        db_manager.diagnose_cache_miss.assert_called_once()
        reporter.report_rebuild_reason.assert_called_once()
        file_arg, job_arg, reason_arg, code_arg = reporter.report_rebuild_reason.call_args.args
        assert file_arg == payload.input_file
        assert job_arg == "notebook"
        assert "content hash changed" in reason_arg
        assert "0123456789ab" in reason_arg
        assert code_arg == "hash_mismatch"  # machine code for summary aggregation
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_explain_rebuilds_triggers_on_cache_miss(temp_db, temp_workspace, temp_cache_db):
    """A processed_files miss under --explain-rebuilds probes for a reason and still submits."""
    reporter = Mock()
    db_manager = _make_mock_db_manager(temp_cache_db, None)  # get_result -> None (miss)
    db_manager.diagnose_cache_miss.return_value = ("no_entry", None)

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        db_manager=db_manager,
        build_reporter=reporter,
        explain_rebuilds=True,
        skip_worker_check=True,
    )
    try:
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload(data="fresh content")
        await backend.execute_operation(operation, payload)

        db_manager.diagnose_cache_miss.assert_called_once()
        reporter.report_rebuild_reason.assert_called_once()
        assert "no cache entry" in reporter.report_rebuild_reason.call_args.args[2]
        assert reporter.report_rebuild_reason.call_args.args[3] == "no_entry"
        # The reason is diagnostic only — the job is still submitted.
        assert len(backend.active_jobs) == 1
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_explain_rebuilds_off_runs_no_probe(temp_db, temp_workspace, temp_cache_db):
    """Default build: a cache miss must NOT run the diagnostic probe (no slowdown)."""
    db_manager = _make_mock_db_manager(temp_cache_db, None)  # get_result -> None (miss)

    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        db_manager=db_manager,
        skip_worker_check=True,
        # explain_rebuilds defaults to False
    )
    try:
        operation = MockOperation(service_name_value="notebook-processor")
        payload = MockPayload(data="fresh content")
        await backend.execute_operation(operation, payload)

        db_manager.diagnose_cache_miss.assert_not_called()
        assert len(backend.active_jobs) == 1
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()
