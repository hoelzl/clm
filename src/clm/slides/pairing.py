"""Structural anchors for slide pairing: DE/EN groups and the title macro.

Shared by :mod:`clm.slides.assign_ids` (Phase 2 — assigning slide_ids
to paired cells) and :mod:`clm.slides.validator` (Phase 3 — verifying
that already-assigned ids honor adjacency and pair-equivalence). The
helpers operate on any cell-like object that exposes ``metadata`` and
``header`` attributes, which covers both the validator's
:class:`clm.notebooks.slide_parser.Cell` and ``assign_ids``'s private
``_Cell`` dataclass.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from clm.notebooks.slide_parser import CellMetadata

# Title-slide anchor: ``# {{ header("DE Title", "EN Title") }}``. The
# macro line itself never carries ``slide_id`` metadata — its presence
# anchors :data:`TITLE_SLIDE_ID` for following narrative cells.
HEADER_MACRO_RE = re.compile(r'\{\{\s*header\s*\(\s*"[^"]*"\s*,\s*"([^"]*)"\s*\)\s*\}\}')

TITLE_SLIDE_ID = "title"


class CellLike(Protocol):
    """Structural protocol: cells produced by the slide parser or by the
    Phase 2 assign-ids splitter both satisfy this shape.
    """

    metadata: CellMetadata
    header: str


def is_title_macro_cell(cell: CellLike) -> bool:
    """Return True iff ``cell`` is the j2 ``header()`` title-slide macro line."""
    if not cell.metadata.is_j2:
        return False
    return bool(HEADER_MACRO_RE.search(cell.header))


def build_slide_groups(cells: Sequence[CellLike]) -> list[tuple[int, ...]]:
    """Group slide/subslide cell indices by source-order DE/EN adjacency.

    Each returned tuple is either ``(idx,)`` for a solo slide cell or
    ``(de_idx, en_idx)`` (in that source order) for an adjacent
    different-language pair. The grouping never spans non-slide cells —
    intervening code, j2, or narrative cells don't split a pair, because
    the algorithm walks the *slide-only* index list. Pairing requires
    that both members carry a ``lang`` attribute and that the two langs
    differ; identical-lang or lang-less neighbours stay solo.
    """
    slide_indices = [i for i, c in enumerate(cells) if c.metadata.is_slide_start]
    groups: list[tuple[int, ...]] = []
    i = 0
    while i < len(slide_indices):
        a = slide_indices[i]
        if i + 1 < len(slide_indices):
            b = slide_indices[i + 1]
            lang_a = cells[a].metadata.lang
            lang_b = cells[b].metadata.lang
            if lang_a and lang_b and lang_a != lang_b:
                groups.append((a, b))
                i += 2
                continue
        groups.append((a,))
        i += 1
    return groups


def build_slide_pairs(cells: Sequence[CellLike]) -> dict[int, int]:
    """Map every slide-cell index to the cell that *drives* its slug.

    EN-derived policy (handover §2.3): when a DE slide cell sits next
    to an EN slide cell in the source order, both cells share the slug
    derived from the EN heading. The returned map gives every slide
    cell the index of the cell to slug from — itself if solo, the EN
    sibling if paired.
    """
    pairs: dict[int, int] = {}
    for group in build_slide_groups(cells):
        if len(group) == 1:
            pairs[group[0]] = group[0]
        else:
            a, b = group
            en_idx = a if cells[a].metadata.lang == "en" else b
            pairs[a] = en_idx
            pairs[b] = en_idx
    return pairs
