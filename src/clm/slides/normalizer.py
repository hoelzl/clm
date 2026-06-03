"""Slide file normalization engine.

Applies mechanical fixes to percent-format ``.py`` slide files:

- **Tag migration**: ``alt`` → ``completed`` after ``start`` cells
- **Workshop tag insertion**: adds ``workshop`` to workshop heading cells,
  and symmetrizes ``workshop``/``end-workshop`` tags across DE/EN heading
  pairs so single-language split builds detect the same workshop ranges as
  bilingual builds
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
from typing import TYPE_CHECKING

from clm.core.topic_resolver import (
    build_topic_map,
    find_slide_files,
    find_slide_files_recursive,
    matches_for_binding,
)
from clm.notebooks.slide_parser import parse_cell_header
from clm.slides.pairing import build_slide_groups
from clm.slides.raw_cells import RawCell as _RawCell
from clm.slides.raw_cells import reconstruct as _reconstruct
from clm.slides.raw_cells import split_cells as _split_raw_cells

if TYPE_CHECKING:
    from clm.slides.assign_ids import AssignOptions

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

ALL_OPERATIONS = frozenset({"tag_migration", "workshop_tags", "interleaving", "slide_ids"})


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

        if _WORKSHOP_RE.search(cell.body):
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
# Workshop tag symmetry: propagate workshop/end-workshop across DE/EN pairs
# ---------------------------------------------------------------------------

# Tags that scope a *slide* (a DE/EN pair), not a single language. They must
# appear on both halves of a slide so that a single-language split build sees
# the same workshop ranges a bilingual build does.
_SLIDE_SCOPED_TAGS = ("workshop", "end-workshop")


def _apply_workshop_symmetry(cells: list[_RawCell], file_path: str) -> list[Change]:
    """Propagate slide-scoped ``workshop``/``end-workshop`` tags across pairs.

    ``workshop`` and ``end-workshop`` mark a *slide* (a DE/EN heading pair) as a
    workshop boundary. The notebook build derives workshop ranges from these
    tags to decide which code cells to blank in the Code-Along/Partial kinds.

    A *bilingual* build runs that range scan over the interleaved cell stream,
    so a tag on **either** language's heading establishes a range that — by
    cell index — also covers the other language's cells. A single-language
    *split* build (``*.de.py`` / ``*.en.py``) only sees one language: if the
    tag is missing on that language's heading, no range is detected and the
    workshop's solution code leaks into the Code-Along/Partial output instead
    of being blanked.

    This pass closes that gap by copying the tag to the untagged half of every
    DE/EN heading pair. Bilingual output is unchanged (the range was already
    detected); split output now matches it. Headings must be adjacent in source
    order to be paired (see :func:`clm.slides.pairing.build_slide_groups`),
    which holds after interleaving and after ``unify``.
    """
    changes: list[Change] = []
    for group in build_slide_groups(cells):
        if len(group) != 2:
            continue
        i, j = group
        for tag in _SLIDE_SCOPED_TAGS:
            i_has = tag in cells[i].metadata.tags
            j_has = tag in cells[j].metadata.tags
            if i_has == j_has:
                continue
            target = cells[j] if i_has else cells[i]
            new_header = _add_tag_to_header(target.header, tag)
            target.header = new_header
            target.metadata = parse_cell_header(new_header)
            changes.append(
                Change(
                    file=file_path,
                    operation="workshop_tags",
                    line=target.line_number,
                    description=(
                        f"Propagated '{tag}' tag to paired {target.metadata.lang or '?'} heading"
                    ),
                )
            )
    return changes


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


def _is_start_completed_pair(de_cell: _RawCell, en_cell: _RawCell) -> bool:
    """True if a DE/EN code pair is a ``start``/``completed`` cohesion unit.

    A same-language ``start`` cell (or its paired ``completed`` cell)
    represents the *same* logical cell shown in two output variants. Its
    DE/EN counterpart is identified by the matching ``start``/``completed``
    tag and document position — not by content similarity, which routinely
    differs for localized identifiers (e.g. ``begruessung`` vs ``greeting``).
    """
    de_tags = set(de_cell.metadata.tags)
    en_tags = set(en_cell.metadata.tags)
    return ("start" in de_tags and "start" in en_tags) or (
        "completed" in de_tags and "completed" in en_tags
    )


def _apply_interleaving(
    cells: list[_RawCell],
    file_path: str,
    *,
    canonicalize_start_completed: bool = False,
) -> tuple[list[_RawCell], list[Change], list[ReviewItem]]:
    """Reorder cells for proper DE/EN interleaving.

    Args:
        cells: The cells to reorder in place (returns a new list).
        file_path: Source path, used for change/review reporting.
        canonicalize_start_completed: When ``True``, ``start``/``completed``
            code pairs are paired *structurally* (by tag + position) and
            forced into the canonical interleave
            ``[DE_start, EN_start, DE_completed, EN_completed]``, bypassing
            the content-similarity gate that otherwise leaves them in the
            permitted cohesion layout ``[DE_start, DE_completed, EN_start,
            EN_completed]``. Used to pre-normalize decks before a split, so
            the round-trip ``unify(split(deck)) == deck`` holds byte-for-byte.

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
            if (
                failed
                and canonicalize_start_completed
                and _is_start_completed_pair(cells[de_i], cells[en_i])
            ):
                # Structural start/completed pairing is authoritative for
                # these cells; ignore content-similarity failures (e.g.
                # localized function names) and force the canonical
                # interleave.
                failed = []
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
# Slide ID auto-generation — delegates to clm.slides.assign_ids
# ---------------------------------------------------------------------------


