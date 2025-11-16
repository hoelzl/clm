"""Workers panel widget for monitor TUI."""

from textual.app import ComposeResult
from textual.widgets import Static
from textual.containers import VerticalScroll

from clx.cli.status.models import StatusInfo, WorkerTypeStats
from clx.cli.monitor.formatters import format_elapsed


class WorkersPanel(Static):
    """Panel showing worker status."""

    DEFAULT_ID = "workers-panel"

    def __init__(self, **kwargs):
        """Initialize workers panel."""
        super().__init__(**kwargs)
        self._workers_data: dict[str, WorkerTypeStats] = {}

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("Workers", classes="panel-title")
        yield VerticalScroll(id="workers-content")

    def update_status(self, status: StatusInfo) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        self._workers_data = status.workers
        self._render_workers()

    def _render_workers(self) -> None:
        """Render workers content."""
        content_widget = self.query_one("#workers-content", VerticalScroll)
        content_widget.remove_children()

        if not self._workers_data:
            content_widget.mount(Static("[yellow]⚠ No workers registered[/yellow]"))
            return

        # Render each worker type
        for worker_type in ["notebook", "plantuml", "drawio"]:
            if worker_type not in self._workers_data:
                # Show message for missing worker type
                header = f"[dim]{worker_type.title()}[/dim] (0 workers)"
                content_widget.mount(Static(header))
                content_widget.mount(Static("  [yellow]⚠ No workers registered[/yellow]"))
                content_widget.mount(Static(""))  # Blank line
                continue

            stats = self._workers_data[worker_type]

            # Worker type header
            mode = stats.execution_mode or "unknown"
            header = f"[cyan]{worker_type.title()}[/cyan] ({stats.total} workers, {mode} mode)"
            content_widget.mount(Static(header))

            # Status summary
            status_lines = []
            if stats.idle > 0:
                status_lines.append(f"  [green]✓ {stats.idle} idle[/green]")
            if stats.busy > 0:
                status_lines.append(f"  [blue]⚙ {stats.busy} busy[/blue]")
            if stats.hung > 0:
                status_lines.append(f"  [yellow]⚠ {stats.hung} hung[/yellow]")
            if stats.dead > 0:
                status_lines.append(f"  [red]✗ {stats.dead} dead[/red]")

            for line in status_lines:
                content_widget.mount(Static(line))

            # Show busy worker details
            if stats.busy_workers:
                for worker in stats.busy_workers:
                    elapsed = format_elapsed(worker.elapsed_seconds)
                    doc = worker.document_path
                    # Truncate long document paths
                    if len(doc) > 50:
                        doc = "..." + doc[-47:]

                    detail = f"     {worker.worker_id[:12]}: {doc} ({elapsed})"
                    content_widget.mount(Static(detail))

            content_widget.mount(Static(""))  # Blank line
