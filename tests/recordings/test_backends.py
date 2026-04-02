"""Tests for recording workflow processing backends."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.workflow.backends import (
    ExternalBackend,
    OnnxBackend,
    ProcessingBackend,
)

# ------------------------------------------------------------------
# Protocol conformance
# ------------------------------------------------------------------


class TestProcessingBackendProtocol:
    def test_onnx_backend_is_processing_backend(self):
        assert isinstance(OnnxBackend(), ProcessingBackend)

    def test_external_backend_is_processing_backend(self):
        assert isinstance(ExternalBackend(), ProcessingBackend)


# ------------------------------------------------------------------
# ExternalBackend
# ------------------------------------------------------------------


class TestExternalBackend:
    def test_process_raises_not_implemented(self, tmp_path: Path):
        backend = ExternalBackend()
        with pytest.raises(NotImplementedError, match="external tool"):
            backend.process(tmp_path / "video.mp4", tmp_path / "audio.wav")


# ------------------------------------------------------------------
# OnnxBackend
# ------------------------------------------------------------------


class TestOnnxBackend:
    def test_init_default_config(self):
        backend = OnnxBackend()
        assert backend._config is None

    def test_init_with_config(self):
        from clm.recordings.processing.config import PipelineConfig

        config = PipelineConfig(sample_rate=44100)
        backend = OnnxBackend(config=config)
        assert backend._config is config

    @patch("clm.recordings.processing.utils.run_subprocess")
    @patch("clm.recordings.processing.utils.run_onnx_denoise")
    @patch("clm.recordings.processing.utils.get_audio_duration", return_value=120.0)
    @patch("clm.recordings.processing.utils.find_ffprobe", return_value=Path("/usr/bin/ffprobe"))
    @patch("clm.recordings.processing.utils.find_ffmpeg", return_value=Path("/usr/bin/ffmpeg"))
    def test_process_calls_pipeline_steps(
        self,
        mock_ffmpeg: MagicMock,
        mock_ffprobe: MagicMock,
        mock_duration: MagicMock,
        mock_denoise: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ):
        video = tmp_path / "input.mp4"
        video.write_bytes(b"fake video")
        output_wav = tmp_path / "output" / "audio.wav"

        backend = OnnxBackend()
        backend.process(video, output_wav)

        # Should create output directory
        assert output_wav.parent.is_dir()

        # Step 1: Extract audio (first run_subprocess call)
        assert mock_subprocess.call_count == 2  # extract + filters
        extract_call = mock_subprocess.call_args_list[0]
        extract_args = [str(a) for a in extract_call[0][0]]
        assert str(Path("/usr/bin/ffmpeg")) in extract_args
        assert str(video) in extract_args

        # Step 2: ONNX denoise
        mock_denoise.assert_called_once()
        denoise_kwargs = mock_denoise.call_args
        assert denoise_kwargs.kwargs["atten_lim_db"] == 35.0

        # Step 3: Audio filters (second run_subprocess call)
        filter_call = mock_subprocess.call_args_list[1]
        filter_args = [str(a) for a in filter_call[0][0]]
        assert "-af" in filter_args
        assert str(output_wav) in filter_args

    @patch("clm.recordings.processing.utils.run_subprocess")
    @patch("clm.recordings.processing.utils.run_onnx_denoise")
    @patch("clm.recordings.processing.utils.get_audio_duration", return_value=60.0)
    @patch("clm.recordings.processing.utils.find_ffprobe", return_value=Path("/usr/bin/ffprobe"))
    @patch("clm.recordings.processing.utils.find_ffmpeg", return_value=Path("/usr/bin/ffmpeg"))
    def test_process_uses_custom_config(
        self,
        mock_ffmpeg: MagicMock,
        mock_ffprobe: MagicMock,
        mock_duration: MagicMock,
        mock_denoise: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ):
        from clm.recordings.processing.config import PipelineConfig

        config = PipelineConfig(denoise_atten_lim=50.0, sample_rate=44100)
        backend = OnnxBackend(config=config)

        video = tmp_path / "input.mp4"
        video.write_bytes(b"fake video")
        output_wav = tmp_path / "audio.wav"

        backend.process(video, output_wav)

        # Should use custom attenuation limit
        mock_denoise.assert_called_once()
        assert mock_denoise.call_args.kwargs["atten_lim_db"] == 50.0

        # Should use custom sample rate
        extract_args = [str(a) for a in mock_subprocess.call_args_list[0][0][0]]
        assert "44100" in extract_args

    @patch(
        "clm.recordings.processing.utils.run_subprocess", side_effect=RuntimeError("ffmpeg fail")
    )
    @patch("clm.recordings.processing.utils.find_ffprobe", return_value=Path("/usr/bin/ffprobe"))
    @patch("clm.recordings.processing.utils.find_ffmpeg", return_value=Path("/usr/bin/ffmpeg"))
    def test_process_propagates_errors(
        self,
        mock_ffmpeg: MagicMock,
        mock_ffprobe: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ):
        backend = OnnxBackend()
        video = tmp_path / "input.mp4"
        video.write_bytes(b"fake video")

        with pytest.raises(RuntimeError, match="ffmpeg fail"):
            backend.process(video, tmp_path / "audio.wav")

    @patch("clm.recordings.processing.utils.run_subprocess")
    @patch("clm.recordings.processing.utils.run_onnx_denoise")
    @patch("clm.recordings.processing.utils.get_audio_duration", return_value=60.0)
    @patch("clm.recordings.processing.utils.find_ffprobe", return_value=Path("/usr/bin/ffprobe"))
    @patch("clm.recordings.processing.utils.find_ffmpeg", return_value=Path("/usr/bin/ffmpeg"))
    def test_process_falls_back_to_default_config_for_non_pipeline_config(
        self,
        mock_ffmpeg: MagicMock,
        mock_ffprobe: MagicMock,
        mock_duration: MagicMock,
        mock_denoise: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ):
        """When config is not a PipelineConfig instance, use defaults."""
        backend = OnnxBackend(config="not-a-config")
        video = tmp_path / "input.mp4"
        video.write_bytes(b"fake video")

        backend.process(video, tmp_path / "audio.wav")

        # Should use default attenuation limit (35.0)
        mock_denoise.assert_called_once()
        assert mock_denoise.call_args.kwargs["atten_lim_db"] == 35.0
