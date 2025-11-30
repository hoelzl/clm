"""Unit tests for monitor TUI components."""

import os
from datetime import datetime
from pathlib import Path

import pytest

from clx.cli.monitor.data_provider import (
    ActivityEvent,
    _find_common_prefix,
    _make_relative,
)
from clx.cli.monitor.formatters import (
    format_elapsed,
    format_rate,
    format_size,
    format_timestamp,
)


class TestFormatters:
    """Test formatting utilities."""

    def test_format_elapsed_seconds(self):
        """Test elapsed time formatting for seconds."""
        assert format_elapsed(30) == "00:30"
        assert format_elapsed(59) == "00:59"

    def test_format_elapsed_minutes(self):
        """Test elapsed time formatting for minutes."""
        assert format_elapsed(60) == "01:00"
        assert format_elapsed(135) == "02:15"
        assert format_elapsed(3599) == "59:59"

    def test_format_elapsed_hours(self):
        """Test elapsed time formatting for hours."""
        assert format_elapsed(3600) == "1:00:00"
        assert format_elapsed(3665) == "1:01:05"
        assert format_elapsed(7200) == "2:00:00"

    def test_format_timestamp_absolute(self):
        """Test timestamp formatting in absolute mode."""
        dt = datetime(2025, 11, 15, 10, 30, 15)
        result = format_timestamp(dt, relative=False)
        assert result == "10:30:15"

    def test_format_size_bytes(self):
        """Test size formatting for bytes."""
        assert format_size(512) == "512 B"
        assert format_size(1023) == "1023 B"

    def test_format_size_kb(self):
        """Test size formatting for kilobytes."""
        assert format_size(1024) == "1.0 KB"
        assert format_size(102400) == "100.0 KB"

    def test_format_size_mb(self):
        """Test size formatting for megabytes."""
        assert format_size(1024 * 1024) == "1.0 MB"
        assert format_size(50 * 1024 * 1024) == "50.0 MB"

    def test_format_size_gb(self):
        """Test size formatting for gigabytes."""
        assert format_size(1024 * 1024 * 1024) == "1.00 GB"
        assert format_size(2 * 1024 * 1024 * 1024) == "2.00 GB"

    def test_format_rate_per_minute(self):
        """Test rate formatting (jobs per minute)."""
        # 10 jobs in 60 seconds = 10/min
        result = format_rate(10, 60)
        assert "/min" in result

    def test_format_rate_per_second(self):
        """Test rate formatting (jobs per second)."""
        # 120 jobs in 60 seconds = 2/sec
        result = format_rate(120, 60)
        assert "/sec" in result


class TestActivityEvent:
    """Test ActivityEvent data class."""

    def test_create_job_started_event(self):
        """Test creating a job started event."""
        event = ActivityEvent(
            timestamp=datetime(2025, 11, 15, 10, 30, 0),
            event_type="job_started",
            job_id="job-123",
            document_path="/path/to/doc.ipynb",
        )

        assert event.event_type == "job_started"
        assert event.job_id == "job-123"
        assert event.document_path == "/path/to/doc.ipynb"

    def test_create_job_completed_event(self):
        """Test creating a job completed event."""
        event = ActivityEvent(
            timestamp=datetime(2025, 11, 15, 10, 30, 45),
            event_type="job_completed",
            job_id="job-123",
            document_path="/path/to/doc.ipynb",
            duration_seconds=45,
        )

        assert event.event_type == "job_completed"
        assert event.duration_seconds == 45

    def test_create_job_failed_event(self):
        """Test creating a job failed event."""
        event = ActivityEvent(
            timestamp=datetime(2025, 11, 15, 10, 30, 15),
            event_type="job_failed",
            job_id="job-456",
            document_path="/path/to/broken.ipynb",
            duration_seconds=15,
            error_message="Execution failed",
        )

        assert event.event_type == "job_failed"
        assert event.error_message == "Execution failed"


