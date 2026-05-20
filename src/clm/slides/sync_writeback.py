"""Shared cell-preserving write infrastructure for sync apply paths.

Used by:

- :mod:`clm.slides.sync_walker` — interactive ``--interactive`` walker
- :mod:`clm.slides.sync_trivial` — ``--apply --trivial`` auto-applier

Both paths must keep cell headers and trailing-blank padding verbatim so
the surrounding bytes never shift; the v1 / Phase 5 round-trip
invariant is what makes `clm slides split` / `unify` work, and the
sync write paths inherit that contract. All three primitives here are
the same primitives the v2 walker shipped with — extracted so a
``--apply --trivial`` pass can share them rather than duplicate.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clm.slides.raw_cells import RawCell, reconstruct, split_cells

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncSnapshotCache
    from clm.slides.sync import PairOutcome, SyncResult


__all__ = [
    "FileState",
    "cell_content_hash",
    "record_snapshot",
    "target_path_for_outcome",
]


def target_path_for_outcome(outcome: PairOutcome, result: SyncResult) -> Path:
    """Return the file path the outcome would write to."""
    if outcome.direction == "de->en":
        return result.en_path
    return result.de_path


def cell_content_hash(text: str) -> str:
    """Hash ``text`` the way :func:`clm.slides.sync._hash` does.

    Both v1's ``_hash`` and ``Cell.content`` operate on the body as the
    parser produces it: body lines joined by ``\\n`` then ``.strip()``-ed.
    Apply-time writes carry whatever the LLM proposed (or the user
    edited), which may have extra leading/trailing whitespace — strip
    the same way before hashing so re-runs find a matching cache row.
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def record_snapshot(
    snapshot_cache: SyncSnapshotCache | None,
    *,
    result: SyncResult,
    outcome: PairOutcome,
    new_target_text: str,
) -> None:
    """Persist the post-write state as the new last-known-synced row.

    The source side's hash was already computed by :mod:`clm.slides.sync`
    and stashed on the outcome. The target side gets a fresh hash from
    the text we just wrote (normalised by :func:`cell_content_hash`).
    No-op when ``snapshot_cache`` is ``None`` or the outcome carries no
    proposal.
    """
    if snapshot_cache is None:
        return
    if outcome.proposal is None:
        return

    target_hash = cell_content_hash(new_target_text)
    if outcome.direction == "de->en":
        de_hash = outcome.de_hash
        en_hash = target_hash
    else:
        de_hash = target_hash
        en_hash = outcome.en_hash

    snapshot_cache.put(
        de_path=str(result.de_path),
        en_path=str(result.en_path),
        slide_id=outcome.slide_id,
        role=outcome.role,
        de_hash=de_hash,
        en_hash=en_hash,
        direction=outcome.direction,
    )


@dataclass
class FileState:
    """In-memory representation of one slide file, ready for batched writes.

    Loaded once per path; ``replace_body`` mutates the matching cell
    in place; ``flush`` writes back via :func:`raw_cells.reconstruct`
    iff anything changed. Multiple writes against the same path share
    one ``FileState`` so they round-trip through a single read+write.
    """

    path: Path
    preamble: str
    cells: list[RawCell]
    dirty: bool = False

    @classmethod
    def load(cls, path: Path) -> FileState:
        text = path.read_text(encoding="utf-8")
        preamble, cells = split_cells(text)
        return cls(path=path, preamble=preamble, cells=cells)

    def replace_body(self, outcome: PairOutcome, new_text: str) -> None:
        """Replace the body of the target cell named by ``outcome``.

        Target line number is ``outcome.en_line`` when direction is
        ``de->en`` and ``outcome.de_line`` otherwise. The header line
        and the trailing blank-line padding stay verbatim so the
        surrounding bytes don't shift.
        """
        target_line = outcome.en_line if outcome.direction == "de->en" else outcome.de_line
        for cell in self.cells:
            if cell.line_number == target_line:
                self._rewrite_cell_body(cell, new_text)
                self.dirty = True
                return
        raise LookupError(
            f"no cell at line {target_line} in {self.path}; "
            "file changed since the sync pass parsed it?"
        )

    def flush(self) -> None:
        if not self.dirty:
            return
        text = reconstruct(self.preamble, self.cells)
        self.path.write_text(text, encoding="utf-8")
        self.dirty = False

    @staticmethod
    def _rewrite_cell_body(cell: RawCell, new_text: str) -> None:
        original = cell.lines[1:]
        trailing_blanks = 0
        for line in reversed(original):
            if line == "":
                trailing_blanks += 1
            else:
                break

        new_lines = new_text.split("\n")
        while new_lines and new_lines[-1] == "":
            new_lines.pop()
        new_lines.extend([""] * trailing_blanks)

        cell.lines = [cell.lines[0], *new_lines]
