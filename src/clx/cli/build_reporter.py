"""Build reporting coordinator.

This module provides the BuildReporter class which coordinates build progress
reporting, error collection, and summary generation during course builds.
"""

from datetime import datetime

from clx.cli.build_data_classes import BuildError, BuildSummary, BuildWarning, ProgressUpdate
from clx.cli.output_formatter import OutputFormatter


class BuildReporter:
    """Coordinates build progress reporting and error collection."""

    def __init__(
        self,
        output_formatter: OutputFormatter,
    ):
        """Initialize build reporter.

        Args:
            output_formatter: Formatter for displaying build output
        """
        self.formatter = output_formatter
        self.errors: list[BuildError] = []
        self.warnings: list[BuildWarning] = []
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None
        self.total_files: int = 0
        self.current_stage: str | None = None
        self.stage_num: int = 0
        self.total_stages: int = 0

        # Per-stage progress tracking
        self._stage_num_jobs: int = 0  # Jobs in current stage
        self._global_base_completed: int = 0  # Global completed when stage started
        self._last_global_completed: int = 0  # Track cumulative progress across updates

        # Flag to suppress late error/warning reports after build finishes
        # This prevents spurious errors from being displayed during worker shutdown
        self._build_finished: bool = False

    def start_build(self, course_name: str, total_files: int, total_stages: int = 1) -> None:
        """Initialize build reporting.

        Args:
            course_name: Name of the course being built
            total_files: Total number of files to process
            total_stages: Total number of processing stages
        """
        self.start_time = datetime.now()
        self.total_files = total_files
        self.total_stages = total_stages
        self.errors = []
        self.warnings = []
        self.stage_num = 0

        # Reset per-stage tracking
        self._stage_num_jobs = 0
        self._global_base_completed = 0
        self._last_global_completed = 0

        # Reset build finished flag
        self._build_finished = False

        self.formatter.show_build_start(course_name, total_files)

    def start_stage(self, stage_name: str, num_jobs: int) -> None:
        """Report stage start.

        Args:
            stage_name: Name of the stage (e.g., "Notebooks", "PlantUML Diagrams")
            num_jobs: Number of jobs in this stage
        """
        self.stage_num += 1
        self.current_stage = stage_name

        # Capture per-stage tracking info
        self._stage_num_jobs = num_jobs
        self._global_base_completed = self._last_global_completed

        self.formatter.show_stage_start(stage_name, self.stage_num, self.total_stages, num_jobs)

    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        """Update progress display.

        Args:
            completed: Number of completed jobs
            total: Total number of jobs
            active_workers: Number of active workers
        """
        self.formatter.update_progress(completed, total, active_workers)

    def on_progress_update(self, update: ProgressUpdate) -> None:
        """Handle progress update callback from ProgressTracker.

        Converts global cumulative progress to per-stage progress for display.

        Args:
            update: Progress update event
        """
        # Track global progress for stage baseline calculations
        self._last_global_completed = update.completed

        # Calculate per-stage progress
        stage_completed = update.completed - self._global_base_completed
        # Clamp to valid range [0, _stage_num_jobs]
        stage_completed = max(0, min(stage_completed, self._stage_num_jobs))

        self.update_progress(
            completed=stage_completed,
            total=self._stage_num_jobs,
            active_workers=update.active,
        )

    def report_file_started(self, file_path: str, job_type: str, job_id: int | None = None) -> None:
        """Report that a file has started processing.

        Args:
            file_path: Path to the file being processed
            job_type: Type of job (notebook, plantuml, drawio)
            job_id: Optional job ID for tracking
        """
        self.formatter.show_file_started(file_path, job_type, job_id)

    def report_file_completed(
        self, file_path: str, job_type: str, job_id: int | None = None, success: bool = True
    ) -> None:
        """Report that a file has finished processing.

        Args:
            file_path: Path to the file that was processed
            job_type: Type of job (notebook, plantuml, drawio)
            job_id: Optional job ID for tracking
            success: Whether processing succeeded
        """
        self.formatter.show_file_completed(file_path, job_type, job_id, success)

    def report_error(self, error: BuildError) -> None:
        """Report an error (display if appropriate, always collect).

        Args:
            error: Build error to report
        """
        # Suppress late error reports after build finishes
        # This prevents spurious errors from worker shutdown from being displayed
        if self._build_finished:
            return

        self.errors.append(error)

        # Display error if appropriate for current output mode
        if self.formatter.should_show_error(error):
            self.formatter.show_error(error)

    def report_warning(self, warning: BuildWarning) -> None:
        """Report a warning (display if appropriate, always collect).

        Args:
            warning: Build warning to report
        """
        # Suppress late warning reports after build finishes
        # This prevents spurious warnings from worker shutdown from being displayed
        if self._build_finished:
            return

        self.warnings.append(warning)

        # Display warning if appropriate for current output mode
        if self.formatter.should_show_warning(warning):
            self.formatter.show_warning(warning)

    def _deduplicate_errors(self, errors: list[BuildError]) -> list[BuildError]:
        """Remove duplicate errors based on file_path + category + message prefix.

        When multiple workers process the same file for different output targets,
        they may report the same error multiple times. This deduplicates them
        to avoid cluttering the summary.

        Args:
            errors: List of errors (may contain duplicates)

        Returns:
            List of unique errors
        """
        seen: set[tuple[str, str, str]] = set()
        unique: list[BuildError] = []

        for error in errors:
            # Use first 200 chars of message for fingerprint to handle minor variations
            message_prefix = error.message[:200] if error.message else ""
            fingerprint = (error.file_path or "", error.category, message_prefix)

            if fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(error)

        return unique

    def _deduplicate_warnings(self, warnings: list[BuildWarning]) -> list[BuildWarning]:
        """Remove duplicate warnings based on file_path + category + message prefix.

        When multiple workers process the same file for different output targets,
        they may report the same warning multiple times. This deduplicates them
        to avoid cluttering the summary.

        Args:
            warnings: List of warnings (may contain duplicates)

        Returns:
            List of unique warnings
        """
        seen: set[tuple[str, str, str]] = set()
        unique: list[BuildWarning] = []

        for warning in warnings:
            # Use first 200 chars of message for fingerprint to handle minor variations
            message_prefix = warning.message[:200] if warning.message else ""
            fingerprint = (warning.file_path or "", warning.category, message_prefix)

            if fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(warning)

        return unique

    def finish_build(self) -> BuildSummary:
        """Generate and display final summary.

        Returns:
            Build summary object
        """
        self.end_time = datetime.now()

        # Mark build as finished - this suppresses any late error/warning reports
        # that might come from worker shutdown
        self._build_finished = True

        # Calculate duration
        if self.start_time:
            duration = (self.end_time - self.start_time).total_seconds()
        else:
            duration = 0.0

        # Deduplicate errors and warnings before creating summary
        deduplicated_errors = self._deduplicate_errors(self.errors)
        deduplicated_warnings = self._deduplicate_warnings(self.warnings)

        # Log if deduplication occurred
        if len(deduplicated_errors) < len(self.errors):
            removed = len(self.errors) - len(deduplicated_errors)
            # Import logging here to avoid circular import issues
            import logging

            logging.getLogger(__name__).debug(f"Removed {removed} duplicate error(s) from summary")

        if len(deduplicated_warnings) < len(self.warnings):
            removed = len(self.warnings) - len(deduplicated_warnings)
            import logging

            logging.getLogger(__name__).debug(
                f"Removed {removed} duplicate warning(s) from summary"
            )

        # Create summary with deduplicated errors/warnings
        summary = BuildSummary(
            duration=duration,
            total_files=self.total_files,
            errors=deduplicated_errors,
            warnings=deduplicated_warnings,
            start_time=self.start_time,
            end_time=self.end_time,
        )

        # Display summary
        self.formatter.show_summary(summary)

        return summary

    def cleanup(self) -> None:
        """Clean up reporter resources."""
        self.formatter.cleanup()
