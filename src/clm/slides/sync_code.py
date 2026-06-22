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

A group is rebuilt when its *structural signature* (the order of role cells, the
verbatim text of shared cells, and the kinds of id-less localized cells — all
language-agnostic) differs between source and target, **or** when it holds an
id-less localized cell whose content drifted from the baseline (Issue #190 item
2b: a body edit leaves the ``("L", kind)`` signature unchanged, so the drift is
detected against the widened watermark instead). A pure narrative edit leaves
both unchanged, so narrative-only passes never touch a group here. Cells the
author **moved between groups** fall out for free, because each group is rebuilt
from its current source membership.

Direction is the run's single propagation direction: the keyed narrative
proposals when present, else the ``anchor_direction`` the item-2 detector
inferred from which half drifted its language-neutral cells (Issue #190 §7). A
pass with no determinable direction (cold start, or cells edited both ways) skips
this structural step.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from clm.slides.raw_cells import RawCell
from clm.slides.sync_translate import TranslationError
from clm.slides.sync_writeback import (
    CODE_ROLE,
    FileState,
    anchor_of,
    build_twin_cell,
    cell_content_hash,
    role_of,
)

if TYPE_CHECKING:
    from clm.slides.sync_apply import ApplyResult
    from clm.slides.sync_plan import SyncPlan
    from clm.slides.sync_translate import SlideTranslator

__all__ = ["TranslationOutcome", "apply_code_structure"]


@dataclass
class TranslationOutcome:
    """The materialized translation of one source cell (#216 2b / #289 P2).

    ``body`` is the translated counterpart, or ``error`` is the deferral message
    to record (the translation raised). Exactly one is set. Keyed by ``id(cell)``
    of the source cell (stable: both decks load once and every walk holds the
    same cell objects). One shared cache serves the add walks
    (:func:`clm.slides.sync_apply._materialize_idcarrying` /
    ``_materialize_idless``) AND the structural pass
    (``_materialize_structural``), so e.g. a deferred add's source cell already
    carries its outcome when the structural rebuild reaches for it. Defined here
    (the lowest consumer) because :mod:`clm.slides.sync_apply` imports this
    module.
    """

    body: str | None = None
    error: str | None = None


def apply_code_structure(
    plan: SyncPlan,
    de_state: FileState,
    en_state: FileState,
    translator: SlideTranslator | None,
    result: ApplyResult,
    baseline_anchors: dict[str, dict[str, str]] | None = None,
    translations: dict[int, TranslationOutcome] | None = None,
    *,
    deterministic_only: bool = False,
) -> None:
    """Propagate language-neutral / id-less-localized cells and fix group order.

    Mutates the target deck's :class:`FileState` in place (and marks it dirty)
    for every slide group whose structure drifted from the source. No-op when
    the run has no single propagation direction.

    ``baseline_anchors`` is the per-language ``{anchor: content_hash}`` of the
    last-synced state (from the widened watermark). When a rebuilt id-less
    localized code cell is **unchanged** (its source-side anchor is in the
    baseline with the same content hash), the existing target twin is spliced
    verbatim instead of re-translated — Issue #190 item 3 (§8).

    ``translations`` is the shared pre-materialized translation cache (#289 P2,
    stage 2b): the rebuild reads each needed translation from it by source-cell
    id, so the common path calls no model inside the execute walk. A miss —
    e.g. a reuse-eligible cell whose target twin turned out absent/ambiguous —
    falls back to translating inline, the same documented safety net as the add
    path.

    ``deterministic_only`` (the model-free ``apply`` verb, epic #440) forbids any
    translation: a region that needs one is left at its current target bytes and
    its cells are deferred as residue (no error) — neutral-cell propagation still
    runs, since that is verbatim and needs no model.
    """
    direction = _single_direction(plan)
    if direction is None:
        # Item-2 fallback (Issue #190 §7): a code-only change to a language-neutral
        # cell produces no keyed proposal, so the keyed walk yields no direction.
        # The anchor diff (build_sync_plan) detected which half drifted — use it so
        # the neutral cell is copied verbatim to its twin (the group's ("S", body)
        # signature already differs, so it rebuilds).
        direction = plan.anchor_direction
    if direction is None:
        return
    if direction == "en->de":
        source_state, target_state, source_lang, target_lang = en_state, de_state, "en", "de"
    else:
        source_state, target_state, source_lang, target_lang = de_state, en_state, "de", "en"
    src_anchors = (baseline_anchors or {}).get(source_lang, {})
    # Source-language baseline id-less localized hashes as a multiset (Issue #269):
    # lets a region detect a HASH-anchored id-less body edit — one whose construct
    # anchor changes with the body, so ``src_anchors`` (keyed by the *current*
    # anchor) cannot see it — by content membership instead.
    src_idless = plan.idless_baseline_de if source_lang == "de" else plan.idless_baseline_en
    src_idless_counts = Counter(src_idless) if src_idless else None

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
            result.applied_structural += 1  # Issue #269: surface structural propagation

    def reconcile(src_region: list[RawCell], tgt_region: list[RawCell]) -> None:
        if _signature(src_region) == _signature(tgt_region) and not _region_has_localized_drift(
            src_region, src_anchors, src_idless_counts
        ):
            emit(tgt_region, was_rebuilt=False)
            return
        rebuilt = _rebuild_region(
            src_region,
            tgt_region,
            target_state,
            source_lang,
            target_lang,
            translator,
            result,
            src_anchors,
            translations,
            deterministic_only=deterministic_only,
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
        if meta.lang is None:
            # Language-neutral: shared verbatim across the halves, so its TEXT is
            # comparable — and it MUST be, even when the cell carries a narrative
            # tag (``role_of`` set). The per-cell engine never reconciles a no-lang
            # cell (``ordered_sync_cells`` filters by ``lang``), so the structural
            # pass owns it; keying it on role alone would discard the body and let a
            # one-sided body edit to a tagged-neutral cell slip through silently
            # (Issue #269). Checked before ``role_of`` precisely so a tag cannot
            # mask the body.
            sig.append(("S", cell.body))
            continue
        role = role_of(meta)
        if role is not None:
            sig.append(("R", role))
        else:
            sig.append(("L", "code" if meta.cell_type == "code" else "markdown"))
    return sig


def _region_has_localized_drift(
    src_region: list[RawCell],
    src_anchors: dict[str, str] | None,
    src_idless_counts: Counter[str] | None = None,
) -> str | None:
    """Whether the source region holds an id-less localized cell edited since baseline.

    Item-2b (§7b). A localized id-less cell that changed must be re-translated, but
    its ``("L", kind)`` signature is unchanged by a body edit, so the group would
    not otherwise rebuild. Two complementary detectors:

    - **construct anchor** (``src_anchors``): a cell whose stable construct anchor
      (``def foo`` → ``construct:foo``) is recorded in the baseline but whose current
      content hash differs. ``src_anchors`` is de-duplicated (the Phase 2 ``Counter``
      guard), so a non-unique construct anchor is simply absent — that cell does not
      force a rebuild here (it falls to the hash-membership check below);
    - **hash membership** (``src_idless_counts``, Issue #269): a *hash-anchored*
      id-less cell (no nameable construct — a bare ``print(...)`` / expression) edits
      its own anchor, so the construct check above can never see it. Instead compare
      content membership: a source id-less cell whose body is not covered by the
      source-language baseline multiset of id-less hashes is new or edited → drift.
      The multiset is consumed as matched so an unchanged duplicate-bodied sibling
      still matches while a genuinely new one does not.

    Returns a truthy marker for the first drifted cell, or ``None``.
    """
    if src_anchors:
        for cell in src_region:
            meta = cell.metadata
            if meta.is_j2 or meta.lang is None or role_of(meta) is not None:
                continue  # only id-less LOCALIZED cells (role_of None, lang set)
            anchor = anchor_of(meta, cell.body)
            baseline_hash = src_anchors.get(anchor)
            if baseline_hash is not None and baseline_hash != cell_content_hash(cell.body):
                return anchor
    if src_idless_counts:
        remaining = Counter(src_idless_counts)
        for cell in src_region:
            meta = cell.metadata
            if meta.is_j2 or meta.lang is None or role_of(meta) is not None:
                continue
            chash = cell_content_hash(cell.body)
            if remaining.get(chash, 0) > 0:
                remaining[chash] -= 1  # an unchanged baseline cell accounts for this one
            else:
                return f"idless-drift:{chash}"  # body not in baseline → new/edited
    return None


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
    src_anchors: dict[str, str] | None = None,
    translations: dict[int, TranslationOutcome] | None = None,
    *,
    deterministic_only: bool = False,
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
        if meta.lang is None:
            # Language-neutral: shared verbatim across the halves, whatever its role.
            # Checked before ``role_of`` so a tagged-neutral cell (``tags=["slide"]``
            # but no ``lang``) is copied from the source rather than pulled as a
            # stale ``(slide_id, role)`` twin — the body edit would otherwise be
            # dropped (Issue #269). Its header is identical on both halves (no lang
            # to swap), so a verbatim copy carries any tag/body change across.
            out.append(_copy_cell(cell))
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
                # is missing (e.g. translation deferred), translate as a fallback —
                # the shared cache already carries a deferred add's outcome — and
                # on failure, abort the whole region rather than drop the cell.
                body = _translate(
                    cell,
                    source_lang,
                    target_lang,
                    role,
                    translator,
                    result,
                    translations,
                    deterministic_only=deterministic_only,
                )
                if body is None:
                    return None
                out.append(build_twin_cell(cell, target_lang, body))
        elif meta.lang == source_lang:
            # Item-3 reuse (§8): an UNCHANGED id-less localized cell keeps its
            # existing target twin verbatim instead of being re-translated. It is
            # "unchanged" iff its source-side anchor is in the baseline with the
            # same content hash; the twin is located in the current target region
            # by the same (language-agnostic, construct-based) anchor.
            reused = _reuse_unchanged_twin(cell, tgt_cells, src_anchors)
            if reused is not None:
                out.append(reused)
                continue
            kind = CODE_ROLE if meta.cell_type == "code" else "markdown"
            body = _translate(
                cell,
                source_lang,
                target_lang,
                kind,
                translator,
                result,
                translations,
                deterministic_only=deterministic_only,
            )
            if body is None:
                return None
            out.append(build_twin_cell(cell, target_lang, body))
        # else: an other-language cell in the source deck — should not occur; skip.
    return out


def _reuse_unchanged_twin(
    cell: RawCell, tgt_cells: list[RawCell], src_anchors: dict[str, str] | None
) -> RawCell | None:
    """Return the existing target twin to splice verbatim, or ``None`` to translate.

    Reuse fires only when (a) the source cell's anchor is in the baseline with the
    *same* content hash — proving the source is unchanged since the last sync — and
    (b) the current target region holds a cell with that same anchor (the twin).
    A construct-based anchor is language-agnostic, so the de/en copies of the same
    code share it; a hash-fallback anchor differs across languages, so such cells
    never reuse (they translate, the honest §12 residual). ``None`` ``src_anchors``
    (no watermark) disables reuse — the pre-#190 always-translate behavior.
    """
    if not src_anchors:
        return None
    anchor = anchor_of(cell.metadata, cell.body)
    if src_anchors.get(anchor) != cell_content_hash(cell.body):
        return None
    return _find_by_anchor(tgt_cells, anchor)


def _find_by_anchor(cells: list[RawCell], anchor: str) -> RawCell | None:
    """The single target cell carrying ``anchor``, or ``None`` if absent/ambiguous.

    Returns ``None`` when *more than one* cell matches: a construct anchor is only
    a name, so a duplicate (two ``import os``, two ``def greet``) cannot reliably
    name a twin — splicing an arbitrary first match would drop one cell and
    duplicate the other (silent cross-deck corruption). The ambiguous cells then
    translate normally (Issue #190 review).
    """
    matches = [
        cell
        for cell in cells
        if not cell.metadata.is_j2 and anchor_of(cell.metadata, cell.body) == anchor
    ]
    return matches[0] if len(matches) == 1 else None


def _translate(
    cell: RawCell,
    source_lang: str,
    target_lang: str,
    role: str,
    translator: SlideTranslator | None,
    result: ApplyResult,
    translations: dict[int, TranslationOutcome] | None = None,
    *,
    deterministic_only: bool = False,
) -> str | None:
    """Resolve a cell's translation for the structural pass, recording failures.

    Reads the pre-materialized outcome from the shared ``translations`` cache
    first (#289 P2 — the model call happened in stage 2b); a miss falls back to
    translating inline, the documented safety net for a cell the materializer
    could not foresee needing (e.g. a reuse-eligible cell whose target twin
    turned out absent or ambiguous).

    ``deterministic_only`` (the model-free ``apply`` verb, epic #440 decision B)
    means "a translation is needed here but you must not call a model": defer the
    cell as residue — increment ``deferred`` so the watermark holds and the pass
    exits non-zero — **without** recording an error. The default (``autopilot`` /
    the human one-shot) keeps the old contract: a missing translator is an error.
    """
    if deterministic_only:
        # The agent path never translates; a localized cell needing translation is
        # residue for `task`/`accept`, not a failure. Defer it silently.
        result.deferred += 1
        return None
    cached = translations.get(id(cell)) if translations is not None else None
    if cached is not None:
        if cached.error is not None:
            result.deferred += 1
            result.errors.append(f"code-structure: {cached.error}")
            return None
        return cached.body
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
    """Give every rebuilt non-last localized cell the deck's separator; clear the last's.

    Only the cells the structural pass produced or reordered (``rebuilt_ids``)
    are touched, so an untouched group keeps its exact bytes. Built twins arrive
    with no trailing blanks; normalising the rebuilt *localized* cells to the
    deck's gap keeps them byte-consistent with the rest. ``j2`` header cells are
    skipped — a header macro often sits tight against its sibling (gap 0) while
    the deck is otherwise blank-separated. The final cell's trailing blank is the
    terminal-newline artifact, restored by :meth:`FileState.flush`.

    **Language-neutral (shared) cells are NOT re-gapped** (Issue #9): a shared
    cell is copied verbatim from the source half (:func:`_copy_cell`), so its
    trailing blanks *are* the source's inter-cell spacing and must survive
    byte-for-byte, or the added shared cell diverges from its source twin and
    fails ``validate``'s split-pair byte-identity check (the ``unify`` invariant
    requires shared cells to match across halves, even when the two halves use a
    different modal gap). The only exception is a shared cell that lands **last**:
    its source-side trailing blanks would become a spurious gap before EOF, so it
    is still cleared to the terminal-newline artifact.
    """
    last = len(cells) - 1
    for i, cell in enumerate(cells):
        if cell.metadata.is_j2 or id(cell) not in rebuilt_ids:
            continue
        is_last = i == last and ends_with_newline
        if cell.metadata.lang is None and not is_last:
            # Shared cell, not last: keep its verbatim (source) spacing.
            continue
        want = 0 if is_last else sep
        _set_trailing_blanks(cell, want)


def _set_trailing_blanks(cell: RawCell, n: int) -> None:
    body = cell.lines[1:]
    while body and body[-1] == "":
        body.pop()
    body.extend([""] * n)
    cell.lines = [cell.lines[0], *body]
