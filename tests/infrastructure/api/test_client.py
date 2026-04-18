"""Tests for ``clm.infrastructure.api.client``.

Covers ``WorkerApiClient``'s HTTP operations and retry loop:
registration, job lifecycle (claim/complete/fail/cancel), heartbeat,
activation, unregistration, cache, plus the retry/error paths for
``ConnectError``, 4xx/5xx ``HTTPStatusError``, and timeouts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from clm.infrastructure.api.client import JobInfo, WorkerApiClient, WorkerApiError


def _make_response(
    status_code: int = 200,
    json_payload: dict | None = None,
    text: str = "",
) -> MagicMock:
    """Build a mock httpx.Response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    response.json.return_value = json_payload or {}
    response.raise_for_status = MagicMock()
    return response


@pytest.fixture
def patched_request():
    """Patch ``httpx.Client.request`` and hand back the mock."""
    with patch("httpx.Client.request") as mock_request:
        yield mock_request


@pytest.fixture
def client() -> WorkerApiClient:
    """A client with a tiny retry delay so failure paths finish fast."""
    return WorkerApiClient(
        "http://localhost:8765",
        timeout=1.0,
        max_retries=3,
        initial_retry_delay=0.001,
    )


class TestConstruction:
    """WorkerApiClient basic construction and context manager."""

    def test_strips_trailing_slash_from_base_url(self) -> None:
        c = WorkerApiClient("http://localhost:8765/")
        assert c.base_url == "http://localhost:8765"
        c.close()

    def test_stores_timeout_and_retry_settings(self) -> None:
        c = WorkerApiClient(
            "http://host:1",
            timeout=7.5,
            max_retries=10,
            initial_retry_delay=2.0,
        )
        assert c.timeout == 7.5
        assert c.max_retries == 10
        assert c.initial_retry_delay == 2.0
        c.close()

    def test_context_manager_closes_client(self) -> None:
        with WorkerApiClient("http://localhost:8765") as c:
            assert c._client is not None
        # httpx.Client.close() has been called; a second close is a no-op.
        c.close()


