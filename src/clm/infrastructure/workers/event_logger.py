"""Worker lifecycle event logging."""

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from clm.infrastructure.database.job_queue import JobQueue

logger = logging.getLogger(__name__)


class WorkerEventType(Enum):
    """Worker lifecycle event types."""

    WORKER_STARTING = "worker_starting"
    WORKER_REGISTERED = "worker_registered"
    WORKER_READY = "worker_ready"
    WORKER_STOPPING = "worker_stopping"
    WORKER_STOPPED = "worker_stopped"
    WORKER_FAILED = "worker_failed"
    POOL_STARTING = "pool_starting"
    POOL_STARTED = "pool_started"
    POOL_STOPPING = "pool_stopping"
    POOL_STOPPED = "pool_stopped"


class WorkerEventLogger:
    """Log worker lifecycle events to database."""

    def __init__(self, db_path: Path, session_id: str | None = None):
        """Initialize event logger.

        Args:
            db_path: Path to database
            session_id: Optional session identifier for grouping events
        """
        self.db_path = db_path
        self.session_id = session_id or f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.job_queue = JobQueue(db_path)

    def close(self):
        """Close database connection."""
        if hasattr(self, "job_queue") and self.job_queue is not None:
            self.job_queue.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

    def log_event(
        self,
        event_type: WorkerEventType,
        worker_type: str,
        message: str,
        worker_id: int | None = None,
        execution_mode: str | None = None,
        **metadata,
    ) -> int:
        """Log a worker lifecycle event.

        Args:
            event_type: Type of event
            worker_type: Worker type (notebook, plantuml, drawio)
            message: Human-readable message
            worker_id: Optional worker ID (for worker-specific events)
            execution_mode: Optional execution mode (docker/direct)
            **metadata: Additional event-specific metadata

        Returns:
            Event ID
        """
        conn = self.job_queue._get_conn()

        # Add common metadata
        metadata["timestamp"] = datetime.now().isoformat()

        cursor = conn.execute(
            """
            INSERT INTO worker_events (
                event_type, worker_id, worker_type, execution_mode,
                message, metadata, session_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type.value,
                worker_id,
                worker_type,
                execution_mode,
                message,
                json.dumps(metadata),
                self.session_id,
            ),
        )

        event_id = cursor.lastrowid
        assert event_id is not None, "INSERT should always return a valid lastrowid"
        conn.commit()

        # Also log to application logger
        log_level = logging.INFO
        if event_type == WorkerEventType.WORKER_FAILED:
            log_level = logging.ERROR
        elif event_type in (
            WorkerEventType.WORKER_STOPPING,
            WorkerEventType.WORKER_STOPPED,
        ):
            log_level = logging.DEBUG

        logger.log(log_level, f"[{event_type.value}] {message}")

        return event_id

    def log_worker_starting(
        self, worker_type: str, execution_mode: str, index: int, config: dict[str, Any]
    ) -> int:
        """Log worker starting event."""
        return self.log_event(
            WorkerEventType.WORKER_STARTING,
            worker_type=worker_type,
            message=f"Starting {execution_mode} worker {worker_type}-{index}",
            execution_mode=execution_mode,
            index=index,
            config=config,
        )

    def log_worker_registered(
        self, worker_type: str, worker_id: int, executor_id: str, execution_mode: str
    ) -> int:
        """Log worker registered event."""
        return self.log_event(
            WorkerEventType.WORKER_REGISTERED,
            worker_type=worker_type,
            message=f"Worker {worker_type} #{worker_id} registered (executor: {executor_id[:12]})",
            worker_id=worker_id,
            execution_mode=execution_mode,
            executor_id=executor_id,
        )

    def log_worker_ready(self, worker_type: str, worker_id: int, execution_mode: str) -> int:
        """Log worker ready event."""
        return self.log_event(
            WorkerEventType.WORKER_READY,
            worker_type=worker_type,
            message=f"Worker {worker_type} #{worker_id} ready to accept jobs",
            worker_id=worker_id,
            execution_mode=execution_mode,
        )

    def log_worker_stopping(
        self, worker_type: str, worker_id: int, reason: str = "shutdown"
    ) -> int:
        """Log worker stopping event."""
        return self.log_event(
            WorkerEventType.WORKER_STOPPING,
            worker_type=worker_type,
            message=f"Stopping worker {worker_type} #{worker_id} ({reason})",
            worker_id=worker_id,
            reason=reason,
        )

    def log_worker_stopped(
        self, worker_type: str, worker_id: int, jobs_processed: int, uptime_seconds: float
    ) -> int:
        """Log worker stopped event."""
        return self.log_event(
            WorkerEventType.WORKER_STOPPED,
            worker_type=worker_type,
            message=f"Worker {worker_type} #{worker_id} stopped (processed {jobs_processed} jobs in {uptime_seconds:.1f}s)",
            worker_id=worker_id,
            jobs_processed=jobs_processed,
            uptime_seconds=uptime_seconds,
        )

    def log_worker_failed(
        self, worker_type: str, error: str, worker_id: int | None = None, **details
    ) -> int:
        """Log worker failed event."""
        return self.log_event(
            WorkerEventType.WORKER_FAILED,
            worker_type=worker_type,
            message=f"Worker {worker_type} failed: {error}",
            worker_id=worker_id,
            error=error,
            **details,
        )

    def log_pool_starting(self, worker_configs: list, total_workers: int) -> int:
        """Log pool starting event."""
        return self.log_event(
            WorkerEventType.POOL_STARTING,
            worker_type="all",
            message=f"Starting worker pool with {total_workers} worker(s)",
            total_workers=total_workers,
            configs=[
                {
                    "worker_type": c.worker_type,
                    "execution_mode": c.execution_mode,
                    "count": c.count,
                }
                for c in worker_configs
            ],
        )

    def log_pool_started(self, worker_count: int, duration_seconds: float) -> int:
        """Log pool started event."""
        return self.log_event(
            WorkerEventType.POOL_STARTED,
            worker_type="all",
            message=f"Worker pool started with {worker_count} worker(s) in {duration_seconds:.1f}s",
            worker_count=worker_count,
            duration_seconds=duration_seconds,
        )

    def log_pool_stopping(self) -> int:
        """Log pool stopping event."""
        return self.log_event(
            WorkerEventType.POOL_STOPPING, worker_type="all", message="Stopping worker pool"
        )

    def log_pool_stopped(self, workers_stopped: int, duration_seconds: float) -> int:
        """Log pool stopped event."""
        return self.log_event(
            WorkerEventType.POOL_STOPPED,
            worker_type="all",
            message=f"Worker pool stopped ({workers_stopped} worker(s) in {duration_seconds:.1f}s)",
            workers_stopped=workers_stopped,
            duration_seconds=duration_seconds,
        )
