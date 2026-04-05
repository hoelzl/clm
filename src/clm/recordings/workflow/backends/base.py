"""Backend Protocol and execution context.

Defines the contract every post-processing backend must satisfy. The
contract abstracts at the "raw recording → final recording" level — what
steps a backend takes internally (extract audio, mux, upload, download)
are its own business.

See ``docs/claude/design/recordings-backend-architecture.md`` §6.4 for
the full design rationale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from clm.recordings.workflow.jobs import (
    BackendCapabilities,
    ProcessingJob,
    ProcessingOptions,
)


@runtime_checkable
class JobContext(Protocol):
    """Environment supplied by the :class:`JobManager` to a backend at submit time.

    Backends use this to publish progress updates without knowing about
    the event bus or persistence layer. Every call to :meth:`report`
    mutates the job's ``updated_at``, persists the job, and publishes a
    ``job`` event.
    """

    def report(self, job: ProcessingJob) -> None:
        """Persist the job and publish a progress event."""
        ...

    @property
    def work_dir(self) -> Path:
        """Scratch directory the backend may use for intermediate files."""
        ...


@runtime_checkable
class ProcessingBackend(Protocol):
    """Interface for recording post-processing backends.

    A backend takes a raw recording in the ``to-process/`` tree and
    yields a final recording under ``final/``. Internal steps —
    audio extraction, muxing, cloud upload/download, archiving — are
    encapsulated; callers see a black box.

    Backends fall into two families:

    * **Synchronous** (``capabilities.is_synchronous == True``): the
      :meth:`submit` call blocks until the job reaches a terminal state.
      :meth:`poll` is a no-op that returns the job unchanged.

    * **Asynchronous** (``capabilities.is_synchronous == False``):
      :meth:`submit` returns as soon as the remote work has been
      registered (state ``PROCESSING`` or ``UPLOADING``). The
      :class:`JobManager`'s poller loop then calls :meth:`poll`
      periodically until the job reaches a terminal state.
    """

    @property
    def capabilities(self) -> BackendCapabilities:
        """Declarative description of what this backend can do."""
        ...

    def accepts_file(self, path: Path) -> bool:
        """Should the watcher hand this file off to this backend?

        Called by :class:`~clm.recordings.workflow.watcher.RecordingsWatcher`
        on every filesystem event. Return True for the files that should
        trigger a new job (e.g., ``--RAW.mp4`` for video-in backends,
        ``--RAW.wav`` for :class:`ExternalAudioFirstBackend`).
        """
        ...

    def submit(
        self,
        raw_path: Path,
        final_path: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
    ) -> ProcessingJob:
        """Start a new processing job.

        For synchronous backends, returns a :class:`ProcessingJob` in a
        terminal state (``COMPLETED`` or ``FAILED``). For asynchronous
        backends, returns as soon as the remote work is registered, in
        state ``PROCESSING`` (or ``FAILED`` on upload failure).
        """
        ...

    def poll(self, job: ProcessingJob, *, ctx: JobContext) -> ProcessingJob:
        """Refresh the state of an in-flight job.

        Synchronous backends should return ``job`` unchanged. Asynchronous
        backends talk to their remote service and transition the job
        forward. On completion, the backend itself performs finalization
        (download, archive) before returning the updated job.
        """
        ...

    def cancel(self, job: ProcessingJob, *, ctx: JobContext) -> None:
        """Best-effort cancel; no-op if the backend cannot cancel in flight.

        The manager marks the job ``CANCELLED`` after this call returns
        regardless of whether the remote work actually stopped.
        """
        ...
