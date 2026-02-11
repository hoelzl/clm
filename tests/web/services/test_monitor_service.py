"""Unit tests for monitor_service module.

Tests the MonitorService class functionality:
- Job payload parsing
- Status response conversion
- Workers list retrieval
- Jobs list retrieval with filtering
"""

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.cli.status.models import (
    BusyWorkerInfo,
    DatabaseInfo,
    QueueStats,
    StatusInfo,
    SystemHealth,
    WorkerTypeStats,
)
from clm.web.models import StatusResponse, WorkersListResponse
from clm.web.services.monitor_service import MonitorService


class TestParseJobPayload:
    """Test _parse_job_payload method."""

    def test_parse_notebook_payload(self):
        """Should parse notebook job payload correctly."""
        service = MonitorService(Path("/tmp/test.db"))

        payload = json.dumps(
            {
                "format": "html",
                "prog_lang": "python",
                "language": "en",
                "kind": "completed",
            }
        )

        result = service._parse_job_payload("notebook", payload)

        assert result["output_format"] == "html"
        assert result["prog_lang"] == "python"
        assert result["language"] == "en"
        assert result["kind"] == "completed"

    def test_parse_plantuml_payload(self):
        """Should parse plantuml job payload correctly."""
        service = MonitorService(Path("/tmp/test.db"))

        payload = json.dumps(
            {
                "output_format": "png",
            }
        )

        result = service._parse_job_payload("plantuml", payload)

        assert result["output_format"] == "png"

    def test_parse_drawio_payload(self):
        """Should parse drawio job payload correctly."""
        service = MonitorService(Path("/tmp/test.db"))

        payload = json.dumps(
            {
                "output_format": "svg",
            }
        )

        result = service._parse_job_payload("drawio", payload)

        assert result["output_format"] == "svg"

    def test_parse_empty_payload(self):
        """Should return empty dict for None payload."""
        service = MonitorService(Path("/tmp/test.db"))

        result = service._parse_job_payload("notebook", None)

        assert result == {}

    def test_parse_invalid_json_payload(self):
        """Should return empty dict for invalid JSON payload."""
        service = MonitorService(Path("/tmp/test.db"))

        result = service._parse_job_payload("notebook", "not valid json")

        assert result == {}

    def test_parse_unknown_job_type(self):
        """Should return empty dict for unknown job types."""
        service = MonitorService(Path("/tmp/test.db"))

        payload = json.dumps({"foo": "bar"})

        result = service._parse_job_payload("unknown_type", payload)

        assert result == {}

    def test_parse_plantuml_default_format(self):
        """Plantuml should default to png if output_format not specified."""
        service = MonitorService(Path("/tmp/test.db"))

        payload = json.dumps({})  # No output_format

        result = service._parse_job_payload("plantuml", payload)

        assert result["output_format"] == "png"

    def test_parse_notebook_missing_fields(self):
        """Should handle notebook payload with missing fields gracefully."""
        service = MonitorService(Path("/tmp/test.db"))

        # Only format provided, others missing
        payload = json.dumps({"format": "code"})

        result = service._parse_job_payload("notebook", payload)

        assert result["output_format"] == "code"
        assert result.get("prog_lang") is None
        assert result.get("language") is None
        assert result.get("kind") is None


