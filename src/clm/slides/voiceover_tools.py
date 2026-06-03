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

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from clm.notebooks.slide_parser import parse_cell_header, parse_cells
from clm.slides.normalizer import (
    _apply_slide_ids,
    _RawCell,
    _reconstruct,
    _split_raw_cells,
)

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


# ---------------------------------------------------------------------------
# Companion file naming
# ---------------------------------------------------------------------------


def companion_path(slide_path: Path) -> Path:
    """Derive the companion voiceover file path from a slide file path.

    ``slides_intro.py`` → ``voiceover_intro.py``
    ``topic_overview.py`` → ``voiceover_overview.py``
    ``project_setup.py`` → ``voiceover_setup.py``
    """
    stem = slide_path.stem
    # Replace known prefixes
    for prefix in ("slides_", "topic_", "project_"):
        if stem.startswith(prefix):
            suffix_part = stem[len(prefix) :]
            return slide_path.with_name(f"voiceover_{suffix_part}.py")
    # Fallback: prepend voiceover_
    return slide_path.with_name(f"voiceover_{stem}.py")


# ---------------------------------------------------------------------------
# Extract voiceover
# ---------------------------------------------------------------------------


def _is_voiceover_cell(cell: _RawCell) -> bool:
    """Check if a cell is a voiceover or notes cell."""
    return cell.metadata.is_narrative


def _ensure_slide_ids(cells: list[_RawCell], path: Path) -> int:
    """Auto-generate slide_ids for content cells that lack them.

    Delegates to the shared assign-ids engine (via the normalizer
    adapter). Returns the number of ids assigned.
    """
    changes, _refusals = _apply_slide_ids(cells, path)
    return len(changes)


# ---------------------------------------------------------------------------
# Positional anchors
#
# ``for_slide`` records the *owning slide* of a voiceover cell — coarse
# enough for the build merge and ``voiceover sync``, but it cannot say
# *where among that slide's continuation cells* the voiceover originally
# sat. ``vo_anchor`` records the voiceover's immediate predecessor content
# cell so ``inline`` can restore it to its exact position instead of
# dumping every voiceover at the end of its slide group.
#
# The anchor is either ``id:<slide_id>`` (when the predecessor carries a
# slide_id — the common "right after the heading" case) or
# ``fp:<fingerprint>`` of the predecessor's body. The fingerprint is
# body-only on purpose: header-tag edits (e.g. adding ``keep``) between
# extract and inline must not break the anchor.
#
# Neither a slide_id nor a body fingerprint is guaranteed unique *within*
# one slide group (repeated boilerplate code cells, two cells sharing a
# slide_id, a de/en pair). So the token also carries a 0-based occurrence
# ordinal — ``id:<sid>#<n>`` / ``fp:<hash>#<n>`` — meaning "the n-th cell
# in the group matching this token". Resolution is always scoped to the
# owning slide group; it never searches across groups.
# ---------------------------------------------------------------------------

_FOR_SLIDE_RE = re.compile(r'\s*for_slide="[^"]*"')
_VO_ANCHOR_RE = re.compile(r'\s*vo_anchor="[^"]*"')
_VO_ANCHOR_VALUE_RE = re.compile(r'vo_anchor="([^"]*)"')


def _body_fingerprint(cell: _RawCell) -> str:
    """Return a short, stable fingerprint of a cell's body.

    Blank lines are dropped entirely (not just leading/trailing) so the
    fingerprint is invariant under the ``\\n{3,}`` -> ``\\n\\n`` blank-line
    cleanup that ``extract`` applies to the whole slide text *after* the
    anchor is recorded. Trailing whitespace is stripped and the cell type
    is folded in to avoid markdown/code collisions.
    """
    body_lines = [ln.rstrip() for ln in cell.lines[1:]]
    body_lines = [ln for ln in body_lines if ln]
    norm = "\n".join(body_lines)
    payload = f"{cell.metadata.cell_type}\x00{norm}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _anchor_key(cell: _RawCell) -> tuple[str, str]:
    """Return the ``(kind, value)`` half of an anchor for ``cell``."""
    sid = cell.metadata.slide_id
    if sid:
        return ("id", sid)
    return ("fp", _body_fingerprint(cell))


