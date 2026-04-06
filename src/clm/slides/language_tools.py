"""Language tools for bilingual slide files.

Provides a single-language view of bilingual percent-format ``.py``
slide files with line-number annotations mapping back to the original.
"""

from __future__ import annotations

from pathlib import Path

from clm.notebooks.slide_parser import Cell, parse_cells


def get_language_view(
    path: Path,
    language: str,
    *,
    include_voiceover: bool = False,
    include_notes: bool = False,
) -> str:
    """Extract a single-language view of a slide file.

    Filters cells to keep only those in *language* (plus
    language-neutral cells), and prepends ``# [original line N]``
    annotations so edits can be mapped back to the bilateral file.

    Args:
        path: Path to the ``.py`` slide file.
        language: Which language to extract (``"de"`` or ``"en"``).
        include_voiceover: Include voiceover cells (default ``False``).
        include_notes: Include speaker-notes cells (default ``False``).

    Returns:
        Filtered file content as a string with line-number annotations.
    """
    text = path.read_text(encoding="utf-8")
    raw_lines = text.split("\n")
    cells = parse_cells(text)

    kept = _filter_cells(cells, language, include_voiceover, include_notes)
    return _reconstruct(kept, raw_lines)


def _filter_cells(
    cells: list[Cell],
    language: str,
    include_voiceover: bool,
    include_notes: bool,
) -> list[Cell]:
    """Return cells that belong to the requested language view."""
    result: list[Cell] = []
    for cell in cells:
        # Skip cells in the other language
        if cell.lang is not None and cell.lang != language:
            continue

        # Skip narrative cells unless explicitly requested
        if "voiceover" in cell.tags and not include_voiceover:
            continue
        if "notes" in cell.tags and not include_notes:
            continue

        result.append(cell)
    return result


def _reconstruct(cells: list[Cell], raw_lines: list[str]) -> str:
    """Reconstruct file text from kept cells with line annotations."""
    parts: list[str] = []

    for cell in cells:
        # j2 cells (header macros) are emitted without annotation
        if cell.metadata.is_j2:
            parts.append(cell.header)
            if cell.content:
                parts.append(cell.content)
            continue

        # Add line-number annotation
        parts.append(f"# [original line {cell.line_number}]")

        # Emit the cell's raw lines from the original file
        cell_lines = _extract_raw_cell(cell, raw_lines)
        parts.append(cell_lines)

    return "\n".join(parts) + "\n" if parts else ""


def _extract_raw_cell(cell: Cell, raw_lines: list[str]) -> str:
    """Extract the raw text of a cell from the original file lines.

    Uses the cell's ``line_number`` (1-based) as start and scans
    forward until the next cell boundary or end of file.
    """
    start = cell.line_number - 1  # 0-based index
    end = start + 1
    while end < len(raw_lines):
        line = raw_lines[end]
        if line.startswith("# %%") or line.startswith("# j2 ") or line.startswith("# {{ "):
            break
        end += 1

    # Strip trailing blank lines from the cell
    while end > start + 1 and raw_lines[end - 1].strip() == "":
        end -= 1

    return "\n".join(raw_lines[start:end])
