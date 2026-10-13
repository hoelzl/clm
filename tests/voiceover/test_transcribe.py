"""Tests for the transcription module."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.voiceover.transcribe import (
    WHISPER_SAMPLE_RATE,
    FasterWhisperBackend,
    Transcript,
    TranscriptSegment,
    _transcribe_in_subprocess,
    extract_audio,
    load_whisper_audio,
    transcribe_video,
)


def _write_wav(
    path: Path,
    samples: list[int],
    *,
    rate: int = WHISPER_SAMPLE_RATE,
    channels: int = 1,
) -> Path:
    """Write int16 *samples* (interleaved when channels > 1) as a PCM WAV."""
    np = pytest.importorskip("numpy")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(np.asarray(samples, dtype="<i2").tobytes())
    return path


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
    def test_transcribe_returns_transcript(self, mock_get_model, tmp_path):
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
        transcript = backend.transcribe(_write_wav(tmp_path / "test.wav", [0] * 160), language="de")

        assert len(transcript.segments) == 2
        assert transcript.segments[0].text == "Hallo, das ist ein Test."
        assert transcript.segments[1].text == "Noch ein Satz."
        assert transcript.language == "de"
        assert transcript.duration == 5.0

    @patch("clm.voiceover.transcribe.FasterWhisperBackend._get_model")
    def test_passes_decoded_samples_not_a_path(self, mock_get_model, tmp_path):
        """#1088: given a path, faster-whisper decodes with PyAV, whose 19.x
        release removed the ``metadata_errors`` keyword faster-whisper passes.
        The backend must hand over the decoded array instead."""
        np = pytest.importorskip("numpy")
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([]), MagicMock(language="en", duration=0.0))
        mock_get_model.return_value = mock_model
        wav = _write_wav(tmp_path / "a.wav", [0, 16384, -32768])

        FasterWhisperBackend(model_size="tiny").transcribe(wav, language="en")

        (audio,), _ = mock_model.transcribe.call_args
        assert isinstance(audio, np.ndarray)
        assert audio.dtype == np.float32
        assert audio.tolist() == [0.0, 0.5, -1.0]


class TestLoadWhisperAudio:
    def test_reads_extract_audio_wav_without_ffmpeg(self, tmp_path):
        np = pytest.importorskip("numpy")
        wav = _write_wav(tmp_path / "a.wav", [0, 16384, -32768, 32767])

        with patch("clm.voiceover.transcribe.subprocess.run") as run:
            audio = load_whisper_audio(wav)

        run.assert_not_called()
        assert audio.dtype == np.float32
        # Same scaling as faster_whisper.decode_audio: int16 / 32768.
        assert audio.tolist() == [0.0, 0.5, -1.0, 32767 / 32768]

    @pytest.mark.parametrize(
        ("rate", "channels"),
        [(44100, 1), (WHISPER_SAMPLE_RATE, 2)],
        ids=["other-rate", "stereo"],
    )
    def test_other_wav_shapes_go_through_ffmpeg(self, tmp_path, rate, channels):
        np = pytest.importorskip("numpy")
        wav = _write_wav(tmp_path / "a.wav", [0] * 8, rate=rate, channels=channels)
        pcm = np.asarray([16384, -16384], dtype="<i2").tobytes()

        with patch(
            "clm.voiceover.transcribe.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, pcm, b""),
        ) as run:
            audio = load_whisper_audio(wav)

        cmd = run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert cmd[cmd.index("-ar") + 1] == str(WHISPER_SAMPLE_RATE)
        assert cmd[cmd.index("-ac") + 1] == "1"
        assert cmd[cmd.index("-f") + 1] == "s16le"
        assert audio.tolist() == [0.5, -0.5]

    def test_non_wav_input_goes_through_ffmpeg(self, tmp_path):
        pytest.importorskip("numpy")
        mp3 = tmp_path / "a.mp3"
        mp3.write_bytes(b"ID3\x04\x00not really an mp3")

        with patch(
            "clm.voiceover.transcribe.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"\x00\x40", b""),
        ) as run:
            audio = load_whisper_audio(mp3)

        assert str(mp3) in run.call_args[0][0]
        assert audio.tolist() == [0.5]

    def test_ffmpeg_failure_raises(self, tmp_path):
        pytest.importorskip("numpy")
        bad = tmp_path / "a.mp3"
        bad.write_bytes(b"garbage")

        with patch(
            "clm.voiceover.transcribe.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, b"", b"Invalid data found"),
        ):
            with pytest.raises(RuntimeError, match="Invalid data found"):
                load_whisper_audio(bad)

    def test_missing_file_raises_file_not_found(self, tmp_path):
        pytest.importorskip("numpy")
        with pytest.raises(FileNotFoundError):
            load_whisper_audio(tmp_path / "missing.wav")

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
    def test_real_ffmpeg_resamples_to_16k_mono(self, tmp_path):
        np = pytest.importorskip("numpy")
        rate = 44100
        t = np.arange(rate) / rate  # one second
        tone = (np.sin(2 * np.pi * 440 * t) * 8000).astype("<i2")
        stereo = np.repeat(tone, 2)  # identical left/right channels
        wav = _write_wav(tmp_path / "tone.wav", stereo.tolist(), rate=rate, channels=2)

        audio = load_whisper_audio(wav)

        assert abs(len(audio) - WHISPER_SAMPLE_RATE) <= 32
        assert 0.2 < float(np.abs(audio).max()) < 0.3  # 8000 / 32768 ~ 0.244

    def test_matches_faster_whisper_decoder(self, tmp_path):
        """Parity with ``faster_whisper.decode_audio`` where it still works
        (PyAV < 19): the model sees exactly the samples it saw before."""
        np = pytest.importorskip("numpy")
        fw_audio = pytest.importorskip("faster_whisper.audio")
        rng = np.random.default_rng(1088)
        samples = rng.integers(-32768, 32767, size=4000).tolist()
        wav = _write_wav(tmp_path / "noise.wav", samples)
        try:
            expected = fw_audio.decode_audio(str(wav))
        except TypeError as exc:  # PyAV >= 19: the very bug this module avoids
            pytest.skip(f"faster-whisper's decoder is broken here: {exc}")

        assert np.array_equal(load_whisper_audio(wav), expected)


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

    @patch("clm.voiceover.transcribe._transcribe_in_subprocess")
    @patch("clm.voiceover.transcribe.extract_audio")
    def test_uses_subprocess_when_no_backend(self, mock_extract, mock_subproc, tmp_path):
        """Without a custom backend, transcribe_video delegates to subprocess."""
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        audio = tmp_path / "audio.wav"
        audio.write_text("fake audio")
        mock_extract.return_value = audio

        expected = Transcript(
            segments=[TranscriptSegment(0.0, 1.0, "Test")],
            language="de",
            duration=1.0,
        )
        mock_subproc.return_value = expected

        result = transcribe_video(video, language="de", device="cpu")

        mock_subproc.assert_called_once_with(
            audio,
            backend_name="faster-whisper",
            model_size="large-v3",
            device="cpu",
            language="de",
        )
        assert result is expected

    @patch("clm.voiceover.transcribe._transcribe_in_subprocess")
    @patch("clm.voiceover.transcribe.extract_audio")
    def test_does_not_use_subprocess_with_custom_backend(
        self, mock_extract, mock_subproc, tmp_path
    ):
        """With a custom backend, transcribe_video runs in-process."""
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        audio = tmp_path / "audio.wav"
        audio.write_text("fake audio")
        mock_extract.return_value = audio

        mock_backend = MagicMock()
        mock_backend.transcribe.return_value = Transcript(segments=[], language="en", duration=0.0)

        transcribe_video(video, backend=mock_backend)

        mock_subproc.assert_not_called()
        mock_backend.transcribe.assert_called_once()


class TestTranscriptSerialization:
    def test_segment_roundtrip(self):
        seg = TranscriptSegment(start=1.5, end=3.0, text="Hallo Welt")
        restored = TranscriptSegment.from_dict(seg.to_dict())
        assert restored == seg

    def test_transcript_roundtrip(self):
        transcript = Transcript(
            segments=[
                TranscriptSegment(0.0, 1.0, "Hello"),
                TranscriptSegment(1.0, 3.5, "world"),
            ],
            language="en",
            duration=3.5,
        )
        restored = Transcript.from_dict(transcript.to_dict())
        assert restored.segments == transcript.segments
        assert restored.language == transcript.language
        assert restored.duration == transcript.duration

    def test_empty_transcript_roundtrip(self):
        transcript = Transcript(segments=[], language="de", duration=0.0)
        restored = Transcript.from_dict(transcript.to_dict())
        assert restored.segments == []
        assert restored.language == "de"

    def test_to_dict_json_serializable(self):
        transcript = Transcript(
            segments=[TranscriptSegment(0.0, 1.0, "Test äöü")],
            language="de",
            duration=1.0,
        )
        # Should not raise
        text = json.dumps(transcript.to_dict(), ensure_ascii=False)
        data = json.loads(text)
        assert data["segments"][0]["text"] == "Test äöü"


def _make_subprocess_result(returncode=0, stderr=""):
    """Create a mock subprocess.CompletedProcess."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = ""
    result.stderr = stderr
    return result


