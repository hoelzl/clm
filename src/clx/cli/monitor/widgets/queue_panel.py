"""Queue panel widget for monitor TUI."""

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from clx.cli.monitor.formatters import format_elapsed
from clx.cli.status.models import QueueStats, StatusInfo


class QueuePanel(Static):
    """Panel showing queue statistics."""

    DEFAULT_ID = "queue-panel"

    def __init__(self, **kwargs):
        """Initialize queue panel."""
        super().__init__(**kwargs)
        self.queue: QueueStats | None = None

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("Job Queue", classes="panel-title")
        yield VerticalScroll(id="queue-content")

    def update_status(self, status: StatusInfo) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        self.queue = status.queue
        self._render_queue()

    def _render_queue(self) -> None:
        """Render queue content."""
        content_widget = self.query_one("#queue-content", VerticalScroll)
        content_widget.remove_children()

        if not self.queue:
            content_widget.mount(Static("[dim]No queue data[/dim]"))
            return

        # Pending jobs
        pending_text = f"Pending:    {self.queue.pending} jobs"
        if self.queue.oldest_pending_seconds:
            oldest = format_elapsed(self.queue.oldest_pending_seconds)
            pending_text += f"  (oldest: {oldest})"
            if self.queue.oldest_pending_seconds > 300:  # 5 minutes
                pending_text = f"[yellow]{pending_text} ⚠[/yellow]"

        content_widget.mount(Static(pending_text))

        # Processing jobs
        content_widget.mount(Static(f"Processing: {self.queue.processing} jobs"))

        # Completed jobs
        content_widget.mount(
            Static(f"Completed:  {self.queue.completed_last_hour} jobs (last hour)")
        )

        # Failed jobs with failure rate
        total = self.queue.completed_last_hour + self.queue.failed_last_hour
        failure_text = f"Failed:     {self.queue.failed_last_hour} jobs"
        if total > 0:
            rate = self.queue.failed_last_hour / total
            failure_text += f"  ({rate:.1%})"
            if rate > 0.2:  # > 20% failure rate
                failure_text = f"[red]{failure_text}[/red]"

        content_widget.mount(Static(failure_text))
