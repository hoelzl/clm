"""Shared cell-preserving write infrastructure for sync apply paths.

Used by:

- :mod:`clm.slides.sync_walker` — interactive ``--interactive`` walker
- :mod:`clm.slides.sync_trivial` — ``--apply --trivial`` auto-applier
- :mod:`clm.slides.sync_apply` — Issue #166 authoring apply engine
  (drives ``find_cell`` / ``replace_cell_body`` / ``delete_cell``, keyed by
  ``(slide_id, role)`` rather than line number)

These paths must keep cell headers and trailing-blank padding verbatim so
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

from clm.notebooks.slide_parser import CellMetadata
from clm.slides.raw_cells import RawCell, reconstruct, split_cells

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncSnapshotCache
    from clm.slides.sync import PairOutcome, SyncResult


__all__ = [
    "FileState",
    "cell_content_hash",
    "record_snapshot",
    "role_of",
    "target_path_for_outcome",
]

# Tags that identify a sync-relevant cell's role. Duplicated from
# ``clm.slides.sync`` / ``clm.slides.sync_plan`` to keep this low-level
# write module free of an import cycle (sync_plan imports this module).
_SYNC_ROLE_TAGS = {"slide", "subslide", "voiceover", "notes"}


def role_of(metadata: CellMetadata) -> str | None:
    """Return the sync role of a cell from its metadata, or ``None``.

    Public so :mod:`clm.slides.sync_apply` reuses the exact same predicate
    instead of keeping its own copy.
    """
    if metadata.is_j2:
        return None
    if metadata.cell_type != "markdown":
        return None
    for tag in metadata.tags:
        if tag in _SYNC_ROLE_TAGS:
            return tag
    return None


def _cell_matches(cell: RawCell, slide_id: str, role: str) -> bool:
    """Whether ``cell`` carries ``slide_id`` in sync ``role``."""
    return cell.metadata.slide_id == slide_id and role_of(cell.metadata) == role


def _trailing_blanks(cell: RawCell) -> int:
    """Count the blank body lines at the end of ``cell``."""
    n = 0
    for line in reversed(cell.lines[1:]):
        if line == "":
            n += 1
        else:
            break
    return n


def _set_trailing_blanks(cell: RawCell, n: int) -> None:
    """Force ``cell`` to end with exactly ``n`` blank body lines."""
    body = cell.lines[1:]
    while body and body[-1] == "":
        body.pop()
    body.extend([""] * n)
    cell.lines = [cell.lines[0], *body]


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
    ends_with_newline: bool = True

    @classmethod
    def load(cls, path: Path) -> FileState:
        text = path.read_text(encoding="utf-8")
        preamble, cells = split_cells(text)
        return cls(
            path=path,
            preamble=preamble,
            cells=cells,
            ends_with_newline=text.endswith("\n"),
        )

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

    def find_cell(self, slide_id: str, role: str) -> RawCell | None:
        """Return the cell carrying ``slide_id`` in ``role``, or ``None``.

        Used by the Issue #166 apply engine, whose proposals are keyed by
        ``(slide_id, role)`` rather than by line number — so deletes and
        edits stay correct even as earlier operations shift line numbers.
        """
        for cell in self.cells:
            if _cell_matches(cell, slide_id, role):
                return cell
        return None

    def replace_cell_body(self, slide_id: str, role: str, new_text: str) -> bool:
        """Rewrite the body of the ``(slide_id, role)`` cell in place.

        Returns ``False`` when no such cell exists. Header and trailing
        blank-line padding stay verbatim (same contract as
        :meth:`replace_body`).
        """
        cell = self.find_cell(slide_id, role)
        if cell is None:
            return False
        self._rewrite_cell_body(cell, new_text)
        self.dirty = True
        return True

    def delete_cell(self, slide_id: str, role: str) -> bool:
        """Remove the ``(slide_id, role)`` cell, lines and all.

        Returns ``False`` when no such cell exists. The cell owns its
        boundary line, body, and trailing blanks, so dropping it from the
        list leaves the surrounding cells' bytes untouched.
        """
        for i, cell in enumerate(self.cells):
            if _cell_matches(cell, slide_id, role):
                del self.cells[i]
                self.dirty = True
                return True
        return False

    def separator_blanks(self) -> int:
        """The deck's inter-cell blank-line gap (0 = tight, 1 = blank-separated).

        Read from the first cell, which is always non-last when there are two
        or more cells, so its trailing-blank count reflects the *separator*
        convention rather than the terminal-newline artifact the last cell
        carries. Compute it **before** a structural mutation. Non-uniform
        decks fall back to the first gap (documented limitation).
        """
        if len(self.cells) < 2:
            return 0
        return _trailing_blanks(self.cells[0])

    def insert_after(self, slide_id: str, role: str, new_cell: RawCell) -> bool:
        """Insert ``new_cell`` immediately after the ``(slide_id, role)`` cell.

        Returns ``False`` when the anchor cell is absent. Used by the Issue
        #166 add path to place a translated counterpart next to its neighbour.
        """
        sep = self.separator_blanks()
        original_last = self.cells[-1] if self.cells else None
        for i, cell in enumerate(self.cells):
            if _cell_matches(cell, slide_id, role):
                self.cells.insert(i + 1, new_cell)
                self.dirty = True
                self._place_inserted(new_cell, original_last, sep)
                return True
        return False

    def insert_before_first_sync_cell(self, new_cell: RawCell) -> None:
        """Insert ``new_cell`` ahead of the first sync cell (after the head).

        The anchor for an add with no preceding shared cell — it becomes the
        deck's first slide, sitting after any j2 header / intro cells but
        before the existing slides/narrative. Appends only when the deck has no
        sync cell at all.
        """
        sep = self.separator_blanks()
        original_last = self.cells[-1] if self.cells else None
        for i, cell in enumerate(self.cells):
            if role_of(cell.metadata) is not None:
                self.cells.insert(i, new_cell)
                self.dirty = True
                self._place_inserted(new_cell, original_last, sep)
                return
        self.cells.append(new_cell)
        self.dirty = True
        self._place_inserted(new_cell, original_last, sep)

    def _place_inserted(self, new_cell: RawCell, original_last: RawCell | None, sep: int) -> None:
        """Give ``new_cell`` the deck's separator (or none, if it is now last).

        When ``new_cell`` lands last, it carries no explicit trailing blank —
        :meth:`flush` restores the terminal newline — and the cell it displaced
        from the end is normalised to the separator (its terminal artifact is
        not a real gap).
        """
        if self.cells and self.cells[-1] is new_cell:
            _set_trailing_blanks(new_cell, 0)
            if original_last is not None:
                self.normalize_displaced_last(original_last, sep)
        else:
            _set_trailing_blanks(new_cell, sep)

    def normalize_displaced_last(self, original_last: RawCell, sep: int) -> None:
        """Normalise the trailing blanks of a cell pushed off the end.

        ``split_cells`` parks the file's final newline as a trailing ``""`` on
        the last cell. When a move/insert pushes that cell off the end, that
        ``""`` is the terminal artifact, not a real separator — reset its
        trailing blanks to the deck ``sep`` (so a tight deck loses it and a
        blank-separated deck keeps one). :meth:`flush` restores the terminal
        newline on whatever ends up last. No-op when still last.
        """
        if not self.ends_with_newline or not self.cells:
            return
        if self.cells[-1] is original_last:
            return
        _set_trailing_blanks(original_last, sep)

    def flush(self) -> None:
        if not self.dirty:
            return
        text = reconstruct(self.preamble, self.cells)
        # Deleting the file's last cell drops the trailing-newline element
        # that ``split_cells`` parked on it; restore the original terminal
        # newline so a remove never emits a "No newline at end of file" diff.
        if self.ends_with_newline and not text.endswith("\n"):
            text += "\n"
        self.path.write_text(text, encoding="utf-8", newline="\n")
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
