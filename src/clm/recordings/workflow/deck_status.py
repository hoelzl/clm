"""Deck recording status scanning for the lectures UI.

Scans the recordings directory tree to determine the recording state
of each slide deck: whether it has been recorded, processed, has
pending pairs ready for assembly, or has failed jobs.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

from .directories import final_dir, takes_dir, to_process_dir
from .naming import (
    DEFAULT_RAW_SUFFIX,
    find_existing_recordings,
    parse_part,
    parse_part_take,
    parse_raw_stem,
)


class DeckRecordingState(enum.Enum):
    """Recording status for a single slide deck."""

    NO_RECORDING = "no_recording"
    RECORDED = "recorded"  # Raw video exists, no processed audio
    READY = "ready"  # Video + audio pair exists (pending assembly)
    PROCESSING = "processing"  # Job is queued or in progress
    COMPLETED = "completed"  # Final output exists
    FAILED = "failed"  # Job failed for this deck


@dataclass
class PartStatus:
    """Per-part recording state used by the chip strip in the lectures UI.

    The chip strip renders one chip per known part plus a trailing
    "next part" placeholder. ``state`` drives the chip's color class
    (``chip-status-<state>``); ``has_failed_retry`` adds the small red
    corner dot for the "processed but the most recent retry failed"
    case.
    """

    part: int
    state: str  # "recorded" | "processed" | "processing" | "failed"
    take_count: int = 1
    has_failed_retry: bool = False
    job_id: str | None = None


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
    parts_status: list[PartStatus] = field(default_factory=list)


def scan_deck_status(
    root: Path,
    course_slug: str,
    section_name: str,
    deck_name: str,
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    failed_jobs: dict[str, str] | None = None,
    active_jobs: dict[str, str] | None = None,
    failed_jobs_per_part: dict[tuple[str, int], str] | None = None,
    active_jobs_per_part: dict[tuple[str, int], str] | None = None,
    take_counts: dict[int, int] | None = None,
) -> DeckStatus:
    """Check ``to-process/`` and ``final/`` for files matching a deck.

    Args:
        root: Recordings root directory.
        course_slug: Sanitized course slug (used as directory name).
        section_name: Sanitized section name (used as directory name).
        deck_name: The deck name (will be sanitized for matching).
        raw_suffix: Raw filename suffix.
        failed_jobs: Optional dict mapping deck names to job IDs
            for failed jobs (deck-level badge).
        active_jobs: Optional dict mapping deck names to job IDs
            for queued/in-progress jobs (deck-level badge).
        failed_jobs_per_part: Optional dict mapping ``(deck, part)``
            to job IDs for the chip-strip per-part view.
        active_jobs_per_part: Optional dict mapping ``(deck, part)``
            to job IDs for the chip-strip per-part view.
        take_counts: Optional dict mapping part numbers to total take
            count (active + superseded). Defaults to ``{}``.

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

    parts_status = _compute_parts_status(
        deck_name=deck_name,
        all_parts=all_parts,
        raw_parts=set(parts),
        final_parts=set(final_parts),
        has_pair=has_pair,
        failed_per_part=failed_jobs_per_part or {},
        active_per_part=active_jobs_per_part or {},
        take_counts=take_counts or {},
    )

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
        parts_status=parts_status,
    )


def _compute_parts_status(
    *,
    deck_name: str,
    all_parts: list[int],
    raw_parts: set[int],
    final_parts: set[int],
    has_pair: bool,
    failed_per_part: dict[tuple[str, int], str],
    active_per_part: dict[tuple[str, int], str],
    take_counts: dict[int, int],
) -> list[PartStatus]:
    """Build per-part chip data from scan results + per-part job/take info."""
    out: list[PartStatus] = []
    for part in all_parts:
        in_raw = part in raw_parts
        in_final = part in final_parts
        active_id = active_per_part.get((deck_name, part))
        failed_id = failed_per_part.get((deck_name, part))

        if active_id:
            state = "processing"
        elif in_final and not in_raw:
            state = "processed"
        elif in_raw:
            # Raw on disk and either no final yet or both — both cases
            # surface as "recorded" so the chip color tracks "needs
            # processing". The has_pair flag is informational only.
            state = "recorded"
        elif failed_id:
            state = "failed"
        else:
            # Nothing on disk for this part — should not normally happen
            # because all_parts is the union of raw + final, but be safe.
            state = "recorded"

        out.append(
            PartStatus(
                part=part,
                state=state,
                take_count=take_counts.get(part, 1),
                has_failed_retry=in_final and bool(failed_id),
                job_id=active_id or failed_id,
            )
        )
    return out


