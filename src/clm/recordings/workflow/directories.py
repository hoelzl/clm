"""Directory structure management for the recording workflow.

Manages the five-tier directory layout under the recordings root::

    <root>/
    +-- to-process/   # Raw recordings and externally processed audio
    +-- final/        # Muxed output (video + processed audio)
    +-- archive/      # Originals moved here after successful assembly
    +-- takes/        # Fully-processed takes replaced by a later take (history)
    +-- superseded/   # Displaced recordings (re-recorded before processing)

``takes/`` holds **processed** takes preserved for history — these cost
Auphonic credits to produce, so they are kept deliberately. ``superseded/``
holds **pre-processing garbage** (zero-length OBS outputs, abandoned takes).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from clm.recordings.processing.batch import VIDEO_EXTENSIONS

from .naming import DEFAULT_RAW_SUFFIX, parse_raw_stem

SUBDIRS = ("to-process", "final", "archive", "takes", "superseded")


class PendingPair(BaseModel):
    """A matched raw video + processed audio pair ready for assembly."""

    video: Path
    audio: Path
    relative_dir: Path
    raw_suffix: str = DEFAULT_RAW_SUFFIX

    @property
    def base_name(self) -> str:
        """Topic name without the raw suffix."""
        name, _ = parse_raw_stem(self.video.stem, self.raw_suffix)
        return name


def ensure_root(root_dir: Path) -> None:
    """Create the ``to-process/``, ``final/``, ``archive/``, and ``superseded/`` subdirectories."""
    for name in SUBDIRS:
        (root_dir / name).mkdir(parents=True, exist_ok=True)
    logger.debug("Ensured recording directories under {}", root_dir)


def validate_root(root_dir: Path) -> list[str]:
    """Check that the recording directory structure exists.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    if not root_dir.is_dir():
        errors.append(f"Root directory does not exist: {root_dir}")
        return errors
    for name in SUBDIRS:
        if not (root_dir / name).is_dir():
            errors.append(f"Missing subdirectory: {root_dir / name}")
    return errors


def to_process_dir(root_dir: Path) -> Path:
    return root_dir / "to-process"


def final_dir(root_dir: Path) -> Path:
    return root_dir / "final"


def archive_dir(root_dir: Path) -> Path:
    return root_dir / "archive"


def superseded_dir(root_dir: Path) -> Path:
    return root_dir / "superseded"


def takes_dir(root_dir: Path) -> Path:
    """Return the ``takes/`` directory — history of superseded processed takes."""
    return root_dir / "takes"


def find_pending_pairs(
    to_process: Path,
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
) -> list[PendingPair]:
    """Scan ``to-process/`` for raw video + audio pairs ready for assembly.

    A pair is "pending" when both ``<topic>--RAW.<video-ext>`` and
    ``<topic>--RAW.wav`` exist in the same directory.
    """
    pairs: list[PendingPair] = []

    for video_file in sorted(to_process.rglob("*")):
        if not video_file.is_file():
            continue
        if video_file.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        stem = video_file.stem
        _, is_raw = parse_raw_stem(stem, raw_suffix)
        if not is_raw:
            continue

        audio_file = video_file.with_name(f"{stem}.wav")
        if audio_file.is_file():
            relative_dir = video_file.parent.relative_to(to_process)
            pairs.append(
                PendingPair(
                    video=video_file,
                    audio=audio_file,
                    relative_dir=relative_dir,
                    raw_suffix=raw_suffix,
                )
            )

    logger.debug("Found {} pending pair(s) in {}", len(pairs), to_process)
    return pairs
