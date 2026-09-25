"""Seed the committed recordings ledger from a machine-local state file (#1005).

Step 3 of the #907 design (``docs/claude/design/recordings-rerecord-backlog.md``
§5, the prerequisite half of gap (a)): recordings made before the ledger
existed live only in ``<user-config>/clm/recordings/<course-id>.json``, with
a stamped ``git_commit`` on the dashboard-era parts and ``recorded_at`` on
all of them. No video analysis is needed to give each of them a ledger
entry:

* **anchor** — the stamped commit (``commit`` / ``commit-dirty``), else the
  last commit before ``recorded_at`` on the current branch (``time``);
* **deck** — the lecture key ``"<section name>::<deck name>"`` resolved
  through the course as it was **at the anchor commit** (the display
  names are the only identity the old parts carry: ``section_id`` is
  ``None`` in every real state file), then mapped onto the current tree;
* **members** — the recorded language's fingerprints of the deck at the
  anchor, read from a throwaway ``git worktree`` of that commit (a
  materialised tree, so renames since then and the sibling-vs-subdir
  companion layout need no special cases).

Idempotent: an entry that already matches is left alone. Parts whose deck
cannot be resolved at the anchor, or whose deck no longer exists in the
current tree, are **listed, never guessed**.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from attrs import define, field
from loguru import logger

from clm.recordings import ledger as rl

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.recordings.state import CourseRecordingState

__all__ = [
    "SeedPart",
    "SeedResult",
    "commit_before",
    "commits_after",
    "lang_of_course_id",
    "repo_prefix",
    "seed_from_state",
]

SeedStatus = Literal["seeded", "updated", "unchanged", "unresolved"]


@define
class SeedPart:
    lecture_id: str
    part: int
    recorded_at: str
    status: SeedStatus
    anchor: dict[str, Any]
    deck: str | None = None  # current path, course-root-relative
    ledger: str | None = None  # current ledger path, course-root-relative
    members: int = 0
    reason: str | None = None


@define
class SeedResult:
    course_id: str
    lang: str
    parts: list[SeedPart] = field(factory=list)
    ledgers_written: list[str] = field(factory=list)

    @property
    def unresolved(self) -> list[SeedPart]:
        return [p for p in self.parts if p.status == "unresolved"]

    def to_dict(self) -> dict[str, Any]:
        from attrs import asdict

        counts: dict[str, int] = {}
        for p in self.parts:
            counts[p.status] = counts.get(p.status, 0) + 1
        return {
            "schema": 1,
            "tool": "recordings",
            "verb": "seed-ledger",
            "course_id": self.course_id,
            "lang": self.lang,
            "counts": counts,
            "ledgers_written": list(self.ledgers_written),
            "parts": [asdict(p) for p in self.parts],
        }


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"},
        )
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode != 0:
        logger.debug("git {} failed: {}", " ".join(args), completed.stderr.strip())
        return None
    return completed.stdout


def commit_before(repo: Path, recorded_at: str) -> str | None:
    """The last commit on ``HEAD`` not after *recorded_at* (``git rev-list -1 --before``).

    *recorded_at* must be an ISO-8601 timestamp: git's approxidate treats
    garbage as "now", which would silently anchor a part on ``HEAD`` — a
    guess, which seeding never makes.
    """
    from datetime import datetime

    try:
        stamp = datetime.fromisoformat(recorded_at)
    except (TypeError, ValueError):
        return None
    out = _git(repo, "rev-list", "-1", f"--before={stamp.isoformat()}", "HEAD")
    return out.strip() or None if out else None


#: How many commits after a recording a time anchor may look at when the
#: deck cannot be resolved at the last commit before it. A course authored
#: just in time is recorded from a working tree whose edits land in the next
#: commits; the week's section is often still disabled in the last commit
#: before the recording.
TIME_ANCHOR_LOOKAHEAD = 5


def commits_after(repo: Path, recorded_at: str, limit: int = TIME_ANCHOR_LOOKAHEAD) -> list[str]:
    """The first *limit* commits on ``HEAD`` after *recorded_at*, oldest first."""
    from datetime import datetime

    try:
        stamp = datetime.fromisoformat(recorded_at)
    except (TypeError, ValueError):
        return []
    out = _git(repo, "rev-list", "--reverse", f"--after={stamp.isoformat()}", "HEAD")
    return out.split()[:limit] if out else []


def lang_of_course_id(course_id: str) -> str | None:
    """``"…-de"`` / ``"…-en"`` → the recording language the dashboard appended."""
    for lang in rl.RECORDABLE_LANGS:
        if course_id.endswith(f"-{lang}"):
            return lang
    return None


def repo_prefix(course_root: Path) -> tuple[Path, Path] | None:
    """``(repo toplevel, course_root relative to it)`` — a course may live in a repo subdirectory."""
    top = _git(course_root, "rev-parse", "--show-toplevel")
    prefix = _git(course_root, "rev-parse", "--show-prefix")
    if top is None or prefix is None:
        return None
    return Path(top.strip()), Path(prefix.strip())


class _CommitTrees:
    """Throwaway detached worktrees of anchor commits, one at a time.

    Seeding walks the parts grouped by anchor commit, so at most one tree
    (the whole repository at that commit, LFS smudge skipped) exists at any
    moment; it is removed as soon as its commit's parts are done.
    """

    def __init__(self, repo: Path):
        self.repo = repo
        self.base = Path(tempfile.mkdtemp(prefix="clm-seed-"))
        self.current: tuple[str, Path] | None = None

    def tree(self, commit: str) -> Path | None:
        if self.current is not None and self.current[0] == commit:
            return self.current[1]
        self.release()
        target = self.base / commit[:12]
        out = _git(self.repo, "worktree", "add", "--detach", "--quiet", str(target), commit)
        if out is None or not target.is_dir():
            logger.warning("Could not check out {} for seeding", commit[:12])
            return None
        self.current = (commit, target)
        return target

    def release(self) -> None:
        if self.current is not None:
            _git(self.repo, "worktree", "remove", "--force", str(self.current[1]))
            self.current = None

    def close(self) -> None:
        self.release()
        shutil.rmtree(self.base, ignore_errors=True)
        _git(self.repo, "worktree", "prune")


@contextmanager
def _commit_trees(repo: Path) -> Iterator[_CommitTrees]:
    trees = _CommitTrees(repo)
    try:
        yield trees
    finally:
        trees.close()


def _load_course(spec_file: Path, course_root: Path) -> Course | None:
    """The course at *course_root*, disabled sections included.

    A week that was disabled in the spec at the anchor commit (and enabled
    in the working tree the recording was made from) must still resolve its
    display names, so the spec is parsed with ``keep_disabled=True``.
    """
    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec, CourseSpecError

    if not spec_file.is_file():
        return None
    try:
        spec = CourseSpec.from_file(spec_file, keep_disabled=True)
        return Course.from_spec(spec, course_root, output_root=None)
    except (CourseSpecError, OSError, ValueError) as exc:
        logger.warning("Could not load course {} at {}: {}", spec_file, course_root, exc)
        return None


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _split_lecture_id(lecture_id: str) -> tuple[str, str] | None:
    section, sep, deck = lecture_id.partition("::")
    return (section, deck) if sep else None


def _map_to_current(
    anchor_deck: Path,
    anchor_course_root: Path,
    course_root: Path,
    topic_id: str | None,
    topic_map: dict[str, list[Any]],
) -> Path | None:
    """The current path of a deck resolved in an anchor tree, or ``None``."""
    try:
        rel = anchor_deck.resolve().relative_to(anchor_course_root.resolve())
    except ValueError:
        return None
    from clm.recordings.report import find_deck_files

    candidate = course_root / rel
    if candidate.is_file():
        return candidate
    # Same topic directory, deck split (or joined) since the anchor: match by stem.
    stem = rl.deck_key_for(anchor_deck)
    lang = (
        lang_of_course_id(anchor_deck.stem.rsplit(".", 1)[-1]) if "." in anchor_deck.stem else None
    )
    for topic_dir in (candidate.parent, *[Path(m.path) for m in topic_map.get(topic_id or "", [])]):
        if not topic_dir.is_dir():
            continue
        by_name = topic_dir / anchor_deck.name
        if by_name.is_file():
            return by_name
        halves = find_deck_files(topic_dir, stem)
        if halves:
            return halves.get(lang or "de") or next(iter(halves.values()))
    return None


@define
class _Pending:
    lecture_id: str
    section: str
    deck: str
    part: Any  # RecordingPart
    anchor: rl.RecordingAnchor
    seed: SeedPart
    #: further commits to try when the deck is not found at ``anchor`` (time anchors only)
    alternatives: list[str] = field(factory=list)


def _resolve_deck(
    course: Course, section: str, deck: str, lang: str
) -> tuple[str | None, Path | None]:
    """``(topic_id, path)`` by section + deck name, else by a deck name unique in the course.

    A section renamed since the recording still carries the same decks; a
    deck name (``"NN Title"``) that occurs exactly once in the course is an
    unambiguous identity.
    """
    _sid, topic_id, path = course.resolve_deck_location(section, deck, lang)
    if path is not None:
        return topic_id, path
    hits: list[tuple[str | None, Path | None]] = []
    for sec in course.sections:
        try:
            name = sec.name[lang]
        except (KeyError, TypeError):
            continue
        _sid, tid, p = course.resolve_deck_location(name, deck, lang)
        if p is not None and (tid, p) not in hits:
            hits.append((tid, p))
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None, None
    # The number changed with the section's line-up: match the title alone.
    title = deck.split(" ", 1)[1] if " " in deck and deck.split(" ", 1)[0].isdigit() else deck
    by_title: list[tuple[str | None, Path | None]] = []
    for sec in course.sections:
        for nb in sec.notebooks:
            nb_lang = getattr(nb, "output_language_filter", None)
            if nb_lang in ("de", "en") and nb_lang != lang:
                continue
            try:
                name = nb.file_name(lang, "")
            except (KeyError, ValueError):
                continue
            if name.split(" ", 1)[-1] == title and (nb.topic.id, nb.source_path) not in by_title:
                by_title.append((nb.topic.id, nb.source_path))
    return by_title[0] if len(by_title) == 1 else (None, None)


def seed_from_state(
    state: CourseRecordingState,
    *,
    spec_file: Path,
    course_root: Path,
    lang: str,
    dry_run: bool = False,
) -> SeedResult:
    """Seed ledger entries for every active take in *state*.

    *spec_file* is the spec the recordings were made from (its
    course-root-relative path is looked up in each anchor tree; the current
    course is the fallback when the spec did not exist at the anchor).
    Raises ``ValueError`` when *spec_file* is not under *course_root* or
    *course_root* is not inside a git repository.
    """
    from clm.core.topic_resolver import build_topic_map

    lang = rl._check_lang(lang)
    result = SeedResult(course_id=state.course_id, lang=lang)
    course_root = course_root.resolve()
    try:
        spec_rel = spec_file.resolve().relative_to(course_root)
    except ValueError:
        raise ValueError(
            f"spec file {spec_file} is not under the course root {course_root}; "
            "pass --spec-file and --course-root consistently"
        ) from None
    located = repo_prefix(course_root)
    if located is None:
        raise ValueError(f"{course_root} is not inside a git repository")
    repo_top, prefix = located
    current_course = _load_course(course_root / spec_rel, course_root)
    topic_map = build_topic_map(course_root / "slides")
    written: set[Path] = set()

    # Pass 1: anchors (no git tree needed), grouped by commit.
    by_commit: dict[str, list[_Pending]] = {}
    for lecture in state.lectures:
        names = _split_lecture_id(lecture.lecture_id)
        for part in lecture.parts:
            seed = SeedPart(
                lecture_id=lecture.lecture_id,
                part=part.part,
                recorded_at=part.recorded_at,
                status="unresolved",
                anchor={},
            )
            result.parts.append(seed)
            alternatives: list[str] = []
            if part.git_commit:
                anchor = rl.anchor_for(part.git_commit, part.git_dirty)
            else:
                commit = commit_before(repo_top, part.recorded_at) if part.recorded_at else None
                alternatives = commits_after(repo_top, part.recorded_at) if part.recorded_at else []
                if commit is None and not alternatives:
                    seed.reason = "no commit stamped and no ISO recorded_at with a commit near it"
                    continue
                if commit is None:
                    commit, alternatives = alternatives[0], alternatives[1:]
                anchor = rl.RecordingAnchor(kind="time", commit=commit, dirty=False)
            seed.anchor = anchor.model_dump()
            if names is None:
                seed.reason = "lecture id is not '<section>::<deck>'"
                continue
            assert anchor.commit is not None
            by_commit.setdefault(anchor.commit, []).append(
                _Pending(lecture.lecture_id, names[0], names[1], part, anchor, seed, alternatives)
            )

    # Pass 2: one worktree per anchor commit, released before the next. A
    # time-anchored part whose deck is not found at its commit moves on to
    # the next candidate commit (queued at the end, so each tree is built
    # once).
    with _commit_trees(repo_top) as trees:
        queue = list(by_commit)
        i = 0
        while i < len(queue):
            commit = queue[i]
            i += 1
            pendings = by_commit[commit]
            tree = trees.tree(commit)
            if tree is None:
                for p in pendings:
                    p.seed.reason = f"anchor commit {commit[:12]} cannot be checked out"
                continue
            anchor_root = tree / prefix
            course_at = _load_course(anchor_root / spec_rel, anchor_root)
            for p in pendings:
                found = _seed_one(
                    p,
                    state=state,
                    lang=lang,
                    course_at=course_at,
                    current_course=current_course,
                    anchor_root=anchor_root,
                    course_root=course_root,
                    topic_map=topic_map,
                    dry_run=dry_run,
                    written=written,
                )
                if not found and p.alternatives:
                    nxt = p.alternatives.pop(0)
                    p.anchor = rl.RecordingAnchor(kind="time", commit=nxt, dirty=False)
                    p.seed.anchor = p.anchor.model_dump()
                    if nxt not in by_commit:
                        by_commit[nxt] = []
                        queue.append(nxt)
                    by_commit[nxt].append(p)

    result.ledgers_written = sorted(p.relative_to(course_root).as_posix() for p in written)
    return result


def _seed_one(
    p: _Pending,
    *,
    state: CourseRecordingState,
    lang: str,
    course_at: Course | None,
    current_course: Course | None,
    anchor_root: Path,
    course_root: Path,
    topic_map: dict[str, list[Any]],
    dry_run: bool,
    written: set[Path],
) -> bool:
    """Seed one part at its current anchor; ``False`` when the deck was not found there."""
    seed, anchor = p.seed, p.anchor
    assert anchor.commit is not None
    short = anchor.commit[:12]

    # The deck, through the course at the anchor.
    anchor_deck: Path | None = None
    topic_id: str | None = None
    if course_at is not None:
        topic_id, anchor_deck = _resolve_deck(course_at, p.section, p.deck, lang)
    if anchor_deck is None and current_course is not None:
        # The spec (or the deck under those names) did not exist at the
        # anchor: resolve through the current course, read at the anchor tree.
        topic_id, current_deck = _resolve_deck(current_course, p.section, p.deck, lang)
        if current_deck is not None:
            try:
                anchor_deck = anchor_root / current_deck.resolve().relative_to(course_root)
            except ValueError:
                anchor_deck = None
    if anchor_deck is None or not anchor_deck.is_file():
        seed.reason = f"deck '{p.deck}' in section '{p.section}' not found at {short}"
        return False

    # The members at the anchor.
    members = rl.deck_members(anchor_deck, lang)
    if not members:
        seed.reason = f"deck at {short} is not a parseable deck"
        return True

    # The current home.
    current_deck = _map_to_current(anchor_deck, anchor_root, course_root, topic_id, topic_map)
    if current_deck is None:
        seed.reason = (
            f"deck exists at {short} as '{anchor_deck.relative_to(anchor_root).as_posix()}' "
            f"but not in the current tree"
        )
        return True
    seed.deck = current_deck.relative_to(course_root).as_posix()
    seed.members = len(members)

    # Write (idempotent; never over record-time evidence).
    ledger_path = rl.ledger_path_for(current_deck)
    seed.ledger = ledger_path.relative_to(course_root).as_posix()
    entry = rl.LedgerPart(
        part=p.part.part,
        recorded_at=p.part.recorded_at,
        course_id=state.course_id,
        lang=lang,
        anchor=anchor,
        members=members,
        order=rl.id_order(members),
        evidence="anchor",
    )
    deck_key = rl.deck_key_for(current_deck)
    try:
        existing = _existing(ledger_path, deck_key, entry)
    except rl.LedgerError as exc:
        seed.reason = str(exc)
        return True
    if existing is not None:
        if existing == entry:
            seed.status = "unchanged"
            return True
        if existing.evidence == "recorded" and existing.members:
            # The dashboard fingerprinted what was on screen; a commit can
            # only approximate that. Keep the better evidence.
            seed.status = "unchanged"
            seed.reason = "kept the record-time entry"
            return True
    seed.status = "updated" if existing is not None else "seeded"
    if not dry_run:
        try:
            rl.record_part(ledger_path, deck_key, entry)
        except rl.LedgerError as exc:
            seed.status = "unresolved"
            seed.reason = str(exc)
            return True
        written.add(ledger_path)
    return True


def _existing(ledger_path: Path, deck_key: str, entry: rl.LedgerPart) -> rl.LedgerPart | None:
    """The entry with *entry*'s identity already in the ledger, or ``None``.

    A ledger that exists but cannot be read raises :class:`rl.LedgerError`
    — the part is then reported unresolved rather than written over.
    """
    ledger = rl.load(ledger_path)
    deck = ledger.decks.get(deck_key)
    if deck is None:
        return None
    key = rl.part_identity(entry)
    for existing in deck.parts:
        if rl.part_identity(existing) == key:
            return existing
    return None
