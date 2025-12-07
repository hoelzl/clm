"""Tests for PlantUML worker module.

This module tests the PlantUML worker implementation including:
- Worker initialization and configuration
- Job processing (both success and error cases)
- Cancellation detection
- Main function entry point
"""

import gc
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clx.infrastructure.database.job_queue import Job, JobQueue
from clx.infrastructure.database.schema import init_database


# Check if PlantUML module can be imported (JAR must exist)
def _can_import_plantuml():
    """Check if plantuml_converter can be imported."""
    try:
        from clx.workers.plantuml import plantuml_converter

        return True
    except (FileNotFoundError, ImportError):
        return False


# Try to find the PlantUML JAR
def _find_plantuml_jar():
    """Find PlantUML JAR in known locations."""
    possible_paths = [
        Path(__file__).parents[4] / "docker" / "plantuml" / "plantuml-1.2024.6.jar",
        Path(__file__).parents[4] / "plantuml-1.2024.6.jar",
    ]
    for path in possible_paths:
        if path.exists():
            return str(path)
    return None


# Set the environment variable if needed
_jar_path = _find_plantuml_jar()
if _jar_path and not _can_import_plantuml():
    os.environ["PLANTUML_JAR"] = _jar_path

HAS_PLANTUML = _can_import_plantuml()


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Close all connections and clean up WAL files on Windows
    gc.collect()

    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass

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
    with JobQueue(db_path) as queue:
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("plantuml", "test-container", "idle"),
        )
        worker_id = cursor.lastrowid
        conn.commit()
        return worker_id


class TestPlantUmlWorkerInit:
    """Test PlantUmlWorker initialization."""

    def test_worker_initializes_correctly(self, worker_id, db_path):
        """Worker should initialize with correct attributes."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        worker = PlantUmlWorker(worker_id, db_path)

        assert worker.worker_id == worker_id
        assert worker.worker_type == "plantuml"
        assert worker.db_path == db_path
        assert worker.job_queue is not None
        assert worker.running is True

    def test_worker_has_correct_type(self, worker_id, db_path):
        """Worker should have worker_type 'plantuml'."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        worker = PlantUmlWorker(worker_id, db_path)
        assert worker.worker_type == "plantuml"


