"""Collect system status information from database."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import cast

from clm.cli.status.models import (
    BusyWorkerInfo,
    DatabaseInfo,
    ErrorStats,
    ErrorTypeStats,
    QueueStats,
    StatusInfo,
    SystemHealth,
    WorkerTypeStats,
)
from clm.infrastructure.database.job_queue import JobQueue

logger = logging.getLogger(__name__)


class StatusCollector:
    """Collect system status from database."""

    HUNG_WORKER_THRESHOLD_SECONDS = 300  # 5 minutes
    STALE_DATA_THRESHOLD_SECONDS = 60  # 1 minute
    LONG_QUEUE_THRESHOLD = 10
    OLD_PENDING_JOB_THRESHOLD_SECONDS = 300  # 5 minutes

    def __init__(self, db_path: Path | None = None):
        """Initialize status collector.

        Args:
            db_path: Path to database. If None, use default location.
        """
        self.db_path = db_path or self._get_default_db_path()
        self.job_queue: JobQueue | None = None

    def __enter__(self) -> "StatusCollector":
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager and close resources."""
        self.close()
        return None

    def close(self):
        """Close database connection and cleanup resources."""
        if self.job_queue:
            self.job_queue.close()
            self.job_queue = None

    def _get_default_db_path(self) -> Path:
        """Get default database path from environment or config."""
        # Check environment variable
        db_path = os.getenv("CLM_DB_PATH")
        if db_path:
            return Path(db_path)

        # Check current directory
        default_paths = [
            Path.cwd() / "clm_jobs.db",
            Path.cwd() / "jobs.db",
            Path.home() / ".clx" / "clm_jobs.db",
        ]

        for path in default_paths:
            if path.exists():
                return path

        # Return default (may not exist yet)
        return Path.cwd() / "clm_jobs.db"

    def collect(self) -> StatusInfo:
        """Collect complete system status.

        Returns:
            StatusInfo with all collected data
        """
        timestamp = datetime.now()

        # Check database
        db_info = self._collect_database_info()

        if not db_info.accessible:
            # Database not accessible - return error state
            return StatusInfo(
                timestamp=timestamp,
                health=SystemHealth.ERROR,
                database=db_info,
                workers={},
                queue=QueueStats(0, 0, 0, 0, None),
                errors=[db_info.error_message or "Database not accessible"],
            )

        # Initialize job queue
        try:
            self.job_queue = JobQueue(self.db_path)
        except Exception as e:
            logger.error(f"Failed to initialize job queue: {e}", exc_info=True)
            return StatusInfo(
                timestamp=timestamp,
                health=SystemHealth.ERROR,
                database=db_info,
                workers={},
                queue=QueueStats(0, 0, 0, 0, None),
                errors=[f"Failed to connect to database: {e}"],
            )

        # Collect worker and queue stats
        workers = self._collect_worker_stats()
        queue = self._collect_queue_stats()
        error_stats = self._collect_error_stats()

        # Determine health and collect warnings/errors
        health, warnings, errors = self._determine_health(workers, queue, db_info)

        return StatusInfo(
            timestamp=timestamp,
            health=health,
            database=db_info,
            workers=workers,
            queue=queue,
            warnings=warnings,
            errors=errors,
            error_stats=error_stats,
        )

    def _collect_database_info(self) -> DatabaseInfo:
        """Collect database metadata."""
        path_str = str(self.db_path)

        if not self.db_path.exists():
            return DatabaseInfo(
                path=path_str,
                accessible=False,
                exists=False,
                error_message=f"Database not found: {path_str}",
            )

        try:
            stat = self.db_path.stat()
            return DatabaseInfo(
                path=path_str,
                accessible=True,
                exists=True,
                size_bytes=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime),
            )
        except Exception as e:
            logger.error(f"Error accessing database: {e}", exc_info=True)
            return DatabaseInfo(
                path=path_str,
                accessible=False,
                exists=True,
                error_message=f"Cannot access database: {e}",
            )

    def _collect_worker_stats(self) -> dict[str, WorkerTypeStats]:
        """Collect worker statistics by type."""
        if not self.job_queue:
            return {}

        try:
            conn = self.job_queue._get_conn()

            # Query workers grouped by type and status
            result = {}
            for worker_type in ["notebook", "plantuml", "drawio"]:
                cursor = conn.execute(
                    """
                    SELECT status, COUNT(*) as count
                    FROM workers
                    WHERE worker_type = ?
                    GROUP BY status
                    """,
                    (worker_type,),
                )

                status_counts = {row[0]: row[1] for row in cursor.fetchall()}

                total = sum(status_counts.values())
                idle = status_counts.get("idle", 0)
                busy = status_counts.get("busy", 0)
                hung = status_counts.get("hung", 0)
                dead = status_counts.get("dead", 0)

                # Get busy workers details
                busy_workers = self._get_busy_workers(worker_type)

                # Determine execution mode
                execution_mode = self._get_worker_execution_mode(worker_type)

                result[worker_type] = WorkerTypeStats(
                    worker_type=worker_type,
                    execution_mode=execution_mode,
                    total=total,
                    idle=idle,
                    busy=busy,
                    hung=hung,
                    dead=dead,
                    busy_workers=busy_workers,
                )

            return result

        except Exception as e:
            logger.error(f"Error collecting worker stats: {e}", exc_info=True)
            return {}

    def _get_busy_workers(self, worker_type: str) -> list[BusyWorkerInfo]:
        """Get details of busy workers for a type."""
        if not self.job_queue:
            return []

        try:
            conn = self.job_queue._get_conn()
            cursor = conn.execute(
                """
                SELECT
                    w.container_id,
                    j.id,
                    j.input_file,
                    CAST((julianday('now') - julianday(j.started_at)) * 86400 AS INTEGER) as elapsed,
                    j.job_type,
                    j.payload
                FROM workers w
                JOIN jobs j ON j.worker_id = w.id
                WHERE w.worker_type = ?
                  AND w.status = 'busy'
                  AND j.status = 'processing'
                ORDER BY j.started_at ASC
                """,
                (worker_type,),
            )

            busy_workers = []
            for row in cursor.fetchall():
                # Parse payload to extract format/language info
                payload_info = self._parse_job_payload(row[4], row[5])

                busy_workers.append(
                    BusyWorkerInfo(
                        worker_id=row[0],
                        job_id=str(row[1]),
                        document_path=row[2],
                        elapsed_seconds=row[3] or 0,
                        output_format=payload_info.get("output_format"),
                        prog_lang=payload_info.get("prog_lang"),
                        language=payload_info.get("language"),
                        kind=payload_info.get("kind"),
                    )
                )

            return busy_workers

        except Exception as e:
            logger.error(f"Error getting busy workers: {e}", exc_info=True)
            return []

    def _parse_job_payload(self, job_type: str, payload_json: str | None) -> dict:
        """Parse job payload and extract relevant fields.

        Args:
            job_type: Type of job (notebook, plantuml, drawio)
            payload_json: JSON string of the payload

        Returns:
            Dictionary with extracted fields (output_format, prog_lang, language, kind)
        """
        if not payload_json:
            return {}

        try:
            payload = json.loads(payload_json)

            if job_type == "notebook":
                return {
                    "output_format": payload.get("format"),
                    "prog_lang": payload.get("prog_lang"),
                    "language": payload.get("language"),
                    "kind": payload.get("kind"),
                }
            elif job_type in ["plantuml", "drawio"]:
                return {
                    "output_format": payload.get("output_format", "png"),
                }
            return {}
        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            logger.debug(f"Failed to parse job payload: {e}")
            return {}

    def _get_worker_execution_mode(self, worker_type: str) -> str | None:
        """Get execution mode for worker type."""
        if not self.job_queue:
            return None

        try:
            conn = self.job_queue._get_conn()
            cursor = conn.execute(
                """
                SELECT DISTINCT execution_mode
                FROM workers
                WHERE worker_type = ?
                  AND status != 'dead'
                """,
                (worker_type,),
            )

            modes = [row[0] for row in cursor.fetchall() if row[0]]

            if not modes:
                return None
            elif len(modes) == 1:
                return cast(str, modes[0])
            else:
                return "mixed"

        except Exception:
            return None

    def _collect_error_stats(self, hours: int = 1) -> ErrorStats | None:
        """Collect error statistics from recent failed jobs.

        Args:
            hours: Number of hours to look back (default: 1)

        Returns:
            ErrorStats with categorized error information, or None if unable to collect
        """
        if not self.job_queue:
            return None

        try:
            conn = self.job_queue._get_conn()
            # SQLite stores timestamps in local time, use datetime() for comparison
            time_modifier = f"-{hours} hour" if hours == 1 else f"-{hours} hours"

            # Query failed jobs from the last N hours
            cursor = conn.execute(
                f"""
                SELECT error
                FROM jobs
                WHERE status = 'failed'
                  AND completed_at > datetime('now', '{time_modifier}')
                """
            )

            # Parse errors and categorize them
            error_types: dict[str, dict[str, int]] = {}  # type -> {category -> count}
            total_errors = 0

            for row in cursor.fetchall():
                error_message = row[0]
                if not error_message:
                    continue

                total_errors += 1

                # Try to parse as JSON
                try:
                    error_data = json.loads(error_message)
                    error_type = error_data.get("error_type", "unknown")
                    category = error_data.get("category", "uncategorized")
                except (json.JSONDecodeError, TypeError, ValueError):
                    # Old-style string error - count as unknown
                    error_type = "unknown"
                    category = "uncategorized"

                # Initialize type if needed
                if error_type not in error_types:
                    error_types[error_type] = {}

                # Increment category count
                error_types[error_type][category] = error_types[error_type].get(category, 0) + 1

            # Build ErrorStats object
            by_type = {}
            for error_type, categories in error_types.items():
                by_type[error_type] = ErrorTypeStats(
                    error_type=error_type,
                    count=sum(categories.values()),
                    categories=categories,
                )

            return ErrorStats(
                total_errors=total_errors,
                by_type=by_type,
                time_period_hours=hours,
            )

        except Exception as e:
            logger.error(f"Error collecting error stats: {e}", exc_info=True)
            return None

    def _collect_queue_stats(self) -> QueueStats:
        """Collect job queue statistics."""
        if not self.job_queue:
            return QueueStats(0, 0, 0, 0, None)

        try:
            conn = self.job_queue._get_conn()

            # Get counts by status
            cursor = conn.execute(
                """
                SELECT status, COUNT(*) as count
                FROM jobs
                WHERE status IN ('pending', 'processing')
                GROUP BY status
                """
            )

            counts = {row[0]: row[1] for row in cursor.fetchall()}
            pending = counts.get("pending", 0)
            processing = counts.get("processing", 0)

            # Calculate oldest pending job
            oldest_pending_seconds = None
            if pending > 0:
                cursor = conn.execute(
                    """
                    SELECT CAST((julianday('now') - julianday(created_at)) * 86400 AS INTEGER)
                    FROM jobs
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()
                if row:
                    oldest_pending_seconds = row[0]

            # Get completed/failed in last hour
            # SQLite stores timestamps in local time, so use local time for comparison
            # Use datetime('now', '-1 hour') in SQL for correct local time comparison
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE status = 'completed'
                  AND completed_at > datetime('now', '-1 hour')
                """
            )
            completed_last_hour = cursor.fetchone()[0]

            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE status = 'failed'
                  AND completed_at > datetime('now', '-1 hour')
                """
            )
            failed_last_hour = cursor.fetchone()[0]

            return QueueStats(
                pending=pending,
                processing=processing,
                completed_last_hour=completed_last_hour,
                failed_last_hour=failed_last_hour,
                oldest_pending_seconds=oldest_pending_seconds,
            )

        except Exception as e:
            logger.error(f"Error collecting queue stats: {e}", exc_info=True)
            return QueueStats(0, 0, 0, 0, None)

    def _determine_health(
        self,
        workers: dict[str, WorkerTypeStats],
        queue: QueueStats,
        db_info: DatabaseInfo,
    ) -> tuple[SystemHealth, list[str], list[str]]:
        """Determine system health and collect warnings/errors.

        Returns:
            Tuple of (health, warnings, errors)
        """
        warnings: list[str] = []
        errors: list[str] = []

        # Check database
        if not db_info.accessible:
            errors.append("Database not accessible")
            return SystemHealth.ERROR, warnings, errors

        # Check for workers
        total_workers = sum(stats.total for stats in workers.values())
        if total_workers == 0:
            errors.append("No workers registered")
            return SystemHealth.ERROR, warnings, errors

        # Check for hung workers
        hung_workers = sum(stats.hung for stats in workers.values())
        if hung_workers > 0:
            warnings.append(f"{hung_workers} worker(s) hung (processing > 5 minutes)")

        # Check for dead workers
        dead_workers = sum(stats.dead for stats in workers.values())
        if dead_workers > 0:
            warnings.append(f"{dead_workers} worker(s) dead (no heartbeat)")

        # Check queue
        if queue.pending > self.LONG_QUEUE_THRESHOLD:
            idle_workers = sum(stats.idle for stats in workers.values())
            if idle_workers == 0:
                warnings.append(f"{queue.pending} jobs pending with no idle workers available")
            else:
                warnings.append(f"{queue.pending} jobs pending")

        # Check oldest pending job
        if (
            queue.oldest_pending_seconds
            and queue.oldest_pending_seconds > self.OLD_PENDING_JOB_THRESHOLD_SECONDS
        ):
            minutes = queue.oldest_pending_seconds // 60
            warnings.append(f"Oldest pending job waiting {minutes} minutes")

        # Check failure rate
        total_recent_jobs = queue.completed_last_hour + queue.failed_last_hour
        if total_recent_jobs > 0:
            failure_rate = queue.failed_last_hour / total_recent_jobs
            if failure_rate > 0.2:  # > 20% failure
                warnings.append(
                    f"High failure rate: {failure_rate:.1%} ({queue.failed_last_hour}/{total_recent_jobs} jobs)"
                )

        # Determine overall health
        if errors:
            return SystemHealth.ERROR, warnings, errors
        elif warnings:
            return SystemHealth.WARNING, warnings, errors
        else:
            return SystemHealth.HEALTHY, warnings, errors
