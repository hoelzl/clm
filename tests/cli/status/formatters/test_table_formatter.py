"""Tests for table formatter module.

Tests the TableFormatter class that formats status info as human-readable tables.
"""

from datetime import datetime, timedelta

import pytest

from clm.cli.status.formatters.table_formatter import TableFormatter
from clm.cli.status.models import (
    BusyWorkerInfo,
    DatabaseInfo,
    ErrorStats,
    ErrorTypeStats,
    QueueStats,
    StatusInfo,
    SystemHealth,
    WorkerTypeStats,
)


@pytest.fixture
def basic_status() -> StatusInfo:
    """Create a basic status info for testing."""
    return StatusInfo(
        timestamp=datetime.now(),
        health=SystemHealth.HEALTHY,
        database=DatabaseInfo(
            path="/path/to/db.db",
            accessible=True,
            exists=True,
            size_bytes=1024 * 100,  # 100 KB
        ),
        workers={
            "notebook": WorkerTypeStats(
                worker_type="notebook",
                execution_mode="direct",
                total=2,
                idle=1,
                busy=1,
                hung=0,
                dead=0,
                busy_workers=[
                    BusyWorkerInfo(
                        worker_id="worker-abc123def456",
                        job_id="job-1",
                        document_path="/path/to/notebook.ipynb",
                        elapsed_seconds=30,
                        output_format="html",
                        prog_lang="python",
                        kind="completed",
                    )
                ],
            ),
        },
        queue=QueueStats(
            pending=5,
            processing=2,
            completed_last_hour=100,
            failed_last_hour=2,
            oldest_pending_seconds=60,
        ),
        warnings=[],
        errors=[],
    )


@pytest.fixture
def formatter() -> TableFormatter:
    """Create a formatter with colors disabled for easier testing."""
    return TableFormatter(use_color=False)


@pytest.fixture
def color_formatter() -> TableFormatter:
    """Create a formatter with colors enabled."""
    return TableFormatter(use_color=True)


class TestTableFormatterInit:
    """Test formatter initialization."""

    def test_default_uses_color(self):
        """By default should use color."""
        formatter = TableFormatter()
        assert formatter.use_color is True

    def test_disable_color(self):
        """Should be able to disable color."""
        formatter = TableFormatter(use_color=False)
        assert formatter.use_color is False


class TestTableFormatterFormat:
    """Test the format method."""

    def test_format_returns_string(self, formatter, basic_status):
        """Format should return a string."""
        result = formatter.format(basic_status)
        assert isinstance(result, str)

    def test_format_includes_header(self, formatter, basic_status):
        """Format should include header section."""
        result = formatter.format(basic_status)
        assert "CLM System Status" in result
        assert "=" in result  # Header separators

    def test_format_includes_workers(self, formatter, basic_status):
        """Format should include workers section."""
        result = formatter.format(basic_status)
        assert "Workers by Type" in result
        assert "Notebook Workers" in result

    def test_format_includes_queue(self, formatter, basic_status):
        """Format should include queue section."""
        result = formatter.format(basic_status)
        assert "Job Queue Status" in result
        assert "Pending:" in result

    def test_format_workers_only(self, formatter, basic_status):
        """workers_only should show only workers section."""
        result = formatter.format(basic_status, workers_only=True)
        assert "Workers by Type" in result
        assert "CLM System Status" not in result
        assert "Pending:" not in result

    def test_format_jobs_only(self, formatter, basic_status):
        """jobs_only should show only queue section."""
        result = formatter.format(basic_status, jobs_only=True)
        assert "Job Queue Status" in result
        assert "CLM System Status" not in result
        assert "Workers by Type" not in result


