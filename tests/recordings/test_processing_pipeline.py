"""Tests for the recording processing pipeline.

These tests cover logic that doesn't require external binaries
(ffmpeg, deepFilter). Integration tests with real binaries are
marked with @pytest.mark.recordings.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.processing import pipeline as pipeline_module
from clm.recordings.processing.config import PipelineConfig
from clm.recordings.processing.pipeline import ProcessingPipeline, ProcessingResult


@pytest.fixture
def mock_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend ffmpeg/ffprobe exist so ProcessingPipeline() can be constructed."""
    monkeypatch.setattr(pipeline_module, "find_ffmpeg", lambda: Path("/mock/ffmpeg"))
    monkeypatch.setattr(pipeline_module, "find_ffprobe", lambda: Path("/mock/ffprobe"))


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestLoudnormParsing:
    """Test parsing of FFmpeg loudnorm measurement output."""

    def test_parses_typical_output(self):
        output = textwrap.dedent("""\
            [Parsed_loudnorm_2 @ 0x55f4c8]
            {
                "input_i" : "-24.50",
                "input_tp" : "-3.20",
                "input_lra" : "8.10",
                "input_thresh" : "-35.00",
                "output_i" : "-16.00",
                "output_tp" : "-1.50",
                "output_lra" : "7.80",
                "output_thresh" : "-26.50",
                "normalization_type" : "dynamic",
                "target_offset" : "0.00"
            }
        """)
        result = ProcessingPipeline._parse_loudnorm_json(output)
        assert result is not None
        assert result["input_i"] == "-24.50"
        assert result["input_tp"] == "-3.20"
        assert result["input_lra"] == "8.10"
        assert result["input_thresh"] == "-35.00"
        # Should not include output values.
        assert "output_i" not in result

    def test_parses_with_surrounding_noise(self):
        output = (
            "frame= 0 fps=0.0 q=0.0 size= 0kB time=00:00:00.00\n"
            "lots of other ffmpeg output here\n"
            "{\n"
            '    "input_i" : "-20.00",\n'
            '    "input_tp" : "-1.00",\n'
            '    "input_lra" : "5.00",\n'
            '    "input_thresh" : "-30.00",\n'
            '    "output_i" : "-16.00",\n'
            '    "output_tp" : "-1.50",\n'
            '    "output_lra" : "4.50",\n'
            '    "output_thresh" : "-26.50",\n'
            '    "normalization_type" : "dynamic",\n'
            '    "target_offset" : "0.00"\n'
            "}\n"
            "more output after\n"
        )
        result = ProcessingPipeline._parse_loudnorm_json(output)
        assert result is not None
        assert result["input_i"] == "-20.00"

    def test_returns_none_for_empty_output(self):
        assert ProcessingPipeline._parse_loudnorm_json("") is None

    def test_returns_none_for_no_json(self):
        output = "frame= 100 fps=50.0 q=0.0 size= 1234kB\n"
        assert ProcessingPipeline._parse_loudnorm_json(output) is None

    def test_returns_none_for_incomplete_json(self):
        output = '{"input_i": "-24.50", "input_tp": "-3.20"}'
        assert ProcessingPipeline._parse_loudnorm_json(output) is None


class TestProcessingResult:
    def test_model_dump(self):
        r = ProcessingResult(
            input_file=Path("/in/test.mkv"),
            output_file=Path("/out/test.mp4"),
            success=True,
            duration_seconds=120.5,
        )
        d = r.model_dump()
        assert d["success"] is True
        assert d["duration_seconds"] == 120.5
        assert d["error"] is None

    def test_failed_result(self):
        r = ProcessingResult(
            input_file=Path("/in/test.mkv"),
            output_file=Path("/out/test.mp4"),
            success=False,
            error="Something went wrong",
        )
        assert not r.success
        assert r.error == "Something went wrong"
        assert r.duration_seconds == 0.0


class TestProcessingPipelineInit:
    def test_locates_binaries(self, mock_binaries: None):
        pipeline = ProcessingPipeline()
        assert pipeline.ffmpeg == Path("/mock/ffmpeg")
        assert pipeline.ffprobe == Path("/mock/ffprobe")
        assert isinstance(pipeline.config, PipelineConfig)

    def test_accepts_custom_config(self, mock_binaries: None):
        cfg = PipelineConfig(keep_temp=True, sample_rate=44100)
        pipeline = ProcessingPipeline(config=cfg)
        assert pipeline.config.keep_temp is True
        assert pipeline.config.sample_rate == 44100


