"""Data models for status information."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


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
    size_bytes: Optional[int] = None
    last_modified: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class BusyWorkerInfo:
    """Information about a busy worker."""

    worker_id: str
    job_id: str
    document_path: str
    elapsed_seconds: int
    output_format: Optional[str] = None
    prog_lang: Optional[str] = None
    language: Optional[str] = None
    kind: Optional[str] = None


@dataclass
class WorkerTypeStats:
    """Statistics for a specific worker type."""

    worker_type: str  # notebook, plantuml, drawio
    execution_mode: Optional[str]  # direct, docker, or mixed
    total: int
    idle: int
    busy: int
    hung: int
    dead: int
    busy_workers: List[BusyWorkerInfo] = field(default_factory=list)


@dataclass
class QueueStats:
    """Job queue statistics."""

    pending: int
    processing: int
    completed_last_hour: int
    failed_last_hour: int
    oldest_pending_seconds: Optional[int] = None


@dataclass
class StatusInfo:
    """Complete system status information."""

    timestamp: datetime
    health: SystemHealth
    database: DatabaseInfo
    workers: Dict[str, WorkerTypeStats]  # key: worker_type
    queue: QueueStats
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
