"""Tests for discovery module.

This module tests worker discovery and health checking including:
- Worker discovery from database
- Filtering by type and status
- Health check logic (status, heartbeat, process)
- Healthy worker counting
- Worker summary statistics
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clx.infrastructure.workers.discovery import (
    DiscoveredWorker,
    WorkerDiscovery,
)


@pytest.fixture
def mock_job_queue():
    """Create a mock JobQueue."""
    with patch("clx.infrastructure.workers.discovery.JobQueue") as mock_class:
        mock_queue = MagicMock()
        mock_class.return_value = mock_queue
        yield mock_queue


@pytest.fixture
def worker_discovery(mock_job_queue, tmp_path):
    """Create a WorkerDiscovery instance with mocked JobQueue."""
    discovery = WorkerDiscovery(tmp_path / "test.db")
    return discovery


@pytest.fixture
def make_db_row():
    """Factory to create mock database rows."""

    def _make_row(
        db_id=1,
        worker_type="notebook",
        container_id="direct-worker-1",
        status="idle",
        last_heartbeat=None,
        jobs_processed=0,
        jobs_failed=0,
        started_at=None,
    ):
        if last_heartbeat is None:
            last_heartbeat = datetime.now(timezone.utc).isoformat()
        if started_at is None:
            started_at = datetime.now(timezone.utc).isoformat()

        # Strip timezone info for database format
        if isinstance(last_heartbeat, str):
            last_heartbeat_str = last_heartbeat.replace("+00:00", "").replace("Z", "")
        else:
            last_heartbeat_str = last_heartbeat.isoformat().replace("+00:00", "").replace("Z", "")

        if isinstance(started_at, str):
            started_at_str = started_at.replace("+00:00", "").replace("Z", "")
        else:
            started_at_str = started_at.isoformat().replace("+00:00", "").replace("Z", "")

        return (
            db_id,
            worker_type,
            container_id,
            status,
            last_heartbeat_str,
            jobs_processed,
            jobs_failed,
            started_at_str,
        )

    return _make_row


class TestDiscoveredWorker:
    """Test the DiscoveredWorker dataclass."""

    def test_discovered_worker_fields(self):
        """DiscoveredWorker should have all expected fields."""
        now = datetime.now(timezone.utc)
        worker = DiscoveredWorker(
            db_id=1,
            worker_type="notebook",
            executor_id="direct-worker-1",
            status="idle",
            last_heartbeat=now,
            jobs_processed=10,
            jobs_failed=1,
            started_at=now,
            is_docker=False,
            is_healthy=True,
        )

        assert worker.db_id == 1
        assert worker.worker_type == "notebook"
        assert worker.executor_id == "direct-worker-1"
        assert worker.status == "idle"
        assert worker.last_heartbeat == now
        assert worker.jobs_processed == 10
        assert worker.jobs_failed == 1
        assert worker.started_at == now
        assert worker.is_docker is False
        assert worker.is_healthy is True


class TestWorkerDiscoveryInit:
    """Test WorkerDiscovery initialization."""

    def test_init_creates_job_queue(self, tmp_path):
        """Should create a JobQueue with the database path."""
        with patch("clx.infrastructure.workers.discovery.JobQueue") as mock_class:
            db_path = tmp_path / "test.db"
            discovery = WorkerDiscovery(db_path)

            mock_class.assert_called_once_with(db_path)
            assert discovery.db_path == db_path

    def test_init_with_executors(self, tmp_path):
        """Should accept executors dict."""
        with patch("clx.infrastructure.workers.discovery.JobQueue"):
            mock_executor = MagicMock()
            executors = {"direct": mock_executor}
            discovery = WorkerDiscovery(tmp_path / "test.db", executors=executors)

            assert discovery.executors == executors

    def test_init_without_executors(self, tmp_path):
        """Should default to empty executors dict."""
        with patch("clx.infrastructure.workers.discovery.JobQueue"):
            discovery = WorkerDiscovery(tmp_path / "test.db")
            assert discovery.executors == {}


class TestDiscoverWorkers:
    """Test the discover_workers method."""

    def test_discover_all_workers(self, worker_discovery, mock_job_queue, make_db_row):
        """Should return all workers when no filters."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            make_db_row(db_id=1, worker_type="notebook"),
            make_db_row(db_id=2, worker_type="plantuml"),
        ]
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        workers = worker_discovery.discover_workers()

        assert len(workers) == 2
        assert workers[0].db_id == 1
        assert workers[1].db_id == 2

    def test_discover_workers_by_type(self, worker_discovery, mock_job_queue, make_db_row):
        """Should filter by worker type."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            make_db_row(db_id=1, worker_type="notebook"),
        ]
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        workers = worker_discovery.discover_workers(worker_type="notebook")

        # Verify query contains type filter
        call_args = mock_conn.execute.call_args
        query = call_args[0][0]
        params = call_args[0][1]
        assert "worker_type = ?" in query
        assert "notebook" in params

    def test_discover_workers_by_status(self, worker_discovery, mock_job_queue, make_db_row):
        """Should filter by status."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            make_db_row(db_id=1, status="idle"),
        ]
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        workers = worker_discovery.discover_workers(status_filter=["idle", "busy"])

        call_args = mock_conn.execute.call_args
        query = call_args[0][0]
        params = call_args[0][1]
        assert "status IN" in query
        assert "idle" in params
        assert "busy" in params

    def test_discover_workers_combined_filters(self, worker_discovery, mock_job_queue, make_db_row):
        """Should combine type and status filters."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        worker_discovery.discover_workers(worker_type="notebook", status_filter=["idle"])

        call_args = mock_conn.execute.call_args
        query = call_args[0][0]
        assert "worker_type = ?" in query
        assert "status IN" in query
        assert "AND" in query

    def test_discover_workers_detects_docker(self, worker_discovery, mock_job_queue, make_db_row):
        """Should detect Docker vs direct workers."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            make_db_row(db_id=1, container_id="direct-worker-1"),
            make_db_row(db_id=2, container_id="abc123container"),
        ]
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        workers = worker_discovery.discover_workers()

        assert workers[0].is_docker is False  # direct-* prefix
        assert workers[1].is_docker is True  # No direct- prefix


