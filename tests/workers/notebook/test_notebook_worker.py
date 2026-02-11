"""Tests for Notebook worker module.

This module tests the Notebook worker implementation including:
- Worker initialization and configuration
- Job processing (both success and error cases)
- Cancellation detection
- Cache initialization
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

from clm.infrastructure.database.job_queue import Job, JobQueue
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

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
def cache_db_path(tmp_path):
    """Create a path for the cache database."""
    return tmp_path / "cache.db"


@pytest.fixture
def worker_id(db_path):
    """Register a test worker and return its ID."""
    with JobQueue(db_path) as queue:
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("notebook", "test-container", "idle"),
        )
        worker_id = cursor.lastrowid
        conn.commit()
        return worker_id


class TestNotebookWorkerInit:
    """Test NotebookWorker initialization."""

    def test_worker_initializes_correctly(self, worker_id, db_path):
        """Worker should initialize with correct attributes."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path)

        assert worker.worker_id == worker_id
        assert worker.worker_type == "notebook"
        assert worker.db_path == db_path
        assert worker.job_queue is not None
        assert worker.running is True
        assert worker.cache_db_path is None
        assert worker._cache is None

    def test_worker_has_correct_type(self, worker_id, db_path):
        """Worker should have worker_type 'notebook'."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path)
        assert worker.worker_type == "notebook"

    def test_worker_initializes_with_cache_path(self, worker_id, db_path, cache_db_path):
        """Worker should accept cache_db_path parameter."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path, cache_db_path=cache_db_path)

        assert worker.cache_db_path == cache_db_path
        assert worker._cache is None  # Cache not initialized until first use