@pytest.mark.integration
class TestMonitorServiceWithDatabase:
    """Tests that use a real temporary database."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database with full schema."""
        from clm.infrastructure.database.schema import init_database

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        # Initialize with full database schema
        init_database(db_path)

        yield db_path

        # Cleanup - try multiple times for Windows file locks
        import time

        for _ in range(5):
            try:
                db_path.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.1)

    def test_get_workers_with_data(self, temp_db):
        """Should return workers from database."""
        # Insert test workers using actual schema columns
        conn = sqlite3.connect(temp_db)
        conn.execute("""
            INSERT INTO workers (container_id, worker_type, status, execution_mode, jobs_processed)
            VALUES ('worker-1', 'notebook', 'idle', 'direct', 5)
        """)
        conn.execute("""
            INSERT INTO workers (container_id, worker_type, status, execution_mode, jobs_processed)
            VALUES ('worker-2', 'plantuml', 'busy', 'docker', 10)
        """)
        conn.commit()
        conn.close()

        service = MonitorService(temp_db)
        result = service.get_workers()

        assert result.total == 2
        assert len(result.workers) == 2

        # Check worker details (worker_id comes from container_id in schema)
        worker_ids = [w.worker_id for w in result.workers]
        assert "worker-1" in worker_ids
        assert "worker-2" in worker_ids

    def test_get_workers_excludes_dead(self, temp_db):
        """Should exclude dead workers from listing."""
        conn = sqlite3.connect(temp_db)
        conn.execute("""
            INSERT INTO workers (container_id, worker_type, status)
            VALUES ('worker-1', 'notebook', 'idle')
        """)
        conn.execute("""
            INSERT INTO workers (container_id, worker_type, status)
            VALUES ('worker-2', 'notebook', 'dead')
        """)
        conn.commit()
        conn.close()

        service = MonitorService(temp_db)
        result = service.get_workers()

        # Only non-dead worker should be returned
        assert result.total == 1
        assert result.workers[0].worker_id == "worker-1"

    def test_get_jobs_with_data(self, temp_db):
        """Should return jobs from database."""
        conn = sqlite3.connect(temp_db)

        # Insert test jobs with all required columns
        conn.execute("""
            INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload, created_at)
            VALUES ('notebook', 'completed', '/path/to/doc.ipynb', '/output/doc.html', 'hash1', '{}', datetime('now'))
        """)
        conn.execute("""
            INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload, created_at)
            VALUES ('plantuml', 'pending', '/path/to/diagram.puml', '/output/diagram.png', 'hash2', '{}', datetime('now'))
        """)
        conn.commit()
        conn.close()

        service = MonitorService(temp_db)
        result = service.get_jobs(limit=10)

        assert len(result) == 2

    def test_get_jobs_filter_by_status(self, temp_db):
        """Should filter jobs by status."""
        conn = sqlite3.connect(temp_db)

        conn.execute("""
            INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload, created_at)
            VALUES ('notebook', 'completed', '/path/1.ipynb', '/out/1.html', 'h1', '{}', datetime('now'))
        """)
        conn.execute("""
            INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload, created_at)
            VALUES ('notebook', 'pending', '/path/2.ipynb', '/out/2.html', 'h2', '{}', datetime('now'))
        """)
        conn.execute("""
            INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload, created_at)
            VALUES ('notebook', 'completed', '/path/3.ipynb', '/out/3.html', 'h3', '{}', datetime('now'))
        """)
        conn.commit()
        conn.close()

        service = MonitorService(temp_db)

        # Get only completed jobs
        completed = service.get_jobs(status="completed", limit=10)
        assert len(completed) == 2
        for job in completed:
            assert job.status == "completed"

        # Get only pending jobs
        pending = service.get_jobs(status="pending", limit=10)
        assert len(pending) == 1
        assert pending[0].status == "pending"

    def test_get_jobs_with_limit_and_offset(self, temp_db):
        """Should support pagination with limit and offset."""
        conn = sqlite3.connect(temp_db)

        # Insert 5 jobs
        for i in range(5):
            conn.execute(f"""
                INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload, created_at)
                VALUES ('notebook', 'completed', '/path/{i}.ipynb', '/out/{i}.html', 'hash{i}', '{{}}', datetime('now', '-{i} seconds'))
            """)
        conn.commit()
        conn.close()

        service = MonitorService(temp_db)

        # Get first 2
        first_page = service.get_jobs(limit=2, offset=0)
        assert len(first_page) == 2

        # Get next 2
        second_page = service.get_jobs(limit=2, offset=2)
        assert len(second_page) == 2

        # Different jobs
        first_ids = [j.job_id for j in first_page]
        second_ids = [j.job_id for j in second_page]
        assert set(first_ids).isdisjoint(set(second_ids))

    def test_get_jobs_with_payload_parsing(self, temp_db):
        """Should parse job payloads to extract metadata."""
        conn = sqlite3.connect(temp_db)

        payload = json.dumps(
            {
                "format": "html",
                "prog_lang": "python",
                "language": "en",
                "kind": "speaker",
            }
        )

        conn.execute(
            """
            INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload, created_at)
            VALUES ('notebook', 'completed', '/path/doc.ipynb', '/out/doc.html', 'hash', ?, datetime('now'))
        """,
            (payload,),
        )
        conn.commit()
        conn.close()

        service = MonitorService(temp_db)
        jobs = service.get_jobs(limit=10)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.output_format == "html"
        assert job.prog_lang == "python"
        assert job.language == "en"
        assert job.kind == "speaker"

    def test_get_jobs_nonexistent_db(self):
        """Should return empty list for nonexistent database."""
        service = MonitorService(Path("/tmp/nonexistent_db_test.db"))
        jobs = service.get_jobs(limit=10)

        assert jobs == []

    def test_get_workers_nonexistent_db(self):
        """Should return empty WorkersListResponse for nonexistent database."""
        service = MonitorService(Path("/tmp/nonexistent_db_test.db"))
        result = service.get_workers()

        assert result.total == 0
        assert result.workers == []


