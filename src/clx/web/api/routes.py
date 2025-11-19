"""API route handlers."""

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from clx.web.models import (
    HealthResponse,
    JobsListResponse,
    StatusResponse,
    VersionResponse,
    WorkersListResponse,
)
from clx.web.services.monitor_service import MonitorService

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api")


def get_monitor_service(request: Request) -> MonitorService:
    """Get monitor service from app state."""
    return request.app.state.monitor_service


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request):
    """Health check endpoint.

    Returns server health and basic info.
    """
    from clx import __version__

    monitor_service = get_monitor_service(request)

    return HealthResponse(
        status="ok",
        version=__version__,
        database_path=str(monitor_service.db_path),
    )


@router.get("/version", response_model=VersionResponse)
async def get_version():
    """Get version information."""
    from clx import __version__

    return VersionResponse(
        clx_version=__version__,
        api_version="1.0",
    )


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request):
    """Get overall system status.

    Returns complete system status including workers, queue, and health.
    """
    monitor_service = get_monitor_service(request)

    try:
        status = monitor_service.get_status()
        return status
    except Exception as e:
        logger.error(f"Error getting status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting status: {e}") from e


@router.get("/workers", response_model=WorkersListResponse)
async def get_workers(request: Request):
    """Get list of all workers.

    Returns detailed information about all registered workers.
    """
    monitor_service = get_monitor_service(request)

    try:
        workers = monitor_service.get_workers()
        return workers
    except Exception as e:
        logger.error(f"Error getting workers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting workers: {e}") from e


@router.get("/jobs", response_model=JobsListResponse)
async def get_jobs(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Jobs per page"),
):
    """Get list of jobs with pagination.

    Args:
        status: Filter by job status (pending, processing, completed, failed)
        page: Page number (1-indexed)
        page_size: Number of jobs per page

    Returns:
        Paginated list of jobs
    """
    monitor_service = get_monitor_service(request)

    try:
        offset = (page - 1) * page_size
        jobs = monitor_service.get_jobs(
            status=status,
            limit=page_size,
            offset=offset,
        )

        # Get total count for pagination
        # For now, just return what we have (can optimize with separate count query)
        total = len(jobs) + offset if len(jobs) == page_size else offset + len(jobs)

        return JobsListResponse(
            jobs=jobs,
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as e:
        logger.error(f"Error getting jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting jobs: {e}") from e
