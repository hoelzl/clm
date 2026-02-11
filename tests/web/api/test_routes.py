"""Tests for web API routes module.

This module tests the FastAPI REST API routes including:
- Health check endpoint
- Version endpoint
- Status endpoint
- Workers list endpoint
- Jobs list endpoint with pagination
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Check if FastAPI and dependencies are available
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

# Skip all tests if FastAPI not available
pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")


@pytest.fixture
def mock_monitor_service():
    """Create a mock MonitorService."""
    service = MagicMock()
    service.db_path = Path("/test/db.sqlite")
    return service


@pytest.fixture
def test_app(mock_monitor_service):
    """Create a test FastAPI app with routes."""
    from clm.web.api.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.monitor_service = mock_monitor_service
    return app


@pytest.fixture
def client(test_app):
    """Create a test client."""
    return TestClient(test_app)


class TestHealthCheckEndpoint:
    """Test the /api/health endpoint."""

    def test_health_check_returns_200(self, client):
        """Health check should return 200 OK."""
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_health_check_returns_status_ok(self, client):
        """Health check should return status 'ok'."""
        response = client.get("/api/health")
        data = response.json()
        assert data["status"] == "ok"

    def test_health_check_includes_version(self, client):
        """Health check should include version."""
        response = client.get("/api/health")
        data = response.json()
        assert "version" in data
        assert isinstance(data["version"], str)

    def test_health_check_includes_database_path(self, client, mock_monitor_service):
        """Health check should include database path."""
        response = client.get("/api/health")
        data = response.json()
        assert "database_path" in data
        assert data["database_path"] == str(mock_monitor_service.db_path)


class TestVersionEndpoint:
    """Test the /api/version endpoint."""

    def test_version_returns_200(self, client):
        """Version endpoint should return 200 OK."""
        response = client.get("/api/version")
        assert response.status_code == 200

    def test_version_includes_clm_version(self, client):
        """Version should include CLM version."""
        response = client.get("/api/version")
        data = response.json()
        assert "clm_version" in data
        assert isinstance(data["clm_version"], str)

    def test_version_includes_api_version(self, client):
        """Version should include API version."""
        response = client.get("/api/version")
        data = response.json()
        assert "api_version" in data
        assert data["api_version"] == "1.0"


class TestStatusEndpoint:
    """Test the /api/status endpoint."""

    @pytest.fixture
    def mock_status_response(self):
        """Create a valid StatusResponse."""
        from clm.web.models import (
            DatabaseInfoResponse,
            QueueStatsResponse,
            StatusResponse,
        )

        return StatusResponse(
            status="healthy",
            timestamp=datetime.now(),
            database=DatabaseInfoResponse(
                path="/test/db.sqlite",
                accessible=True,
                exists=True,
            ),
            workers={},
            queue=QueueStatsResponse(
                pending=0,
                processing=0,
                completed_last_hour=0,
                failed_last_hour=0,
            ),
        )

    def test_status_returns_200(self, client, mock_monitor_service, mock_status_response):
        """Status endpoint should return 200 OK."""
        mock_monitor_service.get_status.return_value = mock_status_response
        response = client.get("/api/status")
        assert response.status_code == 200

    def test_status_calls_monitor_service(self, client, mock_monitor_service, mock_status_response):
        """Status should delegate to MonitorService."""
        mock_monitor_service.get_status.return_value = mock_status_response

        client.get("/api/status")

        mock_monitor_service.get_status.assert_called_once()

    def test_status_returns_500_on_error(self, client, mock_monitor_service):
        """Status should return 500 on error."""
        mock_monitor_service.get_status.side_effect = RuntimeError("Database error")

        response = client.get("/api/status")

        assert response.status_code == 500
        assert "Error getting status" in response.json()["detail"]


class TestWorkersEndpoint:
    """Test the /api/workers endpoint."""

    @pytest.fixture
    def mock_workers_response(self):
        """Create a valid WorkersListResponse."""
        from clm.web.models import WorkersListResponse

        return WorkersListResponse(workers=[], total=0)

    def test_workers_returns_200(self, client, mock_monitor_service, mock_workers_response):
        """Workers endpoint should return 200 OK."""
        mock_monitor_service.get_workers.return_value = mock_workers_response
        response = client.get("/api/workers")
        assert response.status_code == 200

    def test_workers_calls_monitor_service(
        self, client, mock_monitor_service, mock_workers_response
    ):
        """Workers should delegate to MonitorService."""
        mock_monitor_service.get_workers.return_value = mock_workers_response

        client.get("/api/workers")

        mock_monitor_service.get_workers.assert_called_once()

    def test_workers_returns_500_on_error(self, client, mock_monitor_service):
        """Workers should return 500 on error."""
        mock_monitor_service.get_workers.side_effect = RuntimeError("Error")

        response = client.get("/api/workers")

        assert response.status_code == 500
        assert "Error getting workers" in response.json()["detail"]


class TestJobsEndpoint:
    """Test the /api/jobs endpoint."""

    @pytest.fixture
    def make_job_summary(self):
        """Factory fixture to create valid JobSummary objects."""
        from clm.web.models import JobSummary

        def _make_job(job_id=1):
            return JobSummary(
                job_id=job_id,
                job_type="notebook",
                status="completed",
                input_file="/test/input.ipynb",
                output_file="/test/output.html",
                created_at=datetime.now(),
            )

        return _make_job

    def test_jobs_returns_200(self, client, mock_monitor_service):
        """Jobs endpoint should return 200 OK."""
        mock_monitor_service.get_jobs.return_value = []
        response = client.get("/api/jobs")
        assert response.status_code == 200

    def test_jobs_calls_monitor_service(self, client, mock_monitor_service):
        """Jobs should delegate to MonitorService."""
        mock_monitor_service.get_jobs.return_value = []

        client.get("/api/jobs")

        mock_monitor_service.get_jobs.assert_called_once()

    def test_jobs_default_pagination(self, client, mock_monitor_service):
        """Jobs should use default pagination (page=1, page_size=50)."""
        mock_monitor_service.get_jobs.return_value = []

        client.get("/api/jobs")

        call_kwargs = mock_monitor_service.get_jobs.call_args[1]
        assert call_kwargs["offset"] == 0
        assert call_kwargs["limit"] == 50

    def test_jobs_custom_pagination(self, client, mock_monitor_service):
        """Jobs should accept custom pagination parameters."""
        mock_monitor_service.get_jobs.return_value = []

        client.get("/api/jobs?page=3&page_size=25")

        call_kwargs = mock_monitor_service.get_jobs.call_args[1]
        # page 3 with page_size 25 means offset = (3-1) * 25 = 50
        assert call_kwargs["offset"] == 50
        assert call_kwargs["limit"] == 25

    def test_jobs_status_filter(self, client, mock_monitor_service):
        """Jobs should accept status filter."""
        mock_monitor_service.get_jobs.return_value = []

        client.get("/api/jobs?status=pending")

        call_kwargs = mock_monitor_service.get_jobs.call_args[1]
        assert call_kwargs["status"] == "pending"

    def test_jobs_response_includes_pagination_info(self, client, mock_monitor_service):
        """Jobs response should include pagination info."""
        mock_monitor_service.get_jobs.return_value = []

        response = client.get("/api/jobs?page=2&page_size=10")
        data = response.json()

        assert "page" in data
        assert data["page"] == 2
        assert "page_size" in data
        assert data["page_size"] == 10

    def test_jobs_response_includes_total(self, client, mock_monitor_service, make_job_summary):
        """Jobs response should include total count."""
        mock_monitor_service.get_jobs.return_value = [make_job_summary(i) for i in range(5)]

        response = client.get("/api/jobs?page=1&page_size=10")
        data = response.json()

        assert "total" in data
        # When results < page_size, total = offset + len(results) = 0 + 5 = 5
        assert data["total"] == 5

    def test_jobs_returns_500_on_error(self, client, mock_monitor_service):
        """Jobs should return 500 on error."""
        mock_monitor_service.get_jobs.side_effect = RuntimeError("Error")

        response = client.get("/api/jobs")

        assert response.status_code == 500
        assert "Error getting jobs" in response.json()["detail"]

    def test_jobs_page_size_validation_min(self, client, mock_monitor_service):
        """Jobs should validate page_size minimum (1)."""
        response = client.get("/api/jobs?page_size=0")
        assert response.status_code == 422  # Validation error

    def test_jobs_page_size_validation_max(self, client, mock_monitor_service):
        """Jobs should validate page_size maximum (200)."""
        response = client.get("/api/jobs?page_size=201")
        assert response.status_code == 422  # Validation error

    def test_jobs_page_validation_min(self, client, mock_monitor_service):
        """Jobs should validate page minimum (1)."""
        response = client.get("/api/jobs?page=0")
        assert response.status_code == 422  # Validation error


class TestGetMonitorService:
    """Test the get_monitor_service helper function."""

    def test_get_monitor_service_returns_from_app_state(self, test_app, mock_monitor_service):
        """Should return monitor service from app state."""
        from clm.web.api.routes import get_monitor_service

        # Create a mock request with app state
        mock_request = MagicMock()
        mock_request.app.state.monitor_service = mock_monitor_service

        service = get_monitor_service(mock_request)

        assert service is mock_monitor_service


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def make_job_summary(self):
        """Factory fixture to create valid JobSummary objects."""
        from clm.web.models import JobSummary

        def _make_job(job_id=1):
            return JobSummary(
                job_id=job_id,
                job_type="notebook",
                status="completed",
                input_file="/test/input.ipynb",
                output_file="/test/output.html",
                created_at=datetime.now(),
            )

        return _make_job

    def test_jobs_full_page_calculates_total_correctly(
        self, client, mock_monitor_service, make_job_summary
    ):
        """When results equal page_size, total should be offset + page_size."""
        # Return exactly page_size results
        mock_monitor_service.get_jobs.return_value = [make_job_summary(i) for i in range(50)]

        response = client.get("/api/jobs?page=2&page_size=50")
        data = response.json()

        # When len(results) == page_size, there might be more
        # total = len(jobs) + offset = 50 + 50 = 100
        assert data["total"] == 100

    def test_jobs_partial_page_calculates_total_correctly(
        self, client, mock_monitor_service, make_job_summary
    ):
        """When results less than page_size, total should be offset + len(results)."""
        # Return fewer than page_size results
        mock_monitor_service.get_jobs.return_value = [make_job_summary(i) for i in range(30)]

        response = client.get("/api/jobs?page=3&page_size=50")
        data = response.json()

        # offset = (3-1) * 50 = 100
        # total = 100 + 30 = 130
        assert data["total"] == 130
