"""Local ONNX (DeepFilterNet3) audio-first backend.

Extracts audio from the raw video, runs frame-by-frame noise reduction
via DeepFilterNet3 (ONNX runtime), applies FFmpeg filters (highpass,
compressor, two-pass EBU R128 loudness normalization), and writes the
result as a ``.wav`` alongside the raw video. The
:class:`AudioFirstBackend` Template Method then muxes the WAV with the
original video and archives the originals.

This is the default backend for fresh installs — it runs fully offline
and has no account or credential requirements. See
``docs/claude/design/recordings-backend-architecture.md`` §6.6.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from clm.recordings.processing.batch import VIDEO_EXTENSIONS
from clm.recordings.workflow.backends.audio_first import AudioFirstBackend
from clm.recordings.workflow.backends.base import JobContext
from clm.recordings.workflow.jobs import (
    BackendCapabilities,
    ProcessingJob,
    ProcessingOptions,
)
from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX, parse_raw_stem

if TYPE_CHECKING:
    from clm.recordings.processing.config import PipelineConfig


class OnnxAudioFirstBackend(AudioFirstBackend):
    """Audio-first backend powered by DeepFilterNet3 via ONNX runtime.

    The implementation mirrors the legacy ``OnnxBackend.process`` body
    but reports progress through the :class:`JobContext` at each step so
    the web dashboard sees a live feed. Expensive dependencies
    (``onnxruntime``, ffmpeg helpers) are imported lazily inside
    :meth:`_produce_audio` so test environments without the ``onnx``
    extras can still import this module.

    Args:
        root_dir: Recordings root (parent of ``to-process/``, ``final/``,
            ``archive/``).
        raw_suffix: Raw-file suffix, default ``"--RAW"``.
        config: Optional :class:`PipelineConfig` override. When ``None``
            a default config is constructed lazily on first use so tests
            that only exercise the type surface don't need onnxruntime.
    """

    capabilities = BackendCapabilities(
        name="onnx",
        display_name="Local (DeepFilterNet3 + FFmpeg)",
        description=(
            "Fully offline audio processing: extracts audio, denoises via "
            "DeepFilterNet3, applies FFmpeg filters and loudness normalization, "
            "then muxes back with the original video."
        ),
        video_in_video_out=False,
        is_synchronous=True,
        requires_internet=False,
        requires_api_key=False,
        supports_cut_lists=False,
        supported_input_extensions=tuple(sorted(VIDEO_EXTENSIONS)),
    )

    def __init__(
        self,
        *,
        root_dir: Path,
        raw_suffix: str = DEFAULT_RAW_SUFFIX,
        config: PipelineConfig | None = None,
    ) -> None:
        super().__init__(name="onnx", root_dir=root_dir, raw_suffix=raw_suffix)
        self._config = config

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    def accepts_file(self, path: Path) -> bool:
        """True for ``<name>--RAW.<ext>`` video files matching the suffix."""
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return False
        _, is_raw = parse_raw_stem(path.stem, self._raw_suffix)
        return is_raw

    # ------------------------------------------------------------------
    # Template Method hook
    # ------------------------------------------------------------------

    def _produce_audio(
        self,
        raw: Path,
        output_wav: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
        job: ProcessingJob,
    ) -> None:
        """Extract → ONNX denoise → FFmpeg filters → write *output_wav*."""
        # Imported lazily so the module is importable without onnxruntime.
        from clm.recordings.processing.config import PipelineConfig
        from clm.recordings.processing.utils import (
            find_ffmpeg,
            find_ffprobe,
            get_audio_duration,
            run_onnx_denoise,
            run_subprocess,
        )

        config = self._config if isinstance(self._config, PipelineConfig) else PipelineConfig()
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()

        output_wav.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="clm_onnx_") as tmp:
            tmp_dir = Path(tmp)
            audio_raw = tmp_dir / "audio_raw.wav"
            audio_cleaned = tmp_dir / "audio_cleaned.wav"

            # Step 1: Extract audio as mono 16-bit WAV
            job.message = "Extracting audio"
            job.progress = 0.1
            ctx.report(job)
            logger.info("Extracting audio from {}", raw.name)
            run_subprocess(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-y",
                    "-i",
                    raw,
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    str(config.sample_rate),
                    "-ac",
                    "1",
                    audio_raw,
                ]
            )
            duration = get_audio_duration(ffprobe, audio_raw)
            logger.info("Audio extracted ({:.0f}s)", duration)

            # Step 2: ONNX denoise
            job.message = "Running ONNX noise reduction"
            job.progress = 0.3
            ctx.report(job)
            logger.info("Running ONNX noise reduction")
            run_onnx_denoise(
                audio_raw,
                audio_cleaned,
                atten_lim_db=config.denoise_atten_lim,
            )
            logger.info("Noise reduction complete")

            # Step 3: Apply FFmpeg audio filters and write final WAV
            job.message = "Applying audio filters"
            job.progress = 0.6
            ctx.report(job)
            logger.info("Applying audio filters")
            cfg = config.audio_filters
            base_filters = f"highpass=f={cfg.highpass_freq},{cfg.compressor_filter}"
            loudnorm = cfg.loudnorm_filter
            full_filter = f"{base_filters},{loudnorm}"

            run_subprocess(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-y",
                    "-i",
                    audio_cleaned,
                    "-af",
                    full_filter,
                    "-ar",
                    str(config.sample_rate),
                    output_wav,
                ]
            )
            logger.info("Processed audio written to {}", output_wav)
