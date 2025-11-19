"""Unit tests for status command components."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from clx.cli.status.formatters.compact_formatter import CompactFormatter
from clx.cli.status.formatters.json_formatter import JsonFormatter
from clx.cli.status.formatters.table_formatter import TableFormatter
from clx.cli.status.models import (
    BusyWorkerInfo,
    DatabaseInfo,
    QueueStats,
    StatusInfo,
    SystemHealth,
    WorkerTypeStats,
)


class TestModels:
    """Test data models."""

    def test_status_info_creation(self):
        """Test creating StatusInfo object."""
        status = StatusInfo(
            timestamp=datetime(2025, 11, 15, 10, 30, 0),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(
                path="/path/to/db", accessible=True, exists=True, size_bytes=102400
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
                )
            },
            queue=QueueStats(pending=5, processing=2, completed_last_hour=100, failed_last_hour=2),
        )

        assert status.health == SystemHealth.HEALTHY
        assert status.database.path == "/path/to/db"
        assert status.workers["notebook"].total == 2
        assert status.queue.pending == 5

    def test_worker_type_stats_with_busy_workers(self):
        """Test WorkerTypeStats with busy workers."""
        stats = WorkerTypeStats(
            worker_type="notebook",
            execution_mode="docker",
            total=3,
            idle=1,
            busy=2,
            hung=0,
            dead=0,
            busy_workers=[
                BusyWorkerInfo(
                    worker_id="worker-1",
                    job_id="job-123",
                    document_path="/path/to/doc.ipynb",
                    elapsed_seconds=45,
                ),
                BusyWorkerInfo(
                    worker_id="worker-2",
                    job_id="job-456",
                    document_path="/path/to/other.ipynb",
                    elapsed_seconds=120,
                ),
            ],
        )

        assert stats.total == 3
        assert stats.busy == 2
        assert len(stats.busy_workers) == 2
        assert stats.busy_workers[0].worker_id == "worker-1"


class TestJsonFormatter:
    """Test JSON formatter."""

    def test_json_formatter_basic(self):
        """Test JSON formatter with basic status."""
        status = StatusInfo(
            timestamp=datetime(2025, 11, 15, 10, 30, 0),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(
                path="/path/to/db", accessible=True, exists=True, size_bytes=102400
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
                )
            },
            queue=QueueStats(pending=5, processing=2, completed_last_hour=100, failed_last_hour=2),
        )

        formatter = JsonFormatter(pretty=False)
        output = formatter.format(status)

        data = json.loads(output)

        assert data["status"] == "healthy"
        assert data["workers"]["notebook"]["total"] == 2
        assert data["queue"]["pending"] == 5

    def test_json_formatter_workers_only(self):
        """Test JSON formatter with workers_only flag."""
        status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={
                "notebook": WorkerTypeStats(
                    worker_type="notebook",
                    execution_mode="direct",
                    total=2,
                    idle=1,
                    busy=1,
                    hung=0,
                    dead=0,
                )
            },
            queue=QueueStats(pending=5, processing=2, completed_last_hour=100, failed_last_hour=2),
        )

        formatter = JsonFormatter(pretty=False)
        output = formatter.format(status, workers_only=True)

        data = json.loads(output)

        assert "workers" in data
        assert "queue" not in data

    def test_json_formatter_jobs_only(self):
        """Test JSON formatter with jobs_only flag."""
        status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={},
            queue=QueueStats(pending=5, processing=2, completed_last_hour=100, failed_last_hour=2),
        )

        formatter = JsonFormatter(pretty=False)
        output = formatter.format(status, jobs_only=True)

        data = json.loads(output)

        assert "queue" in data
        assert "workers" not in data
        assert "database" not in data

    def test_json_formatter_exit_codes(self):
        """Test exit codes for different health states."""
        formatter = JsonFormatter()

        healthy_status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={},
            queue=QueueStats(0, 0, 0, 0),
        )
        assert formatter.get_exit_code(healthy_status) == 0

        warning_status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.WARNING,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={},
            queue=QueueStats(0, 0, 0, 0),
            warnings=["Something"],
        )
        assert formatter.get_exit_code(warning_status) == 1

        error_status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.ERROR,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={},
            queue=QueueStats(0, 0, 0, 0),
            errors=["Database error"],
        )
        assert formatter.get_exit_code(error_status) == 2


class TestCompactFormatter:
    """Test compact formatter."""

    def test_compact_formatter(self):
        """Test compact formatter output."""
        status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.WARNING,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={
                "notebook": WorkerTypeStats(
                    worker_type="notebook",
                    execution_mode="direct",
                    total=2,
                    idle=0,
                    busy=2,
                    hung=0,
                    dead=0,
                )
            },
            queue=QueueStats(
                pending=10,
                processing=2,
                completed_last_hour=50,
                failed_last_hour=5,
            ),
            warnings=["High queue depth"],
        )

        formatter = CompactFormatter()
        output = formatter.format(status)

        assert "warning" in output
        assert "2 notebook" in output
        assert "10 pending" in output

    def test_compact_formatter_healthy(self):
        """Test compact formatter with healthy status."""
        status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={
                "notebook": WorkerTypeStats(
                    worker_type="notebook",
                    execution_mode="direct",
                    total=2,
                    idle=2,
                    busy=0,
                    hung=0,
                    dead=0,
                )
            },
            queue=QueueStats(
                pending=0,
                processing=0,
                completed_last_hour=50,
                failed_last_hour=0,
            ),
        )

        formatter = CompactFormatter()
        output = formatter.format(status)

        assert "healthy" in output
        assert "2 notebook (2 idle, 0 busy)" in output


class TestTableFormatter:
    """Test table formatter."""

    def test_table_formatter_basic(self):
        """Test table formatter with basic status."""
        status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(
                path="/path/to/db", accessible=True, exists=True, size_bytes=102400
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
                )
            },
            queue=QueueStats(pending=5, processing=2, completed_last_hour=100, failed_last_hour=2),
        )

        formatter = TableFormatter(use_color=False)
        output = formatter.format(status)

        assert "CLX System Status" in output
        assert "Notebook Workers: 2 total" in output
        assert "Pending:    5 jobs" in output

    def test_table_formatter_no_workers(self):
        """Test table formatter with no workers."""
        status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.ERROR,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={
                "notebook": WorkerTypeStats(
                    worker_type="notebook",
                    execution_mode=None,
                    total=0,
                    idle=0,
                    busy=0,
                    hung=0,
                    dead=0,
                )
            },
            queue=QueueStats(pending=0, processing=0, completed_last_hour=0, failed_last_hour=0),
            errors=["No workers registered"],
        )

        formatter = TableFormatter(use_color=False)
        output = formatter.format(status)

        assert "No workers registered" in output

    def test_table_formatter_workers_only(self):
        """Test table formatter with workers_only flag."""
        status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={
                "notebook": WorkerTypeStats(
                    worker_type="notebook",
                    execution_mode="direct",
                    total=2,
                    idle=2,
                    busy=0,
                    hung=0,
                    dead=0,
                )
            },
            queue=QueueStats(pending=0, processing=0, completed_last_hour=0, failed_last_hour=0),
        )

        formatter = TableFormatter(use_color=False)
        output = formatter.format(status, workers_only=True)

        assert "Notebook Workers" in output
        assert "Job Queue Status" not in output

    def test_table_formatter_jobs_only(self):
        """Test table formatter with jobs_only flag."""
        status = StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
            workers={},
            queue=QueueStats(pending=5, processing=2, completed_last_hour=100, failed_last_hour=2),
        )

        formatter = TableFormatter(use_color=False)
        output = formatter.format(status, jobs_only=True)

        assert "Job Queue Status" in output
        assert "Workers by Type" not in output

    def test_table_formatter_elapsed_time_format(self):
        """Test elapsed time formatting."""
        formatter = TableFormatter(use_color=False)

        assert formatter._format_elapsed(30) == "30s"
        assert formatter._format_elapsed(90) == "1:30"
        assert formatter._format_elapsed(3661) == "1:01:01"