def _anchor_candidates(
    cells: list[_RawCell],
    bounds: tuple[int, int],
    kind: str,
    value: str,
    vo_lang: str | None,
) -> list[int]:
    """Indices within ``bounds`` matching an anchor ``(kind, value)``.

    Returned in document order. Narrative/j2 cells and cells of a
    conflicting language are excluded so the occurrence ordinal counts the
    same content cells at extract time and at inline time.
    """
    lo, hi = bounds
    out: list[int] = []
    for i in range(lo, hi):
        meta = cells[i].metadata
        if meta.is_narrative or meta.is_j2:
            continue
        if vo_lang and meta.lang and meta.lang != vo_lang:
            continue
        if kind == "id" and meta.slide_id == value:
            out.append(i)
        elif kind == "fp" and _body_fingerprint(cells[i]) == value:
            out.append(i)
    return out


def _anchor_token(
    cells: list[_RawCell],
    pred_idx: int,
    bounds: tuple[int, int],
    vo_lang: str | None,
) -> str:
    """Build the occurrence-qualified anchor token for a predecessor cell.

    ``bounds`` is the predecessor's owning slide group. The ordinal is the
    predecessor's position among same-token candidates in that group, so a
    voiceover after the second of two identical cells resolves back to the
    second, not the first.
    """
    kind, value = _anchor_key(cells[pred_idx])
    candidates = _anchor_candidates(cells, bounds, kind, value, vo_lang)
    occ = candidates.index(pred_idx) if pred_idx in candidates else 0
    return f"{kind}:{value}#{occ}"


def _parse_vo_anchor(header: str) -> str | None:
    """Extract the ``vo_anchor`` token from a cell header, if present."""
    m = _VO_ANCHOR_VALUE_RE.search(header)
    return m.group(1) if m else None


def _split_anchor(anchor: str) -> tuple[str, str, int]:
    """Parse ``kind:value#occ`` into ``(kind, value, occ)``.

    A legacy token without the ``#occ`` suffix yields occurrence 0.
    """
    kind, _, rest = anchor.partition(":")
    value, _, occ_s = rest.partition("#")
    occ = int(occ_s) if occ_s.isdigit() else 0
    return kind, value, occ


