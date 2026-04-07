"""Language tools for bilingual slide files.

Provides a single-language view of bilingual percent-format ``.py``
slide files with line-number annotations mapping back to the original,
and sync suggestions for asymmetric bilingual edits.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
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


# ===================================================================
# suggest_sync — detect asymmetric bilingual edits
# ===================================================================


@dataclass
class SyncSuggestion:
    """One suggestion for a cell that needs syncing."""

    type: str  # "modified", "added", or "deleted"
    slide_id: str | None = None
    source_line: int | None = None
    source_content: str | None = None
    target_line: int | None = None
    target_content_current: str | None = None
    suggestion: str = ""


@dataclass
class SyncResult:
    """Result of comparing a slide file's languages against git HEAD."""

    file: str
    source_language: str
    target_language: str
    pairing_method: str  # "slide_id", "positional", or "mixed"
    suggestions: list[SyncSuggestion] = field(default_factory=list)
    unmodified_pairs: int = 0
    sync_needed: bool = False


def suggest_sync(
    path: Path,
    *,
    source_language: str | None = None,
) -> SyncResult:
    """Compare a slide file against git HEAD and suggest bilingual sync updates.

    Parses the current and committed versions, pairs DE/EN cells
    (by ``slide_id`` when available, otherwise positionally), and
    identifies cells that changed in one language without a corresponding
    change in the other.

    Args:
        path: Path to the ``.py`` slide file.
        source_language: The language that was edited (``"de"`` or ``"en"``).
            If ``None``, auto-detects the language with more changes.

    Returns:
        A :class:`SyncResult` with suggestions for the target language.
    """
    current_text = path.read_text(encoding="utf-8")
    head_text = _git_head_content(path)

    current_cells = parse_cells(current_text)
    head_cells = parse_cells(head_text) if head_text is not None else []

    # Separate cells by language
    cur_de = _lang_cells(current_cells, "de")
    cur_en = _lang_cells(current_cells, "en")
    head_de = _lang_cells(head_cells, "de")
    head_en = _lang_cells(head_cells, "en")

    # Auto-detect source language if not specified
    if source_language is None:
        source_language = _detect_source_language(cur_de, cur_en, head_de, head_en)

    if source_language == "de":
        src_cur, tgt_cur = cur_de, cur_en
        src_head, tgt_head = head_de, head_en
        target_language = "en"
    else:
        src_cur, tgt_cur = cur_en, cur_de
        src_head, tgt_head = head_en, head_de
        target_language = "de"

    # Determine pairing method and build pairs
    pairing_method, src_pairs, tgt_pairs = _pair_cells(src_cur, tgt_cur)

    # Build content maps for HEAD versions (keyed same as current pairs)
    src_head_map = _build_content_map(src_head)
    tgt_head_map = _build_content_map(tgt_head)

    suggestions: list[SyncSuggestion] = []
    unmodified = 0

    # Check each source cell for changes
    matched_tgt_keys: set[str] = set()
    for key, src_cell in src_pairs.items():
        tgt_cell = tgt_pairs.get(key)
        if tgt_cell is not None:
            matched_tgt_keys.add(key)

        src_changed = _cell_changed(src_cell, src_head_map)
        tgt_changed = _cell_changed(tgt_cell, tgt_head_map) if tgt_cell else False

        if src_changed and not tgt_changed:
            if tgt_cell is not None:
                # Source modified, target not → suggest update
                suggestions.append(
                    SyncSuggestion(
                        type="modified",
                        slide_id=src_cell.metadata.slide_id,
                        source_line=src_cell.line_number,
                        source_content=_cell_preview(src_cell),
                        target_line=tgt_cell.line_number,
                        target_content_current=_cell_preview(tgt_cell),
                        suggestion=(
                            f"{source_language.upper()} cell at line {src_cell.line_number} "
                            f"was modified. Update the corresponding "
                            f"{target_language.upper()} cell at line {tgt_cell.line_number}."
                        ),
                    )
                )
            else:
                # Source cell exists but no target pair → added
                suggestions.append(
                    SyncSuggestion(
                        type="added",
                        slide_id=src_cell.metadata.slide_id,
                        source_line=src_cell.line_number,
                        source_content=_cell_preview(src_cell),
                        suggestion=(
                            f"New {source_language.upper()} cell at line "
                            f"{src_cell.line_number}. Add a corresponding "
                            f"{target_language.upper()} cell."
                        ),
                    )
                )
        elif not src_changed and not tgt_changed and tgt_cell is not None:
            unmodified += 1
        # If both changed, we count as unmodified (already in sync)
        elif src_changed and tgt_changed:
            unmodified += 1

    # Check for source cells with no target that weren't already reported
    # (cells that existed in HEAD source but are now gone)
    if head_text is not None:
        head_src_pairs, _ = _pair_cells_single(src_head)
        for key in head_src_pairs:
            if key not in src_pairs:
                # Source cell was deleted
                tgt_cell = tgt_pairs.get(key)
                if tgt_cell is not None:
                    suggestions.append(
                        SyncSuggestion(
                            type="deleted",
                            slide_id=tgt_cell.metadata.slide_id
                            or (key if not key.startswith("pos:") else None),
                            target_line=tgt_cell.line_number,
                            target_content_current=_cell_preview(tgt_cell),
                            suggestion=(
                                f"{source_language.upper()} cell was deleted. "
                                f"Consider deleting the {target_language.upper()} "
                                f"cell at line {tgt_cell.line_number}."
                            ),
                        )
                    )

    return SyncResult(
        file=str(path),
        source_language=source_language,
        target_language=target_language,
        pairing_method=pairing_method,
        suggestions=suggestions,
        unmodified_pairs=unmodified,
        sync_needed=len(suggestions) > 0,
    )


