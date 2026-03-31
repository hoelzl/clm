"""Cross-platform utility functions for finding binaries and running subprocesses."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger


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


def find_deepfilter() -> Path:
    """Find the DeepFilterNet CLI binary.

    The binary name varies by platform and installation method:
    - pip install: 'deepFilter' (case-sensitive on Linux)
    - Some versions: 'deep-filter' or 'deepfilter'
    """
    names_to_try = ["deepFilter", "deep-filter", "deepfilter"]
    for name in names_to_try:
        try:
            return find_binary(name)
        except BinaryNotFoundError:
            continue

    raise BinaryNotFoundError(
        "deepFilter",
        "pip install deepfilternet",
    )


def check_dependencies() -> dict[str, Path | None]:
    """Check all required dependencies and return their paths.

    Returns a dict mapping tool name to path (or None if not found).
    """
    deps: dict[str, Path | None] = {}

    for name, finder in [
        ("ffmpeg", find_ffmpeg),
        ("ffprobe", find_ffprobe),
        ("deepFilter", find_deepfilter),
    ]:
        try:
            deps[name] = finder()
        except BinaryNotFoundError as e:
            logger.warning(str(e))
            deps[name] = None

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
