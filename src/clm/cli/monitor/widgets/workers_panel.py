"""Workers panel widget for monitor TUI."""

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from clm.cli.monitor.formatters import format_elapsed
from clm.cli.status.models import BusyWorkerInfo, StatusInfo, WorkerTypeStats

# Worker type abbreviations and icons
WORKER_ABBREV = {
    "notebook": "nb",
    "plantuml": "uml",
    "drawio": "dio",
}

# Mode indicators (icons for direct vs docker)
MODE_ICON = {
    "direct": "◆",  # Solid diamond for direct/native
    "docker": "◇",  # Empty diamond for docker/container
    None: "?",
    "unknown": "?",
}


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
                content_widget.mount(Static("  [dim]No workers started[/dim]"))
                content_widget.mount(Static(""))  # Blank line
                continue

            stats = self._workers_data[worker_type]

            # Worker type header with mode icon
            mode_icon = MODE_ICON.get(stats.execution_mode, "?")
            if stats.total > 0:
                header = f"[cyan]{worker_type.title()}[/cyan] {mode_icon} ({stats.total})"
            else:
                header = f"[dim]{worker_type.title()}[/dim] (0)"
            content_widget.mount(Static(header))

            # Status summary - compact format on one line
            if stats.total == 0:
                content_widget.mount(Static("  [dim]No workers started[/dim]"))
            else:
                status_parts = []
                if stats.idle > 0:
                    status_parts.append(f"[green]{stats.idle} idle[/green]")
                if stats.busy > 0:
                    status_parts.append(f"[blue]{stats.busy} busy[/blue]")
                if stats.hung > 0:
                    status_parts.append(f"[yellow]{stats.hung} hung[/yellow]")
                if stats.dead > 0:
                    status_parts.append(f"[red]{stats.dead} dead[/red]")

                if status_parts:
                    content_widget.mount(Static("  " + " | ".join(status_parts)))

            # Show busy worker details - compact format
            if stats.busy_workers:
                for worker in stats.busy_workers:
                    detail = self._format_busy_worker(worker, stats.execution_mode)
                    content_widget.mount(Static(detail))

            content_widget.mount(Static(""))  # Blank line

    def _format_busy_worker(self, worker: BusyWorkerInfo, mode: str | None) -> str:
        """Format a busy worker entry for display.

        Args:
            worker: Busy worker information
            mode: Execution mode (direct/docker)

        Returns:
            Formatted string for display
        """
        elapsed = format_elapsed(worker.elapsed_seconds)

        # Get just the filename from the path
        doc = worker.document_path
        if "/" in doc:
            doc = doc.rsplit("/", 1)[-1]
        if "\\" in doc:
            doc = doc.rsplit("\\", 1)[-1]

        # Remove common extensions for brevity
        for ext in [".ipynb", ".puml", ".drawio"]:
            if doc.endswith(ext):
                doc = doc[: -len(ext)]
                break

        # Truncate if still too long
        if len(doc) > 40:
            doc = doc[:37] + "..."

        # Build compact info string
        info_parts = []
        if worker.output_format:
            info_parts.append(worker.output_format)
        if worker.kind:
            info_parts.append(worker.kind)

        if info_parts:
            info_str = f"[dim]({', '.join(info_parts)})[/dim]"
            return f"    [blue]⚙[/blue] {doc} {info_str} [{elapsed}]"
        else:
            return f"    [blue]⚙[/blue] {doc} [{elapsed}]"
