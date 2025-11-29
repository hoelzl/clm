"""Main TUI application for CLX monitoring."""

import logging
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Footer

from clx.cli.monitor.data_provider import DataProvider
from clx.cli.monitor.widgets.activity_panel import ActivityPanel
from clx.cli.monitor.widgets.queue_panel import QueuePanel
from clx.cli.monitor.widgets.status_header import StatusHeader
from clx.cli.monitor.widgets.workers_panel import WorkersPanel

logger = logging.getLogger(__name__)


class CLXMonitorApp(App):
    """CLX Real-Time Monitoring TUI Application."""

    # Inline CSS styling
    CSS = """
    #status-header {
        height: 3;
        background: $panel;
        border: solid $primary;
        padding: 1;
        content-align: center middle;
    }

    #main-content {
        height: 1fr;
    }

    #top-panels {
        height: 40%;
    }

    .panel {
        border: solid $primary;
        background: $panel;
        padding: 1;
    }

    .panel-title {
        color: $accent;
        text-style: bold;
        background: $panel;
        padding-bottom: 1;
    }

    #workers-panel {
        width: 60%;
    }

    #queue-panel {
        width: 40%;
    }

    #activity-panel {
        height: 60%;
    }

    #workers-content, #queue-content {
        height: 1fr;
    }

    RichLog {
        background: $surface;
        border: none;
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "refresh", "Refresh"),
        Binding("p", "pause", "Pause/Resume"),
        Binding("escape", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        db_path: Path | None = None,
        refresh_interval: int = 2,
    ):
        """Initialize monitor application.

        Args:
            db_path: Path to SQLite database
            refresh_interval: Refresh interval in seconds
        """
        super().__init__()
        self.db_path = db_path
        self.refresh_interval = refresh_interval
        self.data_provider = DataProvider(db_path=db_path)
        self.paused = False

        # Widget references
        self.status_header: StatusHeader | None = None
        self.workers_panel: WorkersPanel | None = None
        self.queue_panel: QueuePanel | None = None
        self.activity_panel: ActivityPanel | None = None

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        # Header
        self.status_header = StatusHeader(id="status-header")
        yield self.status_header

        # Main content area
        yield Container(
            Horizontal(
                WorkersPanel(id=WorkersPanel.DEFAULT_ID, classes="panel"),
                QueuePanel(id=QueuePanel.DEFAULT_ID, classes="panel"),
                id="top-panels",
            ),
            ActivityPanel(id=ActivityPanel.DEFAULT_ID, classes="panel"),
            id="main-content",
        )

        # Footer with keyboard shortcuts
        yield Footer()

    def on_mount(self) -> None:
        """Set up refresh timer when app mounts."""
        self.workers_panel = self.query_one(f"#{WorkersPanel.DEFAULT_ID}", WorkersPanel)
        self.queue_panel = self.query_one(f"#{QueuePanel.DEFAULT_ID}", QueuePanel)
        self.activity_panel = self.query_one(f"#{ActivityPanel.DEFAULT_ID}", ActivityPanel)

        # Set app title
        self.title = "CLX Monitor"
        self.sub_title = f"Refresh: {self.refresh_interval}s"

        # Initial data load
        self.refresh_data()

        # Set up periodic refresh
        self.set_interval(self.refresh_interval, self.refresh_data)

    def refresh_data(self) -> None:
        """Refresh data from database and update widgets."""
        if self.paused:
            return

        try:
            # Get fresh status data
            status = self.data_provider.get_status()

            # Get recent events
            events = self.data_provider.get_recent_events(limit=100)

            # Update all widgets
            if self.status_header:
                self.status_header.update_status(status)
            if self.workers_panel:
                self.workers_panel.update_status(status)
            if self.queue_panel:
                self.queue_panel.update_status(status)
            if self.activity_panel:
                self.activity_panel.update_events(events)

        except Exception as e:
            logger.error(f"Error refreshing data: {e}", exc_info=True)
            self.notify(f"Error refreshing data: {e}", severity="error", timeout=5)

    def action_refresh(self) -> None:
        """Handle manual refresh (r key)."""
        self.refresh_data()
        self.notify("Refreshed", timeout=1)

    def action_pause(self) -> None:
        """Handle pause/resume (p key)."""
        self.paused = not self.paused
        if self.paused:
            self.notify("Paused - Press 'p' to resume", severity="warning", timeout=None)
            self.sub_title = "PAUSED"
        else:
            self.notify("Resumed", timeout=1)
            self.sub_title = f"Refresh: {self.refresh_interval}s"
            self.refresh_data()

    def action_quit(self) -> None:  # type: ignore[override]
        """Handle quit action."""
        self.data_provider.close()
        self.exit()
