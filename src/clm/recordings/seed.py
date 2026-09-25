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

__all__ = ["SeedPart", "SeedResult", "commit_before", "lang_of_course_id", "seed_from_state"]

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
    """The last commit on ``HEAD`` not after *recorded_at* (``git rev-list -1 --before``)."""
    out = _git(repo, "rev-list", "-1", f"--before={recorded_at}", "HEAD")
    return out.strip() or None if out else None


def lang_of_course_id(course_id: str) -> str | None:
    """``"…-de"`` / ``"…-en"`` → the recording language the dashboard appended."""
    for lang in rl.RECORDABLE_LANGS:
        if course_id.endswith(f"-{lang}"):
            return lang
    return None


class _CommitTrees:
    """Throwaway detached worktrees, one per anchor commit, removed on exit."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.base = Path(tempfile.mkdtemp(prefix="clm-seed-"))
        self.trees: dict[str, Path | None] = {}

    def tree(self, commit: str) -> Path | None:
        if commit not in self.trees:
            target = self.base / commit[:12]
            out = _git(self.repo, "worktree", "add", "--detach", "--quiet", str(target), commit)
            self.trees[commit] = target if out is not None and target.is_dir() else None
            if self.trees[commit] is None:
                logger.warning("Could not check out {} for seeding", commit[:12])
        return self.trees[commit]

    def close(self) -> None:
        for tree in self.trees.values():
            if tree is not None:
                _git(self.repo, "worktree", "remove", "--force", str(tree))
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
    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec, CourseSpecError

    if not spec_file.is_file():
        return None
    try:
        spec = CourseSpec.from_file(spec_file)
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
    anchor_deck: Path, anchor_root: Path, course_root: Path, topic_id: str | None
) -> Path | None:
    """The current path of a deck resolved in an anchor tree, or ``None``."""
    from clm.core.topic_resolver import build_topic_map

    try:
        rel = anchor_deck.resolve().relative_to(anchor_root.resolve())
    except ValueError:
        return None
    candidate = course_root / rel
    if candidate.is_file():
        return candidate
    if topic_id:
        matches = build_topic_map(course_root / "slides").get(topic_id, [])
        for match in matches:
            by_name = match.path / anchor_deck.name
            if by_name.is_file():
                return by_name
    return None


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
    """
    lang = rl._check_lang(lang)
    result = SeedResult(course_id=state.course_id, lang=lang)
    course_root = course_root.resolve()
    spec_rel = spec_file.resolve().relative_to(course_root)
    current_course = _load_course(course_root / spec_rel, course_root)
    courses_at: dict[str, Course | None] = {}
    written: set[Path] = set()

    with _commit_trees(course_root) as trees:
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

                # 1. the anchor
                if part.git_commit:
                    anchor = rl.anchor_for(part.git_commit, part.git_dirty)
                else:
                    commit = (
                        commit_before(course_root, part.recorded_at) if part.recorded_at else None
                    )
                    if commit is None:
                        seed.reason = "no commit stamped and none before recorded_at"
                        continue
                    anchor = rl.RecordingAnchor(kind="time", commit=commit, dirty=False)
                seed.anchor = anchor.model_dump()
                assert anchor.commit is not None
                if names is None:
                    seed.reason = "lecture id is not '<section>::<deck>'"
                    continue
                section_name, deck_name = names

                # 2. the deck, through the course at the anchor
                tree = trees.tree(anchor.commit)
                if tree is None:
                    seed.reason = f"anchor commit {anchor.commit[:12]} cannot be checked out"
                    continue
                if anchor.commit not in courses_at:
                    courses_at[anchor.commit] = _load_course(tree / spec_rel, tree)
                course_at = courses_at[anchor.commit]
                anchor_deck: Path | None = None
                topic_id: str | None = None
                if course_at is not None:
                    _sid, topic_id, anchor_deck = course_at.resolve_deck_location(
                        section_name, deck_name, lang
                    )
                if anchor_deck is None:
                    # The spec (or the deck under those names) did not exist
                    # at the anchor: resolve through the current course and
                    # read that deck at the anchor tree instead.
                    if current_course is not None:
                        _sid, topic_id, current_deck = current_course.resolve_deck_location(
                            section_name, deck_name, lang
                        )
                        if current_deck is not None:
                            anchor_deck = tree / current_deck.resolve().relative_to(course_root)
                if anchor_deck is None or not anchor_deck.is_file():
                    seed.reason = (
                        f"deck '{deck_name}' in section '{section_name}' not found at "
                        f"{anchor.commit[:12]}"
                    )
                    continue

                # 3. the members at the anchor
                members = rl.deck_members(anchor_deck, lang)
                if not members:
                    seed.reason = f"deck at {anchor.commit[:12]} is not a parseable split pair"
                    continue

                # 4. the current home
                current_deck = _map_to_current(anchor_deck, tree, course_root, topic_id)
                if current_deck is None:
                    seed.reason = (
                        f"deck exists at {anchor.commit[:12]} as "
                        f"'{anchor_deck.relative_to(tree).as_posix()}' but not in the current tree"
                    )
                    continue
                seed.deck = current_deck.relative_to(course_root).as_posix()
                seed.members = len(members)

                # 5. write (idempotent)
                ledger_path = rl.ledger_path_for(current_deck)
                seed.ledger = ledger_path.relative_to(course_root).as_posix()
                entry = rl.LedgerPart(
                    part=part.part,
                    recorded_at=part.recorded_at,
                    course_id=state.course_id,
                    lang=lang,
                    anchor=anchor,
                    members=members,
                    order=rl.id_order(members),
                )
                existing = _existing(ledger_path, rl.deck_key_for(current_deck), entry)
                if existing is not None and existing == entry:
                    seed.status = "unchanged"
                    continue
                seed.status = "updated" if existing is not None else "seeded"
                if not dry_run:
                    rl.record_part(ledger_path, rl.deck_key_for(current_deck), entry)
                    written.add(ledger_path)

    result.ledgers_written = sorted(p.relative_to(course_root).as_posix() for p in written)
    return result


def _existing(ledger_path: Path, deck_key: str, entry: rl.LedgerPart) -> rl.LedgerPart | None:
    try:
        ledger = rl.load(ledger_path)
    except rl.LedgerError:
        return None
    deck = ledger.decks.get(deck_key)
    if deck is None:
        return None
    key = rl.part_identity(entry)
    for existing in deck.parts:
        if rl.part_identity(existing) == key:
            return existing
    return None
