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
    """

    category: str
    message: str
    severity: Literal["high", "medium", "low"]
    file_path: str | None = None

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

    @property
    def successful_files(self) -> int:
        """Number of successfully processed files."""
        return self.total_files - len([e for e in self.errors if e.severity == "error"])

    @property
    def failed_files(self) -> int:
        """Number of failed files."""
        return len([e for e in self.errors if e.severity == "error"])

    def has_errors(self) -> bool:
        """Check if build has any errors."""
        return len([e for e in self.errors if e.severity in ("error", "fatal")]) > 0

    def has_fatal_errors(self) -> bool:
        """Check if build has fatal errors."""
        return len([e for e in self.errors if e.severity == "fatal"]) > 0

    def __str__(self) -> str:
        """Human-readable summary representation."""
        status = "✗" if self.has_errors() else "✓"
        status_text = "with errors" if self.has_errors() else "successfully"

        parts = [f"{status} Build completed {status_text} in {self.duration:.1f}s"]
        parts.append("")
        parts.append("Summary:")
        parts.append(f"  {self.total_files} files processed")
        parts.append(f"  {len(self.errors)} errors")
        parts.append(f"  {len(self.warnings)} warnings")
        if self.output_dedup_count or self.output_conflicts:
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
