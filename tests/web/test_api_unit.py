"""Unit tests for web API."""

from datetime import datetime
from pathlib import Path

import pytest

from clm.web.models import (
    BusyWorkerDetail,
    HealthResponse,
    StatusResponse,
    VersionResponse,
    WorkerTypeStatsResponse,
)


class TestAPIModels:
    """Test API models."""

    def test_health_response_creation(self):
        """Test creating HealthResponse."""
        response = HealthResponse(
            status="ok",
            version="1.0.2",
            database_path="/path/to/db",
        )

        assert response.status == "ok"
        assert response.version == "1.0.2"
        assert response.database_path == "/path/to/db"

    def test_version_response_creation(self):
        """Test creating VersionResponse."""
        response = VersionResponse(
            clm_version="1.0.2",
            api_version="1.0",
        )

        assert response.clm_version == "1.0.2"
        assert response.api_version == "1.0"

    def test_worker_type_stats_response(self):
        """Test WorkerTypeStatsResponse with busy workers."""
        busy_workers = [
            BusyWorkerDetail(
                worker_id="worker-1",
                job_id="job-123",
                document_path="/path/to/doc.ipynb",
                elapsed_seconds=45,
            )
        ]

        response = WorkerTypeStatsResponse(
            worker_type="notebook",
            execution_mode="direct",
            total=2,
            idle=1,
            busy=1,
            hung=0,
            dead=0,
            busy_workers=busy_workers,
        )

        assert response.worker_type == "notebook"
        assert response.total == 2
        assert response.busy == 1
        assert len(response.busy_workers) == 1
        assert response.busy_workers[0].worker_id == "worker-1"


@pytest.mark.integration
class TestMonitorService:
    """Test MonitorService integration."""

    def test_monitor_service_get_status_nonexistent_db(self):
        """Test getting status with nonexistent database."""
        from clm.web.services.monitor_service import MonitorService

        db_path = Path("/tmp/nonexistent_test.db")
        service = MonitorService(db_path=db_path)

        status = service.get_status()

        assert status is not None
        assert isinstance(status, StatusResponse)
        # Database doesn't exist
        assert not status.database.accessible

    def test_monitor_service_get_workers_empty(self):
        """Test getting workers with nonexistent database."""
        from clm.web.services.monitor_service import MonitorService

        db_path = Path("/tmp/nonexistent_test.db")
        service = MonitorService(db_path=db_path)

        workers = service.get_workers()

        assert workers is not None
        assert workers.total == 0
        assert len(workers.workers) == 0

    def test_monitor_service_get_jobs_empty(self):
        """Test getting jobs with nonexistent database."""
        from clm.web.services.monitor_service import MonitorService

        db_path = Path("/tmp/nonexistent_test.db")
        service = MonitorService(db_path=db_path)

        jobs = service.get_jobs(limit=10)

        assert isinstance(jobs, list)
        assert len(jobs) == 0
