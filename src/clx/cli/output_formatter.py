"""Output formatting for build reporting.

This module provides various output formatters for displaying build progress,
errors, warnings, and summaries in different modes (default, verbose, quiet).
"""

import json
import sys
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from clx.cli.build_data_classes import BuildError, BuildSummary, BuildWarning
from clx.cli.text_utils import format_error_path, strip_ansi


class OutputMode(Enum):
    """Output mode for build reporting."""

    DEFAULT = "default"
    VERBOSE = "verbose"
    QUIET = "quiet"
    JSON = "json"


class OutputFormatter(ABC):
    """Abstract base class for output formatting."""

    @abstractmethod
    def show_build_start(self, course_name: str, total_files: int) -> None:
        """Display build initialization.

        Args:
            course_name: Name of the course being built
            total_files: Total number of files to process
        """
        pass

    @abstractmethod
    def show_stage_start(
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int, num_cached: int = 0
    ) -> None:
        """Display stage start.

        Args:
            stage_name: Name of the stage (e.g., "Notebooks", "PlantUML Diagrams")
            stage_num: Current stage number (1-indexed)
            total_stages: Total number of stages
            num_jobs: Total number of jobs in this stage
            num_cached: Number of jobs expected to be served from cache
        """
        pass

    @abstractmethod
    def update_progress(
        self, completed: int, total: int, active_workers: int = 0, cached: int = 0
    ) -> None:
        """Update progress display.

        Args:
            completed: Number of completed worker jobs
            total: Total number of worker jobs
            active_workers: Number of active workers
            cached: Number of cached operations completed
        """
        pass

    @abstractmethod
    def should_show_error(self, error: BuildError) -> bool:
        """Determine if error should be displayed immediately.

        Args:
            error: Build error

        Returns:
            True if error should be shown immediately
        """
        pass

    @abstractmethod
    def show_error(self, error: BuildError) -> None:
        """Display an error.

        Args:
            error: Build error to display
        """
        pass

    @abstractmethod
    def should_show_warning(self, warning: BuildWarning) -> bool:
        """Determine if warning should be displayed immediately.

        Args:
            warning: Build warning

        Returns:
            True if warning should be shown immediately
        """
        pass

    @abstractmethod
    def show_warning(self, warning: BuildWarning) -> None:
        """Display a warning.

        Args:
            warning: Build warning to display
        """
        pass

    @abstractmethod
    def show_summary(self, summary: BuildSummary) -> None:
        """Display final build summary.

        Args:
            summary: Build summary data
        """
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up formatter resources (e.g., stop progress bars)."""
        pass

    def show_file_started(  # noqa: B027
        self, file_path: str, job_type: str, job_id: int | None = None
    ) -> None:
        """Show when a file starts processing (optional, for verbose modes).

        This method is intentionally not abstract - it's an optional hook that
        subclasses may override for verbose output. The default implementation
        is a no-op.

        Args:
            file_path: Path to the file being processed
            job_type: Type of job (notebook, plantuml, drawio)
            job_id: Optional job ID for tracking
        """

    def show_file_completed(  # noqa: B027
        self, file_path: str, job_type: str, job_id: int | None = None, success: bool = True
    ) -> None:
        """Show when a file finishes processing (optional, for verbose modes).

        This method is intentionally not abstract - it's an optional hook that
        subclasses may override for verbose output. The default implementation
        is a no-op.

        Args:
            file_path: Path to the file that was processed
            job_type: Type of job (notebook, plantuml, drawio)
            job_id: Optional job ID for tracking
            success: Whether processing succeeded
        """

    def show_startup_message(self, message: str) -> None:  # noqa: B027
        """Show a startup progress message (optional).

        This method is intentionally not abstract - it's an optional hook that
        subclasses may override to show startup progress. The default
        implementation is a no-op.

        Args:
            message: Progress message to display
        """


class DefaultOutputFormatter(OutputFormatter):
    """Default human-readable output with progress bars."""

    def __init__(self, show_progress: bool = True, use_color: bool = True):
        """Initialize formatter.

        Args:
            show_progress: Whether to show progress bar
            use_color: Whether to use colored output
        """
        self.show_progress = show_progress
        self.use_color = use_color
        self.console = Console(force_terminal=use_color, file=sys.stderr)
        self.progress: Progress | None = None
        self.current_task: TaskID | None = None
        self._is_started = False
        # Cached tracking for progress display
        self._stage_cached = 0
        self._stage_total = 0

    def show_startup_message(self, message: str) -> None:
        """Show a startup progress message."""
        self.console.print(f"[dim]{message}[/dim]")

    def show_build_start(self, course_name: str, total_files: int) -> None:
        """Display build initialization."""
        self.console.print(f"\n[bold]Building course:[/bold] {course_name}", style="cyan")
        self.console.print(f"Total files: {total_files}\n")

        if self.show_progress and not self._is_started:
            # Create progress bar with file counts
            self.progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=self.console,
                transient=False,  # Keep progress bar after completion
            )
            self.progress.start()
            self._is_started = True

    def show_stage_start(
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int, num_cached: int = 0
    ) -> None:
        """Display stage start."""
        description = f"Stage {stage_num}/{total_stages}: {stage_name}"

        # Track cached count for progress display
        self._stage_cached = num_cached
        self._stage_total = num_jobs

        if self.progress:
            if self.current_task is not None:
                # Remove previous task if it exists
                self.progress.remove_task(self.current_task)

            # Add new task for this stage - total is worker jobs (non-cached)
            # The display will show "completed/total (+cached cached)"
            self.current_task = self.progress.add_task(description, total=num_jobs)
        else:
            # No progress bar, just print stage info
            if num_cached > 0:
                self.console.print(
                    f"\nProcessing {description} ({num_jobs} jobs, {num_cached} cached)"
                )
            else:
                self.console.print(f"\nProcessing {description} ({num_jobs} jobs)")

    def update_progress(
        self, completed: int, total: int, active_workers: int = 0, cached: int = 0
    ) -> None:
        """Update progress display."""
        if self.progress and self.current_task is not None and self._is_started:
            # Update progress bar
            self.progress.update(self.current_task, completed=completed + cached, total=total)

            # Update description to show cached count if any
            if cached > 0:
                # Find the task by ID (can't use TaskID as direct list index)
                current_task_obj = None
                for task in self.progress.tasks:
                    if task.id == self.current_task:
                        current_task_obj = task
                        break

                if current_task_obj is not None:
                    stage_info = current_task_obj.description
                    # Extract base stage info (e.g., "Stage 1/4: Processing")
                    base_desc = stage_info.split(" [")[0] if " [" in stage_info else stage_info
                    new_desc = f"{base_desc} [dim]({cached} cached)[/dim]"
                    self.progress.update(self.current_task, description=new_desc)

    def should_show_error(self, error: BuildError) -> bool:
        """Show errors with severity 'error' or 'fatal' immediately."""
        return error.severity in ("error", "fatal")

    def show_error(self, error: BuildError) -> None:
        """Display an error."""
        # Format error type with color
        error_type_str = f"[{error.error_type.title()} Error]"
        if error.error_type == "user":
            color = "yellow"
        elif error.error_type == "configuration":
            color = "magenta"
        else:  # infrastructure
            color = "red"

        self.console.print(f"\n[bold {color}]✗ {error_type_str}[/bold {color}]")

        # Display relative path for readability
        display_path = format_error_path(error.file_path)
        self.console.print(f"  File: {display_path}")

        # Show cell number if available (for notebooks)
        if "cell_number" in error.details:
            cell_info = f"#{error.details['cell_number']}"
            if "line_number" in error.details:
                cell_info += f", line {error.details['line_number']}"
            self.console.print(f"  Cell: {cell_info}")

        # Show error class and message (strip any remaining ANSI sequences)
        if "error_class" in error.details:
            error_class = strip_ansi(str(error.details["error_class"]))
            self.console.print(f"  Error: {error_class}")
            if "short_message" in error.details:
                short_msg = strip_ansi(str(error.details["short_message"]))
                self.console.print(f"  Message: {short_msg}")
        else:
            error_msg = strip_ansi(error.message)
            self.console.print(f"  Error: {error_msg}")

        # Show code snippet if available (strip ANSI sequences)
        if "code_snippet" in error.details and error.details["code_snippet"]:
            self.console.print("\n  Code context:")
            # Display code snippet with syntax highlighting
            from rich.syntax import Syntax

            code_snippet = strip_ansi(str(error.details["code_snippet"]))
            syntax = Syntax(
                code_snippet,
                "python",
                theme="monokai",
                line_numbers=False,
                word_wrap=True,
            )
            self.console.print(syntax, style="dim")
            self.console.print()

        guidance = strip_ansi(error.actionable_guidance)
        self.console.print(f"  [bold]Action:[/bold] {guidance}")

        if error.job_id:
            self.console.print(f"  Job ID: #{error.job_id}", style="dim")

    def should_show_warning(self, warning: BuildWarning) -> bool:
        """Show high-priority warnings immediately."""
        return warning.severity == "high"

    def show_warning(self, warning: BuildWarning) -> None:
        """Display a warning."""
        self.console.print(f"\n[yellow]⚠ {warning}[/yellow]")

    def show_summary(self, summary: BuildSummary) -> None:
        """Display final build summary."""
        # Stop progress bar if running
        if self.progress:
            self.progress.stop()
            self._is_started = False

        # Status symbol and message
        if summary.has_errors():
            status_symbol = "✗"
            status_color = "red"
            status_text = "with errors"
        else:
            status_symbol = "✓"
            status_color = "green"
            status_text = "successfully"

        self.console.print(
            f"\n[bold {status_color}]{status_symbol} Build completed {status_text}[/bold {status_color}] "
            f"in {summary.duration:.1f}s\n"
        )

        # Summary statistics
        self.console.print("[bold]Summary:[/bold]")
        self.console.print(f"  {summary.total_files} files processed")
        self.console.print(f"  [red]{len(summary.errors)} errors[/red]")
        self.console.print(f"  [yellow]{len(summary.warnings)} warnings[/yellow]")

        # Show errors (up to 10)
        max_errors_to_show = 10
        if summary.errors:
            self.console.print("\n[bold]Errors:[/bold]")
            for i, error in enumerate(summary.errors[:max_errors_to_show], 1):
                error_type_color = {
                    "user": "yellow",
                    "configuration": "magenta",
                    "infrastructure": "red",
                }[error.error_type]

                # Use relative path for readability
                display_path = format_error_path(error.file_path)

                # Build compact error info
                location_info = display_path
                if "cell_number" in error.details:
                    location_info += f" (cell #{error.details['cell_number']})"

                # Get the most useful error message (strip ANSI sequences)
                # Prefer actionable_guidance if available and meaningful
                if error.actionable_guidance and len(error.actionable_guidance) > 20:
                    error_msg = strip_ansi(error.actionable_guidance[:120])
                elif "error_class" in error.details:
                    error_msg = strip_ansi(str(error.details["error_class"]))
                    if "short_message" in error.details:
                        short_msg = strip_ansi(str(error.details["short_message"]))
                        error_msg += f": {short_msg[:100]}"
                else:
                    # Extract first meaningful line from message
                    first_line = strip_ansi(error.message.split("\n")[0][:120])
                    error_msg = first_line

                self.console.print(
                    f"  [{error_type_color}]{i}. [{error.error_type.title()}][/{error_type_color}] "
                    f"{location_info}"
                )
                self.console.print(f"     {error_msg}")

            if len(summary.errors) > max_errors_to_show:
                self.console.print(
                    f"  ... and {len(summary.errors) - max_errors_to_show} more errors"
                )

            self.console.print(
                "\nRun with [bold]--output-mode verbose[/bold] for detailed error information"
            )

        # Show warnings (up to 5, with severity color coding)
        max_warnings_to_show = 5
        if summary.warnings:
            self.console.print("\n[bold]Warnings:[/bold]")
            for i, warning in enumerate(summary.warnings[:max_warnings_to_show], 1):
                # Color-code by severity
                if warning.severity == "high":
                    color = "red"
                    severity_label = "[HIGH]"
                elif warning.severity == "medium":
                    color = "yellow"
                    severity_label = ""
                else:  # low
                    color = "dim yellow"
                    severity_label = "[low]"

                # Include file path if available
                location = ""
                if warning.file_path:
                    display_path = format_error_path(warning.file_path)
                    location = f" ({display_path})"

                # Format the message with severity and location
                if severity_label:
                    self.console.print(
                        f"  [{color}]{i}. {severity_label} {warning.message}{location}[/{color}]"
                    )
                else:
                    self.console.print(f"  [{color}]{i}. {warning.message}{location}[/{color}]")

            if len(summary.warnings) > max_warnings_to_show:
                self.console.print(
                    f"  ... and {len(summary.warnings) - max_warnings_to_show} more warnings"
                )

        # Show log file location for detailed debugging
        if summary.errors or summary.warnings:
            from clx.infrastructure.logging.log_paths import get_log_dir

            log_dir = get_log_dir()
            self.console.print(f"\n[dim]Full logs available in: {log_dir}[/dim]")

    def cleanup(self) -> None:
        """Clean up progress bar."""
        if self.progress and self._is_started:
            self.progress.stop()
            self._is_started = False


class VerboseOutputFormatter(DefaultOutputFormatter):
    """Verbose output showing all errors, warnings, and detailed file processing info."""

    def __init__(self, show_progress: bool = True, use_color: bool = True):
        """Initialize verbose formatter.

        Args:
            show_progress: Whether to show progress bar
            use_color: Whether to use colored output
        """
        super().__init__(show_progress=show_progress, use_color=use_color)
        self._files_started: dict[str, float] = {}  # file_path -> start_time
        self._files_completed: int = 0
        self._files_failed: int = 0

    def should_show_error(self, error: BuildError) -> bool:
        """Always show errors in verbose mode."""
        return True

    def should_show_warning(self, warning: BuildWarning) -> bool:
        """Always show warnings in verbose mode."""
        return True

    def show_file_started(self, file_path: str, job_type: str, job_id: int | None = None) -> None:
        """Show when a file starts processing.

        Args:
            file_path: Path to the file being processed
            job_type: Type of job (notebook, plantuml, drawio)
            job_id: Optional job ID for tracking
        """
        import time

        self._files_started[file_path] = time.time()
        display_path = format_error_path(file_path)

        job_info = f" (job #{job_id})" if job_id else ""
        self.console.print(f"  [dim]▶ Processing {job_type}:{job_info} {display_path}[/dim]")

    def show_file_completed(
        self, file_path: str, job_type: str, job_id: int | None = None, success: bool = True
    ) -> None:
        """Show when a file finishes processing.

        Args:
            file_path: Path to the file that was processed
            job_type: Type of job (notebook, plantuml, drawio)
            job_id: Optional job ID for tracking
            success: Whether processing succeeded
        """
        import time

        # Calculate duration if we have start time
        duration_str = ""
        if file_path in self._files_started:
            duration = time.time() - self._files_started[file_path]
            duration_str = f" ({duration:.2f}s)"
            del self._files_started[file_path]

        display_path = format_error_path(file_path)
        job_info = f" (job #{job_id})" if job_id else ""

        if success:
            self._files_completed += 1
            self.console.print(
                f"  [green]✓[/green] [dim]Completed {job_type}:{job_info} "
                f"{display_path}{duration_str}[/dim]"
            )
        else:
            self._files_failed += 1
            self.console.print(
                f"  [red]✗[/red] Failed {job_type}:{job_info} {display_path}{duration_str}"
            )

    def show_stage_start(
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int, num_cached: int = 0
    ) -> None:
        """Display stage start with more details in verbose mode."""
        # Call parent to handle progress bar
        super().show_stage_start(stage_name, stage_num, total_stages, num_jobs, num_cached)

        # Add verbose info about the stage
        self.console.print(
            f"\n[bold cyan]Stage {stage_num}/{total_stages}: {stage_name}[/bold cyan]"
        )
        if num_cached > 0:
            self.console.print(f"  Processing {num_jobs} file(s) ({num_cached} from cache)...\n")
        else:
            self.console.print(f"  Processing {num_jobs} file(s)...\n")

    def show_summary(self, summary: BuildSummary) -> None:
        """Display verbose summary with all errors and warnings."""
        # Stop progress bar if running
        if self.progress:
            self.progress.stop()
            self._is_started = False

        # Status symbol and message
        if summary.has_errors():
            status_symbol = "✗"
            status_color = "red"
            status_text = "with errors"
        else:
            status_symbol = "✓"
            status_color = "green"
            status_text = "successfully"

        self.console.print(
            f"\n[bold {status_color}]{status_symbol} Build completed {status_text}[/bold {status_color}] "
            f"in {summary.duration:.1f}s\n"
        )

        # Summary statistics
        self.console.print("[bold]Summary:[/bold]")
        self.console.print(f"  {summary.total_files} files processed")
        self.console.print(f"  {summary.successful_files} successful")
        self.console.print(f"  [red]{len(summary.errors)} errors[/red]")
        self.console.print(f"  [yellow]{len(summary.warnings)} warnings[/yellow]")

        # Show ALL errors (not just first 5)
        if summary.errors:
            self.console.print(f"\n[bold red]All Errors ({len(summary.errors)}):[/bold red]")
            for i, error in enumerate(summary.errors, 1):
                self._show_error_detail(i, error)

        # Show ALL warnings (not just first 3)
        if summary.warnings:
            self.console.print(
                f"\n[bold yellow]All Warnings ({len(summary.warnings)}):[/bold yellow]"
            )
            for i, warning in enumerate(summary.warnings, 1):
                display_path = format_error_path(warning.file_path) if warning.file_path else ""
                location = f" ({display_path})" if display_path else ""
                self.console.print(
                    f"  [yellow]{i}. [{warning.severity}] {warning.message}{location}[/yellow]"
                )

        # Show log file location for detailed debugging
        if summary.errors or summary.warnings:
            from clx.infrastructure.logging.log_paths import get_log_dir

            log_dir = get_log_dir()
            self.console.print(f"\n[dim]Full logs available in: {log_dir}[/dim]")

    def _show_error_detail(self, index: int, error: BuildError) -> None:
        """Show detailed error information for verbose mode.

        Args:
            index: Error number for display
            error: The error to display
        """
        error_type_color = {
            "user": "yellow",
            "configuration": "magenta",
            "infrastructure": "red",
        }[error.error_type]

        # Use relative path for readability
        display_path = format_error_path(error.file_path)

        # Build location info
        location_info = display_path
        if "cell_number" in error.details:
            location_info += f" (cell #{error.details['cell_number']}"
            if "line_number" in error.details:
                location_info += f", line {error.details['line_number']}"
            location_info += ")"

        self.console.print(
            f"\n  [{error_type_color}]{index}. [{error.error_type.title()} Error - {error.category}][/{error_type_color}]"
        )
        self.console.print(f"     File: {location_info}")

        # Get the most useful error message
        if "error_class" in error.details:
            error_class = strip_ansi(str(error.details["error_class"]))
            self.console.print(f"     Error: {error_class}")
            if "short_message" in error.details:
                short_msg = strip_ansi(str(error.details["short_message"]))
                self.console.print(f"     Message: {short_msg}")
        else:
            error_msg = strip_ansi(error.message)
            # Show full message in verbose mode, not truncated
            self.console.print(f"     Error: {error_msg}")

        # Show code snippet if available
        if "code_snippet" in error.details and error.details["code_snippet"]:
            self.console.print("\n     Code context:")
            from rich.syntax import Syntax

            code_snippet = strip_ansi(str(error.details["code_snippet"]))
            syntax = Syntax(
                code_snippet,
                "python",
                theme="monokai",
                line_numbers=False,
                word_wrap=True,
            )
            self.console.print(syntax, style="dim")

        # Show actionable guidance
        guidance = strip_ansi(error.actionable_guidance)
        self.console.print(f"     [bold]Action:[/bold] {guidance}")

        # Show job ID and correlation ID if available
        if error.job_id:
            self.console.print(f"     Job ID: #{error.job_id}", style="dim")
        if error.correlation_id:
            self.console.print(f"     Correlation ID: {error.correlation_id}", style="dim")


class QuietOutputFormatter(OutputFormatter):
    """Minimal output, only errors and summary."""

    def __init__(self):
        """Initialize quiet formatter."""
        self.console = Console(file=sys.stderr)
        self.errors_shown = 0

    def show_build_start(self, course_name: str, total_files: int) -> None:
        """Silent in quiet mode."""
        pass

    def show_stage_start(
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int, num_cached: int = 0
    ) -> None:
        """Silent in quiet mode."""
        pass

    def update_progress(
        self, completed: int, total: int, active_workers: int = 0, cached: int = 0
    ) -> None:
        """No progress bar in quiet mode."""
        pass

    def should_show_error(self, error: BuildError) -> bool:
        """Show only fatal and error severity."""
        return error.severity in ("error", "fatal")

    def show_error(self, error: BuildError) -> None:
        """Display error briefly."""
        display_path = format_error_path(error.file_path)
        error_msg = strip_ansi(error.message)
        self.console.print(f"ERROR: {display_path}: {error_msg}", style="red")
        self.errors_shown += 1

    def should_show_warning(self, warning: BuildWarning) -> bool:
        """Don't show warnings in quiet mode."""
        return False

    def show_warning(self, warning: BuildWarning) -> None:
        """Silent in quiet mode."""
        pass

    def show_summary(self, summary: BuildSummary) -> None:
        """Display minimal summary."""
        if summary.has_errors():
            self.console.print(
                f"\nBuild failed with {len(summary.errors)} errors in {summary.duration:.1f}s",
                style="red bold",
            )
            # Show log file location
            from clx.infrastructure.logging.log_paths import get_log_dir

            log_dir = get_log_dir()
            self.console.print(f"See logs: {log_dir}", style="dim")
        else:
            self.console.print(
                f"\nBuild completed successfully in {summary.duration:.1f}s",
                style="green bold",
            )

    def cleanup(self) -> None:
        """No cleanup needed for quiet mode."""
        pass


