"""Unit tests for ``clm.recordings.processing.utils``.

These tests stay on the host side of every external dependency — no real
ffmpeg, no real network, no real ONNX inference. The ONNX path is exercised
with heavy mocking so the function body is covered without a model file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from clm.recordings.processing import utils
from clm.recordings.processing.utils import (
    BinaryNotFoundError,
    check_dependencies,
    check_onnxruntime,
    download_onnx_model,
    find_binary,
    find_ffmpeg,
    find_ffprobe,
    get_audio_duration,
    run_onnx_denoise,
    run_subprocess,
)


class TestBinaryNotFoundError:
    def test_message_without_hint(self):
        err = BinaryNotFoundError("ffmpeg")
        assert "ffmpeg" in str(err)
        assert "Install" not in str(err)
        assert err.name == "ffmpeg"

    def test_message_with_hint(self):
        err = BinaryNotFoundError("ffmpeg", "winget install FFmpeg")
        text = str(err)
        assert "ffmpeg" in text
        assert "Install: winget install FFmpeg" in text


class TestFindBinary:
    def test_returns_path_when_on_path(self):
        with patch("clm.recordings.processing.utils.shutil.which", return_value="/usr/bin/ffmpeg"):
            assert find_binary("ffmpeg") == Path("/usr/bin/ffmpeg")

    def test_windows_scripts_fallback(self, monkeypatch: pytest.MonkeyPatch):
        """On Windows, pip-installed scripts should be located via sys.prefix."""
        monkeypatch.setattr(sys, "platform", "win32")
        with (
            patch("clm.recordings.processing.utils.shutil.which", return_value=None),
            patch.object(Path, "is_file", return_value=True),
        ):
            result = find_binary("auphonic")
            # First candidate is sys.prefix / "Scripts" / "auphonic.exe"
            assert result.name == "auphonic.exe"
            assert "Scripts" in result.parts

    def test_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("clm.recordings.processing.utils.shutil.which", return_value=None):
            with pytest.raises(BinaryNotFoundError) as exc:
                find_binary("nonesuch")
            assert exc.value.name == "nonesuch"

    def test_raises_on_windows_when_no_candidate_exists(self, monkeypatch: pytest.MonkeyPatch):
        """Windows fallback iterates candidates; all missing → raise."""
        monkeypatch.setattr(sys, "platform", "win32")
        with (
            patch("clm.recordings.processing.utils.shutil.which", return_value=None),
            patch.object(Path, "is_file", return_value=False),
        ):
            with pytest.raises(BinaryNotFoundError):
                find_binary("nonesuch")


class TestFindFfmpeg:
    def test_delegates_to_find_binary(self):
        with patch(
            "clm.recordings.processing.utils.find_binary",
            return_value=Path("/opt/ffmpeg"),
        ) as m:
            assert find_ffmpeg() == Path("/opt/ffmpeg")
            m.assert_called_once_with("ffmpeg")

    def test_adds_windows_hint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "win32")
        with patch(
            "clm.recordings.processing.utils.find_binary",
            side_effect=BinaryNotFoundError("ffmpeg"),
        ):
            with pytest.raises(BinaryNotFoundError) as exc:
                find_ffmpeg()
            assert "winget install FFmpeg" in str(exc.value)

    def test_adds_posix_hint(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with patch(
            "clm.recordings.processing.utils.find_binary",
            side_effect=BinaryNotFoundError("ffmpeg"),
        ):
            with pytest.raises(BinaryNotFoundError) as exc:
                find_ffmpeg()
            text = str(exc.value)
            assert "apt install" in text or "pacman" in text


class TestFindFfprobe:
    def test_delegates_to_find_binary(self):
        with patch(
            "clm.recordings.processing.utils.find_binary",
            return_value=Path("/opt/ffprobe"),
        ) as m:
            assert find_ffprobe() == Path("/opt/ffprobe")
            m.assert_called_once_with("ffprobe")

    def test_wraps_not_found_with_hint(self):
        with patch(
            "clm.recordings.processing.utils.find_binary",
            side_effect=BinaryNotFoundError("ffprobe"),
        ):
            with pytest.raises(BinaryNotFoundError) as exc:
                find_ffprobe()
            assert "alongside ffmpeg" in str(exc.value)


class TestDownloadOnnxModel:
    def test_returns_existing_model_without_download(self, tmp_path: Path):
        cache_dir = tmp_path / "models"
        cache_dir.mkdir()
        cached = cache_dir / utils.ONNX_MODEL_FILENAME
        cached.write_bytes(b"fake-model")

        with patch("clm.recordings.processing.utils.urllib.request.urlretrieve") as mock_retrieve:
            result = download_onnx_model(cache_dir=cache_dir)

        assert result == cached
        mock_retrieve.assert_not_called()

    def test_downloads_when_missing(self, tmp_path: Path):
        cache_dir = tmp_path / "new_cache"

        def fake_retrieve(url: str, dest: str) -> None:
            Path(dest).write_bytes(b"downloaded")

        with patch(
            "clm.recordings.processing.utils.urllib.request.urlretrieve",
            side_effect=fake_retrieve,
        ) as mock_retrieve:
            result = download_onnx_model(cache_dir=cache_dir)

        assert result.exists()
        assert result.read_bytes() == b"downloaded"
        mock_retrieve.assert_called_once()
        # URL is the first positional argument.
        args, _ = mock_retrieve.call_args
        assert args[0] == utils.ONNX_MODEL_URL

    def test_default_cache_dir_uses_platformdirs(self, tmp_path: Path):
        """When cache_dir is None, the function should route through platformdirs."""
        with (
            patch(
                "platformdirs.user_cache_dir",
                return_value=str(tmp_path / "user_cache"),
            ),
            patch("clm.recordings.processing.utils.urllib.request.urlretrieve") as mock_retrieve,
        ):

            def fake_retrieve(url: str, dest: str) -> None:
                Path(dest).write_bytes(b"x")

            mock_retrieve.side_effect = fake_retrieve
            result = download_onnx_model()

        assert result.exists()
        assert result.parent.name == "models"
        # Directory below the platformdirs-provided cache dir.
        assert "user_cache" in str(result)


class TestCheckOnnxruntime:
    def test_returns_version_when_installed(self):
        # onnxruntime is a real dep — the version is a non-empty string.
        version = check_onnxruntime()
        assert version is not None
        assert isinstance(version, str)
        assert version  # non-empty

    def test_returns_none_on_import_error(self):
        def fake_import(name: str, *args, **kwargs):
            if name == "onnxruntime":
                raise ImportError("no onnxruntime")
            return real_import(name, *args, **kwargs)

        import builtins

        real_import = builtins.__import__
        with patch("builtins.__import__", side_effect=fake_import):
            assert check_onnxruntime() is None


class TestCheckDependencies:
    def test_reports_all_found(self):
        with (
            patch(
                "clm.recordings.processing.utils.find_ffmpeg",
                return_value=Path("/usr/bin/ffmpeg"),
            ),
            patch(
                "clm.recordings.processing.utils.find_ffprobe",
                return_value=Path("/usr/bin/ffprobe"),
            ),
            patch(
                "clm.recordings.processing.utils.check_onnxruntime",
                return_value="1.17.0",
            ),
        ):
            deps = check_dependencies()

        assert deps["ffmpeg"] == Path("/usr/bin/ffmpeg")
        assert deps["ffprobe"] == Path("/usr/bin/ffprobe")
        assert deps["onnxruntime"] == "1.17.0"

    def test_reports_missing_binaries_as_none(self):
        with (
            patch(
                "clm.recordings.processing.utils.find_ffmpeg",
                side_effect=BinaryNotFoundError("ffmpeg"),
            ),
            patch(
                "clm.recordings.processing.utils.find_ffprobe",
                side_effect=BinaryNotFoundError("ffprobe"),
            ),
            patch(
                "clm.recordings.processing.utils.check_onnxruntime",
                return_value=None,
            ),
        ):
            deps = check_dependencies()

        assert deps == {"ffmpeg": None, "ffprobe": None, "onnxruntime": None}


class TestRunSubprocess:
    def test_success_returns_completed_process(self):
        fake_result = subprocess.CompletedProcess(
            args=["echo"], returncode=0, stdout="hi\n", stderr=""
        )
        with patch(
            "clm.recordings.processing.utils.subprocess.run", return_value=fake_result
        ) as mock_run:
            result = run_subprocess(["echo", "hi"])
        assert result.returncode == 0
        assert result.stdout == "hi\n"
        # args must be stringified.
        called_args, called_kwargs = mock_run.call_args
        assert called_args[0] == ["echo", "hi"]
        assert called_kwargs["text"] is True
        assert called_kwargs["check"] is True
        assert called_kwargs["capture_output"] is True

    def test_stringifies_path_arguments(self):
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch(
            "clm.recordings.processing.utils.subprocess.run", return_value=fake_result
        ) as mock_run:
            run_subprocess(["ffmpeg", Path("/tmp/in.wav"), Path("/tmp/out.wav")])
        called_args, _ = mock_run.call_args
        assert all(isinstance(a, str) for a in called_args[0])

    def test_passes_cwd_through(self, tmp_path: Path):
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch(
            "clm.recordings.processing.utils.subprocess.run", return_value=fake_result
        ) as mock_run:
            run_subprocess(["ls"], cwd=tmp_path)
        _, called_kwargs = mock_run.call_args
        assert called_kwargs["cwd"] == tmp_path

    def test_check_false_returns_nonzero_exit(self):
        """When ``check=False`` and the command exits nonzero, the warning
        branch must execute and the CompletedProcess is returned to the caller.
        """
        fake_result = subprocess.CompletedProcess(
            args=["bad"], returncode=2, stdout="", stderr="kaboom"
        )
        with patch(
            "clm.recordings.processing.utils.subprocess.run",
            return_value=fake_result,
        ):
            result = run_subprocess(["bad"], check=False)
        assert result.returncode == 2

    def test_windows_applies_creationflags(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "win32")
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch(
            "clm.recordings.processing.utils.subprocess.run", return_value=fake_result
        ) as mock_run:
            run_subprocess(["echo", "hi"])
        _, called_kwargs = mock_run.call_args
        assert called_kwargs.get("creationflags") == 0x08000000

    def test_non_windows_omits_creationflags(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "linux")
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch(
            "clm.recordings.processing.utils.subprocess.run", return_value=fake_result
        ) as mock_run:
            run_subprocess(["echo", "hi"])
        _, called_kwargs = mock_run.call_args
        assert "creationflags" not in called_kwargs


class TestGetAudioDuration:
    def test_parses_ffprobe_output(self):
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="123.45\n", stderr=""
        )
        with patch(
            "clm.recordings.processing.utils.run_subprocess",
            return_value=fake_result,
        ) as mock_run:
            duration = get_audio_duration(Path("/usr/bin/ffprobe"), Path("/tmp/x.wav"))
        assert duration == pytest.approx(123.45)
        # ffprobe is the first argv element.
        called_args, _ = mock_run.call_args
        argv = called_args[0]
        assert argv[0] == Path("/usr/bin/ffprobe")
        assert "-show_entries" in argv


class TestRunOnnxDenoise:
    """Exercise run_onnx_denoise end-to-end with heavy mocks.

    Covers the happy-path happy branch (sample-rate OK, mono audio) and the
    stereo + sample-rate-error branches. No real model file is loaded.
    """

    def _make_fake_session(self, hop_size: int = 480) -> MagicMock:
        session = MagicMock()

        def fake_run(_outputs, inputs):
            frame = inputs["input_frame"]
            state = inputs["states"]
            lsnr = np.zeros(1, dtype=np.float32)
            return [frame.copy(), state.copy(), lsnr]

        session.run.side_effect = fake_run
        return session

    def test_denoises_mono_audio(self, tmp_path: Path):
        # Length is a multiple of the hop size so no padding is added.
        n_samples = utils.ONNX_HOP_SIZE * 100  # ~1 second at 48 kHz
        audio = np.linspace(-0.5, 0.5, n_samples, dtype=np.float32)

        with (
            patch(
                "clm.recordings.processing.utils.download_onnx_model",
                return_value=tmp_path / "fake.onnx",
            ),
            patch("onnxruntime.InferenceSession", return_value=self._make_fake_session()),
            patch("soundfile.read", return_value=(audio, 48000)),
            patch("soundfile.write") as sf_write,
        ):
            run_onnx_denoise(tmp_path / "in.wav", tmp_path / "out.wav")

        sf_write.assert_called_once()
        args, _ = sf_write.call_args
        written = args[1]
        # The pipeline trims the algorithmic delay from the start.
        delay = utils.ONNX_FFT_SIZE - utils.ONNX_HOP_SIZE
        assert written.ndim == 1
        assert written.shape[0] == n_samples - delay
        # Sample rate argument is passed through.
        assert args[2] == 48000

    def test_stereo_input_uses_first_channel(self, tmp_path: Path):
        n_samples = utils.ONNX_HOP_SIZE * 3  # multiple of hop size → no padding
        mono = np.linspace(-0.5, 0.5, n_samples, dtype=np.float32)
        stereo = np.stack([mono, -mono], axis=1)

        with (
            patch(
                "clm.recordings.processing.utils.download_onnx_model",
                return_value=tmp_path / "fake.onnx",
            ),
            patch("onnxruntime.InferenceSession", return_value=self._make_fake_session()),
            patch("soundfile.read", return_value=(stereo, 48000)),
            patch("soundfile.write") as sf_write,
        ):
            run_onnx_denoise(tmp_path / "in.wav", tmp_path / "out.wav")

        args, _ = sf_write.call_args
        written = args[1]
        delay = utils.ONNX_FFT_SIZE - utils.ONNX_HOP_SIZE
        assert written.ndim == 1
        assert written.shape[0] == n_samples - delay

    def test_rejects_non_48k_sample_rate(self, tmp_path: Path):
        audio = np.zeros(1000, dtype=np.float32)
        with (
            patch(
                "clm.recordings.processing.utils.download_onnx_model",
                return_value=tmp_path / "fake.onnx",
            ),
            patch("onnxruntime.InferenceSession", return_value=self._make_fake_session()),
            patch("soundfile.read", return_value=(audio, 44100)),
            patch("soundfile.write"),
        ):
            with pytest.raises(ValueError, match="48 kHz"):
                run_onnx_denoise(tmp_path / "in.wav", tmp_path / "out.wav")
