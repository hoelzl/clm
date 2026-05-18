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
                    # Render a second line per busy worker showing per-cell
                    # heartbeat info when available (notebook workers in
                    # direct SQLite mode publish this; PlantUML/DrawIO
                    # workers do not, so the line is silently skipped).
                    cell_line = self._format_cell_heartbeat(worker)
                    if cell_line is not None:
                        content_widget.mount(Static(cell_line))

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
        if worker.kind:
            info_parts.append(worker.kind)
        if worker.output_format:
            info_parts.append(worker.output_format)
        if worker.language:
            info_parts.append(worker.language)

        if info_parts:
            info_str = f"[dim]({', '.join(info_parts)})[/dim]"
            return f"    [blue]⚙[/blue] {doc} {info_str} [{elapsed}]"
        else:
            return f"    [blue]⚙[/blue] {doc} [{elapsed}]"

    @staticmethod
    def _format_cell_heartbeat(worker: BusyWorkerInfo) -> str | None:
        """Render per-cell visibility for a notebook worker, or None.

        Returns a pre-formatted markup string when at least one heartbeat
        field is populated; returns ``None`` for workers with no heartbeat
        row (non-notebook workers, or workers that haven't started a cell
        yet). Format:

            cell N/M  in-cell <duration>  idle <duration>  last: <excerpt>

        Each segment is omitted individually when its source field is
        missing so the line stays compact for partial data.
        """
        if (
            worker.current_cell is None
            and worker.cell_elapsed_seconds is None
            and worker.last_output_excerpt is None
            and worker.since_last_output_seconds is None
        ):
            return None

        segments: list[str] = []
        if worker.current_cell is not None:
            if worker.total_cells is not None:
                segments.append(f"cell {worker.current_cell + 1}/{worker.total_cells}")
            else:
                segments.append(f"cell {worker.current_cell + 1}")
        if worker.cell_elapsed_seconds is not None:
            segments.append(f"in-cell {format_elapsed(worker.cell_elapsed_seconds)}")
        if worker.since_last_output_seconds is not None:
            segments.append(f"idle {format_elapsed(worker.since_last_output_seconds)}")

        if worker.last_output_excerpt:
            # Truncate display further for the TUI row; the store already
            # capped it at 120 chars but the panel is narrower.
            excerpt = worker.last_output_excerpt
            if len(excerpt) > 60:
                excerpt = excerpt[:57] + "..."
            segments.append(f"last: {excerpt}")
        elif worker.current_cell is not None:
            segments.append("last: [dim]<no output>[/dim]")

        return "        [dim]" + "  ".join(segments) + "[/dim]"