class TestCheckWorkerHealth:
    """Test the check_worker_health method."""

    def test_healthy_worker_idle_status(self, worker_discovery):
        """Idle worker with recent heartbeat should be healthy."""
        worker = DiscoveredWorker(
            db_id=1,
            worker_type="notebook",
            executor_id="direct-1",
            status="idle",
            last_heartbeat=datetime.now(timezone.utc),
            jobs_processed=0,
            jobs_failed=0,
            started_at=datetime.now(timezone.utc),
            is_docker=False,
            is_healthy=False,
        )

        result = worker_discovery.check_worker_health(worker)
        assert result is True

    def test_healthy_worker_busy_status(self, worker_discovery):
        """Busy worker with recent heartbeat should be healthy."""
        worker = DiscoveredWorker(
            db_id=1,
            worker_type="notebook",
            executor_id="direct-1",
            status="busy",
            last_heartbeat=datetime.now(timezone.utc),
            jobs_processed=0,
            jobs_failed=0,
            started_at=datetime.now(timezone.utc),
            is_docker=False,
            is_healthy=False,
        )

        result = worker_discovery.check_worker_health(worker)
        assert result is True

    def test_unhealthy_status(self, worker_discovery):
        """Worker with non-idle/busy status should be unhealthy."""
        for status in ["stopped", "failed", "starting", "unknown"]:
            worker = DiscoveredWorker(
                db_id=1,
                worker_type="notebook",
                executor_id="direct-1",
                status=status,
                last_heartbeat=datetime.now(timezone.utc),
                jobs_processed=0,
                jobs_failed=0,
                started_at=datetime.now(timezone.utc),
                is_docker=False,
                is_healthy=False,
            )

            result = worker_discovery.check_worker_health(worker)
            assert result is False, f"Status '{status}' should be unhealthy"

    def test_stale_heartbeat_unhealthy(self, worker_discovery):
        """Worker with stale heartbeat (>30s) should be unhealthy."""
        worker = DiscoveredWorker(
            db_id=1,
            worker_type="notebook",
            executor_id="direct-1",
            status="idle",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=35),
            jobs_processed=0,
            jobs_failed=0,
            started_at=datetime.now(timezone.utc),
            is_docker=False,
            is_healthy=False,
        )

        result = worker_discovery.check_worker_health(worker)
        assert result is False

    def test_recent_heartbeat_healthy(self, worker_discovery):
        """Worker with recent heartbeat (<30s) should be healthy."""
        worker = DiscoveredWorker(
            db_id=1,
            worker_type="notebook",
            executor_id="direct-1",
            status="idle",
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=25),
            jobs_processed=0,
            jobs_failed=0,
            started_at=datetime.now(timezone.utc),
            is_docker=False,
            is_healthy=False,
        )

        result = worker_discovery.check_worker_health(worker)
        assert result is True

    def test_executor_check_running(self, tmp_path):
        """Should check executor if available and worker is running."""
        with patch("clx.infrastructure.workers.discovery.JobQueue"):
            mock_executor = MagicMock()
            mock_executor.is_worker_running.return_value = True

            discovery = WorkerDiscovery(tmp_path / "test.db", executors={"direct": mock_executor})

            worker = DiscoveredWorker(
                db_id=1,
                worker_type="notebook",
                executor_id="direct-1",
                status="idle",
                last_heartbeat=datetime.now(timezone.utc),
                jobs_processed=0,
                jobs_failed=0,
                started_at=datetime.now(timezone.utc),
                is_docker=False,
                is_healthy=False,
            )

            result = discovery.check_worker_health(worker)
            assert result is True
            mock_executor.is_worker_running.assert_called_once_with("direct-1")

    def test_executor_check_not_running(self, tmp_path):
        """Should return unhealthy if executor says worker not running."""
        with patch("clx.infrastructure.workers.discovery.JobQueue"):
            mock_executor = MagicMock()
            mock_executor.is_worker_running.return_value = False

            discovery = WorkerDiscovery(tmp_path / "test.db", executors={"direct": mock_executor})

            worker = DiscoveredWorker(
                db_id=1,
                worker_type="notebook",
                executor_id="direct-1",
                status="idle",
                last_heartbeat=datetime.now(timezone.utc),
                jobs_processed=0,
                jobs_failed=0,
                started_at=datetime.now(timezone.utc),
                is_docker=False,
                is_healthy=False,
            )

            result = discovery.check_worker_health(worker)
            assert result is False

    def test_executor_check_error_returns_unhealthy(self, tmp_path):
        """Should return unhealthy if executor check raises error."""
        with patch("clx.infrastructure.workers.discovery.JobQueue"):
            mock_executor = MagicMock()
            mock_executor.is_worker_running.side_effect = RuntimeError("check failed")

            discovery = WorkerDiscovery(tmp_path / "test.db", executors={"direct": mock_executor})

            worker = DiscoveredWorker(
                db_id=1,
                worker_type="notebook",
                executor_id="direct-1",
                status="idle",
                last_heartbeat=datetime.now(timezone.utc),
                jobs_processed=0,
                jobs_failed=0,
                started_at=datetime.now(timezone.utc),
                is_docker=False,
                is_healthy=False,
            )

            result = discovery.check_worker_health(worker)
            assert result is False


