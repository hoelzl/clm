"""Pydantic models for web API."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# Health & Metadata Models
class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str
    database_path: str


class VersionResponse(BaseModel):
    """Version information response."""

    clx_version: str
    api_version: str = "1.0"


# Worker Models
class BusyWorkerDetail(BaseModel):
    """Information about a busy worker."""

    worker_id: str
    job_id: str
    document_path: str
    elapsed_seconds: int
    output_format: Optional[str] = None
    prog_lang: Optional[str] = None
    language: Optional[str] = None
    kind: Optional[str] = None


class WorkerTypeStatsResponse(BaseModel):
    """Statistics for a worker type."""

    worker_type: str
    execution_mode: Optional[str] = None
    total: int
    idle: int
    busy: int
    hung: int
    dead: int
    busy_workers: List[BusyWorkerDetail] = Field(default_factory=list)


class WorkerDetailResponse(BaseModel):
    """Detailed worker information."""

    worker_id: str
    worker_type: str
    status: str
    execution_mode: Optional[str]
    current_job_id: Optional[str] = None
    current_document: Optional[str] = None
    elapsed_seconds: Optional[int] = None
    jobs_processed: int
    uptime_seconds: int
    last_heartbeat: Optional[datetime] = None


class WorkersListResponse(BaseModel):
    """Response with list of workers."""

    workers: List[WorkerDetailResponse]
    total: int


# Job Models
class JobSummary(BaseModel):
    """Job summary information."""

    job_id: int
    job_type: str
    status: str
    input_file: str
    output_file: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None
    output_format: Optional[str] = None
    prog_lang: Optional[str] = None
    language: Optional[str] = None
    kind: Optional[str] = None


class JobsListResponse(BaseModel):
    """Response with list of jobs."""

    jobs: List[JobSummary]
    total: int
    page: int = 1
    page_size: int = 50


# Queue Models
class QueueStatsResponse(BaseModel):
    """Queue statistics."""

    pending: int
    processing: int
    completed_last_hour: int
    failed_last_hour: int
    oldest_pending_seconds: Optional[int] = None
    throughput_jobs_per_min: float = 0.0
    avg_duration_seconds: float = 0.0


# Status Models
class DatabaseInfoResponse(BaseModel):
    """Database information."""

    path: str
    accessible: bool
    exists: bool
    size_bytes: Optional[int] = None
    last_modified: Optional[datetime] = None


class StatusResponse(BaseModel):
    """Overall system status."""

    status: str  # healthy, warning, error
    timestamp: datetime
    database: DatabaseInfoResponse
    workers: dict[str, WorkerTypeStatsResponse]
    queue: QueueStatsResponse
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# Error Response
class ErrorResponse(BaseModel):
    """Error response."""

    error: str
    code: str
    details: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
