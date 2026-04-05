"""Template Method ABC for audio-first backends.

Audio-first backends share a common flow: produce a processed ``.wav``
alongside the raw video, then mux the two into ``final/`` and archive
the originals. Only the "produce the .wav" step differs between the
local ONNX pipeline and the external (iZotope RX 11) workflow.

This base class captures the shared flow as a Template Method. Concrete
subclasses implement :meth:`_produce_audio` and declare their own
``capabilities`` and ``accepts_file``.

See ``docs/claude/design/recordings-backend-architecture.md`` §6.5.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger

from clm.recordings.workflow.assembler import assemble_one
from clm.recordings.workflow.backends.base import JobContext
from clm.recordings.workflow.directories import (
    PendingPair,
    archive_dir,
    final_dir,
    to_process_dir,
)
from clm.recordings.workflow.jobs import (
    BackendCapabilities,
    JobState,
    ProcessingJob,
    ProcessingOptions,
)
from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX


class AudioFirstBackend(ABC):
    """Base class for backends that produce a ``.wav`` alongside the raw video.

    Subclasses only need to implement :meth:`_produce_audio` and supply a
    :attr:`capabilities` class attribute plus an :meth:`accepts_file`
    decision. Everything else — muxing, archiving, job state transitions,
    progress reporting, failure handling — is provided by this base class.

    Args:
        name: Machine id used in logs and :attr:`ProcessingJob.backend_name`.
        root_dir: Recordings root (the directory containing ``to-process/``,
            ``final/``, and ``archive/``).
        raw_suffix: Filename suffix identifying raw recordings (default
            ``"--RAW"``). Passed through to :class:`PendingPair` so the
            assembler strips it correctly when deriving the final file name.
    """

    #: Concrete subclasses override this class attribute.
    capabilities: BackendCapabilities

    def __init__(
        self,
        *,
        name: str,
        root_dir: Path,
        raw_suffix: str = DEFAULT_RAW_SUFFIX,
    ) -> None:
        self._name = name
        self._root_dir = root_dir
        self._raw_suffix = raw_suffix

    # ------------------------------------------------------------------
    # Protocol surface — shared implementations
    # ------------------------------------------------------------------

    @abstractmethod
    def accepts_file(self, path: Path) -> bool:
        """Return True if the watcher should dispatch this file to this backend."""

    def submit(
        self,
        raw_path: Path,
        final_path: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
    ) -> ProcessingJob:
        """Template Method: produce audio, assemble, archive.

        Reports progress at each step via :meth:`JobContext.report` so the
        web dashboard sees a live status feed. Any exception from a
        subclass hook is caught and stored on ``job.error``; the job
        transitions to :attr:`JobState.FAILED` and is returned to the
        caller (the :class:`JobManager` persists and publishes).
        """
        relative = self._relative_dir_for(raw_path)
        job = ProcessingJob(
            backend_name=self._name,
            raw_path=raw_path,
            final_path=final_path,
            relative_dir=relative,
            state=JobState.PROCESSING,
            message="Producing audio",
        )
        ctx.report(job)

        try:
            audio_path = self._audio_output_path(raw_path)
            self._produce_audio(
                raw_path,
                audio_path,
                options=options,
                ctx=ctx,
                job=job,
            )

            job.state = JobState.ASSEMBLING
            job.message = "Muxing and archiving"
            job.progress = max(job.progress, 0.8)
            ctx.report(job)

            self._assemble(raw_path, audio_path, job)

            job.state = JobState.COMPLETED
            job.message = "Done"
            job.progress = 1.0
        except Exception as exc:
            logger.exception("Audio-first backend {} failed: {}", self._name, exc)
            job.state = JobState.FAILED
            job.error = str(exc)

        ctx.report(job)
        return job

    def poll(self, job: ProcessingJob, *, ctx: JobContext) -> ProcessingJob:
        """Audio-first backends are synchronous; polling is a no-op."""
        return job

    def cancel(self, job: ProcessingJob, *, ctx: JobContext) -> None:
        """Best-effort cancel; the in-flight subprocess call runs to completion."""
        return

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def _produce_audio(
        self,
        raw: Path,
        output_wav: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
        job: ProcessingJob,
    ) -> None:
        """Create *output_wav* from *raw*.

        Subclasses should update ``job.progress`` and ``job.message`` as
        they work, calling ``ctx.report(job)`` after each meaningful
        transition so the UI stays live.

        Raises any exception on failure; the Template Method catches it
        and marks the job FAILED.
        """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _audio_output_path(self, raw: Path) -> Path:
        """Return the path where :meth:`_produce_audio` should write the WAV."""
        return raw.with_name(f"{raw.stem}.wav")

    def _relative_dir_for(self, raw: Path) -> Path:
        """Compute the course-relative directory for *raw*.

        Used to place the final file under ``final/<relative_dir>/`` and
        to archive originals under ``archive/<relative_dir>/``. If *raw*
        is not under the ``to-process/`` tree (e.g. in tests) an empty
        relative path is returned so the final file lands at the root.
        """
        tp = to_process_dir(self._root_dir)
        try:
            return raw.parent.relative_to(tp)
        except ValueError:
            return Path()

    def _assemble(self, raw: Path, audio: Path, job: ProcessingJob) -> None:
        """Mux *raw* + *audio* into ``final/`` and archive the originals."""
        fl = final_dir(self._root_dir)
        ar = archive_dir(self._root_dir)

        pair = PendingPair(
            video=raw,
            audio=audio,
            relative_dir=job.relative_dir,
            raw_suffix=self._raw_suffix,
        )

        result = assemble_one(pair, fl, ar)
        if not result.success:
            raise RuntimeError(result.error or "Assembly failed")

        # Keep the job's final_path in sync with what the assembler
        # actually produced (extension may differ from the planned value).
        job.final_path = result.output_file