class TestJobQueueCaching:
    """Test job queue instance caching."""

    def test_job_queue_created_lazily(self):
        """JobQueue should be created only when needed."""
        service = MonitorService(Path("/tmp/test.db"))

        # Initially no job queue
        assert service.job_queue is None

    @pytest.mark.integration
    def test_job_queue_reused(self):
        """Same JobQueue instance should be reused."""
        from clm.infrastructure.database.schema import init_database

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        # Initialize full database schema
        init_database(db_path)

        service = MonitorService(db_path)

        # Get job queue twice
        jq1 = service._get_job_queue()
        jq2 = service._get_job_queue()

        # Should be same instance
        assert jq1 is jq2

        # Cleanup
        import time

        for _ in range(5):
            try:
                db_path.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.1)


class TestGetStatus:
    """Test get_status method."""

    @pytest.fixture
    def mock_status_info(self):
        """Create a mock StatusInfo for testing."""
        return StatusInfo(
            timestamp=datetime.now(),
            health=SystemHealth.HEALTHY,
            database=DatabaseInfo(
                path="/path/to/db.db",
                accessible=True,
                exists=True,
                size_bytes=1024 * 100,
                last_modified=datetime.now(),
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
                            worker_id="worker-1",
                            job_id="job-1",
                            document_path="/path/to/doc.ipynb",
                            elapsed_seconds=30,
                            output_format="html",
                            prog_lang="python",
                            language="en",
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
            warnings=["Test warning"],
            errors=["Test error"],
        )

    def test_get_status_returns_status_response(self, mock_status_info):
        """Should return a StatusResponse."""
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert isinstance(result, StatusResponse)

    def test_get_status_converts_health(self, mock_status_info):
        """Should convert health status correctly."""
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert result.status == "healthy"

    def test_get_status_converts_database_info(self, mock_status_info):
        """Should convert database info correctly."""
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert result.database.path == "/path/to/db.db"
        assert result.database.accessible is True
        assert result.database.exists is True
        assert result.database.size_bytes == 1024 * 100

    def test_get_status_converts_worker_stats(self, mock_status_info):
        """Should convert worker stats correctly."""
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert "notebook" in result.workers
        notebook_stats = result.workers["notebook"]
        assert notebook_stats.total == 2
        assert notebook_stats.idle == 1
        assert notebook_stats.busy == 1
        assert notebook_stats.execution_mode == "direct"

    def test_get_status_converts_busy_workers(self, mock_status_info):
        """Should convert busy worker details correctly."""
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        notebook_stats = result.workers["notebook"]
        assert len(notebook_stats.busy_workers) == 1
        busy_worker = notebook_stats.busy_workers[0]
        assert busy_worker.worker_id == "worker-1"
        assert busy_worker.job_id == "job-1"
        assert busy_worker.elapsed_seconds == 30
        assert busy_worker.output_format == "html"

    def test_get_status_converts_queue_stats(self, mock_status_info):
        """Should convert queue stats correctly."""
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert result.queue.pending == 5
        assert result.queue.processing == 2
        assert result.queue.completed_last_hour == 100
        assert result.queue.failed_last_hour == 2
        assert result.queue.oldest_pending_seconds == 60

    def test_get_status_includes_warnings(self, mock_status_info):
        """Should include warnings."""
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert "Test warning" in result.warnings

    def test_get_status_includes_errors(self, mock_status_info):
        """Should include errors."""
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert "Test error" in result.errors

    def test_get_status_with_warning_health(self, mock_status_info):
        """Should handle warning health status."""
        mock_status_info.health = SystemHealth.WARNING
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert result.status == "warning"

    def test_get_status_with_error_health(self, mock_status_info):
        """Should handle error health status."""
        mock_status_info.health = SystemHealth.ERROR
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert result.status == "error"

    def test_get_status_empty_workers(self, mock_status_info):
        """Should handle empty workers dict."""
        mock_status_info.workers = {}
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        assert result.workers == {}

    def test_get_status_no_busy_workers(self, mock_status_info):
        """Should handle workers with no busy workers."""
        mock_status_info.workers["notebook"].busy_workers = []
        mock_status_info.workers["notebook"].busy = 0
        service = MonitorService(Path("/tmp/test.db"))

        with patch.object(service.status_collector, "collect", return_value=mock_status_info):
            result = service.get_status()

        notebook_stats = result.workers["notebook"]
        assert notebook_stats.busy_workers == []
