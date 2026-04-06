"""Parse percent-format .py slide files into structured representations.

This module parses .py files that use the Jupyter percent-format convention
(cells delimited by ``# %%``) with CLM-specific metadata (language tags,
slide/subslide/notes tags, etc.) into a structured list of cells and
slide groups.

The parser is used by:
- The voiceover pipeline (to match video frames against slide content)
- The polish command (to locate and update speaker notes)
- The slide writer (to know where to insert/replace cells)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CellMetadata:
    """Metadata extracted from a cell header line."""

    cell_type: str  # "markdown" or "code"
    lang: str | None = None
    tags: list[str] = field(default_factory=list)
    is_j2: bool = False
    raw_header: str = ""
    slide_id: str | None = None
    for_slide: str | None = None

    @property
    def is_slide(self) -> bool:
        return "slide" in self.tags

    @property
    def is_subslide(self) -> bool:
        return "subslide" in self.tags

    @property
    def is_slide_start(self) -> bool:
        return self.is_slide or self.is_subslide

    @property
    def is_narrative(self) -> bool:
        return "notes" in self.tags or "voiceover" in self.tags

    @property
    def slide_type(self) -> str | None:
        if "slide" in self.tags:
            return "slide"
        if "subslide" in self.tags:
            return "subslide"
        return None


@dataclass
class Cell:
    """A single cell from a percent-format .py file."""

    line_number: int
    header: str
    content: str
    metadata: CellMetadata

    @property
    def cell_type(self) -> str:
        return self.metadata.cell_type

    @property
    def lang(self) -> str | None:
        return self.metadata.lang

    @property
    def tags(self) -> list[str]:
        return self.metadata.tags

    def text_content(self) -> str:
        """Extract readable text from the cell, stripping comment prefixes and formatting."""
        if self.metadata.cell_type == "markdown":
            return _strip_markdown(self.content)
        return _strip_code_comments(self.content)


@dataclass
class SlideGroup:
    """A visual slide: the unit of presentation.

    A slide group starts with a cell tagged ``slide`` or ``subslide`` and
    includes all subsequent cells until the next slide/subslide begins.
    This is the unit that maps to a single visual state in the presentation
    and gets one block of speaker notes.
    """

    index: int
    slide_type: str  # "slide", "subslide", or "header"
    lang: str | None
    title: str
    cells: list[Cell] = field(default_factory=list)
    notes_cells: list[Cell] = field(default_factory=list)

    @property
    def text_content(self) -> str:
        """All readable text from non-notes cells, for OCR matching."""
        parts = []
        for cell in self.cells:
            text = cell.text_content()
            if text:
                parts.append(text)
        return " ".join(parts)

    @property
    def notes_text(self) -> str:
        """Existing speaker notes text, if any."""
        parts = []
        for cell in self.notes_cells:
            text = cell.text_content()
            if text:
                parts.append(text)
        return "\n".join(parts)

    @property
    def has_notes(self) -> bool:
        return len(self.notes_cells) > 0


def parse_cell_header(header: str) -> CellMetadata:
    """Extract metadata from a cell header line.

    Examples::

        # %% [markdown] lang="de" tags=["slide"]
        # %% tags=["keep"]
        # %%
        # j2 from 'macros.j2' import header
    """
    is_j2 = header.startswith("# j2 ") or header.startswith("# {{ ")

    if is_j2:
        return CellMetadata(
            cell_type="j2",
            is_j2=True,
            raw_header=header,
        )

    cell_type = "markdown" if "[markdown]" in header else "code"

    lang_match = re.search(r'lang="(\w+)"', header)
    lang = lang_match.group(1) if lang_match else None

    # Parse tags - handle tags=["slide"], tags=["slide", "keep"], etc.
    tags: list[str] = []
    tags_match = re.search(r"tags=\[([^\]]*)\]", header)
    if tags_match:
        tags_content = tags_match.group(1)
        tags = re.findall(r'"([^"]*)"', tags_content)

    # Parse slide_id and for_slide metadata
    slide_id_match = re.search(r'slide_id="([^"]*)"', header)
    slide_id = slide_id_match.group(1) if slide_id_match else None

    for_slide_match = re.search(r'for_slide="([^"]*)"', header)
    for_slide = for_slide_match.group(1) if for_slide_match else None

    return CellMetadata(
        cell_type=cell_type,
        lang=lang,
        tags=tags,
        is_j2=False,
        raw_header=header,
        slide_id=slide_id,
        for_slide=for_slide,
    )


def parse_cells(text: str) -> list[Cell]:
    """Parse a percent-format .py file into a list of cells."""
    lines = text.split("\n")
    cells: list[Cell] = []
    current_header: str | None = None
    current_lines: list[str] = []
    current_line_number = 0

    for i, line in enumerate(lines, 1):
        if _is_cell_boundary(line):
            if current_header is not None:
                content = "\n".join(current_lines).strip()
                metadata = parse_cell_header(current_header)
                cells.append(
                    Cell(
                        line_number=current_line_number,
                        header=current_header,
                        content=content,
                        metadata=metadata,
                    )
                )
            current_header = line
            current_lines = []
            current_line_number = i
        else:
            current_lines.append(line)

    # Final cell
    if current_header is not None:
        content = "\n".join(current_lines).strip()
        metadata = parse_cell_header(current_header)
        cells.append(
            Cell(
                line_number=current_line_number,
                header=current_header,
                content=content,
                metadata=metadata,
            )
        )

    return cells


def parse_slides(
    path: Path,
    lang: str,
    *,
    include_header: bool = True,
) -> list[SlideGroup]:
    """Parse a .py slide file into slide groups for a given language.

    Args:
        path: Path to the .py slide file.
        lang: Target language ("de" or "en"). Only cells in this language
            (or language-neutral cells) are included.
        include_header: If True, recognize the j2 header macro as a
            synthetic "header" slide group (index 0).

    Returns:
        Ordered list of SlideGroup objects.
    """
    text = path.read_text(encoding="utf-8")
    cells = parse_cells(text)
    return group_slides(cells, lang, include_header=include_header)


def group_slides(
    cells: list[Cell],
    lang: str,
    *,
    include_header: bool = True,
) -> list[SlideGroup]:
    """Group parsed cells into slide groups for a given language."""
    groups: list[SlideGroup] = []
    current_group: SlideGroup | None = None

    # Check for j2 header macro at the start.
    # The header is typically two j2 cells: an import and a call.
    # We look for the call (which contains the title).
    if include_header and cells:
        header_cells: list[Cell] = []
        title = ""
        for cell in cells:
            if not cell.metadata.is_j2:
                break
            header_cells.append(cell)
            extracted = _extract_header_title(cell.header, lang)
            if extracted:
                title = extracted
        if header_cells:
            groups.append(
                SlideGroup(
                    index=0,
                    slide_type="header",
                    lang=None,
                    title=title,
                    cells=header_cells,
                )
            )

    for cell in cells:
        # Skip j2 directives (already handled above)
        if cell.metadata.is_j2:
            continue

        # Skip cells in the other language
        if cell.lang is not None and cell.lang != lang:
            continue

        is_narrative = cell.metadata.is_narrative
        is_slide_start = cell.metadata.is_slide_start

        if is_narrative:
            # Attach notes to the current group
            if current_group is not None:
                current_group.notes_cells.append(cell)
        elif is_slide_start:
            # Start a new group
            if current_group is not None and current_group not in groups:
                groups.append(current_group)

            title = _extract_title(cell)
            current_group = SlideGroup(
                index=len(groups),
                slide_type=cell.metadata.slide_type or "slide",
                lang=lang,
                title=title,
                cells=[cell],
            )
        elif current_group is not None:
            # Accumulate into current group
            current_group.cells.append(cell)

    # Final group
    if current_group is not None and current_group not in groups:
        groups.append(current_group)

    return groups


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_cell_boundary(line: str) -> bool:
    """Check if a line starts a new cell."""
    return line.startswith("# %%") or line.startswith("# j2 ") or line.startswith("# {{ ")


def _strip_markdown(content: str) -> str:
    """Strip comment prefixes and markdown formatting from a markdown cell."""
    parts = []
    for line in content.split("\n"):
        # Remove comment prefix
        stripped = line.lstrip("# ").rstrip()
        if stripped.startswith("#"):
            # This is still a comment prefix artifact, strip more
            stripped = stripped.lstrip("# ").strip()
        # Remove markdown formatting
        stripped = re.sub(r"[*_`]", "", stripped)
        # Remove markdown links: [text](url) -> text
        stripped = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", stripped)
        # Remove HTML tags
        stripped = re.sub(r"<[^>]+>", "", stripped)
        # Remove LaTeX math delimiters
        stripped = re.sub(r"\$[^$]*\$", "", stripped)
        if stripped:
            parts.append(stripped)
    return " ".join(parts)


def _strip_code_comments(content: str) -> str:
    """Strip comment prefixes from code content."""
    parts = []
    for line in content.split("\n"):
        stripped = line.lstrip("# ").strip()
        if stripped:
            parts.append(stripped)
    return " ".join(parts)


def _extract_title(cell: Cell) -> str:
    """Extract the title from a slide cell's content."""
    if cell.cell_type != "markdown":
        return ""
    for line in cell.content.split("\n"):
        stripped = line.lstrip("# ").strip()
        if stripped.startswith("#"):
            # Markdown heading
            return stripped.lstrip("# ").strip()
        if stripped and not stripped.startswith("-") and not stripped.startswith("*"):
            # First non-empty, non-list line
            return stripped
    return ""


def _extract_header_title(header_line: str, lang: str) -> str:
    """Extract the title from a j2 header macro call.

    Handles patterns like:
        # {{ header("German Title", "English Title") }}
    """
    match = re.search(r'header\("([^"]*)"(?:,\s*"([^"]*)")?\)', header_line)
    if match:
        de_title = match.group(1)
        en_title = match.group(2) or de_title
        return en_title if lang == "en" else de_title
    return ""
