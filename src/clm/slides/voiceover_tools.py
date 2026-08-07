"""Extract voiceover cells to companion files, or inline them back.

``extract_voiceover`` moves voiceover (and optionally notes) cells from
a slide file to a companion ``voiceover_*.py`` file, linked via
``slide_id`` / ``for_slide`` metadata.

``inline_voiceover`` reverses the operation: merges the companion file
back into the slide file and deletes the companion.

``read_companion_baselines`` and ``update_companion_narrative`` support
the ``clm voiceover sync`` companion-aware merge path: reading baseline
narrative text and writing merged results back to a companion file,
keyed by ``slide_id`` via each cell's ``for_slide`` attribute.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from clm.core.slide_text.anchor_primitives import (
    TITLE_MACRO_ANCHOR as _TITLE_MACRO_ANCHOR,
)
from clm.core.slide_text.anchor_primitives import (
    anchor_token as _anchor_token,
)
from clm.core.slide_text.anchor_primitives import (
    find_predecessor_index as _find_predecessor_index,
)
from clm.core.slide_text.pairing import TITLE_SLIDE_ID, is_title_macro_cell, order_split_pair
from clm.core.slide_text.raw_cells import RawCell, reconstruct, split_cells
from clm.core.slide_text.slide_parser import comment_token_for_path, parse_cell_header, parse_cells
from clm.core.slide_text.voiceover_merge import (
    VO_ANCHOR_RE as _VO_ANCHOR_RE,
)
from clm.core.slide_text.voiceover_merge import (
    apply_insertions as _apply_insertions,
)
from clm.core.slide_text.voiceover_merge import (
    build_slide_id_to_cell_map as _build_slide_id_to_cell_map,
)
from clm.core.slide_text.voiceover_merge import (
    parse_vo_anchor as _parse_vo_anchor,
)
from clm.core.slide_text.voiceover_merge import (
    plan_insertion as _plan_insertion,
)
from clm.core.slide_text.voiceover_merge import (
    slide_group_bounds as _slide_group_bounds,
)
from clm.core.voiceover_companions import (
    COMPANION_SUBDIR,
    companion_locations,
    companion_path,
    expected_companion,
    resolve_companion,
)
from clm.infrastructure.utils.path_utils import atomic_write_all
from clm.slides.normalizer import apply_slide_ids

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class VoiceoverError(Exception):
    """A voiceover extract/inline operation refused to proceed (e.g. to avoid
    clobbering an existing companion). Mirrors ``split.SplitError`` — the caller
    (CLI / MCP) turns it into a clean, non-zero-exit message."""


@dataclass
class ExtractionResult:
    """Result of extracting voiceover cells from a slide file."""

    slide_file: str
    companion_file: str
    cells_extracted: int = 0
    ids_generated: int = 0
    dry_run: bool = False

    @property
    def summary(self) -> str:
        parts: list[str] = []
        prefix = "[DRY RUN] " if self.dry_run else ""
        if self.cells_extracted:
            parts.append(
                f"{prefix}{self.cells_extracted} voiceover cell(s) "
                f"extracted to {self.companion_file}"
            )
        else:
            parts.append(f"{prefix}No voiceover cells found.")
        if self.ids_generated:
            parts.append(f"{self.ids_generated} slide_id(s) auto-generated")
        return "; ".join(parts)


@dataclass
class PairedExtractionResult:
    """Result of a paired extraction over both halves of a split deck.

    Holds the two per-half :class:`ExtractionResult`s (``results[0]`` is the DE
    half, ``results[1]`` the EN half, by construction) plus the count of
    EN-authority ``slide_id``s minted across both halves by the pre-extraction
    pass. The two companions' ``for_slide`` sets agree by construction (the
    EN-authority mint stamped the same ids on both halves before extraction).
    """

    results: list[ExtractionResult]
    ids_minted: int = 0
    dry_run: bool = False

    @property
    def de(self) -> ExtractionResult:
        return self.results[0]

    @property
    def en(self) -> ExtractionResult:
        return self.results[1]

    @property
    def summary(self) -> str:
        prefix = "[DRY RUN] " if self.dry_run else ""
        total = sum(r.cells_extracted for r in self.results)
        comps = [r.companion_file for r in self.results if r.cells_extracted]
        if comps:
            head = f"{prefix}paired extract: {total} voiceover cell(s) → {', '.join(comps)}"
        else:
            head = f"{prefix}paired extract: no voiceover cells found in either half."
        parts = [head]
        if self.ids_minted:
            parts.append(f"{self.ids_minted} EN-authority slide_id(s) minted across both halves")
        return "; ".join(parts)


@dataclass
class Placement:
    """Where a single voiceover cell will be (or was) inlined.

    Surfaced for dry-run reporting and JSON output so a relocation is
    visible *before* the file is written, rather than discovered later.
    """

    for_slide: str | None
    anchor: str | None
    status: str  # "anchored" | "placed" | "relocated" | "unmatched"
    after_line: int | None = None
    after_header: str | None = None


@dataclass
class InlineResult:
    """Result of inlining voiceover cells from a companion file."""

    slide_file: str
    companion_file: str
    cells_inlined: int = 0
    unmatched_cells: int = 0
    relocated_cells: int = 0
    companion_deleted: bool = False
    companion_retained: bool = False
    dry_run: bool = False
    placements: list[Placement] = field(default_factory=list)

    @property
    def summary(self) -> str:
        prefix = "[DRY RUN] " if self.dry_run else ""
        parts: list[str] = []
        if self.cells_inlined:
            parts.append(
                f"{prefix}{self.cells_inlined} voiceover cell(s) inlined from {self.companion_file}"
            )
        else:
            parts.append(f"{prefix}No voiceover cells to inline.")
        if self.relocated_cells:
            parts.append(
                f"{self.relocated_cells} cell(s) relocated to the end of their slide "
                f"(original anchor cell was edited or removed)"
            )
        if self.unmatched_cells:
            parts.append(
                f"{self.unmatched_cells} cell(s) could not be matched "
                f"(missing slide_id in slide file)"
            )
        if self.companion_deleted:
            parts.append("companion file deleted")
        if self.companion_retained:
            parts.append(
                f"companion {self.companion_file} retained with the unmatched "
                f"cell(s) — fix the slide_id(s) and re-run inline"
            )
        return "; ".join(parts)


@dataclass
class InlineTextResult:
    """Pure result of inlining companion voiceover into slide *text* (issue #501).

    The IO-free core of :func:`inline_voiceover`: given the slide and companion
    texts it computes where each companion cell lands, the resulting inlined slide
    text, and the text of any cell that could *not* be placed — but writes
    nothing. The ``clm slides sync`` companion projection (design
    ``sync-separated-voiceover-companions.md``) feeds ``inlined_text`` to the
    plan engine in read *and* apply modes, so both observe the identical
    representation; ``unmatched`` is the total-transform hook — an unresolvable
    ``for_slide`` becomes a blocking plan issue rather than dropped narration.
    """

    inlined_text: str
    """Slide text with every *matched* companion cell inlined at its anchor.
    Equals the input ``slide_text`` when nothing matched."""
    remaining_companion_text: str
    """Reconstructed text of the unmatched companion cells (``""`` when none).
    These keep their ``for_slide`` / ``vo_anchor`` so a retry can re-place them."""
    unmatched: list[RawCell] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)
    cells_inlined: int = 0
    relocated_cells: int = 0
    had_companion_cells: bool = False

    @property
    def unmatched_cells(self) -> int:
        return len(self.unmatched)


def inline_pair_text(
    slide_text: str,
    companion_text: str,
    comment_token: str = "#",
) -> InlineTextResult:
    """Inline companion voiceover cells into slide *text*, IO-free (issue #501).

    The pure core of :func:`inline_voiceover`. Companion cells are parsed fresh
    from ``companion_text`` (so the in-place header rewrite below never leaks into
    a caller's model — the projection is safe to run in a non-mutating ``sync``
    read mode), placed after their owning slide via ``for_slide`` / ``vo_anchor``
    (:func:`_plan_insertion`), and stripped of the author-only ``for_slide`` /
    ``vo_anchor`` attributes so an inlined cell looks exactly like a hand-authored
    inline voiceover cell. A cell whose anchor no longer resolves is returned
    *unmatched* (never dropped, never dumped at EOF), mirroring
    :func:`inline_voiceover`'s retain-in-companion contract.

    Returns an :class:`InlineTextResult`; writes nothing.
    """
    result = InlineTextResult(inlined_text=slide_text, remaining_companion_text="")

    preamble, slide_cells = split_cells(slide_text, comment_token)
    _, companion_cells = split_cells(companion_text, comment_token)
    if not companion_cells:
        return result
    result.had_companion_cells = True

    id_map = _build_slide_id_to_cell_map(slide_cells)

    insertions: list[tuple[int, RawCell]] = []  # (insert_after_idx, cell), companion order
    unmatched: list[RawCell] = []
    for vo_cell in companion_cells:
        anchor = _parse_vo_anchor(vo_cell.header)
        for_slide = vo_cell.metadata.for_slide
        insert_after, status = _plan_insertion(slide_cells, vo_cell, id_map)

        if insert_after is None:
            result.placements.append(Placement(for_slide, anchor, "unmatched"))
            unmatched.append(vo_cell)
            continue

        if status == "relocated":
            result.relocated_cells += 1
        anchor_cell = slide_cells[insert_after]
        result.placements.append(
            Placement(
                for_slide,
                anchor,
                status,
                after_line=anchor_cell.line_number,
                after_header=anchor_cell.header,
            )
        )
        insertions.append((insert_after, vo_cell))

    # Strip the author-only companion attributes from the cells about to land
    # back in the slide. Unmatched cells are NOT stripped: they keep their
    # for_slide / vo_anchor so a retry after fixing the slide_id can re-place them.
    for _, vo_cell in insertions:
        clean_header = _strip_author_attrs(vo_cell.header)
        vo_cell.lines[0] = clean_header
        vo_cell.metadata = parse_cell_header(clean_header)

    result.unmatched = unmatched
    result.cells_inlined = len(insertions)

    if insertions:
        new_cells = _apply_insertions(slide_cells, insertions, [])
        result.inlined_text = reconstruct(preamble, new_cells)
    if unmatched:
        result.remaining_companion_text = reconstruct("", unmatched)
    return result


# ---------------------------------------------------------------------------
# Companion file maintenance (naming/location live in core.voiceover_companions)
# ---------------------------------------------------------------------------


def _prune_other_companions(slide_path: Path, keep: Path) -> list[Path]:
    """Delete every existing companion for ``slide_path`` except ``keep``.

    Run after a forced ``extract`` rewrite so relocating a companion (e.g. into
    ``voiceover/``) does not strand a stale copy in the other location, which
    :func:`resolve_companion` would then shadow. Returns the removed paths.
    """
    removed: list[Path] = []
    keep = keep.resolve()
    for loc in companion_locations(slide_path):
        if loc.resolve() != keep:
            loc.unlink()
            removed.append(loc)
    return removed


# ---------------------------------------------------------------------------
# Extract voiceover
# ---------------------------------------------------------------------------


def _is_extractable_cell(cell: RawCell, *, include_notes: bool) -> bool:
    """Cells that ``extract`` pulls into the voiceover companion.

    By default only ``voiceover``-tagged cells are extracted; ``notes``
    (speaker-notes) cells stay inline in the deck. Speaker notes are short and
    belong with the slide they annotate, and leaving them inline keeps the
    companion a *pure voiceover* file (the historical "voiceover companion also
    holds notes" behavior confused both authors and tooling). They still reach
    the trainer/recording outputs from their inline position — the build filters
    by tag regardless of where a cell lives.

    Set ``include_notes`` to restore the pre-split behavior and extract both
    ``voiceover`` and ``notes`` cells (e.g. a course that deliberately keeps
    speaker notes externalized alongside narration). The build merge always
    reads both tags back, so a companion that still contains notes keeps working.
    """
    tags = cell.metadata.tags
    if "voiceover" in tags:
        return True
    return include_notes and "notes" in tags


# Sentinel for :func:`_ensure_slide_ids` / :func:`_plan_extraction_from_text`:
# "derive the twin ids from disk" (the historical default). Distinct from
# ``twin_ids=None`` (a real value meaning "no twin", used for bilingual files),
# so the sync companion projection (issue #501) can thread the already-loaded
# in-memory twin ids without a re-read while callers that pass nothing keep the
# exact disk-reading behavior.
_TWIN_FROM_DISK: Final = object()


def _ensure_slide_ids(
    cells: list[RawCell],
    path: Path,
    text: str,
    *,
    twin_ids: list[str | None] | None | object = _TWIN_FROM_DISK,
) -> int:
    """Auto-generate slide_ids for content cells that lack them.

    Delegates to the shared assign-ids engine (via the normalizer adapter).
    Returns the number of ids assigned.

    Twin-aware (#162 defensive): on a split half (``*.de.py`` / ``*.en.py``)
    whose twin exists on disk with a matching slide count, an **id-less** slide
    adopts the twin's id for the corresponding slide instead of minting a
    divergent slug from its own heading. This keeps ``de_id == en_id`` across a
    per-language extract — without it, extracting each half separately would
    mint independent slugs and the two companions' ``for_slide`` sets would
    diverge (which ``clm validate``'s #162 detective would then flag). The twin
    is read-only; when it has no id for a slide, minting falls back to the
    normal EN-derived slug. Bilingual files (no ``.de`` / ``.en`` suffix) pass
    ``twin_ids=None`` and are unaffected.

    ``twin_ids`` defaults to :data:`_TWIN_FROM_DISK`, deriving the twin's ids from
    disk exactly as before. The sync companion projection (issue #501) threads
    the already-loaded in-memory twin ids instead, so a text-only extract mints
    twin-consistently with no hidden disk read.
    """
    from clm.slides.assign_ids import twin_ids_for

    if twin_ids is _TWIN_FROM_DISK:
        resolved = twin_ids_for(path, text)
    else:
        resolved = cast("list[str | None] | None", twin_ids)
    changes, _refusals = apply_slide_ids(cells, path, twin_ids=resolved)
    return len(changes)


# Positional anchors. The ``vo_anchor`` algorithm — anchor a narrative cell to
# its occurrence-qualified immediate predecessor, scoped to the owning slide
# group — lives in :mod:`clm.core.slide_text.anchor_primitives` so the ``clm slides sync``
# engine can key/place narratives by the same algorithm (Issue #403). Only the
# stored-attribute helpers (``vo_anchor="…"`` read/write) remain here.

_FOR_SLIDE_RE = re.compile(r'\s*for_slide="[^"]*"')


def _build_voiceover_header(
    voiceover_cell: RawCell,
    slide_id: str,
    anchor: str | None,
) -> str:
    """Build a companion header carrying ``for_slide`` and ``vo_anchor``.

    Any pre-existing ``for_slide`` / ``vo_anchor`` attributes are dropped
    first so the operation is idempotent, then re-appended. Other
    attributes (``slide_id``, ``tags``, ``lang``) are preserved in place.
    """
    header = voiceover_cell.header
    header = _VO_ANCHOR_RE.sub("", header)
    header = _FOR_SLIDE_RE.sub("", header).rstrip()
    header += f' for_slide="{slide_id}"'
    if anchor:
        header += f' vo_anchor="{anchor}"'
    return header


def _strip_author_attrs(header: str) -> str:
    """Remove ``for_slide`` / ``vo_anchor`` — author-only companion attrs."""
    header = _VO_ANCHOR_RE.sub("", header)
    header = _FOR_SLIDE_RE.sub("", header)
    return header


def _find_owning_slide_id(cells: list[RawCell], voiceover_idx: int) -> str | None:
    """Find the slide_id of the content cell that owns a voiceover cell.

    Walks backward from the voiceover cell to find the most recent
    slide/subslide cell in the same language (or language-neutral).

    The macro-generated title slide carries no ``slide_id`` of its own, so a
    voiceover sitting directly under the j2 ``header`` macro resolves to
    :data:`TITLE_SLIDE_ID` (the ``"title"`` greeting convention) — mirroring
    ``assign_ids._handle_title_macro`` and the validator (#242). A real
    slide-start cell that still lacks an id stops the walk rather than letting
    it run past into the title macro, which would mis-anchor the voiceover.
    """
    vo_cell = cells[voiceover_idx]
    vo_lang = vo_cell.metadata.lang

    for i in range(voiceover_idx - 1, -1, -1):
        cell = cells[i]
        meta = cell.metadata
        # Detect the title macro before the is_j2 skip below would hide it.
        if is_title_macro_cell(cell):
            return TITLE_SLIDE_ID
        if meta.is_j2:
            continue
        if meta.is_narrative:
            continue
        # Must be same language or language-neutral
        if meta.lang is not None and vo_lang is not None and meta.lang != vo_lang:
            continue
        # Only a slide/subslide anchor owns a voiceover. Plain content cells
        # carry their own slide_ids after `normalize --stamp-ids` (#520) —
        # returning one of those would silently drift for_slide onto a
        # non-slide id. An id-less real slide is still the owning slide but
        # offers no id to reference; stop there instead of walking past it
        # into the title macro, which would mis-anchor the voiceover.
        if meta.is_slide_start:
            return meta.slide_id or None
    return None


def has_voiceover_cells_text(
    text: str, comment_token: str = "#", *, include_notes: bool = False
) -> bool:
    """True iff slide *text* carries at least one cell ``extract`` would pull out.

    The IO-free core of :func:`_has_voiceover_cells`. By default only a
    ``voiceover``-tagged cell counts; with ``include_notes`` a ``notes`` cell
    counts too (see :func:`_is_extractable_cell`). The ``clm slides sync`` companion
    projection (issue #501) uses the voiceover-only form to tell a *mixed* deck
    (inline ``voiceover`` cells **and** a companion — refused) from the sanctioned
    steady state (inline ``notes`` beside a voiceover companion, post-#387), so the
    predicate must never count ``notes``.
    """
    _preamble, cells = split_cells(text, comment_token)
    return any(_is_extractable_cell(c, include_notes=include_notes) for c in cells)


def _has_voiceover_cells(path: Path, *, include_notes: bool = False) -> bool:
    """True iff ``path`` has at least one cell ``extract`` would pull out.

    By default that means a ``voiceover``-tagged cell; with ``include_notes``
    a ``notes`` cell also counts (see :func:`_is_extractable_cell`).
    """
    return has_voiceover_cells_text(
        path.read_text(encoding="utf-8"),
        comment_token_for_path(path),
        include_notes=include_notes,
    )


def _slide_start_ids_of(path: Path) -> list[str | None]:
    """Ordered ``slide_id``s of the slide/subslide cells in ``path`` (``None``
    where a slide carries no id)."""
    return [
        c.metadata.slide_id
        for c in parse_cells(path.read_text(encoding="utf-8"))
        if c.metadata.is_slide_start
    ]


def _slide_ids_in_parity(de_path: Path, en_path: Path) -> bool:
    """True iff both halves carry the **same** ordered ``slide_id``s with none
    missing — the precondition for skipping the EN-authority pre-mint
    (``mint_ids=False``). An id-less or divergent pair fails this, so the caller
    refuses rather than letting the per-half mint diverge (#162)."""
    de_ids = _slide_start_ids_of(de_path)
    en_ids = _slide_start_ids_of(en_path)
    return de_ids == en_ids and all(de_ids)


def _plan_extraction(
    path: Path, *, dry_run: bool, layout: str | None = None, include_notes: bool = False
) -> tuple[ExtractionResult, list[tuple[Path, str]]]:
    """Compute the extraction result and the ``(path, text)`` writes WITHOUT
    writing anything.

    Reads ``path`` and delegates to :func:`_plan_extraction_from_text` (issue
    #501); see there for the returned shape. The caller owns the
    existing-companion force check and the actual commit (via
    :func:`atomic_write_all`), so the paired path can guard *both* companions up
    front and write all four files in one atomic batch.
    """
    text = path.read_text(encoding="utf-8")
    return _plan_extraction_from_text(
        text, path, dry_run=dry_run, layout=layout, include_notes=include_notes
    )


def _plan_extraction_from_text(
    text: str,
    path: Path,
    *,
    dry_run: bool,
    layout: str | None = None,
    include_notes: bool = False,
    twin_ids: list[str | None] | None | object = _TWIN_FROM_DISK,
) -> tuple[ExtractionResult, list[tuple[Path, str]]]:
    """IO-free core of :func:`_plan_extraction` (issue #501): compute the
    extraction from slide *text* rather than reading ``path``.

    Returns ``(result, writes)``. ``writes`` is empty when there are no voiceover
    cells (nothing to do) or under ``dry_run`` — so an empty list means "do not
    touch disk". ``path`` is used only for the companion location, the comment
    token, and twin-aware id minting; ``twin_ids`` threads the sync projection's
    in-memory twin ids (default: derive from disk, the historical behavior).

    ``layout`` selects the companion write target (see
    :func:`expected_companion`): ``"subdir"`` / ``"sibling"`` force a location,
    ``None`` auto-detects an existing ``voiceover/`` directory.
    """
    comp = expected_companion(path, layout=layout)
    result = ExtractionResult(
        slide_file=str(path),
        companion_file=str(comp),
        dry_run=dry_run,
    )

    preamble, cells = split_cells(text, comment_token_for_path(path))

    # Indices of the cells we will pull into the companion (voiceover by
    # default; notes too when include_notes is set). Notes left behind stay
    # inline in the slide and are reconstructed below with the survivors.
    vo_indices = [
        i for i, c in enumerate(cells) if _is_extractable_cell(c, include_notes=include_notes)
    ]
    if not vo_indices:
        return result, []

    # Auto-generate slide_ids for cells that need them (twin-aware on a split
    # half so a per-language extract keeps de_id == en_id; see _ensure_slide_ids).
    result.ids_generated = _ensure_slide_ids(cells, path, text, twin_ids=twin_ids)

    # Build companion cells with for_slide metadata (owning slide) and a
    # vo_anchor (immediate predecessor, occurrence-qualified) so inline can
    # restore the exact position rather than the slide-group end.
    id_map = _build_slide_id_to_cell_map(cells)
    companion_cells: list[RawCell] = []
    for idx in vo_indices:
        vo_cell = cells[idx]
        vo_lang = vo_cell.metadata.lang
        slide_id = _find_owning_slide_id(cells, idx)
        if slide_id:
            pred_idx = _find_predecessor_index(cells, idx, vo_lang)
            bounds = _slide_group_bounds(cells, slide_id, vo_lang, id_map)
            # The predecessor must lie *within* the owning slide group for its
            # anchor to resolve there at merge time. For non-title slides this
            # always holds (the slide-start cell is itself an eligible
            # predecessor). For the title slide the group starts at the j2 macro,
            # which the predecessor walk skips over — so the walk can escape
            # *upward* past the macro onto a cell authored before it (e.g. a
            # top-of-deck import). Such a predecessor is out of group; anchoring
            # to it would silently misplace the greeting at the group end on
            # merge (#246). Fall back to the title-macro anchor instead.
            anchor: str | None
            if pred_idx is not None and bounds is not None and bounds[0] <= pred_idx < bounds[1]:
                anchor = _anchor_token(cells, pred_idx, bounds, vo_lang)
            elif slide_id == TITLE_SLIDE_ID and bounds is not None:
                # The title greeting has no in-group content predecessor: its
                # only predecessor is the slide_id-less j2 title macro (or a cell
                # above it). Record a title-macro anchor so the merge restores it
                # at the *start* of the title group rather than the end (#246).
                anchor = _TITLE_MACRO_ANCHOR
            else:
                anchor = None
            new_header = _build_voiceover_header(vo_cell, slide_id, anchor)
            vo_cell.lines[0] = new_header
            vo_cell.metadata = parse_cell_header(new_header)

        companion_cells.append(vo_cell)

    result.cells_extracted = len(companion_cells)

    if dry_run:
        return result, []

    # Remove voiceover cells from the slide file
    remaining_cells = [c for i, c in enumerate(cells) if i not in set(vo_indices)]
    new_slide_text = reconstruct(preamble, remaining_cells)
    # Clean up double blank lines left by removal
    new_slide_text = re.sub(r"\n{3,}", "\n\n", new_slide_text)
    companion_text = reconstruct("", companion_cells)
    return result, [(path, new_slide_text), (comp, companion_text)]


def extract_voiceover(
    path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    layout: str | None = None,
    include_notes: bool = False,
) -> ExtractionResult:
    """Extract voiceover cells from a slide file to a companion file.

    Content cells without ``slide_id`` get auto-generated IDs before
    extraction.  Voiceover cells are linked to their owning slide via
    ``for_slide`` metadata. On a split half whose twin exists on disk with a
    matching slide count, that id generation is **twin-aware** (#162): an
    id-less slide adopts the twin's id rather than minting a divergent slug, so
    extracting the ``.de`` and ``.en`` halves separately keeps their companions'
    ``for_slide`` sets in agreement (see :func:`_ensure_slide_ids`). For a
    one-op, EN-authority paired extract over *both* halves, see
    :func:`extract_voiceover_pair`.

    The companion is *rebuilt* from the voiceover cells currently in the slide
    file. If a companion already exists (in **either** the ``voiceover/`` subdir
    or as a sibling) it would be overwritten, discarding any hand-edits (or
    previously-extracted cells) that live only in the companion — so, like
    ``split_in_file``, this refuses without ``force``.

    Args:
        path: Path to the ``.py`` slide file.
        force: Overwrite an existing companion file. Without it, an existing
            companion raises :class:`VoiceoverError` rather than clobbering it.
        dry_run: If ``True``, preview without writing files.
        layout: Where to write the companion — ``"subdir"`` (``voiceover/``),
            ``"sibling"``, or ``None`` to auto-detect an existing ``voiceover/``
            directory (see :func:`expected_companion`).
        include_notes: Also extract ``notes`` (speaker-notes) cells. By default
            only ``voiceover`` cells are extracted and notes stay inline in the
            deck (see :func:`_is_extractable_cell`).

    Returns:
        An :class:`ExtractionResult` describing what was done.

    Raises:
        VoiceoverError: a companion already exists and ``force`` is not set.
    """
    result, writes = _plan_extraction(
        path, dry_run=dry_run, layout=layout, include_notes=include_notes
    )
    if writes:
        # Refuse to clobber an existing companion *before* any write — otherwise
        # the slide rewrite would strip voiceover and leave no companion (data
        # loss). The guard spans *both* layouts (``resolve_companion``) so a
        # relocate-on-extract never silently discards a companion in the other
        # location. ``force`` opts into the rebuild. The two writes commit
        # together; a forced relocation then prunes the stale other-location copy.
        existing = resolve_companion(path)
        if existing is not None and not force:
            raise VoiceoverError(
                f"refusing to overwrite existing companion '{existing.name}' "
                f"(pass force=True / --force to rebuild it from the current "
                f"voiceover cells; this discards content present only in the "
                f"companion)"
            )
        target = expected_companion(path, layout=layout)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_all(writes)
        _prune_other_companions(path, keep=target)
    return result


def extract_voiceover_pair(
    de_path: Path,
    en_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    mint_ids: bool = True,
    layout: str | None = None,
    include_notes: bool = False,
) -> PairedExtractionResult:
    """Extract voiceover from *both* halves of a split deck in one op.

    The companion footgun this closes: running ``extract`` once per language by
    hand can mint divergent slugs on id-less slides, so the two companions'
    ``for_slide`` sets disagree. Here the two halves are first minted with
    **EN-authority** ``slide_id``s across both at once
    (:func:`~clm.slides.assign_ids.assign_ids_in_split_pair`), so each
    companion's ``for_slide`` set agrees by construction; then each half is
    extracted and all writes commit in one atomic batch.

    Refuses **loudly** (:class:`VoiceoverError`) when the two halves are not
    structurally alignable (divergent shared cells / mismatched cell count): the
    EN-authority pre-mint cannot then guarantee parity, and a silent per-half
    fallback would reintroduce the exact divergence this op exists to prevent.
    Reconcile the pair first (e.g. ``clm slides sync``) and retry.

    Args:
        de_path, en_path: the two halves, in either order (reordered defensively).
        force: overwrite existing companions — **all-or-nothing** over both
            halves (refuses if *either* companion exists and ``force`` is unset).
        dry_run: preview without writing (the pre-mint runs report-only, so no
            slide ids are written either).
        mint_ids: run the EN-authority pre-mint (default on). Set ``False`` only
            when the pair is already known to be in ``slide_id`` parity — chiefly
            for tests isolating the extraction from the minting.
        include_notes: also extract ``notes`` cells from both halves (default
            off — notes stay inline; see :func:`_is_extractable_cell`).

    Raises:
        VoiceoverError: the paths are not a valid same-deck ``.de``/``.en`` pair;
            an existing companion would be clobbered without ``force``; or the
            pair is not structurally alignable for the EN-authority mint.
    """
    ordered = order_split_pair(de_path, en_path)
    if ordered is None:
        raise VoiceoverError(
            f"'{de_path.name}' and '{en_path.name}' are not the two halves of one "
            f"split deck (expected <deck>.de.py and <deck>.en.py of the same deck); "
            f"cannot paired-extract."
        )
    de_path, en_path = ordered

    # Match single-extract's no-op-on-empty contract FIRST (before the force
    # guard): if neither half has any voiceover cells there is nothing to
    # extract, so do nothing — don't refuse on a stale companion and don't
    # id-stamp a deck with nothing to extract (a per-half extract no-ops here).
    if not _has_voiceover_cells(de_path, include_notes=include_notes) and not _has_voiceover_cells(
        en_path, include_notes=include_notes
    ):
        return PairedExtractionResult(
            results=[
                ExtractionResult(
                    slide_file=str(de_path),
                    companion_file=str(expected_companion(de_path, layout=layout)),
                    dry_run=dry_run,
                ),
                ExtractionResult(
                    slide_file=str(en_path),
                    companion_file=str(expected_companion(en_path, layout=layout)),
                    dry_run=dry_run,
                ),
            ],
            dry_run=dry_run,
        )

    # All-or-nothing companion guard, before any write (mirrors split_in_file):
    # refuse if *either* companion exists (in either layout) and not force.
    if not dry_run:
        blockers = [c for c in (resolve_companion(de_path), resolve_companion(en_path)) if c]
        if blockers and not force:
            names = ", ".join(f"'{b.name}'" for b in blockers)
            raise VoiceoverError(
                f"refusing to overwrite existing companion(s): {names} "
                f"(pass force=True / --force to rebuild them from the current "
                f"voiceover cells; this discards content present only in the companion)"
            )

    # EN-authority slide_id mint across both halves first, so the two companions'
    # for_slide sets agree by construction. report_only on a dry run writes nothing.
    ids_minted = 0
    if mint_ids:
        from clm.slides.assign_ids import AssignOptions, assign_ids_in_split_pair

        pre = assign_ids_in_split_pair(de_path, en_path, AssignOptions(report_only=dry_run))
        if pre is None:
            raise VoiceoverError(
                f"cannot paired-extract '{de_path.name}' / '{en_path.name}': the two "
                f"halves are not structurally aligned (divergent shared cells / cell "
                f"count), so EN-authority slide_id parity cannot be guaranteed. "
                f"Reconcile them first (e.g. `clm slides sync`), then retry."
            )
        # Distinct slide_ids stamped on slide-role cells. The same id lands on
        # both halves, so the set dedups to one entry per logical slide;
        # narrative ``voiceover-inherit`` writes are not minted ids.
        ids_minted = len({a.slide_id for a in pre.assignments if a.source != "voiceover-inherit"})
    elif not _slide_ids_in_parity(de_path, en_path):
        # Without the pre-mint, the per-half _ensure_slide_ids would mint
        # independently on an id-less pair and silently diverge (#162). Enforce
        # the documented mint_ids=False contract loudly instead of breaking it.
        raise VoiceoverError(
            f"mint_ids=False requires '{de_path.name}' / '{en_path.name}' to be already "
            f"in slide_id parity (every slide id'd and de_id == en_id); run with "
            f"mint_ids=True (the default) to mint EN-authority ids."
        )

    de_result, de_writes = _plan_extraction(
        de_path, dry_run=dry_run, layout=layout, include_notes=include_notes
    )
    en_result, en_writes = _plan_extraction(
        en_path, dry_run=dry_run, layout=layout, include_notes=include_notes
    )
    writes = [*de_writes, *en_writes]
    if writes:
        de_target = expected_companion(de_path, layout=layout)
        en_target = expected_companion(en_path, layout=layout)
        de_target.parent.mkdir(parents=True, exist_ok=True)
        en_target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_all(writes)
        # Forced relocation prunes any stale companion left in the other layout.
        _prune_other_companions(de_path, keep=de_target)
        _prune_other_companions(en_path, keep=en_target)

    # On the paired path the EN-authority pre-mint owns id generation; the per-half
    # extract mints nothing in a real run (ids are already on disk). Report the
    # count via ``ids_minted`` only, and zero the per-half ``ids_generated`` so the
    # dry-run preview — where the report-only pre-mint writes nothing and
    # ``_plan_extraction`` re-mints in memory — matches the real run.
    for r in (de_result, en_result):
        r.ids_generated = 0

    return PairedExtractionResult(
        results=[de_result, en_result], ids_minted=ids_minted, dry_run=dry_run
    )


def extract_pair_text(
    inlined_text: str,
    deck_path: Path,
    *,
    layout: str | None = None,
    include_notes: bool = False,
    twin_ids: list[str | None] | None | object = _TWIN_FROM_DISK,
) -> tuple[str, str, Path]:
    """Inverse of :func:`inline_pair_text` (issue #501): split inlined deck *text*
    into ``(deck_text, companion_text, companion_path)`` without touching disk.

    The IO-free core of ``extract`` for the ``clm slides sync`` companion
    projection (design ``sync-separated-voiceover-companions.md``): after the plan
    engine reconciles the inlined deck, this re-homes the voiceover into the
    companion — **voiceover-only by default** (notes stay inline, per the issue
    #501 maintainer decision) — and returns the two texts for the caller to commit
    atomically alongside the twin's. ``companion_text`` is ``""`` when the deck has
    no voiceover to extract. ``deck_path`` is used only for the companion location,
    the comment token, and (twin-aware) id minting; pass ``twin_ids`` to keep the
    mint pure and twin-consistent without a disk read.
    """
    _result, writes = _plan_extraction_from_text(
        inlined_text,
        deck_path,
        dry_run=False,
        layout=layout,
        include_notes=include_notes,
        twin_ids=twin_ids,
    )
    comp = expected_companion(deck_path, layout=layout)
    if not writes:
        return inlined_text, "", comp
    deck_text = next(t for p, t in writes if p == deck_path)
    companion_text = next(t for p, t in writes if p == comp)
    return deck_text, companion_text, comp


def inline_voiceover(
    path: Path,
    *,
    dry_run: bool = False,
) -> InlineResult:
    """Inline voiceover cells from a companion file back into a slide file.

    Voiceover cells are inserted after their owning slide (matched via
    ``for_slide`` ↔ ``slide_id``).  The ``for_slide`` attribute is
    removed after inlining.

    Args:
        path: Path to the ``.py`` slide file.
        dry_run: If ``True``, preview without modifying files.

    Returns:
        An :class:`InlineResult` describing what was done.
    """
    comp = resolve_companion(path)
    result = InlineResult(
        slide_file=str(path),
        companion_file=str(comp if comp is not None else companion_path(path)),
        dry_run=dry_run,
    )

    if comp is None:
        return result

    slide_text = path.read_text(encoding="utf-8")
    companion_text = comp.read_text(encoding="utf-8")

    # Pure core (issue #501): plan the placement and compute the inlined slide
    # text plus any unmatched remainder without touching disk. The IO — the
    # writes / unlink / empty-dir cleanup below — stays here.
    core = inline_pair_text(slide_text, companion_text, comment_token_for_path(path))

    result.relocated_cells = core.relocated_cells
    result.unmatched_cells = core.unmatched_cells
    result.placements = core.placements
    result.cells_inlined = core.cells_inlined

    if not core.had_companion_cells:
        return result
    if core.cells_inlined == 0 and core.unmatched_cells == 0:
        return result

    if not dry_run:
        if core.cells_inlined:
            # Inline only the cells we could place. Unmatched cells are *not*
            # dumped at the end of the slide; they are preserved in the
            # companion below so they stay placeable.
            path.write_text(core.inlined_text, encoding="utf-8", newline="\n")

        if core.unmatched:
            # Some companion cells could not be matched — typically the owning
            # slide_id was renamed. Rather than destroying the clean,
            # anchor-bearing companion (the recoverable source of truth) and
            # stranding the narration at EOF, rewrite the companion to the
            # unmatched remainder and keep it. The author fixes the slide_id(s)
            # and re-runs inline to place them.
            comp.write_text(core.remaining_companion_text, encoding="utf-8", newline="\n")
            result.companion_retained = True
        else:
            comp.unlink()
            result.companion_deleted = True
            # If the companion lived in a now-empty ``voiceover/`` subdir, remove
            # the directory too so a fully-inlined topic returns to a clean tree.
            parent = comp.parent
            if parent.name == COMPANION_SUBDIR and not any(parent.iterdir()):
                parent.rmdir()

    return result


def inline_notes(path: Path, *, dry_run: bool = False) -> InlineResult:
    """Move ``notes`` cells from a companion back inline into the slide.

    Migration helper for companions written before voiceover-only extraction
    (or via ``--include-notes``): it inlines just the companion's ``notes``
    (speaker-notes) cells at their anchored positions — exactly as
    :func:`inline_voiceover` does for every cell — and rewrites the companion
    keeping the ``voiceover`` cells (plus any notes that could not be placed) in
    place. ``voiceover`` cells are never moved.

    The companion is deleted only when nothing is left in it (it was notes-only
    and every note was placed). A companion with no ``notes`` cells is a no-op
    (``cells_inlined == 0``, companion untouched).
    """
    comp = resolve_companion(path)
    result = InlineResult(
        slide_file=str(path),
        companion_file=str(comp if comp is not None else companion_path(path)),
        dry_run=dry_run,
    )
    if comp is None:
        return result

    comment_token = comment_token_for_path(path)
    preamble, slide_cells = split_cells(path.read_text(encoding="utf-8"), comment_token)
    _, companion_cells = split_cells(comp.read_text(encoding="utf-8"), comment_token)
    if not companion_cells:
        return result

    id_map = _build_slide_id_to_cell_map(slide_cells)
    insertions: list[tuple[int, RawCell]] = []
    # ``retained`` keeps companion order: voiceover (and other non-notes) cells
    # are passed through untouched; unplaceable notes are kept for a retry.
    retained: list[RawCell] = []

    for cell in companion_cells:
        if "notes" not in cell.metadata.tags:
            retained.append(cell)
            continue
        anchor = _parse_vo_anchor(cell.header)
        for_slide = cell.metadata.for_slide
        insert_after, status = _plan_insertion(slide_cells, cell, id_map)
        if insert_after is None:
            result.unmatched_cells += 1
            result.placements.append(Placement(for_slide, anchor, "unmatched"))
            retained.append(cell)
            continue
        if status == "relocated":
            result.relocated_cells += 1
        anchor_cell = slide_cells[insert_after]
        result.placements.append(
            Placement(
                for_slide,
                anchor,
                status,
                after_line=anchor_cell.line_number,
                after_header=anchor_cell.header,
            )
        )
        insertions.append((insert_after, cell))

    # Strip the author-only attributes from the notes about to land back inline.
    for _, note_cell in insertions:
        clean_header = _strip_author_attrs(note_cell.header)
        note_cell.lines[0] = clean_header
        note_cell.metadata = parse_cell_header(clean_header)

    result.cells_inlined = len(insertions)
    if not insertions:
        # No notes (or none placeable) — leave both files untouched.
        return result

    if not dry_run:
        new_cells = _apply_insertions(slide_cells, insertions, [])
        path.write_text(reconstruct(preamble, new_cells), encoding="utf-8", newline="\n")
        if retained:
            comp.write_text(reconstruct("", retained), encoding="utf-8", newline="\n")
            result.companion_retained = True
        else:
            comp.unlink()
            result.companion_deleted = True
            parent = comp.parent
            if parent.name == COMPANION_SUBDIR and not any(parent.iterdir()):
                parent.rmdir()

    return result


# ---------------------------------------------------------------------------
# Companion baseline read / narrative write (used by `voiceover sync`)
# ---------------------------------------------------------------------------


def read_companion_baselines(
    companion: Path,
    lang: str,
    *,
    tag: str = "voiceover",
) -> dict[str, str]:
    """Return a mapping ``slide_id -> baseline text`` from a companion file.

    Reads every narrative cell with ``for_slide`` set, matching ``lang``
    and carrying ``tag``. The body of each matching cell is returned as
    plain text (comment prefixes stripped). Cells without ``for_slide``
    are skipped; unmatched or missing companion files yield an empty map.
    """
    if not companion.exists():
        return {}

    text = companion.read_text(encoding="utf-8")
    cells = parse_cells(text)

    by_id: dict[str, list[str]] = {}
    for cell in cells:
        meta = cell.metadata
        if not meta.is_narrative:
            continue
        if tag not in meta.tags:
            continue
        if meta.lang is not None and meta.lang != lang:
            continue
        if not meta.for_slide:
            continue
        body = cell.text_content()
        if body:
            by_id.setdefault(meta.for_slide, []).append(body)

    return {sid: "\n".join(parts) for sid, parts in by_id.items()}


def _format_companion_cell_body(text: str, comment_token: str = "#") -> list[str]:
    """Format narrative text as comment-prefixed body lines for a companion cell."""
    lines = text.strip().split("\n")
    body: list[str] = [comment_token]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            body.append(comment_token)
        elif stripped.startswith("- ") or stripped.startswith("**["):
            body.append(f"{comment_token} {stripped}")
        else:
            body.append(f"{comment_token} - {stripped}")
    return body


def render_companion_update(
    companion_text: str,
    notes_by_slide_id: Mapping[str, str],
    lang: str,
    *,
    tag: str = "voiceover",
    comment_token: str = "#",
) -> str:
    """Return updated companion file text with ``notes_by_slide_id`` applied.

    Pure function used by the sync dry-run diff and by
    ``update_companion_narrative``. Existing cells matching
    ``(for_slide, lang, tag)`` have their bodies replaced; unknown
    slide_ids produce appended cells with a new ``for_slide`` header.
    Empty input is returned unchanged.
    """
    if not notes_by_slide_id:
        return companion_text

    preamble, cells = split_cells(companion_text, comment_token)

    existing: dict[str, int] = {}
    for i, cell in enumerate(cells):
        meta = cell.metadata
        if not meta.is_narrative:
            continue
        if tag not in meta.tags:
            continue
        if meta.lang is not None and meta.lang != lang:
            continue
        if meta.for_slide:
            existing[meta.for_slide] = i

    for slide_id, text in notes_by_slide_id.items():
        body = _format_companion_cell_body(text, comment_token)
        if slide_id in existing:
            cell = cells[existing[slide_id]]
            cell.lines = [cell.lines[0], *body]
        else:
            header = (
                f'{comment_token} %% [markdown] lang="{lang}" tags=["{tag}"] for_slide="{slide_id}"'
            )
            new_lines = [header, *body]
            cells.append(
                RawCell(
                    lines=new_lines,
                    line_number=0,
                    metadata=parse_cell_header(header),
                )
            )

    new_text = reconstruct(preamble, cells)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text


def update_companion_narrative(
    companion: Path,
    notes_by_slide_id: Mapping[str, str],
    lang: str,
    *,
    tag: str = "voiceover",
) -> Path:
    """Update or insert narrative cells in a companion file, keyed by slide_id.

    For each ``(slide_id, text)`` in ``notes_by_slide_id``:

    - If a cell with ``for_slide=slide_id`` matching ``lang`` and ``tag``
      already exists, its body is replaced (header is preserved).
    - Otherwise a new cell is appended with ``for_slide="<slide_id>"``.

    If the companion file does not exist, it is created. Empty input is
    a no-op.
    """
    if not notes_by_slide_id:
        return companion

    existing_text = companion.read_text(encoding="utf-8") if companion.exists() else ""
    new_text = render_companion_update(
        existing_text,
        notes_by_slide_id,
        lang,
        tag=tag,
        comment_token=comment_token_for_path(companion),
    )
    # Create the parent on first write so a fresh companion can land in a
    # not-yet-existing ``voiceover/`` subdir (``sync --layout subdir``).
    companion.parent.mkdir(parents=True, exist_ok=True)
    companion.write_text(new_text, encoding="utf-8", newline="\n")
    return companion