class TestPlantUmlWorkerProcessJob:
    """Test job processing functionality."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_PLANTUML, reason="PlantUML JAR not available")
    async def test_process_job_async_reads_input_file(self, worker_id, db_path, tmp_path):
        """Processing should read the input file."""
        from clx.workers.plantuml.plantuml_converter import (
            convert_plantuml,
            get_plantuml_output_name,
        )
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        # Create input file
        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice: Hello\n@enduml")

        output_file = tmp_path / "diagram.png"

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.return_value = None

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "plantuml"

                # Mock the temp directory to create output file
                with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                    real_tmp = tmp_path / "temp_dir"
                    real_tmp.mkdir()
                    mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(real_tmp))
                    mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                    # Create the expected output file
                    expected_output = real_tmp / "plantuml.png"
                    expected_output.write_bytes(b"PNG data")

                    await worker._process_job_async(job)

                    mock_convert.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_job_async_handles_missing_file(self, worker_id, db_path):
        """Should raise FileNotFoundError for missing input file."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        job = Job(
            id=1,
            job_type="plantuml",
            input_file="/nonexistent/file.pu",
            output_file="/output/file.png",
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        with pytest.raises(FileNotFoundError, match="Input file not found"):
            await worker._process_job_async(job)

    @pytest.mark.asyncio
    async def test_process_job_async_detects_cancellation(self, worker_id, db_path, tmp_path):
        """Should detect cancelled jobs before processing."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(tmp_path / "output.png"),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        # Mock cancellation - converter should not be called
        with patch.object(worker.job_queue, "is_job_cancelled", return_value=True):
            await worker._process_job_async(job)
            # If cancelled, method returns early without calling converter

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_PLANTUML, reason="PlantUML JAR not available")
    async def test_process_job_async_determines_output_format(self, worker_id, db_path, tmp_path):
        """Should determine output format from file extension."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        # Test SVG format
        output_file = tmp_path / "output.svg"

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.return_value = None

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "diagram"

                with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                    real_tmp = tmp_path / "temp_dir"
                    real_tmp.mkdir()
                    mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(real_tmp))
                    mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                    expected_output = real_tmp / "diagram.svg"
                    expected_output.write_bytes(b"SVG data")

                    await worker._process_job_async(job)

                    # Converter should be called
                    mock_convert.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_PLANTUML, reason="PlantUML JAR not available")
    async def test_process_job_async_default_format_png(self, worker_id, db_path, tmp_path):
        """Should default to PNG format when no extension."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        # Output file without extension
        output_file = tmp_path / "output"

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.return_value = None

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "diagram"

                with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                    real_tmp = tmp_path / "temp_dir2"
                    real_tmp.mkdir()
                    mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(real_tmp))
                    mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                    # Default format is PNG
                    expected_output = real_tmp / "diagram.png"
                    expected_output.write_bytes(b"PNG data")

                    await worker._process_job_async(job)

                    mock_convert.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_PLANTUML, reason="PlantUML JAR not available")
    async def test_process_job_async_raises_on_empty_result(self, worker_id, db_path, tmp_path):
        """Should raise ValueError when conversion produces empty result."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        output_file = tmp_path / "output.png"

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.return_value = None

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "diagram"

                with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                    real_tmp = tmp_path / "temp_dir3"
                    real_tmp.mkdir()
                    mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(real_tmp))
                    mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                    # Create empty output file
                    expected_output = real_tmp / "diagram.png"
                    expected_output.write_bytes(b"")

                    with pytest.raises(ValueError, match="Conversion produced empty result"):
                        await worker._process_job_async(job)

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_PLANTUML, reason="PlantUML JAR not available")
    async def test_process_job_async_raises_on_missing_output(self, worker_id, db_path, tmp_path):
        """Should raise FileNotFoundError when conversion doesn't produce output."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        output_file = tmp_path / "output.png"

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.return_value = None

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "diagram"

                with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                    real_tmp = tmp_path / "temp_dir4"
                    real_tmp.mkdir()
                    mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(real_tmp))
                    mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                    # Don't create output file - simulate conversion failure
                    with pytest.raises(
                        FileNotFoundError, match="Conversion did not produce expected output"
                    ):
                        await worker._process_job_async(job)

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_PLANTUML, reason="PlantUML JAR not available")
    async def test_process_job_async_writes_output_file(self, worker_id, db_path, tmp_path):
        """Should write output file with conversion result."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        output_dir = tmp_path / "output_dir"
        output_file = output_dir / "output.png"

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)
        result_bytes = b"PNG image data here"

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.return_value = None

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "diagram"

                with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                    real_tmp = tmp_path / "temp_dir5"
                    real_tmp.mkdir()
                    mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(real_tmp))
                    mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                    expected_output = real_tmp / "diagram.png"
                    expected_output.write_bytes(result_bytes)

                    await worker._process_job_async(job)

                    # Output directory should be created
                    assert output_dir.exists()

                    # Output file should be written
                    assert output_file.exists()
                    assert output_file.read_bytes() == result_bytes

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_PLANTUML, reason="PlantUML JAR not available")
    async def test_process_job_async_adds_to_cache(self, worker_id, db_path, tmp_path):
        """Should add result to cache after processing."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        output_file = tmp_path / "output.png"

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.return_value = None

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "diagram"

                with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                    real_tmp = tmp_path / "temp_dir6"
                    real_tmp.mkdir()
                    mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(real_tmp))
                    mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                    expected_output = real_tmp / "diagram.png"
                    expected_output.write_bytes(b"PNG data")

                    with patch.object(worker.job_queue, "add_to_cache") as mock_cache:
                        await worker._process_job_async(job)

                        mock_cache.assert_called_once()
                        call_args = mock_cache.call_args
                        assert call_args[0][0] == str(output_file)
                        assert call_args[0][1] == "test-hash"

    def test_process_job_uses_event_loop(self, worker_id, db_path, tmp_path):
        """process_job should use persistent event loop."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        job = Job(
            id=1,
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(tmp_path / "output.png"),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

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
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        job = Job(
            id=1,
            job_type="plantuml",
            input_file="/nonexistent/file.pu",
            output_file=str(tmp_path / "output.png"),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = PlantUmlWorker(worker_id, db_path)

        with pytest.raises(FileNotFoundError):
            worker.process_job(job)


class TestPlantUmlWorkerMain:
    """Test the main() entry point."""

    def test_main_creates_database_if_missing(self, tmp_path):
        """main() should initialize database if it doesn't exist."""
        from clx.workers.plantuml import plantuml_worker

        db_path = tmp_path / "new_db.db"
        assert not db_path.exists()

        with patch.object(plantuml_worker, "DB_PATH", db_path):
            with patch.object(plantuml_worker, "init_database") as mock_init:
                with patch.object(
                    plantuml_worker.Worker, "register_worker_with_retry", return_value=1
                ):
                    with patch.object(plantuml_worker, "PlantUmlWorker") as mock_worker_class:
                        mock_worker = MagicMock()
                        mock_worker.run.side_effect = KeyboardInterrupt()
                        mock_worker_class.return_value = mock_worker

                        plantuml_worker.main()

                        mock_init.assert_called_once_with(db_path)

    def test_main_registers_worker(self, tmp_path):
        """main() should register worker with database."""
        from clx.workers.plantuml import plantuml_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(plantuml_worker, "DB_PATH", db_path):
            with patch.object(
                plantuml_worker.Worker, "register_worker_with_retry", return_value=1
            ) as mock_register:
                with patch.object(plantuml_worker, "PlantUmlWorker") as mock_worker_class:
                    mock_worker = MagicMock()
                    mock_worker.run.side_effect = KeyboardInterrupt()
                    mock_worker_class.return_value = mock_worker

                    try:
                        plantuml_worker.main()
                    except (KeyboardInterrupt, SystemExit):
                        pass

                    mock_register.assert_called_once_with(db_path, "plantuml")

    def test_main_handles_keyboard_interrupt(self, tmp_path):
        """main() should handle keyboard interrupt gracefully."""
        from clx.workers.plantuml import plantuml_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(plantuml_worker, "DB_PATH", db_path):
            with patch.object(plantuml_worker.Worker, "register_worker_with_retry", return_value=1):
                with patch.object(plantuml_worker, "PlantUmlWorker") as mock_worker_class:
                    mock_worker = MagicMock()
                    mock_worker.run.side_effect = KeyboardInterrupt()
                    mock_worker_class.return_value = mock_worker

                    plantuml_worker.main()

                    mock_worker.stop.assert_called_once()
                    mock_worker.cleanup.assert_called_once()

    def test_main_handles_worker_crash(self, tmp_path):
        """main() should handle worker crash and re-raise."""
        from clx.workers.plantuml import plantuml_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(plantuml_worker, "DB_PATH", db_path):
            with patch.object(plantuml_worker.Worker, "register_worker_with_retry", return_value=1):
                with patch.object(plantuml_worker, "PlantUmlWorker") as mock_worker_class:
                    mock_worker = MagicMock()
                    mock_worker.run.side_effect = RuntimeError("Worker crashed")
                    mock_worker_class.return_value = mock_worker

                    with pytest.raises(RuntimeError, match="Worker crashed"):
                        plantuml_worker.main()

                    mock_worker.cleanup.assert_called_once()


@pytest.mark.skipif(not HAS_PLANTUML, reason="PlantUML JAR not available")
class TestPlantUmlWorkerIntegration:
    """Integration tests for PlantUML worker."""

    def test_worker_processes_plantuml_job(self, worker_id, db_path, tmp_path):
        """Worker should process a PlantUML conversion job end-to-end."""
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        # Create input file
        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml diagram\nBob -> Alice: Hello\n@enduml")

        output_file = tmp_path / "diagram.png"

        # Add job to queue
        queue = JobQueue(db_path)
        job_id = queue.add_job(
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
        )
        queue.close()

        # Create worker
        worker = PlantUmlWorker(worker_id, db_path)

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.return_value = None

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "diagram"

                with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                    real_tmp = tmp_path / "temp_int"
                    real_tmp.mkdir()
                    mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(real_tmp))
                    mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                    expected_output = real_tmp / "diagram.png"
                    expected_output.write_bytes(b"PNG image data")

                    # Run worker in thread
                    thread = threading.Thread(target=worker.run)
                    thread.start()

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
        from clx.workers.plantuml.plantuml_worker import PlantUmlWorker

        input_file = tmp_path / "diagram.pu"
        input_file.write_text("@startuml\nBob -> Alice\n@enduml")

        output_file = tmp_path / "diagram.png"

        # Add job to queue
        queue = JobQueue(db_path)
        job_id = queue.add_job(
            job_type="plantuml",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={},
        )
        queue.close()

        # Create worker
        worker = PlantUmlWorker(worker_id, db_path)

        with patch("clx.workers.plantuml.plantuml_converter.convert_plantuml") as mock_convert:
            mock_convert.side_effect = RuntimeError("Conversion failed")

            with patch(
                "clx.workers.plantuml.plantuml_converter.get_plantuml_output_name"
            ) as mock_name:
                mock_name.return_value = "diagram"

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


class TestPlantUmlWorkerConfiguration:
    """Test worker configuration."""

    def test_log_level_from_environment(self):
        """LOG_LEVEL should be read from environment."""
        from clx.workers.plantuml import plantuml_worker

        assert hasattr(plantuml_worker, "LOG_LEVEL")

    def test_db_path_from_environment(self):
        """DB_PATH should be read from environment."""
        from clx.workers.plantuml import plantuml_worker

        assert hasattr(plantuml_worker, "DB_PATH")
        assert isinstance(plantuml_worker.DB_PATH, Path)
