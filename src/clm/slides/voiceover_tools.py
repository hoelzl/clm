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
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from clm.infrastructure.utils.path_utils import atomic_write_all
from clm.notebooks.slide_parser import comment_token_for_path, parse_cell_header, parse_cells
from clm.slides.anchor_primitives import (
    TITLE_MACRO_ANCHOR as _TITLE_MACRO_ANCHOR,
)
from clm.slides.anchor_primitives import (
    TITLE_MACRO_KIND as _TITLE_MACRO_KIND,
)
from clm.slides.anchor_primitives import (
    anchor_candidates as _anchor_candidates,
)
from clm.slides.anchor_primitives import (
    anchor_token as _anchor_token,
)
from clm.slides.anchor_primitives import (
    find_predecessor_index as _find_predecessor_index,
)
from clm.slides.anchor_primitives import (
    split_anchor as _split_anchor,
)
from clm.slides.normalizer import (
    _apply_slide_ids,
    _RawCell,
    _reconstruct,
    _split_raw_cells,
)
from clm.slides.pairing import TITLE_SLIDE_ID, is_title_macro_cell, order_split_pair

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
    unmatched: list[_RawCell] = field(default_factory=list)
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

    preamble, slide_cells = _split_raw_cells(slide_text, comment_token)
    _, companion_cells = _split_raw_cells(companion_text, comment_token)
    if not companion_cells:
        return result
    result.had_companion_cells = True

    id_map = _build_slide_id_to_cell_map(slide_cells)

    insertions: list[tuple[int, _RawCell]] = []  # (insert_after_idx, cell), companion order
    unmatched: list[_RawCell] = []
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
        result.inlined_text = _reconstruct(preamble, new_cells)
    if unmatched:
        result.remaining_companion_text = _reconstruct("", unmatched)
    return result


# ---------------------------------------------------------------------------
# Companion file naming
# ---------------------------------------------------------------------------


# Topic-relative subdirectory that may hold extracted voiceover companions
# instead of placing them as siblings of the slide file. Auto-detected on read
# by the companion file's presence (see :func:`resolve_companion`) — the
# voiceover analogue of the ``cassettes/`` cassette sidecar.
COMPANION_SUBDIR = "voiceover"


def companion_name(slide_path: Path) -> str:
    """Return the companion voiceover *filename* for a slide file.

    Directory-independent — the name only. Known slide prefixes are replaced
    with ``voiceover_``; the slide's own extension (``.py``/``.cs``/``.cpp``/
    ``.java``/``.ts``) is preserved, and any ``.de`` / ``.en`` language tag (part
    of the stem) is kept so the two halves of a split deck never collide:

    ``slides_intro.py`` → ``voiceover_intro.py``
    ``slides_010_x.de.cs`` → ``voiceover_010_x.de.cs``
    ``topic_overview.cpp`` → ``voiceover_overview.cpp``
    ``project_setup.py`` → ``voiceover_setup.py``
    """
    stem = slide_path.stem
    ext = slide_path.suffix
    # Replace known prefixes
    for prefix in ("slides_", "topic_", "project_"):
        if stem.startswith(prefix):
            return f"voiceover_{stem[len(prefix) :]}{ext}"
    # Fallback: prepend voiceover_
    return f"voiceover_{stem}{ext}"


def companion_path(slide_path: Path) -> Path:
    """Return the *sibling* companion path for a slide file.

    This is the nominal companion location next to the slide — the
    backward-compatible default used as a write target and for display. To find
    a companion that may have been relocated into the ``voiceover/``
    subdirectory, use :func:`resolve_companion` instead.
    """
    return slide_path.with_name(companion_name(slide_path))


def companion_locations(slide_path: Path) -> list[Path]:
    """Return every *existing* companion path for a slide, in read-precedence
    order (the ``voiceover/`` subdir before the sibling).

    Normally length 0 or 1. Length ≥ 2 means the same companion exists in *both*
    the relocated subdir and as a sibling — an ambiguity where
    :func:`resolve_companion` silently prefers the relocated copy. ``clm
    validate`` surfaces that case so it can be reconciled.
    """
    name = companion_name(slide_path)
    out: list[Path] = []
    nested = slide_path.parent / COMPANION_SUBDIR / name
    if nested.exists():
        out.append(nested)
    sibling = slide_path.with_name(name)
    if sibling.exists():
        out.append(sibling)
    return out


def resolve_companion(slide_path: Path) -> Path | None:
    """Return the *existing* companion for a slide file, or ``None``.

    Prefers the relocated ``<topic>/voiceover/<name>`` when present, else the
    sibling ``<topic>/<name>``. Location-config-free: it finds the companion in
    either layout, so reads (the build merge, ``inline``, ``validate``, the
    ``sync`` baseline) work without knowing how a given topic is organised. The
    ``voiceover/`` subdirectory is auto-detected by the file's presence — exactly
    as ``cassettes/`` is for cassettes. When a companion exists in *both*
    locations the relocated one wins.
    """
    locations = companion_locations(slide_path)
    return locations[0] if locations else None


