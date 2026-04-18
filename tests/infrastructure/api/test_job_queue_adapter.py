"""Tests for ``clm.infrastructure.api.job_queue_adapter``.

``ApiJobQueue`` wraps ``WorkerApiClient`` with the JobQueue-like interface
used by workers. These tests substitute a fake client so we exercise the
adapter logic without real HTTP traffic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from clm.infrastructure.api.client import JobInfo, WorkerApiError
from clm.infrastructure.api.job_queue_adapter import ApiJobQueue


@pytest.fixture
def fake_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def adapter(fake_client: MagicMock) -> ApiJobQueue:
    """Adapter wired to a fake client, with a pre-set worker_id."""
    q = ApiJobQueue("http://host:1", worker_id=42)
    q._client = fake_client
    return q


class TestConstructionAndClose:
    def test_stores_api_url_and_worker_id(self) -> None:
        q = ApiJobQueue("http://localhost:8765/", worker_id=7)
        assert q.api_url == "http://localhost:8765/"
        assert q.worker_id == 7
        q.close()

    def test_close_delegates_to_client(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        adapter.close()
        fake_client.close.assert_called_once()


class TestGetNextJob:
    def test_returns_none_when_no_jobs(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        fake_client.claim_job.return_value = None
        assert adapter.get_next_job("notebook") is None

    def test_returns_job_when_claimed(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        fake_client.claim_job.return_value = JobInfo(
            id=1,
            job_type="notebook",
            input_file="in.py",
            output_file="out.html",
            content_hash="abc",
            payload={"k": "v"},
            correlation_id="c-1",
        )

        job = adapter.get_next_job("notebook")

        assert job is not None
        assert job.id == 1
        assert job.job_type == "notebook"
        assert job.status == "processing"
        assert job.worker_id == 42
        assert job.correlation_id == "c-1"

    def test_explicit_worker_id_overrides_default(
        self, adapter: ApiJobQueue, fake_client: MagicMock
    ) -> None:
        fake_client.claim_job.return_value = None

        adapter.get_next_job("notebook", worker_id=99)

        fake_client.claim_job.assert_called_once_with(99, "notebook")

    def test_raises_if_no_worker_id(self, fake_client: MagicMock) -> None:
        q = ApiJobQueue("http://host", worker_id=None)
        q._client = fake_client

        with pytest.raises(ValueError, match="worker_id"):
            q.get_next_job("notebook")

    def test_propagates_worker_api_error(
        self, adapter: ApiJobQueue, fake_client: MagicMock
    ) -> None:
        fake_client.claim_job.side_effect = WorkerApiError("boom")

        with pytest.raises(WorkerApiError):
            adapter.get_next_job("notebook")


class TestUpdateJobStatus:
    def test_completed_parses_result_json(
        self, adapter: ApiJobQueue, fake_client: MagicMock
    ) -> None:
        adapter.update_job_status(
            job_id=5,
            status="completed",
            result='{"warnings": 1}',
        )

        fake_client.complete_job.assert_called_once_with(5, 42, {"warnings": 1})

    def test_completed_with_none_result(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        adapter.update_job_status(job_id=5, status="completed", result=None)

        fake_client.complete_job.assert_called_once_with(5, 42, None)

    def test_failed_parses_error_json(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        adapter.update_job_status(
            job_id=7,
            status="failed",
            error='{"type": "ExecError"}',
        )

        fake_client.fail_job.assert_called_once_with(7, 42, {"type": "ExecError"})

    def test_failed_with_none_error_uses_default(
        self, adapter: ApiJobQueue, fake_client: MagicMock
    ) -> None:
        adapter.update_job_status(job_id=7, status="failed", error=None)

        fake_client.fail_job.assert_called_once_with(7, 42, {"error_message": "Unknown error"})

    def test_invalid_status_raises(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="Invalid status"):
            adapter.update_job_status(job_id=1, status="bogus")

    def test_requires_worker_id(self, fake_client: MagicMock) -> None:
        q = ApiJobQueue("http://host", worker_id=None)
        q._client = fake_client

        with pytest.raises(ValueError, match="worker_id"):
            q.update_job_status(job_id=1, status="completed")

    def test_propagates_worker_api_error(
        self, adapter: ApiJobQueue, fake_client: MagicMock
    ) -> None:
        fake_client.complete_job.side_effect = WorkerApiError("boom")

        with pytest.raises(WorkerApiError):
            adapter.update_job_status(job_id=1, status="completed")


class TestIsJobCancelled:
    def test_returns_client_result(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        fake_client.is_job_cancelled.return_value = True
        assert adapter.is_job_cancelled(1) is True

    def test_returns_false_on_error(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        fake_client.is_job_cancelled.side_effect = WorkerApiError("boom")
        assert adapter.is_job_cancelled(1) is False


class TestUpdateHeartbeat:
    def test_uses_explicit_worker_id(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        adapter.update_heartbeat(worker_id=99)
        fake_client.heartbeat.assert_called_once_with(99)

    def test_falls_back_to_instance_worker_id(
        self, adapter: ApiJobQueue, fake_client: MagicMock
    ) -> None:
        adapter.update_heartbeat()
        fake_client.heartbeat.assert_called_once_with(42)

    def test_requires_worker_id(self, fake_client: MagicMock) -> None:
        q = ApiJobQueue("http://host", worker_id=None)
        q._client = fake_client

        with pytest.raises(ValueError, match="worker_id"):
            q.update_heartbeat()

    def test_swallows_api_error(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        """Heartbeat failures must not stop job processing."""
        fake_client.heartbeat.side_effect = WorkerApiError("boom")

        # Should not raise
        adapter.update_heartbeat()


class TestAddToCache:
    def test_forwards_to_client(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        adapter.add_to_cache("out.html", "abc", {"size": 10})
        fake_client.add_to_cache.assert_called_once_with("out.html", "abc", {"size": 10})

    def test_swallows_api_error(self, adapter: ApiJobQueue, fake_client: MagicMock) -> None:
        fake_client.add_to_cache.side_effect = WorkerApiError("boom")

        # Should not raise
        adapter.add_to_cache("f", "h", {})


class TestGetConn:
    """_get_conn must raise — the API adapter has no direct SQLite access."""

    def test_raises_not_implemented(self, adapter: ApiJobQueue) -> None:
        with pytest.raises(NotImplementedError, match="Direct database access"):
            adapter._get_conn()
