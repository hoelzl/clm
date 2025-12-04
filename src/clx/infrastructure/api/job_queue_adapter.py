"""Adapter that provides JobQueue-like interface using REST API client.

This adapter allows workers to use the same interface whether they are
communicating via direct SQLite or via REST API.
"""

import json
import logging
from datetime import datetime

from clx.infrastructure.api.client import WorkerApiClient, WorkerApiError
from clx.infrastructure.database.job_queue import Job

logger = logging.getLogger(__name__)


class ApiJobQueue:
    """Job queue adapter that uses REST API instead of direct SQLite.

    This class provides the same interface as JobQueue but communicates
    via REST API, solving the SQLite WAL mode issues on Windows Docker.

    Note: This is a subset of the full JobQueue interface, containing
    only the methods needed by workers.
    """

    def __init__(self, api_url: str, worker_id: int | None = None):
        """Initialize the API job queue adapter.

        Args:
            api_url: Base URL of the Worker API
            worker_id: Worker ID (set after registration)
        """
        self.api_url = api_url
        self.worker_id = worker_id
        self._client = WorkerApiClient(api_url)

    def close(self):
        """Close the API client."""
        self._client.close()

    def get_next_job(self, job_type: str, worker_id: int | None = None) -> Job | None:
        """Get next pending job for the given type.

        Args:
            job_type: Type of job to retrieve
            worker_id: Worker ID to assign the job to

        Returns:
            Job object if available, None otherwise
        """
        wid = worker_id or self.worker_id
        if wid is None:
            raise ValueError("worker_id must be set")

        try:
            job_info = self._client.claim_job(wid, job_type)

            if job_info is None:
                return None

            # Convert to Job dataclass
            return Job(
                id=job_info.id,
                job_type=job_info.job_type,
                status="processing",
                input_file=job_info.input_file,
                output_file=job_info.output_file,
                content_hash=job_info.content_hash,
                payload=job_info.payload,
                created_at=datetime.now(),  # Not available from API
                attempts=1,
                priority=0,
                worker_id=wid,
                correlation_id=job_info.correlation_id,
            )

        except WorkerApiError as e:
            logger.error(f"Failed to claim job: {e}")
            raise

    def update_job_status(
        self, job_id: int, status: str, error: str | None = None, result: str | None = None
    ):
        """Update job status.

        Args:
            job_id: Job ID
            status: New status ('completed' or 'failed')
            error: Optional error message (JSON string)
            result: Optional result data (JSON string)
        """
        if self.worker_id is None:
            raise ValueError("worker_id must be set")

        try:
            if status == "completed":
                # Parse result JSON if provided
                result_dict = json.loads(result) if result else None
                self._client.complete_job(job_id, self.worker_id, result_dict)
            elif status == "failed":
                # Parse error JSON if provided
                error_dict = json.loads(error) if error else {"error_message": "Unknown error"}
                self._client.fail_job(job_id, self.worker_id, error_dict)
            else:
                raise ValueError(f"Invalid status: {status}")

        except WorkerApiError as e:
            logger.error(f"Failed to update job status: {e}")
            raise

    def is_job_cancelled(self, job_id: int) -> bool:
        """Check if a job has been cancelled.

        Args:
            job_id: Job ID

        Returns:
            True if job was cancelled
        """
        try:
            return self._client.is_job_cancelled(job_id)
        except WorkerApiError as e:
            logger.error(f"Failed to check job cancellation: {e}")
            return False  # Assume not cancelled on error

    def update_heartbeat(self, worker_id: int | None = None):
        """Update worker heartbeat.

        Args:
            worker_id: Worker ID (uses self.worker_id if not provided)
        """
        wid = worker_id or self.worker_id
        if wid is None:
            raise ValueError("worker_id must be set")

        try:
            self._client.heartbeat(wid)
        except WorkerApiError as e:
            logger.warning(f"Failed to update heartbeat: {e}")
            # Don't raise - heartbeat failures shouldn't stop processing

    def _get_conn(self):
        """Compatibility method - returns self for direct attribute access.

        This is needed because some worker code does:
            conn = self.job_queue._get_conn()
            conn.execute(...)

        For API mode, we raise an error since direct database access
        is not supported.
        """
        raise NotImplementedError(
            "Direct database access not available in API mode. Use the provided methods instead."
        )
