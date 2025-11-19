"""Job queue management using SQLite.

This module provides the JobQueue class for managing job submission, retrieval,
and status updates, as well as result caching.
"""

import json
import logging
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """Represents a job in the queue."""

    id: int
    job_type: str
    status: str
    input_file: str
    output_file: str
    content_hash: str
    payload: dict[str, Any]
    created_at: datetime
    attempts: int = 0
    priority: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    worker_id: int | None = None
    error: str | None = None
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert job to dictionary."""
        data = asdict(self)
        # Convert datetime objects to ISO format strings
        if self.created_at:
            data["created_at"] = self.created_at.isoformat()
        if self.started_at:
            data["started_at"] = self.started_at.isoformat()
        if self.completed_at:
            data["completed_at"] = self.completed_at.isoformat()
        return data


class JobQueue:
    """Thread-safe job queue manager using SQLite."""

    def __init__(self, db_path: Path):
        """Initialize job queue.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local database connection.

        Returns:
            SQLite connection object
        """
        if not hasattr(self._local, "conn"):
            # Ensure database schema is initialized (defensive programming)
            from clx.infrastructure.database.schema import init_database

            init_database(self.db_path)

            # Create thread-local connection
            # check_same_thread=True (default) is safe because we use threading.local()
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                isolation_level=None,  # Enable autocommit mode for simple operations
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def add_job(
        self,
        job_type: str,
        input_file: str,
        output_file: str,
        content_hash: str,
        payload: dict[str, Any],
        priority: int = 0,
        correlation_id: str | None = None,
    ) -> int:
        """Add a new job to the queue.

        Args:
            job_type: Type of job ('notebook', 'drawio', 'plantuml')
            input_file: Path to input file
            output_file: Path to output file
            content_hash: Hash of input file content
            payload: Job-specific parameters as dictionary
            priority: Job priority (higher = more urgent)
            correlation_id: Optional correlation ID for tracing

        Returns:
            Job ID
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, input_file, output_file,
                content_hash, payload, priority, correlation_id
            ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                job_type,
                input_file,
                output_file,
                content_hash,
                json.dumps(payload),
                priority,
                correlation_id,
            ),
        )
        # No commit() needed - connection is in autocommit mode
        job_id = cursor.lastrowid

        logger.info(
            f"Job #{job_id} submitted: {job_type} for {input_file}"
            + (f" [correlation_id: {correlation_id}]" if correlation_id else "")
        )

        return job_id

    def check_cache(self, output_file: str, content_hash: str) -> dict[str, Any] | None:
        """Check if result exists in cache.

        Args:
            output_file: Output file path
            content_hash: Content hash

        Returns:
            Result metadata if found, None otherwise
        """
        conn = self._get_conn()

        # Use explicit transaction for read-then-write atomicity
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """
                SELECT result_metadata FROM results_cache
                WHERE output_file = ? AND content_hash = ?
                """,
                (output_file, content_hash),
            )
            row = cursor.fetchone()

            if row:
                # Update access statistics
                conn.execute(
                    """
                    UPDATE results_cache
                    SET last_accessed = CURRENT_TIMESTAMP,
                        access_count = access_count + 1
                    WHERE output_file = ? AND content_hash = ?
                    """,
                    (output_file, content_hash),
                )
                conn.commit()
                return json.loads(row[0]) if row[0] else None
            else:
                # Cache miss
                conn.rollback()
                return None
        except Exception:
            conn.rollback()
            raise

    def add_to_cache(self, output_file: str, content_hash: str, result_metadata: dict[str, Any]):
        """Add result to cache.

        Args:
            output_file: Output file path
            content_hash: Content hash
            result_metadata: Metadata about the result
        """
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO results_cache
            (output_file, content_hash, result_metadata)
            VALUES (?, ?, ?)
            """,
            (output_file, content_hash, json.dumps(result_metadata)),
        )

    def get_next_job(self, job_type: str, worker_id: int | None = None) -> Job | None:
        """Get next pending job for the given type.

        This method atomically retrieves and marks a job as processing.

        Args:
            job_type: Type of job to retrieve
            worker_id: Optional worker ID to assign the job to

        Returns:
            Job object if available, None otherwise
        """
        conn = self._get_conn()

        # Use explicit transaction to atomically get and update job
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'pending' AND job_type = ? AND attempts < max_attempts
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                (job_type,),
            )
            row = cursor.fetchone()

            if not row:
                conn.rollback()
                return None

            # Update job status
            conn.execute(
                """
                UPDATE jobs
                SET status = 'processing',
                    started_at = CURRENT_TIMESTAMP,
                    worker_id = ?,
                    attempts = attempts + 1
                WHERE id = ?
                """,
                (worker_id, row["id"]),
            )
            conn.commit()

            job = Job(
                id=row["id"],
                job_type=row["job_type"],
                status="processing",
                input_file=row["input_file"],
                output_file=row["output_file"],
                content_hash=row["content_hash"],
                payload=json.loads(row["payload"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                attempts=row["attempts"] + 1,
                priority=row["priority"],
                worker_id=worker_id,
                correlation_id=row["correlation_id"] if "correlation_id" in row.keys() else None,
            )

            logger.info(
                f"Worker {worker_id} picked up Job #{job.id} [{job.job_type}] for {job.input_file}"
            )

            return job
        except Exception:
            conn.rollback()
            raise

    def update_job_status(self, job_id: int, status: str, error: str | None = None):
        """Update job status.

        Args:
            job_id: Job ID
            status: New status ('pending', 'processing', 'completed', 'failed')
            error: Optional error message
        """
        conn = self._get_conn()

        # Get job info for logging
        job = self.get_job(job_id)

        if status == "completed":
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = CURRENT_TIMESTAMP, error = NULL
                WHERE id = ?
                """,
                (status, job_id),
            )
            # No commit() needed - connection is in autocommit mode

            if job:
                # Calculate duration
                duration = None
                if job.started_at:
                    duration = (datetime.now() - job.started_at).total_seconds()
                duration_str = f" in {duration:.2f}s" if duration else ""
                logger.info(
                    f"Job #{job_id} completed{duration_str} "
                    f"[worker: {job.worker_id}, file: {job.input_file}]"
                )
        elif status == "failed":
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?
                WHERE id = ?
                """,
                (status, error, job_id),
            )
            # No commit() needed - connection is in autocommit mode

            if job:
                logger.error(
                    f"Job #{job_id} FAILED: {error} "
                    f"[worker: {job.worker_id}, file: {job.input_file}]"
                )
        else:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?
                WHERE id = ?
                """,
                (status, error, job_id),
            )
            # No commit() needed - connection is in autocommit mode

    def get_job(self, job_id: int) -> Job | None:
        """Get job by ID.

        Args:
            job_id: Job ID

        Returns:
            Job object if found, None otherwise
        """
        conn = self._get_conn()
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return Job(
            id=row["id"],
            job_type=row["job_type"],
            status=row["status"],
            input_file=row["input_file"],
            output_file=row["output_file"],
            content_hash=row["content_hash"],
            payload=json.loads(row["payload"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            attempts=row["attempts"],
            priority=row["priority"],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None,
            worker_id=row["worker_id"],
            error=row["error"],
            correlation_id=row["correlation_id"] if "correlation_id" in row.keys() else None,
        )

    def get_job_stats(self) -> dict[str, Any]:
        """Get statistics about jobs.

        Returns:
            Dictionary with job counts by status
        """
        conn = self._get_conn()

        stats = {}
        for status in ["pending", "processing", "completed", "failed"]:
            cursor = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = ?", (status,))
            stats[status] = cursor.fetchone()[0]

        return stats

    def get_queue_statistics(self) -> dict[str, Any]:
        """Get detailed statistics about the job queue.

        Returns:
            Dictionary with detailed statistics including counts by type and status
        """
        conn = self._get_conn()

        # Overall counts by status
        stats = self.get_job_stats()

        # Counts by job type
        cursor = conn.execute(
            """
            SELECT job_type, COUNT(*) as count
            FROM jobs
            GROUP BY job_type
            """
        )
        stats["by_type"] = {row[0]: row[1] for row in cursor.fetchall()}

        # Currently processing jobs with details
        cursor = conn.execute(
            """
            SELECT id, job_type, input_file, worker_id,
                   (julianday('now') - julianday(started_at)) * 86400 as elapsed_seconds
            FROM jobs
            WHERE status = 'processing'
            """
        )
        stats["processing_jobs"] = [
            {
                "job_id": row[0],
                "job_type": row[1],
                "input_file": row[2],
                "worker_id": row[3],
                "elapsed_seconds": row[4] or 0,
            }
            for row in cursor.fetchall()
        ]

        return stats

    def get_jobs_by_status(self, status: str, limit: int = 100) -> list[Job]:
        """Get jobs by status.

        Args:
            status: Job status to filter by
            limit: Maximum number of jobs to return

        Returns:
            List of Job objects
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (status, limit),
        )

        jobs = []
        for row in cursor.fetchall():
            jobs.append(
                Job(
                    id=row["id"],
                    job_type=row["job_type"],
                    status=row["status"],
                    input_file=row["input_file"],
                    output_file=row["output_file"],
                    content_hash=row["content_hash"],
                    payload=json.loads(row["payload"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    attempts=row["attempts"],
                    priority=row["priority"],
                    started_at=datetime.fromisoformat(row["started_at"])
                    if row["started_at"]
                    else None,
                    completed_at=datetime.fromisoformat(row["completed_at"])
                    if row["completed_at"]
                    else None,
                    worker_id=row["worker_id"],
                    error=row["error"],
                    correlation_id=row["correlation_id"]
                    if "correlation_id" in row.keys()
                    else None,
                )
            )

        return jobs

    def reset_hung_jobs(self, timeout_seconds: int = 600) -> int:
        """Reset jobs that have been processing for too long.

        Args:
            timeout_seconds: Time in seconds before considering a job hung

        Returns:
            Number of jobs reset
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """
            UPDATE jobs
            SET status = 'pending', worker_id = NULL
            WHERE status = 'processing'
            AND started_at < datetime('now', '-' || ? || ' seconds')
            """,
            (timeout_seconds,),
        )
        # No commit() needed - connection is in autocommit mode
        return cursor.rowcount

    def clear_old_completed_jobs(self, days: int = 7) -> int:
        """Delete old completed jobs.

        Args:
            days: Number of days to keep

        Returns:
            Number of jobs deleted
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """
            DELETE FROM jobs
            WHERE status = 'completed'
            AND completed_at < datetime('now', '-' || ? || ' days')
            """,
            (days,),
        )
        # No commit() needed - connection is in autocommit mode
        return cursor.rowcount

    def close(self):
        """Close database connection."""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            delattr(self._local, "conn")
