"""Data provider for monitor TUI application."""

import logging
import os
from datetime import datetime
from pathlib import Path

from clx.cli.status.collector import StatusCollector
from clx.cli.status.models import StatusInfo
from clx.infrastructure.database.job_queue import JobQueue

logger = logging.getLogger(__name__)


def _find_common_prefix(paths: list[str]) -> str:
    """Find the common directory prefix among a list of paths.

    Args:
        paths: List of file paths

    Returns:
        Common directory prefix (empty string if no common prefix)
    """
    if not paths:
        return ""

    # Filter out empty paths and convert to Path objects
    valid_paths = [p for p in paths if p]
    if not valid_paths:
        return ""

    # Use os.path.commonpath to find common prefix
    try:
        common = os.path.commonpath(valid_paths)
        # Ensure we return a directory path (not partial file name)
        if os.path.isfile(common):
            common = os.path.dirname(common)
        return common
    except (ValueError, TypeError):
        # ValueError if paths are on different drives (Windows)
        # TypeError if paths contain None
        return ""


def _make_relative(path: str, base: str) -> str:
    """Make a path relative to a base directory.

    Args:
        path: The full path
        base: The base directory to make relative to

    Returns:
        Relative path if under base, otherwise original path
    """
    if not path or not base:
        return path

    try:
        # Try to make path relative to base
        rel_path = os.path.relpath(path, base)
        # If result starts with "..", path is not under base
        if rel_path.startswith(".."):
            return path
        return rel_path
    except ValueError:
        # Different drives on Windows
        return path


class ActivityEvent:
    """Activity log event for display."""

    def __init__(
        self,
        timestamp: datetime,
        event_type: str,  # job_started, job_completed, job_failed, worker_assigned
        job_id: str | None = None,
        worker_id: str | None = None,
        document_path: str | None = None,
        duration_seconds: int | None = None,
        error_message: str | None = None,
    ):
        """Initialize activity event."""
        self.timestamp = timestamp
        self.event_type = event_type
        self.job_id = job_id
        self.worker_id = worker_id
        self.document_path = document_path
        self.duration_seconds = duration_seconds
        self.error_message = error_message


class DataProvider:
    """Provide data for monitor UI."""

    def __init__(self, db_path: Path | None = None):
        """Initialize data provider.

        Args:
            db_path: Path to SQLite database
        """
        self.status_collector = StatusCollector(db_path=db_path)
        self.db_path = self.status_collector.db_path
        self.job_queue: JobQueue | None = None

    def get_status(self) -> StatusInfo:
        """Get complete system status.

        Returns:
            StatusInfo with all current data
        """
        return self.status_collector.collect()

    def get_recent_events(self, limit: int = 100) -> list[ActivityEvent]:
        """Get recent activity events.

        Args:
            limit: Maximum number of events to return

        Returns:
            List of recent activity events (with paths made relative)
        """
        raw_events = []

        try:
            # Initialize job queue if needed
            if self.job_queue is None:
                if not self.db_path.exists():
                    return []
                self.job_queue = JobQueue(self.db_path)

            conn = self.job_queue._get_conn()

            # Query job events (started, completed, failed)
            # We'll use the jobs table to derive events
            cursor = conn.execute(
                """
                SELECT
                    j.id,
                    j.status,
                    j.input_file,
                    j.created_at,
                    j.started_at,
                    j.completed_at,
                    w.container_id,
                    CAST((julianday(j.completed_at) - julianday(j.started_at)) * 86400 AS INTEGER) as duration,
                    j.error
                FROM jobs j
                LEFT JOIN workers w ON w.id = j.worker_id
                WHERE j.status IN ('processing', 'completed', 'failed')
                ORDER BY
                    COALESCE(j.completed_at, j.started_at, j.created_at) DESC
                LIMIT ?
                """,
                (limit,),
            )

            for row in cursor.fetchall():
                job_id = row[0]
                status = row[1]
                document_path = row[2]
                row[3]
                started_at = row[4]
                completed_at = row[5]
                worker_id = row[6]
                duration = row[7]
                error_message = row[8]

                # Determine event type and timestamp
                if status == "processing" and started_at:
                    event_type = "job_started"
                    timestamp = datetime.fromisoformat(started_at)
                elif status == "completed" and completed_at:
                    event_type = "job_completed"
                    timestamp = datetime.fromisoformat(completed_at)
                elif status == "failed" and completed_at:
                    event_type = "job_failed"
                    timestamp = datetime.fromisoformat(completed_at)
                else:
                    # Skip if we can't determine proper event
                    continue

                raw_events.append(
                    {
                        "timestamp": timestamp,
                        "event_type": event_type,
                        "job_id": job_id,
                        "worker_id": worker_id,
                        "document_path": document_path,
                        "duration_seconds": duration,
                        "error_message": error_message,
                    }
                )

            # Find common prefix for all document paths to make them relative
            all_paths = [e["document_path"] for e in raw_events if e["document_path"]]
            common_base = _find_common_prefix(all_paths)

            # Create events with relative paths
            events = []
            for raw in raw_events:
                rel_path = (
                    _make_relative(raw["document_path"], common_base)
                    if raw["document_path"]
                    else None
                )
                events.append(
                    ActivityEvent(
                        timestamp=raw["timestamp"],
                        event_type=raw["event_type"],
                        job_id=raw["job_id"],
                        worker_id=raw["worker_id"],
                        document_path=rel_path,
                        duration_seconds=raw["duration_seconds"],
                        error_message=raw["error_message"],
                    )
                )

            return events

        except Exception as e:
            logger.error(f"Error getting recent events: {e}", exc_info=True)
            return []

    def close(self):
        """Close database connections."""
        if self.job_queue:
            self.job_queue.close()
            self.job_queue = None