def expected_companion(slide_path: Path, *, layout: str | None = None) -> Path:
    """Return the *write target* path for a slide's companion.

    Where a newly-created companion (``extract``, ``sync``, ``split``) should be
    written. Resolution:

    - ``layout="subdir"``: ``<topic>/voiceover/<name>`` (the dir is created by
      the caller on write).
    - ``layout="sibling"``: ``<topic>/<name>`` (next to the slide).
    - ``layout=None`` (auto): prefer the ``voiceover/`` subdir — when that
      directory already exists, **or** for a brand-new companion. The one
      exception is a deck that *already* has a sibling companion: that one stays
      a sibling so a single deck is never split across both layouts. So the auto
      precedence is: existing ``voiceover/`` dir → subdir; else existing sibling
      companion for *this* deck → sibling; else → subdir (the default for new
      companions). ``NotebookFile.expected_cassette_path`` uses the same rule for
      cassettes.

    Reads do not consult this — they use :func:`resolve_companion`, which finds
    the companion in either layout regardless of the write target.
    """
    name = companion_name(slide_path)
    parent = slide_path.parent
    if layout == "subdir":
        return parent / COMPANION_SUBDIR / name
    if layout == "sibling":
        return parent / name
    if (parent / COMPANION_SUBDIR).is_dir():
        return parent / COMPANION_SUBDIR / name
    if (parent / name).exists():
        return parent / name
    return parent / COMPANION_SUBDIR / name


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


def _is_extractable_cell(cell: _RawCell, *, include_notes: bool) -> bool:
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
    cells: list[_RawCell],
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
    from clm.slides.assign_ids import _twin_ids_for

    if twin_ids is _TWIN_FROM_DISK:
        resolved = _twin_ids_for(path, text)
    else:
        resolved = cast("list[str | None] | None", twin_ids)
    changes, _refusals = _apply_slide_ids(cells, path, twin_ids=resolved)
    return len(changes)


# Positional anchors. The ``vo_anchor`` algorithm — anchor a narrative cell to
# its occurrence-qualified immediate predecessor, scoped to the owning slide
# group — lives in :mod:`clm.slides.anchor_primitives` so the ``clm slides sync``
# engine can key/place narratives by the same algorithm (Issue #403). Only the
# stored-attribute helpers (``vo_anchor="…"`` read/write) remain here.

_FOR_SLIDE_RE = re.compile(r'\s*for_slide="[^"]*"')
_VO_ANCHOR_RE = re.compile(r'\s*vo_anchor="[^"]*"')
_VO_ANCHOR_VALUE_RE = re.compile(r'vo_anchor="([^"]*)"')


def _parse_vo_anchor(header: str) -> str | None:
    """Extract the ``vo_anchor`` token from a cell header, if present."""
    m = _VO_ANCHOR_VALUE_RE.search(header)
    return m.group(1) if m else None


