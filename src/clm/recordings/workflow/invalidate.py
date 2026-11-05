"""Take back a recorded take: the shared core of ``clm recordings invalidate`` (#1093, #908).

A take reaches the ledger by default when it stops; this is the explicit
exception for one that should not count. One operation over three stores, used
by the CLI verb and the dashboard action:

* the committed ledger (:func:`clm.recordings.ledger.invalidate_part`): the
  previous take comes back from ``superseded``, else the part is gone;
* the machine-local state file (:meth:`CourseRecordingState.invalidate_take`):
  the take leaves the history for the lecture's ``invalidated`` audit list;
* the video files: moved, **never deleted**. The invalidated take's files go to
  ``superseded/`` (not ``takes/``, which feeds the Restore button), the restored
  take's come back from ``takes/``, and a deck left with only part 1 returns to
  the unsuffixed single-part names.

Part semantics, the one rule being that **parts are never renumbered**:

* a take with a previous take is simply replaced by it, whatever its part;
* the only take of the highest part goes away, and the numbers below it stand;
  if that leaves only part 1, the deck is single-part again (#908);
* the only take of any lower part is **refused**: removing it leaves a hole
  whose meaning is ambiguous (the dashboard offers no slot to fill it, and
  whether later parts should move up is not ours to guess).

Everything refusable is checked before the first change. A file move that fails
is rolled back, and the ledger is only written after the moves succeeded.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from attrs import define, field
from loguru import logger

from clm.core.utils.text_utils import sanitize_file_name
from clm.recordings import ledger as rl
from clm.recordings.ledger import InvalidationRefused

from .directories import archive_dir, final_dir, superseded_dir, takes_dir, to_process_dir
from .naming import (
    DEFAULT_RAW_SUFFIX,
    final_filename,
    parse_part,
    parse_part_take,
    parse_raw_stem,
    raw_filename,
    recording_relative_dir,
    take_filename,
)
from .safe_move import safe_move
from .session import _scan_active_take_files, _scan_takes_for

if TYPE_CHECKING:
    from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart

    from .jobs import ProcessingJob

__all__ = ["InvalidateRequest", "InvalidateResult", "InvalidationRefused", "invalidate_take"]

Effect = Literal["restored", "removed", "history"]
Move = tuple[Path, Path]


@define
class InvalidateRequest:
    """Which take to invalidate, and the stores the caller has at hand.

    ``part`` is the state/ledger part number (a single-part recording is part
    1). The ledger is optional (``ledger_path``/``deck_key``/``deck_path``: a
    dashboard deck that does not resolve through the course has none); so are
    the state (a machine without the state file) and the files (no
    ``recordings_root``, or no section and deck name to find them by).
    """

    course_id: str
    part: int
    lang: str
    recorded_at: str | None = None
    note: str | None = None
    ledger_path: Path | None = None
    deck_key: str | None = None
    deck_path: Path | None = None
    state: CourseRecordingState | None = None
    lecture_id: str | None = None
    section_name: str | None = None
    deck_name: str | None = None
    recordings_root: Path | None = None
    raw_suffix: str = DEFAULT_RAW_SUFFIX
    active_jobs: Sequence[ProcessingJob] = ()
    pending_paths: Sequence[Path] = ()
    persist_state: Callable[[CourseRecordingState], Any] | None = None
    dry_run: bool = False


@define
class InvalidateResult:
    """What happened (or, with ``dry_run``, what would)."""

    course_id: str
    part: int
    lang: str
    recorded_at: str
    effect: Effect
    deck: str | None = None
    ledger_path: Path | None = None
    restored_recorded_at: str | None = None
    single_part: bool = False
    ack_dropped: bool = False
    state_updated: bool = False
    moves: list[Move] = field(factory=list)
    warnings: list[str] = field(factory=list)
    note: str | None = None
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        """The ``--json`` envelope (identity head first, per ``clm info agent-tasks``)."""
        return {
            "schema": 1,
            "tool": "recordings",
            "verb": "invalidate",
            "deck": self.deck,
            "ledger": str(self.ledger_path) if self.ledger_path else None,
            "course_id": self.course_id,
            "part": self.part,
            "lang": self.lang,
            "recorded_at": self.recorded_at,
            "effect": self.effect,
            "restored_recorded_at": self.restored_recorded_at,
            "single_part": self.single_part,
            "ack_dropped": self.ack_dropped,
            "state_updated": self.state_updated,
            "files": [{"from": str(a), "to": str(b)} for a, b in self.moves],
            "warnings": list(self.warnings),
            "note": self.note,
            "dry_run": self.dry_run,
        }


def invalidate_take(req: InvalidateRequest) -> InvalidateResult:
    """Invalidate one take; raises :class:`InvalidationRefused` with nothing changed."""
    warnings: list[str] = []

    # --- The ledger's view.
    ledger: rl.RecordingsLedger | None = None
    entry: rl.DeckEntry | None = None
    if req.ledger_path is not None and req.deck_key is not None:
        try:
            ledger = rl.load(req.ledger_path)
        except rl.LedgerError as exc:
            raise InvalidationRefused(str(exc)) from None
        entry = ledger.decks.get(req.deck_key)
    key = (req.course_id, req.part, req.lang)
    active_entry = (
        next((p for p in entry.parts if rl.part_identity(p) == key), None) if entry else None
    )

    # --- The take, and where the state file has it.
    lecture, state_part = _known_lecture_part(req)
    stamp = req.recorded_at
    if stamp is None:
        if state_part is not None and req.lecture_id is not None:
            stamp = state_part.recorded_at  # the dashboard acts on the state's active take
        elif active_entry is not None:
            stamp = active_entry.recorded_at
    if stamp is None:
        raise InvalidationRefused(
            f"no recorded take of part {req.part} ({req.lang}, course {req.course_id})"
        )
    if lecture is None and req.state is not None and req.lecture_id is None:
        lecture, state_part = _find_in_state(req.state, req.part, stamp)
    state_take: int | None = None  # the take number when it is not the active one
    state_is_active = False
    if state_part is not None:
        if state_part.recorded_at == stamp:
            state_is_active = True
        else:
            state_take = next((t.take for t in state_part.takes if t.recorded_at == stamp), None)
            if state_take is None and req.recorded_at is not None and active_entry is None:
                raise InvalidationRefused(
                    f"part {req.part} has no take recorded at {stamp} in the state file"
                )

    # --- Which ledger entry is the target.
    in_history = bool(entry) and any(
        rl.part_identity(p) == key and p.recorded_at == stamp
        for p in (entry.superseded if entry else [])
    )
    ledger_is_active = active_entry is not None and active_entry.recorded_at == stamp
    ledger_has_target = ledger_is_active or in_history
    if not ledger_has_target and not state_is_active and state_take is None:
        known = sorted(
            {p.recorded_at for p in ([active_entry] if active_entry else [])}
            | {
                p.recorded_at
                for p in (entry.superseded if entry else [])
                if rl.part_identity(p) == key
            }
        )
        raise InvalidationRefused(
            f"no take of part {req.part} ({req.lang}, course {req.course_id}) recorded at {stamp}"
            + (f"; known takes: {', '.join(known)}" if known else "")
        )
    if not ledger_has_target:
        if ledger is None:
            warnings.append("the committed ledger was not updated: this deck has no known ledger")
        elif active_entry is not None:
            warnings.append(
                f"the ledger's part {req.part} is the take of {active_entry.recorded_at}, "
                f"not this one; left as it is"
            )

    # --- What stands afterwards.
    restored = None
    fallback = None
    if ledger_is_active and entry is not None:
        restored = rl.previous_take(entry, *key)
        if restored is None and state_part is not None:
            fallback = _fallback_from_state(req, state_part, stamp)
            restored = fallback
    promote = None
    if state_is_active and state_part is not None and state_part.takes:
        wanted = restored.recorded_at if restored is not None else None
        promote = next((t for t in state_part.takes if t.recorded_at == wanted), None) or max(
            state_part.takes, key=lambda t: t.take
        )

    stands_in_ledger = restored is not None or (active_entry is not None and not ledger_is_active)
    stands_in_state = state_part is not None and (not state_is_active or promote is not None)
    gone = not stands_in_ledger and not stands_in_state

    # --- The files' view, and the refusals.
    location = _file_location(req, lecture)
    disk_parts = _disk_parts(req, location) if location else set()
    if location:
        _refuse_if_busy(req, location)
    ledger_parts = {
        p.part
        for p in (entry.parts if entry else [])
        if (p.course_id, p.lang) == (req.course_id, req.lang)
    }
    state_parts = {p.part for p in lecture.parts} if lecture else set()
    if gone:
        later = sorted(n for n in ledger_parts | state_parts | disk_parts if n > req.part)
        if later:
            listing = ", ".join(str(n) for n in later)
            raise InvalidationRefused(
                f"part {req.part} has no previous take to fall back on, and part(s) {listing} "
                f"come after it: removing it would leave a hole in the numbering, and parts are "
                f"never renumbered. Re-record part {req.part} (the new take replaces this one), "
                f"or invalidate the later part(s) first, highest first."
            )
    left = (ledger_parts | state_parts | {n or 1 for n in disk_parts}) - {req.part}
    single_part = gone and req.part >= 2 and left == {1}

    # --- The file plan.
    moves: list[Move] = []
    renames_to_single: list[Move] = []
    if location:
        slot = _file_slot(req, location)
        moves = _plan_moves(req, location, slot, state_is_active, state_take, promote)
        if not moves and (state_is_active or state_take is not None):
            warnings.append(f"no video files of this take found under {location.root}")
        if single_part:
            renames_to_single = _plan_back_to_single_part(req, location, warnings)
    elif req.state is not None and lecture is not None:
        warnings.append("video files not touched: no recordings root (pass --root)")
    elif req.state is None or lecture is None:
        warnings.append("video files not touched: the state file does not know this take")
    all_moves = moves + renames_to_single

    effect: Effect = "restored" if restored is not None or promote is not None else "removed"
    if not ledger_is_active and not state_is_active:
        effect = "history"
    result = InvalidateResult(
        course_id=req.course_id,
        part=req.part,
        lang=req.lang,
        recorded_at=stamp,
        effect=effect,
        deck=req.deck_key,
        ledger_path=req.ledger_path if ledger is not None else None,
        restored_recorded_at=(
            restored.recorded_at
            if restored is not None
            else (promote.recorded_at if promote is not None else None)
        ),
        single_part=single_part,
        moves=all_moves,
        warnings=warnings,
        note=req.note,
        dry_run=req.dry_run,
    )
    if fallback is not None:
        warnings.append(
            f"the ledger kept no earlier take; rebuilt the take of {fallback.recorded_at} "
            f"from the state file (fingerprints from its commit, or unverifiable)"
        )
    elif effect == "removed" and ledger_is_active:
        warnings.append(
            "the ledger holds no earlier take of this part. If one was overwritten by a clm "
            "older than this feature, its entry is in the ledger's git history."
        )

    # --- Do it: the moves first (rollback-able), then the ledger, then the state.
    if ledger_has_target and entry is not None:
        outcome = rl.invalidate_part(
            entry, *key, recorded_at=stamp, note=req.note, fallback=fallback
        )
        result.ack_dropped = outcome.ack_dropped
    if req.dry_run:
        return result

    completed = _run_moves(all_moves)
    if ledger_has_target and ledger is not None and req.ledger_path is not None:
        try:
            rl.save(ledger, req.ledger_path)
        except OSError as exc:
            _rollback(completed)
            raise InvalidationRefused(
                f"cannot write the ledger {req.ledger_path}: {exc}; nothing changed"
            ) from None

    if req.state is not None and lecture is not None:
        try:
            for old, new in completed:
                req.state.rename_recording_paths(
                    str(old), str(new), old_processed=str(old), new_processed=str(new)
                )
            req.state.invalidate_take(
                lecture.lecture_id,
                req.part,
                take=state_take,
                promote=promote.take if promote is not None else None,
                note=req.note,
            )
            result.state_updated = state_is_active or state_take is not None
            if req.persist_state is not None:
                req.persist_state(req.state)
        except (OSError, ValueError) as exc:
            result.state_updated = False
            warnings.append(f"the state file was not updated: {exc}")
            logger.warning("State update failed invalidating {}: {}", req.deck_key, exc)
    logger.info(
        "Invalidated take {} of {} part {} ({}): {}",
        stamp,
        req.deck_key or req.deck_name,
        req.part,
        req.lang,
        effect,
    )
    return result


# ---------------------------------------------------------------------------
# State lookups
# ---------------------------------------------------------------------------


def _known_lecture_part(
    req: InvalidateRequest,
) -> tuple[LectureState | None, RecordingPart | None]:
    if req.state is None or req.lecture_id is None:
        return None, None
    lecture = req.state.get_lecture(req.lecture_id)
    if lecture is None:
        return None, None
    return lecture, next((p for p in lecture.parts if p.part == req.part), None)


def _find_in_state(
    state: CourseRecordingState, part: int, stamp: str
) -> tuple[LectureState | None, RecordingPart | None]:
    """The lecture holding ``(part, recorded_at)``: the ledger knows decks, the state lectures.

    The stamp is the one link between the two stores (:func:`clm.recordings.report`
    relies on it too). Two lectures holding the same stamp would be a guess.
    """
    hits = [
        (lecture, sp)
        for lecture in state.lectures
        for sp in lecture.parts
        if sp.part == part
        and (sp.recorded_at == stamp or any(t.recorded_at == stamp for t in sp.takes))
    ]
    if len(hits) > 1:
        names = ", ".join(lecture.lecture_id for lecture, _ in hits)
        raise InvalidationRefused(
            f"the state file has part {part} recorded at {stamp} in more than one lecture: {names}"
        )
    return hits[0] if hits else (None, None)


def _fallback_from_state(
    req: InvalidateRequest, state_part: RecordingPart, stamp: str
) -> rl.LedgerPart | None:
    """Rebuild the take before *stamp* from the state file, for a ledger without history.

    Ledgers written before ``superseded`` existed overwrote the previous take,
    but the state file still knows it. Its fingerprints come from its commit;
    without those the entry reads as ``unverifiable`` in the report, which is
    the truth, and still better than claiming the part was never recorded.
    """
    earlier = [
        (t.recorded_at, t.git_commit, t.git_dirty)
        for t in state_part.takes
        if t.recorded_at and t.recorded_at < stamp
    ]
    if state_part.recorded_at and state_part.recorded_at < stamp:
        earlier.append((state_part.recorded_at, state_part.git_commit, state_part.git_dirty))
    if not earlier:
        return None
    recorded_at, commit, dirty = max(earlier, key=lambda t: t[0])
    members = None
    if commit and req.deck_path is not None:
        members = rl.deck_members_at_ref(req.deck_path, commit, req.lang)
    members = members or {}
    return rl.LedgerPart(
        part=req.part,
        recorded_at=recorded_at,
        course_id=req.course_id,
        lang=req.lang,
        anchor=rl.anchor_for(commit, dirty),
        members=members,
        order=rl.id_order(members),
        evidence="anchor",
    )


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@define
class _Location:
    root: Path
    rel_dir: str
    deck_name: str
    raw_suffix: str


def _file_location(req: InvalidateRequest, lecture: LectureState | None) -> _Location | None:
    if req.recordings_root is None:
        return None
    section, deck = req.section_name, req.deck_name
    if (section is None or deck is None) and lecture is not None:
        section, sep, deck = lecture.lecture_id.partition("::")
        if not sep:
            return None
    if not section or not deck:
        return None
    rel_dir = str(recording_relative_dir(req.course_id, section))
    return _Location(req.recordings_root, rel_dir, deck, req.raw_suffix)


def _names_deck(path: Path, location: _Location) -> bool:
    """Whether *path* belongs to the deck: in its directory, any part or take."""
    rel_parts = PurePosixPath(Path(location.rel_dir).as_posix()).parts
    if path.parent.parts[-len(rel_parts) :] != rel_parts:
        return False
    base_with, _ = parse_raw_stem(path.stem, location.raw_suffix)
    base, _, _ = parse_part_take(base_with)
    return base == sanitize_file_name(location.deck_name)


def _refuse_if_busy(req: InvalidateRequest, location: _Location) -> None:
    """A job still running would land its output back in the slot we are clearing."""
    for job in req.active_jobs:
        if job.is_terminal:
            continue
        if _names_deck(job.raw_path, location) or _names_deck(job.final_path, location):
            raise InvalidationRefused(
                f"processing job {job.id[:8]} ({job.state.value}) is still running for this deck; "
                f"wait for it, or cancel it with `clm recordings jobs cancel`, first"
            )
    for path in req.pending_paths:
        if _names_deck(path, location):
            raise InvalidationRefused(
                f"a deferred rename of {path.name} is still pending (file locked); try again "
                f"once it has drained"
            )


def _disk_parts(req: InvalidateRequest, location: _Location) -> set[int]:
    """Active part numbers on disk for the deck (``0`` = unsuffixed)."""
    from clm.recordings.processing.batch import VIDEO_EXTENSIONS

    sanitized = sanitize_file_name(location.deck_name)
    found: set[int] = set()
    for base_dir in (
        to_process_dir(location.root),
        archive_dir(location.root),
        final_dir(location.root),
    ):
        subtree = base_dir / location.rel_dir
        if not subtree.is_dir():
            continue
        for child in subtree.iterdir():
            if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS:
                base_with, _ = parse_raw_stem(child.stem, req.raw_suffix)
                base, part = parse_part(base_with)
                if base == sanitized:
                    found.add(part)
    return found


def _file_slot(req: InvalidateRequest, location: _Location) -> int:
    """The part number in the file names: state part 1 is ``0`` while unsuffixed."""
    if req.part != 1:
        return req.part
    occupied = [slot for slot in (1, 0) if _active_files(req, location, slot)]
    if len(occupied) > 1:
        raise InvalidationRefused(
            f"both an unsuffixed and a '(part 1)' recording of {location.deck_name} are on disk "
            f"in {location.rel_dir}; which one is part 1 is ambiguous, so move one away first"
        )
    if occupied:
        return occupied[0]
    takes = takes_dir(location.root) / location.rel_dir
    if takes.is_dir() and any(_take_file_part(c, req, location) == 1 for c in takes.iterdir()):
        return 1
    return 0


def _take_file_part(child: Path, req: InvalidateRequest, location: _Location) -> int | None:
    base_with, is_raw = parse_raw_stem(child.stem, req.raw_suffix)
    base, part, take = parse_part_take(base_with if is_raw else child.stem)
    if not child.is_file() or base != sanitize_file_name(location.deck_name) or take == 0:
        return None
    return part


def _active_files(req: InvalidateRequest, location: _Location, slot: int) -> list[Path]:
    return _scan_active_take_files(
        recordings_root=location.root,
        rel_dir=location.rel_dir,
        deck_name=location.deck_name,
        part=slot,
        raw_suffix=req.raw_suffix,
        lang=req.lang,
    )


def _free_destination(directory: Path, name: str, taken: set[Path]) -> Path:
    """``directory/name``, with the ``(2)``, ``(3)`` … of ``superseded/`` on collision."""
    dest = directory / name
    stem, ext = Path(name).stem, Path(name).suffix
    counter = 2
    while dest.exists() or dest in taken:
        dest = directory / f"{stem} ({counter}){ext}"
        counter += 1
    taken.add(dest)
    return dest


def _plan_moves(
    req: InvalidateRequest,
    location: _Location,
    slot: int,
    state_is_active: bool,
    state_take: int | None,
    promote: Any,
) -> list[Move]:
    """The invalidated take's files → ``superseded/``, the restored take's → the active slots."""
    takes_subtree = takes_dir(location.root) / location.rel_dir
    dest_dir = superseded_dir(location.root) / location.rel_dir
    taken: set[Path] = set()
    moves: list[Move] = []

    if state_is_active:
        sources = _active_files(req, location, slot)
    elif state_take is not None:
        raws, finals = _scan_takes_for(
            takes_subtree, location.deck_name, slot, state_take, req.raw_suffix
        )
        sources = raws + finals
    else:
        sources = []
    for src in sources:
        moves.append((src, _free_destination(dest_dir, src.name, taken)))

    if promote is not None:
        raws, finals = _scan_takes_for(
            takes_subtree, location.deck_name, slot, promote.take, req.raw_suffix
        )
        if not raws and not finals:
            logger.warning("No files in takes/ for take {} of {}", promote.take, location.deck_name)
        # Same rule as restore: a final in takes/ means the take was processed
        # and its raw belongs back in archive/.
        raw_base = archive_dir(location.root) if finals else to_process_dir(location.root)
        leaving = {src for src, _ in moves}
        for src in raws:
            name = raw_filename(
                location.deck_name,
                ext=src.suffix,
                raw_suffix=req.raw_suffix,
                part=slot,
                lang=req.lang,
            )
            moves.append(_checked(src, raw_base / location.rel_dir / name, leaving))
        for src in finals:
            name = final_filename(location.deck_name, ext=src.suffix, part=slot, lang=req.lang)
            moves.append(_checked(src, final_dir(location.root) / location.rel_dir / name, leaving))
    return moves


def _checked(src: Path, dest: Path, leaving: Iterable[Path]) -> Move:
    if dest.exists() and dest not in leaving:
        raise InvalidationRefused(
            f"cannot restore {src.name}: {dest} is occupied by a file that is not part of this take"
        )
    return src, dest


def _plan_back_to_single_part(
    req: InvalidateRequest, location: _Location, warnings: list[str]
) -> list[Move]:
    """``(part 1)`` → unsuffixed: the reverse of ``_cascade_unsuffixed_to_part1`` (#908).

    A file name in the way leaves all the names as they are: the deck is
    single-part in the ledger and the state either way, and recording another
    part would find the names it expects.
    """
    moves: list[Move] = []
    for src in _active_files(req, location, 1):
        base_with, is_raw = parse_raw_stem(src.stem, req.raw_suffix)
        name = (
            raw_filename(location.deck_name, ext=src.suffix, raw_suffix=req.raw_suffix)
            if is_raw
            else final_filename(location.deck_name, ext=src.suffix)
        )
        moves.append((src, src.with_name(name)))
    takes_subtree = takes_dir(location.root) / location.rel_dir
    if takes_subtree.is_dir():
        for child in sorted(takes_subtree.iterdir()):
            if _take_file_part(child, req, location) != 1:
                continue
            base_with, is_raw = parse_raw_stem(child.stem, req.raw_suffix)
            _, _, take = parse_part_take(base_with if is_raw else child.stem)
            name = take_filename(
                location.deck_name,
                ext=child.suffix,
                raw_suffix=req.raw_suffix,
                take=take,
                is_raw=is_raw,
            )
            moves.append((child, child.with_name(name)))
    blocked = [dest for _, dest in moves if dest.exists()]
    if blocked:
        warnings.append(
            f"the deck is single-part again, but its files keep their '(part 1)' names: "
            f"{blocked[0].name} is in the way"
        )
        return []
    return moves


def _run_moves(moves: list[Move]) -> list[Move]:
    completed: list[Move] = []
    try:
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            safe_move(src, dst)
            completed.append((src, dst))
    except OSError as exc:
        _rollback(completed)
        raise InvalidationRefused(
            f"cannot move the take's files ({exc}); everything was put back, nothing changed"
        ) from None
    return completed


def _rollback(completed: list[Move]) -> None:
    for src, dst in reversed(completed):
        try:
            safe_move(dst, src)
        except OSError as exc:
            logger.error("Rollback failed for {} → {}: {}", dst, src, exc)
