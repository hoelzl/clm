"""Activity panel widget for monitor TUI."""

from textual.app import ComposeResult
from textual.widgets import Static, RichLog

from clx.cli.status.models import StatusInfo
from clx.cli.monitor.data_provider import ActivityEvent
from clx.cli.monitor.formatters import format_timestamp, format_elapsed


class ActivityPanel(Static):
    """Panel showing recent activity log."""

    DEFAULT_ID = "activity-panel"

    def __init__(self, **kwargs):
        """Initialize activity panel."""
        super().__init__(**kwargs)
        self.events: list[ActivityEvent] = []

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("Recent Activity", classes="panel-title")
        yield RichLog(id="activity-log", wrap=False, highlight=True, max_lines=500)

    def update_events(self, events: list[ActivityEvent]) -> None:
        """Update with new events.

        Args:
            events: List of activity events (newest first)
        """
        log = self.query_one("#activity-log", RichLog)

        # Clear and repopulate (Textual RichLog handles scrolling)
        log.clear()

        if not events:
            log.write("[dim]No recent activity[/dim]")
            return

        for event in events:
            timestamp = format_timestamp(event.timestamp)

            # Format based on event type
            if event.event_type == "job_started":
                log.write(f"{timestamp} [blue]⚙ Started[/blue]    {event.document_path}")

            elif event.event_type == "job_completed":
                duration = (
                    format_elapsed(event.duration_seconds)
                    if event.duration_seconds
                    else "?"
                )
                log.write(
                    f"{timestamp} [green]✓ Completed[/green]  {event.document_path}  ({duration})"
                )

            elif event.event_type == "job_failed":
                duration = (
                    format_elapsed(event.duration_seconds)
                    if event.duration_seconds
                    else "?"
                )
                error = (
                    event.error_message[:40] if event.error_message else "unknown error"
                )
                log.write(
                    f"{timestamp} [red]✗ Failed[/red]     {event.document_path}  ({duration}) - {error}"
                )
