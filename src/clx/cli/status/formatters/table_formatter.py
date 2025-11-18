"""Table formatter for human-readable output."""

from datetime import datetime

from clx.cli.status.formatter import StatusFormatter
from clx.cli.status.models import StatusInfo, SystemHealth


class TableFormatter(StatusFormatter):
    """Format status as human-readable tables."""

    def __init__(self, use_color: bool = True):
        """Initialize formatter.

        Args:
            use_color: Whether to use colored output (ANSI codes)
        """
        self.use_color = use_color

    def format(
        self, status: StatusInfo, workers_only: bool = False, jobs_only: bool = False
    ) -> str:
        """Format status information as tables."""
        lines = []

        if not workers_only and not jobs_only:
            # Show header
            lines.extend(self._format_header(status))
            lines.append("")

        if not jobs_only:
            # Show workers
            lines.extend(self._format_workers(status))
            lines.append("")

        if not workers_only:
            # Show queue
            lines.extend(self._format_queue(status))
            lines.append("")

        if not workers_only and not jobs_only:
            # Show warnings/errors
            lines.extend(self._format_issues(status))

        return "\n".join(lines)

    def _format_header(self, status: StatusInfo) -> list[str]:
        """Format header with system status."""
        health_icon = {
            SystemHealth.HEALTHY: "✓",
            SystemHealth.WARNING: "⚠",
            SystemHealth.ERROR: "✗",
        }[status.health]

        health_text = f"{health_icon} {status.health.value.title()}"

        if self.use_color:
            health_color = {
                SystemHealth.HEALTHY: "\033[32m",  # Green
                SystemHealth.WARNING: "\033[33m",  # Yellow
                SystemHealth.ERROR: "\033[31m",  # Red
            }[status.health]
            reset = "\033[0m"
            health_text = f"{health_color}{health_text}{reset}"

        # Calculate time since last update
        seconds_ago = (datetime.now() - status.timestamp).total_seconds()
        if seconds_ago < 2:
            update_str = "just now"
        elif seconds_ago < 60:
            update_str = f"{int(seconds_ago)}s ago"
        else:
            minutes = int(seconds_ago / 60)
            update_str = f"{minutes}m ago"

        # Get database size
        if status.database.size_bytes:
            size_kb = status.database.size_bytes / 1024
            db_size = f"({size_kb:.0f} KB)"
        else:
            db_size = ""

        lines = [
            "=" * 70,
            "CLX System Status",
            "=" * 70,
            f"Overall Status: {health_text}",
            f"Database: {status.database.path} {db_size}",
            f"Last Updated: {update_str}",
        ]

        return lines

    def _format_workers(self, status: StatusInfo) -> list[str]:
        """Format workers section."""
        lines = ["Workers by Type"]
        lines.append("-" * 70)

        for worker_type in ["notebook", "plantuml", "drawio"]:
            stats = status.workers.get(worker_type)
            if stats is None:
                continue

            # Worker type header
            mode_str = f" ({stats.execution_mode} mode)" if stats.execution_mode else ""
            lines.append(
                f"{worker_type.title()} Workers: {stats.total} total{mode_str}"
            )

            if stats.total == 0:
                warning = "⚠ No workers registered"
                if self.use_color:
                    warning = f"\033[33m{warning}\033[0m"  # Yellow
                lines.append(f"  {warning}")
                lines.append("")
                continue

            # Status breakdown
            if stats.idle > 0:
                idle_text = f"✓ {stats.idle} idle"
                if self.use_color:
                    idle_text = f"\033[32m{idle_text}\033[0m"  # Green
                lines.append(f"  {idle_text}")

            if stats.busy > 0:
                busy_text = f"⚙ {stats.busy} busy"
                if self.use_color:
                    busy_text = f"\033[34m{busy_text}\033[0m"  # Blue
                lines.append(f"  {busy_text}")

                # Show busy worker details
                for bw in stats.busy_workers:
                    elapsed_str = self._format_elapsed(bw.elapsed_seconds)
                    # Truncate document path if too long
                    doc_path = bw.document_path
                    if len(doc_path) > 50:
                        doc_path = "..." + doc_path[-47:]

                    # Build details list with format/language info
                    details = [elapsed_str]
                    if bw.output_format:
                        details.append(f"format={bw.output_format}")
                    if bw.prog_lang:
                        details.append(f"lang={bw.prog_lang}")
                    if bw.kind:
                        details.append(f"kind={bw.kind}")

                    details_str = ", ".join(details)
                    lines.append(
                        f"     Worker {bw.worker_id[:12]}: {doc_path} ({details_str})"
                    )

            if stats.hung > 0:
                hung_text = f"⚠ {stats.hung} hung"
                if self.use_color:
                    hung_text = f"\033[33m{hung_text}\033[0m"  # Yellow
                lines.append(f"  {hung_text}")

            if stats.dead > 0:
                dead_text = f"✗ {stats.dead} dead"
                if self.use_color:
                    dead_text = f"\033[31m{dead_text}\033[0m"  # Red
                lines.append(f"  {dead_text}")

            lines.append("")

        return lines

    def _format_queue(self, status: StatusInfo) -> list[str]:
        """Format queue statistics."""
        lines = ["Job Queue Status"]
        lines.append("-" * 70)

        queue = status.queue

        # Pending jobs
        pending_str = f"Pending:    {queue.pending} jobs"
        if queue.oldest_pending_seconds:
            oldest_str = self._format_elapsed(queue.oldest_pending_seconds)
            pending_str += f"  (oldest: {oldest_str})"
            if queue.oldest_pending_seconds > 300:  # 5 minutes
                if self.use_color:
                    pending_str = f"\033[33m{pending_str} ⚠\033[0m"  # Yellow
                else:
                    pending_str += " ⚠"

        lines.append(f"  {pending_str}")

        # Processing jobs
        lines.append(f"  Processing: {queue.processing} jobs")

        # Completed jobs
        lines.append(f"  Completed:  {queue.completed_last_hour} jobs (last hour)")

        # Failed jobs
        total_recent = queue.completed_last_hour + queue.failed_last_hour
        failure_str = f"  Failed:     {queue.failed_last_hour} jobs"
        if total_recent > 0:
            failure_rate = queue.failed_last_hour / total_recent
            failure_str += f"  ({failure_rate:.1%} failure rate)"
            if failure_rate > 0.2:
                if self.use_color:
                    failure_str = f"\033[31m{failure_str}\033[0m"  # Red

        lines.append(failure_str)

        # Error statistics (if available)
        if status.error_stats and status.error_stats.total_errors > 0:
            lines.append("")
            lines.extend(self._format_error_stats(status))

        return lines

    def _format_error_stats(self, status: StatusInfo) -> list[str]:
        """Format error statistics by type."""
        lines = []
        error_stats = status.error_stats
        if not error_stats:
            return lines

        lines.append(f"  Error Breakdown (last {error_stats.time_period_hours}h):")

        # Define color mappings and icons
        type_info = {
            "user": ("yellow", "📝"),
            "configuration": ("orange", "⚙️"),
            "infrastructure": ("cyan", "🔧"),
            "unknown": ("red", "❓"),
        }

        # Sort by count (descending)
        sorted_types = sorted(
            error_stats.by_type.items(),
            key=lambda x: x[1].count,
            reverse=True
        )

        for error_type, type_stats in sorted_types:
            # Get color and icon
            color_name, icon = type_info.get(error_type, ("red", "❌"))

            # Format type line
            type_line = f"    {icon} {error_type.capitalize()}: {type_stats.count}"

            if self.use_color:
                color_codes = {
                    "yellow": "\033[33m",
                    "orange": "\033[38;5;208m",
                    "cyan": "\033[36m",
                    "red": "\033[31m",
                }
                color_code = color_codes.get(color_name, "\033[0m")
                type_line = f"{color_code}{type_line}\033[0m"

            lines.append(type_line)

            # Show top categories for this type
            if type_stats.categories:
                sorted_categories = sorted(
                    type_stats.categories.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:3]  # Top 3 categories

                for category, count in sorted_categories:
                    lines.append(f"       • {category}: {count}")

        return lines

    def _format_issues(self, status: StatusInfo) -> list[str]:
        """Format warnings and errors."""
        lines = []

        if status.errors:
            for error in status.errors:
                error_text = f"✗ Error: {error}"
                if self.use_color:
                    error_text = f"\033[31m{error_text}\033[0m"  # Red
                lines.append(error_text)

        if status.warnings:
            for warning in status.warnings:
                warning_text = f"⚠ Warning: {warning}"
                if self.use_color:
                    warning_text = f"\033[33m{warning_text}\033[0m"  # Yellow
                lines.append(warning_text)

        return lines

    def _format_elapsed(self, seconds: int) -> str:
        """Format elapsed time."""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}:{secs:02d}"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}:{minutes:02d}:{seconds % 60:02d}"

    def get_exit_code(self, status: StatusInfo) -> int:
        """Get exit code based on health."""
        return {
            SystemHealth.HEALTHY: 0,
            SystemHealth.WARNING: 1,
            SystemHealth.ERROR: 2,
        }[status.health]
