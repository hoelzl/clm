"""Data classes for build reporting.

This module defines the data structures used for tracking build progress,
errors, warnings, and summaries during course builds.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class BuildError:
    """Represents a build error with categorization.

    Attributes:
        error_type: Category of error (user, configuration, or infrastructure)
        category: Specific error category (e.g., 'notebook_compilation', 'missing_tool')
        severity: Error severity level
        file_path: Path to the file that caused the error
        message: Error message
        actionable_guidance: Suggestion for how to fix the error
        job_id: Optional job ID for tracking
        correlation_id: Optional correlation ID for end-to-end tracing
        details: Additional error details (e.g., cell_number, code_snippet)
        occurrence_count: How many times an identical error was reported
            during the build. A single source slide is processed once per
            output target and language, so the same drop/failure is reported
            several times; the live stream shows it once and the summary
            collapses the duplicates into one entry carrying this count.
    """

    error_type: Literal["user", "configuration", "infrastructure"]
    category: str
    severity: Literal["error", "warning", "fatal"]
    file_path: str
    message: str
    actionable_guidance: str
    job_id: int | None = None
    correlation_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    occurrence_count: int = 1

    def __str__(self) -> str:
        """Human-readable error representation."""
        parts = [f"[{self.error_type.title()} Error] {self.category}"]
        parts.append(f"  File: {self.file_path}")
        parts.append(f"  Error: {self.message}")
        if self.actionable_guidance:
            parts.append(f"  Action: {self.actionable_guidance}")
        if self.job_id:
            parts.append(f"  Job ID: #{self.job_id}")
        return "\n".join(parts)

    def to_json(self) -> str:
        """Serialize to JSON string for database storage."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, json_str: str) -> "BuildError":
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls(**data)