class TestProcessingPipelineProcess:
    """Exercise ProcessingPipeline.process with all external calls mocked."""

    @pytest.fixture
    def loudnorm_stderr(self) -> str:
        return textwrap.dedent("""\
            {
                "input_i" : "-24.00",
                "input_tp" : "-3.00",
                "input_lra" : "8.00",
                "input_thresh" : "-35.00",
                "output_i" : "-16.00",
                "output_tp" : "-1.50",
                "output_lra" : "7.80",
                "output_thresh" : "-26.50",
                "normalization_type" : "dynamic",
                "target_offset" : "0.00"
            }
        """)

    def test_missing_input_returns_failed_result(self, mock_binaries: None, tmp_path: Path):
        pipeline = ProcessingPipeline()
        out = tmp_path / "out.mp4"
        result = pipeline.process(tmp_path / "missing.mkv", out)
        assert result.success is False
        assert result.error is not None
        assert "not found" in result.error

    def test_happy_path_invokes_all_steps(
        self,
        mock_binaries: None,
        tmp_path: Path,
        loudnorm_stderr: str,
    ):
        input_file = tmp_path / "raw.mkv"
        input_file.write_bytes(b"fake-video")
        output_file = tmp_path / "out" / "final.mp4"

        steps: list[tuple[int, str, int]] = []

        def on_step(step: int, name: str, total: int) -> None:
            steps.append((step, name, total))

        # Two-pass loudnorm: first pass returns the JSON above on stderr,
        # subsequent calls return empty (they don't inspect output).
        run_calls: list[list] = []

        def fake_run_subprocess(argv, *, check: bool = True, **kwargs):
            run_calls.append(list(argv))
            return _completed(stderr=loudnorm_stderr if "null" in argv else "")

        with (
            patch.object(pipeline_module, "run_subprocess", side_effect=fake_run_subprocess),
            patch.object(pipeline_module, "run_onnx_denoise") as onnx_mock,
            patch.object(pipeline_module, "get_audio_duration", return_value=42.0) as duration_mock,
        ):
            pipeline = ProcessingPipeline()
            result = pipeline.process(input_file, output_file, on_step=on_step)

        assert result.success is True
        assert result.duration_seconds == 42.0
        assert result.output_file == output_file.resolve()

        # Callback saw all five steps in order.
        assert [s[0] for s in steps] == [1, 2, 3, 4, 5]
        assert all(s[2] == 5 for s in steps)
        assert "Extracting audio" in steps[0][1]
        assert "Muxing" in steps[4][1]

        # Output dir was created.
        assert output_file.parent.exists()
        # ONNX denoise called once.
        onnx_mock.assert_called_once()
        duration_mock.assert_called_once()

        # run_subprocess was called for each ffmpeg step (extract, loudnorm
        # measurement pass, loudnorm apply pass, encode, mux = 5 calls).
        assert len(run_calls) >= 5
        # Mux command must map video + audio and use +faststart.
        mux_argv = run_calls[-1]
        assert any("+faststart" == str(a) for a in mux_argv)
        assert "-map" in [str(a) for a in mux_argv]

    def test_exception_during_processing_returns_failed_result(
        self, mock_binaries: None, tmp_path: Path
    ):
        input_file = tmp_path / "raw.mkv"
        input_file.write_bytes(b"x")
        output_file = tmp_path / "out.mp4"

        with (
            patch.object(
                pipeline_module,
                "run_subprocess",
                side_effect=RuntimeError("ffmpeg exploded"),
            ),
            patch.object(pipeline_module, "run_onnx_denoise"),
            patch.object(pipeline_module, "get_audio_duration", return_value=1.0),
        ):
            pipeline = ProcessingPipeline()
            result = pipeline.process(input_file, output_file)

        assert result.success is False
        assert result.error is not None
        assert "ffmpeg exploded" in result.error

    def test_keep_temp_preserves_temp_dir(
        self, mock_binaries: None, tmp_path: Path, loudnorm_stderr: str
    ):
        """keep_temp=True must not remove the temp directory after processing."""
        input_file = tmp_path / "raw.mkv"
        input_file.write_bytes(b"x")
        output_file = tmp_path / "out.mp4"

        recorded_temp_dirs: list[Path] = []
        original_mkdtemp = pipeline_module.tempfile.mkdtemp

        def record_mkdtemp(*args, **kwargs) -> str:
            path: str = original_mkdtemp(*args, **kwargs)
            recorded_temp_dirs.append(Path(path))
            return path

        def fake_run_subprocess(argv, *, check: bool = True, **kwargs):
            return _completed(stderr=loudnorm_stderr if "null" in argv else "")

        cfg = PipelineConfig(keep_temp=True)
        with (
            patch.object(pipeline_module.tempfile, "mkdtemp", side_effect=record_mkdtemp),
            patch.object(pipeline_module, "run_subprocess", side_effect=fake_run_subprocess),
            patch.object(pipeline_module, "run_onnx_denoise"),
            patch.object(pipeline_module, "get_audio_duration", return_value=1.0),
        ):
            pipeline = ProcessingPipeline(config=cfg)
            result = pipeline.process(input_file, output_file)

        assert result.success is True
        assert recorded_temp_dirs
        assert recorded_temp_dirs[0].exists()
        # Caller is responsible for cleanup when keep_temp is set.
        import shutil

        shutil.rmtree(recorded_temp_dirs[0], ignore_errors=True)

    def test_single_pass_fallback_when_loudnorm_unparseable(
        self, mock_binaries: None, tmp_path: Path
    ):
        """If the first loudnorm pass produces no JSON, the second pass uses
        ``loudnorm_filter`` directly."""
        input_file = tmp_path / "raw.mkv"
        input_file.write_bytes(b"x")
        output_file = tmp_path / "out.mp4"

        run_calls: list[list] = []

        def fake_run_subprocess(argv, *, check: bool = True, **kwargs):
            run_calls.append([str(a) for a in argv])
            return _completed(stderr="no json here")  # measure pass returns garbage

        with (
            patch.object(pipeline_module, "run_subprocess", side_effect=fake_run_subprocess),
            patch.object(pipeline_module, "run_onnx_denoise"),
            patch.object(pipeline_module, "get_audio_duration", return_value=1.0),
        ):
            pipeline = ProcessingPipeline()
            result = pipeline.process(input_file, output_file)

        assert result.success is True
        # The apply-pass ffmpeg call must use the fallback single-pass filter
        # (no "measured_I" when measurement failed).
        apply_calls = [c for c in run_calls if "-af" in c and "loudnorm" in " ".join(c)]
        # Find the apply pass — it follows the measurement pass. Measurement
        # pass outputs to "NUL" or "/dev/null"; apply pass outputs to the
        # temp wav file.
        apply_pass = [c for c in apply_calls if "NUL" not in c and "/dev/null" not in c]
        assert apply_pass, "expected an apply-pass ffmpeg invocation"
        apply_filter = apply_pass[0][apply_pass[0].index("-af") + 1]
        assert "measured_I" not in apply_filter