def _build_voiceover_header(
    voiceover_cell: _RawCell,
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


def _find_owning_slide_id(cells: list[_RawCell], voiceover_idx: int) -> str | None:
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
        if meta.slide_id:
            return meta.slide_id
        # An id-less real slide is the owning slide but offers no id to
        # reference; stop here instead of walking past it into the title macro.
        if meta.is_slide_start:
            return None
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
    _preamble, cells = _split_raw_cells(text, comment_token)
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

    preamble, cells = _split_raw_cells(text, comment_token_for_path(path))

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
    companion_cells: list[_RawCell] = []
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
    new_slide_text = _reconstruct(preamble, remaining_cells)
    # Clean up double blank lines left by removal
    new_slide_text = re.sub(r"\n{3,}", "\n\n", new_slide_text)
    companion_text = _reconstruct("", companion_cells)
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


# ---------------------------------------------------------------------------
# In-memory merge (used by the build pipeline)
# ---------------------------------------------------------------------------


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
    preamble, slide_cells = _split_raw_cells(slide_text, comment_token)
    _, companion_cells = _split_raw_cells(companion_text, comment_token)

    if not companion_cells:
        return slide_text, []

    id_map = _build_slide_id_to_cell_map(slide_cells)

    insertions: list[tuple[int, _RawCell]] = []
    unmatched_ids: list[str] = []

    for vo_cell in companion_cells:
        for_slide = vo_cell.metadata.for_slide

        # Let _plan_insertion decide — it owns the for_slide match, the
        # vo_anchor whole-file fallback, and the title-greeting fallback. A
        # companion with no for_slide is no longer short-circuited here, so a
        # title cell (slide_id="title", no for_slide — what pre-#242 extract
        # wrote) and a hand-authored anchor-only cell can still be placed.
        insert_after, status = _plan_insertion(slide_cells, vo_cell, id_map)
        if insert_after is None:
            unmatched_ids.append(for_slide if for_slide else "<no for_slide>")
            continue

        # vo_anchor is an author-only positional hint; never leak it into
        # the merged notebook the build consumes. (for_slide is left as-is
        # to preserve existing build output.)
        vo_cell.lines[0] = _VO_ANCHOR_RE.sub("", vo_cell.header)
        vo_cell.metadata = parse_cell_header(vo_cell.lines[0])
        insertions.append((insert_after, vo_cell))

    if not insertions and not unmatched_ids:
        return slide_text, []

    merged_cells = _apply_insertions(slide_cells, insertions, [])
    merged_text = _reconstruct(preamble, merged_cells)
    return merged_text, unmatched_ids


# ---------------------------------------------------------------------------
# Inline voiceover
# ---------------------------------------------------------------------------


def _build_slide_id_to_cell_map(
    cells: list[_RawCell],
) -> dict[str, list[int]]:
    """Map slide_id → list of cell indices (for content cells)."""
    result: dict[str, list[int]] = {}
    for idx, cell in enumerate(cells):
        if cell.metadata.slide_id and not cell.metadata.is_narrative:
            result.setdefault(cell.metadata.slide_id, []).append(idx)
    return result


def _find_insertion_point(
    cells: list[_RawCell],
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


def _find_title_macro_index(cells: list[_RawCell]) -> int | None:
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
    cells: list[_RawCell],
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


def _slide_group_bounds(
    cells: list[_RawCell],
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
    cells: list[_RawCell],
    vo_lang: str | None,
) -> tuple[int, int] | None:
    """Return ``(start, end)`` bounds of the macro-generated title slide group.

    ``start`` is the j2 title macro cell; ``end`` is the index of the next
    slide-start after it (exclusive, language-aware), or ``len(cells)``. The
    title slide has no ``slide_id``, so this is the title analogue of the
    ``id_map`` lookup in :func:`_slide_group_bounds`, used to scope a title
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
    cells: list[_RawCell],
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
    candidates = _anchor_candidates(cells, bounds, kind, value, vo_lang)
    if occ < len(candidates):
        return candidates[occ]
    return None


def _match_anchor(
    cells: list[_RawCell],
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
    kind, value, occ = _split_anchor(anchor)

    if kind == _TITLE_MACRO_KIND:
        return _find_title_macro_index(cells)

    if for_slide:
        bounds = _slide_group_bounds(cells, for_slide, vo_lang, id_map)
        if bounds is None:
            return None
        return _resolve_in_group(cells, bounds, kind, value, occ, vo_lang)

    return _resolve_in_group(cells, (0, len(cells)), kind, value, occ, vo_lang)


def _plan_insertion(
    cells: list[_RawCell],
    vo_cell: _RawCell,
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
    anchor = _parse_vo_anchor(vo_cell.header)
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


def _apply_insertions(
    cells: list[_RawCell],
    insertions: list[tuple[int, _RawCell]],
    unmatched: list[_RawCell],
) -> list[_RawCell]:
    """Rebuild the cell list with voiceovers inserted after their anchors.

    ``insertions`` must be in companion (document) order. Multiple
    voiceovers sharing the same ``insert_after`` index are emitted in that
    order — a plain index-shifting ``list.insert`` reverses such groups.
    ``unmatched`` cells are appended at the end.
    """
    by_after: dict[int, list[_RawCell]] = defaultdict(list)
    for insert_after, vo_cell in insertions:
        by_after[insert_after].append(vo_cell)

    new_cells: list[_RawCell] = []
    for i, cell in enumerate(cells):
        new_cells.append(cell)
        new_cells.extend(by_after.get(i, ()))
    new_cells.extend(unmatched)
    return new_cells


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
    preamble, slide_cells = _split_raw_cells(path.read_text(encoding="utf-8"), comment_token)
    _, companion_cells = _split_raw_cells(comp.read_text(encoding="utf-8"), comment_token)
    if not companion_cells:
        return result

    id_map = _build_slide_id_to_cell_map(slide_cells)
    insertions: list[tuple[int, _RawCell]] = []
    # ``retained`` keeps companion order: voiceover (and other non-notes) cells
    # are passed through untouched; unplaceable notes are kept for a retry.
    retained: list[_RawCell] = []

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
        path.write_text(_reconstruct(preamble, new_cells), encoding="utf-8", newline="\n")
        if retained:
            comp.write_text(_reconstruct("", retained), encoding="utf-8", newline="\n")
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

    preamble, cells = _split_raw_cells(companion_text, comment_token)

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
                _RawCell(
                    lines=new_lines,
                    line_number=0,
                    metadata=parse_cell_header(header),
                )
            )

    new_text = _reconstruct(preamble, cells)
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
