"""Structural code-cell / cell-order propagation for single-language sync.

Phase 6 of Issue #166 — the reported *"sync never propagates CODE cells"* bug.
The per-``(slide_id, role)`` engine in :mod:`clm.slides.sync_apply` reconciles
narrative markdown, auxiliary markdown, and **localized id'd** code cells. It
cannot reach two other kinds of cell, nor place them in the right order:

- **language-neutral** cells (no ``lang=``): code or markdown shared *verbatim*
  between the two halves of a split deck — imports, setup, plain output cells;
- **id-less localized** cells (a ``lang=`` cell with no ``slide_id``): a one-off
  demo/output cell that needs translating but has no stable identity.

This module runs **after** the per-cell apply, when the narrative / aux /
id'd-code twins are already on the target deck, and rebuilds the cell order of
each *changed* slide group from the source deck:

- a cell reconciled per-cell (``role_of`` is not ``None``) is pulled back in by
  its ``(slide_id, role)`` — its content was already reconciled, just reposition;
- a **language-neutral** cell is copied verbatim;
- an **id-less localized** cell is translated.

A group is rebuilt only when its *structural signature* (the order of role
cells, the verbatim text of shared cells, and the kinds of id-less localized
cells — all language-agnostic) differs between source and target. A pure
narrative edit leaves the signature unchanged, so narrative-only passes never
touch a group here. Cells the author **moved between groups** fall out for free,
because each group is rebuilt from its current source membership.

Direction is taken from the run's narrative proposals (the single-language
workflow edits one deck): a uniform ``en->de`` / ``de->en`` selects the source.
A pass with no determinable single direction (cold start, or cells edited both
ways) skips this structural step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from clm.slides.raw_cells import RawCell
from clm.slides.sync_translate import TranslationError
from clm.slides.sync_writeback import CODE_ROLE, FileState, build_twin_cell, role_of

if TYPE_CHECKING:
    from clm.slides.sync_apply import ApplyResult
    from clm.slides.sync_plan import SyncPlan
    from clm.slides.sync_translate import SlideTranslator

__all__ = ["apply_code_structure"]


def apply_code_structure(
    plan: SyncPlan,
    de_state: FileState,
    en_state: FileState,
    translator: SlideTranslator | None,
    result: ApplyResult,
) -> None:
    """Propagate language-neutral / id-less-localized cells and fix group order.

    Mutates the target deck's :class:`FileState` in place (and marks it dirty)
    for every slide group whose structure drifted from the source. No-op when
    the run has no single propagation direction.
    """
    direction = _single_direction(plan)
    if direction is None:
        return
    if direction == "en->de":
        source_state, target_state, source_lang, target_lang = en_state, de_state, "en", "de"
    else:
        source_state, target_state, source_lang, target_lang = de_state, en_state, "de", "en"

    src_head, src_groups = _split_groups(source_state.cells)
    tgt_head, tgt_groups = _split_groups(target_state.cells)

    # First source group per id (a copy-paste duplicate is resolved upstream).
    src_group_by_id: dict[str, list[RawCell]] = {}
    for group in src_groups:
        sid = group[0].metadata.slide_id
        if sid is not None:
            src_group_by_id.setdefault(sid, group)

    sep = target_state.separator_blanks()  # read before we swap cells out
    new_cells: list[RawCell] = []
    rebuilt_ids: set[int] = set()

    def emit(region: list[RawCell], *, was_rebuilt: bool) -> None:
        new_cells.extend(region)
        if was_rebuilt:
            rebuilt_ids.update(id(c) for c in region)

    def reconcile(src_region: list[RawCell], tgt_region: list[RawCell]) -> None:
        if _signature(src_region) == _signature(tgt_region):
            emit(tgt_region, was_rebuilt=False)
            return
        rebuilt = _rebuild_region(
            src_region, tgt_region, target_state, source_lang, target_lang, translator, result
        )
        if rebuilt is None:
            # A translation needed for the rebuild failed (no translator, or it
            # raised). Keep the target region byte-for-byte rather than commit a
            # partial rebuild that would DROP a pre-existing target cell; the
            # failure is already recorded (deferred + error), so the watermark
            # holds and the region re-attempts on the next run.
            emit(tgt_region, was_rebuilt=False)
        else:
            emit(rebuilt, was_rebuilt=True)

    # Head region (cells before the first slide) — same rule as a group.
    reconcile(src_head, tgt_head)

    for tgt_group in tgt_groups:
        sid = tgt_group[0].metadata.slide_id
        src_group = src_group_by_id.get(sid) if sid is not None else None
        if src_group is None:
            emit(tgt_group, was_rebuilt=False)
        else:
            reconcile(src_group, tgt_group)

    if rebuilt_ids:
        _normalize_separators(
            new_cells, sep, rebuilt_ids, ends_with_newline=target_state.ends_with_newline
        )
        target_state.cells = new_cells
        target_state.dirty = True


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


def _single_direction(plan: SyncPlan) -> str | None:
    """The one propagation direction of the plan, or ``None`` if not unambiguous."""
    directions = {p.direction for p in plan.proposals if p.direction in ("de->en", "en->de")}
    if len(directions) == 1:
        return next(iter(directions))
    return None


# ---------------------------------------------------------------------------
# Grouping + structural signature
# ---------------------------------------------------------------------------


def _split_groups(cells: list[RawCell]) -> tuple[list[RawCell], list[list[RawCell]]]:
    """Split ``cells`` into a head (before the first slide) and slide groups.

    A group is a slide/subslide cell plus every following cell until the next
    slide/subslide.
    """
    head: list[RawCell] = []
    groups: list[list[RawCell]] = []
    current: list[RawCell] | None = None
    for cell in cells:
        if cell.metadata.is_slide_start:
            current = [cell]
            groups.append(current)
        elif current is None:
            head.append(cell)
        else:
            current.append(cell)
    return head, groups


def _signature(cells: list[RawCell]) -> list[tuple]:
    """A language-agnostic structural fingerprint of a region.

    Per cell:

    - **per-cell-synced** (``role_of`` set): ``("R", role)`` — a position marker
      *without* the slide_id. Its identity and content are the per-cell pass's
      and the move logic's job; the structural pass must not react to a narrative
      content edit, nor to a narrative cell reassigned to a different slide (a
      move the engine may have deliberately deferred). Only the *role* is kept,
      so a code/shared cell relocating relative to its narrative anchors is still
      detected;
    - **language-neutral** (no ``lang``): ``("S", body)`` — shared verbatim, so
      its text is comparable across decks;
    - **id-less localized** (``lang`` set): ``("L", kind)`` — body is
      language-specific (not comparable), so only its presence/kind/order count;
    - **j2 directive**: ``("J",)`` — position marker only. A header macro is
      language-specific (``header_en`` vs ``header_de``) without a ``lang=``
      attribute, so its body must NOT count: a differing header is correct, not
      drift, and must never trigger a rebuild that would copy it across.

    Two regions with equal signatures are structurally in sync; a difference is
    an added / removed / relocated code or shared cell that the structural pass
    must reconcile.
    """
    sig: list[tuple] = []
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2:
            sig.append(("J",))
            continue
        role = role_of(meta)
        if role is not None:
            sig.append(("R", role))
        elif meta.lang is None:
            sig.append(("S", cell.body))
        else:
            sig.append(("L", "code" if meta.cell_type == "code" else "markdown"))
    return sig


# ---------------------------------------------------------------------------
# Region rebuild
# ---------------------------------------------------------------------------


def _rebuild_region(
    src_cells: list[RawCell],
    tgt_cells: list[RawCell],
    target_state: FileState,
    source_lang: str,
    target_lang: str,
    translator: SlideTranslator | None,
    result: ApplyResult,
) -> list[RawCell] | None:
    """Rebuild a target region to mirror the source region's cell order.

    Walks the source region in order, emitting for each cell its target-side
    counterpart: a pulled twin (per-cell-synced), a verbatim copy (shared), or a
    fresh translation (id-less localized). The source order becomes the target
    order, so cross-group moves and intra-group reorders are resolved here.

    Returns ``None`` if any required translation fails (no translator, or it
    raised). Aborting — rather than returning a partial list — is essential:
    the caller replaces the *whole* target region with the result, so dropping
    one cell from a partial rebuild would delete a pre-existing target cell from
    disk. The failure is recorded (deferred + error) before returning ``None``.
    """
    tgt_j2 = [c for c in tgt_cells if c.metadata.is_j2]
    j2_seen = 0
    out: list[RawCell] = []
    for cell in src_cells:
        meta = cell.metadata
        if meta.is_j2:
            # A j2 header is language-specific (header_en / header_de) but not via
            # a lang attribute, so keep the TARGET deck's own header rather than
            # copying the source's. Match by ordinal; fall back to a verbatim copy
            # only if the target somehow has fewer j2 cells.
            twin = tgt_j2[j2_seen] if j2_seen < len(tgt_j2) else None
            out.append(twin if twin is not None else _copy_cell(cell))
            j2_seen += 1
            continue
        role = role_of(meta)
        if role is not None:
            twin = _find(tgt_cells, meta.slide_id, role)
            if twin is None and meta.slide_id is not None:
                twin = target_state.find_cell(meta.slide_id, role)
            if twin is not None:
                out.append(twin)
            else:
                # The per-cell pass should have placed this twin (add/edit). If it
                # is missing (e.g. translation deferred), translate as a fallback;
                # on failure, abort the whole region rather than drop the cell.
                body = _translate(cell, source_lang, target_lang, role, translator, result)
                if body is None:
                    return None
                out.append(build_twin_cell(cell, target_lang, body))
        elif meta.lang is None:
            out.append(_copy_cell(cell))  # language-neutral: shared verbatim
        elif meta.lang == source_lang:
            kind = CODE_ROLE if meta.cell_type == "code" else "markdown"
            body = _translate(cell, source_lang, target_lang, kind, translator, result)
            if body is None:
                return None
            out.append(build_twin_cell(cell, target_lang, body))
        # else: an other-language cell in the source deck — should not occur; skip.
    return out


def _translate(
    cell: RawCell,
    source_lang: str,
    target_lang: str,
    role: str,
    translator: SlideTranslator | None,
    result: ApplyResult,
) -> str | None:
    """Translate a cell body for the structural pass, recording failures."""
    if translator is None:
        result.deferred += 1
        result.errors.append(f"code-structure: no translator for a {role} cell")
        return None
    try:
        return translator.translate(
            source_body=cell.body.rstrip("\n"),
            source_lang=source_lang,
            target_lang=target_lang,
            role=role,
        )
    except TranslationError as exc:
        result.deferred += 1
        result.errors.append(f"code-structure: translate {role} cell failed: {exc}")
        return None


def _copy_cell(cell: RawCell) -> RawCell:
    """A verbatim copy of a language-neutral source cell for the other deck."""
    return RawCell(lines=list(cell.lines), line_number=0, metadata=cell.metadata)


def _find(cells: list[RawCell], slide_id: str | None, role: str) -> RawCell | None:
    for cell in cells:
        if cell.metadata.slide_id == slide_id and role_of(cell.metadata) == role:
            return cell
    return None


# ---------------------------------------------------------------------------
# Separators
# ---------------------------------------------------------------------------


def _normalize_separators(
    cells: list[RawCell], sep: int, rebuilt_ids: set[int], *, ends_with_newline: bool
) -> None:
    """Give every rebuilt non-last cell the deck's separator; clear the last's.

    Only the cells the structural pass produced or reordered (``rebuilt_ids``)
    are touched, so an untouched group keeps its exact bytes. Built twins arrive
    with no trailing blanks and copied/pulled cells carry their own; normalising
    the rebuilt cells to the deck's gap keeps them byte-consistent with the rest.
    ``j2`` header cells are skipped — a header macro often sits tight against its
    sibling (gap 0) while the deck is otherwise blank-separated. The final cell's
    trailing blank is the terminal-newline artifact, restored by
    :meth:`FileState.flush`.
    """
    last = len(cells) - 1
    for i, cell in enumerate(cells):
        if cell.metadata.is_j2 or id(cell) not in rebuilt_ids:
            continue
        want = 0 if (i == last and ends_with_newline) else sep
        _set_trailing_blanks(cell, want)


def _set_trailing_blanks(cell: RawCell, n: int) -> None:
    body = cell.lines[1:]
    while body and body[-1] == "":
        body.pop()
    body.extend([""] * n)
    cell.lines = [cell.lines[0], *body]
