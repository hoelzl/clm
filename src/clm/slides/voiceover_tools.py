"""Extract voiceover cells to companion files, or inline them back.

``extract_voiceover`` moves voiceover (and optionally notes) cells from
a slide file to a companion ``voiceover_*.py`` file, linked via
``slide_id`` / ``for_slide`` metadata.

``inline_voiceover`` reverses the operation: merges the companion file
back into the slide file and deletes the companion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from clm.notebooks.slide_parser import parse_cell_header
from clm.slides.normalizer import (
    _apply_slide_ids,
    _RawCell,
    _reconstruct,
    _split_raw_cells,
)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


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
class InlineResult:
    """Result of inlining voiceover cells from a companion file."""

    slide_file: str
    companion_file: str
    cells_inlined: int = 0
    unmatched_cells: int = 0
    companion_deleted: bool = False
    dry_run: bool = False

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
        if self.unmatched_cells:
            parts.append(
                f"{self.unmatched_cells} cell(s) could not be matched "
                f"(missing slide_id in slide file)"
            )
        if self.companion_deleted:
            parts.append("companion file deleted")
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


def _ensure_slide_ids(cells: list[_RawCell], file_path: str, file_stem: str) -> int:
    """Auto-generate slide_ids for content cells that lack them.

    Returns the number of IDs generated.
    """
    changes = _apply_slide_ids(cells, file_path, file_stem)
    return len(changes)


def _build_for_slide_header(
    voiceover_cell: _RawCell,
    slide_id: str,
) -> str:
    """Build a voiceover cell header with ``for_slide`` metadata.

    If the cell already has ``for_slide``, update it.  Otherwise add it.
    """
    header = voiceover_cell.header
    existing = re.search(r'for_slide="[^"]*"', header)
    if existing:
        return header[: existing.start()] + f'for_slide="{slide_id}"' + header[existing.end() :]
    return header.rstrip() + f' for_slide="{slide_id}"'


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
    dry_run: bool = False,
) -> ExtractionResult:
    """Extract voiceover cells from a slide file to a companion file.

    Content cells without ``slide_id`` get auto-generated IDs before
    extraction.  Voiceover cells are linked to their owning slide via
    ``for_slide`` metadata.

    Args:
        path: Path to the ``.py`` slide file.
        dry_run: If ``True``, preview without writing files.

    Returns:
        An :class:`ExtractionResult` describing what was done.
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
    result.ids_generated = _ensure_slide_ids(cells, str(path), path.stem)

    # Build companion cells with for_slide metadata
    companion_cells: list[_RawCell] = []
    for idx in vo_indices:
        vo_cell = cells[idx]
        slide_id = _find_owning_slide_id(cells, idx)
        if slide_id:
            new_header = _build_for_slide_header(vo_cell, slide_id)
            vo_cell.lines[0] = new_header
            vo_cell.metadata = parse_cell_header(new_header)

        companion_cells.append(vo_cell)

    result.cells_extracted = len(companion_cells)

    # Remove voiceover cells from the slide file
    remaining_cells = [c for i, c in enumerate(cells) if i not in set(vo_indices)]

    if not dry_run:
        # Write the slide file without voiceover cells
        new_slide_text = _reconstruct(preamble, remaining_cells)
        # Clean up double blank lines left by removal
        new_slide_text = re.sub(r"\n{3,}", "\n\n", new_slide_text)
        path.write_text(new_slide_text, encoding="utf-8")

        # Write the companion file
        companion_text = _reconstruct("", companion_cells)
        comp.write_text(companion_text, encoding="utf-8")

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

        insert_after = _find_insertion_point(slide_cells, for_slide, vo_cell.metadata.lang, id_map)
        if insert_after is None:
            unmatched_ids.append(for_slide)
            continue

        insertions.append((insert_after, vo_cell))

    if not insertions and not unmatched_ids:
        return slide_text, []

    if insertions:
        # Sort insertions by position (descending) to keep indices stable
        insertions.sort(key=lambda x: x[0], reverse=True)

        for insert_after, vo_cell in insertions:
            slide_cells.insert(insert_after + 1, vo_cell)

    merged_text = _reconstruct(preamble, slide_cells)
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

    # Process companion cells: group by for_slide, then insert
    # We work in reverse insertion order to keep indices stable
    insertions: list[tuple[int, _RawCell]] = []  # (insert_after_idx, cell)
    unmatched: list[_RawCell] = []

    for vo_cell in companion_cells:
        for_slide = vo_cell.metadata.for_slide
        if not for_slide:
            unmatched.append(vo_cell)
            continue

        insert_after = _find_insertion_point(slide_cells, for_slide, vo_cell.metadata.lang, id_map)
        if insert_after is None:
            unmatched.append(vo_cell)
            continue

        # Strip for_slide from the header (it was added during extraction)
        clean_header = re.sub(r'\s*for_slide="[^"]*"', "", vo_cell.header)
        vo_cell.lines[0] = clean_header
        vo_cell.metadata = parse_cell_header(clean_header)

        insertions.append((insert_after, vo_cell))

    result.cells_inlined = len(insertions)
    result.unmatched_cells = len(unmatched)

    if not insertions and not unmatched:
        return result

    if not dry_run:
        # Sort insertions by position (descending) to keep indices stable
        insertions.sort(key=lambda x: x[0], reverse=True)

        for insert_after, vo_cell in insertions:
            slide_cells.insert(insert_after + 1, vo_cell)

        # Append any unmatched cells at the end
        slide_cells.extend(unmatched)

        new_text = _reconstruct(preamble, slide_cells)
        path.write_text(new_text, encoding="utf-8")

        comp.unlink()
        result.companion_deleted = True

    return result