class TestNotebookWorkerCache:
    """Test cache functionality."""

    def test_ensure_cache_initialized_returns_none_without_path(self, worker_id, db_path):
        """Should return None if no cache path configured."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path)
        cache = worker._ensure_cache_initialized()
        assert cache is None

    def test_ensure_cache_initialized_creates_cache(self, worker_id, db_path, cache_db_path):
        """Should create cache when path is configured."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path, cache_db_path=cache_db_path)

        with patch("clm.workers.notebook.notebook_worker.ExecutedNotebookCache") as MockCache:
            mock_cache = MagicMock()
            mock_cache.__enter__ = MagicMock(return_value=mock_cache)
            MockCache.return_value = mock_cache

            result = worker._ensure_cache_initialized()

            MockCache.assert_called_once_with(cache_db_path)
            mock_cache.__enter__.assert_called_once()
            assert result == mock_cache

    def test_ensure_cache_initialized_reuses_existing(self, worker_id, db_path, cache_db_path):
        """Should reuse existing cache if already initialized."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path, cache_db_path=cache_db_path)

        with patch("clm.workers.notebook.notebook_worker.ExecutedNotebookCache") as MockCache:
            mock_cache = MagicMock()
            mock_cache.__enter__ = MagicMock(return_value=mock_cache)
            MockCache.return_value = mock_cache

            # First call creates cache
            result1 = worker._ensure_cache_initialized()
            # Second call should reuse
            result2 = worker._ensure_cache_initialized()

            MockCache.assert_called_once()  # Only called once
            assert result1 == result2


class TestNotebookWorkerProcessJob:
    """Test job processing functionality."""

    @pytest.mark.asyncio
    async def test_process_job_async_handles_missing_file(self, worker_id, db_path):
        """Should raise FileNotFoundError for missing input file."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        job = Job(
            id=1,
            job_type="notebook",
            input_file="/nonexistent/notebook.ipynb",
            output_file="/output/notebook.html",
            content_hash="test-hash",
            payload={"kind": "completed", "prog_lang": "python"},
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

        with pytest.raises(FileNotFoundError, match="Input file not found"):
            await worker._process_job_async(job)

    @pytest.mark.asyncio
    async def test_process_job_async_detects_cancellation(self, worker_id, db_path, tmp_path):
        """Should detect cancelled jobs before processing."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        job = Job(
            id=1,
            job_type="notebook",
            input_file=str(input_file),
            output_file=str(tmp_path / "output.html"),
            content_hash="test-hash",
            payload={"kind": "completed"},
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

        with patch.object(worker.job_queue, "is_job_cancelled", return_value=True):
            await worker._process_job_async(job)
            # Should return early without processing

    @pytest.mark.asyncio
    async def test_process_job_async_detects_cancellation_after_read(
        self, worker_id, db_path, tmp_path
    ):
        """Should detect cancellation after reading input file."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        job = Job(
            id=1,
            job_type="notebook",
            input_file=str(input_file),
            output_file=str(tmp_path / "output.html"),
            content_hash="test-hash",
            payload={"kind": "completed"},
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

        # Not cancelled on first check, cancelled on second
        call_count = [0]

        def side_effect(_):
            call_count[0] += 1
            return call_count[0] > 1

        with patch.object(worker.job_queue, "is_job_cancelled", side_effect=side_effect):
            await worker._process_job_async(job)
            # Should return after second cancellation check

    @pytest.mark.asyncio
    async def test_process_job_async_creates_output_spec(self, worker_id, db_path, tmp_path):
        """Should create output spec from payload."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        job = Job(
            id=1,
            job_type="notebook",
            input_file=str(input_file),
            output_file=str(tmp_path / "output.html"),
            content_hash="test-hash",
            payload={
                "kind": "completed",
                "prog_lang": "python",
                "language": "en",
                "format": "html",
            },
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

        with patch("clm.workers.notebook.notebook_worker.create_output_spec") as mock_create_spec:
            mock_spec = MagicMock()
            mock_create_spec.return_value = mock_spec

            with patch("clm.workers.notebook.notebook_worker.NotebookProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_notebook = AsyncMock(return_value="<html>output</html>")
                MockProcessor.return_value = mock_processor

                await worker._process_job_async(job)

                mock_create_spec.assert_called_once_with(
                    kind="completed",
                    prog_lang="python",
                    language="en",
                    format="html",
                )

    @pytest.mark.asyncio
    async def test_process_job_async_processes_notebook(self, worker_id, db_path, tmp_path):
        """Should process notebook with NotebookProcessor."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        output_file = tmp_path / "output.html"

        job = Job(
            id=1,
            job_type="notebook",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={"kind": "completed"},
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

        with patch("clm.workers.notebook.notebook_worker.create_output_spec") as mock_create_spec:
            mock_spec = MagicMock()
            mock_create_spec.return_value = mock_spec

            with patch("clm.workers.notebook.notebook_worker.NotebookProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_notebook = AsyncMock(return_value="<html>output</html>")
                MockProcessor.return_value = mock_processor

                await worker._process_job_async(job)

                MockProcessor.assert_called_once()
                mock_processor.process_notebook.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_job_async_writes_output_file(self, worker_id, db_path, tmp_path):
        """Should write output file with processing result."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        output_dir = tmp_path / "output"
        output_file = output_dir / "notebook.html"

        job = Job(
            id=1,
            job_type="notebook",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={"kind": "completed"},
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)
        result_content = "<html><body>Processed notebook</body></html>"

        with patch("clm.workers.notebook.notebook_worker.create_output_spec") as mock_create_spec:
            mock_spec = MagicMock()
            mock_create_spec.return_value = mock_spec

            with patch("clm.workers.notebook.notebook_worker.NotebookProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_notebook = AsyncMock(return_value=result_content)
                MockProcessor.return_value = mock_processor

                await worker._process_job_async(job)

                # Output directory should be created
                assert output_dir.exists()

                # Output file should be written
                assert output_file.exists()
                assert output_file.read_text() == result_content

    @pytest.mark.asyncio
    async def test_process_job_async_adds_to_cache(self, worker_id, db_path, tmp_path):
        """Should add result to cache after processing."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        output_file = tmp_path / "output.html"

        job = Job(
            id=1,
            job_type="notebook",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={"kind": "completed", "format": "html"},
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

        with patch("clm.workers.notebook.notebook_worker.create_output_spec") as mock_create_spec:
            mock_spec = MagicMock()
            mock_create_spec.return_value = mock_spec

            with patch("clm.workers.notebook.notebook_worker.NotebookProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_notebook = AsyncMock(return_value="<html>output</html>")
                MockProcessor.return_value = mock_processor

                with patch.object(worker.job_queue, "add_to_cache") as mock_cache:
                    await worker._process_job_async(job)

                    mock_cache.assert_called_once()
                    call_args = mock_cache.call_args
                    assert call_args[0][0] == str(output_file)
                    assert call_args[0][1] == "test-hash"

    def test_process_job_uses_event_loop(self, worker_id, db_path, tmp_path):
        """process_job should use persistent event loop."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        job = Job(
            id=1,
            job_type="notebook",
            input_file=str(input_file),
            output_file=str(tmp_path / "output.html"),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

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
        from clm.workers.notebook.notebook_worker import NotebookWorker

        job = Job(
            id=1,
            job_type="notebook",
            input_file="/nonexistent/notebook.ipynb",
            output_file=str(tmp_path / "output.html"),
            content_hash="test-hash",
            payload={},
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

        with pytest.raises(FileNotFoundError):
            worker.process_job(job)


class TestNotebookWorkerCleanup:
    """Test cleanup functionality."""

    def test_cleanup_closes_cache(self, worker_id, db_path, cache_db_path):
        """cleanup should close the cache if initialized."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path, cache_db_path=cache_db_path)

        # Initialize cache
        mock_cache = MagicMock()
        mock_cache.__enter__ = MagicMock(return_value=mock_cache)
        mock_cache.__exit__ = MagicMock()
        worker._cache = mock_cache

        worker.cleanup()

        mock_cache.__exit__.assert_called_once_with(None, None, None)
        assert worker._cache is None

    def test_cleanup_handles_cache_error(self, worker_id, db_path, cache_db_path):
        """cleanup should handle errors when closing cache."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path, cache_db_path=cache_db_path)

        mock_cache = MagicMock()
        mock_cache.__exit__ = MagicMock(side_effect=RuntimeError("Close error"))
        worker._cache = mock_cache

        # Should not raise, just log warning
        worker.cleanup()

        assert worker._cache is None

    def test_cleanup_without_cache(self, worker_id, db_path):
        """cleanup should work without cache."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        worker = NotebookWorker(worker_id, db_path)

        # Should not raise
        worker.cleanup()


class TestNotebookWorkerMain:
    """Test the main() entry point."""

    def test_main_creates_database_if_missing(self, tmp_path):
        """main() should initialize database if it doesn't exist."""
        from clm.workers.notebook import notebook_worker

        db_path = tmp_path / "new_db.db"
        assert not db_path.exists()

        with patch.object(notebook_worker, "DB_PATH", db_path):
            with patch.object(notebook_worker, "CACHE_DB_PATH", tmp_path / "cache.db"):
                with patch.object(notebook_worker, "init_database") as mock_init:
                    with patch.object(
                        notebook_worker.Worker, "register_worker_with_retry", return_value=1
                    ):
                        with patch.object(notebook_worker, "NotebookWorker") as mock_worker_class:
                            mock_worker = MagicMock()
                            mock_worker.run.side_effect = KeyboardInterrupt()
                            mock_worker_class.return_value = mock_worker

                            notebook_worker.main()

                            mock_init.assert_called_once_with(db_path)

    def test_main_registers_worker(self, tmp_path):
        """main() should register worker with database."""
        from clm.workers.notebook import notebook_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(notebook_worker, "DB_PATH", db_path):
            with patch.object(notebook_worker, "CACHE_DB_PATH", tmp_path / "cache.db"):
                with patch.object(
                    notebook_worker.Worker, "register_worker_with_retry", return_value=1
                ) as mock_register:
                    with patch.object(notebook_worker, "NotebookWorker") as mock_worker_class:
                        mock_worker = MagicMock()
                        mock_worker.run.side_effect = KeyboardInterrupt()
                        mock_worker_class.return_value = mock_worker

                        try:
                            notebook_worker.main()
                        except (KeyboardInterrupt, SystemExit):
                            pass

                        mock_register.assert_called_once_with(db_path, "notebook")

    def test_main_handles_keyboard_interrupt(self, tmp_path):
        """main() should handle keyboard interrupt gracefully."""
        from clm.workers.notebook import notebook_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(notebook_worker, "DB_PATH", db_path):
            with patch.object(notebook_worker, "CACHE_DB_PATH", tmp_path / "cache.db"):
                with patch.object(
                    notebook_worker.Worker, "register_worker_with_retry", return_value=1
                ):
                    with patch.object(notebook_worker, "NotebookWorker") as mock_worker_class:
                        mock_worker = MagicMock()
                        mock_worker.run.side_effect = KeyboardInterrupt()
                        mock_worker_class.return_value = mock_worker

                        notebook_worker.main()

                        mock_worker.stop.assert_called_once()
                        mock_worker.cleanup.assert_called_once()

    def test_main_handles_worker_crash(self, tmp_path):
        """main() should handle worker crash and re-raise."""
        from clm.workers.notebook import notebook_worker

        db_path = tmp_path / "test.db"
        init_database(db_path)

        with patch.object(notebook_worker, "DB_PATH", db_path):
            with patch.object(notebook_worker, "CACHE_DB_PATH", tmp_path / "cache.db"):
                with patch.object(
                    notebook_worker.Worker, "register_worker_with_retry", return_value=1
                ):
                    with patch.object(notebook_worker, "NotebookWorker") as mock_worker_class:
                        mock_worker = MagicMock()
                        mock_worker.run.side_effect = RuntimeError("Worker crashed")
                        mock_worker_class.return_value = mock_worker

                        with pytest.raises(RuntimeError, match="Worker crashed"):
                            notebook_worker.main()

                        mock_worker.cleanup.assert_called_once()


class TestNotebookWorkerIntegration:
    """Integration tests for Notebook worker."""

    def test_worker_processes_notebook_job(self, worker_id, db_path, tmp_path):
        """Worker should process a notebook job end-to-end."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        # Create input file
        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text(
            '{"cells": [{"cell_type": "code", "source": "print(\\"hello\\")"}], '
            '"metadata": {}, "nbformat": 4, "nbformat_minor": 5}'
        )

        output_file = tmp_path / "notebook.html"

        # Add job to queue
        with JobQueue(db_path) as queue:
            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(input_file),
                output_file=str(output_file),
                content_hash="test-hash",
                payload={"kind": "completed", "prog_lang": "python"},
            )

        # Create worker
        worker = NotebookWorker(worker_id, db_path)

        with patch("clm.workers.notebook.notebook_worker.create_output_spec") as mock_create_spec:
            mock_spec = MagicMock()
            mock_create_spec.return_value = mock_spec

            with patch("clm.workers.notebook.notebook_worker.NotebookProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_notebook = AsyncMock(
                    return_value="<html>Processed notebook</html>"
                )
                MockProcessor.return_value = mock_processor

                # Run worker in thread
                thread = threading.Thread(target=worker.run)
                thread.start()

                time.sleep(0.5)
                worker.stop()
                thread.join(timeout=2)

        # Verify job was completed
        with JobQueue(db_path) as queue:
            job = queue.get_job(job_id)
            assert job.status == "completed"

        # Verify output file exists
        assert output_file.exists()

    def test_worker_handles_processing_error(self, worker_id, db_path, tmp_path):
        """Worker should handle processing errors properly."""
        from clm.workers.notebook.notebook_worker import NotebookWorker

        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        output_file = tmp_path / "notebook.html"

        # Add job to queue
        with JobQueue(db_path) as queue:
            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(input_file),
                output_file=str(output_file),
                content_hash="test-hash",
                payload={"kind": "completed"},
            )

        # Create worker
        worker = NotebookWorker(worker_id, db_path)

        with patch("clm.workers.notebook.notebook_worker.create_output_spec") as mock_create_spec:
            mock_spec = MagicMock()
            mock_create_spec.return_value = mock_spec

            with patch("clm.workers.notebook.notebook_worker.NotebookProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_notebook = AsyncMock(
                    side_effect=RuntimeError("Processing failed")
                )
                MockProcessor.return_value = mock_processor

                # Run worker in thread
                thread = threading.Thread(target=worker.run)
                thread.start()

                time.sleep(0.5)
                worker.stop()
                thread.join(timeout=2)

        # Verify job failed
        with JobQueue(db_path) as queue:
            job = queue.get_job(job_id)
            assert job.status == "failed"
            assert "Processing failed" in job.error


class TestNotebookWorkerConfiguration:
    """Test worker configuration."""

    def test_log_level_from_environment(self):
        """LOG_LEVEL should be read from environment."""
        from clm.workers.notebook import notebook_worker

        assert hasattr(notebook_worker, "LOG_LEVEL")

    def test_db_path_from_environment(self):
        """DB_PATH should be read from environment."""
        from clm.workers.notebook import notebook_worker

        assert hasattr(notebook_worker, "DB_PATH")
        assert isinstance(notebook_worker.DB_PATH, Path)

    def test_cache_db_path_from_environment(self):
        """CACHE_DB_PATH should be read from environment."""
        from clm.workers.notebook import notebook_worker

        assert hasattr(notebook_worker, "CACHE_DB_PATH")
        assert isinstance(notebook_worker.CACHE_DB_PATH, Path)


class TestNotebookWorkerSourceDirectory:
    """Test source directory handling for Docker mode with source mount.

    When CLM_HOST_DATA_DIR is set and the payload contains source_topic_dir,
    the worker should compute the source directory path and pass it to the
    processor, enabling it to read supporting files directly from the
    mounted /source directory instead of from base64-encoded other_files.
    """

    def test_source_dir_computed_when_docker_mode_and_source_topic_dir_present(self):
        """Source dir should be computed from host_data_dir and source_topic_dir."""
        from clm.infrastructure.workers.worker_base import convert_input_path_to_container

        # Simulate Docker mode path conversion
        host_data_dir = "/home/user/courses"
        source_topic_dir = "/home/user/courses/slides/topic1"

        result = convert_input_path_to_container(source_topic_dir, host_data_dir)

        # Use as_posix() for cross-platform comparison
        assert result.as_posix() == "/source/slides/topic1"

    def test_source_dir_computed_windows_paths(self):
        """Source dir should be computed correctly for Windows paths."""
        from clm.infrastructure.workers.worker_base import convert_input_path_to_container

        # Simulate Docker mode path conversion with Windows paths
        host_data_dir = r"C:\Users\tc\courses"
        source_topic_dir = r"C:\Users\tc\courses\slides\topic1"

        result = convert_input_path_to_container(source_topic_dir, host_data_dir)

        # Use as_posix() for cross-platform comparison
        assert result.as_posix() == "/source/slides/topic1"

    @pytest.mark.asyncio
    async def test_worker_passes_none_source_dir_without_docker_mode(
        self, worker_id, db_path, tmp_path, monkeypatch
    ):
        """Worker should pass None source_dir when not in Docker mode."""
        from unittest.mock import MagicMock, patch

        from clm.workers.notebook.notebook_worker import NotebookWorker

        # Ensure no Docker environment variables are set
        monkeypatch.delenv("CLM_HOST_DATA_DIR", raising=False)
        monkeypatch.delenv("CLM_HOST_WORKSPACE", raising=False)

        # Create input file
        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4}')

        # Create output directory
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        output_file = output_dir / "notebook.html"

        job = Job(
            id=1,
            job_type="notebook",
            input_file=str(input_file),
            output_file=str(output_file),
            content_hash="test-hash",
            payload={
                "kind": "completed",
                "prog_lang": "python",
                "language": "en",
                "format": "notebook",
                "source_topic_dir": "/some/path/slides/topic1",  # This should be ignored
            },
            status="processing",
            created_at=datetime.now(),
        )

        worker = NotebookWorker(worker_id, db_path)

        # Mock the processor to capture the source_dir parameter
        captured_source_dir = "NOT_SET"

        async def mock_process_notebook(payload, source_dir=None):
            nonlocal captured_source_dir
            captured_source_dir = source_dir
            return '{"cells": [], "metadata": {}}'

        with patch.object(worker, "_ensure_cache_initialized", return_value=None):
            with patch("clm.workers.notebook.notebook_worker.NotebookProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_notebook = mock_process_notebook
                mock_processor.get_warnings.return_value = []
                MockProcessor.return_value = mock_processor

                await worker._process_job_async(job)

        # Should have passed None since we're not in Docker mode
        assert captured_source_dir is None

    def test_payload_includes_source_topic_dir_field(self):
        """NotebookPayload should be able to hold source_topic_dir."""
        from clm.infrastructure.messaging.notebook_classes import NotebookPayload

        payload = NotebookPayload(
            data="",
            input_file="/test/notebook.ipynb",
            input_file_name="notebook.ipynb",
            output_file="/output/notebook.html",
            kind="speaker",
            prog_lang="python",
            language="en",
            format="html",
            correlation_id="test-123",
            source_topic_dir="/home/user/courses/slides/topic1",
        )

        assert payload.source_topic_dir == "/home/user/courses/slides/topic1"

    def test_payload_source_topic_dir_defaults_to_empty(self):
        """source_topic_dir should default to empty string."""
        from clm.infrastructure.messaging.notebook_classes import NotebookPayload

        payload = NotebookPayload(
            data="",
            input_file="/test/notebook.ipynb",
            input_file_name="notebook.ipynb",
            output_file="/output/notebook.html",
            kind="speaker",
            prog_lang="python",
            language="en",
            format="html",
            correlation_id="test-123",
        )

        assert payload.source_topic_dir == ""
