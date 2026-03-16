"""Insert or update speaker notes cells in percent-format .py slide files.

This module modifies .py slide files by inserting new ``tags=["notes"]`` cells
or replacing existing ones. It operates on the raw text to preserve exact
formatting of all untouched content.

Used by:
- The voiceover pipeline (to write transcript-derived notes)
- The polish command (to update polished notes)
"""

from __future__ import annotations

import logging
from pathlib import Path

from clm.notebooks.slide_parser import Cell, parse_cells

logger = logging.getLogger(__name__)


def format_notes_cell(text: str, lang: str) -> str:
    """Format notes text as a percent-format notes cell.

    Args:
        text: The notes text (plain text, one thought per line).
        lang: Language code ("de" or "en").

    Returns:
        Complete cell text including the header line.
    """
    header = f'# %% [markdown] lang="{lang}" tags=["notes"]'
    lines = text.strip().split("\n")
    body_lines = ["#"]  # blank comment line after header
    for line in lines:
        stripped = line.strip()
        if not stripped:
            body_lines.append("#")
        elif stripped.startswith("- ") or stripped.startswith("**["):
            body_lines.append(f"# {stripped}")
        else:
            body_lines.append(f"# - {stripped}")
    return header + "\n" + "\n".join(body_lines)


def update_notes(
    text: str,
    notes_map: dict[int, str],
    lang: str,
) -> str:
    """Update or insert notes cells in a percent-format .py file.

    Args:
        text: The complete file text.
        notes_map: Mapping of slide index to notes text. Slide indices
            correspond to the order from ``group_slides()`` with the same
            language.
        lang: Target language ("de" or "en").

    Returns:
        Modified file text with updated/inserted notes.
    """
    if not notes_map:
        return text

    cells = parse_cells(text)
    if not cells:
        return text

    # Build a map of slide index -> (slide cells, notes cells, insertion line)
    slide_info = _map_slides_to_cells(cells, lang)

    # Process in reverse order so line numbers remain valid
    lines = text.split("\n")
    for slide_idx in sorted(notes_map.keys(), reverse=True):
        if slide_idx not in slide_info:
            logger.warning("Slide index %d not found in file, skipping", slide_idx)
            continue

        notes_text = notes_map[slide_idx]
        info = slide_info[slide_idx]

        notes_cell_text = format_notes_cell(notes_text, lang)

        if info["existing_notes_lines"]:
            # Replace existing notes cell
            start_line, end_line = info["existing_notes_lines"]
            lines[start_line:end_line] = notes_cell_text.split("\n")
        else:
            # Insert new notes cell after the slide group
            insert_at = info["insert_after_line"]
            # Add blank line before and after for readability
            insert_lines = ["", notes_cell_text, ""]
            for j, new_line in enumerate(insert_lines):
                lines.insert(insert_at + j, new_line)

    return "\n".join(lines)


def write_notes(
    path: Path,
    notes_map: dict[int, str],
    lang: str,
    *,
    output_path: Path | None = None,
) -> Path:
    """Update notes in a .py slide file and write the result.

    Args:
        path: Path to the source .py file.
        notes_map: Mapping of slide index to notes text.
        lang: Target language ("de" or "en").
        output_path: If given, write to this path instead of modifying in-place.

    Returns:
        Path to the written file.
    """
    text = path.read_text(encoding="utf-8")
    updated = update_notes(text, notes_map, lang)

    dest = output_path or path
    dest.write_text(updated, encoding="utf-8")
    logger.info("Wrote notes for %d slides to %s", len(notes_map), dest)
    return dest


def _map_slides_to_cells(
    cells: list[Cell],
    lang: str,
) -> dict[int, dict]:
    """Map slide indices to their cell positions and notes locations.

    Returns a dict of slide_index -> {
        "existing_notes_lines": (start, end) or None,
        "insert_after_line": line number to insert after,
    }

    Line numbers are 0-based indices into the split lines array.
    """
    # First, identify slide groups in the target language
    # (mirrors the logic of group_slides but tracks positions)
    slide_groups: list[dict] = []
    current_group: dict | None = None

    for i, cell in enumerate(cells):
        # Skip j2 directives
        if cell.metadata.is_j2:
            continue
        # Skip other-language cells
        if cell.lang is not None and cell.lang != lang:
            continue

        is_narrative = cell.metadata.is_narrative
        is_slide_start = cell.metadata.is_slide_start

        if is_narrative:
            if current_group is not None:
                current_group["notes_cells"].append(cell)
        elif is_slide_start:
            if current_group is not None:
                slide_groups.append(current_group)
            current_group = {
                "cells": [cell],
                "notes_cells": [],
                "cell_index": i,
            }
        elif current_group is not None:
            current_group["cells"].append(cell)

    if current_group is not None:
        slide_groups.append(current_group)

    # Check if there's a j2 header at the start (occupies index 0)
    has_header = cells and cells[0].metadata.is_j2
    start_index = 1 if has_header else 0

    # Build the result map
    result: dict[int, dict] = {}

    for group_idx, group in enumerate(slide_groups):
        slide_index = group_idx + start_index

        if group["notes_cells"]:
            # Existing notes: compute the line range to replace
            first_notes = group["notes_cells"][0]
            last_notes = group["notes_cells"][-1]
            start_line = first_notes.line_number - 1  # 1-based to 0-based

            # Find the end of the last notes cell
            end_line = _find_cell_end(cells, last_notes)

            result[slide_index] = {
                "existing_notes_lines": (start_line, end_line),
                "insert_after_line": end_line,
            }
        else:
            # No existing notes: insert after the last cell of this group
            last_cell = group["cells"][-1]
            end_line = _find_cell_end(cells, last_cell)

            result[slide_index] = {
                "existing_notes_lines": None,
                "insert_after_line": end_line,
            }

    return result


def _find_cell_end(cells: list[Cell], target_cell: Cell) -> int:
    """Find the 0-based line index where a cell ends.

    The cell ends at the line before the next cell starts, or at
    the end of the file.
    """
    found = False
    for cell in cells:
        if found:
            # Next cell starts at cell.line_number (1-based)
            return cell.line_number - 1  # 0-based, exclusive
        if cell is target_cell:
            found = True

    # target_cell is the last cell — return a sentinel meaning "end of file"
    # We need to know total line count, but we don't have the raw text here.
    # Use a large number; callers handle this correctly since we operate on
    # the lines list which naturally bounds the index.
    return target_cell.line_number + _estimate_cell_lines(target_cell)


def _estimate_cell_lines(cell: Cell) -> int:
    """Estimate the number of lines in a cell (header + content)."""
    if not cell.content:
        return 1
    return 1 + cell.content.count("\n") + 1
