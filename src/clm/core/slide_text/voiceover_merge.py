"""Payload-time merge of voiceover companions into slide text (Phase 8 S5).

The in-memory merge the build runs during notebook payload construction
(``ProcessNotebookOperation.payload``), plus the placement machinery it
shares with the authoring-side ``inline``/``extract`` workflows in
``clm.slides.voiceover_tools`` (which imports it from here): slide_id →
cell mapping, ``vo_anchor`` parsing and resolution, group-bounds scoping,
and order-stable insertion.
"""

import re
from collections import defaultdict

from clm.core.slide_text.anchor_primitives import (
    TITLE_MACRO_KIND,
    anchor_candidates,
    split_anchor,
)
from clm.core.slide_text.pairing import TITLE_SLIDE_ID, is_title_macro_cell
from clm.core.slide_text.raw_cells import RawCell, reconstruct, split_cells
from clm.core.slide_text.slide_parser import parse_cell_header

VO_ANCHOR_RE = re.compile(r'\s*vo_anchor="[^"]*"')
_VO_ANCHOR_VALUE_RE = re.compile(r'vo_anchor="([^"]*)"')


def parse_vo_anchor(header: str) -> str | None:
    """Extract the ``vo_anchor`` token from a cell header, if present."""
    m = _VO_ANCHOR_VALUE_RE.search(header)
    return m.group(1) if m else None


def merge_voiceover_text(
    slide_text: str,
    companion_text: str,
    comment_token: str = "#",
) -> tuple[str, list[str]]:
    """Merge companion voiceover cells into slide text in-memory.

    This is used by the build pipeline to merge companion voiceover
    files during notebook processing, without modifying files on disk.

    Args:
        slide_text: Content of the slide file.
        companion_text: Content of the companion voiceover file.

    Returns:
        Tuple of (merged_text, unmatched_for_slide_ids).
        ``unmatched_for_slide_ids`` lists any ``for_slide`` values from
        the companion that could not be matched to a ``slide_id``
        in the slide file.
    """
    preamble, slide_cells = split_cells(slide_text, comment_token)
    _, companion_cells = split_cells(companion_text, comment_token)

    if not companion_cells:
        return slide_text, []

    id_map = build_slide_id_to_cell_map(slide_cells)

    insertions: list[tuple[int, RawCell]] = []
    unmatched_ids: list[str] = []

    for vo_cell in companion_cells:
        for_slide = vo_cell.metadata.for_slide

        # Let plan_insertion decide — it owns the for_slide match, the
        # vo_anchor whole-file fallback, and the title-greeting fallback. A
        # companion with no for_slide is no longer short-circuited here, so a
        # title cell (slide_id="title", no for_slide — what pre-#242 extract
        # wrote) and a hand-authored anchor-only cell can still be placed.
        insert_after, status = plan_insertion(slide_cells, vo_cell, id_map)
        if insert_after is None:
            unmatched_ids.append(for_slide if for_slide else "<no for_slide>")
            continue

        # vo_anchor is an author-only positional hint; never leak it into
        # the merged notebook the build consumes. (for_slide is left as-is
        # to preserve existing build output.)
        vo_cell.lines[0] = VO_ANCHOR_RE.sub("", vo_cell.header)
        vo_cell.metadata = parse_cell_header(vo_cell.lines[0])
        insertions.append((insert_after, vo_cell))

    if not insertions and not unmatched_ids:
        return slide_text, []

    merged_cells = apply_insertions(slide_cells, insertions, [])
    merged_text = reconstruct(preamble, merged_cells)
    return merged_text, unmatched_ids


def build_slide_id_to_cell_map(
    cells: list[RawCell],
) -> dict[str, list[int]]:
    """Map slide_id → list of cell indices (for content cells)."""
    result: dict[str, list[int]] = {}
    for idx, cell in enumerate(cells):
        if cell.metadata.slide_id and not cell.metadata.is_narrative:
            result.setdefault(cell.metadata.slide_id, []).append(idx)
    return result


