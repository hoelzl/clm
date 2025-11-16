"""Integration tests for web API endpoints."""

import pytest
from pathlib import Path
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestAPIEndpoints:
    """Test API endpoints with TestClient."""

    @pytest.fixture
    def test_db(self, tmp_path):
        """Create a test database."""
        from clx.infrastructure.database.schema import init_database

        db_path = tmp_path / "test_api.db"
        init_database(db_path)
        return db_path

    @pytest.fixture
    def client(self, test_db):
        """Create test client with test database."""
        from clx.web.app import create_app

        app = create_app(
            db_path=test_db,
            host="127.0.0.1",
            port=8000,
        )

        return TestClient(app)

    def test_health_endpoint(self, client):
        """Test /api/health endpoint."""
        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "database_path" in data

    def test_version_endpoint(self, client):
        """Test /api/version endpoint."""
        response = client.get("/api/version")

        assert response.status_code == 200
        data = response.json()
        assert "clx_version" in data
        assert "api_version" in data
        assert data["api_version"] == "1.0"

    def test_status_endpoint(self, client):
        """Test /api/status endpoint."""
        response = client.get("/api/status")

        assert response.status_code == 200
        data = response.json()

        # Verify structure
        assert "status" in data  # healthy/warning/error
        assert "timestamp" in data
        assert "database" in data
        assert "workers" in data
        assert "queue" in data

        # Database should be accessible
        assert data["database"]["accessible"] is True

    def test_workers_endpoint(self, client):
        """Test /api/workers endpoint."""
        response = client.get("/api/workers")

        assert response.status_code == 200
        data = response.json()

        assert "workers" in data
        assert "total" in data
        assert isinstance(data["workers"], list)
        assert data["total"] == 0  # No workers in test DB

    def test_jobs_endpoint(self, client):
        """Test /api/jobs endpoint."""
        response = client.get("/api/jobs")

        assert response.status_code == 200
        data = response.json()

        assert "jobs" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert isinstance(data["jobs"], list)

    def test_jobs_endpoint_with_pagination(self, client):
        """Test /api/jobs endpoint with pagination."""
        response = client.get("/api/jobs?page=2&page_size=25")

        assert response.status_code == 200
        data = response.json()

        assert data["page"] == 2
        assert data["page_size"] == 25

    def test_jobs_endpoint_with_status_filter(self, client):
        """Test /api/jobs endpoint with status filter."""
        response = client.get("/api/jobs?status=pending")

        assert response.status_code == 200
        data = response.json()

        assert isinstance(data["jobs"], list)

    def test_root_endpoint(self, client):
        """Test / root endpoint."""
        response = client.get("/")

        assert response.status_code == 200
        # Should return HTML
        assert "text/html" in response.headers["content-type"]
        assert "CLX Dashboard API" in response.text

    def test_docs_endpoint(self, client):
        """Test /docs endpoint (Swagger UI)."""
        response = client.get("/docs")

        assert response.status_code == 200

    def test_openapi_endpoint(self, client):
        """Test /openapi.json endpoint."""
        response = client.get("/openapi.json")

        assert response.status_code == 200
        data = response.json()

        assert "openapi" in data
        assert "info" in data
        assert data["info"]["title"] == "CLX Dashboard API"
