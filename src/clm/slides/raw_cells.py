"""Lossless preamble + cell primitives for slide-file rewriters.

Several slide tools (``assign_ids``, ``normalizer``, ``split``) need to
walk a percent-format ``.py`` file at cell granularity *and* reconstruct
the file byte-identically afterwards. The standard parser in
:mod:`clm.notebooks.slide_parser` strips whitespace and joins content
into a single string, which is lossy.

This module exposes a raw representation: each cell keeps its original
line list verbatim (header line at ``lines[0]``, body lines after). The
preamble — everything before the first cell — is preserved as a string.
``split_cells`` is the inverse of ``reconstruct`` for any input that
contains at least one cell boundary.

Round-trip invariant (used by Phase 5 split/unify and elsewhere)::

    text == reconstruct(*split_cells(text))     # for any cell-shaped text
"""

from __future__ import annotations

from dataclasses import dataclass

from clm.notebooks.slide_parser import CellMetadata, parse_cell_header


def is_cell_boundary(line: str) -> bool:
    """Return True iff ``line`` opens a new percent-format cell."""
    return line.startswith("# %%") or line.startswith("# j2 ") or line.startswith("# {{ ")


@dataclass
class RawCell:
    """A cell that preserves its original lines verbatim."""

    lines: list[str]
    line_number: int  # 1-based; line of the header
    metadata: CellMetadata

    @property
    def header(self) -> str:
        return self.lines[0]

    @header.setter
    def header(self, value: str) -> None:
        self.lines[0] = value

    @property
    def body(self) -> str:
        return "\n".join(self.lines[1:])


def split_cells(text: str) -> tuple[str, list[RawCell]]:
    """Split ``text`` into ``(preamble, cells)`` losslessly.

    ``preamble`` contains every line before the first cell boundary. Each
    ``RawCell`` keeps the boundary line and all following lines until the
    next boundary (or end of file) in ``lines``.
    """
    lines = text.split("\n")
    cells: list[RawCell] = []
    preamble: list[str] = []
    current: list[str] = []
    current_line = 0
    in_cell = False
    for i, line in enumerate(lines):
        if is_cell_boundary(line):
            if in_cell:
                cells.append(
                    RawCell(
                        lines=current,
                        line_number=current_line,
                        metadata=parse_cell_header(current[0]),
                    )
                )
            current = [line]
            current_line = i + 1
            in_cell = True
        else:
            if in_cell:
                current.append(line)
            else:
                preamble.append(line)
    if in_cell:
        cells.append(
            RawCell(
                lines=current,
                line_number=current_line,
                metadata=parse_cell_header(current[0]),
            )
        )
    return ("\n".join(preamble), cells)


def reconstruct(preamble: str, cells: list[RawCell]) -> str:
    """Inverse of :func:`split_cells` — assemble preamble + cells back to text."""
    parts: list[str] = []
    if preamble:
        parts.append(preamble)
    for cell in cells:
        parts.append("\n".join(cell.lines))
    return "\n".join(parts)
