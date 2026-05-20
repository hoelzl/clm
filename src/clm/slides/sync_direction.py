"""Direction-of-edit inference for ``clm slides sync``.

Phase 7 v2 follow-up of the slide-format-redesign. When the user omits
``--source-lang``, this module infers which half of the DE/EN split pair
was the source of the most recent edit so the sync pass knows whose
cells to propose updates *from*.

Two signals are consulted, in order of preference:

1. **Snapshot drift** (preferred). A
   :class:`~clm.infrastructure.llm.cache.SyncSnapshotCache` row for the
   pair captures the ``(de_hash, en_hash)`` last accepted by the user.
   If the current on-disk hash matches on exactly one side, the *other*
   side drifted since the last sync — that drifted side is the source.
   Snapshots are content-addressed: a rebase that rewrites commit
   metadata does not invalidate them.

2. **Git commit timestamp** (fallback). When no snapshot evidence is
   conclusive, the most recently committed half is treated as the
   source. Requires both files tracked in a git repo.

Both signals are computed when possible; if they disagree, the result
is ambiguous and the caller must require an explicit ``--source-lang``.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clm.notebooks.slide_parser import Cell, parse_cells
from clm.slides.sync_writeback import cell_content_hash

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncSnapshotCache

logger = logging.getLogger(__name__)

__all__ = [
    "DirectionInference",
    "infer_source_lang",
]


# Roles the sync walker covers — kept in sync with ``clm.slides.sync``.
# Duplicated here (rather than imported) to keep this module independent
# of the engine module and avoid a circular import at type-check time.
_ROLE_TAGS = {"slide", "subslide", "voiceover", "notes"}


@dataclass
class DirectionInference:
    """Result of trying to infer which side was edited.

    ``source_lang`` is ``"de"`` or ``"en"`` when one of the signals
    yielded a definite answer, or ``None`` when no signal was usable
    (no git history, no snapshot rows, signals disagree, or a tie).

    ``signal`` names which source the answer came from:
    ``"snapshot"``, ``"git-timestamp"``, ``"snapshot+git-timestamp"``
    (used when the two disagree, in which case ``source_lang`` is
    ``None``), or ``"none"`` when nothing was conclusive.

    ``reason`` is a short human-readable explanation surfaced to the
    user in the CLI's note/warning output.
    """

    source_lang: str | None
    signal: str
    reason: str


def infer_source_lang(
    de_path: Path,
    en_path: Path,
    snapshot_cache: SyncSnapshotCache | None,
) -> DirectionInference:
    """Infer which side was edited from snapshots and/or git history.

    Returns a :class:`DirectionInference` describing the verdict. The
    caller is responsible for surfacing the result to the user (echoing
    a note when inferring, warning on disagreement with an explicit
    ``--source-lang`` override, or raising a ``UsageError`` when no
    signal was usable).
    """
    snapshot = _infer_from_snapshot(de_path, en_path, snapshot_cache)
    git_ts = _infer_from_git_timestamp(de_path, en_path)

    # Snapshot rows exist but disagree internally — bail to manual.
    if snapshot.verdict == "ambiguous":
        return DirectionInference(
            source_lang=None,
            signal="snapshot",
            reason=snapshot.detail,
        )

    snap_side = snapshot.side
    git_side = git_ts.side if git_ts.verdict == "ok" else None

    # Both signals usable and definite — cross-check.
    if snap_side is not None and git_side is not None and snap_side != git_side:
        return DirectionInference(
            source_lang=None,
            signal="snapshot+git-timestamp",
            reason=(
                f"snapshot points to {snap_side!r} ({snapshot.detail}) "
                f"but git timestamps point to {git_side!r} ({git_ts.detail})"
            ),
        )

    if snap_side is not None:
        return DirectionInference(
            source_lang=snap_side,
            signal="snapshot",
            reason=snapshot.detail,
        )

    if git_side is not None:
        return DirectionInference(
            source_lang=git_side,
            signal="git-timestamp",
            reason=git_ts.detail,
        )

    # Neither signal yielded a definite verdict. Combine the reasons so
    # the user knows why both paths failed.
    parts = []
    if snapshot.detail:
        parts.append(f"snapshot: {snapshot.detail}")
    if git_ts.detail:
        parts.append(f"git: {git_ts.detail}")
    reason = "; ".join(parts) if parts else "no snapshot evidence and no git history available"
    return DirectionInference(source_lang=None, signal="none", reason=reason)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass
class _SignalResult:
    """Outcome of one signal source.

    ``verdict``:
      * ``"ok"`` — ``side`` carries ``"de"`` / ``"en"``.
      * ``"ambiguous"`` — the signal exists but is internally inconsistent
        (e.g. snapshot rows disagree). ``side`` is ``None``.
      * ``"none"`` — the signal produced no answer (no rows, no git,
        equal timestamps). ``side`` is ``None``.
    """

    verdict: str
    side: str | None
    detail: str


def _infer_from_snapshot(
    de_path: Path,
    en_path: Path,
    snapshot_cache: SyncSnapshotCache | None,
) -> _SignalResult:
    if snapshot_cache is None:
        return _SignalResult(verdict="none", side=None, detail="no snapshot cache")

    rows = [
        (slide_id, role, de_hash, en_hash)
        for de_p, en_p, slide_id, role, de_hash, en_hash, _direction, _at in (
            snapshot_cache.iter_entries()
        )
        if de_p == str(de_path) and en_p == str(en_path)
    ]
    if not rows:
        return _SignalResult(verdict="none", side=None, detail="no snapshot rows for this pair")

    try:
        de_cells = parse_cells(de_path.read_text(encoding="utf-8"))
        en_cells = parse_cells(en_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return _SignalResult(
            verdict="none", side=None, detail=f"could not read source files: {exc}"
        )

    de_index = _index_first(de_cells, "de")
    en_index = _index_first(en_cells, "en")

    de_drift = 0
    en_drift = 0
    both_drift = 0
    matched = 0

    for slide_id, role, snap_de_hash, snap_en_hash in rows:
        de_cell = de_index.get((slide_id, role))
        en_cell = en_index.get((slide_id, role))
        if de_cell is None or en_cell is None:
            continue
        matched += 1
        cur_de_hash = cell_content_hash(de_cell.content)
        cur_en_hash = cell_content_hash(en_cell.content)
        de_changed = cur_de_hash != snap_de_hash
        en_changed = cur_en_hash != snap_en_hash
        if de_changed and en_changed:
            both_drift += 1
        elif de_changed:
            de_drift += 1
        elif en_changed:
            en_drift += 1

    if matched == 0:
        return _SignalResult(
            verdict="none",
            side=None,
            detail="snapshot rows reference slide_ids no longer in the files",
        )

    # Both sides drifted on the same cell anywhere → 3-way territory.
    # That's ITEM 2 of the v2 follow-up list, not direction inference.
    if both_drift > 0:
        return _SignalResult(
            verdict="ambiguous",
            side=None,
            detail=f"{both_drift} snapshot row(s) show both sides drifted (3-way merge case)",
        )

    if de_drift > 0 and en_drift == 0:
        return _SignalResult(
            verdict="ok",
            side="de",
            detail=f"DE drifted in {de_drift} of {matched} snapshot row(s); EN unchanged",
        )
    if en_drift > 0 and de_drift == 0:
        return _SignalResult(
            verdict="ok",
            side="en",
            detail=f"EN drifted in {en_drift} of {matched} snapshot row(s); DE unchanged",
        )
    if de_drift > 0 and en_drift > 0:
        return _SignalResult(
            verdict="ambiguous",
            side=None,
            detail=f"snapshot rows disagree: DE drifted in {de_drift}, EN drifted in {en_drift}",
        )

    # de_drift == 0 and en_drift == 0 and both_drift == 0: everything
    # matches the last snapshot — no edits since.
    return _SignalResult(
        verdict="none",
        side=None,
        detail=f"all {matched} snapshot row(s) match current file state",
    )


def _infer_from_git_timestamp(de_path: Path, en_path: Path) -> _SignalResult:
    de_ct = _git_last_commit_timestamp(de_path)
    en_ct = _git_last_commit_timestamp(en_path)
    if de_ct is None and en_ct is None:
        return _SignalResult(
            verdict="none",
            side=None,
            detail="neither file is tracked in a git repo",
        )
    if de_ct is None:
        return _SignalResult(
            verdict="none",
            side=None,
            detail=f"{de_path.name} is not tracked in git",
        )
    if en_ct is None:
        return _SignalResult(
            verdict="none",
            side=None,
            detail=f"{en_path.name} is not tracked in git",
        )

    if de_ct > en_ct:
        return _SignalResult(
            verdict="ok",
            side="de",
            detail=f"DE last commit @{de_ct} is newer than EN @{en_ct}",
        )
    if en_ct > de_ct:
        return _SignalResult(
            verdict="ok",
            side="en",
            detail=f"EN last commit @{en_ct} is newer than DE @{de_ct}",
        )
    return _SignalResult(
        verdict="none",
        side=None,
        detail=f"DE and EN last-commit timestamps are equal (@{de_ct})",
    )


def _git_last_commit_timestamp(path: Path) -> int | None:
    """Return committer timestamp of the most recent commit touching ``path``.

    Returns ``None`` when git is unavailable, the directory is not in a
    git repo, or the file is untracked.
    """
    try:
        completed = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", path.name],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _index_first(cells: list[Cell], expected_lang: str) -> dict[tuple[str, str], Cell]:
    """Index sync-relevant cells by ``(slide_id, role)``; first occurrence wins.

    Mirrors the role/lang filter from :func:`clm.slides.sync._index_cells`
    but keeps a single cell per key — direction inference is a coarse
    structural signal, so the first cell in source order is enough.
    """
    out: dict[tuple[str, str], Cell] = {}
    for cell in cells:
        if cell.metadata.is_j2:
            continue
        if cell.metadata.cell_type != "markdown":
            continue
        if cell.metadata.lang != expected_lang:
            continue
        sid = cell.metadata.slide_id
        if not sid:
            continue
        role: str | None = None
        for tag in cell.metadata.tags:
            if tag in _ROLE_TAGS:
                role = tag
                break
        if role is None:
            continue
        out.setdefault((sid, role), cell)
    return out
