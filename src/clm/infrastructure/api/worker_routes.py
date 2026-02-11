"""REST API routes for Docker worker communication.

These routes allow Docker containers to communicate with the CLX job queue
without requiring direct SQLite access, solving the WAL mode issues on Windows.
"""

import json
import logging
from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, HTTPException, Request

from clm.infrastructure.api.models import (
    CacheAddRequest,
    CacheAddResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    JobCancellationResponse,
    JobClaimRequest,
    JobClaimResponse,
    JobData,
    JobStatusUpdateRequest,
    JobStatusUpdateResponse,
    WorkerActivationRequest,
    WorkerActivationResponse,
    WorkerRegistrationRequest,
    WorkerRegistrationResponse,
    WorkerUnregisterRequest,
    WorkerUnregisterResponse,
)
from clm.infrastructure.database.job_queue import JobQueue

logger = logging.getLogger(__name__)

# Create router with /api/worker prefix
router = APIRouter(prefix="/api/worker", tags=["worker"])


def get_job_queue(request: Request) -> JobQueue:
    """Get JobQueue from app state."""
    return cast(JobQueue, request.app.state.job_queue)


@router.post("/register", response_model=WorkerRegistrationResponse)
async def register_worker(request: Request, body: WorkerRegistrationRequest):
    """Register a new worker.

    This endpoint is called by Docker workers on startup to register
    themselves with the job queue.
    """
    job_queue = get_job_queue(request)

    try:
        conn = job_queue._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, parent_pid)
            VALUES (?, ?, 'idle', ?)
            """,
            (body.worker_type, body.container_id, body.parent_pid),
        )
        worker_id = cursor.lastrowid
        assert worker_id is not None, "INSERT should always return a valid lastrowid"

        registered_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"REST API: Registered {body.worker_type} worker {worker_id} "
            f"(container: {body.container_id})"
        )

        return WorkerRegistrationResponse(
            worker_id=worker_id,
            registered_at=registered_at,
        )

    except Exception as e:
        logger.error(f"Failed to register worker: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to register worker: {e}") from e


@router.post("/jobs/claim", response_model=JobClaimResponse)
async def claim_job(request: Request, body: JobClaimRequest):
    """Claim the next available job.

    This endpoint atomically retrieves and marks a job as processing.
    Returns null job if no jobs are available.
    """
    job_queue = get_job_queue(request)

    try:
        job = job_queue.get_next_job(body.job_type, body.worker_id)

        if job is None:
            return JobClaimResponse(job=None)

        job_data = JobData(
            id=job.id,
            job_type=job.job_type,
            input_file=job.input_file,
            output_file=job.output_file,
            content_hash=job.content_hash,
            payload=job.payload,
            correlation_id=job.correlation_id,
        )

        logger.debug(f"REST API: Worker {body.worker_id} claimed job {job.id} [{job.job_type}]")

        return JobClaimResponse(job=job_data)

    except Exception as e:
        logger.error(f"Failed to claim job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to claim job: {e}") from e


@router.post("/jobs/{job_id}/status", response_model=JobStatusUpdateResponse)
async def update_job_status(request: Request, job_id: int, body: JobStatusUpdateRequest):
    """Update job status (completed or failed).

    This endpoint is called by workers when they finish processing a job.
    """
    job_queue = get_job_queue(request)

    if body.status not in ("completed", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {body.status}. Must be 'completed' or 'failed'",
        )

    try:
        # Convert result/error dicts to JSON strings
        result_json = json.dumps(body.result) if body.result else None
        error_json = json.dumps(body.error) if body.error else None

        job_queue.update_job_status(
            job_id=job_id,
            status=body.status,
            error=error_json,
            result=result_json,
        )

        logger.debug(f"REST API: Worker {body.worker_id} updated job {job_id} to {body.status}")

        return JobStatusUpdateResponse(acknowledged=True)

    except Exception as e:
        logger.error(f"Failed to update job status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update job status: {e}") from e


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(request: Request, body: HeartbeatRequest):
    """Update worker heartbeat.

    Workers should call this periodically to indicate they are still alive.
    """
    job_queue = get_job_queue(request)

    try:
        conn = job_queue._get_conn()
        conn.execute(
            """
            UPDATE workers
            SET last_heartbeat = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (body.worker_id,),
        )

        timestamp = datetime.now(timezone.utc).isoformat()

        logger.debug(f"REST API: Heartbeat from worker {body.worker_id}")

        return HeartbeatResponse(acknowledged=True, timestamp=timestamp)

    except Exception as e:
        logger.error(f"Failed to update heartbeat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update heartbeat: {e}") from e


