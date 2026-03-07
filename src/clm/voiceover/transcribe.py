"""Audio transcription with timestamps using Whisper models.

This module extracts audio from video files and transcribes it using
speech recognition models. The primary backend is faster-whisper
(CTranslate2-based), with a pluggable interface for future backends.

The transcription produces timestamped segments that are later aligned
to slides by the aligner module.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptSegment:
    """A single segment of transcribed speech with timestamps."""

    start: float  # seconds
    end: float  # seconds
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2.0


@dataclass
class Transcript:
    """Complete transcript of a video's audio track."""

    segments: list[TranscriptSegment]
    language: str  # detected or specified language code
    duration: float  # total audio duration in seconds

    @property
    def full_text(self) -> str:
        return " ".join(seg.text for seg in self.segments)


class TranscriptionBackend(Protocol):
    """Protocol for transcription backends.

    Implement this to add support for alternative ASR engines
    (e.g., OpenAI Whisper API, whisper.cpp, etc.).
    """

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> Transcript: ...


class FasterWhisperBackend:
    """Transcription backend using faster-whisper (CTranslate2).

    This is the default and recommended backend. It provides:
    - Same quality as OpenAI Whisper (large-v3 model)
    - 4-8x faster inference via CTranslate2
    - No PyTorch dependency
    - Segment-level timestamps out of the box
    """

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "auto",
        compute_type: str = "default",
    ):
        """Initialize the faster-whisper backend.

        Args:
            model_size: Whisper model size. Options: "tiny", "base", "small",
                "medium", "large-v2", "large-v3" (default).
            device: Device to use: "auto" (default), "cpu", or "cuda".
            compute_type: Compute type: "default", "int8", "float16", etc.
                "default" selects automatically based on device.
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _get_model(self):
        """Lazy-load the model on first use."""
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]

            logger.info(
                "Loading Whisper model '%s' (device=%s, compute=%s)...",
                self.model_size,
                self.device,
                self.compute_type,
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info("Model loaded.")
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> Transcript:
        """Transcribe an audio file.

        Args:
            audio_path: Path to the audio file (WAV, MP3, etc.).
            language: Language code (e.g., "de", "en"). If None, the
                language is auto-detected.

        Returns:
            Transcript with timestamped segments.
        """
        model = self._get_model()

        logger.info("Transcribing %s (language=%s)...", audio_path.name, language or "auto")

        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
        )

        segments: list[TranscriptSegment] = []
        for segment in segments_iter:
            text = segment.text.strip()
            if text:
                segments.append(
                    TranscriptSegment(
                        start=segment.start,
                        end=segment.end,
                        text=text,
                    )
                )

        detected_lang = info.language if hasattr(info, "language") else (language or "unknown")
        duration = (
            info.duration if hasattr(info, "duration") else (segments[-1].end if segments else 0.0)
        )

        logger.info(
            "Transcription complete: %d segments, %.1fs duration, language=%s",
            len(segments),
            duration,
            detected_lang,
        )

        return Transcript(
            segments=segments,
            language=detected_lang,
            duration=duration,
        )


def extract_audio(
    video_path: str | Path,
    output_path: str | Path | None = None,
    *,
    sample_rate: int = 16000,
) -> Path:
    """Extract audio from a video file using ffmpeg.

    Args:
        video_path: Path to the video file.
        output_path: Path for the output WAV file. If None, a temporary
            file is created.
        sample_rate: Audio sample rate in Hz (default: 16000, which is
            what Whisper expects).

    Returns:
        Path to the extracted audio file.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        output_path = Path(tmp.name)
        tmp.close()
    else:
        output_path = Path(output_path)

    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vn",  # no video
        "-acodec",
        "pcm_s16le",  # 16-bit PCM
        "-ar",
        str(sample_rate),  # sample rate
        "-ac",
        "1",  # mono
        "-y",  # overwrite
        str(output_path),
    ]

    logger.info("Extracting audio: %s -> %s", video_path.name, output_path.name)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr[:500]}")

    logger.info("Audio extracted: %s", output_path)
    return output_path


def transcribe_video(
    video_path: str | Path,
    *,
    language: str | None = None,
    backend: TranscriptionBackend | None = None,
    model_size: str = "large-v3",
    keep_audio: bool = False,
) -> Transcript:
    """Transcribe a video file end-to-end.

    This is the main entry point for transcription. It extracts the audio
    track and runs it through the specified backend.

    Args:
        video_path: Path to the video file.
        language: Language hint (e.g., "de", "en"). None for auto-detect.
        backend: Transcription backend to use. If None, uses
            FasterWhisperBackend with the given model_size.
        model_size: Whisper model size (only used if backend is None).
        keep_audio: If True, keep the extracted audio file. If False,
            delete it after transcription.

    Returns:
        Transcript with timestamped segments.
    """
    video_path = Path(video_path)

    if backend is None:
        backend = FasterWhisperBackend(model_size=model_size)

    audio_path = extract_audio(video_path)

    try:
        transcript = backend.transcribe(audio_path, language=language)
    finally:
        if not keep_audio:
            try:
                audio_path.unlink()
            except OSError:
                pass

    return transcript