def _find_insertion_point(
    cells: list[RawCell],
    slide_id: str,
    vo_lang: str | None,
    id_map: dict[str, list[int]],
) -> int | None:
    """Find where to insert a voiceover cell after its owning slide.

    Returns the index in the cells list *after which* the voiceover cell
    should be inserted, or None if the slide_id is not found.
    """
    indices = id_map.get(slide_id)
    if not indices:
        return None

    # Find the last content cell with this slide_id in the matching language
    best = None
    for idx in indices:
        cell = cells[idx]
        if vo_lang is None or cell.metadata.lang is None or cell.metadata.lang == vo_lang:
            best = idx

    if best is None:
        # Fall back to last cell with this slide_id regardless of language
        best = indices[-1]

    # Walk forward from `best` to skip any non-voiceover continuation cells
    # that belong to the same slide group (e.g., code cells after a slide).
    # A mid-group j2 cell (an inline widget macro) is also a continuation: it
    # carries no slide_id and is not a slide-start, so the group only ends at
    # the next slide-start. Breaking at it would strand a group-end fallback
    # before the j2 instead of after it (#247).
    insert_after = best
    for i in range(best + 1, len(cells)):
        cell = cells[i]
        if cell.metadata.is_narrative:
            break
        if cell.metadata.is_slide_start:
            break
        # If this cell has a different slide_id, stop
        if cell.metadata.slide_id and cell.metadata.slide_id != slide_id:
            break
        # If this cell is lang-tagged and doesn't match, stop
        if vo_lang and cell.metadata.lang and cell.metadata.lang != vo_lang:
            break
        insert_after = i

    return insert_after


def _is_title_intent(for_slide: str | None, slide_id: str | None) -> bool:
    """True iff a companion cell narrates the macro-generated title slide.

    Recognized by ``for_slide="title"`` (companions written by a fixed
    ``extract``) or — for backward compatibility with companions extracted
    before the #242 fix, and hand-authored ones — ``slide_id="title"`` with no
    ``for_slide``. The latter is exactly what ``extract`` wrote historically
    (the title voiceover inherits ``slide_id="title"`` but never got a
    ``for_slide``), so those on-disk companions keep working without a
    re-extract.
    """
    if for_slide == TITLE_SLIDE_ID:
        return True
    return for_slide is None and slide_id == TITLE_SLIDE_ID


def _find_title_macro_index(cells: list[RawCell]) -> int | None:
    """Index of the j2 ``header`` title-macro cell, or ``None`` if absent.

    The macro-generated title slide carries no ``slide_id``, so it never appears
    in ``id_map``; this is how the title group is located instead (#242, #246).
    A deck has at most one title macro, so the first match is returned.
    """
    for i, cell in enumerate(cells):
        if is_title_macro_cell(cell):
            return i
    return None


def _find_title_insertion_point(
    cells: list[RawCell],
    vo_lang: str | None,
) -> int | None:
    """Find where to insert an *anchorless* title-greeting voiceover.

    The macro-generated title slide is the j2 ``header`` macro cell, which
    carries no ``slide_id`` — so :func:`_find_insertion_point` cannot resolve
    ``for_slide="title"`` against ``id_map``. This locates the title macro cell
    and walks forward over its (id-less, non-slide-start) continuation cells —
    mirroring the group-end logic of :func:`_find_insertion_point` — so the
    voiceover lands at the end of the title slide group, just before the first
    real slide.

    This is the *fallback* for a title companion with no ``vo_anchor`` (legacy
    pre-#242/#246 extracts, hand-authored cells). A freshly-extracted title
    greeting now carries a ``tm:`` anchor recording its exact authored position
    and is restored via :func:`_match_anchor`, so it never reaches here (#246).

    Returns the insert-after index, or ``None`` when the deck has no title
    macro (e.g. a mis-authored ``slide_id="title"`` with no header macro), in
    which case the caller reports the cell unmatched rather than guessing.
    """
    start = _find_title_macro_index(cells)
    if start is None:
        return None

    insert_after = start
    for i in range(start + 1, len(cells)):
        meta = cells[i].metadata
        if meta.is_narrative:
            break
        if meta.is_slide_start:
            break
        # A continuation cell carrying its own slide_id belongs to a later
        # slide group (the title group has none of its own), so stop.
        if meta.slide_id:
            break
        if vo_lang and meta.lang and meta.lang != vo_lang:
            break
        # A mid-title-group j2 cell (e.g. a widget on the title slide) is a
        # continuation, not a boundary: walk over it so an anchorless title
        # greeting still lands at the true group end rather than before it
        # (#247).
        insert_after = i

    return insert_after