def scan_section_takes(
    root: Path,
    course_slug: str,
    section_name: str,
    deck_names: list[str],
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
) -> dict[str, dict[int, list[int]]]:
    """Scan the section's ``takes/`` directory once for every deck name.

    Returns a mapping of ``deck_name -> {part: [sorted take numbers]}``.
    Decks with no superseded takes are present with an empty inner dict.

    Take entries are deduped by ``(part, take)`` so that a take with
    both raw and final files only counts once. Files without a
    ``(take K)`` suffix are ignored — those belong to the active take.
    """
    from clm.core.utils.text_utils import sanitize_file_name

    sanitized_to_name = {sanitize_file_name(n): n for n in deck_names}
    out: dict[str, dict[int, set[int]]] = {n: {} for n in deck_names}

    subtree = takes_dir(root) / sanitize_file_name(course_slug) / sanitize_file_name(section_name)
    if not subtree.is_dir():
        return {n: {} for n in deck_names}

    for child in subtree.iterdir():
        if not child.is_file():
            continue
        stem = child.stem
        base_with, is_raw = parse_raw_stem(stem, raw_suffix)
        base, part, take = parse_part_take(base_with if is_raw else stem)
        if take == 0:
            continue
        deck = sanitized_to_name.get(base)
        if deck is None:
            continue
        out[deck].setdefault(part, set()).add(take)

    return {n: {p: sorted(ts) for p, ts in m.items()} for n, m in out.items()}


def take_counts_from_section_takes(
    section_takes: dict[int, list[int]],
    *,
    known_parts: list[int],
) -> dict[int, int]:
    """Combine ``takes/`` history with the active take to get a chip count.

    For every known part, ``count = len(superseded_takes_for_part) + 1``
    (the active take). Parts without a ``takes/`` entry get ``1``.
    """
    return {part: len(section_takes.get(part, [])) + 1 for part in known_parts}


@dataclass
class TakeFileInfo:
    """One superseded take, surfaced in the inline take-history panel."""

    take: int
    raw_path: Path | None
    final_path: Path | None
    raw_size: int | None = None
    final_size: int | None = None
    recorded_at: float | None = None  # POSIX timestamp (mtime), latest of the two

    @property
    def display_stem(self) -> str:
        path = self.final_path or self.raw_path
        return path.stem if path else ""

    @property
    def kind(self) -> str:
        if self.final_path is not None:
            return "processed"
        if self.raw_path is not None:
            return "recorded"
        return "unknown"


def scan_take_files(
    root: Path,
    course_slug: str,
    section_name: str,
    deck_name: str,
    *,
    part: int,
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
) -> list[TakeFileInfo]:
    """Return one ``TakeFileInfo`` per superseded take for the deck/part.

    Pairs raw and final files that share the same ``(part N, take K)``
    suffix. Returned list is sorted by take number ascending.
    """
    from clm.core.utils.text_utils import sanitize_file_name
    from clm.recordings.processing.batch import VIDEO_EXTENSIONS

    sanitized = sanitize_file_name(deck_name)
    subtree = takes_dir(root) / sanitize_file_name(course_slug) / sanitize_file_name(section_name)
    if not subtree.is_dir():
        return []

    by_take: dict[int, TakeFileInfo] = {}
    for child in subtree.iterdir():
        if not child.is_file():
            continue
        if child.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        stem = child.stem
        base_with, is_raw = parse_raw_stem(stem, raw_suffix)
        base, p, take = parse_part_take(base_with if is_raw else stem)
        if take == 0 or p != part or base != sanitized:
            continue

        info = by_take.setdefault(take, TakeFileInfo(take=take, raw_path=None, final_path=None))
        try:
            size = child.stat().st_size
            mtime = child.stat().st_mtime
        except OSError:
            size = None
            mtime = None
        if is_raw:
            info.raw_path = child
            info.raw_size = size
        else:
            info.final_path = child
            info.final_size = size
        if mtime is not None and (info.recorded_at is None or mtime > info.recorded_at):
            info.recorded_at = mtime

    return [by_take[k] for k in sorted(by_take)]


def scan_section_deck_statuses(
    root: Path,
    course_slug: str,
    section_name: str,
    deck_names: list[str],
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    failed_jobs: dict[str, str] | None = None,
    active_jobs: dict[str, str] | None = None,
    failed_jobs_per_part: dict[tuple[str, int], str] | None = None,
    active_jobs_per_part: dict[tuple[str, int], str] | None = None,
) -> dict[str, DeckStatus]:
    """Scan status for all decks in a section.

    Returns a dict mapping deck names to their :class:`DeckStatus`.
    """
    section_takes = scan_section_takes(root, course_slug, section_name, deck_names, raw_suffix)

    result: dict[str, DeckStatus] = {}
    for name in deck_names:
        # First pass to know the part list — needed to compute take counts.
        status = scan_deck_status(
            root,
            course_slug,
            section_name,
            name,
            raw_suffix,
            failed_jobs,
            active_jobs,
            failed_jobs_per_part=failed_jobs_per_part,
            active_jobs_per_part=active_jobs_per_part,
            take_counts=take_counts_from_section_takes(
                section_takes.get(name, {}),
                known_parts=[],  # patched below after we know all_parts
            ),
        )
        # Recompute take_counts now that we have the part list, then
        # rebuild parts_status. Cheaper than running scan_deck_status
        # twice — only the per-part view is regenerated.
        take_counts = take_counts_from_section_takes(
            section_takes.get(name, {}),
            known_parts=status.parts,
        )
        status.parts_status = _compute_parts_status(
            deck_name=name,
            all_parts=status.parts,
            raw_parts=set(status.raw_parts),
            final_parts=set(status.final_parts),
            has_pair=status.has_pair,
            failed_per_part=failed_jobs_per_part or {},
            active_per_part=active_jobs_per_part or {},
            take_counts=take_counts,
        )
        result[name] = status
    return result
