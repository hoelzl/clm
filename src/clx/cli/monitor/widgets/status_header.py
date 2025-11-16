"""Status header widget for monitor TUI."""

from textual.app import ComposeResult
from textual.widgets import Static
from rich.text import Text

from clx.cli.status.models import StatusInfo, SystemHealth
from clx.cli.monitor.formatters import format_size


class StatusHeader(Static):
    """Header showing system status summary."""

    def __init__(self, **kwargs):
        """Initialize header widget."""
        super().__init__(**kwargs)
        self.status: StatusInfo | None = None

    def update_status(self, status: StatusInfo) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        self.status = status
        self.update(self._render_content())

    def _render_content(self) -> Text:
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

        # Format timestamp
        time_str = self.status.timestamp.strftime("%H:%M:%S")

        # Format database size
        db_size_str = "N/A"
        if self.status.database.size_bytes is not None:
            db_size_str = format_size(self.status.database.size_bytes)

        # Build header text
        text = Text()
        text.append("CLX Monitor v0.3.0", style="bold cyan")
        text.append(" | ")
        text.append(
            f"{health_icon} {self.status.health.value.title()}",
            style=f"bold {health_color}",
        )
        text.append(" | ")
        text.append(time_str, style="dim")
        text.append(" | DB: ")
        text.append(db_size_str, style="dim")

        # Add warnings if any
        if self.status.warnings:
            text.append(" | ", style="dim")
            text.append(f"⚠ {len(self.status.warnings)} warning(s)", style="yellow")

        return text