class TestCountHealthyWorkers:
    """Test the count_healthy_workers method."""

    def test_count_healthy_workers(self, worker_discovery, mock_job_queue, make_db_row):
        """Should count only healthy workers of a type."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # Two workers, both with recent heartbeats and valid status
        now = datetime.now(timezone.utc)
        mock_cursor.fetchall.return_value = [
            make_db_row(db_id=1, worker_type="notebook", status="idle", last_heartbeat=now),
            make_db_row(db_id=2, worker_type="notebook", status="busy", last_heartbeat=now),
        ]
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        count = worker_discovery.count_healthy_workers("notebook")

        assert count == 2

    def test_count_excludes_unhealthy(self, worker_discovery, mock_job_queue, make_db_row):
        """Should exclude unhealthy workers from count."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        now = datetime.now(timezone.utc)
        stale = now - timedelta(seconds=60)
        mock_cursor.fetchall.return_value = [
            make_db_row(db_id=1, worker_type="notebook", status="idle", last_heartbeat=now),
            make_db_row(db_id=2, worker_type="notebook", status="idle", last_heartbeat=stale),
        ]
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        count = worker_discovery.count_healthy_workers("notebook")

        assert count == 1  # Only the one with recent heartbeat


class TestDatetimeCompatibility:
    """Test datetime compatibility between DiscoveredWorker and CLI operations.

    These tests verify that timezone-aware datetimes from DiscoveredWorker
    can be safely used with datetime operations in the CLI commands.
    """

    def test_worker_uptime_calculation_with_utc(self):
        """Test that uptime can be calculated from timezone-aware started_at.

        This is a regression test for a bug where workers_list command used
        datetime.now() (naive) with timezone-aware started_at, causing:
        TypeError: can't subtract offset-naive and offset-aware datetimes
        """
        now = datetime.now(timezone.utc)
        started_at = now - timedelta(hours=2, minutes=30)

        worker = DiscoveredWorker(
            db_id=1,
            worker_type="notebook",
            executor_id="direct-worker-1",
            status="idle",
            last_heartbeat=now,
            jobs_processed=10,
            jobs_failed=0,
            started_at=started_at,
            is_docker=False,
            is_healthy=True,
        )

        # This should work without TypeError when using datetime.now(timezone.utc)
        uptime = datetime.now(timezone.utc) - worker.started_at
        assert uptime.total_seconds() > 0
        # Uptime should be approximately 2.5 hours (allowing for test execution time)
        assert uptime.total_seconds() >= 2 * 3600

    def test_worker_stale_heartbeat_check_with_utc(self):
        """Test that stale heartbeat check works with timezone-aware datetimes.

        This is a regression test for a bug where workers_cleanup command used
        datetime.now() (naive) with timezone-aware last_heartbeat, causing:
        TypeError: can't subtract offset-naive and offset-aware datetimes
        """
        now = datetime.now(timezone.utc)
        stale_heartbeat = now - timedelta(minutes=5)

        worker = DiscoveredWorker(
            db_id=1,
            worker_type="notebook",
            executor_id="direct-worker-1",
            status="idle",
            last_heartbeat=stale_heartbeat,
            jobs_processed=10,
            jobs_failed=0,
            started_at=now - timedelta(hours=1),
            is_docker=False,
            is_healthy=False,
        )

        # This should work without TypeError when using datetime.now(timezone.utc)
        heartbeat_age = datetime.now(timezone.utc) - worker.last_heartbeat
        is_stale = heartbeat_age.total_seconds() > 60
        assert is_stale is True

    def test_datetime_now_without_timezone_raises_error(self):
        """Verify that naive datetime subtraction with timezone-aware raises error.

        This test documents the original bug behavior to ensure we don't regress.
        """
        now_utc = datetime.now(timezone.utc)
        now_naive = datetime.now()

        with pytest.raises(TypeError, match="can't subtract offset-naive and offset-aware"):
            _ = now_naive - now_utc


