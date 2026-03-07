"""Tests for the transcription module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.voiceover.transcribe import (
    FasterWhisperBackend,
    Transcript,
    TranscriptSegment,
    extract_audio,
    transcribe_video,
)


class TestTranscriptSegment:
    def test_duration(self):
        seg = TranscriptSegment(start=1.0, end=3.5, text="hello")
        assert seg.duration == pytest.approx(2.5)

    def test_midpoint(self):
        seg = TranscriptSegment(start=10.0, end=20.0, text="hello")
        assert seg.midpoint == pytest.approx(15.0)

    def test_frozen(self):
        seg = TranscriptSegment(start=1.0, end=2.0, text="hello")
        with pytest.raises(AttributeError):
            seg.start = 5.0  # type: ignore[misc]


class TestTranscript:
    def test_full_text(self):
        transcript = Transcript(
            segments=[
                TranscriptSegment(0.0, 1.0, "Hello"),
                TranscriptSegment(1.0, 2.0, "world"),
                TranscriptSegment(2.0, 3.0, "test"),
            ],
            language="en",
            duration=3.0,
        )
        assert transcript.full_text == "Hello world test"

    def test_empty_transcript(self):
        transcript = Transcript(segments=[], language="de", duration=0.0)
        assert transcript.full_text == ""


class TestExtractAudio:
    def test_nonexistent_video_raises(self):
        with pytest.raises(FileNotFoundError, match="Video not found"):
            extract_audio("/nonexistent/video.mp4")

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_ffmpeg_failure_raises(self, mock_run, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")

        mock_run.return_value = MagicMock(returncode=1, stderr="error msg")

        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            extract_audio(video)

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_ffmpeg_called_correctly(self, mock_run, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        output = tmp_path / "out.wav"

        mock_run.return_value = MagicMock(returncode=0)

        result = extract_audio(video, output, sample_rate=16000)
        assert result == output

        # Check ffmpeg was called with the right args
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "ffmpeg"
        assert "-vn" in call_args
        assert "16000" in call_args
        assert str(video) in call_args
        assert str(output) in call_args


class TestFasterWhisperBackend:
    def test_lazy_model_loading(self):
        backend = FasterWhisperBackend(model_size="tiny")
        # Model should not be loaded yet
        assert backend._model is None

    @patch("clm.voiceover.transcribe.FasterWhisperBackend._get_model")
    def test_transcribe_returns_transcript(self, mock_get_model):
        # Mock the model's transcribe method
        mock_segment = MagicMock()
        mock_segment.start = 0.0
        mock_segment.end = 2.5
        mock_segment.text = " Hallo, das ist ein Test. "

        mock_segment2 = MagicMock()
        mock_segment2.start = 2.5
        mock_segment2.end = 5.0
        mock_segment2.text = " Noch ein Satz. "

        mock_info = MagicMock()
        mock_info.language = "de"
        mock_info.duration = 5.0

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            iter([mock_segment, mock_segment2]),
            mock_info,
        )
        mock_get_model.return_value = mock_model

        backend = FasterWhisperBackend(model_size="tiny")
        transcript = backend.transcribe(Path("test.wav"), language="de")

        assert len(transcript.segments) == 2
        assert transcript.segments[0].text == "Hallo, das ist ein Test."
        assert transcript.segments[1].text == "Noch ein Satz."
        assert transcript.language == "de"
        assert transcript.duration == 5.0


class TestTranscribeVideo:
    @patch("clm.voiceover.transcribe.extract_audio")
    def test_uses_custom_backend(self, mock_extract, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        audio = tmp_path / "audio.wav"
        audio.write_text("fake audio")
        mock_extract.return_value = audio

        mock_backend = MagicMock()
        mock_backend.transcribe.return_value = Transcript(
            segments=[TranscriptSegment(0.0, 1.0, "Test")],
            language="en",
            duration=1.0,
        )

        result = transcribe_video(video, language="en", backend=mock_backend)

        mock_backend.transcribe.assert_called_once_with(audio, language="en")
        assert result.segments[0].text == "Test"

    @patch("clm.voiceover.transcribe.extract_audio")
    def test_cleans_up_audio(self, mock_extract, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        audio = tmp_path / "audio.wav"
        audio.write_text("fake audio")
        mock_extract.return_value = audio

        mock_backend = MagicMock()
        mock_backend.transcribe.return_value = Transcript(
            segments=[],
            language="en",
            duration=0.0,
        )

        transcribe_video(video, backend=mock_backend, keep_audio=False)

        # Audio file should be deleted
        assert not audio.exists()

    @patch("clm.voiceover.transcribe.extract_audio")
    def test_keeps_audio_when_requested(self, mock_extract, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        audio = tmp_path / "audio.wav"
        audio.write_text("fake audio")
        mock_extract.return_value = audio

        mock_backend = MagicMock()
        mock_backend.transcribe.return_value = Transcript(
            segments=[],
            language="en",
            duration=0.0,
        )

        transcribe_video(video, backend=mock_backend, keep_audio=True)

        assert audio.exists()
