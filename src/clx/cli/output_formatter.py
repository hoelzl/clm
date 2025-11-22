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
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int
    ) -> None:
        """Display stage start.

        Args:
            stage_name: Name of the stage (e.g., "Notebooks", "PlantUML Diagrams")
            stage_num: Current stage number (1-indexed)
            total_stages: Total number of stages
            num_jobs: Number of jobs in this stage
        """
        pass

    @abstractmethod
    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        """Update progress display.

        Args:
            completed: Number of completed jobs
            total: Total number of jobs
            active_workers: Number of active workers
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
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int
    ) -> None:
        """Display stage start."""
        description = f"Stage {stage_num}/{total_stages}: {stage_name}"

        if self.progress:
            if self.current_task is not None:
                # Remove previous task if it exists
                self.progress.remove_task(self.current_task)

            # Add new task for this stage
            self.current_task = self.progress.add_task(description, total=num_jobs)
        else:
            # No progress bar, just print stage info
            self.console.print(f"\nProcessing {description} ({num_jobs} jobs)")

    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        """Update progress display."""
        if self.progress and self.current_task is not None:
            self.progress.update(self.current_task, completed=completed, total=total)

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
        self.console.print(f"  File: {error.file_path}")

        # Show cell number if available (for notebooks)
        if "cell_number" in error.details:
            cell_info = f"#{error.details['cell_number']}"
            if "line_number" in error.details:
                cell_info += f", line {error.details['line_number']}"
            self.console.print(f"  Cell: {cell_info}")

        # Show error class and message
        if "error_class" in error.details:
            self.console.print(f"  Error: {error.details['error_class']}")
            if "short_message" in error.details:
                self.console.print(f"  Message: {error.details['short_message']}")
        else:
            self.console.print(f"  Error: {error.message}")

        # Show code snippet if available
        if "code_snippet" in error.details and error.details["code_snippet"]:
            self.console.print("\n  Code context:")
            # Display code snippet with syntax highlighting
            from rich.syntax import Syntax

            syntax = Syntax(
                error.details["code_snippet"],
                "python",
                theme="monokai",
                line_numbers=False,
                word_wrap=True,
            )
            self.console.print(syntax, style="dim")
            self.console.print()

        self.console.print(f"  [bold]Action:[/bold] {error.actionable_guidance}")

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

        # Show errors
        if summary.errors:
            self.console.print("\n[bold]Errors:[/bold]")
            for i, error in enumerate(summary.errors[:5], 1):  # Show first 5
                error_type_color = {
                    "user": "yellow",
                    "configuration": "magenta",
                    "infrastructure": "red",
                }[error.error_type]

                # Extract filename from full path
                from pathlib import Path

                filename = Path(error.file_path).name

                # Build compact error info
                location_info = filename
                if "cell_number" in error.details:
                    location_info += f" (cell #{error.details['cell_number']})"

                # Get the most useful error message
                if "error_class" in error.details:
                    error_msg = error.details["error_class"]
                    if "short_message" in error.details:
                        error_msg += f": {error.details['short_message'][:60]}"
                elif error.actionable_guidance:
                    error_msg = error.actionable_guidance[:80]
                else:
                    # Extract first meaningful line from message
                    first_line = error.message.split("\n")[0][:80]
                    error_msg = first_line

                self.console.print(
                    f"  [{error_type_color}]{i}. [{error.error_type.title()}][/{error_type_color}] "
                    f"{location_info}"
                )
                self.console.print(f"     {error_msg}")

            if len(summary.errors) > 5:
                self.console.print(f"  ... and {len(summary.errors) - 5} more errors")

            self.console.print(
                "\nRun with [bold]--output-mode verbose[/bold] for detailed error information"
            )

        # Show warnings
        if summary.warnings:
            self.console.print("\n[bold]Warnings:[/bold]")
            for i, warning in enumerate(summary.warnings[:3], 1):  # Show first 3
                self.console.print(f"  [yellow]{i}. {warning}[/yellow]")

            if len(summary.warnings) > 3:
                self.console.print(f"  ... and {len(summary.warnings) - 3} more warnings")

    def cleanup(self) -> None:
        """Clean up progress bar."""
        if self.progress and self._is_started:
            self.progress.stop()
            self._is_started = False


class VerboseOutputFormatter(DefaultOutputFormatter):
    """Verbose output showing all errors and warnings immediately."""

    def should_show_error(self, error: BuildError) -> bool:
        """Always show errors in verbose mode."""
        return True

    def should_show_warning(self, warning: BuildWarning) -> bool:
        """Always show warnings in verbose mode."""
        return True


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
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int
    ) -> None:
        """Silent in quiet mode."""
        pass

    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        """No progress bar in quiet mode."""
        pass

    def should_show_error(self, error: BuildError) -> bool:
        """Show only fatal and error severity."""
        return error.severity in ("error", "fatal")

    def show_error(self, error: BuildError) -> None:
        """Display error briefly."""
        self.console.print(f"ERROR: {error.file_path}: {error.message}", style="red")
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
        self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int
    ) -> None:
        """Record stage start (silent in JSON mode)."""
        self.current_stage = {
            "name": stage_name,
            "stage_num": stage_num,
            "total_stages": total_stages,
            "num_jobs": num_jobs,
            "completed": 0,
        }
        self.output_data["stages"].append(self.current_stage)

    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        """Update progress (silent in JSON mode)."""
        if self.current_stage:
            self.current_stage["completed"] = completed
            self.current_stage["total"] = total

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

        # Output JSON to stdout
        print(json.dumps(self.output_data, indent=2))

    def _error_to_dict(self, error: BuildError) -> dict[str, Any]:
        """Convert BuildError to dictionary for JSON serialization."""
        return {
            "error_type": error.error_type,
            "category": error.category,
            "severity": error.severity,
            "file_path": error.file_path,
            "message": error.message,
            "actionable_guidance": error.actionable_guidance,
            "job_id": error.job_id,
            "correlation_id": error.correlation_id,
            "details": error.details,
        }

    def _warning_to_dict(self, warning: BuildWarning) -> dict[str, Any]:
        """Convert BuildWarning to dictionary for JSON serialization."""
        return {
            "category": warning.category,
            "message": warning.message,
            "severity": warning.severity,
            "file_path": warning.file_path,
        }

    def cleanup(self) -> None:
        """No cleanup needed for JSON mode."""
        pass
