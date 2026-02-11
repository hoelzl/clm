"""Compact single-line formatter."""

from clm.cli.status.formatter import StatusFormatter
from clm.cli.status.models import StatusInfo, SystemHealth


class CompactFormatter(StatusFormatter):
    """Format status as compact single line."""

    def format(
        self, status: StatusInfo, workers_only: bool = False, jobs_only: bool = False
    ) -> str:
        """Format status information as compact line."""
        parts = []

        # Health
        parts.append(status.health.value)

        if not jobs_only:
            # Workers
            worker_parts = []
            for worker_type in ["notebook", "plantuml", "drawio"]:
                stats = status.workers.get(worker_type)
                if stats and stats.total > 0:
                    worker_parts.append(
                        f"{stats.total} {worker_type} ({stats.idle} idle, {stats.busy} busy)"
                    )
            if worker_parts:
                parts.append(": " + ", ".join(worker_parts))

        if not workers_only:
            # Queue
            queue_parts = []
            if status.queue.pending > 0:
                queue_parts.append(f"{status.queue.pending} pending")
            if status.queue.processing > 0:
                queue_parts.append(f"{status.queue.processing} processing")
            if queue_parts:
                parts.append(" | queue: " + ", ".join(queue_parts))

        return "".join(parts)

    def get_exit_code(self, status: StatusInfo) -> int:
        """Get exit code based on health."""
        return {
            SystemHealth.HEALTHY: 0,
            SystemHealth.WARNING: 1,
            SystemHealth.ERROR: 2,
        }[status.health]
