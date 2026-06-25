"""Tests for watch mode features including debouncing and fast mode."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.cli.file_event_handler import FileEventHandler


async def _drain_debounce(handler: FileEventHandler, timeout: float = 5.0) -> None:
    """Await all currently-pending debounce tasks to completion.

    Event-driven replacement for a fixed ``await asyncio.sleep(margin)``: it
    returns the instant the scheduled debounce work finishes (so the happy path
    is faster) instead of racing a hard-coded ~50ms wall-clock margin that a
    CPU-starved xdist worker can overshoot, running the assertion before the
    debounce continuation has run its mock. ``handler._pending_tasks`` holds the
    task scheduled by each ``on_*`` callback (it removes itself from the dict
    then runs the handler); a coalesced/cancelled event leaves exactly the
    surviving task. ``return_exceptions=True`` tolerates a task cancelled by a
    newer event; an empty dict (nothing scheduled — e.g. an ignored event)
    returns immediately, which also makes the negative assertions deterministic.
    """
    tasks = list(handler._pending_tasks.values())
    if not tasks:
        return
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)


class MockEvent:
    """Mock watchdog event."""

    def __init__(self, src_path: str, dest_path: str | None = None):
        self.src_path = src_path
        self.dest_path = dest_path


@pytest.fixture
def mock_course():
    """Create a mock course object."""
    course = MagicMock()
    course.find_course_file = MagicMock(return_value=True)
    course.process_file = AsyncMock()
    course.add_file = MagicMock(return_value=MagicMock())
    return course


@pytest.fixture
def mock_backend():
    """Create a mock backend object."""
    backend = MagicMock()
    backend.delete_dependencies = AsyncMock()
    backend.cancel_jobs_for_file = AsyncMock(return_value=0)
    return backend


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestDebouncing:
    """Tests for event debouncing functionality."""

    @pytest.mark.asyncio
    async def test_single_event_processed(self, mock_course, mock_backend, tmp_path):
        """Test that a single event is processed correctly."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.1,
            patterns=["*"],
        )

        test_file = tmp_path / "test.ipynb"
        test_file.write_text("test content")

        # Trigger on_modified
        handler.on_modified(MockEvent(str(test_file)))

        # Wait for the debounced task to finish.
        await _drain_debounce(handler)

        # Should process the file once
        assert mock_course.process_file.call_count == 1

    @pytest.mark.asyncio
    async def test_rapid_events_coalesced(self, mock_course, mock_backend, tmp_path):
        """Test that multiple rapid events are coalesced into one processing."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.1,
            patterns=["*"],
        )

        test_file = tmp_path / "test.ipynb"
        test_file.write_text("test content")

        # Simulate rapid file changes (like text editor auto-save)
        for i in range(5):
            handler.on_modified(MockEvent(str(test_file)))
            await asyncio.sleep(0.02)  # 20ms between events

        # Wait for the surviving debounced task to finish.
        await _drain_debounce(handler)

        # Should only process once due to debouncing
        assert mock_course.process_file.call_count == 1

    @pytest.mark.asyncio
    async def test_separated_events_processed_separately(self, mock_course, mock_backend, tmp_path):
        """Test that events separated by more than debounce delay are processed separately."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.05,
            patterns=["*"],
        )

        test_file = tmp_path / "test.ipynb"
        test_file.write_text("test content")

        # First event
        handler.on_modified(MockEvent(str(test_file)))
        await _drain_debounce(handler)  # Wait for first debounce to complete

        # Second event after debounce delay
        handler.on_modified(MockEvent(str(test_file)))
        await _drain_debounce(handler)  # Wait for second debounce to complete

        # Should process twice
        assert mock_course.process_file.call_count == 2

    @pytest.mark.asyncio
    async def test_different_files_processed_independently(
        self, mock_course, mock_backend, tmp_path
    ):
        """Test that events for different files are processed independently."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.1,
            patterns=["*"],
        )

        file1 = tmp_path / "test1.ipynb"
        file2 = tmp_path / "test2.ipynb"
        file1.write_text("test1")
        file2.write_text("test2")

        # Trigger events for different files
        handler.on_modified(MockEvent(str(file1)))
        handler.on_modified(MockEvent(str(file2)))

        # Wait for both debounced tasks to finish.
        await _drain_debounce(handler)

        # Both files should be processed
        assert mock_course.process_file.call_count == 2

    @pytest.mark.asyncio
    async def test_pending_task_cancelled_on_new_event(self, mock_course, mock_backend, tmp_path):
        """Test that pending task is cancelled when a new event arrives."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.2,
            patterns=["*"],
        )

        test_file = tmp_path / "test.ipynb"
        test_file.write_text("test content")

        # First event
        handler.on_modified(MockEvent(str(test_file)))

        # Check there's a pending task
        assert len(handler._pending_tasks) == 1

        # Second event before debounce completes
        await asyncio.sleep(0.05)
        handler.on_modified(MockEvent(str(test_file)))

        # Still should have one pending task (the new one)
        assert len(handler._pending_tasks) == 1

        # Wait for the surviving (post-cancellation) debounced task to finish.
        await _drain_debounce(handler)

        # Should only process once
        assert mock_course.process_file.call_count == 1

    @pytest.mark.asyncio
    async def test_debounce_delay_configurable(self, mock_course, mock_backend, tmp_path):
        """Test that debounce delay can be configured."""
        loop = asyncio.get_running_loop()

        # Create handler with custom debounce delay
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.5,  # 500ms
            patterns=["*"],
        )

        assert handler.debounce_delay == 0.5

    @pytest.mark.asyncio
    async def test_default_debounce_delay(self, mock_course, mock_backend, tmp_path):
        """Test that default debounce delay is 0.3 seconds."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            patterns=["*"],
        )

        assert handler.debounce_delay == 0.3


class TestFileEventHandlerEvents:
    """Tests for different event types."""

    @pytest.mark.asyncio
    async def test_on_created_debounced(self, mock_course, mock_backend, tmp_path):
        """Test that on_created events are debounced."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.1,
            patterns=["*"],
        )

        test_file = tmp_path / "new_file.ipynb"
        test_file.write_text("test")

        # Multiple rapid create events
        for _ in range(3):
            handler.on_created(MockEvent(str(test_file)))
            await asyncio.sleep(0.02)

        await _drain_debounce(handler)

        # Should only add file once
        assert mock_course.add_file.call_count == 1

    @pytest.mark.asyncio
    async def test_on_deleted_debounced(self, mock_course, mock_backend, tmp_path):
        """Test that on_deleted events are debounced."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.1,
            patterns=["*"],
        )

        test_file = tmp_path / "deleted_file.ipynb"

        # Multiple rapid delete events
        for _ in range(3):
            handler.on_deleted(MockEvent(str(test_file)))
            await asyncio.sleep(0.02)

        await _drain_debounce(handler)

        # Should only try to find and delete once
        assert mock_course.find_course_file.call_count == 1


class TestIgnoredFiles:
    """Tests for file filtering."""

    @pytest.mark.asyncio
    async def test_temp_files_ignored(self, mock_course, mock_backend, tmp_path):
        """Test that temporary files are ignored."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.1,
            patterns=["*"],
        )

        # Temporary file pattern
        temp_file = tmp_path / ".~lock.test.ipynb"

        handler.on_modified(MockEvent(str(temp_file)))
        await _drain_debounce(handler)

        # Should not process temp files
        assert mock_course.process_file.call_count == 0

    @pytest.mark.asyncio
    async def test_git_directory_ignored(self, mock_course, mock_backend, tmp_path):
        """Test that .git directory is ignored."""
        loop = asyncio.get_running_loop()

        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=loop,
            debounce_delay=0.1,
            patterns=["*"],
        )

        # Create .git directory and file
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        git_file = git_dir / "HEAD"

        handler.on_modified(MockEvent(str(git_file)))
        await _drain_debounce(handler)

        # Should not process .git files
        assert mock_course.process_file.call_count == 0