@router.get("/jobs/{job_id}/cancelled", response_model=JobCancellationResponse)
async def check_job_cancelled(request: Request, job_id: int):
    """Check if a job has been cancelled.

    Workers can call this during long-running jobs to check if they
    should abort processing.
    """
    job_queue = get_job_queue(request)

    try:
        job = job_queue.get_job(job_id)

        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        cancelled = job.status == "cancelled"

        return JobCancellationResponse(
            cancelled=cancelled,
            cancelled_at=job.cancelled_at.isoformat() if job.cancelled_at else None,
            cancelled_by=job.cancelled_by,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to check job cancellation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to check job cancellation: {e}") from e


@router.post("/unregister", response_model=WorkerUnregisterResponse)
async def unregister_worker(request: Request, body: WorkerUnregisterRequest):
    """Unregister a worker (graceful shutdown).

    This endpoint is called by workers when they are shutting down.
    """
    job_queue = get_job_queue(request)

    try:
        conn = job_queue._get_conn()
        conn.execute(
            """
            UPDATE workers
            SET status = 'dead'
            WHERE id = ?
            """,
            (body.worker_id,),
        )

        logger.info(f"REST API: Worker {body.worker_id} unregistered (reason: {body.reason})")

        return WorkerUnregisterResponse(acknowledged=True)

    except Exception as e:
        logger.error(f"Failed to unregister worker: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to unregister worker: {e}") from e


@router.post("/activate", response_model=WorkerActivationResponse)
async def activate_worker(request: Request, body: WorkerActivationRequest):
    """Activate a pre-registered worker.

    This endpoint is called by workers that were pre-registered by the parent
    process with status='created'. It updates the status to 'idle' to signal
    that the worker is ready to accept jobs.

    This is part of the worker pre-registration optimization that eliminates
    the startup wait time for workers to self-register.
    """
    job_queue = get_job_queue(request)

    try:
        conn = job_queue._get_conn()
        cursor = conn.execute(
            """
            UPDATE workers
            SET status = 'idle', last_heartbeat = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'created'
            """,
            (body.worker_id,),
        )

        if cursor.rowcount == 0:
            # Check if worker exists at all
            check_cursor = conn.execute(
                "SELECT status FROM workers WHERE id = ?", (body.worker_id,)
            )
            row = check_cursor.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Worker {body.worker_id} not found")
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Worker {body.worker_id} has status '{row[0]}', expected 'created'",
                )

        activated_at = datetime.now(timezone.utc).isoformat()

        logger.info(f"REST API: Worker {body.worker_id} activated (created -> idle)")

        return WorkerActivationResponse(acknowledged=True, activated_at=activated_at)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to activate worker: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to activate worker: {e}") from e


@router.post("/cache/add", response_model=CacheAddResponse)
async def add_to_cache(request: Request, body: CacheAddRequest):
    """Add result to cache.

    This endpoint allows Docker workers to cache their results in the host's
    results_cache database, ensuring caching works uniformly for both direct
    and Docker workers.
    """
    job_queue = get_job_queue(request)

    try:
        job_queue.add_to_cache(
            output_file=body.output_file,
            content_hash=body.content_hash,
            result_metadata=body.result_metadata,
        )

        logger.debug(f"REST API: Added cache entry for {body.output_file}")

        return CacheAddResponse(acknowledged=True)

    except Exception as e:
        logger.error(f"Failed to add to cache: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to add to cache: {e}") from e
