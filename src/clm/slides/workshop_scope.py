"""Locate workshop boundaries within a slide cell list.

A notebook may contain zero or more workshop sections. Each workshop starts
at a markdown cell tagged ``workshop`` and ends — exclusively — at the first
of:

* the next markdown cell tagged ``end-workshop``,
* the next markdown cell tagged ``workshop`` (a new workshop begins), or
* end-of-notebook.

The cell carrying ``end-workshop`` is therefore *outside* the workshop:
trainers add the tag to the heading that starts the next non-workshop
section. A workshop without a closing ``end-workshop`` extends to EOF, which
preserves the legacy single-workshop behaviour.

Both ``workshop`` and ``end-workshop`` are recognized only on markdown cells;
the same tags on a code cell are ignored for boundary detection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class _CellLike(Protocol):
    @property
    def cell_type(self) -> str: ...

    @property
    def tags(self) -> Sequence[str]: ...


def find_workshop_ranges(cells: Sequence[_CellLike]) -> list[tuple[int, int]]:
    """Return ``[(start_inclusive, end_exclusive), ...]`` for each workshop.

    Cells in the half-open interval ``[start, end)`` belong to one workshop.
    The list is empty when the notebook contains no ``workshop`` heading.
    """
    ranges: list[tuple[int, int]] = []
    open_start: int | None = None
    for i, cell in enumerate(cells):
        if cell.cell_type != "markdown":
            continue
        tags = cell.tags
        if "workshop" in tags:
            if open_start is not None:
                ranges.append((open_start, i))
            open_start = i
        elif "end-workshop" in tags and open_start is not None:
            ranges.append((open_start, i))
            open_start = None
    if open_start is not None:
        ranges.append((open_start, len(cells)))
    return ranges


def is_in_workshop(idx: int, ranges: Sequence[tuple[int, int]]) -> bool:
    """Return whether cell ``idx`` falls inside any workshop range."""
    return any(start <= idx < end for start, end in ranges)


def find_workshop_start_index(cells: Sequence[_CellLike]) -> int | None:
    """Return the index of the first markdown cell tagged ``workshop``.

    Returns ``None`` if no workshop heading is present. Retained as a
    convenience for callers that only care about the first workshop's start
    line (e.g. validator diagnostics).
    """
    ranges = find_workshop_ranges(cells)
    return ranges[0][0] if ranges else None
