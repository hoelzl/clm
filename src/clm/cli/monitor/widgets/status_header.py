"""Status header widget for monitor TUI."""

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from clm.cli.status.models import StatusInfo, SystemHealth


class StatusHeader(Static):
    """Header showing system status summary.

    Renders via a child Static driven from a Rich-markup string. The
    markup-string path renders reliably under ``content-align`` and
    nested ``padding`` declarations where directly storing a styled
    ``Text`` on the parent widget sometimes produced an empty pane.
    """

    def __init__(self, **kwargs):
        """Initialize header widget."""
        super().__init__(**kwargs)
        self.status: StatusInfo | None = None

    def compose(self) -> ComposeResult:
        """Create the child Static that displays the rendered header line."""
        yield Static("[bold]CLM Monitor - Loading...[/bold]", id="status-header-line")

    def update_status(self, status: StatusInfo) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        self.status = status
        line = self.query_one("#status-header-line", Static)
        line.update(self._build_markup())

    def _build_markup(self) -> str:
        """Build the header content as a Rich-markup string."""
        if not self.status:
            return "[bold]CLM Monitor - Loading...[/bold]"
        return self._render_content().markup

    def _render_content(self) -> Text:  # type: ignore[override]
        """Render header content as Rich :class:`Text`.

        Kept on the public surface because unit tests assert against
        ``_render_content().plain``. The :meth:`update_status` path
        consumes the same Text via :attr:`Text.markup` so all callers
        stay in sync.
        """
        if not self.status:
            return Text.from_markup("[bold]CLM Monitor - Loading...[/bold]")

        health_icons = {
            SystemHealth.HEALTHY: "✓",
            SystemHealth.WARNING: "⚠",
            SystemHealth.ERROR: "✗",
        }
        health_colors = {
            SystemHealth.HEALTHY: "green",
            SystemHealth.WARNING: "yellow",
            SystemHealth.ERROR: "red",
        }
        health_icon = health_icons.get(self.status.health, "?")
        health_color = health_colors.get(self.status.health, "white")

        total_workers = sum(s.total for s in self.status.workers.values())
        busy_workers = sum(s.busy for s in self.status.workers.values())

        segments: list[str] = []
        segments.append(f"[bold {health_color}]{health_icon} {self.status.health.value.title()}[/]")

        if total_workers > 0:
            segments.append(f"[cyan]{busy_workers}/{total_workers} workers busy[/cyan]")
        else:
            segments.append("[dim]No workers[/dim]")

        processing = self.status.queue.processing
        pending = self.status.queue.pending
        if processing > 0 or pending > 0:
            queue_bits = [f"[blue]{processing} processing[/blue]"]
            if pending > 0:
                queue_bits.append(f"[yellow]{pending} pending[/yellow]")
            segments.append(", ".join(queue_bits))
        else:
            segments.append("[dim]Queue empty[/dim]")

        completed = self.status.queue.completed_last_hour
        failed = self.status.queue.failed_last_hour
        if completed > 0 or failed > 0:
            last_hour = [f"[green]{completed} done[/green]"]
            if failed > 0:
                last_hour.append(f"[red]{failed} failed[/red]")
            last_hour_text = ", ".join(last_hour) + " [dim](1h)[/dim]"
            segments.append(last_hour_text)
        else:
            segments.append("[dim]No activity (1h)[/dim]")

        summary_line = "  [dim]|[/dim]  ".join(segments)

        # Top line: highlight the build context so the pane stops looking
        # empty when summary stats are all dim.
        spec = getattr(self.status, "current_course_spec", None)
        if spec:
            top_line = f"[bold]Building:[/bold] [magenta]{spec}[/magenta]"
        else:
            top_line = "[bold]CLM Monitor[/bold]"

        return Text.from_markup(f"{top_line}\n{summary_line}")
