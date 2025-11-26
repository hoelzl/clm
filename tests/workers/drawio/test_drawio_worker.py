"""Tests for DrawIO worker module.

This module tests the DrawIO worker implementation including:
- Worker initialization and configuration
- Job processing (both success and error cases)
- Cancellation detection
- Main function entry point
"""

import gc
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clx.infrastructure.database.job_queue import Job, JobQueue
from clx.infrastructure.database.schema import init_database


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Close all connections and clean up WAL files on Windows
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
        for suffix in ["-wal", "-shm"]:
            wal_file = Path(str(path) + suffix)
            wal_file.unlink(missing_ok=True)
    except PermissionError:
        time.sleep(0.1)
        try:
            path.unlink(missing_ok=True)
            for suffix in ["-wal", "-shm"]:
                wal_file = Path(str(path) + suffix)
                wal_file.unlink(missing_ok=True)
        except Exception:
            pass


@pytest.fixture
def worker_id(db_path):
    """Register a test worker and return its ID."""
    queue = JobQueue(db_path)
    conn = queue._get_conn()
    cursor = conn.execute(
        "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
        ("drawio", "test-container", "idle"),
    )
    worker_id = cursor.lastrowid
    conn.commit()
    queue.close()
    return worker_id


class TestDrawioWorkerInit:
    """Test DrawioWorker initialization."""

    def test_worker_initializes_correctly(self, worker_id, db_path):
        """Worker should initialize with correct attributes."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        worker = DrawioWorker(worker_id, db_path)

        assert worker.worker_id == worker_id
        assert worker.worker_type == "drawio"
        assert worker.db_path == db_path
        assert worker.job_queue is not None
        assert worker.running is True

    def test_worker_has_correct_type(self, worker_id, db_path):
        """Worker should have worker_type 'drawio'."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        worker = DrawioWorker(worker_id, db_path)
        assert worker.worker_type == "drawio"


