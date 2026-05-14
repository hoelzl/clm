"""Recording session state machine.

Coordinates the recording workflow by tracking which slide deck is "armed"
for recording and responding to OBS events to rename the output file
into the structured directory layout.

State transitions::

    idle ──arm()──► armed ──OBS starts──► recording ──OBS stops──► renaming ──done──┐
      ▲               │                    ▲  │ ▲                                    │
      │               │                    │  │ │ OBS resumes                        ▼
      │               │                    │  ▼ │                                    │
      │               │                    │ paused ─ OBS stops ──────► renaming ────┤
      │               │                    │                                          │
      │               │                    └── OBS starts ── armed_after_take ◄───────┤
      │               │                                         │                    │
      │               │                                         ▼                    │
      └──disarm()─────┴────────────────── timer expires ────────┴────────────────────┘

A "short take" (OBS stops within ``short_take_seconds`` of starting) is
treated as an accidental start-then-stop: the output goes to
``superseded/`` and the session returns to ``ARMED`` with the same deck
intact, so the user can start again without re-arming.

A "retake" starts when OBS begins recording again inside the
``retake_window_seconds`` after a normal take completes. The new
recording is associated with the same armed deck; the old raw is
superseded via the existing ``_prepare_target_slot`` cascade.

The session manager is **thread-safe**.  OBS events arrive on a background
thread (from ``obsws-python``), while :meth:`arm` / :meth:`disarm` are
called from the main thread or a web request handler.
"""

from __future__ import annotations

import enum
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.recordings.state import CourseRecordingState

from loguru import logger

from clm.core.utils.text_utils import sanitize_file_name

from .directories import archive_dir, final_dir, superseded_dir, takes_dir, to_process_dir
from .naming import (
    DEFAULT_RAW_SUFFIX,
    final_filename,
    find_existing_recordings,
    parse_part,
    parse_part_take,
    parse_raw_stem,
    raw_filename,
    recording_relative_dir,
    take_filename,
)
from .obs import ObsClient, RecordingEvent
from .rename_queue import PendingRenameQueue
from .safe_move import FileLockedError, safe_move


class SessionState(enum.Enum):
    """Recording session states."""

    IDLE = "idle"
    ARMED = "armed"
    RECORDING = "recording"
    PAUSED = "paused"
    """OBS has paused the recording — output file is still open.

    The armed deck is kept intact; a subsequent OBS
    ``OBS_WEBSOCKET_OUTPUT_RESUMED`` event returns the session to
    :attr:`RECORDING`, while a ``OBS_WEBSOCKET_OUTPUT_STOPPED`` triggers
    the normal rename path as if the pause had not happened.
    """
    RENAMING = "renaming"
    ARMED_AFTER_TAKE = "armed_after_take"
    """A take has completed and the deck remains armed for a short window.

    A new OBS recording that begins before the window expires is
    treated as a retake of the same deck. The window expiring
    transitions the session to :attr:`IDLE` and clears the armed deck.
    """


@dataclass(frozen=True)
class ArmedDeck:
    """Identifies the slide deck armed for the next recording.

    ``lecture_id`` is optional so existing CLI/tests can construct
    decks without a course-state identity; the web app is expected to
    resolve it from the course spec at ``/arm`` time so the session
    can keep ``state.json`` in sync with filesystem renames.
    """

    course_slug: str
    section_name: str
    deck_name: str
    part_number: int = 0
    lang: str = "en"
    lecture_id: str | None = None


# Keep old name as alias for backward compatibility during transition
ArmedTopic = ArmedDeck


@dataclass
class SessionSnapshot:
    """Immutable snapshot of session state for UI consumption."""

    state: SessionState
    armed_deck: ArmedDeck | None = None
    obs_connected: bool = False
    obs_state: str = "disconnected"
    last_output: Path | None = None
    error: str | None = None
    recording_elapsed_seconds: float | None = None
    """Seconds of *actual recording time* so far, excluding pause windows.

    ``None`` when no recording is active. When a recording is in
    progress, the UI captures this value on render and extrapolates
    client-side via ``setInterval`` so the elapsed-time display ticks
    smoothly without per-second server chatter. During :attr:`SessionState.PAUSED`
    the value is frozen at the moment OBS paused so the displayed timer
    does not tick; resuming unfreezes it.
    """
    paused: bool = False
    """``True`` while the session is in :attr:`SessionState.PAUSED`.

    Exposed as a plain boolean rather than requiring callers to compare
    against the enum so templates can use the flag directly.
    """

    @property
    def armed_topic(self) -> ArmedDeck | None:
        """Deprecated alias for :attr:`armed_deck`."""
        return self.armed_deck


def _next_take_number(
    takes_subtree: Path,
    deck_name: str,
    part: int,
) -> int:
    """Return the next take number to assign when demoting the active take.

    Scans *takes_subtree* for files named ``deck (part N, take K).*`` (or
    ``deck (take K).*`` for single-part lectures) and returns ``max(K) + 1``.
    Returns ``1`` if no historical takes exist — i.e., the take being
    demoted right now is the first one to enter the history shelf.
    """
    if not takes_subtree.is_dir():
        return 1

    sanitized = sanitize_file_name(deck_name)
    highest = 0
    for child in takes_subtree.iterdir():
        if not child.is_file():
            continue
        stem = child.stem
        base_with, is_raw = parse_raw_stem(stem)
        if is_raw:
            base, p, take = parse_part_take(base_with)
        else:
            base, p, take = parse_part_take(stem)
        if take == 0:
            continue
        if p != part:
            continue
        if base != sanitized:
            continue
        highest = max(highest, take)
    return highest + 1


def _scan_active_take_files(
    *,
    recordings_root: Path,
    rel_dir: str,
    deck_name: str,
    part: int,
    raw_suffix: str,
    lang: str,
) -> list[Path]:
    """Collect the filesystem paths that belong to the active take of *part*.

    Returns a list of existing paths across ``to-process/``, ``archive/``,
    and ``final/`` that would collide with a new recording of the same
    part. Anchors on the video file in each location and then sweeps in
    every sibling that shares its stem — picks up companions like the
    raw ``.wav`` and the final ``.edl`` cut list, and any future sidecar
    Auphonic emits (``.vtt``/``.srt``/``.json``/``.html``) without
    needing a hard-coded extension list.

    Files are returned in a deterministic order for reproducibility.
    """
    from clm.recordings.processing.batch import VIDEO_EXTENSIONS

    sanitized = sanitize_file_name(deck_name)
    result: list[Path] = []

    # Raw candidates in to-process/ and archive/. Anchor on the --RAW
    # video file matching deck+part, then collect every sibling that
    # shares its stem (the .wav companion today; future raw sidecars
    # tomorrow).
    for base_dir in (to_process_dir(recordings_root), archive_dir(recordings_root)):
        subtree = base_dir / rel_dir
        if not subtree.is_dir():
            continue
        anchor_stems: set[str] = set()
        for child in subtree.iterdir():
            if not child.is_file() or child.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            base_with, is_raw = parse_raw_stem(child.stem, raw_suffix)
            if not is_raw:
                continue
            base, p = parse_part(base_with)
            if base != sanitized or p != part:
                continue
            anchor_stems.add(child.stem)
        if anchor_stems:
            for child in subtree.iterdir():
                if child.is_file() and child.stem in anchor_stems:
                    result.append(child)

    # Final/: anchor on the video, then sweep every sibling sharing its
    # stem (the Auphonic .edl, future .vtt/.srt/.json/.html outputs).
    final_subtree = final_dir(recordings_root) / rel_dir
    if final_subtree.is_dir():
        anchor_stems = set()
        for child in final_subtree.iterdir():
            if not child.is_file() or child.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            base, p = parse_part(child.stem)
            if base == sanitized and p == part:
                anchor_stems.add(child.stem)
        if anchor_stems:
            for child in final_subtree.iterdir():
                if child.is_file() and child.stem in anchor_stems:
                    result.append(child)

    return result


def _classify_retake_source(path: Path, recordings_root: Path) -> str:
    """Return ``"raw"`` for to-process/archive files, ``"final"`` otherwise."""
    try:
        path.relative_to(final_dir(recordings_root))
        return "final"
    except ValueError:
        return "raw"


