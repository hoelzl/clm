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

from clx.infrastructure.backends.sqlite_backend import SqliteBackend
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.messaging.base_classes import Payload
from clx.infrastructure.operation import Operation


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

    assert backend.job_queue is not None
    assert backend.db_path == temp_db
    assert backend.workspace_path == temp_workspace
    assert backend.active_jobs == {}


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

    operation = MockOperation(service_name_value="notebook-processor")
    payload = MockPayload()

    await backend.execute_operation(operation, payload)

    # Check that job was added
    assert len(backend.active_jobs) == 1

    # Verify job in database
    job_queue = JobQueue(temp_db)
    job = job_queue.get_next_job("notebook")
    assert job is not None
    assert job.input_file == payload.input_file
    assert job.output_file == payload.output_file


@pytest.mark.asyncio
async def test_execute_operation_multiple_job_types(temp_db, temp_workspace):
    """Test execute_operation with different job types."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

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
    notebook_job = job_queue.get_next_job("notebook")
    drawio_job = job_queue.get_next_job("drawio")
    plantuml_job = job_queue.get_next_job("plantuml")

    assert notebook_job is not None
    assert drawio_job is not None
    assert plantuml_job is not None


@pytest.mark.asyncio
async def test_execute_operation_unknown_service(temp_db, temp_workspace):
    """Test that execute_operation raises error for unknown service."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    operation = MockOperation(service_name_value="unknown-service")
    payload = MockPayload()

    with pytest.raises(ValueError, match="Unknown service"):
        await backend.execute_operation(operation, payload)


@pytest.mark.asyncio
async def test_wait_for_completion_no_jobs(temp_db, temp_workspace):
    """Test wait_for_completion when no jobs are active."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    result = await backend.wait_for_completion()
    assert result is True


@pytest.mark.asyncio
async def test_wait_for_completion_successful_jobs(temp_db, temp_workspace):
    """Test wait_for_completion when jobs complete successfully."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

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
        job_queue.update_job_status(job_id, "completed")

    # Start background task
    task = asyncio.create_task(complete_job())

    # Wait for completion
    result = await backend.wait_for_completion()

    await task  # Ensure background task completes
    assert result is True
    assert len(backend.active_jobs) == 0


@pytest.mark.asyncio
async def test_wait_for_completion_failed_job(temp_db, temp_workspace):
    """Test wait_for_completion when a job fails."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    # Add a job
    operation = MockOperation(service_name_value="notebook-processor")
    payload = MockPayload()
    await backend.execute_operation(operation, payload)

    job_id = list(backend.active_jobs.keys())[0]

    # Simulate job failure
    async def fail_job():
        await asyncio.sleep(0.1)
        job_queue = JobQueue(temp_db)
        job_queue.update_job_status(job_id, "failed", error="Test error")

    task = asyncio.create_task(fail_job())

    # Wait for completion (should return False due to failure)
    result = await backend.wait_for_completion()

    await task
    assert result is False
    assert len(backend.active_jobs) == 0


@pytest.mark.asyncio
async def test_wait_for_completion_timeout(temp_db, temp_workspace):
    """Test wait_for_completion timeout behavior."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        max_wait_for_completion_duration=0.5,  # Short timeout
        skip_worker_check=True,  # Unit test - no workers needed
    )

    # Add a job but don't complete it
    operation = MockOperation(service_name_value="notebook-processor")
    payload = MockPayload()
    await backend.execute_operation(operation, payload)

    # Should timeout
    with pytest.raises(TimeoutError, match="did not complete within"):
        await backend.wait_for_completion()


@pytest.mark.asyncio
async def test_sqlite_cache_hit(temp_db, temp_workspace):
    """Test that SQLite cache prevents duplicate job submission."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

    operation = MockOperation(service_name_value="notebook-processor")
    payload = MockPayload()

    # Add job first time
    await backend.execute_operation(operation, payload)
    job_id = list(backend.active_jobs.keys())[0]

    # Complete the job and add to cache
    job_queue = JobQueue(temp_db)
    job_queue.update_job_status(job_id, "completed")
    job_queue.add_to_cache(payload.output_file, payload.content_hash(), {"format": "notebook"})

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


@pytest.mark.asyncio
async def test_database_cache_hit(temp_db, temp_workspace):
    """Test that database manager cache prevents job submission."""
    from clx.infrastructure.database.db_operations import DatabaseManager
    from clx.infrastructure.messaging.base_classes import Result

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

    # Mock database manager with cached result
    backend.db_manager = Mock()
    mock_result = MockResult(
        correlation_id="test", output_file="test.ipynb", input_file="test.py", content_hash="abc123"
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

    # Shutdown should timeout gracefully
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

    await asyncio.gather(*[backend.execute_operation(op, payload) for op, payload in operations])

    # All jobs should be tracked
    assert len(backend.active_jobs) == 10

    # All jobs should be in database
    job_queue = JobQueue(temp_db)
    stats = job_queue.get_job_stats()
    assert stats["pending"] + stats["processing"] == 10


@pytest.mark.asyncio
async def test_poll_interval_respected(temp_db, temp_workspace):
    """Test that polling interval is respected during wait_for_completion."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        poll_interval=0.2,  # 200ms
        skip_worker_check=True,  # Unit test - no workers needed
    )

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
        job_queue.update_job_status(job_id, "completed")

    task = asyncio.create_task(complete_job())

    with patch("asyncio.sleep", side_effect=tracked_sleep):
        await backend.wait_for_completion()

    await task

    # Should have polled at least twice (with 0.2s interval over 0.5s)
    assert len(poll_times) >= 2


@pytest.mark.asyncio
async def test_job_not_found_in_database(temp_db, temp_workspace):
    """Test handling when a job is not found in database."""
    backend = SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        skip_worker_check=True,  # Unit test - no workers needed
    )

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
