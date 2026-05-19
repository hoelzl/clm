"""Locate workshop boundaries within a slide cell list.

A notebook may contain zero or more workshop sections. Each workshop starts
at one of:

* a markdown cell tagged ``workshop``, or
* a slide/subslide markdown cell whose ``slide_id`` starts with ``workshop-``
  (the deck-level convention used when the announcement slide is tagged as a
  regular slide but its identity carries the workshop scope forward — e.g.
  ``slide_id="workshop-persona-switcher"`` followed by ``task-*`` slides).

It ends — exclusively — at the first of:

* the next markdown cell tagged ``end-workshop``,
* the next workshop opener (a new workshop begins), or
* end-of-notebook.

The cell carrying ``end-workshop`` is therefore *outside* the workshop:
trainers add the tag to the heading that starts the next non-workshop
section. A workshop without a closing ``end-workshop`` extends to EOF, which
preserves the legacy single-workshop behaviour.

Both opener forms and ``end-workshop`` are recognized only on markdown cells;
the same tags or slide_ids on a code cell are ignored for boundary detection.
The slide_id-prefix opener additionally requires a ``slide`` or ``subslide``
tag so that voiceover / notes cells that share the announcement slide's id
do not fragment the range.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class _CellLike(Protocol):
    @property
    def cell_type(self) -> str: ...

    @property
    def tags(self) -> Sequence[str]: ...

    @property
    def slide_id(self) -> str | None: ...


def _is_workshop_opener(cell: _CellLike) -> bool:
    """Detect either the ``workshop`` tag or a ``workshop-…`` slide_id opener.

    The slide_id form only counts on slide-start cells (``slide`` /
    ``subslide``) so that voiceover or notes cells inheriting the same
    slide_id from the announcement slide do not re-trigger a new range.
    """
    if cell.cell_type != "markdown":
        return False
    tags = cell.tags
    if "workshop" in tags:
        return True
    slide_id = cell.slide_id
    if slide_id is None or not slide_id.startswith("workshop-"):
        return False
    return "slide" in tags or "subslide" in tags


def find_workshop_ranges(cells: Sequence[_CellLike]) -> list[tuple[int, int]]:
    """Return ``[(start_inclusive, end_exclusive), ...]`` for each workshop.

    Cells in the half-open interval ``[start, end)`` belong to one workshop.
    The list is empty when the notebook contains no workshop opener (neither
    a ``workshop`` tag nor a slide-start cell with a ``workshop-…`` slide_id).
    """
    ranges: list[tuple[int, int]] = []
    open_start: int | None = None
    for i, cell in enumerate(cells):
        if cell.cell_type != "markdown":
            continue
        if _is_workshop_opener(cell):
            if open_start is not None:
                ranges.append((open_start, i))
            open_start = i
        elif "end-workshop" in cell.tags and open_start is not None:
            ranges.append((open_start, i))
            open_start = None
    if open_start is not None:
        ranges.append((open_start, len(cells)))
    return ranges


def is_in_workshop(idx: int, ranges: Sequence[tuple[int, int]]) -> bool:
    """Return whether cell ``idx`` falls inside any workshop range."""
    return any(start <= idx < end for start, end in ranges)


def find_workshop_start_index(cells: Sequence[_CellLike]) -> int | None:
    """Return the index of the first workshop opener.

    A workshop opener is either a markdown cell tagged ``workshop`` or a
    slide-start markdown cell whose ``slide_id`` starts with ``workshop-``.
    Returns ``None`` if no opener is present. Retained as a convenience for
    callers that only care about the first workshop's start line (e.g.
    validator diagnostics).
    """
    ranges = find_workshop_ranges(cells)
    return ranges[0][0] if ranges else None
