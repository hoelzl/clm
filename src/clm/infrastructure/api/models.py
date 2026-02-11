"""Pydantic models for Worker REST API requests and responses."""

from typing import Any

from pydantic import BaseModel, Field

# === Worker Registration ===


class WorkerRegistrationRequest(BaseModel):
    """Request body for worker registration."""

    worker_type: str = Field(..., description="Type of worker: notebook, plantuml, drawio")
    container_id: str = Field(..., description="Docker container ID or worker identifier")
    parent_pid: int | None = Field(None, description="Parent process ID for orphan detection")


class WorkerRegistrationResponse(BaseModel):
    """Response body for worker registration."""

    worker_id: int = Field(..., description="Assigned worker ID from database")
    registered_at: str = Field(..., description="ISO format timestamp of registration")


# === Job Claiming ===


class JobClaimRequest(BaseModel):
    """Request body for claiming a job."""

    worker_id: int = Field(..., description="ID of the worker claiming the job")
    job_type: str = Field(..., description="Type of job to claim: notebook, plantuml, drawio")


class JobData(BaseModel):
    """Job data returned when a job is claimed."""

    id: int
    job_type: str
    input_file: str
    output_file: str
    content_hash: str
    payload: dict[str, Any]
    correlation_id: str | None = None


class JobClaimResponse(BaseModel):
    """Response body for job claim."""

    job: JobData | None = Field(None, description="Claimed job data, or null if no jobs available")


# === Job Status Update ===


class JobStatusUpdateRequest(BaseModel):
    """Request body for updating job status."""

    worker_id: int = Field(..., description="ID of the worker updating the job")
    status: str = Field(..., description="New status: completed or failed")
    result: dict[str, Any] | None = Field(
        None, description="Result data (for completed jobs, may include warnings)"
    )
    error: dict[str, Any] | None = Field(None, description="Error data (for failed jobs)")


class JobStatusUpdateResponse(BaseModel):
    """Response body for job status update."""

    acknowledged: bool = True


# === Heartbeat ===


class HeartbeatRequest(BaseModel):
    """Request body for worker heartbeat."""

    worker_id: int = Field(..., description="ID of the worker sending heartbeat")


class HeartbeatResponse(BaseModel):
    """Response body for heartbeat."""

    acknowledged: bool = True
    timestamp: str = Field(..., description="Server timestamp of heartbeat receipt")


# === Job Cancellation Check ===


class JobCancellationResponse(BaseModel):
    """Response body for job cancellation check."""

    cancelled: bool = Field(..., description="Whether the job has been cancelled")
    cancelled_at: str | None = Field(None, description="ISO timestamp when cancelled")
    cancelled_by: str | None = Field(None, description="Who/what cancelled the job")


# === Worker Unregistration ===


class WorkerUnregisterRequest(BaseModel):
    """Request body for worker unregistration."""

    worker_id: int = Field(..., description="ID of the worker to unregister")
    reason: str = Field("graceful_shutdown", description="Reason for unregistration")


class WorkerUnregisterResponse(BaseModel):
    """Response body for worker unregistration."""

    acknowledged: bool = True


# === Worker Activation (Pre-registration) ===


class WorkerActivationRequest(BaseModel):
    """Request body for activating a pre-registered worker."""

    worker_id: int = Field(..., description="Pre-assigned worker ID from CLM_WORKER_ID")


class WorkerActivationResponse(BaseModel):
    """Response body for worker activation."""

    acknowledged: bool = True
    activated_at: str = Field(..., description="ISO timestamp of activation")


# === Cache ===


class CacheAddRequest(BaseModel):
    """Request body for adding result to cache."""

    output_file: str = Field(..., description="Output file path")
    content_hash: str = Field(..., description="Content hash of the source file")
    result_metadata: dict[str, Any] = Field(..., description="Metadata about the result")


class CacheAddResponse(BaseModel):
    """Response body for cache add."""

    acknowledged: bool = True


# === Health Check ===


class HealthResponse(BaseModel):
    """Response body for health check."""

    status: str = "ok"
    version: str
    api_version: str = "1.0"
