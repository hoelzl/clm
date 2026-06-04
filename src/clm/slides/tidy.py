"""Reorganize a topic's authoring sidecars between the flat and foldered layouts.

``clm slides tidy`` moves the authoring *sidecars* — voiceover companions
(``voiceover_*.py``) and HTTP-replay cassettes (``*.http-cassette.yaml``) — into
per-type subdirectories (``voiceover/`` and ``cassettes/``) so the topic
directory holds only the core ``slides_*.py`` sources and genuine output
companions (``img/``, ``drawio/``). ``--layout sibling`` flattens them back.

- **Moves** use ``git mv`` for tracked files (history follows the file), falling
  back to a plain move for untracked files or outside a repo.
- **Transient** cassette staging markers (``*.http-cassette.yaml.staging-*`` and
  their ``.completed`` companions) are *deleted*, not moved — they are
  regenerated, and the orphan sweep keys off the canonical's parent, so a stale
  sibling marker after the canonical moves would be orphaned.
- A file already at its target is a **no-op** (the op is idempotent).
- A target that already exists (the same sidecar present in *both* layouts) is a
  **conflict**: that one move is skipped so nothing is clobbered, and the caller
  is told to reconcile the duplicate (``clm validate`` flags it too).
- A ``voiceover/`` / ``cassettes/`` / legacy ``_cassettes/`` directory emptied by
  a flatten is removed.

This is the bulk counterpart to the per-op layout handling in
:mod:`clm.slides.voiceover_tools` and ``NotebookFile`` cassette resolution.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from clm.slides.voiceover_tools import COMPANION_SUBDIR

# Canonical cassette sidecar dir, plus the legacy underscore-prefixed alias that
# `tidy` consolidates into the canonical one under ``--layout subdir``.
CASSETTE_SUBDIR = "cassettes"
CASSETTE_LEGACY_SUBDIR = "_cassettes"

_VOICEOVER_RE = re.compile(r"voiceover_.*\.py$")
_CASSETTE_RE = re.compile(r".*\.http-cassette\.yaml$")
_STAGING_RE = re.compile(r".*\.http-cassette\.yaml\.staging-.*$")

# Junk directories never worth descending into for a reorg walk. Note this is
# deliberately *not* ``SKIP_DIRS_FOR_COURSE`` — that set now contains
# ``voiceover``, and tidy must descend into an existing ``voiceover/`` to flatten
# or to no-op files already inside it.
_SKIP_WALK_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ipynb_checkpoints",
        "node_modules",
    }
)


@dataclass
class TidyMove:
    """A planned relocation of one sidecar file."""

    src: Path
    dst: Path
    kind: str  # "voiceover" | "cassette"


@dataclass
class TidyPlan:
    """The planned moves/deletes for a tidy run (computed without touching disk)."""

    moves: list[TidyMove] = field(default_factory=list)
    deletes: list[Path] = field(default_factory=list)
    # (src, dst) skipped because dst already exists (the sidecar is present in
    # both layouts). Never overwritten.
    conflicts: list[tuple[Path, Path]] = field(default_factory=list)
    # Filled in by ``apply_tidy``: sidecar dirs removed because a flatten emptied
    # them.
    removed_dirs: list[Path] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return not self.moves and not self.deletes


def _iter_files(root: Path) -> Iterator[Path]:
    """Yield files under ``root`` (recursively), skipping junk dirs.

    If ``root`` is a single file, yield just it.
    """
    if root.is_file():
        yield root
        return
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if any(part in _SKIP_WALK_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _topic_dir(f: Path, sidecar_dirs: tuple[str, ...]) -> Path:
    """The topic directory owning sidecar file ``f``.

    If ``f`` already lives in one of ``sidecar_dirs`` the topic is its
    grandparent; otherwise (a sibling) the topic is its own parent.
    """
    return f.parent.parent if f.parent.name in sidecar_dirs else f.parent


def _target(topic: Path, name: str, subdir: str, layout: str) -> Path:
    return topic / subdir / name if layout == "subdir" else topic / name


def plan_tidy(
    root: Path,
    *,
    layout: str = "subdir",
    do_voiceover: bool = True,
    do_cassettes: bool = True,
) -> TidyPlan:
    """Compute the moves/deletes to bring ``root`` to ``layout`` WITHOUT writing.

    ``root`` may be a single slide/sidecar file or a directory (topic, section,
    or whole course root) walked recursively. ``layout`` is ``"subdir"`` (move
    sidecars into ``voiceover/`` / ``cassettes/``) or ``"sibling"`` (flatten).
    """
    plan = TidyPlan()
    planned_dst: set[Path] = set()

    def add_move(src: Path, dst: Path, kind: str) -> None:
        if src.resolve() == dst.resolve():
            return  # already in place — idempotent no-op
        if dst.exists() or dst.resolve() in planned_dst:
            plan.conflicts.append((src, dst))
            return
        planned_dst.add(dst.resolve())
        plan.moves.append(TidyMove(src=src, dst=dst, kind=kind))

    for f in _iter_files(root):
        name = f.name
        # Staging markers first (their name also matches the cassette prefix on a
        # naive check, but the regex anchors on the full suffix so order is just
        # for clarity). Gated under cassettes since they are cassette artifacts.
        if do_cassettes and _STAGING_RE.match(name):
            plan.deletes.append(f)
            continue
        if do_voiceover and _VOICEOVER_RE.match(name):
            topic = _topic_dir(f, (COMPANION_SUBDIR,))
            add_move(f, _target(topic, name, COMPANION_SUBDIR, layout), "voiceover")
            continue
        if do_cassettes and _CASSETTE_RE.match(name):
            topic = _topic_dir(f, (CASSETTE_SUBDIR, CASSETTE_LEGACY_SUBDIR))
            add_move(f, _target(topic, name, CASSETTE_SUBDIR, layout), "cassette")
            continue

    return plan


def _git_mv(src: Path, dst: Path) -> bool:
    """Move ``src`` → ``dst`` via ``git mv`` (staged rename). Return success.

    Returns ``False`` (so the caller falls back to a plain move) when ``src`` is
    untracked, git is unavailable, or the path is outside a work tree.
    """
    try:
        result = subprocess.run(
            ["git", "mv", str(src), str(dst)],
            cwd=str(src.parent),
            capture_output=True,
            text=True,
        )
    except (OSError, FileNotFoundError):
        return False
    return result.returncode == 0


def _prune_empty_sidecar_dirs(plan: TidyPlan) -> list[Path]:
    """Remove sidecar dirs left empty by the applied plan. Return removed dirs."""
    sidecar_names = {COMPANION_SUBDIR, CASSETTE_SUBDIR, CASSETTE_LEGACY_SUBDIR}
    candidates: set[Path] = set()
    for mv in plan.moves:
        if mv.src.parent.name in sidecar_names:
            candidates.add(mv.src.parent)
    for f in plan.deletes:
        if f.parent.name in sidecar_names:
            candidates.add(f.parent)
    removed: list[Path] = []
    for d in sorted(candidates):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            removed.append(d)
    return removed


def apply_tidy(plan: TidyPlan, *, use_git: bool = True) -> TidyPlan:
    """Execute ``plan`` against the filesystem and return it (with ``removed_dirs``).

    Moves prefer ``git mv`` for tracked files (so history follows); untracked
    files or a non-repo fall back to :func:`shutil.move`. Staging markers are
    deleted. Sidecar dirs emptied by the moves/deletes are removed.
    """
    for mv in plan.moves:
        mv.dst.parent.mkdir(parents=True, exist_ok=True)
        if not (use_git and _git_mv(mv.src, mv.dst)):
            shutil.move(str(mv.src), str(mv.dst))
    for f in plan.deletes:
        f.unlink()
    plan.removed_dirs = _prune_empty_sidecar_dirs(plan)
    return plan
