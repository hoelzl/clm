"""Status header widget for monitor TUI."""

from rich.text import Text
from textual.widgets import Static

from clm.cli.status.models import StatusInfo, SystemHealth


class StatusHeader(Static):
    """Header showing system status summary."""

    def __init__(self, **kwargs):
        """Initialize header widget."""
        super().__init__(Text("CLX Monitor - Loading...", style="bold"), **kwargs)
        self.status: StatusInfo | None = None

    def update_status(self, status: StatusInfo) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        self.status = status
        self.update(self._render_content())

    def _render_content(self) -> Text:  # type: ignore[override]
        """Render header content."""
        if not self.status:
            return Text("CLX Monitor - Loading...", style="bold")

        # Health indicator
        health_icons = {
            SystemHealth.HEALTHY: "✓",
            SystemHealth.WARNING: "⚠",
            SystemHealth.ERROR: "✗",
        }
        health_icon = health_icons.get(self.status.health, "?")

        health_colors = {
            SystemHealth.HEALTHY: "green",
            SystemHealth.WARNING: "yellow",
            SystemHealth.ERROR: "red",
        }
        health_color = health_colors.get(self.status.health, "white")

        # Calculate worker stats
        total_workers = sum(s.total for s in self.status.workers.values())
        busy_workers = sum(s.busy for s in self.status.workers.values())

        # Build header text
        text = Text()
        text.append(
            f"{health_icon} {self.status.health.value.title()}",
            style=f"bold {health_color}",
        )
        text.append("  |  ", style="dim")

        # Workers summary
        if total_workers > 0:
            text.append(f"{busy_workers}/{total_workers} workers busy", style="cyan")
        else:
            text.append("No workers", style="dim")

        text.append("  |  ", style="dim")

        # Queue summary
        processing = self.status.queue.processing
        pending = self.status.queue.pending
        if processing > 0 or pending > 0:
            text.append(f"{processing} processing", style="blue")
            if pending > 0:
                text.append(f", {pending} pending", style="yellow")
        else:
            text.append("Queue empty", style="dim")

        text.append("  |  ", style="dim")

        # Completed in last hour
        completed = self.status.queue.completed_last_hour
        failed = self.status.queue.failed_last_hour
        if completed > 0 or failed > 0:
            text.append(f"{completed} done", style="green")
            if failed > 0:
                text.append(f", {failed} failed", style="red")
            text.append(" (1h)", style="dim")
        else:
            text.append("No activity (1h)", style="dim")

        return text