class JSONOutputFormatter(OutputFormatter):
    """Machine-readable JSON output for CI/CD integration."""

    def __init__(self):
        """Initialize JSON formatter."""
        self.console = Console(file=sys.stdout)  # JSON goes to stdout
        self.output_data: dict[str, Any] = {
            "status": "in_progress",
            "errors": [],
            "warnings": [],
            "stages": [],
        }
        self.current_stage: dict[str, Any] | None = None

    def show_build_start(self, course_name: str, total_files: int) -> None:
        """Record build start (silent in JSON mode)."""
        self.output_data["course_name"] = course_name
        self.output_data["total_files"] = total_files

    def show_stage_start(
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int, num_cached: int = 0
    ) -> None:
        """Record stage start (silent in JSON mode)."""
        self.current_stage = {
            "name": stage_name,
            "stage_num": stage_num,
            "total_stages": total_stages,
            "num_jobs": num_jobs,
            "num_cached": num_cached,
            "completed": 0,
            "cached_completed": 0,
        }
        self.output_data["stages"].append(self.current_stage)

    def update_progress(
        self, completed: int, total: int, active_workers: int = 0, cached: int = 0
    ) -> None:
        """Update progress (silent in JSON mode)."""
        if self.current_stage:
            self.current_stage["completed"] = completed
            self.current_stage["total"] = total
            self.current_stage["cached_completed"] = cached

    def should_show_error(self, error: BuildError) -> bool:
        """Never show errors immediately in JSON mode."""
        return False

    def show_error(self, error: BuildError) -> None:
        """Silent in JSON mode (errors collected in summary)."""
        pass

    def should_show_warning(self, warning: BuildWarning) -> bool:
        """Never show warnings immediately in JSON mode."""
        return False

    def show_warning(self, warning: BuildWarning) -> None:
        """Silent in JSON mode (warnings collected in summary)."""
        pass

    def show_summary(self, summary: BuildSummary) -> None:
        """Output final JSON to stdout."""
        # Determine final status
        if summary.has_fatal_errors():
            self.output_data["status"] = "fatal"
        elif summary.has_errors():
            self.output_data["status"] = "failed"
        else:
            self.output_data["status"] = "success"

        # Add summary data
        self.output_data["duration_seconds"] = summary.duration
        self.output_data["files_processed"] = summary.total_files
        self.output_data["files_succeeded"] = summary.successful_files
        self.output_data["files_failed"] = summary.failed_files

        # Convert errors to dicts
        self.output_data["errors"] = [self._error_to_dict(e) for e in summary.errors]
        self.output_data["warnings"] = [self._warning_to_dict(w) for w in summary.warnings]

        # Add counts for convenience
        self.output_data["error_count"] = len(summary.errors)
        self.output_data["warning_count"] = len(summary.warnings)

        # Add timestamps if available
        if summary.start_time:
            self.output_data["start_time"] = summary.start_time.isoformat()
        if summary.end_time:
            self.output_data["end_time"] = summary.end_time.isoformat()

        # Add log file paths for debugging
        from clx.infrastructure.logging.log_paths import get_log_dir, get_worker_log_dir

        self.output_data["log_directory"] = str(get_log_dir())
        self.output_data["worker_log_directory"] = str(get_worker_log_dir())

        # Output JSON to stdout
        print(json.dumps(self.output_data, indent=2))

    def _error_to_dict(self, error: BuildError) -> dict[str, Any]:
        """Convert BuildError to dictionary for JSON serialization."""
        # Strip ANSI sequences from string values
        clean_details = {}
        for k, v in error.details.items():
            if isinstance(v, str):
                clean_details[k] = strip_ansi(v)
            else:
                clean_details[k] = v

        return {
            "error_type": error.error_type,
            "category": error.category,
            "severity": error.severity,
            "file_path": error.file_path,
            "message": strip_ansi(error.message),
            "actionable_guidance": strip_ansi(error.actionable_guidance),
            "job_id": error.job_id,
            "correlation_id": error.correlation_id,
            "details": clean_details,
        }

    def _warning_to_dict(self, warning: BuildWarning) -> dict[str, Any]:
        """Convert BuildWarning to dictionary for JSON serialization."""
        return {
            "category": warning.category,
            "message": strip_ansi(warning.message),
            "severity": warning.severity,
            "file_path": warning.file_path,
        }

    def cleanup(self) -> None:
        """No cleanup needed for JSON mode."""
        pass
