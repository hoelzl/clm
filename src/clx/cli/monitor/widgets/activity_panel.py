"""Activity panel widget for monitor TUI."""

import json

from textual.app import ComposeResult
from textual.widgets import RichLog, Static

from clx.cli.monitor.data_provider import ActivityEvent
from clx.cli.monitor.formatters import format_elapsed, format_timestamp


class ActivityPanel(Static):
    """Panel showing recent activity log."""

    DEFAULT_ID = "activity-panel"

    def __init__(self, **kwargs):
        """Initialize activity panel."""
        super().__init__(**kwargs)
        self._events_data: list[ActivityEvent] = []

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("Recent Activity", classes="panel-title")
        yield RichLog(id="activity-log", wrap=False, highlight=True, markup=True, max_lines=500)

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
                duration = format_elapsed(event.duration_seconds) if event.duration_seconds else "?"
                log.write(
                    f"{timestamp} [green]✓ Completed[/green]  {event.document_path}  ({duration})"
                )

            elif event.event_type == "job_failed":
                duration = format_elapsed(event.duration_seconds) if event.duration_seconds else "?"

                # Try to parse error as JSON for rich display
                error_display = self._format_error(event.error_message)

                log.write(
                    f"{timestamp} [red]✗ Failed[/red]     {event.document_path}  ({duration})"
                )
                log.write(f"  {error_display}")

    def _format_error(self, error_message: str | None) -> str:
        """Format error message with categorization if available.

        Tries to parse error as JSON and display rich information.
        Falls back to truncated raw string if parsing fails.

        Args:
            error_message: Error message (possibly JSON)

        Returns:
            Formatted error string for display
        """
        if not error_message:
            return "[dim]unknown error[/dim]"

        try:
            # Try to parse as JSON
            error_data = json.loads(error_message)

            # Check if we have categorization fields
            if "error_type" in error_data and "category" in error_data:
                # Color code based on error type
                error_type = error_data.get("error_type", "unknown")
                category = error_data.get("category", "unknown")
                error_data.get("severity", "error")

                # Choose color based on error type
                if error_type == "user":
                    type_color = "yellow"
                    type_icon = "📝"
                elif error_type == "configuration":
                    type_color = "orange1"
                    type_icon = "⚙️"
                elif error_type == "infrastructure":
                    type_color = "cyan"
                    type_icon = "🔧"
                else:
                    type_color = "red"
                    type_icon = "❌"

                # Build formatted message
                parts = [f"[{type_color}]{type_icon} {error_type.capitalize()}[/{type_color}]"]
                parts.append(f"[dim]({category})[/dim]")

                # Add error message (short version)
                error_msg = error_data.get("error_message", "")
                if error_msg:
                    # Truncate to reasonable length
                    msg_short = error_msg[:60] + "..." if len(error_msg) > 60 else error_msg
                    parts.append(f"- {msg_short}")

                # Add actionable guidance if available
                guidance = error_data.get("actionable_guidance", "")
                if guidance:
                    parts.append(f"\n    [green]💡 {guidance}[/green]")

                # Add cell number for notebook errors
                details = error_data.get("details", {})
                if isinstance(details, dict) and "cell_number" in details:
                    parts.append(f"\n    [dim]Cell #{details['cell_number']}[/dim]")

                return " ".join(parts)
            else:
                # JSON but no categorization - show basic info
                error_msg = str(error_data.get("error_message", ""))
                if error_msg:
                    return error_msg[:60] + "..." if len(error_msg) > 60 else error_msg
                return "[dim]error (see logs)[/dim]"

        except (json.JSONDecodeError, TypeError, ValueError):
            # Not JSON or invalid - fall back to raw string (truncated)
            return error_message[:60] + "..." if len(error_message) > 60 else error_message
