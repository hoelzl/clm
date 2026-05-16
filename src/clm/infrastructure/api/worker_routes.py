"""REST API routes for Docker worker communication.

These routes allow Docker containers to communicate with the CLM job queue
without requiring direct SQLite access, solving the WAL mode issues on Windows.
"""

import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from clm.infrastructure.api.models import (
    CacheAddRequest,
    CacheAddResponse,
    ExecutedNotebookStoreResponse,
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
from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
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


def _get_cache_db_path(request: Request) -> Path:
    """Resolve the executed_notebooks cache DB path from app state.

    The executed_notebooks table lives in ``clm_cache.db``, which the host
    server publishes on ``app.state.cache_db_path``. For backwards
    compatibility with older test setups we fall back to the job DB path
    (the table is created on demand by ``ExecutedNotebookCache``).
    """
    cache_db = getattr(request.app.state, "cache_db_path", None)
    if cache_db is None:
        cache_db = request.app.state.db_path
    return Path(cache_db)


@router.get("/cache/executed_notebook")
async def get_executed_notebook(
    request: Request,
    input_file: str,
    content_hash: str,
    language: str,
    prog_lang: str,
):
    """Fetch a pickled executed NotebookNode by cache key.

    Returns the gzipped pickle bytes (octet-stream + Content-Encoding: gzip)
    on a hit, or 404 on a miss. The payload is shipped without unpickling
    server-side: pickled NotebookNodes can be multi-MB with image outputs,
    so we round-trip the bytes directly.
    """
    cache_db_path = _get_cache_db_path(request)
    try:
        with ExecutedNotebookCache(cache_db_path) as cache:
            pickle_bytes = cache.get_raw(
                input_file=input_file,
                content_hash=content_hash,
                language=language,
                prog_lang=prog_lang,
            )
    except Exception as e:
        logger.error(f"Failed to read executed_notebook cache: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to read executed_notebook cache: {e}"
        ) from e

    if pickle_bytes is None:
        raise HTTPException(status_code=404, detail="Executed notebook not cached")

    gz = gzip.compress(pickle_bytes)
    logger.debug(
        f"REST API: executed_notebook cache hit for {input_file} "
        f"({language}, {prog_lang}); shipping {len(gz)} bytes"
    )
    return Response(
        content=gz,
        media_type="application/octet-stream",
        headers={"Content-Encoding": "gzip"},
    )


@router.post("/cache/executed_notebook", response_model=ExecutedNotebookStoreResponse)
async def store_executed_notebook(
    request: Request,
    input_file: str,
    content_hash: str,
    language: str,
    prog_lang: str,
):
    """Store a pickled executed NotebookNode under the given cache key.

    The request body MUST be gzip-compressed pickle bytes — the inverse of
    what :func:`get_executed_notebook` returns. The server stores the
    decompressed pickle bytes verbatim so subsequent :meth:`ExecutedNotebookCache.get`
    calls (from direct-mode workers or the Stage 4 reuse path) round-trip
    cleanly.
    """
    body = await request.body()
    try:
        pickle_bytes = gzip.decompress(body)
    except gzip.BadGzipFile as e:
        raise HTTPException(status_code=400, detail=f"Body is not valid gzip: {e}") from e

    cache_db_path = _get_cache_db_path(request)
    try:
        with ExecutedNotebookCache(cache_db_path) as cache:
            cache.store_raw(
                input_file=input_file,
                content_hash=content_hash,
                language=language,
                prog_lang=prog_lang,
                pickle_bytes=pickle_bytes,
            )
    except Exception as e:
        logger.error(f"Failed to store executed_notebook cache: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to store executed_notebook cache: {e}"
        ) from e

    logger.debug(
        f"REST API: executed_notebook cache store for {input_file} "
        f"({language}, {prog_lang}); {len(pickle_bytes)} bytes (pickle)"
    )
    return ExecutedNotebookStoreResponse(acknowledged=True, bytes_stored=len(pickle_bytes))


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