class TestFormatHeader:
    """Test _format_header method."""

    def test_format_header_healthy(self, formatter, basic_status):
        """Should show healthy status icon."""
        result = formatter.format(basic_status)
        assert "Healthy" in result

    def test_format_header_warning(self, formatter, basic_status):
        """Should show warning status icon."""
        basic_status.health = SystemHealth.WARNING
        result = formatter.format(basic_status)
        assert "Warning" in result

    def test_format_header_error(self, formatter, basic_status):
        """Should show error status icon."""
        basic_status.health = SystemHealth.ERROR
        result = formatter.format(basic_status)
        assert "Error" in result

    def test_format_header_with_color(self, color_formatter, basic_status):
        """Should include ANSI color codes when enabled."""
        result = color_formatter.format(basic_status)
        # Green color code for healthy
        assert "\033[32m" in result

    def test_format_header_shows_db_path(self, formatter, basic_status):
        """Should show database path."""
        result = formatter.format(basic_status)
        assert "/path/to/db.db" in result

    def test_format_header_shows_db_size(self, formatter, basic_status):
        """Should show database size in KB."""
        result = formatter.format(basic_status)
        assert "100 KB" in result

    def test_format_header_update_time_just_now(self, formatter, basic_status):
        """Recent timestamp should show 'just now'."""
        basic_status.timestamp = datetime.now()
        result = formatter.format(basic_status)
        # Should show "just now" or small number of seconds
        assert "just now" in result or "1s ago" in result or "0s ago" in result

    def test_format_header_update_time_seconds(self, formatter, basic_status):
        """Timestamp few seconds ago should show seconds."""
        basic_status.timestamp = datetime.now() - timedelta(seconds=30)
        result = formatter.format(basic_status)
        assert "s ago" in result

    def test_format_header_update_time_minutes(self, formatter, basic_status):
        """Timestamp minutes ago should show minutes."""
        basic_status.timestamp = datetime.now() - timedelta(minutes=5)
        result = formatter.format(basic_status)
        assert "5m ago" in result


class TestFormatWorkers:
    """Test _format_workers method."""

    def test_format_workers_shows_total(self, formatter, basic_status):
        """Should show total worker count."""
        result = formatter.format(basic_status)
        assert "2 total" in result

    def test_format_workers_shows_execution_mode(self, formatter, basic_status):
        """Should show execution mode."""
        result = formatter.format(basic_status)
        assert "direct mode" in result

    def test_format_workers_shows_idle(self, formatter, basic_status):
        """Should show idle workers."""
        result = formatter.format(basic_status)
        assert "1 idle" in result

    def test_format_workers_shows_busy(self, formatter, basic_status):
        """Should show busy workers."""
        result = formatter.format(basic_status)
        assert "1 busy" in result

    def test_format_workers_shows_busy_details(self, formatter, basic_status):
        """Should show busy worker details."""
        result = formatter.format(basic_status)
        # Should show truncated worker ID (first 12 chars)
        assert "worker-abc12" in result
        # Should show document path
        assert "notebook.ipynb" in result
        # Should show elapsed time
        assert "30s" in result
        # Should show format info
        assert "format=html" in result
        assert "lang=python" in result

    def test_format_workers_truncates_long_paths(self, formatter, basic_status):
        """Should truncate long document paths."""
        basic_status.workers["notebook"].busy_workers[
            0
        ].document_path = "/very/long/path/to/some/nested/directory/with/many/levels/document.ipynb"
        result = formatter.format(basic_status)
        assert "..." in result  # Path should be truncated

    def test_format_workers_shows_hung(self, formatter, basic_status):
        """Should show hung workers."""
        basic_status.workers["notebook"].hung = 1
        result = formatter.format(basic_status)
        assert "1 hung" in result

    def test_format_workers_shows_dead(self, formatter, basic_status):
        """Should show dead workers."""
        basic_status.workers["notebook"].dead = 1
        result = formatter.format(basic_status)
        assert "1 dead" in result

    def test_format_workers_no_workers_warning(self, formatter, basic_status):
        """Should show warning when no workers registered."""
        basic_status.workers["notebook"].total = 0
        basic_status.workers["notebook"].idle = 0
        basic_status.workers["notebook"].busy = 0
        result = formatter.format(basic_status)
        assert "No workers registered" in result