class TestPrivateArgv:
    """Verify the argv each private step method assembles."""

    @pytest.fixture
    def pipeline(self, mock_binaries: None) -> ProcessingPipeline:
        return ProcessingPipeline()

    @staticmethod
    def _capture_argv(captured: list[list[str]]):
        """Build a patch side_effect that records the argv of each call."""

        def _side_effect(argv, **_kwargs):
            captured.append([str(a) for a in argv])
            return _completed()

        return _side_effect

    def test_extract_audio_argv(self, pipeline: ProcessingPipeline, tmp_path: Path):
        captured: list[list[str]] = []
        with patch.object(
            pipeline_module, "run_subprocess", side_effect=self._capture_argv(captured)
        ):
            pipeline._extract_audio(tmp_path / "in.mkv", tmp_path / "out.wav")

        argv = captured[0]
        # ffmpeg path is always the first argv element; platform separators vary.
        assert argv[0].endswith("ffmpeg") or argv[0].endswith("ffmpeg.exe")
        assert "-vn" in argv
        assert "pcm_s16le" in argv
        assert "48000" in argv  # default sample rate
        assert argv[-1].endswith("out.wav")

    def test_encode_audio_argv(self, pipeline: ProcessingPipeline, tmp_path: Path):
        captured: list[list[str]] = []
        with patch.object(
            pipeline_module, "run_subprocess", side_effect=self._capture_argv(captured)
        ):
            pipeline._encode_audio(tmp_path / "in.wav", tmp_path / "out.aac")

        argv = captured[0]
        assert "aac" in argv
        assert "192k" in argv  # default bitrate
        assert argv[-1].endswith("out.aac")

    def test_mux_video_argv(self, pipeline: ProcessingPipeline, tmp_path: Path):
        captured: list[list[str]] = []
        with patch.object(
            pipeline_module, "run_subprocess", side_effect=self._capture_argv(captured)
        ):
            pipeline._mux_video(
                tmp_path / "video.mkv",
                tmp_path / "audio.aac",
                tmp_path / "final.mp4",
            )

        argv = captured[0]
        assert "copy" in argv  # default video codec
        assert "+faststart" in argv
        assert "0:v:0" in argv
        assert "1:a:0" in argv

    def test_run_denoise_forwards_atten(self, pipeline: ProcessingPipeline, tmp_path: Path):
        pipeline.config.denoise_atten_lim = 42.0
        with patch.object(pipeline_module, "run_onnx_denoise") as denoise:
            pipeline._run_denoise(tmp_path / "a.wav", tmp_path / "b.wav")
        denoise.assert_called_once()
        _, kwargs = denoise.call_args
        assert kwargs["atten_lim_db"] == 42.0

    def test_measure_loudness_returns_none_on_empty_output(
        self, pipeline: ProcessingPipeline, tmp_path: Path
    ):
        with patch.object(
            pipeline_module, "run_subprocess", return_value=_completed(stderr="no json")
        ):
            result = pipeline._measure_loudness(
                tmp_path / "in.wav",
                base_filters="highpass=f=80,compand",
            )
        assert result is None
