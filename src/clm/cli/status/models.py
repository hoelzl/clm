"""Data models for status information."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SystemHealth(Enum):
    """Overall system health status."""

    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"


class WorkerStatus(Enum):
    """Worker status."""

    IDLE = "idle"
    BUSY = "busy"
    HUNG = "hung"
    DEAD = "dead"


@dataclass
class DatabaseInfo:
    """Database connectivity and metadata."""

    path: str
    accessible: bool
    exists: bool
    size_bytes: int | None = None
    last_modified: datetime | None = None
    error_message: str | None = None


@dataclass
class BusyWorkerInfo:
    """Information about a busy worker."""

    worker_id: str
    job_id: str
    document_path: str
    elapsed_seconds: int
    output_format: str | None = None
    prog_lang: str | None = None
    language: str | None = None
    kind: str | None = None


@dataclass
class WorkerTypeStats:
    """Statistics for a specific worker type."""

    worker_type: str  # notebook, plantuml, drawio
    execution_mode: str | None  # direct, docker, or mixed
    total: int
    idle: int
    busy: int
    hung: int
    dead: int
    busy_workers: list[BusyWorkerInfo] = field(default_factory=list)


@dataclass
class QueueStats:
    """Job queue statistics."""

    pending: int
    processing: int
    completed_last_hour: int
    failed_last_hour: int
    oldest_pending_seconds: int | None = None


@dataclass
class ErrorTypeStats:
    """Statistics for a specific error type."""

    error_type: str  # user, configuration, infrastructure
    count: int
    categories: dict[str, int] = field(default_factory=dict)  # category -> count


@dataclass
class ErrorStats:
    """Error statistics for recent failed jobs."""

    total_errors: int
    by_type: dict[str, ErrorTypeStats] = field(default_factory=dict)  # error_type -> stats
    time_period_hours: int = 1


@dataclass
class StatusInfo:
    """Complete system status information."""

    timestamp: datetime
    health: SystemHealth
    database: DatabaseInfo
    workers: dict[str, WorkerTypeStats]  # key: worker_type
    queue: QueueStats
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    error_stats: ErrorStats | None = None  # Recent error statistics
