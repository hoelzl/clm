"""Worker discovery and health checking utilities."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.workers.worker_executor import WorkerExecutor

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredWorker:
    """Information about a discovered worker."""

    db_id: int
    worker_type: str
    executor_id: str
    status: str
    last_heartbeat: datetime
    jobs_processed: int
    jobs_failed: int
    started_at: datetime
    is_docker: bool
    is_healthy: bool


class WorkerDiscovery:
    """Discover and validate existing workers."""

    def __init__(
        self, db_path: Path, executors: dict[str, WorkerExecutor] | None = None
    ):
        """Initialize worker discovery.

        Args:
            db_path: Path to database
            executors: Optional dict of execution_mode -> executor
        """
        self.db_path = db_path
        self.job_queue = JobQueue(db_path)
        self.executors = executors or {}

    def discover_workers(
        self,
        worker_type: str | None = None,
        status_filter: list[str] | None = None,
    ) -> list[DiscoveredWorker]:
        """Discover workers from database.

        Args:
            worker_type: Filter by worker type (None = all types)
            status_filter: Filter by status (None = all statuses)

        Returns:
            List of discovered workers
        """
        conn = self.job_queue._get_conn()

        # Build query
        query = """
            SELECT
                id, worker_type, container_id, status,
                last_heartbeat, jobs_processed, jobs_failed, started_at
            FROM workers
        """

        conditions = []
        params = []

        if worker_type:
            conditions.append("worker_type = ?")
            params.append(worker_type)

        if status_filter:
            placeholders = ",".join("?" * len(status_filter))
            conditions.append(f"status IN ({placeholders})")
            params.extend(status_filter)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY worker_type, id"

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        # Convert to DiscoveredWorker objects
        workers = []
        for row in rows:
            is_docker = not row[2].startswith("direct-")

            # Parse timestamps from database (SQLite CURRENT_TIMESTAMP is UTC)
            # Make them timezone-aware by adding UTC timezone info
            last_heartbeat = datetime.fromisoformat(row[4]).replace(tzinfo=timezone.utc)
            started_at = datetime.fromisoformat(row[7]).replace(tzinfo=timezone.utc)

            worker = DiscoveredWorker(
                db_id=row[0],
                worker_type=row[1],
                executor_id=row[2],
                status=row[3],
                last_heartbeat=last_heartbeat,
                jobs_processed=row[5],
                jobs_failed=row[6],
                started_at=started_at,
                is_docker=is_docker,
                is_healthy=False,  # Will be set by health check
            )

            workers.append(worker)

        # Perform health checks
        for worker in workers:
            worker.is_healthy = self.check_worker_health(worker)

        return workers

    def check_worker_health(self, worker: DiscoveredWorker) -> bool:
        """Check if a worker is healthy.

        A worker is considered healthy if:
        - Status is 'idle' or 'busy'
        - Heartbeat is recent (within 30 seconds)
        - Process/container is running (if executor available)

        Args:
            worker: Worker to check

        Returns:
            True if worker is healthy
        """
        # 1. Check status
        if worker.status not in ("idle", "busy"):
            logger.debug(f"Worker {worker.db_id} status is {worker.status}")
            return False

        # 2. Check heartbeat (within last 30 seconds)
        # Note: SQLite CURRENT_TIMESTAMP returns UTC, so we must use timezone-aware UTC
        # to compare against database timestamps correctly
        heartbeat_age = datetime.now(timezone.utc) - worker.last_heartbeat
        if heartbeat_age > timedelta(seconds=30):
            logger.debug(
                f"Worker {worker.db_id} has stale heartbeat "
                f"({heartbeat_age.total_seconds():.1f}s ago)"
            )
            return False

        # 3. Check process/container is actually running
        executor_type = "docker" if worker.is_docker else "direct"

        if executor_type not in self.executors:
            # No executor available to check, assume healthy based on heartbeat
            logger.debug(f"No executor for type {executor_type}, relying on heartbeat")
            return True

        executor = self.executors[executor_type]

        try:
            if not executor.is_worker_running(worker.executor_id):
                logger.debug(f"Worker {worker.db_id} process/container not running")
                return False
        except Exception as e:
            logger.debug(f"Error checking worker {worker.db_id}: {e}")
            return False

        return True

    def count_healthy_workers(self, worker_type: str) -> int:
        """Count healthy workers of a specific type.

        Args:
            worker_type: Worker type to count

        Returns:
            Number of healthy workers
        """
        workers = self.discover_workers(
            worker_type=worker_type, status_filter=["idle", "busy"]
        )

        return sum(1 for w in workers if w.is_healthy)

    def get_worker_summary(self) -> dict[str, dict[str, int]]:
        """Get summary of workers by type and status.

        Returns:
            Dict of worker_type -> {status -> count}
        """
        workers = self.discover_workers()

        summary: dict[str, dict[str, int]] = {}
        for worker in workers:
            if worker.worker_type not in summary:
                summary[worker.worker_type] = {
                    "total": 0,
                    "healthy": 0,
                    "unhealthy": 0,
                }

            summary[worker.worker_type]["total"] += 1

            if worker.is_healthy:
                summary[worker.worker_type]["healthy"] += 1
            else:
                summary[worker.worker_type]["unhealthy"] += 1

        return summary
