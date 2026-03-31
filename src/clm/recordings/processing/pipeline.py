"""Cross-platform video post-processing pipeline.

The processing steps are:
1. Extract audio from the recording (FFmpeg)
2. Run DeepFilterNet noise reduction
3. Apply FFmpeg audio filters (highpass, compressor, loudness normalization)
4. Encode cleaned audio to AAC
5. Mux processed audio back into the original video

All intermediate files are written to a temp directory and cleaned up
on completion (unless keep_temp is set).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from .config import PipelineConfig
from .utils import (
    find_deepfilter,
    find_ffmpeg,
    find_ffprobe,
    get_audio_duration,
    run_subprocess,
)


class ProcessingResult(BaseModel):
    """Result of processing a single recording."""

    input_file: Path
    output_file: Path
    success: bool
    duration_seconds: float = 0.0
    error: str | None = None


class ProcessingPipeline:
    """Video post-processing pipeline.

    Usage:
        pipeline = ProcessingPipeline()
        result = pipeline.process(
            input_file=Path("raw_recording.mkv"),
            output_file=Path("final_lecture.mp4"),
        )
    """

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()

        # Locate binaries once at init. Raises BinaryNotFoundError if missing.
        self.ffmpeg = find_ffmpeg()
        self.ffprobe = find_ffprobe()
        self.deepfilter = find_deepfilter()

        logger.info("FFmpeg:      {}", self.ffmpeg)
        logger.info("FFprobe:     {}", self.ffprobe)
        logger.info("DeepFilter:  {}", self.deepfilter)

    def process(
        self,
        input_file: Path,
        output_file: Path,
        *,
        on_step: Callable[[int, str, int], None] | None = None,
    ) -> ProcessingResult:
        """Process a single recording through the full pipeline.

        Args:
            input_file: Path to the raw recording.
            output_file: Path for the final output.
            on_step: Optional callback called with (step_number, step_name, total_steps)
                     for progress reporting.

        Returns:
            ProcessingResult with success status and details.
        """
        input_file = Path(input_file).resolve()
        output_file = Path(output_file).resolve()

        if not input_file.is_file():
            return ProcessingResult(
                input_file=input_file,
                output_file=output_file,
                success=False,
                error=f"Input file not found: {input_file}",
            )

        # Ensure output directory exists.
        output_file.parent.mkdir(parents=True, exist_ok=True)

        total_steps = 5
        notify = on_step or (lambda step, name, total: None)

        temp_dir = Path(tempfile.mkdtemp(prefix="clm_recording_"))
        try:
            audio_raw = temp_dir / "audio_raw.wav"
            audio_cleaned = temp_dir / "audio_cleaned.wav"
            audio_processed = temp_dir / "audio_processed.wav"
            audio_final = temp_dir / "audio_final.aac"

            # Step 1: Extract audio
            notify(1, "Extracting audio", total_steps)
            self._extract_audio(input_file, audio_raw)
            duration = get_audio_duration(self.ffprobe, audio_raw)
            logger.info("Audio extracted ({:.0f}s)", duration)

            # Step 2: DeepFilterNet noise reduction
            notify(2, "Noise reduction (DeepFilterNet)", total_steps)
            self._run_deepfilter(audio_raw, audio_cleaned, temp_dir)
            logger.info("Noise reduction complete")

            # Step 3: FFmpeg audio filters
            notify(3, "Audio filters (highpass, compressor, loudnorm)", total_steps)
            self._apply_audio_filters(audio_cleaned, audio_processed)
            logger.info("Audio filters applied")

            # Step 4: Encode to AAC
            notify(4, "Encoding audio", total_steps)
            self._encode_audio(audio_processed, audio_final)
            logger.info("Audio encoded")

            # Step 5: Mux video + cleaned audio
            notify(5, "Muxing final video", total_steps)
            self._mux_video(input_file, audio_final, output_file)
            logger.info("Muxing complete: {}", output_file)

            return ProcessingResult(
                input_file=input_file,
                output_file=output_file,
                success=True,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.error("Processing failed: {}", e)
            return ProcessingResult(
                input_file=input_file,
                output_file=output_file,
                success=False,
                error=str(e),
            )

        finally:
            if not self.config.keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.debug("Cleaned up temp dir: {}", temp_dir)
            else:
                logger.info("Temp files kept at: {}", temp_dir)

    def _extract_audio(self, input_file: Path, output: Path) -> None:
        """Extract audio as mono 16-bit WAV."""
        run_subprocess(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                input_file,
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                str(self.config.sample_rate),
                "-ac",
                "1",
                output,
            ]
        )

    def _run_deepfilter(self, input_file: Path, output_file: Path, temp_dir: Path) -> None:
        """Run DeepFilterNet noise reduction.

        DeepFilterNet writes output files to --output-dir with a suffix
        or the same name. We handle the various naming conventions across
        versions.
        """
        df_output_dir = temp_dir / "df_output"
        df_output_dir.mkdir(exist_ok=True)

        run_subprocess(
            [
                self.deepfilter,
                input_file,
                "--output-dir",
                df_output_dir,
                "--atten-lim",
                str(self.config.deepfilter_atten_lim),
            ]
        )

        # Find the output file. DeepFilterNet uses various naming schemes
        # depending on version:
        #   - <name>_DeepFilterNet3.wav
        #   - <name>_DeepFilterNet2.wav
        #   - <name>.wav (same name)
        stem = input_file.stem
        candidates = sorted(df_output_dir.glob(f"{stem}*.wav"))

        if not candidates:
            # Fallback: just grab any wav in the output dir.
            candidates = sorted(df_output_dir.glob("*.wav"))

        if not candidates:
            raise FileNotFoundError(
                f"DeepFilterNet produced no output in {df_output_dir}. "
                f"Contents: {list(df_output_dir.iterdir())}"
            )

        # Use the first match (there should be exactly one).
        shutil.move(str(candidates[0]), str(output_file))

    def _apply_audio_filters(self, input_file: Path, output_file: Path) -> None:
        """Apply highpass, compression, and two-pass loudness normalization."""
        cfg = self.config.audio_filters

        base_filters = f"highpass=f={cfg.highpass_freq},{cfg.compressor_filter}"

        # Pass 1: Measure loudness.
        measured = self._measure_loudness(input_file, base_filters)

        # Pass 2: Apply filters with measured values (or single-pass fallback).
        if measured:
            loudnorm = (
                f"loudnorm=I={cfg.loudnorm_target}"
                f":TP={cfg.loudnorm_true_peak}"
                f":LRA={cfg.loudnorm_lra}"
                f":measured_I={measured['input_i']}"
                f":measured_TP={measured['input_tp']}"
                f":measured_LRA={measured['input_lra']}"
                f":measured_thresh={measured['input_thresh']}"
                f":linear=true"
            )
            logger.info(
                "Measured loudness: I={} LUFS, TP={} dBTP, LRA={}",
                measured["input_i"],
                measured["input_tp"],
                measured["input_lra"],
            )
        else:
            logger.warning("Could not parse loudnorm measurements; using single-pass.")
            loudnorm = cfg.loudnorm_filter

        full_filter = f"{base_filters},{loudnorm}"

        run_subprocess(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                input_file,
                "-af",
                full_filter,
                "-ar",
                str(self.config.sample_rate),
                output_file,
            ]
        )

    def _measure_loudness(self, input_file: Path, base_filters: str) -> dict[str, str] | None:
        """Run FFmpeg loudnorm first pass to measure loudness values.

        Returns a dict with measured values, or None if parsing fails.
        """
        cfg = self.config.audio_filters
        measure_filter = (
            f"{base_filters},"
            f"loudnorm=I={cfg.loudnorm_target}"
            f":TP={cfg.loudnorm_true_peak}"
            f":LRA={cfg.loudnorm_lra}"
            f":print_format=json"
        )

        # loudnorm prints its JSON to stderr.
        import sys

        result = run_subprocess(
            [
                self.ffmpeg,
                "-hide_banner",
                "-y",
                "-i",
                input_file,
                "-af",
                measure_filter,
                "-f",
                "null",
                # Cross-platform null device
                "NUL" if sys.platform == "win32" else "/dev/null",
            ],
            check=False,
        )

        # Parse the JSON block from stderr. FFmpeg outputs it at the end
        # of the loudnorm filter output.
        stderr = result.stdout + "\n" + result.stderr
        return self._parse_loudnorm_json(stderr)

    @staticmethod
    def _parse_loudnorm_json(output: str) -> dict[str, str] | None:
        """Extract loudnorm measurement values from FFmpeg output.

        FFmpeg prints a JSON block like:
        {
            "input_i" : "-24.50",
            "input_tp" : "-3.20",
            "input_lra" : "8.10",
            "input_thresh" : "-35.00",
            ...
        }
        """
        json_match = re.search(
            r'\{[^{}]*"input_i"\s*:\s*"[^"]*"[^{}]*\}',
            output,
            re.DOTALL,
        )
        if not json_match:
            return None

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return None

        required = ["input_i", "input_tp", "input_lra", "input_thresh"]
        if all(k in data for k in required):
            return {k: data[k] for k in required}
        return None

    def _encode_audio(self, input_file: Path, output_file: Path) -> None:
        """Encode processed audio to AAC."""
        run_subprocess(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                input_file,
                "-c:a",
                "aac",
                "-b:a",
                self.config.audio_bitrate,
                "-ar",
                str(self.config.sample_rate),
                output_file,
            ]
        )

    def _mux_video(self, video_source: Path, audio_source: Path, output: Path) -> None:
        """Mux original video with processed audio."""
        run_subprocess(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                video_source,
                "-i",
                audio_source,
                "-c:v",
                self.config.video_codec,
                "-c:a",
                "copy",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-movflags",
                "+faststart",
                output,
            ]
        )
