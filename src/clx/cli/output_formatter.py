"""Output formatting for build reporting.

This module provides various output formatters for displaying build progress,
errors, warnings, and summaries in different modes (default, verbose, quiet).
"""

import sys
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
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
    def update_progress(
        self, completed: int, total: int, active_workers: int = 0
    ) -> None:
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
        self.progress: Optional[Progress] = None
        self.current_task: Optional[TaskID] = None
        self._is_started = False

    def show_build_start(self, course_name: str, total_files: int) -> None:
        """Display build initialization."""
        self.console.print(f"\n[bold]Building course:[/bold] {course_name}", style="cyan")
        self.console.print(f"Total files: {total_files}\n")

        if self.show_progress and not self._is_started:
            # Create progress bar
            self.progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
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

    def update_progress(
        self, completed: int, total: int, active_workers: int = 0
    ) -> None:
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

                cell_info = ""
                if "cell_number" in error.details:
                    cell_info = f" (cell #{error.details['cell_number']})"

                self.console.print(
                    f"  [{error_type_color}]{i}. [{error.error_type.title()}][/{error_type_color}] "
                    f"{error.file_path}{cell_info}: {error.message[:80]}"
                )

            if len(summary.errors) > 5:
                self.console.print(f"  ... and {len(summary.errors) - 5} more errors")

            self.console.print("\nRun with [bold]--verbose[/bold] for detailed error information")

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

    def update_progress(
        self, completed: int, total: int, active_workers: int = 0
    ) -> None:
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