# -------------------------------------------------------------------
# Internal helpers
# -------------------------------------------------------------------


def _git_head_content(path: Path) -> str | None:
    """Get the file content from git HEAD.

    Returns ``None`` if the file is untracked or not in a git repo.
    """
    try:
        # Find the repo root relative path
        repo_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(path.parent),
            check=True,
        ).stdout.strip()

        rel_path = path.resolve().relative_to(Path(repo_root).resolve())
        # Use forward slashes for git
        git_path = str(rel_path).replace("\\", "/")

        result = subprocess.run(
            ["git", "show", f"HEAD:{git_path}"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None


def _lang_cells(cells: list[Cell], lang: str) -> list[Cell]:
    """Filter cells to those with a specific language attribute."""
    return [c for c in cells if c.lang == lang]


def _detect_source_language(
    cur_de: list[Cell],
    cur_en: list[Cell],
    head_de: list[Cell],
    head_en: list[Cell],
) -> str:
    """Detect which language has more changes from HEAD."""
    de_changes = _count_changes(cur_de, head_de)
    en_changes = _count_changes(cur_en, head_en)
    return "de" if de_changes >= en_changes else "en"


def _count_changes(current: list[Cell], head: list[Cell]) -> int:
    """Count the number of cells that differ between current and HEAD."""
    head_contents = {_cell_content_key(c) for c in head}
    current_contents = {_cell_content_key(c) for c in current}
    # Symmetric difference gives us both new and removed cells
    return len(head_contents ^ current_contents)


def _cell_content_key(cell: Cell) -> str:
    """Create a content key for a cell (header + content)."""
    return f"{cell.header}\n{cell.content}"


def _pair_cells(
    src_cells: list[Cell],
    tgt_cells: list[Cell],
) -> tuple[str, dict[str, Cell], dict[str, Cell]]:
    """Pair source and target cells by slide_id or position.

    Returns (pairing_method, src_pairs, tgt_pairs) where both dicts
    use the same keys.
    """
    src_has_ids = any(c.metadata.slide_id for c in src_cells)
    tgt_has_ids = any(c.metadata.slide_id for c in tgt_cells)

    if src_has_ids and tgt_has_ids:
        src_map: dict[str, Cell] = {}
        tgt_map: dict[str, Cell] = {}
        src_pos_count = 0
        tgt_pos_count = 0

        for c in src_cells:
            if c.metadata.slide_id:
                src_map[c.metadata.slide_id] = c
            else:
                src_map[f"pos:{src_pos_count}"] = c
                src_pos_count += 1

        for c in tgt_cells:
            if c.metadata.slide_id:
                tgt_map[c.metadata.slide_id] = c
            else:
                tgt_map[f"pos:{tgt_pos_count}"] = c
                tgt_pos_count += 1

        has_positional = src_pos_count > 0 or tgt_pos_count > 0
        method = "mixed" if has_positional else "slide_id"
        return method, src_map, tgt_map

    # Positional pairing
    src_map = {f"pos:{i}": c for i, c in enumerate(src_cells)}
    tgt_map = {f"pos:{i}": c for i, c in enumerate(tgt_cells)}
    return "positional", src_map, tgt_map


def _pair_cells_single(cells: list[Cell]) -> tuple[dict[str, Cell], str]:
    """Create a key→cell map for a single language's cells."""
    result: dict[str, Cell] = {}
    pos = 0
    for c in cells:
        if c.metadata.slide_id:
            result[c.metadata.slide_id] = c
        else:
            result[f"pos:{pos}"] = c
            pos += 1
    return result, "slide_id" if pos == 0 and result else "positional"


def _build_content_map(cells: list[Cell]) -> dict[str, str]:
    """Build a key→content map for cells (for HEAD comparison)."""
    result: dict[str, str] = {}
    pos = 0
    for c in cells:
        if c.metadata.slide_id:
            result[c.metadata.slide_id] = _cell_content_key(c)
        else:
            result[f"pos:{pos}"] = _cell_content_key(c)
            pos += 1
    return result


def _cell_changed(cell: Cell | None, head_map: dict[str, str]) -> bool:
    """Check if a cell's content differs from its HEAD version."""
    if cell is None:
        return False

    key = cell.metadata.slide_id or None
    if key is None:
        # For positional cells, we can't reliably match to HEAD
        # Fall back: check if content exists anywhere in HEAD
        content = _cell_content_key(cell)
        return content not in head_map.values()

    head_content = head_map.get(key)
    if head_content is None:
        # Cell didn't exist in HEAD → it's new (changed)
        return True
    return _cell_content_key(cell) != head_content


def _cell_preview(cell: Cell) -> str:
    """Create a short preview of a cell (header + first few lines)."""
    lines = [cell.header]
    content_lines = cell.content.split("\n") if cell.content else []
    lines.extend(content_lines[:3])
    if len(content_lines) > 3:
        lines.append("...")
    return "\n".join(lines)
