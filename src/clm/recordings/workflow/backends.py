"""Pluggable processing backends for the recording workflow.

Two backends are provided:

- :class:`ExternalBackend` — "wait for external tool" mode.  The user
  processes audio externally (e.g. iZotope RX 11) and the file watcher
  simply waits for the ``.wav`` to appear alongside the raw video.

- :class:`OnnxBackend` — "process locally" mode.  Extracts audio from
  the raw video, runs the ONNX DeepFilterNet3 denoising pipeline, applies
  FFmpeg audio filters, and writes the processed ``.wav``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from loguru import logger


@runtime_checkable
class ProcessingBackend(Protocol):
    """Protocol for audio processing backends."""

    def process(self, video: Path, output_wav: Path) -> None:
        """Extract and process audio from a video file.

        Args:
            video: Input raw video file.
            output_wav: Destination path for the processed WAV audio.

        Raises:
            Exception: On processing failure.
        """
        ...


class ExternalBackend:
    """Backend that relies on an external tool for audio processing.

    This backend does **not** process files itself.  In external mode the
    file watcher monitors ``to-process/`` for ``.wav`` files that appear
    from an external tool (e.g. iZotope RX 11 Batch Processor) and
    triggers assembly when the matching video is found.
    """

    def process(self, video: Path, output_wav: Path) -> None:  # pragma: no cover
        raise NotImplementedError(
            "ExternalBackend does not process files. "
            "Audio is processed by an external tool (e.g. iZotope RX 11)."
        )


class OnnxBackend:
    """Process audio locally using the ONNX DeepFilterNet3 pipeline.

    Reuses the existing :class:`~clm.recordings.processing.pipeline.ProcessingPipeline`
    to: extract audio → ONNX denoise → FFmpeg filters → write WAV.

    Unlike the full pipeline (which muxes back into video), this backend
    stops after producing the processed ``.wav`` so that the assembler
    can pair it with the original video.
    """

    def __init__(self, config: object | None = None) -> None:
        # Lazy import to avoid hard dependency on onnxruntime at import time
        self._config = config

    def process(self, video: Path, output_wav: Path) -> None:
        """Extract audio, denoise via ONNX, apply filters, write WAV.

        Args:
            video: Input raw video file (must contain an audio stream).
            output_wav: Output path for the processed WAV audio.
        """
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
            logger.info("Extracting audio from {}", video.name)
            run_subprocess(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-y",
                    "-i",
                    video,
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
            logger.info("Running ONNX noise reduction")
            run_onnx_denoise(
                audio_raw,
                audio_cleaned,
                atten_lim_db=config.denoise_atten_lim,
            )
            logger.info("Noise reduction complete")

            # Step 3: Apply FFmpeg audio filters and write final WAV
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