class TestFormatQueue:
    """Test _format_queue method."""

    def test_format_queue_shows_pending(self, formatter, basic_status):
        """Should show pending jobs."""
        result = formatter.format(basic_status)
        assert "5 jobs" in result  # Pending count

    def test_format_queue_shows_oldest_pending(self, formatter, basic_status):
        """Should show oldest pending time."""
        result = formatter.format(basic_status)
        assert "oldest:" in result

    def test_format_queue_old_pending_warning(self, formatter, basic_status):
        """Should show warning for old pending jobs."""
        basic_status.queue.oldest_pending_seconds = 400  # > 5 minutes
        result = formatter.format(basic_status)
        # Should have warning indicator

    def test_format_queue_shows_processing(self, formatter, basic_status):
        """Should show processing jobs."""
        result = formatter.format(basic_status)
        assert "Processing:" in result

    def test_format_queue_shows_completed(self, formatter, basic_status):
        """Should show completed jobs."""
        result = formatter.format(basic_status)
        assert "Completed:" in result
        assert "last hour" in result

    def test_format_queue_shows_failure_rate(self, formatter, basic_status):
        """Should show failure rate."""
        result = formatter.format(basic_status)
        assert "failure rate" in result


class TestFormatIssues:
    """Test _format_issues method."""

    def test_format_issues_shows_errors(self, formatter, basic_status):
        """Should show errors."""
        basic_status.errors = ["Error message 1", "Error message 2"]
        result = formatter.format(basic_status)
        assert "Error: Error message 1" in result
        assert "Error: Error message 2" in result

    def test_format_issues_shows_warnings(self, formatter, basic_status):
        """Should show warnings."""
        basic_status.warnings = ["Warning message"]
        result = formatter.format(basic_status)
        assert "Warning: Warning message" in result


class TestFormatErrorStats:
    """Test _format_error_stats method."""

    def test_format_error_stats_shows_breakdown(self, formatter, basic_status):
        """Should show error breakdown when error_stats present."""
        basic_status.error_stats = ErrorStats(
            total_errors=5,
            time_period_hours=1,
            by_type={
                "user": ErrorTypeStats(
                    error_type="user",
                    count=3,
                    categories={"syntax_error": 2, "import_error": 1},
                ),
                "infrastructure": ErrorTypeStats(
                    error_type="infrastructure",
                    count=2,
                    categories={"timeout": 2},
                ),
            },
        )
        result = formatter.format(basic_status)
        assert "Error Breakdown" in result
        assert "User" in result
        assert "syntax_error" in result

    def test_format_error_stats_empty(self, formatter, basic_status):
        """Should not show error breakdown when no errors."""
        basic_status.error_stats = None
        result = formatter.format(basic_status)
        assert "Error Breakdown" not in result


class TestFormatElapsed:
    """Test _format_elapsed method."""

    def test_format_elapsed_seconds(self, formatter):
        """Should format seconds correctly."""
        result = formatter._format_elapsed(30)
        assert result == "30s"

    def test_format_elapsed_minutes(self, formatter):
        """Should format minutes:seconds correctly."""
        result = formatter._format_elapsed(90)  # 1:30
        assert result == "1:30"

    def test_format_elapsed_hours(self, formatter):
        """Should format hours:minutes:seconds correctly."""
        result = formatter._format_elapsed(3661)  # 1:01:01
        assert result == "1:01:01"


class TestGetExitCode:
    """Test get_exit_code method."""

    def test_exit_code_healthy(self, formatter, basic_status):
        """Healthy status should return 0."""
        basic_status.health = SystemHealth.HEALTHY
        assert formatter.get_exit_code(basic_status) == 0

    def test_exit_code_warning(self, formatter, basic_status):
        """Warning status should return 1."""
        basic_status.health = SystemHealth.WARNING
        assert formatter.get_exit_code(basic_status) == 1

    def test_exit_code_error(self, formatter, basic_status):
        """Error status should return 2."""
        basic_status.health = SystemHealth.ERROR
        assert formatter.get_exit_code(basic_status) == 2
