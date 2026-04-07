"""Audio transcription with timestamps using speech recognition models.

This module extracts audio from video files and transcribes it using
speech recognition models. The primary backend is faster-whisper
(CTranslate2-based), with pluggable backends for Cohere Transcribe
and IBM Granite Speech.

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


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


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
            from faster_whisper import WhisperModel

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


def _torch_device(device: str) -> str:
    """Resolve device string, handling 'auto' via torch.cuda.is_available()."""
    if device != "auto":
        return device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class CohereTranscribeBackend:
    """Transcription backend using Cohere Transcribe (cohere-transcribe-03-2026).

    2B parameter conformer model, Apache 2.0 licensed.
    Supports 14 languages including German and English.

    Install: pip install clm[voiceover-cohere]
    """

    MODEL_ID = "CohereLabs/cohere-transcribe-03-2026"

    def __init__(self, *, device: str = "auto"):
        self.device = device
        self._model = None
        self._processor = None
        self._resolved_device: str | None = None

    def _get_model(self):
        """Lazy-load the model on first use."""
        if self._model is None:
            from transformers import AutoProcessor

            try:
                from transformers import (  # type: ignore[attr-defined]
                    CohereAsrForConditionalGeneration,
                )
            except ImportError:
                raise ImportError(
                    "CohereAsrForConditionalGeneration requires transformers>=5.4.0. "
                    "Please upgrade: pip install 'transformers>=5.4.0'"
                ) from None

            self._resolved_device = _torch_device(self.device)
            logger.info(
                "Loading Cohere Transcribe model (device=%s)...",
                self._resolved_device,
            )
            self._processor = AutoProcessor.from_pretrained(self.MODEL_ID)
            self._model = CohereAsrForConditionalGeneration.from_pretrained(self.MODEL_ID).to(
                self._resolved_device
            )
            logger.info("Model loaded on %s.", self._resolved_device)
        return self._model, self._processor

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> Transcript:
        import soundfile as sf
        import torch

        model, processor = self._get_model()
        audio, sr = sf.read(str(audio_path))

        inputs = processor(audio, sampling_rate=sr, return_tensors="pt", language=language)
        inputs = inputs.to(self._resolved_device, dtype=model.dtype)

        with torch.no_grad():
            output = model.generate(**inputs, max_new_tokens=4096)

        text = processor.decode(output[0], skip_special_tokens=True)
        duration = len(audio) / sr

        # Cohere model returns full text without per-segment timestamps;
        # create a single segment spanning the audio
        segments = [TranscriptSegment(start=0.0, end=duration, text=text.strip())]

        logger.info(
            "Transcription complete: %d chars, %.1fs duration",
            len(text),
            duration,
        )

        return Transcript(
            segments=segments,
            language=language or "unknown",
            duration=duration,
        )


class GraniteSpeechBackend:
    """Transcription backend using IBM Granite 4.0 1B Speech.

    1B parameter speech-language model, Apache 2.0 licensed.
    Compact model designed for edge/resource-constrained devices.

    Install: pip install clm[voiceover-granite]
    """

    MODEL_ID = "ibm-granite/granite-4.0-1b-speech"

    def __init__(self, *, device: str = "auto"):
        self.device = device
        self._model = None
        self._processor = None
        self._resolved_device: str | None = None

    def _get_model(self):
        """Lazy-load the model on first use."""
        if self._model is None:
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

            self._resolved_device = _torch_device(self.device)
            logger.info(
                "Loading Granite Speech model (device=%s)...",
                self._resolved_device,
            )
            self._processor = AutoProcessor.from_pretrained(self.MODEL_ID, trust_remote_code=True)
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                self.MODEL_ID, trust_remote_code=True
            ).to(self._resolved_device)
            logger.info("Model loaded on %s.", self._resolved_device)
        return self._model, self._processor

    TRANSCRIBE_PROMPT = "<|audio|> Transcribe this audio."

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> Transcript:
        import soundfile as sf
        import torch

        model, processor = self._get_model()
        audio, sr = sf.read(str(audio_path))

        inputs = processor(
            text=self.TRANSCRIBE_PROMPT,
            audio=audio,
            sampling_rate=sr,
            return_tensors="pt",
        )
        inputs = inputs.to(self._resolved_device)

        with torch.no_grad():
            output = model.generate(**inputs, max_new_tokens=4096)

        text = processor.batch_decode(output, skip_special_tokens=True)[0]
        # Strip the prompt prefix if echoed back
        if text.startswith(self.TRANSCRIBE_PROMPT):
            text = text[len(self.TRANSCRIBE_PROMPT) :]
        duration = len(audio) / sr

        # Granite returns full text without per-segment timestamps;
        # create a single segment spanning the audio
        segments = [TranscriptSegment(start=0.0, end=duration, text=text.strip())]

        logger.info(
            "Transcription complete: %d chars, %.1fs duration",
            len(text),
            duration,
        )

        return Transcript(
            segments=segments,
            language=language or "unknown",
            duration=duration,
        )


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

BACKENDS = ("faster-whisper", "cohere", "granite")


def create_backend(
    name: str = "faster-whisper",
    *,
    model_size: str = "large-v3",
    device: str = "auto",
) -> TranscriptionBackend:
    """Create a transcription backend by name.

    Args:
        name: Backend name. One of "faster-whisper" (default),
            "cohere", or "granite".
        model_size: Model size (only used by faster-whisper).
        device: Device: "auto", "cpu", or "cuda".

    Returns:
        A TranscriptionBackend instance.

    Raises:
        ValueError: If the backend name is unknown.
    """
    if name == "faster-whisper":
        return FasterWhisperBackend(model_size=model_size, device=device)
    elif name == "cohere":
        return CohereTranscribeBackend(device=device)
    elif name == "granite":
        return GraniteSpeechBackend(device=device)
    else:
        raise ValueError(f"Unknown backend: {name!r}. Available: {', '.join(BACKENDS)}")


# ---------------------------------------------------------------------------
# Audio extraction and high-level API
# ---------------------------------------------------------------------------


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
    backend_name: str = "faster-whisper",
    model_size: str = "large-v3",
    device: str = "auto",
    keep_audio: bool = False,
) -> Transcript:
    """Transcribe a video file end-to-end.

    This is the main entry point for transcription. It extracts the audio
    track and runs it through the specified backend.

    Args:
        video_path: Path to the video file.
        language: Language hint (e.g., "de", "en"). None for auto-detect.
        backend: Transcription backend to use. If provided, backend_name,
            model_size, and device are ignored.
        backend_name: Backend to create if ``backend`` is None.
            One of "faster-whisper", "cohere", or "granite".
        model_size: Whisper model size (only used by faster-whisper).
        device: Device: "auto", "cpu", or "cuda".
        keep_audio: If True, keep the extracted audio file. If False,
            delete it after transcription.

    Returns:
        Transcript with timestamped segments.
    """
    video_path = Path(video_path)

    if backend is None:
        backend = create_backend(backend_name, model_size=model_size, device=device)

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