class TestGetWorkerSummary:
    """Test the get_worker_summary method."""

    def test_worker_summary_structure(self, worker_discovery, mock_job_queue, make_db_row):
        """Should return summary dict with correct structure."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        now = datetime.now(timezone.utc)
        mock_cursor.fetchall.return_value = [
            make_db_row(db_id=1, worker_type="notebook", status="idle", last_heartbeat=now),
            make_db_row(db_id=2, worker_type="notebook", status="idle", last_heartbeat=now),
            make_db_row(db_id=3, worker_type="plantuml", status="idle", last_heartbeat=now),
        ]
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        summary = worker_discovery.get_worker_summary()

        assert "notebook" in summary
        assert "plantuml" in summary
        assert "total" in summary["notebook"]
        assert "healthy" in summary["notebook"]
        assert "unhealthy" in summary["notebook"]

    def test_worker_summary_counts(self, worker_discovery, mock_job_queue, make_db_row):
        """Should count workers correctly by type."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        now = datetime.now(timezone.utc)
        stale = now - timedelta(seconds=60)
        mock_cursor.fetchall.return_value = [
            make_db_row(db_id=1, worker_type="notebook", status="idle", last_heartbeat=now),
            make_db_row(db_id=2, worker_type="notebook", status="idle", last_heartbeat=stale),
            make_db_row(db_id=3, worker_type="plantuml", status="idle", last_heartbeat=now),
        ]
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        summary = worker_discovery.get_worker_summary()

        assert summary["notebook"]["total"] == 2
        assert summary["notebook"]["healthy"] == 1
        assert summary["notebook"]["unhealthy"] == 1
        assert summary["plantuml"]["total"] == 1
        assert summary["plantuml"]["healthy"] == 1
        assert summary["plantuml"]["unhealthy"] == 0

    def test_worker_summary_empty(self, worker_discovery, mock_job_queue):
        """Should return empty dict when no workers."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.execute.return_value = mock_cursor
        mock_job_queue._get_conn.return_value = mock_conn

        summary = worker_discovery.get_worker_summary()

        assert summary == {}
