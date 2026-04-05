"""External-tool (iZotope RX 11, …) audio-first backend.

The external workflow inverts the usual "raw video triggers processing"
shape: the user records with OBS (producing ``<topic>--RAW.mp4``), then
runs the audio through an external tool (e.g. iZotope RX 11), which
drops ``<topic>--RAW.wav`` next to the video. The **appearance of the
.wav** is what signals that work is ready — not the appearance of the
video. This backend therefore:

* Declares ``accepts_file(path)`` True for ``--RAW.wav`` files (audio,
  not video). The watcher dispatches on the ``.wav`` trigger.
* Overrides :meth:`submit` instead of :meth:`_produce_audio`, because
  the ``.wav`` **is already** the processed audio — there is no
  "produce" step. Submit resolves the matching raw video in the same
  directory and runs the assembly step directly.

See ``docs/claude/design/recordings-backend-architecture.md`` §6.7 and
the "External backend trigger inversion" entry in §2 of the same doc.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from clm.recordings.processing.batch import VIDEO_EXTENSIONS
from clm.recordings.workflow.assembler import assemble_one
from clm.recordings.workflow.backends.audio_first import AudioFirstBackend
from clm.recordings.workflow.backends.base import JobContext
from clm.recordings.workflow.directories import (
    PendingPair,
    archive_dir,
    final_dir,
)
from clm.recordings.workflow.jobs import (
    BackendCapabilities,
    JobState,
    ProcessingJob,
    ProcessingOptions,
)
from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX, parse_raw_stem


class ExternalAudioFirstBackend(AudioFirstBackend):
    """Audio-first backend that waits for an external tool to deliver the .wav.

    No local audio processing happens here — the user runs the audio
    through iZotope RX 11 (or similar) and drops the processed
    ``.wav`` next to the raw video in ``to-process/``. This backend is
    a thin wrapper that pairs the two files and kicks off assembly.

    The class is marked ``is_synchronous=True`` because once the trigger
    file lands, submit runs to completion in one call — there is no
    remote state to poll. Asynchrony lives in the *watcher*: it's the
    piece that waits for the trigger file to appear.

    Args:
        root_dir: Recordings root (parent of ``to-process/``, ``final/``,
            ``archive/``).
        raw_suffix: Filename suffix identifying raw recordings. Defaults
            to ``"--RAW"``.
    """

    capabilities = BackendCapabilities(
        name="external",
        display_name="External tool (e.g. iZotope RX 11)",
        description=(
            "Wait for an external audio processing tool to drop a "
            "--RAW.wav next to the raw video, then mux and archive."
        ),
        video_in_video_out=False,
        is_synchronous=True,
        requires_internet=False,
        requires_api_key=False,
        supports_cut_lists=False,
        supported_input_extensions=(".wav",),
    )

    def __init__(
        self,
        *,
        root_dir: Path,
        raw_suffix: str = DEFAULT_RAW_SUFFIX,
    ) -> None:
        super().__init__(name="external", root_dir=root_dir, raw_suffix=raw_suffix)

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    def accepts_file(self, path: Path) -> bool:
        """True for ``<name>--RAW.wav`` files matching the suffix.

        The trigger is the **audio** file, not the video. The watcher
        waits for the external tool to deliver this file, then calls
        :meth:`submit`.
        """
        if path.suffix.lower() != ".wav":
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
        """Pair the .wav with its matching raw video and run assembly.

        *raw_path* is the trigger file — a ``--RAW.wav`` — not a video.
        The method resolves the matching ``--RAW.<video-ext>`` in the
        same directory and hands the pair to the assembler. If no
        matching video exists, the job is marked ``FAILED`` with a
        message explaining what's missing.
        """
        relative = self._relative_dir_for(raw_path)
        job = ProcessingJob(
            backend_name=self._name,
            raw_path=raw_path,
            final_path=final_path,
            relative_dir=relative,
            state=JobState.ASSEMBLING,
            message="Muxing and archiving",
            progress=0.5,
        )
        ctx.report(job)

        try:
            video = self._find_matching_video(raw_path)
            if video is None:
                raise FileNotFoundError(
                    f"No matching raw video found for {raw_path.name} in {raw_path.parent}"
                )

            self._assemble_wav_with_video(video, raw_path, job)

            job.state = JobState.COMPLETED
            job.message = "Done"
            job.progress = 1.0
        except Exception as exc:
            logger.exception("External backend failed for {}: {}", raw_path.name, exc)
            job.state = JobState.FAILED
            job.error = str(exc)

        ctx.report(job)
        return job

    # ------------------------------------------------------------------
    # Template Method hook (not used by this backend)
    # ------------------------------------------------------------------

    def _produce_audio(
        self,
        raw: Path,
        output_wav: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
        job: ProcessingJob,
    ) -> None:  # pragma: no cover — not reached because submit is overridden
        raise NotImplementedError(
            "ExternalAudioFirstBackend overrides submit() directly; _produce_audio is never called."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_matching_video(self, wav_path: Path) -> Path | None:
        """Find a raw video file matching *wav_path* in the same directory."""
        stem = wav_path.stem  # e.g. "topic--RAW"
        for ext in VIDEO_EXTENSIONS:
            candidate = wav_path.with_name(f"{stem}{ext}")
            if candidate.is_file():
                return candidate
        return None

    def _assemble_wav_with_video(
        self,
        video: Path,
        audio: Path,
        job: ProcessingJob,
    ) -> None:
        """Mux *video* + *audio* into ``final/`` and archive the originals."""
        fl = final_dir(self._root_dir)
        ar = archive_dir(self._root_dir)

        pair = PendingPair(
            video=video,
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
