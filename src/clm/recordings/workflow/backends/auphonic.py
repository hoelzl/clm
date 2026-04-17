"""Auphonic (cloud) video-in/video-out processing backend.

Auphonic is a commercial cloud service that takes a video file, runs
speech-aware denoising, leveling, loudness normalization, optional
filler/silence removal, and returns a processed video. This backend
drives an Auphonic production from creation to download and implements
the :class:`ProcessingBackend` Protocol directly — it does **not**
inherit from :class:`AudioFirstBackend` because the audio-first template
method does not fit the upload/poll/download shape.

Lifecycle:

1. ``submit(raw, final_path, …)`` creates a production, uploads the
   video (reporting upload progress to the dashboard), starts
   processing, and returns with the job in state ``PROCESSING``.
2. The :class:`JobManager`'s poller thread calls ``poll(job, …)`` on a
   cadence controlled by the constants at the top of this module. On
   ``DONE``, ``poll`` downloads the processed video, archives the raw,
   and transitions the job to ``COMPLETED``.
3. If Auphonic reports ``ERROR`` or the timeout elapses, the job
   transitions to ``FAILED``. The caller can re-submit.

Polling cadence is **code-level**, not user-facing config. See the
constants below and the design doc §10 for rationale.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from clm.recordings.processing.batch import VIDEO_EXTENSIONS
from clm.recordings.workflow.backends.auphonic_client import (
    AuphonicClient,
    AuphonicError,
    AuphonicProduction,
    AuphonicStatus,
)
from clm.recordings.workflow.backends.base import JobContext
from clm.recordings.workflow.directories import archive_dir
from clm.recordings.workflow.jobs import (
    BackendCapabilities,
    JobState,
    ProcessingJob,
    ProcessingOptions,
)
from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX, parse_raw_stem

#: Code-level polling cadence. These are **not** exposed as user config.
#: Change them by editing this module.
AUPHONIC_POLL_INITIAL_SECONDS = 30
"""First 30 minutes of a job: poll every 30 seconds."""

AUPHONIC_POLL_BACKOFF_AFTER_MINUTES = 30
"""After this many minutes, switch to the slower cadence."""

AUPHONIC_POLL_LONG_SECONDS = 300
"""Slow cadence: poll every 5 minutes once the job has been running long."""

AUPHONIC_POLL_TIMEOUT_MINUTES = 120
"""Default timeout: fail the job after this many total minutes.
Overridable per-user via ``RecordingsConfig.auphonic.poll_timeout_minutes``.
"""

#: Preset name used by ``clm recordings auphonic preset sync``.
DEFAULT_MANAGED_PRESET_NAME = "CLM Lecture Recording"

#: Algorithm configuration shipped inline on every production when the
#: user has not opted into a managed preset. Matches §3.5 of the design
#: doc: speech-aware denoise, leveler, loudness normalization, highpass.
#:
#: NOTE: The Auphonic API expects these fields inside an ``algorithms``
#: dict on the production create body.
DEFAULT_INLINE_ALGORITHMS: dict[str, Any] = {
    "denoise": True,
    "denoisemethod": "dynamic",
    "denoiseamount": 0,  # 0 = auto-select
    "leveler": True,
    "normloudness": True,
    "loudnesstarget": -16,  # LUFS
    "filtering": True,
    "filler_cutter": False,  # Phase 2 opt-in
    "silence_cutter": False,  # Phase 2 opt-in
}

#: Default output file descriptor — one video output that preserves the
#: input container format. Auphonic muxes the processed audio back in.
DEFAULT_VIDEO_OUTPUT: dict[str, Any] = {
    "format": "video",
    "ending": "mp4",
}

#: Default output file descriptor for an EDL cut list. Only added when
#: ``options.request_cut_list`` is True.
DEFAULT_CUT_LIST_OUTPUT: dict[str, Any] = {
    "format": "cut-list",
    "ending": "DaVinciResolve.edl",
}


def _humanize_duration(delta: timedelta) -> str:
    """Format *delta* as a compact human-readable duration.

    ``0:00:45`` → ``"45s"``, ``0:03:47`` → ``"3m 47s"``, ``1:05:30`` →
    ``"1h 5m"``. Seconds are dropped once the total exceeds an hour to
    keep the string short in the jobs panel.
    """
    total_seconds = int(max(0.0, delta.total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class AuphonicBackend:
    """Asynchronous cloud backend that drives Auphonic productions.

    The backend is video-in/video-out: there is no audio-first mux step
    and no ``.wav`` intermediate. ``accepts_file`` returns True for
    ``--RAW.{mp4,mkv,…}`` files and ``submit`` uploads them directly to
    Auphonic.

    Args:
        client: Configured :class:`AuphonicClient` for talking to the
            Auphonic API.
        root_dir: Recordings root (parent of ``to-process/``, ``final/``,
            ``archive/``). Used to compute archive destinations.
        raw_suffix: Raw filename suffix; default ``"--RAW"``.
        preset: Optional managed preset name. If set, productions
            reference the preset by name and do **not** send inline
            algorithms. If empty, every production is submitted with
            :data:`DEFAULT_INLINE_ALGORITHMS` instead.
        poll_timeout_minutes: Per-job timeout. Jobs older than this are
            transitioned to ``FAILED`` with a timeout error on the next
            poll. Defaults to :data:`AUPHONIC_POLL_TIMEOUT_MINUTES`.
        request_cut_list_default: Backend-level default for
            ``ProcessingOptions.request_cut_list``. Per-job options
            override this.
    """

    capabilities = BackendCapabilities(
        name="auphonic",
        display_name="Auphonic (cloud)",
        description=(
            "Cloud audio/video processing with speech-aware denoising, "
            "leveling, loudness normalization, and optional cut lists."
        ),
        video_in_video_out=True,
        is_synchronous=False,
        requires_internet=True,
        requires_api_key=True,
        supports_cut_lists=True,
        supports_filler_removal=True,
        supports_silence_removal=True,
        supports_chapter_detection=True,
        supported_input_extensions=tuple(sorted(VIDEO_EXTENSIONS)),
        max_file_size_mb=None,
    )

    def __init__(
        self,
        *,
        client: AuphonicClient,
        root_dir: Path,
        raw_suffix: str = DEFAULT_RAW_SUFFIX,
        preset: str = "",
        poll_timeout_minutes: int = AUPHONIC_POLL_TIMEOUT_MINUTES,
        request_cut_list_default: bool = False,
    ) -> None:
        self._client = client
        self._root_dir = root_dir
        self._raw_suffix = raw_suffix
        self._preset = preset.strip()
        self._poll_timeout_minutes = poll_timeout_minutes
        self._request_cut_list_default = request_cut_list_default

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    def accepts_file(self, path: Path) -> bool:
        """True for ``<name>--RAW.<video-ext>`` files."""
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return False
        _, is_raw = parse_raw_stem(path.stem, self._raw_suffix)
        return is_raw

    def submit(
        self,
        raw_path: Path,
        final_path: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
    ) -> ProcessingJob:
        """Create production, upload, start processing.

        Returns a job in state ``PROCESSING`` on success. The
        :class:`JobManager` poller then calls :meth:`poll` until the
        job reaches a terminal state.
        """
        relative = self._relative_dir_for(raw_path)
        title = options.title or self._title_for(raw_path)
        request_cut_list = options.request_cut_list or self._request_cut_list_default

        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw_path,
            final_path=final_path,
            relative_dir=relative,
            state=JobState.UPLOADING,
            message="Creating Auphonic production",
            progress=0.0,
        )
        ctx.report(job)

        try:
            # Step 1: create production
            production = self._client.create_production(
                metadata={"title": title},
                preset=self._preset_reference(options) or None,
                algorithms=self._inline_algorithms_or_none(options),
                output_files=self._output_files_for(
                    options,
                    request_cut_list=request_cut_list,
                ),
            )
            job.backend_ref = production.uuid
            job.message = "Uploading video"
            ctx.report(job)

            # Step 2: upload (streamed, 0.0 → 0.4 of total progress)
            def _upload_progress(fraction: float) -> None:
                job.progress = min(0.4, fraction * 0.4)
                ctx.report(job)

            self._client.upload_input(
                production.uuid,
                raw_path,
                on_progress=_upload_progress,
            )

            # Step 3: start processing
            self._client.start_production(production.uuid)
            job.state = JobState.PROCESSING
            job.progress = 0.4
            job.message = "Processing on Auphonic"
            if job.started_at is None:
                job.started_at = datetime.now(timezone.utc)
            ctx.report(job)
            # Auphonic returns status in <1s; nudge the poller so the
            # dashboard sees the first in-processing update promptly
            # rather than waiting out the full poll interval.
            ctx.request_poll_soon()
        except Exception as exc:
            logger.exception("Auphonic submit failed for {}: {}", raw_path.name, exc)
            job.state = JobState.FAILED
            job.error = str(exc)
            ctx.report(job)
            # Best-effort cleanup: delete the orphan production so
            # credits aren't burned waiting for an upload that never
            # happened. Ignored if deletion itself fails.
            if job.backend_ref:
                try:
                    self._client.delete_production(job.backend_ref)
                except Exception as cleanup_exc:  # pragma: no cover — defensive
                    logger.warning(
                        "Failed to delete orphan Auphonic production {}: {}",
                        job.backend_ref,
                        cleanup_exc,
                    )

        return job

    def poll(self, job: ProcessingJob, *, ctx: JobContext) -> ProcessingJob:
        """Query Auphonic and advance *job*.

        On ``DONE``: downloads the output video + any artifacts, archives
        the raw, and marks the job ``COMPLETED``.

        On ``ERROR`` or local timeout: marks the job ``FAILED``.

        Otherwise: updates ``message``/``progress`` heuristically and
        returns the job unchanged in its current state.
        """
        if job.is_terminal:
            return job

        if not job.backend_ref:
            job.state = JobState.FAILED
            job.error = "Missing Auphonic production reference"
            ctx.report(job)
            return job

        # Enforce the local timeout regardless of Auphonic's state. The
        # started_at anchor is set when the job enters PROCESSING.
        if self._has_timed_out(job):
            logger.warning(
                "Auphonic job {} timed out after {} minutes",
                job.id,
                self._poll_timeout_minutes,
            )
            self._fail(
                job,
                f"Auphonic processing timed out after {self._poll_timeout_minutes} minutes",
                ctx,
            )
            return job

        try:
            production = self._client.get_production(job.backend_ref)
        except AuphonicError as exc:
            logger.warning("Auphonic poll error for {}: {}", job.id, exc)
            # Transient errors should not fail the job outright — keep
            # the job in its current state and let the next poll retry.
            # (The JobManager publishes the job regardless.)
            job.message = f"Auphonic poll error: {exc}"
            ctx.report(job)
            return job

        if production.status == AuphonicStatus.DONE:
            return self._finalize(job, production, ctx)
        if production.status == AuphonicStatus.ERROR:
            self._fail(
                job,
                production.error_message or "Auphonic reported ERROR",
                ctx,
            )
            return job

        # In-progress: surface Auphonic's own status_string plus a
        # heuristic progress value so the dashboard stays lively.
        # Passing *job* appends an elapsed-time heartbeat so every poll
        # publishes a visibly different message even when Auphonic's
        # own status hasn't changed.
        job.message = self._message_for(production, job)
        job.progress = self._progress_for(production.status, job.progress)
        ctx.report(job)
        return job

    def cancel(self, job: ProcessingJob, *, ctx: JobContext) -> None:
        """Best-effort cancel via production deletion."""
        if not job.backend_ref:
            return
        try:
            self._client.delete_production(job.backend_ref)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "Auphonic delete failed for {} (uuid={}): {}",
                job.id,
                job.backend_ref,
                exc,
            )

    # ------------------------------------------------------------------
    # Finalization / state transitions
    # ------------------------------------------------------------------

    def _finalize(
        self,
        job: ProcessingJob,
        production: AuphonicProduction,
        ctx: JobContext,
    ) -> ProcessingJob:
        """Download outputs, archive raw, mark job COMPLETED."""
        job.state = JobState.DOWNLOADING
        job.message = "Downloading processed video"
        job.progress = max(job.progress, 0.85)
        ctx.report(job)

        try:
            video_downloaded = False
            for out in production.output_files:
                if not out.download_url:
                    continue
                if out.format == "video" and not video_downloaded:
                    self._download_video(job, out.download_url, ctx)
                    video_downloaded = True
                elif out.format == "cut-list":
                    self._download_cut_list(job, out.download_url, out.ending)

            if not video_downloaded:
                raise AuphonicError(
                    "Auphonic production reported DONE but no video output was returned"
                )

            self._archive_raw(job)

            job.state = JobState.COMPLETED
            job.progress = 1.0
            job.message = "Done"
            job.completed_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.exception("Auphonic finalize failed for {}: {}", job.id, exc)
            job.state = JobState.FAILED
            job.error = str(exc)

        ctx.report(job)
        return job

    def _download_video(
        self,
        job: ProcessingJob,
        url: str,
        ctx: JobContext,
    ) -> None:
        """Stream the processed video to ``job.final_path``."""
        job.final_path.parent.mkdir(parents=True, exist_ok=True)

        def _on_progress(fraction: float) -> None:
            # Download occupies the 0.85 → 0.98 slice of the progress bar.
            job.progress = 0.85 + (fraction * 0.13)
            ctx.report(job)

        self._client.download(url, job.final_path, on_progress=_on_progress)

    def _download_cut_list(
        self,
        job: ProcessingJob,
        url: str,
        ending: str,
    ) -> None:
        """Download the cut-list artifact next to the final video."""
        suffix = ".edl"
        if ending.lower().endswith(".edl"):
            suffix = ".edl"
        elif ending.lower().endswith(".csv"):
            suffix = ".csv"
        cut_path = job.final_path.with_suffix(suffix)
        self._client.download(url, cut_path)
        job.artifacts["cut_list"] = cut_path

    def _archive_raw(self, job: ProcessingJob) -> None:
        """Move the raw video into ``archive/<relative_dir>/``.

        Auphonic produces a final video directly, so there is no matching
        ``.wav`` to move alongside it (unlike the audio-first backends).
        If the raw file has been deleted out from under us (the user
        cleaned up mid-run), we log and continue.
        """
        import shutil

        if not job.raw_path.exists():
            logger.warning(
                "Raw file {} disappeared before archive; skipping archive step",
                job.raw_path,
            )
            return

        dest_dir = archive_dir(self._root_dir) / job.relative_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / job.raw_path.name
        shutil.move(str(job.raw_path), str(dest))
        logger.info("Archived {} to {}", job.raw_path.name, dest)

    def _fail(self, job: ProcessingJob, error: str, ctx: JobContext) -> None:
        job.state = JobState.FAILED
        job.error = error
        ctx.report(job)

    # ------------------------------------------------------------------
    # Request-body helpers
    # ------------------------------------------------------------------

    def _preset_reference(self, options: ProcessingOptions) -> str:
        """Return the preset name to send on the create request, or ''.

        Per-job override (``options.custom_preset``) wins over backend
        default (``self._preset``). Empty string means "use inline
        algorithms instead".
        """
        if options.custom_preset:
            return options.custom_preset
        return self._preset

    def _inline_algorithms_or_none(
        self,
        options: ProcessingOptions,
    ) -> dict[str, Any] | None:
        """Return the inline algorithm dict, or None when referencing a preset.

        Auphonic accepts either mode on a production create request but
        the two are mutually exclusive in practice. When the user has
        opted into a managed preset we send the preset name only; when
        they have not, we send the full algorithm config inline so there
        is no Auphonic-side state the user must set up first.
        """
        if self._preset_reference(options):
            return None
        return dict(DEFAULT_INLINE_ALGORITHMS)

    def _output_files_for(
        self,
        options: ProcessingOptions,
        *,
        request_cut_list: bool,
    ) -> list[dict[str, Any]]:
        """Build the ``output_files`` request array.

        Always includes a single ``video`` output. Appends a ``cut-list``
        output when the user (or backend default) asks for one.
        """
        outputs: list[dict[str, Any]] = [dict(DEFAULT_VIDEO_OUTPUT)]
        if request_cut_list:
            outputs.append(dict(DEFAULT_CUT_LIST_OUTPUT))
        return outputs

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _relative_dir_for(self, raw: Path) -> Path:
        """Course-relative directory for *raw*, empty if outside to-process."""
        from clm.recordings.workflow.directories import to_process_dir

        tp = to_process_dir(self._root_dir)
        try:
            return raw.parent.relative_to(tp)
        except ValueError:
            return Path()

    def _title_for(self, raw: Path) -> str:
        """Derive a human-readable title from the raw filename."""
        base, _ = parse_raw_stem(raw.stem, self._raw_suffix)
        return base or raw.stem

    def _has_timed_out(self, job: ProcessingJob) -> bool:
        """True if *job* has been in-flight longer than the configured timeout.

        Uses ``started_at`` as the anchor (set when the job first enters
        ``PROCESSING``). Falls back to ``created_at`` if somehow unset.
        """
        anchor = job.started_at or job.created_at
        if anchor is None:  # pragma: no cover — both defaulted in the model
            return False
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        deadline = anchor + timedelta(minutes=self._poll_timeout_minutes)
        return datetime.now(timezone.utc) > deadline

    @staticmethod
    def _message_for(
        production: AuphonicProduction,
        job: ProcessingJob | None = None,
    ) -> str:
        """Human-readable status message for an in-flight production.

        When *job* is supplied and its ``started_at`` is set, the elapsed
        wall-clock time spent in the current phase is appended. This gives
        the UI something to tick on every poll even when Auphonic's own
        status string hasn't changed — the perceptual difference between
        "frozen" and "working" is often just a heartbeat.
        """
        if production.status_string:
            base = f"Auphonic: {production.status_string}"
        else:
            base = f"Auphonic status {production.status}"

        if job is None or job.started_at is None:
            return base
        anchor = job.started_at
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - anchor
        return f"{base} — {_humanize_duration(delta)}"

    @staticmethod
    def _progress_for(status: int, current: float) -> float:
        """Heuristic progress value for an in-flight Auphonic job.

        Maps Auphonic's status codes to rough progress fractions so the
        dashboard bar advances instead of sitting at 0.4 for 15 minutes.
        Never decreases below the current value.
        """
        mapping = {
            AuphonicStatus.WAITING: 0.45,
            AuphonicStatus.AUDIO_PROCESSING: 0.60,
            AuphonicStatus.SPEECH_RECOGNITION: 0.70,
            AuphonicStatus.AUDIO_ENCODING: 0.80,
            AuphonicStatus.OUTGOING_FILE_TRANSFER: 0.85,
        }
        return max(current, mapping.get(status, current))


__all__ = [
    "AUPHONIC_POLL_BACKOFF_AFTER_MINUTES",
    "AUPHONIC_POLL_INITIAL_SECONDS",
    "AUPHONIC_POLL_LONG_SECONDS",
    "AUPHONIC_POLL_TIMEOUT_MINUTES",
    "DEFAULT_CUT_LIST_OUTPUT",
    "DEFAULT_INLINE_ALGORITHMS",
    "DEFAULT_MANAGED_PRESET_NAME",
    "DEFAULT_VIDEO_OUTPUT",
    "AuphonicBackend",
]
