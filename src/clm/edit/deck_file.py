"""The pure library core of the deck editor.

A :class:`DeckFile` is a thin wrapper around the lossless
:mod:`clm.slides.raw_cells` primitives (``split_cells`` / ``reconstruct``)
plus index-keyed edit operations. It is modeled on
:class:`clm.slides.sync_writeback.FileState` — which already solves the
trailing-blank and terminal-newline edge cases — but is keyed by **cell
index** (what a web editor works in) rather than by ``(slide_id, role)``.

Round-trip guarantee: a :class:`DeckFile` loaded and immediately flushed
without edits reproduces the original file bytes exactly. Editing one
cell re-encodes only that cell; every other cell's verbatim ``lines`` are
untouched, so a single-cell edit changes only that cell's bytes.

This module has **no web dependencies** and is fully unit-testable on its
own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from clm.notebooks.slide_parser import (
    CellMetadata,
    comment_token_for_path,
    parse_cell_header,
)
from clm.slides.raw_cells import RawCell, reconstruct, split_cells


class DeckFileError(Exception):
    """A cell index is out of range, or the file cannot be parsed/written."""


@dataclass(frozen=True)
class CellInfo:
    """A read-only projection of one cell for display.

    The full verbatim text is available via :attr:`header` and :attr:`body`
    so the UI can round-trip an edit without re-parsing. :attr:`kind`,
    :attr:`lang`, :attr:`tags`, and :attr:`is_j2` are convenience views
    parsed from the header for rendering chips and previews.
    """

    index: int
    header: str
    body: str
    metadata: CellMetadata

    @property
    def kind(self) -> str:
        """Short label for the cell's header: ``j2``, ``code``, or ``markdown``."""
        if self.metadata.is_j2:
            return "j2"
        return self.metadata.cell_type  # "code" or "markdown"

    @property
    def lang(self) -> str | None:
        return self.metadata.lang

    @property
    def tags(self) -> list[str]:
        return self.metadata.tags

    @property
    def is_j2(self) -> bool:
        return self.metadata.is_j2

    @property
    def is_slide_start(self) -> bool:
        return self.metadata.is_slide_start

    @property
    def is_narrative(self) -> bool:
        return self.metadata.is_narrative


