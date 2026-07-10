"""Build reporting coordinator.

This module provides the BuildReporter class which coordinates build progress
reporting, error collection, and summary generation during course builds.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from clm.cli.build_data_classes import (
    BuildError,
    BuildSummary,
    BuildWarning,
    FlakyFileInfo,
    OutputConflictInfo,
    ProgressUpdate,
)
from clm.cli.output_formatter import OutputFormatter

if TYPE_CHECKING:
    from clm.core.output_write_registry import OutputWriteRegistry


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

        # Cached operations tracking (operations served from cache without worker execution)
        self._stage_cached_count: int = 0  # Cached ops in current stage
        self._global_cached_count: int = 0  # Total cached ops across all stages

        # Flag to suppress late error/warning reports after build finishes
        # This prevents spurious errors from being displayed during worker shutdown
        self._build_finished: bool = False

        # Fingerprints of errors/warnings already shown in the live stream.
        # A source slide is processed once per output target and language, so
        # the same finding (e.g. a dropped voiceover narration) is reported
        # several times; we display each unique message once as it occurs and
        # let the final summary report the aggregate count.
        self._shown_error_fingerprints: set[tuple[str, str, str]] = set()
        self._shown_warning_fingerprints: set[tuple[str, str, str]] = set()

        # Set when a worker-job timeout aborts the build (issue #143). Carried
        # into BuildSummary.timed_out so the entry point can force a non-zero
        # exit independent of the --fail-on-error policy.
        self._timed_out: bool = False

        # Set when stage processing raised and the exception is propagating
        # past the summary (issue #596). Carried into BuildSummary.aborted so
        # renderers show a failure headline instead of "completed successfully".
        self._aborted: bool = False

        # Output-write registry summary, populated by
        # :meth:`report_output_writes` shortly before ``finish_build``.
        self._output_dedup_count: int = 0
        self._output_conflicts: list[OutputConflictInfo] = []
        self._output_large_file_collision_count: int = 0

        # Decks that passed only after a retry (issue #330), aggregated per
        # source file via :meth:`report_flaky_file`.
        self._flaky_files: dict[str, FlakyFileInfo] = {}

        # Cache-miss reason counts under ``--explain-rebuilds`` (issue: many
        # decks rebuilding whose sources should not change), keyed by reason
        # code. Stays empty unless :meth:`report_rebuild_reason` is called,
        # which only happens when the flag is on.
        self._rebuild_reasons: dict[str, int] = {}

    def start_build(
        self,
        course_name: str,
        total_files: int,
        total_stages: int = 1,
        output_dirs: list[str] | None = None,
    ) -> None:
        """Initialize build reporting.

        Args:
            course_name: Name of the course being built
            total_files: Total number of files to process
            total_stages: Total number of processing stages
            output_dirs: List of output directory names
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

        # Reset cached tracking
        self._stage_cached_count = 0
        self._global_cached_count = 0

        # Reset build finished flag
        self._build_finished = False

        # Reset live-stream dedup tracking
        self._shown_error_fingerprints = set()
        self._shown_warning_fingerprints = set()

        # Reset output-write registry summary
        self._output_dedup_count = 0
        self._output_conflicts = []
        self._output_large_file_collision_count = 0

        # Reset flake tracking
        self._flaky_files = {}

        # Reset rebuild-reason tracking
        self._rebuild_reasons = {}

        self.formatter.show_build_start(course_name, total_files, output_dirs)

    def start_stage(self, stage_name: str, num_jobs: int, num_cached: int = 0) -> None:
        """Report stage start.

        Args:
            stage_name: Name of the stage (e.g., "Notebooks", "PlantUML Diagrams")
            num_jobs: Total number of jobs in this stage (including cached)
            num_cached: Number of jobs that will be served from cache
        """
        self.stage_num += 1
        self.current_stage = stage_name

        # Capture per-stage tracking info
        self._stage_num_jobs = num_jobs
        self._stage_cached_count = num_cached
        self._global_base_completed = self._last_global_completed

        self.formatter.show_stage_start(
            stage_name, self.stage_num, self.total_stages, num_jobs, num_cached
        )

    def update_progress(
        self, completed: int, total: int, active_workers: int = 0, cached: int = 0
    ) -> None:
        """Update progress display.

        Args:
            completed: Number of completed jobs (worker jobs only, not cached)
            total: Total number of jobs (worker jobs only, not cached)
            active_workers: Number of active workers
            cached: Number of cached operations completed
        """
        self.formatter.update_progress(completed, total, active_workers, cached)

    def on_progress_update(self, update: ProgressUpdate) -> None:
        """Handle progress update callback from ProgressTracker.

        Converts global cumulative progress to per-stage progress for display.

        Args:
            update: Progress update event
        """
        # Track global progress for stage baseline calculations
        self._last_global_completed = update.completed

        # Calculate per-stage progress (worker jobs only)
        stage_completed = update.completed - self._global_base_completed
        # Clamp to valid range [0, _stage_num_jobs]
        stage_completed = max(0, min(stage_completed, self._stage_num_jobs))

        # Include cached count in progress update
        self.update_progress(
            completed=stage_completed,
            total=self._stage_num_jobs,
            active_workers=update.active,
            cached=self._stage_cached_count,
        )

    def report_cache_hit(self, file_path: str, job_type: str, detail: str | None = None) -> None:
        """Report that a file was served from cache without worker execution.

        This method should be called when a cache hit occurs and the file
        is processed without submitting a job to a worker.

        Args:
            file_path: Path to the cached file
            job_type: Type of job (notebook, plantuml, drawio)
            detail: Optional cache-layer description shown in verbose mode
                (issue #321: replayed output is freshly timestamped and
                otherwise indistinguishable from executed output)
        """
        self._stage_cached_count += 1
        self._global_cached_count += 1

        # Per-file replay line in verbose modes (no-op in other formatters)
        self.formatter.show_cache_hit(file_path, job_type, detail)

        # Trigger a progress update to reflect the cache hit
        self.update_progress(
            completed=self._last_global_completed - self._global_base_completed,
            total=self._stage_num_jobs,
            active_workers=0,  # Cache hits don't involve active workers
            cached=self._stage_cached_count,
        )

    def report_rebuild_reason(
        self, file_path: str, job_type: str, reason: str, reason_code: str
    ) -> None:
        """Explain why a file is being rebuilt instead of served from cache.

        Only called under ``clm build --explain-rebuilds``. Purely
        informational: it does not touch the progress/cache counters (the
        normal :meth:`report_file_started` path still tracks the submitted
        job). Records the ``reason_code`` for the aggregated per-reason
        breakdown in the final :class:`BuildSummary`, and surfaces the
        human-readable ``reason`` on the console in verbose output modes (the
        backend also writes it to the log file).

        Args:
            file_path: Path to the source file being rebuilt
            job_type: Type of job (notebook, plantuml, drawio)
            reason: Human-readable explanation of the cache miss
            reason_code: Machine reason code for aggregation (``hash_mismatch``
                / ``no_entry`` / ``metadata_mismatch``)
        """
        self._rebuild_reasons[reason_code] = self._rebuild_reasons.get(reason_code, 0) + 1
        self.formatter.show_rebuild_reason(file_path, job_type, reason)

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

        # Display each unique error once. The same source slide is processed
        # once per output target and language, so an identical finding is
        # reported several times; showing every copy floods the stream (the
        # full count is reported in the summary).
        fingerprint = self._error_fingerprint(error)
        if fingerprint in self._shown_error_fingerprints:
            return
        self._shown_error_fingerprints.add(fingerprint)

        # Display error if appropriate for current output mode
        if self.formatter.should_show_error(error):
            self.formatter.show_error(error)

    def mark_timed_out(self) -> None:
        """Flag the build as aborted by a worker-job timeout (issue #143).

        Recorded on the :class:`BuildSummary` so the entry point exits
        non-zero even when ``--fail-on-error`` is off — a build with stuck
        jobs produced an incomplete output tree and must not look successful.
        """
        self._timed_out = True

    def mark_aborted(self, exc: BaseException) -> None:
        """Flag the build as aborted by a propagating exception (issue #596).

        Called from the stage-processing exception handler *before* the
        exception is re-raised, so the summary rendered by the ``finally``
        block reports a failure instead of "Build completed successfully".

        Also records the exception as a fatal infrastructure error: it then
        appears in the summary's error count (submission-time failures
        previously never did, unlike execution failures) and — because the
        stale-output sweep skips itself when errors were recorded — keeps an
        aborted build's incomplete write registry from sweeping away valid
        outputs of prior successful builds.
        """
        self._aborted = True
        messages = [f"{type(exc).__name__}: {exc}"]
        # ``BaseExceptionGroup`` is a builtin on the supported runtimes but
        # ruff's py310 target flags the bare name (same workaround as
        # ``_contains_jobs_pending_timeout`` in the build command).
        import builtins

        group_type = getattr(builtins, "BaseExceptionGroup", None)
        if group_type is not None and isinstance(exc, group_type):
            messages.extend(f"{type(sub).__name__}: {sub}" for sub in exc.exceptions[:5])
        self.errors.append(
            BuildError(
                error_type="infrastructure",
                category="build_aborted",
                severity="fatal",
                file_path="",
                message="\n".join(messages),
                actionable_guidance=(
                    "The build was aborted before all stages completed; the "
                    "output tree is incomplete. See the traceback below for "
                    "the underlying failure."
                ),
            )
        )

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

        # Display each unique warning once (see :meth:`report_error`).
        fingerprint = self._warning_fingerprint(warning)
        if fingerprint in self._shown_warning_fingerprints:
            return
        self._shown_warning_fingerprints.add(fingerprint)

        # Display warning if appropriate for current output mode
        if self.formatter.should_show_warning(warning):
            self.formatter.show_warning(warning)

    def report_flaky_file(
        self,
        file_path: str,
        attempts: int,
        failure_types: list[str] | None = None,
        language: str = "",
    ) -> None:
        """Record a deck that passed only after a retry (issue #330).

        Aggregated per source file: a bilingual deck flaking in both
        languages becomes one entry with both languages listed. The list is
        carried into :class:`BuildSummary` as ``flaky_files``.

        Args:
            file_path: Source path of the deck.
            attempts: Number of attempts the passing execution needed.
            failure_types: Failure types of the preceding failed attempts.
            language: Output language of the flaky execution.
        """
        if self._build_finished:
            return
        entry = self._flaky_files.get(file_path)
        if entry is None:
            entry = FlakyFileInfo(file_path=file_path, max_attempts=attempts, flake_count=0)
            self._flaky_files[file_path] = entry
        entry.max_attempts = max(entry.max_attempts, attempts)
        entry.flake_count += 1
        for failure_type in failure_types or []:
            if failure_type and failure_type not in entry.failure_types:
                entry.failure_types.append(failure_type)
        if language and language not in entry.languages:
            entry.languages.append(language)

    @staticmethod
    def _error_fingerprint(error: BuildError) -> tuple[str, str, str]:
        """Identity used to collapse duplicate errors (file + category + message).

        The message is truncated to its first 200 characters so minor tail
        variations (e.g. a trailing job id) still fold together.
        """
        message_prefix = error.message[:200] if error.message else ""
        return (error.file_path or "", error.category, message_prefix)

    @staticmethod
    def _warning_fingerprint(warning: BuildWarning) -> tuple[str, str, str]:
        """Identity used to collapse duplicate warnings (see _error_fingerprint)."""
        message_prefix = warning.message[:200] if warning.message else ""
        return (warning.file_path or "", warning.category, message_prefix)

    def _deduplicate_errors(self, errors: list[BuildError]) -> list[BuildError]:
        """Collapse duplicate errors, recording how often each one occurred.

        When the same file is processed for several output targets, the same
        error is reported multiple times. Each unique error is kept once with
        its ``occurrence_count`` set to the number of times it was reported.

        Args:
            errors: List of errors (may contain duplicates)

        Returns:
            List of unique errors, each with ``occurrence_count`` populated
        """
        unique: list[BuildError] = []
        by_fingerprint: dict[tuple[str, str, str], BuildError] = {}

        for error in errors:
            fingerprint = self._error_fingerprint(error)
            representative = by_fingerprint.get(fingerprint)
            if representative is None:
                error.occurrence_count = 1
                by_fingerprint[fingerprint] = error
                unique.append(error)
            else:
                representative.occurrence_count += 1

        return unique

    def _deduplicate_warnings(self, warnings: list[BuildWarning]) -> list[BuildWarning]:
        """Collapse duplicate warnings, recording how often each one occurred.

        See :meth:`_deduplicate_errors`; warnings are aggregated the same way.

        Args:
            warnings: List of warnings (may contain duplicates)

        Returns:
            List of unique warnings, each with ``occurrence_count`` populated
        """
        unique: list[BuildWarning] = []
        by_fingerprint: dict[tuple[str, str, str], BuildWarning] = {}

        for warning in warnings:
            fingerprint = self._warning_fingerprint(warning)
            representative = by_fingerprint.get(fingerprint)
            if representative is None:
                warning.occurrence_count = 1
                by_fingerprint[fingerprint] = warning
                unique.append(warning)
            else:
                representative.occurrence_count += 1

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
            output_dedup_count=self._output_dedup_count,
            output_conflicts=list(self._output_conflicts),
            output_large_file_collision_count=self._output_large_file_collision_count,
            flaky_files=sorted(self._flaky_files.values(), key=lambda f: f.file_path),
            rebuild_reasons=dict(self._rebuild_reasons),
            timed_out=self._timed_out,
            aborted=self._aborted,
        )

        # Display summary
        self.formatter.show_summary(summary)

        return summary

    def report_output_writes(self, registry: "OutputWriteRegistry") -> None:
        """Drain an :class:`OutputWriteRegistry` into the build summary.

        Builds one :class:`BuildWarning` per conflict (so it appears in the
        deduplicated warning list and in any per-warning display channel),
        and stores the aggregate counts + structured conflict list for
        the final :class:`BuildSummary`. Call this once, after the last
        write hook has had a chance to fire and before :meth:`finish_build`.

        This is idempotent in practice but not in form — calling it twice
        would double-count. The build command calls it exactly once per
        build.
        """
        self._output_dedup_count = registry.total_dedups
        self._output_large_file_collision_count = registry.large_file_collision_count

        conflicts: list[OutputConflictInfo] = []
        for entry in registry.conflict_entries:
            conflict = OutputConflictInfo(
                output_path=str(entry.output_path),
                first_writer=(
                    str(entry.first_writer_source)
                    if entry.first_writer_source is not None
                    else None
                ),
                last_writer=(
                    str(entry.last_writer_source) if entry.last_writer_source is not None else None
                ),
                first_hash=entry.first_writer_hash,
                last_hash=entry.last_writer_hash or entry.content_hash,
                conflict_count=entry.conflict_count,
            )
            conflicts.append(conflict)
            self.report_warning(
                BuildWarning(
                    category="output_path_conflict",
                    severity="high",
                    file_path=conflict.output_path,
                    message=(
                        f"Multiple writers produced different content for "
                        f"{conflict.output_path}: first writer "
                        f"{conflict.first_writer}, last writer "
                        f"{conflict.last_writer} (last writer won — the "
                        f"file on disk is race-dependent across runs)"
                    ),
                )
            )
        self._output_conflicts = conflicts

        if registry.large_file_collision_count > 0:
            self.report_warning(
                BuildWarning(
                    category="output_large_file_collision",
                    severity="low",
                    file_path=None,
                    message=(
                        f"{registry.large_file_collision_count} write(s) targeted "
                        f"output paths over the dedup hash limit; dedup was "
                        f"skipped for these (set CLM_OUTPUT_DEDUP_HASH_LIMIT_MB "
                        f"higher to include larger files)."
                    ),
                )
            )

    def cleanup(self) -> None:
        """Clean up reporter resources."""
        self.formatter.cleanup()