class TestDrawioWorkerProcessJob:
    """Test job processing functionality."""

    @pytest.mark.asyncio
    async def test_process_job_async_reads_input_file(self, worker_id, db_path, tmp_path):
        """Processing should read the input file."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        # Create input file
        input_file = tmp_path / "input.drawio"
        input_file.write_text("<mxfile>test content</mxfile>")

        output_file = tmp_path / "output.png"

        # Create job
        job = Job(
            id=1,
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
            mock_convert.return_value = None

            # Mock aiofiles to write some output
            with patch("aiofiles.open") as mock_aiofiles_open:
                mock_file = MagicMock()
                mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                mock_file.__aexit__ = AsyncMock(return_value=None)
                mock_file.write = AsyncMock()
                mock_file.read = AsyncMock(return_value=b"PNG output data")
                mock_aiofiles_open.return_value = mock_file

                await worker._process_job_async(job)

                # Verify converter was called
                mock_convert.assert_called_once()
                call_args = mock_convert.call_args
                assert "test content" in str(call_args) or mock_convert.called

    @pytest.mark.asyncio
    async def test_process_job_async_handles_missing_file(self, worker_id, db_path):
        """Should raise FileNotFoundError for missing input file."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        job = Job(
            id=1,
            job_type="drawio",
            input_file="/nonexistent/file.drawio",
            output_file="/output/file.png",
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        with pytest.raises(FileNotFoundError, match="Input file not found"):
            await worker._process_job_async(job)

    @pytest.mark.asyncio
    async def test_process_job_async_detects_cancellation(self, worker_id, db_path, tmp_path):
        """Should detect cancelled jobs before processing."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        input_file = tmp_path / "input.drawio"
        input_file.write_text("<mxfile>content</mxfile>")

        job = Job(
            id=1,
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(tmp_path / "output.png"),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        # Mock cancellation check to return True
        with patch.object(worker.job_queue, "is_job_cancelled", return_value=True):
            with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
                await worker._process_job_async(job)

                # Converter should not be called for cancelled jobs
                mock_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_job_async_determines_output_format(self, worker_id, db_path, tmp_path):
        """Should determine output format from file extension."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        input_file = tmp_path / "input.drawio"
        input_file.write_text("<mxfile>content</mxfile>")

        # Test SVG format
        output_file = tmp_path / "output.svg"

        job = Job(
            id=1,
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
            mock_convert.return_value = None

            with patch("aiofiles.open") as mock_aiofiles_open:
                mock_file = MagicMock()
                mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                mock_file.__aexit__ = AsyncMock(return_value=None)
                mock_file.write = AsyncMock()
                mock_file.read = AsyncMock(return_value=b"SVG output")
                mock_aiofiles_open.return_value = mock_file

                await worker._process_job_async(job)

                # Verify SVG format was passed
                call_args = mock_convert.call_args
                assert call_args[0][2] == "svg"

    @pytest.mark.asyncio
    async def test_process_job_async_default_format_png(self, worker_id, db_path, tmp_path):
        """Should default to PNG format when no extension."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        input_file = tmp_path / "input.drawio"
        input_file.write_text("<mxfile>content</mxfile>")

        # Output file without extension
        output_file = tmp_path / "output"

        job = Job(
            id=1,
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
            mock_convert.return_value = None

            with patch("aiofiles.open") as mock_aiofiles_open:
                mock_file = MagicMock()
                mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                mock_file.__aexit__ = AsyncMock(return_value=None)
                mock_file.write = AsyncMock()
                mock_file.read = AsyncMock(return_value=b"PNG output")
                mock_aiofiles_open.return_value = mock_file

                await worker._process_job_async(job)

                # Verify PNG format was used as default
                call_args = mock_convert.call_args
                assert call_args[0][2] == "png"

    @pytest.mark.asyncio
    async def test_process_job_async_raises_on_empty_result(self, worker_id, db_path, tmp_path):
        """Should raise ValueError when conversion produces empty result."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        input_file = tmp_path / "input.drawio"
        input_file.write_text("<mxfile>content</mxfile>")

        output_file = tmp_path / "output.png"

        job = Job(
            id=1,
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
            mock_convert.return_value = None

            with patch("aiofiles.open") as mock_aiofiles_open:
                mock_file = MagicMock()
                mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                mock_file.__aexit__ = AsyncMock(return_value=None)
                mock_file.write = AsyncMock()
                mock_file.read = AsyncMock(return_value=b"")  # Empty result
                mock_aiofiles_open.return_value = mock_file

                with pytest.raises(ValueError, match="Conversion produced empty result"):
                    await worker._process_job_async(job)

    @pytest.mark.asyncio
    async def test_process_job_async_writes_output_file(self, worker_id, db_path, tmp_path):
        """Should write output file with conversion result."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        input_file = tmp_path / "input.drawio"
        input_file.write_text("<mxfile>content</mxfile>")

        output_dir = tmp_path / "output_dir"
        output_file = output_dir / "output.png"

        job = Job(
            id=1,
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)
        result_bytes = b"PNG image data here"

        with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
            mock_convert.return_value = None

            with patch("aiofiles.open") as mock_aiofiles_open:
                mock_file = MagicMock()
                mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                mock_file.__aexit__ = AsyncMock(return_value=None)
                mock_file.write = AsyncMock()
                mock_file.read = AsyncMock(return_value=result_bytes)
                mock_aiofiles_open.return_value = mock_file

                await worker._process_job_async(job)

                # Output directory should be created
                assert output_dir.exists()

                # Output file should be written
                assert output_file.exists()
                assert output_file.read_bytes() == result_bytes

    @pytest.mark.asyncio
    async def test_process_job_async_adds_to_cache(self, worker_id, db_path, tmp_path):
        """Should add result to cache after processing."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        input_file = tmp_path / "input.drawio"
        input_file.write_text("<mxfile>content</mxfile>")

        output_file = tmp_path / "output.png"

        job = Job(
            id=1,
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
            mock_convert.return_value = None

            with patch("aiofiles.open") as mock_aiofiles_open:
                mock_file = MagicMock()
                mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                mock_file.__aexit__ = AsyncMock(return_value=None)
                mock_file.write = AsyncMock()
                mock_file.read = AsyncMock(return_value=b"PNG data")
                mock_aiofiles_open.return_value = mock_file

                with patch.object(worker.job_queue, "add_to_cache") as mock_cache:
                    await worker._process_job_async(job)

                    # Verify cache was called
                    mock_cache.assert_called_once()
                    call_args = mock_cache.call_args
                    assert call_args[0][0] == str(output_file)
                    assert call_args[0][1] == "test-hash"

    def test_process_job_uses_event_loop(self, worker_id, db_path, tmp_path):
        """process_job should use persistent event loop."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        input_file = tmp_path / "input.drawio"
        input_file.write_text("<mxfile>content</mxfile>")

        job = Job(
            id=1,
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(tmp_path / "output.png"),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        with patch.object(worker, "_process_job_async", new_callable=AsyncMock) as mock_async:
            with patch.object(worker, "_get_or_create_loop") as mock_loop:
                mock_event_loop = MagicMock()
                # Consume the coroutine to avoid "coroutine was never awaited" warning
                mock_event_loop.run_until_complete.side_effect = lambda coro: coro.close()
                mock_loop.return_value = mock_event_loop

                worker.process_job(job)

                mock_loop.assert_called_once()
                mock_event_loop.run_until_complete.assert_called_once()

    def test_process_job_propagates_errors(self, worker_id, db_path, tmp_path):
        """process_job should propagate errors from async processing."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        job = Job(
            id=1,
            job_type="drawio",
            input_file="/nonexistent/file.drawio",
            output_file=str(tmp_path / "output.png"),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = DrawioWorker(worker_id, db_path)

        with pytest.raises(FileNotFoundError):
            worker.process_job(job)


class TestDrawioWorkerMain:
    """Test the main() entry point."""

    def test_main_creates_database_if_missing(self, tmp_path):
        """main() should initialize database if it doesn't exist."""
        from clx.workers.drawio import drawio_worker

        db_path = tmp_path / "new_db.db"
        assert not db_path.exists()

        with patch.object(drawio_worker, "DB_PATH", db_path):
            with patch.object(drawio_worker, "init_database") as mock_init:
                with patch.object(
                    drawio_worker.Worker, "register_worker_with_retry", return_value=1
                ):
                    with patch.object(drawio_worker, "DrawioWorker") as mock_worker_class:
                        mock_worker = MagicMock()
                        mock_worker.run.side_effect = KeyboardInterrupt()
                        mock_worker_class.return_value = mock_worker

                        drawio_worker.main()

                        # Database initialization should be called for missing database
                        mock_init.assert_called_once_with(db_path)

    def test_main_registers_worker(self, tmp_path):
        """main() should register worker with database."""
        from clx.workers.drawio import drawio_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(drawio_worker, "DB_PATH", db_path):
            with patch.object(
                drawio_worker.Worker, "register_worker_with_retry", return_value=1
            ) as mock_register:
                with patch.object(drawio_worker, "DrawioWorker") as mock_worker_class:
                    mock_worker = MagicMock()
                    mock_worker.run.side_effect = KeyboardInterrupt()
                    mock_worker_class.return_value = mock_worker

                    try:
                        drawio_worker.main()
                    except (KeyboardInterrupt, SystemExit):
                        pass

                    mock_register.assert_called_once_with(db_path, "drawio")

    def test_main_handles_keyboard_interrupt(self, tmp_path):
        """main() should handle keyboard interrupt gracefully."""
        from clx.workers.drawio import drawio_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(drawio_worker, "DB_PATH", db_path):
            with patch.object(drawio_worker.Worker, "register_worker_with_retry", return_value=1):
                with patch.object(drawio_worker, "DrawioWorker") as mock_worker_class:
                    mock_worker = MagicMock()
                    mock_worker.run.side_effect = KeyboardInterrupt()
                    mock_worker_class.return_value = mock_worker

                    # Should not raise
                    drawio_worker.main()

                    # Worker should be stopped and cleaned up
                    mock_worker.stop.assert_called_once()
                    mock_worker.cleanup.assert_called_once()

    def test_main_handles_worker_crash(self, tmp_path):
        """main() should handle worker crash and re-raise."""
        from clx.workers.drawio import drawio_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(drawio_worker, "DB_PATH", db_path):
            with patch.object(drawio_worker.Worker, "register_worker_with_retry", return_value=1):
                with patch.object(drawio_worker, "DrawioWorker") as mock_worker_class:
                    mock_worker = MagicMock()
                    mock_worker.run.side_effect = RuntimeError("Worker crashed")
                    mock_worker_class.return_value = mock_worker

                    with pytest.raises(RuntimeError, match="Worker crashed"):
                        drawio_worker.main()

                    # Cleanup should still be called
                    mock_worker.cleanup.assert_called_once()


class TestDrawioWorkerIntegration:
    """Integration tests for DrawIO worker."""

    def test_worker_processes_drawio_job(self, worker_id, db_path, tmp_path):
        """Worker should process a DrawIO conversion job end-to-end."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        # Create input file
        input_file = tmp_path / "diagram.drawio"
        input_file.write_text("<mxfile><diagram>test</diagram></mxfile>")

        output_file = tmp_path / "diagram.png"

        # Add job to queue
        queue = JobQueue(db_path)
        job_id = queue.add_job(
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
        )
        queue.close()

        # Create worker
        worker = DrawioWorker(worker_id, db_path)

        # Mock the converter
        with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
            mock_convert.return_value = None

            with patch("aiofiles.open") as mock_aiofiles_open:
                mock_file = MagicMock()
                mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                mock_file.__aexit__ = AsyncMock(return_value=None)
                mock_file.write = AsyncMock()
                mock_file.read = AsyncMock(return_value=b"PNG image data")
                mock_aiofiles_open.return_value = mock_file

                # Run worker in thread
                thread = threading.Thread(target=worker.run)
                thread.start()

                # Wait for job to process
                time.sleep(0.5)
                worker.stop()
                thread.join(timeout=2)

        # Verify job was completed
        queue = JobQueue(db_path)
        job = queue.get_job(job_id)
        assert job.status == "completed"
        queue.close()

        # Verify output file exists
        assert output_file.exists()

    def test_worker_handles_conversion_error(self, worker_id, db_path, tmp_path):
        """Worker should handle conversion errors properly."""
        from clx.workers.drawio.drawio_worker import DrawioWorker

        # Create input file
        input_file = tmp_path / "diagram.drawio"
        input_file.write_text("<mxfile>test</mxfile>")

        output_file = tmp_path / "diagram.png"

        # Add job to queue
        queue = JobQueue(db_path)
        job_id = queue.add_job(
            job_type="drawio",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
        )
        queue.close()

        # Create worker
        worker = DrawioWorker(worker_id, db_path)

        # Mock converter to raise error
        with patch("clx.workers.drawio.drawio_converter.convert_drawio") as mock_convert:
            mock_convert.side_effect = RuntimeError("Conversion failed")

            with patch("aiofiles.open") as mock_aiofiles_open:
                mock_file = MagicMock()
                mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                mock_file.__aexit__ = AsyncMock(return_value=None)
                mock_file.write = AsyncMock()
                mock_aiofiles_open.return_value = mock_file

                # Run worker in thread
                thread = threading.Thread(target=worker.run)
                thread.start()

                time.sleep(0.5)
                worker.stop()
                thread.join(timeout=2)

        # Verify job failed
        queue = JobQueue(db_path)
        job = queue.get_job(job_id)
        assert job.status == "failed"
        assert "Conversion failed" in job.error
        queue.close()


class TestDrawioWorkerConfiguration:
    """Test worker configuration."""

    def test_log_level_from_environment(self):
        """LOG_LEVEL should be read from environment."""
        from clx.workers.drawio import drawio_worker

        assert hasattr(drawio_worker, "LOG_LEVEL")

    def test_db_path_from_environment(self):
        """DB_PATH should be read from environment."""
        from clx.workers.drawio import drawio_worker

        assert hasattr(drawio_worker, "DB_PATH")
        assert isinstance(drawio_worker.DB_PATH, Path)
