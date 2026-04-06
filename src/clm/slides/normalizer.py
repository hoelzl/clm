"""Slide file normalization engine.

Applies mechanical fixes to percent-format ``.py`` slide files:

- **Tag migration**: ``alt`` → ``completed`` after ``start`` cells
- **Workshop tag insertion**: adds ``workshop`` to workshop heading cells
- **Interleaving normalization**: reorders cells so paired DE/EN cells
  are adjacent

Uses a three-tier strategy for interleaving:

- Tier 1: Structural count check (DE vs EN per category)
- Tier 2: Positional pairing with similarity verification
- Tier 3: Report for issues that need manual resolution
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from clm.core.topic_resolver import build_topic_map, find_slide_files
from clm.infrastructure.utils.path_utils import is_slides_file
from clm.notebooks.slide_parser import CellMetadata, parse_cell_header

# ---------------------------------------------------------------------------
# Raw cell representation (preserves exact file text for lossless round-trip)
# ---------------------------------------------------------------------------


def _is_cell_boundary(line: str) -> bool:
    return line.startswith("# %%") or line.startswith("# j2 ") or line.startswith("# {{ ")


@dataclass
class _RawCell:
    """A cell preserving its raw text for lossless reconstruction."""

    lines: list[str]
    line_number: int  # 1-based
    metadata: CellMetadata

    @property
    def header(self) -> str:
        return self.lines[0]

    @header.setter
    def header(self, value: str) -> None:
        self.lines[0] = value

    @property
    def content_text(self) -> str:
        return "\n".join(self.lines[1:])


def _split_raw_cells(text: str) -> tuple[str, list[_RawCell]]:
    """Split file text into preamble (before first cell) and raw cells."""
    lines = text.split("\n")
    cells: list[_RawCell] = []
    preamble_lines: list[str] = []
    current_lines: list[str] = []
    current_line_number = 0
    in_cell = False

    for i, line in enumerate(lines):
        if _is_cell_boundary(line):
            if in_cell:
                cells.append(
                    _RawCell(
                        lines=current_lines,
                        line_number=current_line_number,
                        metadata=parse_cell_header(current_lines[0]),
                    )
                )
            current_lines = [line]
            current_line_number = i + 1
            in_cell = True
        else:
            if in_cell:
                current_lines.append(line)
            else:
                preamble_lines.append(line)

    if in_cell:
        cells.append(
            _RawCell(
                lines=current_lines,
                line_number=current_line_number,
                metadata=parse_cell_header(current_lines[0]),
            )
        )

    preamble = "\n".join(preamble_lines) if preamble_lines else ""
    return preamble, cells


def _reconstruct(preamble: str, cells: list[_RawCell]) -> str:
    """Reconstruct file text from preamble and cells."""
    parts: list[str] = []
    if preamble:
        parts.append(preamble)
    for cell in cells:
        parts.append("\n".join(cell.lines))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Change:
    """A single change applied (or proposed in dry-run) by the normalizer."""

    file: str
    operation: str  # "tag_migration", "workshop_tags", "interleaving"
    line: int
    description: str


@dataclass
class ReviewItem:
    """An issue found during normalization that requires manual review."""

    file: str
    issue: str  # "count_mismatch", "similarity_failure"
    suggestion: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class NormalizationResult:
    """Result of normalizing one or more slide files."""

    files_modified: int = 0
    changes: list[Change] = field(default_factory=list)
    review_items: list[ReviewItem] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.changes and not self.review_items:
            return "clean"
        if self.review_items:
            return "partial"
        return "applied"

    @property
    def summary(self) -> str:
        parts: list[str] = []
        if self.files_modified:
            parts.append(
                f"{self.files_modified} file{'s' if self.files_modified != 1 else ''} modified"
            )
        op_counts: dict[str, int] = {}
        for c in self.changes:
            op_counts[c.operation] = op_counts.get(c.operation, 0) + 1
        for op, count in sorted(op_counts.items()):
            parts.append(f"{count} {op.replace('_', ' ')}{'s' if count != 1 else ''}")
        if self.review_items:
            ri = len(self.review_items)
            parts.append(f"{ri} item{'s' if ri != 1 else ''} for review")
        if not parts:
            parts.append("no changes needed")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

ALL_OPERATIONS = frozenset({"tag_migration", "workshop_tags", "interleaving"})


# ---------------------------------------------------------------------------
# Tag migration: alt → completed after start cells
# ---------------------------------------------------------------------------


def _apply_tag_migration(cells: list[_RawCell], file_path: str) -> list[Change]:
    changes: list[Change] = []
    prev_has_start = False

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2:
            prev_has_start = False
            continue

        if prev_has_start and "alt" in meta.tags:
            new_header = cell.header.replace('"alt"', '"completed"')
            cell.header = new_header
            cell.metadata = parse_cell_header(new_header)
            changes.append(
                Change(
                    file=file_path,
                    operation="tag_migration",
                    line=cell.line_number,
                    description='Renamed "alt" -> "completed" (follows start cell)',
                )
            )

        prev_has_start = "start" in meta.tags

    return changes


# ---------------------------------------------------------------------------
# Workshop tags: add 'workshop' to workshop heading cells
# ---------------------------------------------------------------------------

_WORKSHOP_RE = re.compile(r"^#\s+##\s+(Workshop|Mini-Workshop)\s*:", re.MULTILINE)


def _apply_workshop_tags(cells: list[_RawCell], file_path: str) -> list[Change]:
    changes: list[Change] = []

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "markdown":
            continue
        if "workshop" in meta.tags:
            continue

        if _WORKSHOP_RE.search(cell.content_text):
            new_header = _add_tag_to_header(cell.header, "workshop")
            cell.header = new_header
            cell.metadata = parse_cell_header(new_header)
            changes.append(
                Change(
                    file=file_path,
                    operation="workshop_tags",
                    line=cell.line_number,
                    description="Added 'workshop' tag to workshop heading cell",
                )
            )

    return changes


def _add_tag_to_header(header: str, tag: str) -> str:
    """Add a tag to a cell header line."""
    tags_match = re.search(r"tags=\[([^\]]*)\]", header)
    if tags_match:
        existing = tags_match.group(1).strip()
        if existing:
            new_tags = f'{existing}, "{tag}"'
        else:
            new_tags = f'"{tag}"'
        return header[: tags_match.start()] + f"tags=[{new_tags}]" + header[tags_match.end() :]
    return header.rstrip() + f' tags=["{tag}"]'


# ---------------------------------------------------------------------------
# Interleaving: three-tier DE/EN reordering
# ---------------------------------------------------------------------------

_PAIRING_CATEGORIES = ("markdown", "code", "voiceover", "notes")


def _classify_cell(cell: _RawCell) -> str:
    """Classify a cell for interleaving purposes.

    Returns one of: ``"j2"``, ``"shared"``, ``"markdown"``, ``"code"``,
    ``"voiceover"``, ``"notes"``.
    """
    meta = cell.metadata
    if meta.is_j2:
        return "j2"
    if meta.is_narrative:
        if "voiceover" in meta.tags:
            return "voiceover"
        return "notes"
    if meta.lang is None:
        return "shared"
    if meta.cell_type == "code":
        return "code"
    return "markdown"


def _apply_interleaving(
    cells: list[_RawCell], file_path: str
) -> tuple[list[_RawCell], list[Change], list[ReviewItem]]:
    """Reorder cells for proper DE/EN interleaving.

    Returns ``(reordered_cells, changes, review_items)``.
    """
    changes: list[Change] = []
    review_items: list[ReviewItem] = []

    # Classify and group cells by (language, category)
    de_by_cat: dict[str, list[int]] = {cat: [] for cat in _PAIRING_CATEGORIES}
    en_by_cat: dict[str, list[int]] = {cat: [] for cat in _PAIRING_CATEGORIES}

    for idx, cell in enumerate(cells):
        cat = _classify_cell(cell)
        if cat in _PAIRING_CATEGORIES:
            if cell.metadata.lang == "de":
                de_by_cat[cat].append(idx)
            elif cell.metadata.lang == "en":
                en_by_cat[cat].append(idx)

    # Tier 1 + 2: count check and positional pairing with similarity
    confident_pairs: dict[int, int] = {}  # de_idx → en_idx

    for cat in _PAIRING_CATEGORIES:
        de_indices = de_by_cat[cat]
        en_indices = en_by_cat[cat]

        if len(de_indices) != len(en_indices):
            # Tier 1 failure: count mismatch → report
            if de_indices or en_indices:
                review_items.append(
                    ReviewItem(
                        file=file_path,
                        issue="count_mismatch",
                        suggestion=(
                            f"DE has {len(de_indices)} {cat} cell(s) "
                            f"but EN has {len(en_indices)}. "
                            f"Review whether cells need to be added, removed, "
                            f"or reclassified."
                        ),
                        details={
                            "category": cat,
                            "de_count": len(de_indices),
                            "en_count": len(en_indices),
                            "de_cells": [
                                {
                                    "line": cells[i].line_number,
                                    "preview": _cell_preview(cells[i]),
                                }
                                for i in de_indices
                            ],
                            "en_cells": [
                                {
                                    "line": cells[i].line_number,
                                    "preview": _cell_preview(cells[i]),
                                }
                                for i in en_indices
                            ],
                        },
                    )
                )
            continue

        # Tier 2: positional pairing with similarity verification
        for pair_idx, (de_i, en_i) in enumerate(zip(de_indices, en_indices, strict=True)):
            failed = _check_similarity(cells[de_i], cells[en_i])
            if failed:
                review_items.append(
                    ReviewItem(
                        file=file_path,
                        issue="similarity_failure",
                        suggestion=_similarity_suggestion(cells[de_i], cells[en_i], failed),
                        details={
                            "pair_index": pair_idx,
                            "de_cell": {
                                "line": cells[de_i].line_number,
                                "tags": list(cells[de_i].metadata.tags),
                                "preview": _cell_preview(cells[de_i]),
                            },
                            "en_cell": {
                                "line": cells[en_i].line_number,
                                "tags": list(cells[en_i].metadata.tags),
                                "preview": _cell_preview(cells[en_i]),
                            },
                            "failed_checks": failed,
                        },
                    )
                )
            else:
                confident_pairs[de_i] = en_i

    # If no confident pairs, nothing to reorder
    confident_en = set(confident_pairs.values())
    if not confident_en:
        return cells, changes, review_items

    # Build reordered cell list:
    # Walk original order, skip confident EN cells (they get inserted
    # right after their DE partner).
    reordered: list[_RawCell] = []
    for idx, cell in enumerate(cells):
        if idx in confident_en:
            continue
        reordered.append(cell)
        if idx in confident_pairs:
            reordered.append(cells[confident_pairs[idx]])

    # Check whether the order actually changed
    if [id(c) for c in reordered] == [id(c) for c in cells]:
        return cells, changes, review_items

    # Count how many EN cells actually moved
    moved = sum(1 for de_i, en_i in confident_pairs.items() if en_i != de_i + 1)
    if moved:
        changes.append(
            Change(
                file=file_path,
                operation="interleaving",
                line=1,
                description=f"Reordered {moved} DE/EN pair(s) to be adjacent",
            )
        )

    return reordered, changes, review_items


# ---------------------------------------------------------------------------
# Similarity checks (Tier 2)
# ---------------------------------------------------------------------------


def _check_similarity(de_cell: _RawCell, en_cell: _RawCell) -> list[str]:
    """Verify structural similarity between a DE/EN pair.

    Returns a list of failed check names (empty = similar).
    """
    failed: list[str] = []
    de_meta = de_cell.metadata
    en_meta = en_cell.metadata

    # Tags check (ignoring order)
    if set(de_meta.tags) != set(en_meta.tags):
        failed.append("tags")

    # Line count check (within ±50%)
    de_lines = sum(1 for ln in de_cell.lines[1:] if ln.strip())
    en_lines = sum(1 for ln in en_cell.lines[1:] if ln.strip())
    if de_lines > 0 and en_lines > 0:
        ratio = min(de_lines, en_lines) / max(de_lines, en_lines)
        if ratio < 0.5:
            failed.append("line_count")
    elif (de_lines == 0) != (en_lines == 0) and max(de_lines, en_lines) > 2:
        failed.append("line_count")

    if de_meta.cell_type == "markdown" and en_meta.cell_type == "markdown":
        # Heading level check
        de_level = _heading_level(de_cell)
        en_level = _heading_level(en_cell)
        if de_level != en_level and (de_level > 0 or en_level > 0):
            failed.append("heading_level")

        # Bullet count check (within ±2)
        de_bullets = _bullet_count(de_cell)
        en_bullets = _bullet_count(en_cell)
        if abs(de_bullets - en_bullets) > 2:
            failed.append("bullet_count")

    if de_meta.cell_type == "code" and en_meta.cell_type == "code":
        de_names = _code_names(de_cell)
        en_names = _code_names(en_cell)
        if de_names != en_names and (de_names or en_names):
            failed.append("code_structure")

    return failed


def _heading_level(cell: _RawCell) -> int:
    """Extract the markdown heading level (0 = no heading)."""
    for line in cell.lines[1:]:
        # Strip Python comment prefix
        if line.startswith("# "):
            inner = line[2:]
        elif line.startswith("#"):
            inner = line[1:]
        else:
            continue
        inner = inner.lstrip()
        if inner.startswith("#"):
            level = 0
            for ch in inner:
                if ch == "#":
                    level += 1
                else:
                    break
            return level
    return 0


def _bullet_count(cell: _RawCell) -> int:
    """Count top-level list items in a markdown cell."""
    count = 0
    for line in cell.lines[1:]:
        if line.startswith("# - ") or line.startswith("# * "):
            count += 1
        elif re.match(r"^# \d+\.\s", line):
            count += 1
    return count


_DEF_RE = re.compile(r"^(?:def|class)\s+(\w+)")


def _code_names(cell: _RawCell) -> frozenset[str]:
    """Extract function/class names defined in a code cell."""
    names: set[str] = set()
    for line in cell.lines[1:]:
        m = _DEF_RE.match(line.strip())
        if m:
            names.add(m.group(1))
    return frozenset(names)


def _cell_preview(cell: _RawCell, max_len: int = 60) -> str:
    """Return a brief preview of a cell's content."""
    for line in cell.lines[1:]:
        text = line.strip()
        if text:
            if len(text) > max_len:
                return text[:max_len] + "..."
            return text
    return ""