def _apply_slide_ids(
    cells: list[_RawCell],
    path: Path,
    options: AssignOptions | None = None,
    *,
    twin_ids: list[str | None] | None = None,
) -> tuple[list[Change], list[ReviewItem]]:
    """Assign ``slide_id`` metadata using the shared assign-ids engine.

    This is a thin adapter over :func:`clm.slides.assign_ids.assign_ids_for_cells`
    so ``clm slides normalize`` produces ids by the same rules as the
    dedicated ``clm slides assign-ids`` command — EN-derived kebab slugs,
    German transliteration, narrative inheritance, ``!``-preserve marker
    — instead of the older naive slug-and-fallback scheme.

    The engine mutates ``cells`` in place (assignments are written into
    the cell headers). We translate its ``AssignedId`` records into the
    normalizer's :class:`Change` records and its ``Refusal`` records
    into :class:`ReviewItem` records.

    ``twin_ids`` is forwarded verbatim to
    :func:`~clm.slides.assign_ids.assign_ids_for_cells` (the #162 defensive):
    when supplied, an id-less slide adopts the positionally-corresponding
    sibling-half id instead of minting a divergent slug.
    """
    from clm.slides.assign_ids import AssignOptions as _AO
    from clm.slides.assign_ids import assign_ids_for_cells

    if options is None:
        options = _AO()

    result = assign_ids_for_cells(cells, path, options, twin_ids=twin_ids)

    changes = [
        Change(
            file=a.file,
            operation="slide_ids",
            line=a.line,
            description=f'Added slide_id="{a.slide_id}" (source={a.source})',
        )
        for a in result.assignments
    ]

    review_items: list[ReviewItem] = []
    for r in result.refusals:
        details: dict = {"severity": r.severity, "line": r.line}
        if r.proposed_slug:
            details["proposed_slug"] = r.proposed_slug
        if r.proposed_title:
            details["proposed_title"] = r.proposed_title
        review_items.append(
            ReviewItem(
                file=r.file,
                issue=f"slide_id_{r.severity}_refusal",
                suggestion=r.reason,
                details=details,
            )
        )

    return changes, review_items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_file(
    path: Path,
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
    assign_options: AssignOptions | None = None,
    canonicalize_start_completed: bool = False,
) -> NormalizationResult:
    """Normalize a single slide file.

    Args:
        path: Path to the ``.py`` slide file.
        operations: Which operations to apply.  ``None`` means all.
        dry_run: If ``True``, preview changes without modifying files.
        assign_options: Options forwarded to the shared assign-ids engine
            for the ``slide_ids`` operation (``--force``,
            ``--accept-content-derived``, LLM suggester, …). ``None``
            uses defaults — refuse headingless slides, never overwrite
            existing ids, no LLM.
        canonicalize_start_completed: Forwarded to the interleaving pass;
            forces ``start``/``completed`` cohesion pairs into the canonical
            interleave so a subsequent ``split``/``unify`` round-trips
            byte-for-byte. No effect unless ``interleaving`` runs.

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
        all_changes.extend(_apply_workshop_symmetry(cells, file_str))

    if "interleaving" in op_set:
        cells, interleave_changes, interleave_reviews = _apply_interleaving(
            cells,
            file_str,
            canonicalize_start_completed=canonicalize_start_completed,
        )
        all_changes.extend(interleave_changes)
        all_review.extend(interleave_reviews)

    if "slide_ids" in op_set:
        slide_id_changes, slide_id_reviews = _apply_slide_ids(cells, path, assign_options)
        all_changes.extend(slide_id_changes)
        all_review.extend(slide_id_reviews)

    modified = False
    if all_changes and not dry_run:
        new_text = _reconstruct(preamble, cells)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8", newline="\n")
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
    assign_options: AssignOptions | None = None,
    canonicalize_start_completed: bool = False,
) -> NormalizationResult:
    """Normalize all slide files in a directory (recursive)."""
    slide_files = find_slide_files_recursive(path)
    combined = NormalizationResult()

    for sf in slide_files:
        result = normalize_file(
            sf,
            operations=operations,
            dry_run=dry_run,
            assign_options=assign_options,
            canonicalize_start_completed=canonicalize_start_completed,
        )
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
    assign_options: AssignOptions | None = None,
    canonicalize_start_completed: bool = False,
) -> NormalizationResult:
    """Normalize all slides referenced by a course spec."""
    from clm.core.course_spec import CourseSpec

    spec = CourseSpec.from_file(course_spec_path)
    topic_map = build_topic_map(slides_dir)
    combined = NormalizationResult()

    for binding in spec.iter_topic_bindings():
        matches = matches_for_binding(topic_map, binding.topic_id, binding.effective_module)
        for match in matches:
            slide_files = find_slide_files(match.path)
            for sf in slide_files:
                result = normalize_file(
                    sf,
                    operations=operations,
                    dry_run=dry_run,
                    assign_options=assign_options,
                    canonicalize_start_completed=canonicalize_start_completed,
                )
                combined.files_modified += result.files_modified
                combined.changes.extend(result.changes)
                combined.review_items.extend(result.review_items)

    return combined
