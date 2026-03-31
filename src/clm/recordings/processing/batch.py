"""Batch processing for multiple recordings."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from .config import PipelineConfig
from .pipeline import ProcessingPipeline, ProcessingResult

# Common video file extensions to look for.
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".ts"}


class BatchResult(BaseModel):
    """Summary of a batch processing run."""

    succeeded: list[ProcessingResult] = Field(default_factory=list)
    failed: list[ProcessingResult] = Field(default_factory=list)
    skipped: list[Path] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.succeeded) + len(self.failed) + len(self.skipped)

    def summary(self) -> str:
        lines = [
            f"Batch complete: {self.total} files",
            f"  Succeeded: {len(self.succeeded)}",
            f"  Skipped:   {len(self.skipped)}",
            f"  Failed:    {len(self.failed)}",
        ]
        if self.failed:
            lines.append("  Failed files:")
            for r in self.failed:
                lines.append(f"    - {r.input_file.name}: {r.error}")
        return "\n".join(lines)


def find_video_files(
    directory: Path,
    *,
    recursive: bool = False,
    extensions: set[str] | None = None,
) -> list[Path]:
    """Find video files in a directory.

    Args:
        directory: Directory to search.
        recursive: Whether to search subdirectories.
        extensions: Set of extensions to match (default: common video formats).

    Returns:
        Sorted list of video file paths.
    """
    exts = extensions or VIDEO_EXTENSIONS
    if recursive:
        files = [f for f in directory.rglob("*") if f.suffix.lower() in exts]
    else:
        files = [f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in exts]
    return sorted(files)


def process_batch(
    input_dir: Path,
    output_dir: Path,
    *,
    config: PipelineConfig | None = None,
    recursive: bool = False,
    suffix: str = "_final",
    on_file: Callable[[int, Path, int], None] | None = None,
    on_step: Callable[[int, str, int], None] | None = None,
) -> BatchResult:
    """Process all video files in a directory.

    Args:
        input_dir: Directory containing raw recordings.
        output_dir: Directory for processed output.
        config: Pipeline configuration (uses defaults if None).
        recursive: Search subdirectories.
        suffix: Suffix to add to output filenames before extension.
        on_file: Callback(file_index, file_path, total_files) for progress.
        on_step: Passed through to pipeline.process() for per-step progress.

    Returns:
        BatchResult with details of all processed files.
    """
    config = config or PipelineConfig()
    pipeline = ProcessingPipeline(config)
    output_dir.mkdir(parents=True, exist_ok=True)

    files = find_video_files(input_dir, recursive=recursive)
    result = BatchResult()
    notify_file = on_file or (lambda i, f, t: None)

    if not files:
        logger.warning("No video files found in {}", input_dir)
        return result

    logger.info("Found {} video(s) in {}", len(files), input_dir)

    for i, input_file in enumerate(files):
        notify_file(i, input_file, len(files))
        stem = input_file.stem
        output_file = output_dir / f"{stem}{suffix}.{config.output_extension}"

        # Skip if output already exists.
        if output_file.is_file():
            logger.info("Skipping {} (output exists)", input_file.name)
            result.skipped.append(input_file)
            continue

        logger.info("[{}/{}] Processing {}", i + 1, len(files), input_file.name)

        proc_result = pipeline.process(input_file, output_file, on_step=on_step)

        if proc_result.success:
            result.succeeded.append(proc_result)
        else:
            result.failed.append(proc_result)

    return result
