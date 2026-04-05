"""Tests for :class:`OnnxAudioFirstBackend`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.workflow.backends.base import ProcessingBackend
from clm.recordings.workflow.backends.onnx import OnnxAudioFirstBackend
from clm.recordings.workflow.directories import ensure_root, to_process_dir
from clm.recordings.workflow.jobs import (
    JobState,
    ProcessingJob,
    ProcessingOptions,
)


class _RecordingContext:
    def __init__(self, work_dir: Path) -> None:
        self.reports: list[tuple[JobState, float, str]] = []
        self._work_dir = work_dir

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    def report(self, job: ProcessingJob) -> None:
        self.reports.append((job.state, job.progress, job.message))


def _setup_tree(tmp_path: Path) -> tuple[Path, Path]:
    ensure_root(tmp_path)
    topic_dir = to_process_dir(tmp_path) / "py/week01"
    topic_dir.mkdir(parents=True, exist_ok=True)
    raw = topic_dir / "lecture--RAW.mp4"
    raw.write_bytes(b"fake video")
    return tmp_path, raw


class TestOnnxCapabilities:
    def test_capabilities(self, tmp_path: Path):
        backend = OnnxAudioFirstBackend(root_dir=tmp_path)
        caps = backend.capabilities
        assert caps.name == "onnx"
        assert caps.is_synchronous is True
        assert caps.requires_internet is False
        assert caps.requires_api_key is False
        assert caps.video_in_video_out is False
        assert ".mp4" in caps.supported_input_extensions

    def test_is_processing_backend(self, tmp_path: Path):
        backend = OnnxAudioFirstBackend(root_dir=tmp_path)
        assert isinstance(backend, ProcessingBackend)


class TestOnnxAcceptsFile:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("lecture--RAW.mp4", True),
            ("lecture--RAW.MP4", True),
            ("lecture--RAW.mkv", True),
            ("lecture--RAW.mov", True),
            ("lecture.mp4", False),  # missing --RAW
            ("lecture--RAW.wav", False),  # wrong extension (audio)
            ("lecture--raw.mp4", False),  # lowercase --raw doesn't match
            ("lecture.wav", False),
        ],
    )
    def test_accepts_file(self, tmp_path: Path, name: str, expected: bool):
        backend = OnnxAudioFirstBackend(root_dir=tmp_path)
        assert backend.accepts_file(Path(name)) is expected

    def test_custom_raw_suffix(self, tmp_path: Path):
        backend = OnnxAudioFirstBackend(root_dir=tmp_path, raw_suffix="--IN")
        assert backend.accepts_file(Path("lecture--IN.mp4")) is True
        assert backend.accepts_file(Path("lecture--RAW.mp4")) is False


class TestOnnxProduceAudio:
    @patch("clm.recordings.processing.utils.run_subprocess")
    @patch("clm.recordings.processing.utils.run_onnx_denoise")
    @patch("clm.recordings.processing.utils.get_audio_duration", return_value=120.0)
    @patch("clm.recordings.processing.utils.find_ffprobe", return_value=Path("/usr/bin/ffprobe"))
    @patch("clm.recordings.processing.utils.find_ffmpeg", return_value=Path("/usr/bin/ffmpeg"))
    def test_produce_audio_runs_pipeline_steps(
        self,
        mock_ffmpeg: MagicMock,
        mock_ffprobe: MagicMock,
        mock_duration: MagicMock,
        mock_denoise: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ):
        root, raw = _setup_tree(tmp_path)
        backend = OnnxAudioFirstBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)

        job = ProcessingJob(
            backend_name="onnx",
            raw_path=raw,
            final_path=root / "final" / "py/week01" / "lecture.mp4",
            relative_dir=Path("py/week01"),
            state=JobState.PROCESSING,
        )
        output_wav = raw.with_name(f"{raw.stem}.wav")

        backend._produce_audio(
            raw,
            output_wav,
            options=ProcessingOptions(),
            ctx=ctx,
            job=job,
        )

        # Extract + filters = two run_subprocess calls.
        assert mock_subprocess.call_count == 2
        # Denoise called once.
        mock_denoise.assert_called_once()

        # Progress reports for extract / denoise / filter show forward motion.
        progresses = [p for (_, p, _) in ctx.reports]
        assert progresses == sorted(progresses)
        # And each step has a non-empty message.
        messages = [m for (_, _, m) in ctx.reports]
        assert any("Extracting" in m for m in messages)
        assert any("ONNX" in m or "noise" in m for m in messages)
        assert any("filters" in m.lower() for m in messages)

    @patch("clm.recordings.processing.utils.run_subprocess")
    @patch("clm.recordings.processing.utils.run_onnx_denoise")
    @patch("clm.recordings.processing.utils.get_audio_duration", return_value=60.0)
    @patch("clm.recordings.processing.utils.find_ffprobe", return_value=Path("/usr/bin/ffprobe"))
    @patch("clm.recordings.processing.utils.find_ffmpeg", return_value=Path("/usr/bin/ffmpeg"))
    def test_produce_audio_uses_custom_config(
        self,
        mock_ffmpeg: MagicMock,
        mock_ffprobe: MagicMock,
        mock_duration: MagicMock,
        mock_denoise: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ):
        from clm.recordings.processing.config import PipelineConfig

        root, raw = _setup_tree(tmp_path)
        config = PipelineConfig(denoise_atten_lim=50.0, sample_rate=44100)
        backend = OnnxAudioFirstBackend(root_dir=root, config=config)
        ctx = _RecordingContext(tmp_path)

        job = ProcessingJob(
            backend_name="onnx",
            raw_path=raw,
            final_path=root / "final" / "py/week01" / "lecture.mp4",
            relative_dir=Path("py/week01"),
            state=JobState.PROCESSING,
        )
        backend._produce_audio(
            raw,
            raw.with_name(f"{raw.stem}.wav"),
            options=ProcessingOptions(),
            ctx=ctx,
            job=job,
        )

        assert mock_denoise.call_args.kwargs["atten_lim_db"] == 50.0
        extract_args = [str(a) for a in mock_subprocess.call_args_list[0][0][0]]
        assert "44100" in extract_args


class TestOnnxEndToEndSubmit:
    @patch("clm.recordings.processing.utils.run_subprocess")
    @patch("clm.recordings.processing.utils.run_onnx_denoise")
    @patch("clm.recordings.processing.utils.get_audio_duration", return_value=60.0)
    @patch("clm.recordings.processing.utils.find_ffprobe", return_value=Path("/usr/bin/ffprobe"))
    @patch("clm.recordings.processing.utils.find_ffmpeg", return_value=Path("/usr/bin/ffmpeg"))
    def test_submit_flows_through_template_method(
        self,
        mock_ffmpeg: MagicMock,
        mock_ffprobe: MagicMock,
        mock_duration: MagicMock,
        mock_denoise: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The submit() Template Method calls _produce_audio + assemble."""
        root, raw = _setup_tree(tmp_path)

        # Fake the mux so we don't need a real ffmpeg.
        def fake_mux(ffmpeg, video, audio, output):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"muxed")

        monkeypatch.setattr(
            "clm.recordings.workflow.assembler.mux_video_audio",
            fake_mux,
        )
        monkeypatch.setattr(
            "clm.recordings.workflow.assembler.find_ffmpeg",
            lambda: Path("/fake/ffmpeg"),
        )

        backend = OnnxAudioFirstBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        final = root / "final" / "py/week01" / "lecture.mp4"

        job = backend.submit(
            raw,
            final,
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.state == JobState.COMPLETED
        assert job.progress == 1.0
        assert final.exists()