def _find_predecessor_index(
    cells: list[_RawCell],
    voiceover_idx: int,
    vo_lang: str | None,
) -> int | None:
    """Index of the content cell immediately preceding a voiceover cell.

    Walks backward over j2 and narrative cells (other voiceover/notes
    cells) and over cells of a conflicting language, returning the first
    real content cell. Returns ``None`` if the voiceover has no content
    cell above it.
    """
    for i in range(voiceover_idx - 1, -1, -1):
        meta = cells[i].metadata
        if meta.is_j2 or meta.is_narrative:
            continue
        if meta.lang is not None and vo_lang is not None and meta.lang != vo_lang:
            continue
        return i
    return None


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
    """
    vo_cell = cells[voiceover_idx]
    vo_lang = vo_cell.metadata.lang

    for i in range(voiceover_idx - 1, -1, -1):
        cell = cells[i]
        meta = cell.metadata
        if meta.is_j2:
            continue
        if meta.is_narrative:
            continue
        # Must be same language or language-neutral
        if meta.lang is not None and vo_lang is not None and meta.lang != vo_lang:
            continue
        if meta.slide_id:
            return meta.slide_id
    return None


def extract_voiceover(
    path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> ExtractionResult:
    """Extract voiceover cells from a slide file to a companion file.

    Content cells without ``slide_id`` get auto-generated IDs before
    extraction.  Voiceover cells are linked to their owning slide via
    ``for_slide`` metadata.

    The companion is *rebuilt* from the voiceover cells currently in the slide
    file. If a companion already exists it would be overwritten, discarding any
    hand-edits (or previously-extracted cells) that live only in the companion —
    so, like ``split_in_file``, this refuses without ``force``.

    Args:
        path: Path to the ``.py`` slide file.
        force: Overwrite an existing companion file. Without it, an existing
            companion raises :class:`VoiceoverError` rather than clobbering it.
        dry_run: If ``True``, preview without writing files.

    Returns:
        An :class:`ExtractionResult` describing what was done.

    Raises:
        VoiceoverError: a companion already exists and ``force`` is not set.
    """
    comp = companion_path(path)
    result = ExtractionResult(
        slide_file=str(path),
        companion_file=str(comp),
        dry_run=dry_run,
    )

    text = path.read_text(encoding="utf-8")
    preamble, cells = _split_raw_cells(text)

    # Check if there are any voiceover cells at all
    vo_indices = [i for i, c in enumerate(cells) if _is_voiceover_cell(c)]
    if not vo_indices:
        return result

    # Auto-generate slide_ids for cells that need them
    result.ids_generated = _ensure_slide_ids(cells, path)

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
            anchor = (
                _anchor_token(cells, pred_idx, bounds, vo_lang)
                if pred_idx is not None and bounds is not None
                else None
            )
            new_header = _build_voiceover_header(vo_cell, slide_id, anchor)
            vo_cell.lines[0] = new_header
            vo_cell.metadata = parse_cell_header(new_header)

        companion_cells.append(vo_cell)

    result.cells_extracted = len(companion_cells)

    # Remove voiceover cells from the slide file
    remaining_cells = [c for i, c in enumerate(cells) if i not in set(vo_indices)]

    if not dry_run:
        # Refuse to clobber an existing companion *before* touching the slide
        # file — otherwise a raise here would strip voiceover from the slide
        # and leave no companion (data loss). ``force`` opts into the rebuild.
        if comp.exists() and not force:
            raise VoiceoverError(
                f"refusing to overwrite existing companion '{comp.name}' "
                f"(pass force=True / --force to rebuild it from the current "
                f"voiceover cells; this discards content present only in the "
                f"companion)"
            )

        # Write the slide file without voiceover cells
        new_slide_text = _reconstruct(preamble, remaining_cells)
        # Clean up double blank lines left by removal
        new_slide_text = re.sub(r"\n{3,}", "\n\n", new_slide_text)
        path.write_text(new_slide_text, encoding="utf-8", newline="\n")

        # Write the companion file
        companion_text = _reconstruct("", companion_cells)
        comp.write_text(companion_text, encoding="utf-8", newline="\n")

    return result


# ---------------------------------------------------------------------------
# In-memory merge (used by the build pipeline)
# ---------------------------------------------------------------------------


def merge_voiceover_text(
    slide_text: str,
    companion_text: str,
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
    preamble, slide_cells = _split_raw_cells(slide_text)
    _, companion_cells = _split_raw_cells(companion_text)

    if not companion_cells:
        return slide_text, []

    id_map = _build_slide_id_to_cell_map(slide_cells)

    insertions: list[tuple[int, _RawCell]] = []
    unmatched_ids: list[str] = []

    for vo_cell in companion_cells:
        for_slide = vo_cell.metadata.for_slide
        if not for_slide:
            unmatched_ids.append("<no for_slide>")
            continue

        insert_after, status = _plan_insertion(slide_cells, vo_cell, id_map)
        if insert_after is None:
            unmatched_ids.append(for_slide)
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
    # that belong to the same slide group (e.g., code cells after a slide)
    insert_after = best
    for i in range(best + 1, len(cells)):
        cell = cells[i]
        if cell.metadata.is_narrative:
            break
        if cell.metadata.is_slide_start:
            break
        if cell.metadata.is_j2:
            break
        # If this cell has a different slide_id, stop
        if cell.metadata.slide_id and cell.metadata.slide_id != slide_id:
            break
        # If this cell is lang-tagged and doesn't match, stop
        if vo_lang and cell.metadata.lang and cell.metadata.lang != vo_lang:
            break
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
    """
    kind, value, occ = _split_anchor(anchor)

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
    for_slide group-end, no anchor recorded), ``"relocated"`` (an anchor
    was recorded but its predecessor is gone, fell back to group end), or
    ``"unmatched"`` (no placement found). ``insert_after_index`` is
    ``None`` only for ``"unmatched"``.
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
    comp = companion_path(path)
    result = InlineResult(
        slide_file=str(path),
        companion_file=str(comp),
        dry_run=dry_run,
    )

    if not comp.exists():
        return result

    slide_text = path.read_text(encoding="utf-8")
    companion_text = comp.read_text(encoding="utf-8")

    preamble, slide_cells = _split_raw_cells(slide_text)
    _, companion_cells = _split_raw_cells(companion_text)

    if not companion_cells:
        return result

    # Build slide_id → cell index map for the slide file
    id_map = _build_slide_id_to_cell_map(slide_cells)

    # Plan each companion cell against the (edited) slide file. Voiceovers
    # are anchored to their original predecessor cell so they return to
    # their exact position; if that anchor is gone we fall back to the end
    # of the owning slide group and record a relocation.
    insertions: list[tuple[int, _RawCell]] = []  # (insert_after_idx, cell) in companion order
    unmatched: list[_RawCell] = []

    for vo_cell in companion_cells:
        anchor = _parse_vo_anchor(vo_cell.header)
        for_slide = vo_cell.metadata.for_slide
        insert_after, status = _plan_insertion(slide_cells, vo_cell, id_map)

        if insert_after is None:
            result.unmatched_cells += 1
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
    # back in the slide file. Unmatched cells are NOT stripped: they stay in
    # the companion (below) and must keep their for_slide / vo_anchor so a
    # retry after fixing the slide_id can re-place them.
    for _, vo_cell in insertions:
        clean_header = _strip_author_attrs(vo_cell.header)
        vo_cell.lines[0] = clean_header
        vo_cell.metadata = parse_cell_header(clean_header)

    result.cells_inlined = len(insertions)

    if not insertions and not unmatched:
        return result

    if not dry_run:
        if insertions:
            # Inline only the cells we could place. Unmatched cells are *not*
            # dumped at the end of the slide (stripped of for_slide/anchor);
            # they are preserved in the companion below so they stay placeable.
            new_cells = _apply_insertions(slide_cells, insertions, [])
            new_text = _reconstruct(preamble, new_cells)
            path.write_text(new_text, encoding="utf-8", newline="\n")

        if unmatched:
            # Some companion cells could not be matched — typically the owning
            # slide_id was renamed. Rather than destroying the clean,
            # anchor-bearing companion (the recoverable source of truth) and
            # stranding the narration at EOF, rewrite the companion to the
            # unmatched remainder and keep it. The author fixes the slide_id(s)
            # and re-runs inline to place them.
            remaining_text = _reconstruct("", unmatched)
            comp.write_text(remaining_text, encoding="utf-8", newline="\n")
            result.companion_retained = True
        else:
            comp.unlink()
            result.companion_deleted = True

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


def _format_companion_cell_body(text: str) -> list[str]:
    """Format narrative text as comment-prefixed body lines for a companion cell."""
    lines = text.strip().split("\n")
    body: list[str] = ["#"]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            body.append("#")
        elif stripped.startswith("- ") or stripped.startswith("**["):
            body.append(f"# {stripped}")
        else:
            body.append(f"# - {stripped}")
    return body


def render_companion_update(
    companion_text: str,
    notes_by_slide_id: Mapping[str, str],
    lang: str,
    *,
    tag: str = "voiceover",
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

    preamble, cells = _split_raw_cells(companion_text)

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
        body = _format_companion_cell_body(text)
        if slide_id in existing:
            cell = cells[existing[slide_id]]
            cell.lines = [cell.lines[0], *body]
        else:
            header = f'# %% [markdown] lang="{lang}" tags=["{tag}"] for_slide="{slide_id}"'
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
    new_text = render_companion_update(existing_text, notes_by_slide_id, lang, tag=tag)
    companion.write_text(new_text, encoding="utf-8", newline="\n")
    return companion
