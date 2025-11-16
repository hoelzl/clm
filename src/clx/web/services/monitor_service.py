"""Service layer for monitoring data."""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from clx.cli.status.collector import StatusCollector
from clx.cli.status.models import StatusInfo, SystemHealth
from clx.web.models import (
    BusyWorkerDetail,
    DatabaseInfoResponse,
    JobSummary,
    QueueStatsResponse,
    StatusResponse,
    WorkerDetailResponse,
    WorkersListResponse,
    WorkerTypeStatsResponse,
)
from clx.infrastructure.database.job_queue import JobQueue

logger = logging.getLogger(__name__)


class MonitorService:
    """Service for monitoring operations."""

    def __init__(self, db_path: Path):
        """Initialize monitor service.

        Args:
            db_path: Path to database
        """
        self.db_path = db_path
        self.status_collector = StatusCollector(db_path=db_path)
        self.job_queue: Optional[JobQueue] = None

    def _get_job_queue(self) -> JobQueue:
        """Get or create job queue instance."""
        if self.job_queue is None:
            self.job_queue = JobQueue(self.db_path)
        return self.job_queue

    def get_status(self) -> StatusResponse:
        """Get overall system status.

        Returns:
            StatusResponse with complete system status
        """
        # Collect status using existing collector
        status_info: StatusInfo = self.status_collector.collect()

        # Convert to API response model
        db_info = DatabaseInfoResponse(
            path=status_info.database.path,
            accessible=status_info.database.accessible,
            exists=status_info.database.exists,
            size_bytes=status_info.database.size_bytes,
            last_modified=status_info.database.last_modified,
        )

        # Convert worker stats
        workers_response = {}
        for worker_type, stats in status_info.workers.items():
            busy_workers = [
                BusyWorkerDetail(
                    worker_id=w.worker_id,
                    job_id=w.job_id,
                    document_path=w.document_path,
                    elapsed_seconds=w.elapsed_seconds,
                )
                for w in stats.busy_workers
            ]

            workers_response[worker_type] = WorkerTypeStatsResponse(
                worker_type=worker_type,
                execution_mode=stats.execution_mode,
                total=stats.total,
                idle=stats.idle,
                busy=stats.busy,
                hung=stats.hung,
                dead=stats.dead,
                busy_workers=busy_workers,
            )

        # Convert queue stats
        queue_response = QueueStatsResponse(
            pending=status_info.queue.pending,
            processing=status_info.queue.processing,
            completed_last_hour=status_info.queue.completed_last_hour,
            failed_last_hour=status_info.queue.failed_last_hour,
            oldest_pending_seconds=status_info.queue.oldest_pending_seconds,
        )

        return StatusResponse(
            status=status_info.health.value,
            timestamp=status_info.timestamp,
            database=db_info,
            workers=workers_response,
            queue=queue_response,
            warnings=status_info.warnings,
            errors=status_info.errors,
        )

    def get_workers(self) -> WorkersListResponse:
        """Get list of all workers.

        Returns:
            WorkersListResponse with worker details
        """
        workers = []

        try:
            if not self.db_path.exists():
                return WorkersListResponse(workers=[], total=0)

            job_queue = self._get_job_queue()
            conn = job_queue._get_conn()

            cursor = conn.execute(
                """
                SELECT
                    w.worker_id,
                    w.worker_type,
                    w.status,
                    w.execution_mode,
                    w.jobs_processed,
                    w.created_at,
                    w.last_heartbeat,
                    j.id as current_job_id,
                    j.input_file as current_document,
                    CAST((julianday('now') - julianday(j.started_at)) * 86400 AS INTEGER) as elapsed
                FROM workers w
                LEFT JOIN jobs j ON j.worker_id = w.id AND j.status = 'processing'
                WHERE w.status != 'dead'
                ORDER BY w.worker_type, w.id
                """
            )

            for row in cursor.fetchall():
                created_at = datetime.fromisoformat(row[5])
                uptime_seconds = int((datetime.now() - created_at).total_seconds())

                last_heartbeat = None
                if row[6]:
                    last_heartbeat = datetime.fromisoformat(row[6])

                workers.append(
                    WorkerDetailResponse(
                        worker_id=row[0],
                        worker_type=row[1],
                        status=row[2],
                        execution_mode=row[3],
                        current_job_id=str(row[7]) if row[7] else None,
                        current_document=row[8],
                        elapsed_seconds=row[9],
                        jobs_processed=row[4] or 0,
                        uptime_seconds=uptime_seconds,
                        last_heartbeat=last_heartbeat,
                    )
                )

            return WorkersListResponse(workers=workers, total=len(workers))

        except Exception as e:
            logger.error(f"Error getting workers: {e}", exc_info=True)
            return WorkersListResponse(workers=[], total=0)

    def get_jobs(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobSummary]:
        """Get list of jobs.

        Args:
            status: Filter by status (pending, processing, completed, failed)
            limit: Maximum jobs to return
            offset: Number of jobs to skip

        Returns:
            List of JobSummary objects
        """
        jobs = []

        try:
            if not self.db_path.exists():
                return []

            job_queue = self._get_job_queue()
            conn = job_queue._get_conn()

            # Build query
            where_clause = "WHERE 1=1"
            params = []

            if status:
                where_clause += " AND status = ?"
                params.append(status)

            query = f"""
                SELECT
                    id,
                    job_type,
                    status,
                    input_file,
                    output_file,
                    created_at,
                    started_at,
                    completed_at,
                    error,
                    CAST((julianday(completed_at) - julianday(started_at)) * 86400 AS INTEGER) as duration
                FROM jobs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])

            cursor = conn.execute(query, params)

            for row in cursor.fetchall():
                jobs.append(
                    JobSummary(
                        job_id=row[0],
                        job_type=row[1],
                        status=row[2],
                        input_file=row[3],
                        output_file=row[4],
                        created_at=datetime.fromisoformat(row[5]),
                        started_at=(
                            datetime.fromisoformat(row[6]) if row[6] else None
                        ),
                        completed_at=(
                            datetime.fromisoformat(row[7]) if row[7] else None
                        ),
                        error_message=row[8],
                        duration_seconds=row[9],
                    )
                )

            return jobs

        except Exception as e:
            logger.error(f"Error getting jobs: {e}", exc_info=True)
            return []