def _similarity_suggestion(de_cell: _RawCell, en_cell: _RawCell, failed: list[str]) -> str:
    """Generate a human-readable suggestion for a similarity failure."""
    parts: list[str] = []
    if "tags" in failed:
        parts.append(
            f"Tags differ: DE has {sorted(de_cell.metadata.tags)} "
            f"but EN has {sorted(en_cell.metadata.tags)}"
        )
    if "heading_level" in failed:
        parts.append("Heading levels differ")
    if "bullet_count" in failed:
        parts.append("Bullet counts differ significantly")
    if "code_structure" in failed:
        parts.append("Different function/class definitions")
    if "line_count" in failed:
        parts.append("Line counts differ by more than 50%")
    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# File discovery helpers
# ---------------------------------------------------------------------------


def _find_slide_files_recursive(path: Path) -> list[Path]:
    """Find slide files in a directory, recursively if needed."""
    # If it looks like a topic directory, use the targeted finder
    direct_files = find_slide_files(path)
    if direct_files:
        return direct_files

    # Otherwise, walk the tree (e.g., slides/ root or module directory)
    result: list[Path] = []
    for child in sorted(path.rglob("*.py")):
        if is_slides_file(child):
            result.append(child)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_file(
    path: Path,
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
) -> NormalizationResult:
    """Normalize a single slide file.

    Args:
        path: Path to the ``.py`` slide file.
        operations: Which operations to apply.  ``None`` means all.
        dry_run: If ``True``, preview changes without modifying files.

    Returns:
        A :class:`NormalizationResult` with changes and review items.
    """
    op_set = set(operations) if operations else set(ALL_OPERATIONS)
    if "all" in op_set:
        op_set = set(ALL_OPERATIONS)

    file_str = str(path)
    text = path.read_text(encoding="utf-8")
    preamble, cells = _split_raw_cells(text)

    all_changes: list[Change] = []
    all_review: list[ReviewItem] = []

    # Apply operations in deterministic order
    if "tag_migration" in op_set:
        all_changes.extend(_apply_tag_migration(cells, file_str))

    if "workshop_tags" in op_set:
        all_changes.extend(_apply_workshop_tags(cells, file_str))

    if "interleaving" in op_set:
        cells, interleave_changes, interleave_reviews = _apply_interleaving(cells, file_str)
        all_changes.extend(interleave_changes)
        all_review.extend(interleave_reviews)

    modified = False
    if all_changes and not dry_run:
        new_text = _reconstruct(preamble, cells)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            modified = True

    return NormalizationResult(
        files_modified=1 if modified else 0,
        changes=all_changes,
        review_items=all_review,
    )


