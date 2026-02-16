"""Activity panel widget for monitor TUI."""

import json

from textual.app import ComposeResult
from textual.widgets import RichLog, Static

from clm.cli.monitor.data_provider import ActivityEvent
from clm.cli.monitor.formatters import format_elapsed, format_timestamp


class ActivityPanel(Static):
    """Panel showing recent activity log."""

    DEFAULT_ID = "activity-panel"

    def __init__(self, **kwargs):
        """Initialize activity panel."""
        super().__init__(**kwargs)
        self._events_data: list[ActivityEvent] = []
        self._seen_event_keys: set[str] = set()

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("Recent Activity", classes="panel-title")
        yield RichLog(id="activity-log", wrap=False, highlight=True, markup=True, max_lines=500)

    @staticmethod
    def _event_key(event: ActivityEvent) -> str:
        """Generate a unique key for an event.

        A job transitioning from 'processing' to 'completed' produces two
        distinct entries in the log, showing the progression of work.
        """
        return f"{event.job_id}:{event.event_type}"

    def update_events(self, events: list[ActivityEvent]) -> None:
        """Update with new events incrementally.

        Only appends events not previously seen. Events are written in
        chronological order (oldest first, newest last) so that RichLog's
        auto-scroll shows the latest activity at the bottom.

        Args:
            events: List of activity events (newest first from data provider)
        """
        log = self.query_one("#activity-log", RichLog)

        if not events:
            if not self._seen_event_keys:
                log.write("[dim]No recent activity[/dim]")
            return

        # If user has scrolled away from the bottom, temporarily disable
        # auto_scroll so we don't yank them back when new events arrive.
        was_at_bottom = log.is_vertical_scroll_end

        # Iterate oldest-first (reverse the DESC-ordered list from DB)
        new_events_written = False
        for event in reversed(events):
            key = self._event_key(event)
            if key in self._seen_event_keys:
                continue
            self._seen_event_keys.add(key)
            log.auto_scroll = was_at_bottom
            self._write_event(log, event)
            new_events_written = True

        if new_events_written:
            log.auto_scroll = True

    def full_refresh_events(self, events: list[ActivityEvent]) -> None:
        """Full clear and repopulate of the activity log.

        Used for manual refresh ('r' key) to reset the view and clear
        stale entries.

        Args:
            events: List of activity events (newest first from data provider)
        """
        log = self.query_one("#activity-log", RichLog)
        log.clear()
        self._seen_event_keys.clear()

        if not events:
            log.write("[dim]No recent activity[/dim]")
            return

        for event in reversed(events):
            key = self._event_key(event)
            self._seen_event_keys.add(key)
            self._write_event(log, event)

    def _write_event(self, log: RichLog, event: ActivityEvent) -> None:
        """Write a single event to the RichLog."""
        timestamp = format_timestamp(event.timestamp)

        if event.event_type == "job_started":
            log.write(f"{timestamp} [blue]⚙ Started[/blue]    {event.document_path}")

        elif event.event_type == "job_completed":
            duration = format_elapsed(event.duration_seconds) if event.duration_seconds else "?"
            log.write(
                f"{timestamp} [green]✓ Completed[/green]  {event.document_path}  ({duration})"
            )

        elif event.event_type == "job_failed":
            duration = format_elapsed(event.duration_seconds) if event.duration_seconds else "?"
            error_display = self._format_error(event.error_message)
            log.write(f"{timestamp} [red]✗ Failed[/red]     {event.document_path}  ({duration})")
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
