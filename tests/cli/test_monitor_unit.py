"""Unit tests for monitor TUI components."""

from datetime import datetime
from pathlib import Path

import pytest

from clx.cli.monitor.data_provider import ActivityEvent
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