def _preserve_active_take(
    *,
    recordings_root: Path,
    rel_dir: str,
    deck_name: str,
    part: int,
    raw_suffix: str,
    lang: str,
    take_number: int | None = None,
    pending: PendingRenameQueue | None = None,
) -> list[tuple[Path, Path]]:
    """Move the active take's files into ``takes/`` with ``(part N, take K)`` suffixes.

    Returns the list of ``(old_path, new_path)`` pairs actually performed.
    If no active-take files are present, returns an empty list.

    When *take_number* is given (recommended path: caller has access to
    ``state.json``'s ``active_take`` for this part), the demoted files
    use that exact ``K`` so the filename matches the take's stable
    identity. After a restore-then-retake sequence, the filesystem max
    diverges from the state's view (state still calls the restored take
    "1" while ``takes/`` already holds "(take 2)"), so trusting the FS
    heuristic would produce a "(take 3)" filename for what state names
    take 1 — breaking the take-number-as-identity invariant. Callers
    without state access (CLI flows) fall back to the FS heuristic
    ``max(existing_takes_for_part) + 1``.
    """
    files = _scan_active_take_files(
        recordings_root=recordings_root,
        rel_dir=rel_dir,
        deck_name=deck_name,
        part=part,
        raw_suffix=raw_suffix,
        lang=lang,
    )
    if not files:
        return []

    takes_subtree = takes_dir(recordings_root) / rel_dir
    if take_number is None:
        take_number = _next_take_number(takes_subtree, deck_name, part)
    takes_subtree.mkdir(parents=True, exist_ok=True)

    renames: list[tuple[Path, Path]] = []
    for src in files:
        kind = _classify_retake_source(src, recordings_root)
        ext = src.suffix
        if kind == "raw" and ext.lower() == ".wav":
            name = take_filename(
                deck_name,
                ext=".wav",
                raw_suffix=raw_suffix,
                part=part,
                take=take_number,
                is_raw=True,
                lang=lang,
            )
        elif kind == "raw":
            name = take_filename(
                deck_name,
                ext=ext,
                raw_suffix=raw_suffix,
                part=part,
                take=take_number,
                is_raw=True,
                lang=lang,
            )
        else:  # final
            name = take_filename(
                deck_name,
                ext=ext,
                raw_suffix=raw_suffix,
                part=part,
                take=take_number,
                is_raw=False,
                lang=lang,
            )
        dest = takes_subtree / name
        # Should be clean because `take_number` is strictly greater than
        # every existing take; defensive collision-handling is still cheap.
        if dest.exists():
            counter = 2
            while dest.exists():
                dest = takes_subtree / f"{dest.stem} ({counter}){dest.suffix}"
                counter += 1
        try:
            safe_move(src, dest)
        except FileLockedError as exc:
            if pending is None:
                raise
            pending.try_or_defer(src, dest, reason="preserve-active-take")
            logger.warning(
                "Preserve-active-take deferred for {} (locked by {}): {}",
                src.name,
                exc.last_error,
                dest,
            )
            continue
        logger.info("Preserved active take {} → {}", src.name, dest)
        renames.append((src, dest))

    return renames


def _scan_takes_for(
    takes_subtree: Path,
    deck_name: str,
    part: int,
    take: int,
    raw_suffix: str,
) -> tuple[list[Path], list[Path]]:
    """Find all files in *takes_subtree* belonging to the given (deck, part, take).

    Returns ``(raw_files, final_files)``. Raw files have the ``--RAW`` suffix
    in their stem (video + companion ``.wav`` share the stem and both end up
    here); final files have no raw suffix. Either list may be empty when only
    one variant was preserved.
    """
    raw_files: list[Path] = []
    final_files: list[Path] = []
    if not takes_subtree.is_dir():
        return raw_files, final_files

    sanitized = sanitize_file_name(deck_name)
    for child in takes_subtree.iterdir():
        if not child.is_file():
            continue
        base_with, is_raw = parse_raw_stem(child.stem, raw_suffix)
        base, p, k = parse_part_take(base_with if is_raw else child.stem)
        if base != sanitized or p != part or k != take:
            continue
        (raw_files if is_raw else final_files).append(child)
    return raw_files, final_files


def _swap_active_with_take(
    *,
    recordings_root: Path,
    rel_dir: str,
    deck_name: str,
    part: int,
    active_take: int,
    target_take: int,
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    lang: str = "en",
) -> list[tuple[Path, Path]]:
    """Swap the active take's files with the historical take *target_take*.

    The active take's files (raw in ``to-process/`` or ``archive/`` plus a
    companion ``.wav``; final in ``final/``) are moved into ``takes/`` with
    a ``(part N, take active_take)`` suffix, while the target take's files
    move out of ``takes/`` into the active slots:

    * Target raw → ``archive/`` when a target-final exists in ``takes/``
      (the take had been processed), else ``to-process/``. Companion
      ``.wav`` goes alongside.
    * Target final → ``final/``.

    Whether the target take was previously processed is detected from
    the filesystem (presence of a final-shaped file in ``takes/``) and
    not from ``state.json`` — the manual ``/process`` route doesn't
    update ``processed_file`` on the active part, so trusting state
    here would dump processed raws back into ``to-process/`` and make
    the chip flip back to amber after a restore.

    Implements a planned-rename rollback: phase A moves active → takes/,
    phase B moves takes/ → active. If any move fails, every completed
    move (in either phase) is reversed in LIFO order before the original
    exception is re-raised. If rollback itself fails, the rollback error
    is logged and the original exception still propagates.

    Raises:
        FileNotFoundError: If no target-take files exist in ``takes/``.
        FileExistsError: If a destination already exists at plan time.
    """
    takes_subtree = takes_dir(recordings_root) / rel_dir
    target_raws, target_finals = _scan_takes_for(
        takes_subtree, deck_name, part, target_take, raw_suffix
    )
    if not target_raws and not target_finals:
        raise FileNotFoundError(
            f"No files for take {target_take} of {deck_name} part {part} in {takes_subtree}"
        )
    # Filesystem-derived: a target final in takes/ means the take had
    # been processed and its raw belongs back in archive/, not to-process/.
    target_processed = bool(target_finals)

    active_files = _scan_active_take_files(
        recordings_root=recordings_root,
        rel_dir=rel_dir,
        deck_name=deck_name,
        part=part,
        raw_suffix=raw_suffix,
        lang=lang,
    )

    # Phase A plan: active → takes/ with (part N, take active_take) suffix.
    plan_a: list[tuple[Path, Path]] = []
    takes_subtree.mkdir(parents=True, exist_ok=True)
    for src in active_files:
        kind = _classify_retake_source(src, recordings_root)
        ext = src.suffix
        is_raw = kind == "raw"
        dst_name = take_filename(
            deck_name,
            ext=ext,
            raw_suffix=raw_suffix,
            part=part,
            take=active_take,
            is_raw=is_raw,
            lang=lang,
        )
        plan_a.append((src, takes_subtree / dst_name))

    # Phase B plan: target files (in takes/) → active slots.
    raw_dest_dir = (
        archive_dir(recordings_root) / rel_dir
        if target_processed
        else to_process_dir(recordings_root) / rel_dir
    )
    final_dest_dir = final_dir(recordings_root) / rel_dir
    plan_b: list[tuple[Path, Path]] = []
    for src in target_raws:
        # Both the video and the companion ``.wav`` (same stem, different
        # ext) get the active-slot raw name with their own extension.
        dst_name = raw_filename(
            deck_name, ext=src.suffix, raw_suffix=raw_suffix, part=part, lang=lang
        )
        plan_b.append((src, raw_dest_dir / dst_name))
    for src in target_finals:
        ext = src.suffix
        dst_name = final_filename(deck_name, ext=ext, part=part, lang=lang)
        plan_b.append((src, final_dest_dir / dst_name))

    # Pre-flight: phase B destinations must be free *after* phase A runs,
    # which happens iff each phase B destination is either empty now or
    # only occupied by an active-take source we are about to move out.
    phase_a_sources = {src for src, _ in plan_a}
    for _src, dst in plan_b:
        if dst.exists() and dst not in phase_a_sources:
            raise FileExistsError(f"Destination {dst} is occupied by an unexpected file")
    # Phase A destinations should never collide either.
    for _src, dst in plan_a:
        if dst.exists():
            raise FileExistsError(f"Take slot {dst} already exists in takes/")

    completed: list[tuple[Path, Path]] = []
    try:
        for src, dst in plan_a:
            dst.parent.mkdir(parents=True, exist_ok=True)
            safe_move(src, dst)
            logger.info("Demoted active → takes/: {} → {}", src.name, dst)
            completed.append((src, dst))
        for src, dst in plan_b:
            dst.parent.mkdir(parents=True, exist_ok=True)
            safe_move(src, dst)
            logger.info("Restored take {} → active: {} → {}", target_take, src.name, dst)
            completed.append((src, dst))
    except Exception:
        for src, dst in reversed(completed):
            try:
                safe_move(dst, src)
                logger.warning("Rolled back swap: {} → {}", dst, src)
            except Exception as roll_exc:
                logger.error("Rollback failed for {} → {}: {}", dst, src, roll_exc)
        raise

    return completed


