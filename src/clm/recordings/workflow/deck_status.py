"""Deck recording status scanning for the lectures UI.

Scans the recordings directory tree to determine the recording state
of each slide deck: whether it has been recorded, processed, has
pending pairs ready for assembly, or has failed jobs.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

from .directories import final_dir, to_process_dir
from .naming import DEFAULT_RAW_SUFFIX, find_existing_recordings, parse_part


class DeckRecordingState(enum.Enum):
    """Recording status for a single slide deck."""

    NO_RECORDING = "no_recording"
    RECORDED = "recorded"  # Raw video exists, no processed audio
    READY = "ready"  # Video + audio pair exists (pending assembly)
    PROCESSING = "processing"  # Job is queued or in progress
    COMPLETED = "completed"  # Final output exists
    FAILED = "failed"  # Job failed for this deck


@dataclass
class DeckStatus:
    """Recording status for a deck, including part information."""

    state: DeckRecordingState
    parts: list[int] = field(default_factory=list)
    raw_parts: list[int] = field(default_factory=list)
    raw_paths: list[Path] = field(default_factory=list)
    final_parts: list[int] = field(default_factory=list)
    has_final: bool = False
    has_raw: bool = False
    has_pair: bool = False
    failed_job_id: str | None = None


def scan_deck_status(
    root: Path,
    course_slug: str,
    section_name: str,
    deck_name: str,
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    failed_jobs: dict[str, str] | None = None,
    active_jobs: dict[str, str] | None = None,
) -> DeckStatus:
    """Check ``to-process/`` and ``final/`` for files matching a deck.

    Args:
        root: Recordings root directory.
        course_slug: Sanitized course slug (used as directory name).
        section_name: Sanitized section name (used as directory name).
        deck_name: The deck name (will be sanitized for matching).
        raw_suffix: Raw filename suffix.
        failed_jobs: Optional dict mapping deck names to job IDs
            for failed jobs.
        active_jobs: Optional dict mapping deck names to job IDs
            for queued/in-progress jobs.

    Returns:
        :class:`DeckStatus` with the current state and part information.
    """
    from clm.core.utils.text_utils import sanitize_file_name

    sanitized = sanitize_file_name(deck_name)

    tp_dir = to_process_dir(root) / course_slug / section_name
    f_dir = final_dir(root) / course_slug / section_name

    # Scan to-process for raw recordings
    existing = find_existing_recordings(tp_dir, deck_name, raw_suffix)
    parts = sorted(existing.keys())
    raw_paths = [existing[p] for p in parts]
    has_raw = len(existing) > 0

    # Check for companion audio (pair = ready for assembly)
    has_pair = False
    for path in existing.values():
        if path.with_suffix(".wav").exists():
            has_pair = True
            break

    # Scan final/ for completed outputs. Filter by video extension so
    # Auphonic's companion files (e.g. ``<stem>.edl`` written alongside
    # the output mp4) don't double-count into ``final_parts``.
    from clm.recordings.processing.batch import VIDEO_EXTENSIONS

    final_parts: list[int] = []
    if f_dir.is_dir():
        for child in f_dir.iterdir():
            if not child.is_file():
                continue
            if child.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            base, part_num = parse_part(child.stem)
            if base == sanitized:
                final_parts.append(part_num)
    final_parts.sort()
    has_final = len(final_parts) > 0

    # All known parts = union of raw and final
    all_parts = sorted(set(parts) | set(final_parts))

    # Check for failed jobs
    failed_id = None
    if failed_jobs and deck_name in failed_jobs:
        failed_id = failed_jobs[deck_name]

    # Check for active (queued/in-progress) jobs
    is_processing = bool(active_jobs and deck_name in active_jobs)

    # Determine state:
    # COMPLETED only when final output exists AND no unprocessed raw files remain
    if has_final and not has_raw:
        state = DeckRecordingState.COMPLETED
    elif is_processing:
        state = DeckRecordingState.PROCESSING
    elif has_pair:
        state = DeckRecordingState.READY
    elif has_raw:
        state = DeckRecordingState.RECORDED
    elif has_final:
        # All parts processed (raw files cleaned up)
        state = DeckRecordingState.COMPLETED
    elif failed_id:
        state = DeckRecordingState.FAILED
    else:
        state = DeckRecordingState.NO_RECORDING

    return DeckStatus(
        state=state,
        parts=all_parts,
        raw_parts=parts,
        raw_paths=raw_paths,
        final_parts=final_parts,
        has_final=has_final,
        has_raw=has_raw,
        has_pair=has_pair,
        failed_job_id=failed_id,
    )


def scan_section_deck_statuses(
    root: Path,
    course_slug: str,
    section_name: str,
    deck_names: list[str],
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    failed_jobs: dict[str, str] | None = None,
    active_jobs: dict[str, str] | None = None,
) -> dict[str, DeckStatus]:
    """Scan status for all decks in a section.

    Returns a dict mapping deck names to their :class:`DeckStatus`.
    """
    return {
        name: scan_deck_status(
            root, course_slug, section_name, name, raw_suffix, failed_jobs, active_jobs
        )
        for name in deck_names
    }