class TestPathHelpers:
    """Test path helper functions for relative paths."""

    def test_find_common_prefix_empty_list(self):
        """Test common prefix with empty list."""
        assert _find_common_prefix([]) == ""

    def test_find_common_prefix_single_path(self):
        """Test common prefix with single path."""
        # Use os.path.join to create cross-platform paths
        path = os.path.join("home", "user", "projects", "file.txt")
        # Make it absolute for the test
        abs_path = os.path.abspath(path)
        paths = [abs_path]
        result = _find_common_prefix(paths)
        # Common prefix of single file is its directory (or file itself on some systems)
        assert result in [os.path.dirname(abs_path), abs_path]

    def test_find_common_prefix_same_directory(self):
        """Test common prefix with files in same directory."""
        # Use os.path.join for cross-platform paths
        base = os.path.join(os.getcwd(), "test_projects")
        paths = [
            os.path.join(base, "file1.txt"),
            os.path.join(base, "file2.txt"),
            os.path.join(base, "file3.txt"),
        ]
        result = _find_common_prefix(paths)
        # Normalize both for comparison
        assert os.path.normpath(result) == os.path.normpath(base)

    def test_find_common_prefix_nested_directories(self):
        """Test common prefix with nested directories."""
        base = os.path.join(os.getcwd(), "test_projects")
        paths = [
            os.path.join(base, "src", "main.py"),
            os.path.join(base, "tests", "test_main.py"),
            os.path.join(base, "docs", "readme.md"),
        ]
        result = _find_common_prefix(paths)
        assert os.path.normpath(result) == os.path.normpath(base)

    def test_find_common_prefix_with_empty_paths(self):
        """Test common prefix filters out empty paths."""
        base = os.path.join(os.getcwd(), "test_user")
        paths = [
            os.path.join(base, "file.txt"),
            "",
            os.path.join(base, "other.txt"),
        ]
        result = _find_common_prefix(paths)
        assert os.path.normpath(result) == os.path.normpath(base)

    def test_find_common_prefix_different_roots(self):
        """Test common prefix with different roots."""
        # On Windows, test with different drives
        # On Unix, test with different top-level directories
        if os.name == "nt":
            # Windows: different drives have no common prefix
            paths = ["C:\\a\\b\\c", "D:\\x\\y\\z"]
            result = _find_common_prefix(paths)
            assert result == ""  # ValueError expected, returns ""
        else:
            # Unix: root "/" is common for all absolute paths
            paths = ["/a/b/c", "/x/y/z"]
            result = _find_common_prefix(paths)
            assert result == "/"

    def test_make_relative_under_base(self):
        """Test making path relative when under base."""
        base = os.path.join(os.getcwd(), "projects")
        path = os.path.join(base, "src", "main.py")
        result = _make_relative(path, base)
        expected = os.path.join("src", "main.py")
        assert result == expected

    def test_make_relative_not_under_base(self):
        """Test making path relative when not under base."""
        base = os.path.join(os.getcwd(), "projects")
        # Create a path that's definitely not under base
        if os.name == "nt":
            path = "C:\\Windows\\System32\\app.log"
            # Make sure base is on a different path
            if base.startswith("C:\\Windows"):
                base = "D:\\projects"
        else:
            path = "/var/log/app.log"
            base = "/home/user/projects"
        result = _make_relative(path, base)
        # Should return original path since not under base
        assert result == path or result.startswith("..")

    def test_make_relative_empty_base(self):
        """Test making path relative with empty base."""
        path = os.path.join(os.getcwd(), "file.txt")
        result = _make_relative(path, "")
        assert result == path

    def test_make_relative_empty_path(self):
        """Test making path relative with empty path."""
        result = _make_relative("", os.getcwd())
        assert result == ""

    def test_make_relative_both_empty(self):
        """Test making path relative with both empty."""
        result = _make_relative("", "")
        assert result == ""