def _prepare_target_slot(
    target_dir: Path,
    deck_name: str,
    ext: str,
    part: int,
    raw_suffix: str,
    recordings_root: Path,
    lang: str = "en",
    pending: PendingRenameQueue | None = None,
) -> tuple[Path, list[tuple[Path, Path]]]:
    """Ensure the target slot is clear and handle dynamic part renaming.

    Implements these rules:

    - If the user records part N>0, every existing unsuffixed (part 0)
      file — raw in ``to-process/`` or ``archive/`` (plus ``.wav``
      companion), final in ``final/`` — is renamed to ``(part 1)`` so
      the numbering stays consistent even when earlier parts have
      already been processed and moved out of ``to-process/``.
    - If the computed target already exists, supersede it.
    - Single recording = no suffix; multiple parts = all get ``(part N)``.

    Returns the final target :class:`Path` for the new recording along with
    the list of ``(old_path, new_path)`` pairs renamed on disk so that
    callers can update any external path index (e.g. ``state.json``).

    ``pending`` (when supplied) is used by the helpers to defer renames
    that hit a Windows file lock — the rename pipeline keeps moving
    instead of aborting the whole take, and the queue is drained later
    when the lock holder (typically an Auphonic upload) finishes.
    """
    renames: list[tuple[Path, Path]] = []

    if part > 0:
        renames.extend(
            _cascade_unsuffixed_to_part1(
                recordings_root=recordings_root,
                target_dir=target_dir,
                deck_name=deck_name,
                raw_suffix=raw_suffix,
                lang=lang,
                pending=pending,
            )
        )

    target_name = raw_filename(deck_name, ext=ext, raw_suffix=raw_suffix, part=part, lang=lang)
    target = target_dir / target_name

    if target.exists():
        _supersede_file(target, recordings_root, pending=pending)

    return target, renames


def _cascade_unsuffixed_to_part1(
    *,
    recordings_root: Path,
    target_dir: Path,
    deck_name: str,
    raw_suffix: str,
    lang: str,
    pending: PendingRenameQueue | None = None,
) -> list[tuple[Path, Path]]:
    """Promote every unsuffixed (part 0) file for *deck_name* to ``(part 1)``.

    Runs when the user records a part > 0 so the prior single-part take
    slots in next to the new part. Covers four locations:

    * ``to-process/`` — raw video + every companion sharing its stem
                        (``.wav`` audio today, future sidecars tomorrow)
    * ``archive/``    — raw video + companions (post-processing home;
                        earlier versions skipped this, leaving a stale
                        unsuffixed raw on disk)
    * ``final/``      — processed video + every file Auphonic writes
                        alongside it (``.edl`` cut list today;
                        ``.vtt``/``.srt`` subtitles, ``.json``/``.html``
                        transcripts once those backends ship)
    * ``takes/``      — every superseded ``(take K)`` file gets promoted
                        to ``(part 1, take K)`` so the take history stays
                        consistent with the now-multi-part naming.
                        Without this, takes recorded before the deck
                        became multi-part keep their old single-part
                        names forever, and the take-history panel can't
                        match them to the newly-suffixed active slot.

    The match is stem-based rather than extension-based so any future
    companion format is handled without further changes here.

    Lock contention on any individual file is surfaced through *pending*
    when supplied (the rename is parked and re-attempted later). When
    *pending* is ``None``, :class:`FileLockedError` propagates so callers
    that need atomic semantics (e.g. CLI tests) still see the error.
    """
    renames: list[tuple[Path, Path]] = []
    sanitized = sanitize_file_name(deck_name)

    tp = to_process_dir(recordings_root)
    try:
        rel = target_dir.relative_to(tp)
    except ValueError:
        # Out-of-tree target — no predictable archive/final mirror.
        return renames
    rel_str = str(rel)

    for base_dir in (tp, archive_dir(recordings_root)):
        subtree = base_dir / rel_str
        existing = find_existing_recordings(subtree, deck_name, raw_suffix)
        unsuffixed = existing.get(0)
        if unsuffixed is None:
            continue
        old_stem = unsuffixed.stem
        new_stem = raw_filename(deck_name, ext="", raw_suffix=raw_suffix, part=1, lang=lang)
        renames.extend(_rename_siblings_by_stem(subtree, old_stem, new_stem, pending=pending))

    fd = final_dir(recordings_root) / rel_str
    if fd.is_dir():
        new_stem = final_filename(deck_name, ext="", part=1, lang=lang)
        renames.extend(_rename_siblings_by_stem(fd, sanitized, new_stem, pending=pending))

    renames.extend(
        _promote_takes_to_part1(
            takes_subtree=takes_dir(recordings_root) / rel_str,
            deck_name=deck_name,
            raw_suffix=raw_suffix,
            lang=lang,
            pending=pending,
        )
    )

    return renames


def _promote_takes_to_part1(
    *,
    takes_subtree: Path,
    deck_name: str,
    raw_suffix: str,
    lang: str,
    pending: PendingRenameQueue | None,
) -> list[tuple[Path, Path]]:
    """Rename every ``<deck> (take K).<ext>`` in ``takes/`` to ``<deck> (part 1, take K).<ext>``.

    Companion to :func:`_cascade_unsuffixed_to_part1`'s
    to-process/archive/final pass: when the deck transitions from
    single-part to multi-part, the take-history shelf needs the same
    renaming so the panel can match historical takes to the now-suffixed
    active slot. Files that already have a ``(part N, take K)`` form are
    left alone.
    """
    renames: list[tuple[Path, Path]] = []
    if not takes_subtree.is_dir():
        return renames
    sanitized = sanitize_file_name(deck_name)
    for child in sorted(takes_subtree.iterdir()):
        if not child.is_file():
            continue
        stem = child.stem
        base_with, is_raw = parse_raw_stem(stem, raw_suffix)
        base, p, take = parse_part_take(base_with if is_raw else stem)
        if base != sanitized or p != 0 or take == 0:
            continue
        new_name = take_filename(
            deck_name,
            ext=child.suffix,
            raw_suffix=raw_suffix,
            part=1,
            take=take,
            is_raw=is_raw,
            lang=lang,
        )
        new_path = takes_subtree / new_name
        if new_path.exists():
            continue
        try:
            safe_move(child, new_path)
        except FileLockedError as exc:
            if pending is None:
                raise
            pending.try_or_defer(child, new_path, reason="cascade-takes-to-part1")
            logger.warning(
                "Cascade promote-take deferred ({}): {} (locked by {})",
                child.name,
                new_path,
                exc.last_error,
            )
            continue
        logger.info("Promoted take to multi-part: {} → {}", child.name, new_path)
        renames.append((child, new_path))
    return renames


