"""HTTP client for Worker REST API communication.

This module provides a client that Docker workers use to communicate
with the CLM job queue via REST API instead of direct SQLite access.
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class JobInfo:
    """Job information returned from the API."""

    id: int
    job_type: str
    input_file: str
    output_file: str
    content_hash: str
    payload: dict[str, Any]
    correlation_id: str | None = None


class WorkerApiError(Exception):
    """Error communicating with the Worker API."""

    pass


class WorkerApiClient:
    """HTTP client for Docker workers to communicate with CLM host.

    This client provides the same operations as direct SQLite access
    but communicates via REST API, solving the WAL mode issues on Windows.

    Usage:
        client = WorkerApiClient("http://host.docker.internal:8765")
        worker_id = client.register("notebook", container_id)

        while True:
            job = client.claim_job(worker_id, "notebook")
            if job:
                # Process job...
                client.complete_job(job.id, worker_id, result)
            else:
                time.sleep(0.1)
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        max_retries: int = 5,
        initial_retry_delay: float = 0.5,
    ):
        """Initialize the API client.

        Args:
            base_url: Base URL of the Worker API (e.g., http://host.docker.internal:8765)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests
            initial_retry_delay: Initial delay between retries (doubles each attempt)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay

        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _request_with_retry(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        retry_on_connect: bool = True,
    ) -> httpx.Response:
        """Make a request with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path
            json_data: JSON body data
            retry_on_connect: Whether to retry on connection errors

        Returns:
            Response object

        Raises:
            WorkerApiError: If request fails after all retries
        """
        retry_delay = self.initial_retry_delay

        for attempt in range(self.max_retries):
            try:
                response = self._client.request(method, path, json=json_data)
                response.raise_for_status()
                return response

            except httpx.ConnectError as e:
                if not retry_on_connect or attempt >= self.max_retries - 1:
                    raise WorkerApiError(
                        f"Failed to connect to Worker API at {self.base_url}: {e}"
                    ) from e
                logger.warning(
                    f"Connection failed (attempt {attempt + 1}/{self.max_retries}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
                retry_delay *= 2

            except httpx.HTTPStatusError as e:
                # Don't retry client errors (4xx)
                if 400 <= e.response.status_code < 500:
                    raise WorkerApiError(
                        f"API error: {e.response.status_code} - {e.response.text}"
                    ) from e
                # Retry server errors (5xx)
                if attempt >= self.max_retries - 1:
                    raise WorkerApiError(f"API error after {self.max_retries} retries: {e}") from e
                logger.warning(
                    f"Server error (attempt {attempt + 1}/{self.max_retries}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
                retry_delay *= 2

            except httpx.TimeoutException as e:
                if attempt >= self.max_retries - 1:
                    raise WorkerApiError(
                        f"Request timeout after {self.max_retries} retries: {e}"
                    ) from e
                logger.warning(
                    f"Timeout (attempt {attempt + 1}/{self.max_retries}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
                retry_delay *= 2

        # Should not reach here
        raise WorkerApiError("Unexpected retry loop exit")

    def register(
        self,
        worker_type: str,
        container_id: str | None = None,
        parent_pid: int | None = None,
    ) -> int:
        """Register worker and return worker_id.

        Args:
            worker_type: Type of worker (notebook, plantuml, drawio)
            container_id: Docker container ID (defaults to HOSTNAME env var)
            parent_pid: Parent process ID for orphan detection

        Returns:
            Worker ID from database
        """
        if container_id is None:
            container_id = os.getenv("HOSTNAME", "unknown")

        response = self._request_with_retry(
            "POST",
            "/api/worker/register",
            json_data={
                "worker_type": worker_type,
                "container_id": container_id,
                "parent_pid": parent_pid,
            },
        )

        data = response.json()
        worker_id: int = data["worker_id"]

        logger.info(f"Registered as {worker_type} worker {worker_id} via REST API")
        return worker_id

    def claim_job(self, worker_id: int, job_type: str) -> JobInfo | None:
        """Claim next available job.

        Args:
            worker_id: ID of the worker claiming the job
            job_type: Type of job to claim

        Returns:
            JobInfo if a job was claimed, None if no jobs available
        """
        response = self._request_with_retry(
            "POST",
            "/api/worker/jobs/claim",
            json_data={
                "worker_id": worker_id,
                "job_type": job_type,
            },
        )

        data = response.json()
        job_data = data.get("job")

        if job_data is None:
            return None

        return JobInfo(
            id=job_data["id"],
            job_type=job_data["job_type"],
            input_file=job_data["input_file"],
            output_file=job_data["output_file"],
            content_hash=job_data["content_hash"],
            payload=job_data["payload"],
            correlation_id=job_data.get("correlation_id"),
        )

    def complete_job(
        self,
        job_id: int,
        worker_id: int,
        result: dict[str, Any] | None = None,
    ):
        """Mark job as completed.

        Args:
            job_id: ID of the job
            worker_id: ID of the worker
            result: Optional result data (warnings, metadata, etc.)
        """
        self._request_with_retry(
            "POST",
            f"/api/worker/jobs/{job_id}/status",
            json_data={
                "worker_id": worker_id,
                "status": "completed",
                "result": result,
            },
        )

        logger.debug(f"Job {job_id} marked as completed via REST API")

    def fail_job(
        self,
        job_id: int,
        worker_id: int,
        error: dict[str, Any],
    ):
        """Mark job as failed.

        Args:
            job_id: ID of the job
            worker_id: ID of the worker
            error: Error information dictionary
        """
        self._request_with_retry(
            "POST",
            f"/api/worker/jobs/{job_id}/status",
            json_data={
                "worker_id": worker_id,
                "status": "failed",
                "error": error,
            },
        )

        logger.debug(f"Job {job_id} marked as failed via REST API")

    def heartbeat(self, worker_id: int):
        """Send heartbeat to indicate worker is alive.

        Args:
            worker_id: ID of the worker
        """
        self._request_with_retry(
            "POST",
            "/api/worker/heartbeat",
            json_data={"worker_id": worker_id},
        )

    def is_job_cancelled(self, job_id: int) -> bool:
        """Check if a job has been cancelled.

        Args:
            job_id: ID of the job

        Returns:
            True if the job was cancelled
        """
        response = self._request_with_retry(
            "GET",
            f"/api/worker/jobs/{job_id}/cancelled",
        )

        data = response.json()
        is_cancelled: bool = data.get("cancelled", False)
        return is_cancelled

    def unregister(self, worker_id: int, reason: str = "graceful_shutdown"):
        """Unregister worker on shutdown.

        Args:
            worker_id: ID of the worker
            reason: Reason for unregistration
        """
        try:
            self._request_with_retry(
                "POST",
                "/api/worker/unregister",
                json_data={
                    "worker_id": worker_id,
                    "reason": reason,
                },
                retry_on_connect=False,  # Don't retry if server is gone
            )
            logger.info(f"Worker {worker_id} unregistered via REST API")
        except WorkerApiError:
            # Ignore errors during unregistration (server may be gone)
            logger.debug(f"Could not unregister worker {worker_id} (server may be stopped)")

    def activate(self, worker_id: int):
        """Activate a pre-registered worker by updating its status from 'created' to 'idle'.

        This is used when workers are pre-registered by the parent process.
        The parent creates the worker row with status='created', and the
        container calls this method to signal it's ready to accept jobs.

        Args:
            worker_id: Pre-assigned worker ID from CLM_WORKER_ID environment variable

        Raises:
            WorkerApiError: If activation fails
        """
        self._request_with_retry(
            "POST",
            "/api/worker/activate",
            json_data={"worker_id": worker_id},
        )
        logger.info(f"Worker {worker_id} activated via REST API (created -> idle)")

    def add_to_cache(
        self,
        output_file: str,
        content_hash: str,
        result_metadata: dict[str, Any],
    ):
        """Add result to cache.

        Args:
            output_file: Output file path
            content_hash: Content hash of the source file
            result_metadata: Metadata about the result
        """
        try:
            self._request_with_retry(
                "POST",
                "/api/worker/cache/add",
                json_data={
                    "output_file": output_file,
                    "content_hash": content_hash,
                    "result_metadata": result_metadata,
                },
            )
            logger.debug(f"Added cache entry for {output_file} via REST API")
        except WorkerApiError as e:
            # Log but don't fail - caching is not critical
            logger.warning(f"Failed to add cache entry for {output_file}: {e}")