def slide_group_bounds(
    cells: list[RawCell],
    for_slide: str,
    vo_lang: str | None,
    id_map: dict[str, list[int]],
) -> tuple[int, int] | None:
    """Return ``(start, end)`` cell indices of a slide group, or None.

    ``start`` is the slide-start cell carrying ``for_slide`` (preferring a
    language match); ``end`` is the index of the next slide-start after it
    (exclusive), or ``len(cells)``. Used to scope anchor matching so a
    fingerprint can only resolve within its own slide group.

    The ``end`` scan is language-aware: in an interleaved bilingual deck
    the next slide-start may be the *other* language's twin carrying the
    same slide_id, which would otherwise truncate the group before its own
    continuation cells. Slide-starts whose language differs from ``vo_lang``
    do not close the group.
    """
    indices = id_map.get(for_slide)
    if not indices:
        # The macro-generated title slide carries no slide_id, so it never
        # appears in id_map. Resolve its group via the title macro cell so a
        # title voiceover's anchor can be scoped to the title group (#246).
        if for_slide == TITLE_SLIDE_ID:
            return _title_group_bounds(cells, vo_lang)
        return None

    start: int | None = None
    for idx in indices:
        cell = cells[idx]
        if not cell.metadata.is_slide_start:
            continue
        if vo_lang is None or cell.metadata.lang is None or cell.metadata.lang == vo_lang:
            start = idx
    if start is None:
        start = indices[0]

    end = len(cells)
    for i in range(start + 1, len(cells)):
        meta = cells[i].metadata
        if not meta.is_slide_start:
            continue
        if vo_lang is not None and meta.lang is not None and meta.lang != vo_lang:
            continue
        end = i
        break
    return start, end


def _title_group_bounds(
    cells: list[RawCell],
    vo_lang: str | None,
) -> tuple[int, int] | None:
    """Return ``(start, end)`` bounds of the macro-generated title slide group.

    ``start`` is the j2 title macro cell; ``end`` is the index of the next
    slide-start after it (exclusive, language-aware), or ``len(cells)``. The
    title slide has no ``slide_id``, so this is the title analogue of the
    ``id_map`` lookup in :func:`slide_group_bounds`, used to scope a title
    voiceover's anchor to the title group (#246). Returns ``None`` when the deck
    has no title macro.
    """
    start = _find_title_macro_index(cells)
    if start is None:
        return None

    end = len(cells)
    for i in range(start + 1, len(cells)):
        meta = cells[i].metadata
        if not meta.is_slide_start:
            continue
        if vo_lang is not None and meta.lang is not None and meta.lang != vo_lang:
            continue
        end = i
        break
    return start, end


def _resolve_in_group(
    cells: list[RawCell],
    bounds: tuple[int, int],
    kind: str,
    value: str,
    occ: int,
    vo_lang: str | None,
) -> int | None:
    """Pick the ``occ``-th in-group cell matching an anchor ``(kind, value)``.

    Returns ``None`` when there is no such occurrence (e.g. a duplicate
    predecessor was deleted) so the caller can fall back to the legacy
    group-end placement and *report* the relocation rather than silently
    binding to the wrong (first) occurrence.
    """
    candidates = anchor_candidates(cells, bounds, kind, value, vo_lang)
    if occ < len(candidates):
        return candidates[occ]
    return None