def _rename_siblings_by_stem(
    directory: Path,
    old_stem: str,
    new_stem: str,
    *,
    pending: PendingRenameQueue | None = None,
) -> list[tuple[Path, Path]]:
    """Rename every file in *directory* whose stem equals *old_stem*.

    The extension is preserved. Files whose destination path already
    exists are skipped defensively; the caller is responsible for not
    calling this on slots that would collide with intentional content.

    When *pending* is supplied, individual lock failures are deferred to
    the queue rather than aborting the loop — the cascade keeps making
    progress on the unlocked siblings, and the deferred ones are
    re-attempted later.

    Returns the list of ``(old, new)`` pairs actually performed in
    :func:`sorted` order so the log trail is deterministic.
    """
    renames: list[tuple[Path, Path]] = []
    if not directory.is_dir():
        return renames
    for child in sorted(directory.iterdir()):
        if not child.is_file():
            continue
        if child.stem != old_stem:
            continue
        new_path = directory / (new_stem + child.suffix)
        if new_path.exists():
            continue
        try:
            safe_move(child, new_path)
        except FileLockedError as exc:
            if pending is None:
                raise
            pending.try_or_defer(child, new_path, reason="cascade-rename-siblings")
            logger.warning(
                "Cascade rename deferred ({}): {} → {} (locked by {})",
                directory.name,
                child.name,
                new_path,
                exc.last_error,
            )
            continue
        logger.info("Renamed {} → {}", child.name, new_path)
        renames.append((child, new_path))
    return renames