def normalize_directory(
    path: Path,
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
) -> NormalizationResult:
    """Normalize all slide files in a directory (recursive).

    Args:
        path: Path to a topic directory, module directory, or ``slides/`` root.
        operations: Which operations to apply.  ``None`` means all.
        dry_run: If ``True``, preview changes without modifying files.
    """
    slide_files = _find_slide_files_recursive(path)
    combined = NormalizationResult()

    for sf in slide_files:
        result = normalize_file(sf, operations=operations, dry_run=dry_run)
        combined.files_modified += result.files_modified
        combined.changes.extend(result.changes)
        combined.review_items.extend(result.review_items)

    return combined


def normalize_course(
    course_spec_path: Path,
    slides_dir: Path,
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
) -> NormalizationResult:
    """Normalize all slides referenced by a course spec.

    Args:
        course_spec_path: Path to the course spec XML file.
        slides_dir: Path to the ``slides/`` directory.
        operations: Which operations to apply.  ``None`` means all.
        dry_run: If ``True``, preview changes without modifying files.
    """
    from clm.core.course_spec import CourseSpec

    spec = CourseSpec.from_file(course_spec_path)
    topic_map = build_topic_map(slides_dir)
    combined = NormalizationResult()

    for section in spec.sections:
        for topic_spec in section.topics:
            matches = topic_map.get(topic_spec.id, [])
            for match in matches:
                slide_files = find_slide_files(match.path)
                for sf in slide_files:
                    result = normalize_file(sf, operations=operations, dry_run=dry_run)
                    combined.files_modified += result.files_modified
                    combined.changes.extend(result.changes)
                    combined.review_items.extend(result.review_items)

    return combined
