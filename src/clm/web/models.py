"""Pydantic models for web API."""

from datetime import datetime

from pydantic import BaseModel, Field


# Health & Metadata Models
class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str
    database_path: str


class VersionResponse(BaseModel):
    """Version information response."""

    clm_version: str
    api_version: str = "1.0"


# Worker Models
class BusyWorkerDetail(BaseModel):
    """Information about a busy worker."""

    worker_id: str
    job_id: str
    document_path: str
    elapsed_seconds: int
    output_format: str | None = None
    prog_lang: str | None = None
    language: str | None = None
    kind: str | None = None


class WorkerTypeStatsResponse(BaseModel):
    """Statistics for a worker type."""

    worker_type: str
    execution_mode: str | None = None
    total: int
    idle: int
    busy: int
    hung: int
    dead: int
    busy_workers: list[BusyWorkerDetail] = Field(default_factory=list)


class WorkerDetailResponse(BaseModel):
    """Detailed worker information."""

    worker_id: str
    worker_type: str
    status: str
    execution_mode: str | None
    current_job_id: str | None = None
    current_document: str | None = None
    elapsed_seconds: int | None = None
    jobs_processed: int
    uptime_seconds: int
    last_heartbeat: datetime | None = None


class WorkersListResponse(BaseModel):
    """Response with list of workers."""

    workers: list[WorkerDetailResponse]
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
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    error_message: str | None = None
    output_format: str | None = None
    prog_lang: str | None = None
    language: str | None = None
    kind: str | None = None


class JobsListResponse(BaseModel):
    """Response with list of jobs."""

    jobs: list[JobSummary]
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
    oldest_pending_seconds: int | None = None
    throughput_jobs_per_min: float = 0.0
    avg_duration_seconds: float = 0.0


# Status Models
class DatabaseInfoResponse(BaseModel):
    """Database information."""

    path: str
    accessible: bool
    exists: bool
    size_bytes: int | None = None
    last_modified: datetime | None = None


class StatusResponse(BaseModel):
    """Overall system status."""

    status: str  # healthy, warning, error
    timestamp: datetime
    database: DatabaseInfoResponse
    workers: dict[str, WorkerTypeStatsResponse]
    queue: QueueStatsResponse
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# Error Response
class ErrorResponse(BaseModel):
    """Error response."""

    error: str
    code: str
    details: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)