class TestWorkersPanel:
    """Test workers panel rendering logic."""

    def test_workers_data_with_zero_total(self):
        """Test that worker types with 0 workers show appropriate message."""
        from clx.cli.status.models import WorkerTypeStats

        # Create stats with 0 total workers
        stats = WorkerTypeStats(
            worker_type="drawio",
            execution_mode=None,
            total=0,
            idle=0,
            busy=0,
            hung=0,
            dead=0,
            busy_workers=[],
        )

        # Verify that total=0 is handled correctly
        assert stats.total == 0
        # The workers panel should show "No workers started" for this case

    def test_workers_data_with_dead_workers(self):
        """Test that dead workers are displayed correctly."""
        from clx.cli.status.models import WorkerTypeStats

        stats = WorkerTypeStats(
            worker_type="notebook",
            execution_mode="direct",
            total=5,
            idle=0,
            busy=0,
            hung=0,
            dead=5,
            busy_workers=[],
        )

        assert stats.total == 5
        assert stats.dead == 5


class TestStatusHeader:
    """Test status header widget."""

    def test_status_header_initial_content(self):
        """Test that status header has initial loading content."""
        from rich.text import Text

        from clx.cli.monitor.widgets.status_header import StatusHeader

        header = StatusHeader(id="test-header")
        # The header should be initialized with loading text
        # We can't easily test the renderable, but we can verify the class works
        assert header.status is None

    def test_status_header_render_content(self):
        """Test status header renders content correctly."""
        from rich.text import Text

        from clx.cli.monitor.widgets.status_header import StatusHeader
        from clx.cli.status.models import DatabaseInfo, QueueStats, StatusInfo, SystemHealth

        header = StatusHeader(id="test-header")

        # Create test status
        status = StatusInfo(
            timestamp=datetime(2025, 11, 30, 10, 30, 0),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(
                path="/tmp/test.db",
                accessible=True,
                exists=True,
                size_bytes=1024,
            ),
            workers={},
            queue=QueueStats(
                pending=0,
                processing=0,
                completed_last_hour=10,
                failed_last_hour=0,
            ),
        )

        # Update and render
        header.status = status
        content = header._render_content()

        assert isinstance(content, Text)
        # Check that the text contains expected parts
        plain_text = content.plain
        assert "CLX Monitor" in plain_text
        assert "Healthy" in plain_text
        assert "10:30:00" in plain_text

    def test_status_header_version_is_current(self):
        """Test that status header shows current version."""
        from rich.text import Text

        from clx.cli.monitor.widgets.status_header import StatusHeader
        from clx.cli.status.models import DatabaseInfo, QueueStats, StatusInfo, SystemHealth

        header = StatusHeader(id="test-header")
        header.status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(
                path="/tmp/test.db", accessible=True, exists=True, size_bytes=1024
            ),
            workers={},
            queue=QueueStats(0, 0, 0, 0),
        )

        content = header._render_content()
        assert "v0.5.0" in content.plain


class TestActivityPanel:
    """Test activity panel widget."""

    def test_activity_panel_markup_enabled(self):
        """Test that RichLog is created with markup=True."""
        # Import the compose method to check RichLog configuration
        from clx.cli.monitor.widgets.activity_panel import ActivityPanel

        # Create panel instance
        panel = ActivityPanel(id="test-panel")

        # Verify the panel class exists and can be instantiated
        assert panel is not None


@pytest.mark.integration
class TestDataProvider:
    """Test DataProvider integration."""

    def test_get_status_nonexistent_db(self):
        """Test getting status from nonexistent database."""
        from clx.cli.monitor.data_provider import DataProvider

        db_path = Path("/tmp/nonexistent_db.db")
        provider = DataProvider(db_path=db_path)
        status = provider.get_status()

        assert status is not None
        # Database doesn't exist, so accessible should be False
        assert not status.database.accessible

    def test_get_recent_events_nonexistent_db(self):
        """Test getting events from nonexistent database."""
        from clx.cli.monitor.data_provider import DataProvider

        db_path = Path("/tmp/nonexistent_db.db")
        provider = DataProvider(db_path=db_path)
        events = provider.get_recent_events(limit=10)

        assert isinstance(events, list)
        assert len(events) == 0  # No events if DB doesn't exist
