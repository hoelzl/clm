"""Locate the workshop boundary within a slide cell list.

A notebook's workshop section runs from the first markdown cell tagged
``workshop`` to end-of-notebook. The ``partial`` output kind and the
validator both depend on this boundary. Trainers may later add an
``end-workshop`` tag if content after the workshop is needed; until then,
workshop = trailing suffix.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class _CellLike(Protocol):
    @property
    def cell_type(self) -> str: ...

    @property
    def tags(self) -> Sequence[str]: ...


def find_workshop_start_index(cells: Sequence[_CellLike]) -> int | None:
    """Return the index of the first markdown cell tagged ``workshop``.

    Returns ``None`` if no workshop heading is present.
    """
    for i, cell in enumerate(cells):
        if cell.cell_type == "markdown" and "workshop" in cell.tags:
            return i
    return None
