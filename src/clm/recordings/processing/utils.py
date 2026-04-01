"""Cross-platform utility functions for finding binaries and running subprocesses."""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from loguru import logger

# DeepFilterNet3 streaming ONNX model from yuyun2000/SpeechDenoiser.
# Originally exported via grazder/DeepFilterNet (torchDF-changes branch).
ONNX_MODEL_URL = (
    "https://github.com/yuyun2000/SpeechDenoiser/raw/refs/heads/main/48k/denoiser_model.onnx"
)
ONNX_MODEL_FILENAME = "deepfilter3_streaming.onnx"
ONNX_MODEL_CACHE_DIR = "clm"

# ONNX model constants
ONNX_HOP_SIZE = 480  # samples per frame at 48 kHz (10 ms)
ONNX_FFT_SIZE = 960
ONNX_STATE_SIZE = 45304


class BinaryNotFoundError(Exception):
    """Raised when a required external binary is not found."""

    def __init__(self, name: str, install_hint: str = "") -> None:
        msg = f"Required binary not found: {name}"
        if install_hint:
            msg += f"\n  Install: {install_hint}"
        super().__init__(msg)
        self.name = name


def find_binary(name: str) -> Path:
    """Find a binary on PATH, returning its full path.

    On Windows, also checks for common pip script locations if the binary
    isn't on PATH directly.
    """
    found = shutil.which(name)
    if found:
        return Path(found)

    # On Windows, pip-installed scripts may be in the user scripts dir
    # and not on PATH.
    if sys.platform == "win32":
        candidates = [
            Path(sys.prefix) / "Scripts" / f"{name}.exe",
            Path(sys.prefix) / "Scripts" / name,
            Path.home()
            / "AppData"
            / "Local"
            / "Programs"
            / "Python"
            / f"Python{sys.version_info.major}{sys.version_info.minor}"
            / "Scripts"
            / f"{name}.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate

    raise BinaryNotFoundError(name)


def find_ffmpeg() -> Path:
    """Find ffmpeg binary."""
    try:
        return find_binary("ffmpeg")
    except BinaryNotFoundError:
        if sys.platform == "win32":
            hint = "Download from https://ffmpeg.org/download.html or: winget install FFmpeg"
        else:
            hint = "pacman -S ffmpeg (Arch) or apt install ffmpeg (Debian/Ubuntu)"
        raise BinaryNotFoundError("ffmpeg", hint) from None


def find_ffprobe() -> Path:
    """Find ffprobe binary."""
    try:
        return find_binary("ffprobe")
    except BinaryNotFoundError:
        raise BinaryNotFoundError("ffprobe", "Installed alongside ffmpeg") from None


def download_onnx_model(cache_dir: Path | None = None) -> Path:
    """Download the DeepFilterNet3 ONNX model if not already cached.

    Returns the path to the cached model file.
    """
    if cache_dir is None:
        import platformdirs

        cache_dir = Path(platformdirs.user_cache_dir(ONNX_MODEL_CACHE_DIR)) / "models"

    cache_dir.mkdir(parents=True, exist_ok=True)
    model_path = cache_dir / ONNX_MODEL_FILENAME

    if model_path.exists():
        logger.debug("ONNX model cached at {}", model_path)
        return model_path

    logger.info("Downloading DeepFilterNet3 ONNX model...")
    urllib.request.urlretrieve(ONNX_MODEL_URL, model_path)
    logger.info("Model saved to {}", model_path)
    return model_path


def check_onnxruntime() -> str | None:
    """Check that onnxruntime is importable. Returns version or None."""
    try:
        import onnxruntime as ort

        return str(ort.__version__)
    except ImportError:
        return None


def run_onnx_denoise(input_file: Path, output_file: Path, *, atten_lim_db: float = 35.0) -> None:
    """Run DeepFilterNet3 noise reduction via ONNX Runtime.

    Processes the input WAV file frame-by-frame through the streaming ONNX
    model and writes the enhanced audio to output_file.

    Args:
        input_file: Path to input WAV (mono, 48 kHz expected).
        output_file: Path for the denoised output WAV.
        atten_lim_db: Attenuation limit in dB (0 = unlimited, 35 = moderate).
    """
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf

    model_path = download_onnx_model()
    session = ort.InferenceSession(str(model_path))

    audio, sr = sf.read(str(input_file), dtype="float32")
    if sr != 48000:
        raise ValueError(f"Expected 48 kHz audio, got {sr} Hz")

    # Handle stereo by taking first channel
    if audio.ndim > 1:
        audio = audio[:, 0]

    orig_len = len(audio)

    # Pad to hop_size boundary
    pad_len = (ONNX_HOP_SIZE - (orig_len % ONNX_HOP_SIZE)) % ONNX_HOP_SIZE
    if pad_len > 0:
        audio = np.concatenate([audio, np.zeros(pad_len, dtype=np.float32)])

    # Initialize state
    state = np.zeros(ONNX_STATE_SIZE, dtype=np.float32)
    atten = np.array([atten_lim_db], dtype=np.float32)

    # Process frame by frame
    num_frames = len(audio) // ONNX_HOP_SIZE
    output_frames = []

    for i in range(num_frames):
        frame = audio[i * ONNX_HOP_SIZE : (i + 1) * ONNX_HOP_SIZE]
        enhanced_frame, state, _lsnr = session.run(
            None,
            {
                "input_frame": frame,
                "states": state,
                "atten_lim_db": atten,
            },
        )
        output_frames.append(enhanced_frame)

    output = np.concatenate(output_frames)

    # Trim algorithmic delay and restore original length
    delay = ONNX_FFT_SIZE - ONNX_HOP_SIZE
    output = output[delay : orig_len + delay]

    sf.write(str(output_file), output, sr, subtype="FLOAT")


def check_dependencies() -> dict[str, str | Path | None]:
    """Check all required dependencies and return their status.

    Returns a dict mapping tool name to path/version (or None if not found).
    """
    deps: dict[str, str | Path | None] = {}

    for name, finder in [
        ("ffmpeg", find_ffmpeg),
        ("ffprobe", find_ffprobe),
    ]:
        try:
            deps[name] = finder()
        except BinaryNotFoundError as e:
            logger.warning(str(e))
            deps[name] = None

    deps["onnxruntime"] = check_onnxruntime()

    return deps


def run_subprocess(
    args: list[str | Path],
    *,
    check: bool = True,
    capture_output: bool = True,
    cwd: Path | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with consistent settings across platforms.

    On Windows, suppresses console window popups via CREATE_NO_WINDOW.

    Args:
        args: Command and arguments.
        check: Raise CalledProcessError on non-zero exit.
        capture_output: Capture stdout and stderr.
        cwd: Working directory.

    Returns:
        CompletedProcess with text output.
    """
    str_args = [str(a) for a in args]
    logger.debug("Running: {}", " ".join(str_args))

    extra_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        CREATE_NO_WINDOW = 0x08000000
        extra_kwargs["creationflags"] = CREATE_NO_WINDOW

    result = subprocess.run(
        str_args,
        check=check,
        capture_output=capture_output,
        text=True,
        cwd=cwd,
        **extra_kwargs,
        **kwargs,
    )

    if result.returncode != 0 and not check:
        logger.warning(
            "Command exited with {}: {}\nstderr: {}",
            result.returncode,
            " ".join(str_args),
            result.stderr,
        )

    return result


def get_audio_duration(ffprobe: Path, audio_file: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    result = run_subprocess(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_file),
        ]
    )
    return float(result.stdout.strip())