def _match_anchor(
    cells: list[RawCell],
    for_slide: str | None,
    anchor: str,
    vo_lang: str | None,
    id_map: dict[str, list[int]],
) -> int | None:
    """Resolve a ``vo_anchor`` to the index of its predecessor cell.

    Matching is strictly scoped to the owning slide group: a fingerprint or
    slide_id can only resolve within ``for_slide``'s group, and to the
    recorded occurrence within it. Returns the index of the cell after
    which the voiceover should be inserted, or ``None`` if the predecessor
    is not found there (the caller then falls back and reports it).

    When ``for_slide`` is present but absent from the slide (e.g. its owning
    slide_id was renamed), this returns ``None`` rather than searching other
    groups — a whole-file search could silently drop the voiceover into a
    foreign slide that happens to share a body fingerprint. The whole-file
    best-effort is used only for an anchor with no ``for_slide`` at all
    (hand-authored companions).

    The title-macro anchor (``tm:``, #246) is resolved directly to the j2 title
    macro cell, independent of ``id_map`` / group bounds — the title slide has
    no ``slide_id`` to scope by, and a title greeting recorded with this anchor
    sits immediately after the macro. Returns ``None`` (caller falls back) when
    the deck no longer has a title macro.
    """
    kind, value, occ = split_anchor(anchor)

    if kind == TITLE_MACRO_KIND:
        return _find_title_macro_index(cells)

    if for_slide:
        bounds = slide_group_bounds(cells, for_slide, vo_lang, id_map)
        if bounds is None:
            return None
        return _resolve_in_group(cells, bounds, kind, value, occ, vo_lang)

    return _resolve_in_group(cells, (0, len(cells)), kind, value, occ, vo_lang)


def plan_insertion(
    cells: list[RawCell],
    vo_cell: RawCell,
    id_map: dict[str, list[int]],
) -> tuple[int | None, str]:
    """Decide where a single voiceover cell should be inserted.

    Returns ``(insert_after_index, status)`` where status is one of
    ``"anchored"`` (exact predecessor match), ``"placed"`` (legacy
    for_slide group-end, no anchor recorded, or the title-macro fallback),
    ``"relocated"`` (an anchor was recorded but its predecessor is gone,
    fell back to group end), or ``"unmatched"`` (no placement found).
    ``insert_after_index`` is ``None`` only for ``"unmatched"``.

    A title-greeting voiceover is a special case: the title slide is the
    macro-generated j2 ``header`` cell, which has no slide_id, so it resolves
    through :func:`_find_title_insertion_point` rather than ``id_map`` (#242).
    """
    for_slide = vo_cell.metadata.for_slide
    anchor = parse_vo_anchor(vo_cell.header)
    vo_lang = vo_cell.metadata.lang

    if anchor:
        idx = _match_anchor(cells, for_slide, anchor, vo_lang, id_map)
        if idx is not None:
            return idx, "anchored"

    if for_slide:
        idx = _find_insertion_point(cells, for_slide, vo_lang, id_map)
        if idx is not None:
            return idx, ("relocated" if anchor else "placed")

    # Title-greeting fallback (#242): the macro-generated title slide carries
    # no slide_id in source, so for_slide="title" (or a pre-fix / hand-authored
    # companion with slide_id="title" and no for_slide) never resolves via
    # id_map. Anchor it to the title macro cell, mirroring the inline-by-
    # position behaviour. This fires only after the normal path fails, so
    # non-title voiceovers are never affected.
    if _is_title_intent(for_slide, vo_cell.metadata.slide_id):
        idx = _find_title_insertion_point(cells, vo_lang)
        if idx is not None:
            return idx, ("relocated" if anchor else "placed")

    return None, "unmatched"


def apply_insertions(
    cells: list[RawCell],
    insertions: list[tuple[int, RawCell]],
    unmatched: list[RawCell],
) -> list[RawCell]:
    """Rebuild the cell list with voiceovers inserted after their anchors.

    ``insertions`` must be in companion (document) order. Multiple
    voiceovers sharing the same ``insert_after`` index are emitted in that
    order — a plain index-shifting ``list.insert`` reverses such groups.
    ``unmatched`` cells are appended at the end.
    """
    by_after: dict[int, list[RawCell]] = defaultdict(list)
    for insert_after, vo_cell in insertions:
        by_after[insert_after].append(vo_cell)

    new_cells: list[RawCell] = []
    for i, cell in enumerate(cells):
        new_cells.append(cell)
        new_cells.extend(by_after.get(i, ()))
    new_cells.extend(unmatched)
    return new_cells
