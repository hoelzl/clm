"""Endpoint tests for ``clm.infrastructure.api.worker_routes``.

Exercises every route except ``activate`` (already covered by
``test_worker_routes.py``): register, claim, status, heartbeat, cancelled,
unregister, and cache endpoints, plus their error paths.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from clm.infrastructure.api.server import WorkerApiServer
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "workers.db"
    init_database(path)
    return path


@pytest.fixture
def client(db_path: Path):
    server = WorkerApiServer(db_path)
    app = server._create_app()
    # raise_server_exceptions=False lets us observe the 500 that FastAPI
    # would return to a real HTTP client instead of re-raising the
    # exception through TestClient.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _seed_pending_job(db_path: Path, job_type: str = "notebook") -> int:
    queue = JobQueue(db_path)
    try:
        conn = queue._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO jobs (
                job_type, input_file, output_file, content_hash,
                payload, status, priority, attempts
            ) VALUES (?, ?, ?, ?, ?, 'pending', 0, 0)
            """,
            (job_type, "in.py", "out.html", "hash", "{}"),
        )
        job_id = cursor.lastrowid
        assert job_id is not None
        return job_id
    finally:
        queue.close()


# ---------------------------------------------------------------------------
# /register
# ---------------------------------------------------------------------------


class TestRegisterEndpoint:
    def test_register_inserts_worker_and_returns_id(self, client: TestClient) -> None:
        response = client.post(
            "/api/worker/register",
            json={
                "worker_type": "notebook",
                "container_id": "c1",
                "parent_pid": 1234,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["worker_id"] >= 1
        assert "registered_at" in data

    def test_register_returns_500_on_db_error(self, client: TestClient) -> None:
        # Patch a method used *inside* the try/except so the handler's
        # generic Exception -> HTTPException(500) path is exercised.
        with patch(
            "clm.infrastructure.database.job_queue.JobQueue._get_conn",
            side_effect=RuntimeError("db gone"),
        ):
            response = client.post(
                "/api/worker/register",
                json={
                    "worker_type": "notebook",
                    "container_id": "c1",
                    "parent_pid": None,
                },
            )

        assert response.status_code == 500
        assert "db gone" in response.json()["detail"]


# ---------------------------------------------------------------------------
# /jobs/claim
# ---------------------------------------------------------------------------


class TestClaimJobEndpoint:
    def test_claim_returns_null_when_no_jobs(self, client: TestClient, db_path: Path) -> None:
        queue = JobQueue(db_path)
        try:
            cursor = queue._get_conn().execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, 'idle')",
                ("notebook", "c1"),
            )
            worker_id = cursor.lastrowid
        finally:
            queue.close()

        response = client.post(
            "/api/worker/jobs/claim",
            json={"worker_id": worker_id, "job_type": "notebook"},
        )

        assert response.status_code == 200
        assert response.json()["job"] is None

    def test_claim_returns_job_data(self, client: TestClient, db_path: Path) -> None:
        job_id = _seed_pending_job(db_path)
        queue = JobQueue(db_path)
        try:
            cursor = queue._get_conn().execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, 'idle')",
                ("notebook", "c1"),
            )
            worker_id = cursor.lastrowid
        finally:
            queue.close()

        response = client.post(
            "/api/worker/jobs/claim",
            json={"worker_id": worker_id, "job_type": "notebook"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job"] is not None
        assert data["job"]["id"] == job_id

    def test_claim_returns_500_on_error(self, client: TestClient) -> None:
        with patch(
            "clm.infrastructure.database.job_queue.JobQueue.get_next_job",
            side_effect=RuntimeError("boom"),
        ):
            response = client.post(
                "/api/worker/jobs/claim",
                json={"worker_id": 1, "job_type": "notebook"},
            )

        assert response.status_code == 500
        assert "boom" in response.json()["detail"]


# ---------------------------------------------------------------------------
# /jobs/{id}/status
# ---------------------------------------------------------------------------


class TestUpdateStatusEndpoint:
    def test_completed_status_is_acknowledged(self, client: TestClient, db_path: Path) -> None:
        job_id = _seed_pending_job(db_path)

        # Put job into 'processing' so the completion transition is valid.
        queue = JobQueue(db_path)
        try:
            queue._get_conn().execute("UPDATE jobs SET status='processing' WHERE id=?", (job_id,))
        finally:
            queue.close()

        response = client.post(
            f"/api/worker/jobs/{job_id}/status",
            json={
                "worker_id": 1,
                "status": "completed",
                "result": {"warnings": 0},
            },
        )

        assert response.status_code == 200
        assert response.json()["acknowledged"] is True

    def test_failed_status_is_acknowledged(self, client: TestClient, db_path: Path) -> None:
        job_id = _seed_pending_job(db_path)
        queue = JobQueue(db_path)
        try:
            queue._get_conn().execute("UPDATE jobs SET status='processing' WHERE id=?", (job_id,))
        finally:
            queue.close()

        response = client.post(
            f"/api/worker/jobs/{job_id}/status",
            json={
                "worker_id": 1,
                "status": "failed",
                "error": {"type": "ExecError", "message": "oops"},
            },
        )

        assert response.status_code == 200

    def test_invalid_status_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/worker/jobs/1/status",
            json={"worker_id": 1, "status": "bogus"},
        )

        assert response.status_code == 400
        assert "Invalid status" in response.json()["detail"]

    def test_update_returns_500_on_db_error(self, client: TestClient) -> None:
        # Valid status, but the underlying job_queue operation blows up.
        with patch(
            "clm.infrastructure.database.job_queue.JobQueue.update_job_status",
            side_effect=RuntimeError("boom"),
        ):
            response = client.post(
                "/api/worker/jobs/1/status",
                json={"worker_id": 1, "status": "completed", "result": {}},
            )

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# /heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeatEndpoint:
    def test_heartbeat_acknowledges(self, client: TestClient, db_path: Path) -> None:
        queue = JobQueue(db_path)
        try:
            cursor = queue._get_conn().execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, 'idle')",
                ("notebook", "c1"),
            )
            worker_id = cursor.lastrowid
        finally:
            queue.close()

        response = client.post(
            "/api/worker/heartbeat",
            json={"worker_id": worker_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["acknowledged"] is True
        assert "timestamp" in data

    def test_heartbeat_returns_500_on_error(self, client: TestClient) -> None:
        with patch(
            "clm.infrastructure.database.job_queue.JobQueue._get_conn",
            side_effect=RuntimeError("db gone"),
        ):
            response = client.post(
                "/api/worker/heartbeat",
                json={"worker_id": 1},
            )

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# /jobs/{id}/cancelled
# ---------------------------------------------------------------------------


class TestCancelledEndpoint:
    def test_not_cancelled_pending_job(self, client: TestClient, db_path: Path) -> None:
        job_id = _seed_pending_job(db_path)

        response = client.get(f"/api/worker/jobs/{job_id}/cancelled")

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled"] is False

    def test_cancelled_job(self, client: TestClient, db_path: Path) -> None:
        job_id = _seed_pending_job(db_path)
        queue = JobQueue(db_path)
        try:
            queue._get_conn().execute(
                """
                UPDATE jobs
                SET status='cancelled',
                    cancelled_at=CURRENT_TIMESTAMP,
                    cancelled_by='test'
                WHERE id=?
                """,
                (job_id,),
            )
        finally:
            queue.close()

        response = client.get(f"/api/worker/jobs/{job_id}/cancelled")

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled"] is True
        assert data["cancelled_by"] == "test"
        assert data["cancelled_at"] is not None

    def test_not_found_returns_404(self, client: TestClient) -> None:
        response = client.get("/api/worker/jobs/9999/cancelled")

        assert response.status_code == 404

    def test_returns_500_on_error(self, client: TestClient) -> None:
        with patch(
            "clm.infrastructure.database.job_queue.JobQueue.get_job",
            side_effect=RuntimeError("boom"),
        ):
            response = client.get("/api/worker/jobs/1/cancelled")

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# /unregister
# ---------------------------------------------------------------------------


class TestUnregisterEndpoint:
    def test_unregister_marks_worker_dead(self, client: TestClient, db_path: Path) -> None:
        queue = JobQueue(db_path)
        try:
            cursor = queue._get_conn().execute(
                "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, 'idle')",
                ("notebook", "c1"),
            )
            worker_id = cursor.lastrowid
        finally:
            queue.close()

        response = client.post(
            "/api/worker/unregister",
            json={"worker_id": worker_id, "reason": "test"},
        )

        assert response.status_code == 200
        assert response.json()["acknowledged"] is True

        queue = JobQueue(db_path)
        try:
            row = (
                queue._get_conn()
                .execute("SELECT status FROM workers WHERE id=?", (worker_id,))
                .fetchone()
            )
        finally:
            queue.close()
        assert row[0] == "dead"

    def test_unregister_returns_500_on_error(self, client: TestClient) -> None:
        with patch(
            "clm.infrastructure.database.job_queue.JobQueue._get_conn",
            side_effect=RuntimeError("boom"),
        ):
            response = client.post(
                "/api/worker/unregister",
                json={"worker_id": 1, "reason": "test"},
            )

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# /activate (error paths not yet covered)
# ---------------------------------------------------------------------------


class TestActivateErrorPath:
    def test_activate_returns_500_on_unexpected_error(self, client: TestClient) -> None:
        with patch(
            "clm.infrastructure.database.job_queue.JobQueue._get_conn",
            side_effect=RuntimeError("boom"),
        ):
            response = client.post(
                "/api/worker/activate",
                json={"worker_id": 1},
            )

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# /cache/add
# ---------------------------------------------------------------------------


class TestCacheAddEndpoint:
    def test_cache_add_acknowledged(self, client: TestClient) -> None:
        response = client.post(
            "/api/worker/cache/add",
            json={
                "output_file": "out.html",
                "content_hash": "abc",
                "result_metadata": {"size": 100},
            },
        )

        assert response.status_code == 200
        assert response.json()["acknowledged"] is True

    def test_cache_add_returns_500_on_error(self, client: TestClient) -> None:
        with patch(
            "clm.infrastructure.database.job_queue.JobQueue.add_to_cache",
            side_effect=RuntimeError("boom"),
        ):
            response = client.post(
                "/api/worker/cache/add",
                json={
                    "output_file": "out.html",
                    "content_hash": "abc",
                    "result_metadata": {},
                },
            )

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# /health (server-level, defined on the FastAPI app itself)
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_status_ok(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["api_version"] == "1.0"