class TestTranscribeInSubprocess:
    """Tests for subprocess isolation of transcription."""

    def _write_result_json(self, cmd, **kwargs):
        """Side-effect for subprocess.run that writes transcript JSON."""
        # Extract the output path from the command (5th element, index 4)
        output_path = Path(cmd[4])
        transcript = Transcript(
            segments=[TranscriptSegment(0.0, 2.0, "Hallo")],
            language="de",
            duration=2.0,
        )
        output_path.write_text(json.dumps(transcript.to_dict()), encoding="utf-8")
        return _make_subprocess_result(returncode=0)

    def _write_result_json_then_crash(self, cmd, **kwargs):
        """Side-effect: writes JSON but returns exit code 127 (CUDA crash)."""
        output_path = Path(cmd[4])
        transcript = Transcript(
            segments=[TranscriptSegment(0.0, 2.0, "Hallo")],
            language="de",
            duration=2.0,
        )
        output_path.write_text(json.dumps(transcript.to_dict()), encoding="utf-8")
        return _make_subprocess_result(returncode=127)

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        mock_run.side_effect = self._write_result_json
        audio = tmp_path / "audio.wav"
        audio.write_text("fake")

        result = _transcribe_in_subprocess(
            audio,
            backend_name="faster-whisper",
            model_size="tiny",
            device="cpu",
            language="de",
        )

        assert len(result.segments) == 1
        assert result.segments[0].text == "Hallo"
        assert result.language == "de"

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_tolerates_exit_code_127(self, mock_run, tmp_path):
        """Exit code 127 (CUDA crash on shutdown) is tolerated when JSON exists."""
        mock_run.side_effect = self._write_result_json_then_crash
        audio = tmp_path / "audio.wav"
        audio.write_text("fake")

        result = _transcribe_in_subprocess(
            audio,
            backend_name="faster-whisper",
            model_size="tiny",
            device="cpu",
            language="de",
        )

        assert len(result.segments) == 1
        assert result.segments[0].text == "Hallo"

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_raises_on_failure_without_output(self, mock_run, tmp_path):
        """Actual failure (no JSON written) raises RuntimeError."""
        mock_run.return_value = _make_subprocess_result(
            returncode=1, stderr="ImportError: No module named faster_whisper"
        )
        audio = tmp_path / "audio.wav"
        audio.write_text("fake")

        with pytest.raises(RuntimeError, match="Transcription subprocess failed"):
            _transcribe_in_subprocess(
                audio,
                backend_name="faster-whisper",
                model_size="tiny",
                device="cpu",
                language="de",
            )

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_raises_on_invalid_json(self, mock_run, tmp_path):
        """Corrupted JSON (e.g., crash mid-write) raises RuntimeError."""

        def write_bad_json(cmd, **kwargs):
            output_path = Path(cmd[4])
            output_path.write_text('{"segments": [', encoding="utf-8")
            return _make_subprocess_result(returncode=127)

        mock_run.side_effect = write_bad_json
        audio = tmp_path / "audio.wav"
        audio.write_text("fake")

        with pytest.raises(RuntimeError, match="invalid output"):
            _transcribe_in_subprocess(
                audio,
                backend_name="faster-whisper",
                model_size="tiny",
                device="cpu",
                language="de",
            )

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_cleans_up_result_file(self, mock_run, tmp_path):
        """The temporary JSON file is cleaned up after reading."""
        written_paths = []

        def track_and_write(cmd, **kwargs):
            output_path = Path(cmd[4])
            written_paths.append(output_path)
            transcript = Transcript(segments=[], language="en", duration=0.0)
            output_path.write_text(json.dumps(transcript.to_dict()), encoding="utf-8")
            return _make_subprocess_result(returncode=0)

        mock_run.side_effect = track_and_write
        audio = tmp_path / "audio.wav"
        audio.write_text("fake")

        _transcribe_in_subprocess(
            audio,
            backend_name="faster-whisper",
            model_size="tiny",
            device="cpu",
            language=None,
        )

        assert len(written_paths) == 1
        assert not written_paths[0].exists()

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_passes_correct_command(self, mock_run, tmp_path):
        """Verify the subprocess command includes all expected arguments."""
        mock_run.side_effect = self._write_result_json
        audio = tmp_path / "audio.wav"
        audio.write_text("fake")

        _transcribe_in_subprocess(
            audio,
            backend_name="granite",
            model_size="large-v3",
            device="cuda",
            language="en",
        )

        cmd = mock_run.call_args[0][0]
        assert "clm.voiceover._transcribe_worker" in cmd
        assert str(audio) in cmd
        assert "--backend" in cmd
        idx = cmd.index("--backend")
        assert cmd[idx + 1] == "granite"
        assert "--device" in cmd
        idx = cmd.index("--device")
        assert cmd[idx + 1] == "cuda"
        assert "--language" in cmd
        idx = cmd.index("--language")
        assert cmd[idx + 1] == "en"

    @patch("clm.voiceover.transcribe.subprocess.run")
    def test_omits_language_when_none(self, mock_run, tmp_path):
        """When language is None, --language flag is omitted."""
        mock_run.side_effect = self._write_result_json
        audio = tmp_path / "audio.wav"
        audio.write_text("fake")

        _transcribe_in_subprocess(
            audio,
            backend_name="faster-whisper",
            model_size="tiny",
            device="cpu",
            language=None,
        )

        cmd = mock_run.call_args[0][0]
        assert "--language" not in cmd


class TestTranscribeWorker:
    """Tests for the _transcribe_worker module."""

    def test_main_writes_json(self, tmp_path):
        """Worker main() creates correct JSON output."""
        audio = tmp_path / "audio.wav"
        audio.write_text("fake")
        output = tmp_path / "result.json"

        expected = Transcript(
            segments=[TranscriptSegment(0.0, 1.0, "Test")],
            language="de",
            duration=1.0,
        )

        with patch("clm.voiceover.transcribe.create_backend") as mock_create:
            mock_backend = MagicMock()
            mock_backend.transcribe.return_value = expected
            mock_create.return_value = mock_backend

            from clm.voiceover._transcribe_worker import main

            main([str(audio), str(output), "--backend", "faster-whisper", "--language", "de"])

        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        restored = Transcript.from_dict(data)
        assert len(restored.segments) == 1
        assert restored.segments[0].text == "Test"
        assert restored.language == "de"
