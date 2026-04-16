"""Download, cache, and emit miniserve binaries for local JupyterLite serving.

When ``<launcher>miniserve</launcher>`` is set, the builder bundles prebuilt
miniserve binaries for all four supported platforms into the site directory so
that a USB- or LAN-shared build works for any recipient without requiring
Python. Each binary is downloaded once from a pinned GitHub release, SHA-256
verified, and cached under ``~/.cache/clm/miniserve/<version>/``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import stat
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MINISERVE_VERSION = "0.35.0"

_ASSETS: dict[str, dict[str, str]] = {
    "windows-x64": {
        "github_name": f"miniserve-{MINISERVE_VERSION}-x86_64-pc-windows-msvc.exe",
        "local_name": "miniserve-windows.exe",
        "sha256": "3229e75b40b3870382d23c9a0ecec880b9bd5f6e49160ce602bb15d00f275b6a",
    },
    "macos-x64": {
        "github_name": f"miniserve-{MINISERVE_VERSION}-x86_64-apple-darwin",
        "local_name": "miniserve-macos-x64",
        "sha256": "63a99448bf7c450f1264a1af313646df30251bdbc3284b8f4d2d55cee2b98c55",
    },
    "macos-arm64": {
        "github_name": f"miniserve-{MINISERVE_VERSION}-aarch64-apple-darwin",
        "local_name": "miniserve-macos-arm64",
        "sha256": "8e8dc916b1dc3bc2a46bf5a44308caf7db153e6940db178668929e1de40d1fbb",
    },
    "linux-x64": {
        "github_name": f"miniserve-{MINISERVE_VERSION}-x86_64-unknown-linux-musl",
        "local_name": "miniserve-linux",
        "sha256": "c630ee030d5d9d83c88c5cc72f43ae215b0c214d64fd7afc92244fe369af2964",
    },
}

_DOWNLOAD_BASE = f"https://github.com/svenstaro/miniserve/releases/download/v{MINISERVE_VERSION}"


def _cache_dir() -> Path:
    """Return the platform-appropriate cache directory for miniserve binaries."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "clm" / "miniserve" / MINISERVE_VERSION


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _download_and_verify(asset: dict[str, str], dest: Path) -> None:
    """Download a single binary and verify its SHA-256 checksum."""
    url = f"{_DOWNLOAD_BASE}/{asset['github_name']}"
    logger.info("Downloading %s from %s", asset["local_name"], url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        urllib.request.urlretrieve(url, tmp)
        actual = _sha256(tmp)
        if actual != asset["sha256"]:
            raise RuntimeError(
                f"SHA-256 mismatch for {asset['github_name']}: "
                f"expected {asset['sha256']}, got {actual}"
            )
        tmp.rename(dest)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_cached() -> dict[str, Path]:
    """Ensure all four miniserve binaries are cached and verified.

    Returns a mapping of platform key to cached binary path.
    """
    cache = _cache_dir()
    result: dict[str, Path] = {}
    for key, asset in _ASSETS.items():
        cached = cache / asset["local_name"]
        if cached.is_file():
            actual = _sha256(cached)
            if actual == asset["sha256"]:
                result[key] = cached
                continue
            logger.warning("Cache checksum mismatch for %s, re-downloading", asset["local_name"])
            cached.unlink()
        _download_and_verify(asset, cached)
        result[key] = cached
    return result


def emit_miniserve_launcher(output_dir: Path, site_dir: Path) -> None:
    """Copy all four miniserve binaries and per-OS launcher scripts into the output."""
    binaries = ensure_cached()
    for key, src in binaries.items():
        local_name = _ASSETS[key]["local_name"]
        dst = output_dir / local_name
        shutil.copy2(src, dst)
        if not local_name.endswith(".exe"):
            dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    _emit_launch_bat(output_dir)
    _emit_launch_command(output_dir)
    _emit_launch_sh(output_dir)


def _emit_launch_bat(output_dir: Path) -> None:
    text = """\
@echo off
echo Starting JupyterLite...
"%~dp0miniserve-windows.exe" --index lab/index.html "%~dp0_output"
"""
    (output_dir / "launch.bat").write_text(text, encoding="utf-8")


def _emit_launch_command(output_dir: Path) -> None:
    """Emit ``launch.command`` — double-clickable on macOS Finder."""
    text = """\
#!/bin/bash
cd "$(dirname "$0")"
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    BINARY="./miniserve-macos-arm64"
else
    BINARY="./miniserve-macos-x64"
fi
echo "Starting JupyterLite..."
exec "$BINARY" --index lab/index.html ./_output
"""
    path = output_dir / "launch.command"
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _emit_launch_sh(output_dir: Path) -> None:
    text = """\
#!/bin/sh
cd "$(dirname "$0")"
echo "Starting JupyterLite..."
exec ./miniserve-linux --index lab/index.html ./_output
"""
    path = output_dir / "launch.sh"
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


__all__ = [
    "MINISERVE_VERSION",
    "emit_miniserve_launcher",
    "ensure_cached",
]