class TestRegister:
    """WorkerApiClient.register()."""

    def test_returns_worker_id_from_response(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={"worker_id": 42})

        worker_id = client.register("notebook", container_id="container-abc", parent_pid=123)

        assert worker_id == 42
        call = patched_request.call_args
        assert call[0] == ("POST", "/api/worker/register")
        body = call[1]["json"]
        assert body == {
            "worker_type": "notebook",
            "container_id": "container-abc",
            "parent_pid": 123,
        }

    def test_defaults_container_id_to_hostname_env(
        self,
        client: WorkerApiClient,
        patched_request: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HOSTNAME", "my-host")
        patched_request.return_value = _make_response(json_payload={"worker_id": 7})

        client.register("plantuml")

        body = patched_request.call_args[1]["json"]
        assert body["container_id"] == "my-host"
        assert body["parent_pid"] is None

    def test_defaults_container_id_to_unknown_when_no_env(
        self,
        client: WorkerApiClient,
        patched_request: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("HOSTNAME", raising=False)
        patched_request.return_value = _make_response(json_payload={"worker_id": 1})

        client.register("drawio")

        body = patched_request.call_args[1]["json"]
        assert body["container_id"] == "unknown"


class TestClaimJob:
    """WorkerApiClient.claim_job()."""

    def test_returns_none_when_no_jobs_available(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={"job": None})

        result = client.claim_job(worker_id=1, job_type="notebook")

        assert result is None

    def test_returns_job_info_when_job_claimed(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(
            json_payload={
                "job": {
                    "id": 99,
                    "job_type": "notebook",
                    "input_file": "in.py",
                    "output_file": "out.html",
                    "content_hash": "abc",
                    "payload": {"x": 1},
                    "correlation_id": "corr-1",
                }
            }
        )

        job = client.claim_job(worker_id=2, job_type="notebook")

        assert isinstance(job, JobInfo)
        assert job.id == 99
        assert job.job_type == "notebook"
        assert job.payload == {"x": 1}
        assert job.correlation_id == "corr-1"

    def test_missing_correlation_id_is_none(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(
            json_payload={
                "job": {
                    "id": 1,
                    "job_type": "plantuml",
                    "input_file": "a",
                    "output_file": "b",
                    "content_hash": "c",
                    "payload": {},
                }
            }
        )

        job = client.claim_job(worker_id=1, job_type="plantuml")

        assert job is not None
        assert job.correlation_id is None


class TestJobStatusUpdates:
    """WorkerApiClient.complete_job() / fail_job()."""

    def test_complete_job_posts_completed_status(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={"acknowledged": True})

        client.complete_job(job_id=5, worker_id=1, result={"warnings": 0})

        call = patched_request.call_args
        assert call[0] == ("POST", "/api/worker/jobs/5/status")
        body = call[1]["json"]
        assert body == {"worker_id": 1, "status": "completed", "result": {"warnings": 0}}

    def test_complete_job_with_none_result(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={"acknowledged": True})

        client.complete_job(job_id=5, worker_id=1, result=None)

        body = patched_request.call_args[1]["json"]
        assert body["result"] is None

    def test_fail_job_posts_failed_status(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={"acknowledged": True})

        client.fail_job(job_id=9, worker_id=2, error={"type": "ExecutionError"})

        call = patched_request.call_args
        assert call[0] == ("POST", "/api/worker/jobs/9/status")
        body = call[1]["json"]
        assert body == {
            "worker_id": 2,
            "status": "failed",
            "error": {"type": "ExecutionError"},
        }


class TestHeartbeatAndCancellation:
    def test_heartbeat_sends_worker_id(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(
            json_payload={"acknowledged": True, "timestamp": "2024-01-01T00:00:00Z"}
        )

        client.heartbeat(worker_id=3)

        call = patched_request.call_args
        assert call[0] == ("POST", "/api/worker/heartbeat")
        assert call[1]["json"] == {"worker_id": 3}

    def test_is_job_cancelled_true(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={"cancelled": True})

        assert client.is_job_cancelled(42) is True
        call = patched_request.call_args
        assert call[0] == ("GET", "/api/worker/jobs/42/cancelled")

    def test_is_job_cancelled_false_when_missing_key(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={})

        assert client.is_job_cancelled(42) is False


class TestActivateAndUnregister:
    def test_activate_posts_worker_id(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(
            json_payload={"acknowledged": True, "activated_at": "2024-01-01T00:00:00Z"}
        )

        client.activate(55)

        call = patched_request.call_args
        assert call[0] == ("POST", "/api/worker/activate")
        assert call[1]["json"] == {"worker_id": 55}

    def test_unregister_posts_reason(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={"acknowledged": True})

        client.unregister(worker_id=11, reason="shutdown")

        call = patched_request.call_args
        assert call[0] == ("POST", "/api/worker/unregister")
        assert call[1]["json"] == {"worker_id": 11, "reason": "shutdown"}

    def test_unregister_swallows_api_error(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        """Unregistration must not raise even if the server is gone."""
        # ConnectError is wrapped in WorkerApiError by _request_with_retry;
        # but unregister uses retry_on_connect=False, so connect errors raise
        # immediately — and unregister must catch them.
        patched_request.side_effect = httpx.ConnectError("server gone")

        # Should not raise
        client.unregister(worker_id=11)


class TestAddToCache:
    def test_add_to_cache_posts_metadata(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.return_value = _make_response(json_payload={"acknowledged": True})

        client.add_to_cache(
            output_file="out.html",
            content_hash="deadbeef",
            result_metadata={"size": 100},
        )

        call = patched_request.call_args
        assert call[0] == ("POST", "/api/worker/cache/add")
        body = call[1]["json"]
        assert body == {
            "output_file": "out.html",
            "content_hash": "deadbeef",
            "result_metadata": {"size": 100},
        }

    def test_add_to_cache_swallows_api_error(
        self,
        client: WorkerApiClient,
        patched_request: MagicMock,
    ) -> None:
        """Cache errors should be logged but not propagate."""
        response = _make_response(status_code=500, text="boom")
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=response
        )
        patched_request.return_value = response

        # Should not raise
        client.add_to_cache("f", "h", {"k": "v"})


class TestRetryLoop:
    """_request_with_retry retry semantics."""

    def test_retries_on_connect_error_then_succeeds(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        success = _make_response(json_payload={"worker_id": 1})
        patched_request.side_effect = [
            httpx.ConnectError("no route"),
            success,
        ]

        worker_id = client.register("notebook", container_id="c")

        assert worker_id == 1
        assert patched_request.call_count == 2

    def test_raises_after_max_retries_on_connect_error(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.side_effect = httpx.ConnectError("refused")

        with pytest.raises(WorkerApiError, match="Failed to connect"):
            client.register("notebook", container_id="c")

        assert patched_request.call_count == client.max_retries

    def test_retries_on_5xx_then_succeeds(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        fail = _make_response(status_code=502, text="bad gateway")
        fail.raise_for_status.side_effect = httpx.HTTPStatusError(
            "502", request=MagicMock(), response=fail
        )
        success = _make_response(json_payload={"worker_id": 7})

        patched_request.side_effect = [fail, success]

        worker_id = client.register("notebook", container_id="c")

        assert worker_id == 7
        assert patched_request.call_count == 2

    def test_does_not_retry_on_4xx(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        fail = _make_response(status_code=400, text="bad request")
        fail.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=fail
        )
        patched_request.return_value = fail

        with pytest.raises(WorkerApiError, match="API error: 400"):
            client.register("notebook", container_id="c")

        # Exactly one attempt — no retry for client error.
        assert patched_request.call_count == 1

    def test_retries_on_timeout_then_raises(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        patched_request.side_effect = httpx.TimeoutException("timeout")

        with pytest.raises(WorkerApiError, match="Request timeout"):
            client.register("notebook", container_id="c")

        assert patched_request.call_count == client.max_retries

    def test_5xx_exhausts_retries(
        self, client: WorkerApiClient, patched_request: MagicMock
    ) -> None:
        fail = _make_response(status_code=500, text="boom")
        fail.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=fail
        )
        patched_request.return_value = fail

        with pytest.raises(WorkerApiError, match="API error after"):
            client.register("notebook", container_id="c")

        assert patched_request.call_count == client.max_retries
