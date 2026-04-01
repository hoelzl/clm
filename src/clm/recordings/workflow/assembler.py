"""Assembly logic: mux raw video with processed audio and archive originals.

Reuses ``find_ffmpeg`` and ``run_subprocess`` from the recording processing
module to avoid duplicating binary lookup and Windows subprocess handling.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from clm.recordings.processing.utils import find_ffmpeg, run_subprocess

from .directories import (
    PendingPair,
    archive_dir,
    final_dir,
    find_pending_pairs,
    to_process_dir,
)
from .naming import DEFAULT_RAW_SUFFIX


class AssemblyResult(BaseModel):
    """Result of assembling a single video + audio pair."""

    video: Path
    output_file: Path
    success: bool
    error: str | None = None


class AssemblyBatchResult(BaseModel):
    """Summary of assembling all pending pairs under a root directory."""

    results: list[AssemblyResult] = Field(default_factory=list)

    @property
    def succeeded(self) -> list[AssemblyResult]:
        return [r for r in self.results if r.success]

    @property
    def failed(self) -> list[AssemblyResult]:
        return [r for r in self.results if not r.success]

    def summary(self) -> str:
        lines = [
            f"Assembly complete: {len(self.results)} pair(s)",
            f"  Succeeded: {len(self.succeeded)}",
            f"  Failed:    {len(self.failed)}",
        ]
        for r in self.failed:
            lines.append(f"    - {r.video.name}: {r.error}")
        return "\n".join(lines)


def mux_video_audio(
    ffmpeg: Path,
    video: Path,
    audio: Path,
    output: Path,
) -> None:
    """Mux video stream from *video* with audio from *audio* into *output*.

    Uses ``-c:v copy`` (no re-encode) and ``-c:a aac`` for the audio.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    run_subprocess(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            video,
            "-i",
            audio,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-movflags",
            "+faststart",
            output,
        ]
    )


def archive_pair(pair: PendingPair, archive: Path) -> None:
    """Move the raw video and audio files to the archive directory."""
    dest_dir = archive / pair.relative_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pair.video), str(dest_dir / pair.video.name))
    shutil.move(str(pair.audio), str(dest_dir / pair.audio.name))
    logger.debug("Archived {} and {} to {}", pair.video.name, pair.audio.name, dest_dir)


def assemble_one(
    pair: PendingPair,
    final: Path,
    archive: Path,
    *,
    ffmpeg: Path | None = None,
    output_ext: str = ".mp4",
) -> AssemblyResult:
    """Mux a single video + audio pair, then archive the originals.

    Args:
        pair: The matched raw video + processed audio.
        final: Root of the ``final/`` directory tree.
        archive: Root of the ``archive/`` directory tree.
        ffmpeg: Path to ffmpeg binary (auto-detected if None).
        output_ext: Extension for the output file.

    Returns:
        AssemblyResult indicating success or failure.
    """
    if ffmpeg is None:
        ffmpeg = find_ffmpeg()

    output_file = final / pair.relative_dir / f"{pair.base_name}{output_ext}"

    try:
        mux_video_audio(ffmpeg, pair.video, pair.audio, output_file)
    except Exception as e:
        logger.error("Assembly failed for {}: {}", pair.video.name, e)
        return AssemblyResult(
            video=pair.video,
            output_file=output_file,
            success=False,
            error=str(e),
        )

    try:
        archive_pair(pair, archive)
    except Exception as e:
        logger.warning("Mux succeeded but archiving failed for {}: {}", pair.video.name, e)

    logger.info("Assembled {} -> {}", pair.video.name, output_file)
    return AssemblyResult(video=pair.video, output_file=output_file, success=True)


def assemble_all(
    root_dir: Path,
    *,
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    output_ext: str = ".mp4",
    on_pair: Callable[[int, PendingPair, int], None] | None = None,
) -> AssemblyBatchResult:
    """Find and assemble all pending pairs under *root_dir*.

    Args:
        root_dir: Recordings root containing ``to-process/``, ``final/``, ``archive/``.
        raw_suffix: Suffix identifying raw files (default ``--RAW``).
        output_ext: Extension for output files.
        on_pair: Optional progress callback ``(index, pair, total)``.

    Returns:
        AssemblyBatchResult with per-pair results.
    """
    tp = to_process_dir(root_dir)
    fl = final_dir(root_dir)
    ar = archive_dir(root_dir)

    pairs = find_pending_pairs(tp, raw_suffix=raw_suffix)
    batch = AssemblyBatchResult()

    if not pairs:
        logger.info("No pending pairs found in {}", tp)
        return batch

    ffmpeg = find_ffmpeg()

    for i, pair in enumerate(pairs):
        if on_pair:
            on_pair(i, pair, len(pairs))
        result = assemble_one(pair, fl, ar, ffmpeg=ffmpeg, output_ext=output_ext)
        batch.results.append(result)

    return batch
