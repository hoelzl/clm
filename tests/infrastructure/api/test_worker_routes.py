"""Tests for worker_routes module."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clm.infrastructure.api.server import WorkerApiServer
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def db_path():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Cleanup
    import gc
    import sqlite3

    gc.collect()

    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass

    try:
        path.unlink(missing_ok=True)
        for suffix in ["-wal", "-shm"]:
            wal_file = Path(str(path) + suffix)
            wal_file.unlink(missing_ok=True)
    except Exception:
        pass


@pytest.fixture
def client(db_path):
    """Create a test client for the Worker API."""
    server = WorkerApiServer(db_path)
    app = server._create_app()
    with TestClient(app) as client:
        yield client


class TestWorkerActivationEndpoint:
    """Tests for /api/worker/activate endpoint."""

    def test_activate_worker_success(self, client, db_path):
        """Test activating a pre-registered worker."""
        # Pre-register a worker with 'created' status
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("notebook", "test-container", "created"),
        )
        pre_registered_id = cursor.lastrowid
        queue.close()

        # Activate via API
        response = client.post(
            "/api/worker/activate",
            json={"worker_id": pre_registered_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["acknowledged"] is True
        assert "activated_at" in data

        # Verify status changed to 'idle'
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute("SELECT status FROM workers WHERE id = ?", (pre_registered_id,))
        status = cursor.fetchone()[0]
        queue.close()

        assert status == "idle"

    def test_activate_worker_not_found(self, client):
        """Test activating a non-existent worker returns 404."""
        response = client.post(
            "/api/worker/activate",
            json={"worker_id": 99999},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_activate_worker_wrong_status(self, client, db_path):
        """Test activating a worker not in 'created' status returns 400."""
        # Create a worker in 'idle' status
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("notebook", "test-container", "idle"),
        )
        worker_id = cursor.lastrowid
        queue.close()

        response = client.post(
            "/api/worker/activate",
            json={"worker_id": worker_id},
        )

        assert response.status_code == 400
        assert "has status 'idle', expected 'created'" in response.json()["detail"]

    def test_activate_worker_updates_heartbeat(self, client, db_path):
        """Test that activating a worker updates the heartbeat timestamp."""
        # Pre-register a worker with 'created' status and old heartbeat
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, last_heartbeat)
            VALUES (?, ?, 'created', datetime('now', '-1 hour'))
            """,
            ("notebook", "test-container"),
        )
        pre_registered_id = cursor.lastrowid

        # Get old heartbeat
        cursor = conn.execute(
            "SELECT last_heartbeat FROM workers WHERE id = ?", (pre_registered_id,)
        )
        old_heartbeat = cursor.fetchone()[0]
        queue.close()

        # Activate via API
        response = client.post(
            "/api/worker/activate",
            json={"worker_id": pre_registered_id},
        )

        assert response.status_code == 200

        # Verify heartbeat was updated
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute(
            "SELECT last_heartbeat FROM workers WHERE id = ?", (pre_registered_id,)
        )
        new_heartbeat = cursor.fetchone()[0]
        queue.close()

        assert new_heartbeat != old_heartbeat


class TestWorkerApiClient:
    """Tests for WorkerApiClient.activate() method."""

    def test_client_activate_success(self, db_path):
        """Test WorkerApiClient.activate() calls the API successfully."""
        from unittest.mock import MagicMock, patch

        from clm.infrastructure.api.client import WorkerApiClient

        # Mock the httpx.Client.request method
        with patch("httpx.Client.request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "acknowledged": True,
                "activated_at": "2025-01-01T00:00:00Z",
            }
            mock_response.raise_for_status = MagicMock()
            mock_request.return_value = mock_response

            client = WorkerApiClient("http://localhost:8765")
            client.activate(1)

            # Verify the correct endpoint was called
            mock_request.assert_called()
            call_args = mock_request.call_args
            assert call_args[0][0] == "POST"  # method
            assert "/api/worker/activate" in call_args[0][1]  # url
            assert call_args[1]["json"]["worker_id"] == 1

            client.close()

    def test_client_activate_failure(self, db_path):
        """Test WorkerApiClient.activate() raises error on failure."""
        from unittest.mock import MagicMock, patch

        import httpx

        from clm.infrastructure.api.client import WorkerApiClient, WorkerApiError

        # Mock the httpx.Client.request method to return an error
        with patch("httpx.Client.request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.text = "Worker not found"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404 Not Found",
                request=MagicMock(),
                response=mock_response,
            )
            mock_request.return_value = mock_response

            client = WorkerApiClient("http://localhost:8765")

            with pytest.raises(WorkerApiError):
                client.activate(99999)

            client.close()
