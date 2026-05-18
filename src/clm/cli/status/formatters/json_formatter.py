"""JSON formatter for machine-readable output."""

import json
from typing import Any

from clm.cli.status.formatter import StatusFormatter
from clm.cli.status.models import StatusInfo, SystemHealth


class JsonFormatter(StatusFormatter):
    """Format status as JSON."""

    def __init__(self, pretty: bool = True):
        """Initialize formatter.

        Args:
            pretty: Whether to pretty-print JSON
        """
        self.pretty = pretty

    def format(
        self, status: StatusInfo, workers_only: bool = False, jobs_only: bool = False
    ) -> str:
        """Format status information as JSON."""
        data: dict[str, Any] = {
            "status": status.health.value,
            "timestamp": status.timestamp.isoformat(),
        }

        if not jobs_only:
            # Database info
            data["database"] = {
                "path": status.database.path,
                "accessible": status.database.accessible,
                "exists": status.database.exists,
            }
            if status.database.size_bytes is not None:
                data["database"]["size_bytes"] = status.database.size_bytes
            if status.database.last_modified:
                data["database"]["last_modified"] = status.database.last_modified.isoformat()
            if status.database.error_message:
                data["database"]["error_message"] = status.database.error_message

        if not jobs_only:
            # Workers
            data["workers"] = {}
            for worker_type, stats in status.workers.items():
                worker_data: dict[str, Any] = {
                    "total": stats.total,
                    "idle": stats.idle,
                    "busy": stats.busy,
                    "hung": stats.hung,
                    "dead": stats.dead,
                }

                if stats.execution_mode:
                    worker_data["execution_mode"] = stats.execution_mode

                if stats.busy_workers:
                    busy_list: list[dict[str, Any]] = []
                    for bw in stats.busy_workers:
                        entry: dict[str, Any] = {
                            "worker_id": bw.worker_id,
                            "job_id": bw.job_id,
                            "document": bw.document_path,
                            "elapsed_seconds": bw.elapsed_seconds,
                        }
                        # Include per-cell visibility when published by the
                        # worker. Each field is opt-in so consumers can
                        # tell "field absent" from "field present but None".
                        if bw.current_cell is not None:
                            entry["current_cell"] = bw.current_cell
                        if bw.total_cells is not None:
                            entry["total_cells"] = bw.total_cells
                        if bw.cell_elapsed_seconds is not None:
                            entry["cell_elapsed_seconds"] = bw.cell_elapsed_seconds
                        if bw.since_last_output_seconds is not None:
                            entry["since_last_output_seconds"] = bw.since_last_output_seconds
                        if bw.last_output_excerpt is not None:
                            entry["last_output_excerpt"] = bw.last_output_excerpt
                        busy_list.append(entry)
                    worker_data["busy_workers"] = busy_list

                data["workers"][worker_type] = worker_data

        if not workers_only:
            # Queue
            data["queue"] = {
                "pending": status.queue.pending,
                "processing": status.queue.processing,
                "completed_last_hour": status.queue.completed_last_hour,
                "failed_last_hour": status.queue.failed_last_hour,
            }
            if status.queue.oldest_pending_seconds is not None:
                data["queue"]["oldest_pending_seconds"] = status.queue.oldest_pending_seconds

        # Issues
        if status.warnings:
            data["warnings"] = status.warnings
        if status.errors:
            data["errors"] = status.errors

        if self.pretty:
            return json.dumps(data, indent=2)
        else:
            return json.dumps(data)

    def get_exit_code(self, status: StatusInfo) -> int:
        """Get exit code based on health."""
        return {
            SystemHealth.HEALTHY: 0,
            SystemHealth.WARNING: 1,
            SystemHealth.ERROR: 2,
        }[status.health]