def _move_to_superseded_dir(
    src: Path,
    dest_dir: Path,
    *,
    pending: PendingRenameQueue | None = None,
) -> Path | None:
    """Move *src* into *dest_dir*, appending ``(2)``, ``(3)``, … on collision.

    Creates *dest_dir* if needed. Returns the final resolved destination
    path on success, or ``None`` when the move was deferred via *pending*
    because the source is currently locked. Shared by
    :func:`_supersede_file` (replacing a processed take that's being
    re-recorded) and :meth:`RecordingSession._handle_short_take`
    (moving an accidental zero-length take out of OBS's default dir).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem = src.stem
        ext = src.suffix
        counter = 2
        while dest.exists():
            dest = dest_dir / f"{stem} ({counter}){ext}"
            counter += 1
    try:
        safe_move(src, dest)
    except FileLockedError as exc:
        if pending is None:
            raise
        pending.try_or_defer(src, dest, reason="supersede")
        logger.warning(
            "Supersede deferred for {} → {} (locked by {})",
            src.name,
            dest,
            exc.last_error,
        )
        return None
    logger.info("Superseded {} → {}", src.name, dest)
    return dest


def _supersede_file(
    existing: Path,
    recordings_root: Path,
    *,
    pending: PendingRenameQueue | None = None,
) -> None:
    """Move *existing* (and any companion ``.wav``) into ``superseded/``.

    Preserves the directory structure relative to ``to-process/``.
    If a file with the same name already exists in the superseded
    directory, appends ``(2)``, ``(3)``, etc. before the extension.
    """
    tp = to_process_dir(recordings_root)
    try:
        rel = existing.parent.relative_to(tp)
    except ValueError:
        rel = Path(".")

    dest_dir = superseded_dir(recordings_root) / str(rel)
    _move_to_superseded_dir(existing, dest_dir, pending=pending)

    # Also move companion .wav if present
    companion = existing.with_suffix(".wav")
    if companion.exists():
        _move_to_superseded_dir(companion, dest_dir, pending=pending)


class RecordingSession:
    """State machine managing the recording → rename workflow.

    Args:
        obs: Connected (or soon-to-be-connected) OBS client.
        recordings_root: Root directory containing ``to-process/``,
            ``final/``, ``archive/`` subdirectories.
        raw_suffix: Suffix for raw recording filenames (default ``--RAW``).
        stability_interval: Seconds between file-size polls when waiting
            for the recording file to stabilise after OBS stops.
        stability_checks: Number of consecutive identical size readings
            required before considering the file stable.
        short_take_seconds: A recording that stops within this many seconds
            of starting is treated as an accidental short take: its output
            is moved to ``superseded/`` and the session remains armed.
            Default 5 seconds.
        retake_window_seconds: After a successful take, the deck stays
            armed for this many seconds. A new OBS recording that begins
            within the window is associated with the same armed deck as
            a retake. When the window expires, the session transitions
            to :attr:`SessionState.IDLE` and clears the armed deck.
            Default 60 seconds.
        rename_timeout_seconds: Total wall-clock budget for
            :meth:`_wait_for_stable` to wait for OBS to finish writing
            the file. A wedged encoder won't freeze the session forever.
            Default 600 seconds (10 minutes).
        on_state_change: Optional callback invoked (outside the lock)
            after every state transition.  Receives a :class:`SessionSnapshot`.
        state: Optional per-course recording state. When provided, each
            filesystem rename performed by the session (retake pre-move,
            multi-part cascade) is paired with a call to
            :meth:`CourseRecordingState.rename_recording_paths` so the
            state's ``raw_file``/``processed_file`` indices stay in sync
            with disk. Pass ``None`` in tests that don't care about
            state tracking — the filesystem behaviour is unchanged.
        state_provider: Optional per-deck state resolver. Called with
            the :class:`ArmedDeck` at rename time to look up the
            matching :class:`CourseRecordingState` (web dashboard
            use case — one session, many courses). Takes precedence
            over ``state`` when it returns a non-``None`` value.
        on_state_mutation: Optional callback invoked after the
            session mutates a ``CourseRecordingState``. Lets the
            caller persist the state without the session having to
            know how/where state is stored.
        on_path_rename: Optional callback invoked with ``(old, new)``
            paths for every cascade rename the session performs. The
            web dashboard uses this to rewrite in-flight job paths so
            Auphonic lands the output at the renamed stem instead of
            the stale one the job captured at submit time.
        pending_renames: Optional :class:`PendingRenameQueue` shared
            with the rest of the workflow. The session uses it to
            defer file moves that hit a Windows file lock (typically
            an in-flight Auphonic upload), so the recording pipeline
            never aborts mid-take and never produces duplicate files.
            When ``None``, a private queue is allocated; callers that
            need to drain it in response to job-lifecycle events should
            pass their own.
    """

    def __init__(
        self,
        obs: ObsClient,
        recordings_root: Path,
        *,
        raw_suffix: str = DEFAULT_RAW_SUFFIX,
        stability_interval: float = 1.0,
        stability_checks: int = 3,
        short_take_seconds: float = 5.0,
        retake_window_seconds: float = 60.0,
        rename_timeout_seconds: float = 600.0,
        on_state_change: Callable[[SessionSnapshot], None] | None = None,
        state: CourseRecordingState | None = None,
        state_provider: Callable[[ArmedDeck], CourseRecordingState | None] | None = None,
        on_state_mutation: Callable[[CourseRecordingState], None] | None = None,
        on_path_rename: Callable[[Path, Path], None] | None = None,
        pending_renames: PendingRenameQueue | None = None,
    ) -> None:
        self._obs = obs
        self._root = recordings_root
        self._raw_suffix = raw_suffix
        self._stability_interval = stability_interval
        self._stability_checks = stability_checks
        self._short_take_seconds = short_take_seconds
        self._retake_window_seconds = retake_window_seconds
        self._rename_timeout_seconds = rename_timeout_seconds
        self._on_state_change = on_state_change
        self._course_state = state
        self._state_provider = state_provider
        self._on_state_mutation = on_state_mutation
        self._on_path_rename = on_path_rename
        self._pending_renames = pending_renames or PendingRenameQueue()

        self._state = SessionState.IDLE
        self._armed: ArmedDeck | None = None
        self._last_output: Path | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

        # Retake machinery (guarded by the session lock).
        self._recording_started_at: float | None = None
        self._retake_timer: threading.Timer | None = None

        # Pause tracking (guarded by the session lock). ``_paused_elapsed``
        # captures how much recording time had accumulated when OBS
        # paused, so the UI can display a frozen elapsed value during
        # PAUSED and we can resume the timer on RESUMED by rebasing
        # ``_recording_started_at`` to ``now - paused_elapsed``.
        self._paused_elapsed: float | None = None

        # Wire up OBS events
        self._obs.on_record_state_changed(self._handle_record_event)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def armed_topic(self) -> ArmedDeck | None:
        """Deprecated alias for :attr:`armed_deck`."""
        return self._armed

    @property
    def armed_deck(self) -> ArmedDeck | None:
        return self._armed

    @property
    def pending_renames(self) -> PendingRenameQueue:
        """The queue of renames deferred by file-lock contention.

        Exposed so callers (typically the JobManager subscriber wired
        in the web app) can :meth:`PendingRenameQueue.drain` it once
        the upload that held the lock completes.
        """
        return self._pending_renames

    def snapshot(self) -> SessionSnapshot:
        """Thread-safe snapshot of the current session state."""
        with self._lock:
            if self._state is SessionState.RECORDING:
                elapsed: float | None = self._elapsed_since_start_locked()
            elif self._state is SessionState.PAUSED:
                elapsed = self._paused_elapsed
            else:
                elapsed = None
            return SessionSnapshot(
                state=self._state,
                armed_deck=self._armed,
                obs_connected=self._obs.connected,
                obs_state=getattr(self._obs, "connection_state", "disconnected"),
                last_output=self._last_output,
                error=self._error,
                recording_elapsed_seconds=elapsed,
                paused=self._state is SessionState.PAUSED,
            )

    def arm(
        self,
        course_slug: str,
        section_name: str,
        deck_name: str,
        *,
        part_number: int = 0,
        lang: str = "en",
        lecture_id: str | None = None,
    ) -> None:
        """Arm a slide deck for the next recording.

        Can be called from ``IDLE``, ``ARMED``, or ``ARMED_AFTER_TAKE``
        (to switch decks mid-retake-window). Calling from
        ``ARMED_AFTER_TAKE`` cancels the retake timer — the window is
        specific to the deck that just finished, and switching decks
        means the user is moving on.

        Raises:
            RuntimeError: If a recording or rename is in progress.
        """
        with self._lock:
            if self._state not in (
                SessionState.IDLE,
                SessionState.ARMED,
                SessionState.ARMED_AFTER_TAKE,
            ):
                raise RuntimeError(
                    f"Cannot arm while in state '{self._state.value}'. "
                    "Wait for the current recording to finish."
                )
            self._cancel_retake_timer_locked()
            self._armed = ArmedDeck(
                course_slug=course_slug,
                section_name=section_name,
                deck_name=deck_name,
                part_number=part_number,
                lang=lang,
                lecture_id=lecture_id,
            )
            self._error = None
            self._state = SessionState.ARMED

        logger.info(
            "User action: arm course={!r} section={!r} deck={!r} part={} lang={!r}",
            course_slug,
            section_name,
            deck_name,
            part_number,
            lang,
        )
        self._notify()

    def disarm(self) -> None:
        """Disarm the currently armed topic, returning to ``IDLE``.

        Can be called from ``ARMED`` or ``ARMED_AFTER_TAKE`` (the latter
        cancels the retake window early).

        Raises:
            RuntimeError: If a recording is in progress.
        """
        with self._lock:
            if self._state == SessionState.RECORDING:
                raise RuntimeError("Cannot disarm while recording is in progress.")
            if self._state == SessionState.PAUSED:
                raise RuntimeError(
                    "Cannot disarm while recording is paused. Resume and stop first."
                )
            if self._state == SessionState.RENAMING:
                raise RuntimeError("Cannot disarm while rename is in progress.")
            self._cancel_retake_timer_locked()
            self._armed = None
            self._state = SessionState.IDLE

        logger.info("User action: disarm")
        self._notify()

    def record(
        self,
        course_slug: str,
        section_name: str,
        deck_name: str,
        *,
        part_number: int = 0,
        lang: str = "en",
        lecture_id: str | None = None,
    ) -> None:
        """Arm a deck and start OBS recording in one operation.

        The two steps are deliberately *not* atomic: ``arm`` succeeds
        under the session lock, then ``obs.start_record`` is invoked
        outside the lock (OBS I/O must not block other callers).

        If OBS rejects the start request, the deck is left **armed** on
        purpose: the user can switch to OBS and start recording manually,
        or retry the Record button once OBS is reachable. The caller is
        responsible for surfacing the error in the UI.

        Raises:
            RuntimeError: If ``arm`` fails (already recording or mid-rename).
            ConnectionError: If OBS rejects the start request. The deck
                remains armed — callers should present a recoverable error.
        """
        self.arm(
            course_slug,
            section_name,
            deck_name,
            part_number=part_number,
            lang=lang,
            lecture_id=lecture_id,
        )
        logger.info(
            "User action: record (arm + obs.start_record) deck={!r} part={}",
            deck_name,
            part_number,
        )
        self._obs.start_record()

    def advance_take(
        self,
        course_slug: str,
        section_name: str,
        deck_name: str,
        *,
        part_number: int = 0,
        lang: str = "en",
        lecture_id: str | None = None,
    ) -> list[tuple[Path, Path]]:
        """Demote the active take for ``(deck, part)`` into ``takes/`` without recording.

        Runs the same ``_preserve_active_take`` cascade that a retake
        would trigger, but without starting a new OBS recording. Useful
        when the user realises mid-session that the current recording
        is wrong and wants to slot it into the take history before
        moving on — without first recording a throwaway just to demote
        the previous take.

        If any files were preserved, also propagates the rename to
        ``state.json`` so the dashboard reflects the move. If the
        session was in :attr:`ARMED_AFTER_TAKE` for this deck, the
        retake-window timer is cancelled (the active-take slot is now
        empty, so the retake-window semantics no longer apply) and the
        state transitions back to :attr:`ARMED`.

        Returns the list of ``(old_path, new_path)`` pairs actually
        moved. Empty if no active-take files existed.

        Raises:
            RuntimeError: If a recording or rename is currently in
                progress — the caller should stop first.
        """
        logger.info(
            "User action: advance_take deck={!r} part={}",
            deck_name,
            part_number,
        )
        with self._lock:
            if self._state in (
                SessionState.RECORDING,
                SessionState.PAUSED,
                SessionState.RENAMING,
            ):
                raise RuntimeError(f"Cannot advance take while in state '{self._state.value}'.")
            was_armed_after_take = (
                self._state is SessionState.ARMED_AFTER_TAKE
                and self._armed is not None
                and self._armed.course_slug == course_slug
                and self._armed.section_name == section_name
                and self._armed.deck_name == deck_name
                and self._armed.part_number == part_number
            )
            if was_armed_after_take:
                self._cancel_retake_timer_locked()
                self._state = SessionState.ARMED

        rel_dir = recording_relative_dir(course_slug, section_name)
        deck = ArmedDeck(
            course_slug=course_slug,
            section_name=section_name,
            deck_name=deck_name,
            part_number=part_number,
            lang=lang,
            lecture_id=lecture_id,
        )
        active_take = self._lookup_active_take(deck)
        preserved = _preserve_active_take(
            recordings_root=self._root,
            rel_dir=str(rel_dir),
            deck_name=deck_name,
            part=part_number,
            raw_suffix=self._raw_suffix,
            lang=lang,
            take_number=active_take,
            pending=self._pending_renames,
        )

        if preserved:
            course_state = self._resolve_course_state(deck)
            self._apply_renames_to_state(preserved, course_state)
            self._notify_path_renames(preserved)

        if was_armed_after_take or preserved:
            self._notify()

        return preserved

    def restore_take(
        self,
        course_slug: str,
        section_name: str,
        deck_name: str,
        target_take: int,
        *,
        part_number: int = 0,
        lang: str = "en",
        lecture_id: str,
    ) -> list[tuple[Path, Path]]:
        """Promote historical take *target_take* back to active and demote the current active.

        Runs :func:`_swap_active_with_take` to perform the filesystem
        swap with planned-rename rollback, then mutates the resolved
        :class:`CourseRecordingState` to match (path migrations applied
        before :meth:`CourseRecordingState.restore_take` swaps the data
        fields, so no temporary path duplicates exist).

        The session must be quiescent (``IDLE``, ``ARMED``, or
        ``ARMED_AFTER_TAKE``); a recording or rename in flight blocks
        the swap to avoid clobbering the file OBS is still writing.

        Returns the list of ``(old_path, new_path)`` pairs moved on
        disk, in execution order. Empty only when nothing existed to
        swap, which is itself an error (we already required at least
        one target file to exist).

        Raises:
            RuntimeError: If the session is recording, paused, or renaming.
            ValueError: If the resolved state has no record of the part
                or the requested take.
            FileNotFoundError: If no files for *target_take* exist in
                ``takes/``.
        """
        logger.info(
            "User action: restore_take deck={!r} part={} target_take={}",
            deck_name,
            part_number,
            target_take,
        )
        with self._lock:
            if self._state in (
                SessionState.RECORDING,
                SessionState.PAUSED,
                SessionState.RENAMING,
            ):
                raise RuntimeError(f"Cannot restore take while in state '{self._state.value}'.")

        deck = ArmedDeck(
            course_slug=course_slug,
            section_name=section_name,
            deck_name=deck_name,
            part_number=part_number,
            lang=lang,
            lecture_id=lecture_id,
        )
        course_state = self._resolve_course_state(deck)
        if course_state is None:
            raise ValueError(f"No course state resolved for {course_slug}")

        state_part = part_number if part_number > 0 else 1
        lecture = course_state.get_lecture(lecture_id)
        if lecture is None:
            raise ValueError(f"Lecture not found: {lecture_id}")
        part_obj = next((p for p in lecture.parts if p.part == state_part), None)
        if part_obj is None:
            raise ValueError(f"Part {state_part} not found in lecture {lecture_id}")
        active_take = part_obj.active_take
        target = next((t for t in part_obj.takes if t.take == target_take), None)
        if target is None:
            raise ValueError(f"Take {target_take} not found in part {state_part} of {lecture_id}")

        rel_dir = recording_relative_dir(course_slug, section_name)
        renames = _swap_active_with_take(
            recordings_root=self._root,
            rel_dir=str(rel_dir),
            deck_name=deck_name,
            part=part_number,
            active_take=active_take,
            target_take=target_take,
            raw_suffix=self._raw_suffix,
            lang=lang,
        )

        # Update state paths to match the post-swap filesystem reality
        # *before* swapping the data fields, so no two records ever hold
        # the same path simultaneously (which would confuse the
        # string-equality match in :meth:`rename_recording_paths`).
        try:
            for old, new in renames:
                course_state.rename_recording_paths(str(old), str(new))
                course_state.rename_recording_paths(
                    str(old),
                    str(old),
                    old_processed=str(old),
                    new_processed=str(new),
                )
            course_state.restore_take(lecture_id, state_part, target_take)
        except Exception as exc:
            logger.error(
                "State update failed after FS swap for {} part {} take {}: {}",
                lecture_id,
                state_part,
                target_take,
                exc,
            )
            raise

        self._persist_state(course_state)
        self._notify_path_renames(renames)
        return renames

    def stop(self) -> None:
        """Ask OBS to stop the current recording.

        Pure convenience so the dashboard can offer a Stop button without
        leaving the web UI. The rest of the stop flow (STOPPED event →
        rename → transition to IDLE) is handled by the existing event
        pipeline. Works both from :attr:`SessionState.RECORDING` and
        :attr:`SessionState.PAUSED` — OBS accepts ``StopRecord`` while
        paused and will emit the normal STOPPED event.

        Raises:
            ConnectionError: If OBS is not connected or rejects the request.
        """
        logger.info("User action: stop (current state={})", self._state.value)
        self._obs.stop_record()

    def pause(self) -> None:
        """Ask OBS to pause the current recording.

        The PAUSED event arrives asynchronously via the OBS event
        client and drives the actual state transition in
        :meth:`_handle_record_event`. This method is a thin wrapper so
        the dashboard can offer a Pause button without leaving the web
        UI.

        Raises:
            RuntimeError: If the session is not in :attr:`SessionState.RECORDING`.
            ConnectionError: If OBS is not connected or rejects the request.
        """
        with self._lock:
            if self._state is not SessionState.RECORDING:
                raise RuntimeError(
                    f"Cannot pause while in state '{self._state.value}'. "
                    "Recording must be in progress."
                )
        logger.info("User action: pause")
        self._obs.pause_record()

    def resume(self) -> None:
        """Ask OBS to resume a paused recording.

        The RESUMED event arrives asynchronously and drives the
        transition back to :attr:`SessionState.RECORDING` in
        :meth:`_handle_record_event`.

        Raises:
            RuntimeError: If the session is not in :attr:`SessionState.PAUSED`.
            ConnectionError: If OBS is not connected or rejects the request.
        """
        with self._lock:
            if self._state is not SessionState.PAUSED:
                raise RuntimeError(
                    f"Cannot resume while in state '{self._state.value}'. Recording must be paused."
                )
        logger.info("User action: resume")
        self._obs.resume_record()

    # ------------------------------------------------------------------
    # OBS event handling
    # ------------------------------------------------------------------

    def _handle_record_event(self, event: RecordingEvent) -> None:
        """Respond to an OBS ``RecordStateChanged`` event.

        Called on the obsws-python daemon thread.

        OBS emits *two* events when recording stops:

        1. ``OBS_WEBSOCKET_OUTPUT_STOPPING`` — ``output_active=False``,
           **no** ``output_path`` yet (OBS is still flushing the file).
        2. ``OBS_WEBSOCKET_OUTPUT_STOPPED`` — ``output_active=False``,
           ``output_path`` is set to the final file location.

        We must ignore the intermediate STOPPING event and only act on
        the definitive STOPPED event, otherwise the session transitions
        to ``IDLE`` before the output path is available.

        Pause handling: OBS emits ``OBS_WEBSOCKET_OUTPUT_PAUSED`` /
        ``OBS_WEBSOCKET_OUTPUT_RESUMED`` with ``output_active=False`` /
        ``True`` respectively. We key on the explicit ``output_state`` so
        the session can expose a visible paused state without mistaking
        it for a stop — previously the paused event fell into the
        stopped branch, had no ``output_path``, and silently disarmed
        the deck, leaving the user to manually move the recording once
        OBS finally stopped.

        OBS START during ``ARMED_AFTER_TAKE`` is treated as a retake of
        the same armed deck. OBS STOP within ``short_take_seconds`` of
        the start is treated as an accidental take: the file is moved
        to ``superseded/`` and the deck stays armed.
        """
        rename_args: tuple[Path, ArmedDeck] | None = None
        short_take_args: tuple[Path, ArmedDeck] | None = None

        with self._lock:
            # Pause / resume are dispatched explicitly before the
            # active/inactive split so the PAUSED event (which carries
            # output_active=False and no output_path) is not confused
            # with a stop.
            if event.output_state == "OBS_WEBSOCKET_OUTPUT_PAUSED":
                if self._state is SessionState.RECORDING:
                    elapsed = self._elapsed_since_start_locked() or 0.0
                    self._paused_elapsed = elapsed
                    self._recording_started_at = None
                    self._state = SessionState.PAUSED
                    logger.info("Recording paused for {} after {:.1f}s", self._armed, elapsed)
                else:
                    logger.debug("OBS paused event ignored (state={})", self._state.value)
                    return
            elif event.output_state == "OBS_WEBSOCKET_OUTPUT_RESUMED":
                if self._state is SessionState.PAUSED:
                    resumed_from = self._paused_elapsed or 0.0
                    # Re-base the start timestamp so elapsed picks up
                    # where pause froze it, ignoring the paused window.
                    self._recording_started_at = time.monotonic() - resumed_from
                    self._paused_elapsed = None
                    self._state = SessionState.RECORDING
                    logger.info("Recording resumed for {}", self._armed)
                else:
                    logger.debug("OBS resumed event ignored (state={})", self._state.value)
                    return
            elif event.output_active:
                # Recording started (STARTED)
                if self._state in (SessionState.ARMED, SessionState.ARMED_AFTER_TAKE):
                    if self._state == SessionState.ARMED_AFTER_TAKE:
                        logger.info("Retake detected for {} (within window)", self._armed)
                    self._cancel_retake_timer_locked()
                    self._state = SessionState.RECORDING
                    self._recording_started_at = time.monotonic()
                    self._paused_elapsed = None
                    logger.info("Recording started for {}", self._armed)
                else:
                    logger.info("Recording started (state={}, no auto-rename)", self._state.value)
            else:
                # Intermediate transition — OBS hasn't finished writing the
                # file yet.  The output_path is only present in the final
                # OBS_WEBSOCKET_OUTPUT_STOPPED event.
                if event.output_state == "OBS_WEBSOCKET_OUTPUT_STOPPING":
                    logger.debug("Intermediate STOPPING event, waiting for STOPPED")
                    return

                # Recording stopped (definitive STOPPED event). A stop
                # arriving while PAUSED is the user ending the take from
                # OBS without resuming first — handle it on the same
                # rename path as a stop from RECORDING.
                active_states = (SessionState.RECORDING, SessionState.PAUSED)
                if self._state in active_states and self._armed is not None:
                    stop_elapsed: float | None
                    if self._state is SessionState.PAUSED:
                        stop_elapsed = self._paused_elapsed
                    else:
                        stop_elapsed = self._elapsed_since_start_locked()
                    is_short_take = (
                        stop_elapsed is not None and stop_elapsed < self._short_take_seconds
                    )
                    self._recording_started_at = None
                    self._paused_elapsed = None

                    if is_short_take and event.output_path:
                        # Accidental start-then-stop — move to superseded/
                        # and stay armed on the same deck.
                        logger.info(
                            "Short take ({:.1f}s < {:.1f}s) — superseding and staying armed",
                            stop_elapsed,
                            self._short_take_seconds,
                        )
                        self._state = SessionState.ARMED
                        short_take_args = (Path(event.output_path), self._armed)
                    elif event.output_path:
                        self._state = SessionState.RENAMING
                        rename_args = (Path(event.output_path), self._armed)
                    else:
                        logger.warning("Recording stopped but no output path reported")
                        self._error = "OBS did not report the output file path"
                        self._armed = None
                        self._state = SessionState.IDLE
                elif self._state in active_states:
                    # Was recording/paused but nothing armed — just go back to idle
                    self._state = SessionState.IDLE
                    self._recording_started_at = None
                    self._paused_elapsed = None
                else:
                    logger.debug("Recording stopped event ignored (state={})", self._state.value)

        self._notify()

        if short_take_args:
            threading.Thread(
                target=self._handle_short_take,
                args=short_take_args,
                daemon=True,
                name="recording-short-take",
            ).start()
        elif rename_args:
            threading.Thread(
                target=self._rename_recording,
                args=rename_args,
                daemon=True,
                name="recording-rename",
            ).start()

    # ------------------------------------------------------------------
    # File rename (runs on a background thread)
    # ------------------------------------------------------------------

    def _rename_recording(self, obs_output: Path, deck: ArmedDeck) -> None:
        """Move the OBS output file into the structured ``to-process/`` tree.

        Before landing the new raw, any existing active-take files for the
        same ``(deck, part)`` are demoted into ``takes/`` with a
        ``(part N, take K)`` suffix. This preserves previously-processed
        finals and their matching raws without overwriting.

        Lock contention against an in-flight Auphonic upload no longer
        aborts the take: any preserve/cascade move that hits a Windows
        file lock is parked on :attr:`pending_renames` and re-attempted
        when the lock holder finishes. The OBS output itself is moved
        with :func:`safe_move` so a stale upload of the previous take
        cannot stop the new recording from landing.

        On success, transitions the session to :attr:`ARMED_AFTER_TAKE`
        and starts a timer that will disarm the deck if no retake
        arrives within ``retake_window_seconds``.
        """
        logger.info(
            "Rename pipeline: obs_output={} deck={} part={}",
            obs_output,
            deck.deck_name,
            deck.part_number,
        )
        try:
            self._wait_for_stable(obs_output)

            rel_dir = recording_relative_dir(deck.course_slug, deck.section_name)
            target_dir = to_process_dir(self._root) / str(rel_dir)
            target_dir.mkdir(parents=True, exist_ok=True)

            course_state = self._resolve_course_state(deck)

            # Retake pre-move: demote the active take's files into takes/
            # before the new recording claims their slots. Pull the
            # take number from state so the demoted filename matches
            # the take's stable identity (FS max+1 diverges from state
            # after a restore). Lock contention here is parked on
            # ``pending_renames`` and drained later — the new recording
            # must always land, even if a previous take is still
            # uploading.
            preserved = _preserve_active_take(
                recordings_root=self._root,
                rel_dir=str(rel_dir),
                deck_name=deck.deck_name,
                part=deck.part_number,
                raw_suffix=self._raw_suffix,
                lang=deck.lang,
                take_number=self._lookup_active_take(deck, course_state=course_state),
                pending=self._pending_renames,
            )
            self._apply_renames_to_state(preserved, course_state)

            target, cascade_renames = _prepare_target_slot(
                target_dir,
                deck.deck_name,
                obs_output.suffix,
                deck.part_number,
                self._raw_suffix,
                self._root,
                lang=deck.lang,
                pending=self._pending_renames,
            )
            self._apply_renames_to_state(cascade_renames, course_state)
            self._notify_path_renames(cascade_renames)

            target = self._land_obs_output(obs_output, target, deck)

            # Register or refresh the part in state.json so the dashboard
            # and CLI see the new recording. ``preserved`` being non-empty
            # means we just demoted a prior take — record that explicitly
            # via ``record_retake`` so the ``takes[]`` history reflects it.
            self._sync_state_after_rename(deck, target, preserved_count=len(preserved))

            with self._lock:
                self._last_output = target
                # Keep _armed so a retake within the window can re-use it.
                self._state = SessionState.ARMED_AFTER_TAKE
                self._start_retake_timer_locked()

        except Exception as exc:
            logger.error("Failed to rename recording: {}", exc)
            with self._lock:
                self._error = str(exc)
                self._armed = None
                self._state = SessionState.IDLE

        self._notify()

    def _land_obs_output(self, obs_output: Path, target: Path, deck: ArmedDeck) -> Path:
        """Move the OBS-produced file into *target*, falling back on lock contention.

        The ``target`` slot may still be occupied by a previous take's
        raw whose supersede was deferred (lock held by an Auphonic
        upload). Rather than fail — which would lose the new
        recording — land the file at a take-suffixed sibling slot so
        the data is safe and the user can sort it out from the take
        history. Returns the actual path the file ended up at.
        """
        try:
            safe_move(obs_output, target)
            logger.info("Renamed {} → {}", obs_output.name, target)
            return target
        except FileLockedError as exc:
            logger.warning(
                "Target slot {} stayed locked for OBS output {}; using fallback name",
                target,
                obs_output.name,
                exc_info=False,
            )
            fallback = self._fallback_target_for_locked_slot(target, deck)
            safe_move(obs_output, fallback)
            logger.warning(
                "OBS output landed at fallback {} (original target {} still locked by {})",
                fallback,
                target,
                exc.last_error,
            )
            self._pending_renames.try_or_defer(
                fallback, target, reason="obs-output-landing-fallback"
            )
            return fallback

    def _fallback_target_for_locked_slot(self, target: Path, deck: ArmedDeck) -> Path:
        """Pick a non-colliding sibling path when ``target`` itself is locked.

        Names the file like a take-history entry so the user can find
        it in the take panel. ``take=99`` is a sentinel meaning "landed
        before we knew the real take number" — the rename queue will
        rename it to the proper slot once the lock clears.
        """
        rel_dir = recording_relative_dir(deck.course_slug, deck.section_name)
        takes_subtree = takes_dir(self._root) / str(rel_dir)
        takes_subtree.mkdir(parents=True, exist_ok=True)
        take = _next_take_number(takes_subtree, deck.deck_name, deck.part_number)
        # Bump until we find a free slot (defensive — _next_take_number
        # is already strictly greater than any existing take).
        while True:
            name = take_filename(
                deck.deck_name,
                ext=target.suffix,
                raw_suffix=self._raw_suffix,
                part=deck.part_number,
                take=take,
                is_raw=True,
                lang=deck.lang,
            )
            candidate = takes_subtree / name
            if not candidate.exists():
                return candidate
            take += 1

    def _lookup_active_take(
        self,
        deck: ArmedDeck,
        *,
        course_state: CourseRecordingState | None = None,
    ) -> int | None:
        """Return ``part.active_take`` from state for *deck* if available.

        Used by the demote-on-retake path so the ``(take K)`` suffix
        matches the take's stable identity in ``state.json`` rather
        than the filesystem heuristic ``max(takes/) + 1`` — they
        diverge after a restore. Returns ``None`` when no state is
        wired (CLI/tests that don't track state) or the part is not
        yet registered, in which case the FS heuristic is the right
        fallback.
        """
        state = course_state if course_state is not None else self._resolve_course_state(deck)
        if state is None or deck.lecture_id is None:
            return None
        lecture = state.get_lecture(deck.lecture_id)
        if lecture is None:
            return None
        state_part = deck.part_number if deck.part_number > 0 else 1
        for part in lecture.parts:
            if part.part == state_part:
                return part.active_take
        return None

    def _resolve_course_state(self, deck: ArmedDeck) -> CourseRecordingState | None:
        """Look up the :class:`CourseRecordingState` for *deck*.

        Prefers :attr:`_state_provider` (web dashboard: one session,
        many courses); falls back to the singleton :attr:`_course_state`
        provided at construction for tests and CLI usage.
        """
        if self._state_provider is not None:
            try:
                resolved = self._state_provider(deck)
            except Exception as exc:
                logger.warning("State provider raised for {}: {}", deck.course_slug, exc)
                resolved = None
            if resolved is not None:
                return resolved
        return self._course_state

    def _sync_state_after_rename(
        self,
        deck: ArmedDeck,
        new_raw: Path,
        *,
        preserved_count: int,
    ) -> None:
        """Update ``state.json`` after a successful rename.

        Requires a ``lecture_id`` on the deck and a resolvable course
        state — falls back silently otherwise so CLI/test flows that
        don't wire state stay untouched.

        ``deck.part_number == 0`` is the UI's "unsuffixed single-part"
        mode; at the state level it is stored as part 1. A later
        recording of part 2 triggers the filesystem cascade that
        renames the unsuffixed file to ``(part 1)``, which keeps the
        on-disk name consistent with the state entry.
        """
        if deck.lecture_id is None:
            return
        course_state = self._resolve_course_state(deck)
        if course_state is None:
            return

        state_part = deck.part_number if deck.part_number > 0 else 1

        try:
            lecture = course_state.get_lecture(deck.lecture_id)
            existing_part = (
                next((p for p in lecture.parts if p.part == state_part), None)
                if lecture is not None
                else None
            )

            if existing_part is not None and preserved_count > 0:
                course_state.record_retake(
                    deck.lecture_id,
                    state_part,
                    str(new_raw),
                )
            else:
                course_state.ensure_part(
                    deck.lecture_id,
                    state_part,
                    str(new_raw),
                    display_name=deck.deck_name,
                )
        except Exception as exc:
            logger.warning(
                "Failed to sync state for {} part {}: {}",
                deck.lecture_id,
                state_part,
                exc,
            )
            return

        self._persist_state(course_state)

    def _apply_renames_to_state(
        self,
        renames: list[tuple[Path, Path]],
        course_state: CourseRecordingState | None,
    ) -> None:
        """Forward disk renames to the course state index when wired up.

        No-op if no state was resolved for the deck or if *renames* is
        empty. Swallows exceptions so state tracking never blocks a
        successful rename on disk — a stale state entry is recoverable;
        losing the recording is not.
        """
        if course_state is None or not renames:
            return
        mutated = False
        try:
            for old, new in renames:
                course_state.rename_recording_paths(str(old), str(new))
                # The moved file may also be referenced as a processed_file
                # when it lived under ``final/``; rename that column too.
                course_state.rename_recording_paths(
                    str(old),
                    str(old),
                    old_processed=str(old),
                    new_processed=str(new),
                )
                mutated = True
        except Exception as exc:
            logger.warning("Failed to propagate rename to course state: {}", exc)
            return

        if mutated:
            self._persist_state(course_state)

    def _persist_state(self, course_state: CourseRecordingState) -> None:
        """Invoke :attr:`_on_state_mutation` if wired up."""
        if self._on_state_mutation is None:
            return
        try:
            self._on_state_mutation(course_state)
        except Exception as exc:
            logger.warning("on_state_mutation raised for {}: {}", course_state.course_id, exc)

    def _notify_path_renames(self, renames: list[tuple[Path, Path]]) -> None:
        """Forward cascade renames to :attr:`_on_path_rename` if wired.

        Called after the filesystem cascade has moved existing raw files
        to their suffixed slots (e.g. ``deck--RAW.mkv`` →
        ``deck (part 1)--RAW.mkv``). The web dashboard uses the callback
        to rewrite any in-flight job whose ``raw_path`` matches the old
        location, so the Auphonic output lands at the renamed stem.

        Swallows exceptions per-rename so one wayward subscriber cannot
        derail the rename thread.
        """
        if self._on_path_rename is None or not renames:
            return
        for old, new in renames:
            try:
                self._on_path_rename(old, new)
            except Exception as exc:
                logger.warning("on_path_rename raised for {} → {}: {}", old, new, exc)

    def _handle_short_take(self, obs_output: Path, deck: ArmedDeck) -> None:
        """Move an accidental short take to ``superseded/`` and log it.

        The session has already transitioned back to :attr:`ARMED` under
        the event-handler lock; this thread exists only to do the
        filesystem I/O without blocking the OBS callback thread.
        """
        try:
            self._wait_for_stable(obs_output)
        except Exception as exc:
            logger.warning(
                "Short-take file {} did not stabilise: {}. Leaving in place.",
                obs_output,
                exc,
            )
            return

        rel_dir = recording_relative_dir(deck.course_slug, deck.section_name)
        dest_dir = superseded_dir(self._root) / str(rel_dir)
        try:
            dest = _move_to_superseded_dir(obs_output, dest_dir)
            logger.info("Short take moved to {}", dest)
        except Exception as exc:
            logger.warning("Failed to move short-take {} to superseded/: {}", obs_output, exc)

    def _wait_for_stable(self, path: Path) -> None:
        """Poll file size until it stops changing.

        Bounded by :attr:`_rename_timeout_seconds` so a wedged encoder
        cannot freeze the session forever.

        Raises:
            FileNotFoundError: If the file does not exist.
            TimeoutError: If the file has not stabilised within the timeout.
        """
        prev_size = -1
        stable_count = 0
        deadline = time.monotonic() + self._rename_timeout_seconds

        while stable_count < self._stability_checks:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"File {path} did not stabilise within {self._rename_timeout_seconds:.0f}s"
                )
            if not path.exists():
                raise FileNotFoundError(f"Recording file not found: {path}")

            size = path.stat().st_size
            if size == prev_size and size > 0:
                stable_count += 1
            else:
                stable_count = 0
            prev_size = size

            if stable_count < self._stability_checks:
                time.sleep(self._stability_interval)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _notify(self) -> None:
        """Emit state-change callback outside the lock."""
        if self._on_state_change:
            try:
                self._on_state_change(self.snapshot())
            except Exception:
                logger.exception("Error in state change callback")

    def _elapsed_since_start_locked(self) -> float | None:
        """Seconds since the most recent OBS STARTED event, or None if unknown.

        The caller must hold :attr:`_lock`.
        """
        if self._recording_started_at is None:
            return None
        return time.monotonic() - self._recording_started_at

    def _cancel_retake_timer_locked(self) -> None:
        """Cancel the retake-window timer if one is running.

        The caller must hold :attr:`_lock`. Safe to call when no timer
        is active.
        """
        timer = self._retake_timer
        self._retake_timer = None
        if timer is not None:
            timer.cancel()

    def _start_retake_timer_locked(self) -> None:
        """Arm a timer that will fire :meth:`_on_retake_window_expired`.

        The caller must hold :attr:`_lock`. Cancels any prior timer
        first so there is only ever one pending.
        """
        self._cancel_retake_timer_locked()
        timer = threading.Timer(self._retake_window_seconds, self._on_retake_window_expired)
        timer.daemon = True
        timer.name = "recording-retake-window"
        self._retake_timer = timer
        timer.start()

    def _on_retake_window_expired(self) -> None:
        """Fire when the retake window elapses without a new OBS recording.

        Transitions ``ARMED_AFTER_TAKE`` → ``IDLE``. Silently does
        nothing if the session has already moved on (e.g. the user
        disarmed, armed a different deck, or a retake arrived just
        before the timer fired).
        """
        with self._lock:
            if self._state != SessionState.ARMED_AFTER_TAKE:
                return
            logger.info("Retake window expired; disarming {}", self._armed)
            self._armed = None
            self._state = SessionState.IDLE
            self._retake_timer = None

        self._notify()