class DeckFile:
    """One slide file, editable at cell granularity.

    Usage::

        deck = DeckFile.load(path)
        deck.replace_cell(2, new_body)      # edit cell 2's body
        deck.delete_cell(0)                 # remove cell 0
        deck.flush()                         # write back to disk

    All index arguments are 0-based positions in :attr:`cells`. Operations
    re-parse from the constructor's read; callers editing the same file
    concurrently should construct a fresh ``DeckFile`` per request (the web
    layer does this) so they never act on stale cell positions.
    """

    def __init__(
        self, path: Path, preamble: str, cells: list[RawCell], ends_with_newline: bool = True
    ):
        self.path = path
        self.preamble = preamble
        self.cells = cells
        self.ends_with_newline = ends_with_newline
        self._comment_token: str = comment_token_for_path(path)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> DeckFile:
        """Read ``path`` and split it into preamble + cells losslessly."""
        text = path.read_text(encoding="utf-8")
        comment_token = comment_token_for_path(path)
        preamble, cells = split_cells(text, comment_token)
        return cls(
            path=path,
            preamble=preamble,
            cells=cells,
            ends_with_newline=text.endswith("\n"),
        )

    # ------------------------------------------------------------------
    # Rendering / writing
    # ------------------------------------------------------------------

    def render(self) -> str:
        """Return the exact text :meth:`flush` would write, without touching disk.

        Restores the original terminal newline so a delete of the last cell
        never emits a "No newline at end of file" diff — mirrors
        :meth:`clm.slides.sync_writeback.FileState.render`.
        """
        text = reconstruct(self.preamble, self.cells)
        if self.ends_with_newline and not text.endswith("\n"):
            text += "\n"
        return text

    def flush(self) -> None:
        """Overwrite :attr:`path` with :meth:`render` (LF line endings)."""
        self.path.write_text(self.render(), encoding="utf-8", newline="\n")

    # ------------------------------------------------------------------
    # Read views
    # ------------------------------------------------------------------

    def cell_infos(self) -> list[CellInfo]:
        """Return a read-only projection of every cell for display."""
        return [
            CellInfo(
                index=i,
                header=cell.header,
                body=cell.body,
                metadata=cell.metadata,
            )
            for i, cell in enumerate(self.cells)
        ]

    def cell_count(self) -> int:
        return len(self.cells)

    # ------------------------------------------------------------------
    # Edit operations (index-keyed)
    # ------------------------------------------------------------------

    def replace_cell_body(self, index: int, new_body: str) -> None:
        """Rewrite cell ``index``'s body in place, preserving its header and
        trailing-blank-line padding.

        ``new_body`` uses real (un-prefixed) line content — the editor
        presents and accepts the *body* of a percent-format cell, not the
        comment-prefixed source. The cell's original trailing blank lines
        are preserved so an edit to a slide's bullets doesn't collapse the
        gap before the next cell. Mirrors
        :meth:`clm.slides.sync_writeback.FileState._rewrite_cell_body`.
        """
        cell = self._require(index)
        trailing_blanks = _count_trailing_blanks(cell)
        new_lines = new_body.split("\n")
        # Drop trailing empties from the user input so we control the
        # padding explicitly (a textarea often appends a trailing newline).
        while new_lines and new_lines[-1] == "":
            new_lines.pop()
        new_lines.extend([""] * trailing_blanks)
        cell.lines = [cell.lines[0], *new_lines]

    def update_cell_header(self, index: int, new_header: str) -> None:
        """Replace cell ``index``'s header line verbatim and re-parse metadata.

        The header is written exactly as given (the caller is responsible
        for keeping it well-formed); the cell's metadata is re-derived so
        downstream chip rendering stays correct.
        """
        cell = self._require(index)
        cell.lines[0] = new_header
        cell.metadata = parse_cell_header(new_header, self._comment_token)

    def delete_cell(self, index: int) -> None:
        """Remove cell ``index`` and all of its lines.

        The cell owns its boundary line, body, and trailing blanks, so
        dropping it from the list leaves surrounding cells' bytes
        untouched. The terminal newline (if any) is restored by
        :meth:`render`.
        """
        self._require(index)
        del self.cells[index]

    def insert_cell(self, index: int, header: str, body: str) -> int:
        """Insert a new cell at ``index``; returns the new cell's index.

        ``index`` may equal :meth:`cell_count` to append. The new cell is
        granted the deck's separator gap (the most common trailing-blank
        count among existing cells) so it visually matches its neighbours;
        when it lands last, it carries no trailing blank and the terminal
        newline is restored on :meth:`flush`. Mirrors
        :meth:`clm.slides.sync_writeback.FileState._place_inserted`.
        """
        if index < 0 or index > len(self.cells):
            raise DeckFileError(f"cell index {index} out of range (0..{len(self.cells)})")
        sep = self._separator_blanks()
        new_cell = _build_cell(header, body, self._comment_token)
        self.cells.insert(index, new_cell)
        # If the inserted cell is now last, it owns the terminal position:
        # no trailing blank (render() restores the newline). The cell it
        # displaced from the end gets normalised to the deck separator.
        if self.cells[-1] is new_cell:
            _set_trailing_blanks(new_cell, 0)
        else:
            _set_trailing_blanks(new_cell, sep)
        return index

    def move_cell(self, index: int, direction: int) -> int:
        """Move cell ``index`` by ``direction`` (+1 down, −1 up).

        Returns the cell's new index. No-op (returns the original index)
        at the deck boundaries. The moved cell's trailing blanks are
        re-granted to match its new neighbourhood so a slide pulled to the
        end doesn't carry a stray blank line.
        """
        cell = self._require(index)
        new_index = index + (1 if direction > 0 else -1)
        if new_index < 0 or new_index >= len(self.cells):
            return index
        del self.cells[index]
        self.cells.insert(new_index, cell)
        sep = self._separator_blanks()
        # Re-grant separator padding for the moved cell unless it is now
        # last (terminal position — no trailing blank, newline restored on
        # flush).
        if self.cells[-1] is cell:
            _set_trailing_blanks(cell, 0)
        else:
            _set_trailing_blanks(cell, sep)
        return new_index

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require(self, index: int) -> RawCell:
        if index < 0 or index >= len(self.cells):
            raise DeckFileError(f"cell index {index} out of range (0..{len(self.cells) - 1})")
        return self.cells[index]

    def _separator_blanks(self) -> int:
        """The deck's inter-cell blank-line gap (0 = tight, 1 = blank-separated).

        The most common trailing-blank count among non-last cells (j2
        header cells excluded, since they sit tight against their sibling
        macro call). Mirrors
        :meth:`clm.slides.sync_writeback.FileState.separator_blanks`.
        """
        if len(self.cells) < 2:
            return 0
        from collections import Counter

        counts = [_count_trailing_blanks(c) for c in self.cells[:-1] if not c.metadata.is_j2]
        if not counts:
            counts = [_count_trailing_blanks(self.cells[0])]
        return Counter(counts).most_common(1)[0][0]


# ----------------------------------------------------------------------
# Module-level helpers (mirror the private ones in sync_writeback)
# ----------------------------------------------------------------------


def _count_trailing_blanks(cell: RawCell) -> int:
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


def _build_cell(header: str, body: str, comment_token: str) -> RawCell:
    """Build a fresh :class:`RawCell` from a header and an un-prefixed body.

    ``body`` is stripped of leading/trailing blank lines (the deck's
    separator is granted separately by the insert/move primitives). The
    comment token is used only to classify the header (j2 vs. code vs.
    markdown).
    """
    body_lines = body.split("\n")
    while body_lines and body_lines[0] == "":
        body_lines.pop(0)
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
    return RawCell(
        lines=[header, *body_lines],
        line_number=0,
        metadata=parse_cell_header(header, comment_token),
    )
