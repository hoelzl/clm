"""Data types for the recordings post-processing job lifecycle.

A :class:`ProcessingJob` represents a single raw-recording → final-recording
job managed by the :class:`~clm.recordings.workflow.job_manager.JobManager`.
All three backend families (audio-first local, audio-first external, and
video-in/video-out cloud) speak in terms of these types.

This module is a leaf: it must not import from anything else in
``clm.recordings.workflow`` so that the backends, the job manager, and the
job store can all depend on it without introducing circular imports.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``.

    Wrapper so default-factories don't embed the call site and so tests
    can monkeypatch a single function for deterministic timestamps.
    """
    return datetime.now(timezone.utc)


class JobState(str, enum.Enum):
    """Lifecycle state of a :class:`ProcessingJob`.

    Synchronous (audio-first) backends pass through
    ``QUEUED → PROCESSING → ASSEMBLING → COMPLETED``.

    Asynchronous (cloud) backends pass through
    ``QUEUED → UPLOADING → PROCESSING → DOWNLOADING → COMPLETED``.

    Either flow can transition to ``FAILED`` or ``CANCELLED`` at any point.
    """

    QUEUED = "queued"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    DOWNLOADING = "downloading"
    ASSEMBLING = "assembling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: States that cannot progress any further.
TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
)


class ProcessingOptions(BaseModel):
    """Per-job options that override default backend behaviour.

    Passed into :meth:`ProcessingBackend.submit`. Backends that do not
    support a given option should ignore it (capability flags on
    :class:`BackendCapabilities` tell the UI which options are meaningful).
    """

    request_cut_list: bool = False
    """Ask the backend to produce a cut-list artifact (Auphonic only today)."""

    apply_cuts: bool = False
    """Phase 2: apply the cut list to the final video automatically."""

    custom_preset: str | None = None
    """Override the backend's default preset by name (Auphonic only today)."""

    title: str | None = None
    """Title metadata for the backend; defaults to the raw file stem."""

    extra: dict[str, object] = Field(default_factory=dict)
    """Escape hatch for backend-specific options not yet first-class."""


class BackendCapabilities(BaseModel):
    """Declarative description of what a backend can do.

    Used by the web UI and CLI to decide which options to expose without
    resorting to ``isinstance`` checks on the backend class.
    """

    name: str
    """Machine identifier used in config and logs (e.g. ``"auphonic"``)."""

    display_name: str
    """Human-readable label for the dashboard (e.g. ``"Auphonic (cloud)"``)."""

    description: str = ""
    """Optional longer description shown in help and info panels."""

    # Processing model ---------------------------------------------------
    video_in_video_out: bool = False
    """False = audio-first (needs assembly); True = produces final video directly."""

    is_synchronous: bool = True
    """False = long-running, backend must be polled by ``JobManager``."""

    requires_internet: bool = False
    requires_api_key: bool = False

    # Optional features --------------------------------------------------
    supports_cut_lists: bool = False
    supports_filler_removal: bool = False
    supports_silence_removal: bool = False
    supports_transcript: bool = False
    supports_chapter_detection: bool = False

    # Limits -------------------------------------------------------------
    max_file_size_mb: int | None = None
    """Maximum input file size in MiB, or None for no limit."""

    supported_input_extensions: tuple[str, ...] = (".mp4", ".mkv", ".mov")
    """Lower-case extensions (with leading dot) this backend can accept."""


class ProcessingJob(BaseModel):
    """A single raw-recording → final-recording job.

    Owned exclusively by the :class:`~clm.recordings.workflow.job_manager.JobManager`.
    Backends read jobs and return updated copies via their public methods
    (``submit`` / ``poll`` / ``cancel``); they do **not** mutate callers'
    instances directly — the manager is the single writer.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    backend_name: str
    """Machine id of the backend that owns this job (``"onnx"``, ``"auphonic"``, …)."""

    raw_path: Path
    """Input file in the ``to-process/`` tree."""

    final_path: Path
    """Planned output file under ``final/``."""

    relative_dir: Path
    """Course-relative directory the final file lives under (e.g. ``py/week01``)."""

    state: JobState = JobState.QUEUED
    progress: float = 0.0
    """Best-effort 0.0–1.0 progress hint for the UI."""

    message: str = ""
    """Human-readable current step (``"Uploading"``, ``"Muxing"``, …)."""

    error: str | None = None
    """Populated when ``state`` is :attr:`JobState.FAILED`."""

    last_poll_error: str | None = None
    """Most recent transient poll error, if any.

    Populated when the last poll cycle raised a transient exception
    (network timeout, HTTP 5xx, rate limit, schema drift, …) and the
    :class:`~clm.recordings.workflow.job_manager.JobManager` deliberately
    left the job in its current state so the next tick can retry. Does
    **not** imply ``state == FAILED`` — check :attr:`state` for that.
    Cleared to ``None`` on the first successful poll.

    Surfaced in ``clm recordings jobs list`` so the user can see why a
    long-running job appears stuck even though it hasn't been marked
    failed.
    """

    artifacts: dict[str, Path] = Field(default_factory=dict)
    """Extra outputs keyed by kind (``"cut_list"``, ``"transcript"``, …)."""

    backend_ref: str | None = None
    """Opaque backend-specific reference (e.g. Auphonic production UUID)."""

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """True if the job cannot progress any further."""
        return self.state in TERMINAL_STATES

    def touch(self) -> None:
        """Update :attr:`updated_at` to the current time.

        Called by the manager after any state transition so the UI can sort
        by recency. Tests can monkeypatch ``_utcnow`` for deterministic
        timestamps.
        """
        self.updated_at = _utcnow()