@dataclass
class BuildWarning:
    """Represents a build warning.

    Attributes:
        category: Warning category (e.g., 'duplicate_topic_id', 'slow_worker')
        message: Warning message
        severity: Warning priority level
        file_path: Optional path to the file that caused the warning
        occurrence_count: How many times an identical warning was reported
            during the build (see :attr:`BuildError.occurrence_count`).
    """

    category: str
    message: str
    severity: Literal["high", "medium", "low"]
    file_path: str | None = None
    occurrence_count: int = 1

    def __str__(self) -> str:
        """Human-readable warning representation."""
        prefix = f"[{self.severity.title()} Priority]"
        if self.file_path:
            return f"{prefix} {self.message} (File: {self.file_path})"
        return f"{prefix} {self.message}"

    def to_json(self) -> str:
        """Serialize to JSON string for database storage."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, json_str: str) -> "BuildWarning":
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls(**data)


@dataclass
class FlakyFileInfo:
    """One deck that passed only after at least one failed execution attempt.

    Aggregated per source file across the build (a bilingual deck that
    flaked in both languages is one entry). Surfaced as the build summary's
    flake list so kernel flakiness is observable over time (issue #330);
    the full per-attempt history is in the execution-telemetry database.

    Attributes:
        file_path: Source path of the flaky deck.
        max_attempts: Highest attempt count that was needed for a pass.
        failure_types: Distinct failure types seen across the failed
            attempts (e.g. ``dead_kernel``, ``cell_execution_error``).
        languages: Output languages in which the deck flaked.
        flake_count: Number of flaky executions of this deck in the build.
    """

    file_path: str
    max_attempts: int
    failure_types: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    flake_count: int = 1


@dataclass
class OutputConflictInfo:
    """One output-path conflict surfaced by :class:`OutputWriteRegistry`.

    Attributes:
        output_path: Absolute output path that received conflicting writes.
        first_writer: Source path of the first writer (None if unknown).
        last_writer: Source path of the most recent writer (None if unknown).
        first_hash: BLAKE2b-128 hash of the first writer's content.
        last_hash: BLAKE2b-128 hash of the last writer's content.
        conflict_count: Number of additional writes after the first that
            produced differing content for this output path.
    """

    output_path: str
    first_writer: str | None
    last_writer: str | None
    first_hash: str
    last_hash: str
    conflict_count: int


# Human-readable labels for the cache-miss reason codes produced by
# ``DatabaseManager.diagnose_cache_miss`` (surfaced by ``clm build
# --explain-rebuilds``). Kept here so every summary renderer — text, rich,
# JSON — agrees on wording and ordering.
REBUILD_REASON_LABELS: dict[str, str] = {
    "hash_mismatch": "content changed (source or a dependency)",
    "no_entry": "no cache entry (never built here, or cache cleared)",
    "metadata_mismatch": "new output target (kind/format/language)",
}


@dataclass
class BuildSummary:
    """Summary of a build execution.

    Attributes:
        duration: Build duration in seconds
        total_files: Total number of files processed
        successful_files: Number of successfully processed files
        errors: List of errors encountered
        warnings: List of warnings encountered
        start_time: Build start timestamp
        end_time: Build end timestamp
        output_dedup_count: Number of output writes that were skipped
            because a previous writer had produced byte-identical content
        output_conflicts: One entry per output path that received
            conflicting writes within the build (last writer won)
        output_large_file_collision_count: Number of repeated writes to
            large-file outputs (over the configured hash limit) — these
            cannot be deduplicated reliably and are reported as a single
            summary value rather than per-event warnings.
    """

    duration: float
    total_files: int
    errors: list[BuildError] = field(default_factory=list)
    warnings: list[BuildWarning] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    output_dedup_count: int = 0
    output_conflicts: list[OutputConflictInfo] = field(default_factory=list)
    output_large_file_collision_count: int = 0
    flaky_files: list[FlakyFileInfo] = field(default_factory=list)
    """Decks that passed only after at least one failed execution attempt
    (issue #330). Empty on builds where every execution passed first try."""
    rebuild_reasons: dict[str, int] = field(default_factory=dict)
    """Count of cache misses per reason code (``hash_mismatch`` /
    ``no_entry`` / ``metadata_mismatch``), aggregated across all rebuilt
    output artifacts. Populated only under ``clm build --explain-rebuilds`` —
    empty otherwise, so every renderer that reads it is a no-op on a normal
    build."""
    timed_out: bool = False
    """True when the build aborted because one or more worker jobs did not
    complete within ``max_wait_for_completion_duration`` (issue #143). A
    timed-out build must always exit non-zero, independent of the
    ``--fail-on-error`` policy, because pending jobs mean the output tree is
    incomplete."""
    aborted: bool = False
    """True when stage processing raised and the exception is propagating
    past the summary (issue #596) — e.g. a job-submission failure such as
    "No workers available". The build did not complete, so renderers must
    show a failure headline instead of "Build completed successfully"."""

    @property
    def failed_files(self) -> int:
        """Number of distinct source files that reported an error.

        Counts unique ``file_path``s among error-severity findings, not raw
        error entries: a single file can emit several errors (e.g. multiple
        dropped voiceover narrations, or several missing cross-references), and
        those must not inflate the file count — which previously could drive
        :attr:`successful_files` negative.
        """
        return len({e.file_path for e in self.errors if e.severity == "error"})

    @property
    def successful_files(self) -> int:
        """Number of source files that processed without an error (never < 0)."""
        return max(0, self.total_files - self.failed_files)

    @property
    def total_rebuilds_explained(self) -> int:
        """Total cache misses recorded under ``--explain-rebuilds`` (0 if off)."""
        return sum(self.rebuild_reasons.values())

    def rebuild_reason_breakdown(self) -> list[tuple[str, int]]:
        """``(label, count)`` per rebuild reason, most frequent first.

        Empty unless the build ran with ``--explain-rebuilds``. Ordered by
        descending count (then label) so the dominant cause of unexpected
        rebuilds reads at the top. Unknown codes fall back to the raw code.
        """
        return sorted(
            (
                (REBUILD_REASON_LABELS.get(code, code), count)
                for code, count in self.rebuild_reasons.items()
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )

    def has_errors(self) -> bool:
        """Check if build has any errors."""
        return len([e for e in self.errors if e.severity in ("error", "fatal")]) > 0

    def has_fatal_errors(self) -> bool:
        """Check if build has fatal errors."""
        return len([e for e in self.errors if e.severity == "fatal"]) > 0

    def __str__(self) -> str:
        """Human-readable summary representation."""
        if self.aborted:
            headline = f"✗ Build aborted after {self.duration:.1f}s"
        elif self.has_errors():
            headline = f"✗ Build completed with errors in {self.duration:.1f}s"
        else:
            headline = f"✓ Build completed successfully in {self.duration:.1f}s"

        parts = [headline]
        parts.append("")
        parts.append("Summary:")
        parts.append(f"  {self.total_files} files processed")
        parts.append(f"  {len(self.errors)} errors")
        parts.append(f"  {len(self.warnings)} warnings")
        parts.append(
            f"  {self.output_dedup_count} duplicate output writes deduplicated; "
            f"{len(self.output_conflicts)} output paths had conflicting writes"
        )

        if self.errors:
            parts.append("")
            parts.append("Errors:")
            for error in self.errors[:5]:  # Show first 5 errors
                parts.append(f"  {error}")
            if len(self.errors) > 5:
                parts.append(f"  ... and {len(self.errors) - 5} more errors")

        if self.warnings:
            parts.append("")
            parts.append("Warnings:")
            for warning in self.warnings[:3]:  # Show first 3 warnings
                parts.append(f"  {warning}")
            if len(self.warnings) > 3:
                parts.append(f"  ... and {len(self.warnings) - 3} more warnings")

        if self.flaky_files:
            parts.append("")
            parts.append("Flaky decks (passed only after retry):")
            for flaky in self.flaky_files:
                types = ", ".join(flaky.failure_types) or "unknown"
                parts.append(f"  {flaky.file_path} (attempts: {flaky.max_attempts}, {types})")

        if self.rebuild_reasons:
            parts.append("")
            parts.append(f"Rebuild reasons ({self.total_rebuilds_explained} cache misses):")
            for label, count in self.rebuild_reason_breakdown():
                parts.append(f"  {count}  {label}")

        return "\n".join(parts)


@dataclass
class ProgressUpdate:
    """Progress update event from ProgressTracker.

    Attributes:
        completed: Number of completed jobs (includes both queued and cached)
        total: Total number of jobs
        active: Number of active jobs (currently processing)
        failed: Number of failed jobs
        stage: Optional current stage name
        cached: Number of jobs served from cache (no worker execution)
    """

    completed: int
    total: int
    active: int
    failed: int
    stage: str | None = None
    cached: int = 0
