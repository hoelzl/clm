"""Slide file validation engine.

Validates percent-format ``.py`` slide files for format correctness,
tag consistency, and DE/EN language pairing (deterministic checks).
For content-quality checks that require LLM judgment (code quality,
voiceover gaps, completeness), extracts structured ``ReviewMaterial``
for the caller to evaluate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from clm.core.topic_resolver import build_topic_map, find_slide_files
from clm.notebooks.slide_parser import Cell, parse_cells
from clm.slides.tags import ALL_VALID_TAGS, EXPECTED_CODE_TAGS, EXPECTED_MARKDOWN_TAGS
from clm.slides.workshop_scope import find_workshop_start_index

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """A single deterministic validation finding."""

    severity: str  # "error", "warning", "info"
    category: str  # "format", "pairing", "tags"
    file: str
    line: int
    message: str
    suggestion: str = ""


@dataclass
class ReviewMaterial:
    """Extracted data for LLM-dependent checks.

    Each field is ``None`` when the corresponding check was not requested.
    """

    code_quality: dict | None = None
    voiceover_gaps: list[dict] | None = None
    completeness: dict | None = None


@dataclass
class ValidationResult:
    """Result of validating one or more slide files."""

    files_checked: int
    findings: list[Finding] = field(default_factory=list)
    review_material: ReviewMaterial | None = None

    @property
    def summary(self) -> str:
        errors = sum(1 for f in self.findings if f.severity == "error")
        warnings = sum(1 for f in self.findings if f.severity == "warning")
        parts = []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        if not parts:
            parts.append("no issues")
        rm_count = 0
        if self.review_material:
            if self.review_material.code_quality is not None:
                rm_count += 1
            if self.review_material.voiceover_gaps is not None:
                rm_count += 1
            if self.review_material.completeness is not None:
                rm_count += 1
        if rm_count:
            parts.append(f"{rm_count} {'category' if rm_count == 1 else 'categories'} for review")
        return f"{self.files_checked} file{'s' if self.files_checked != 1 else ''} checked: {', '.join(parts)}"


# ---------------------------------------------------------------------------
# Deterministic check categories
# ---------------------------------------------------------------------------

ALL_DETERMINISTIC_CHECKS = frozenset({"format", "pairing", "tags"})
ALL_REVIEW_CHECKS = frozenset({"code_quality", "voiceover", "completeness"})
ALL_CHECKS = ALL_DETERMINISTIC_CHECKS | ALL_REVIEW_CHECKS


def _check_format(cells: list[Cell], file_path: str) -> list[Finding]:
    """Check cell header syntax and structure."""
    findings: list[Finding] = []

    for cell in cells:
        header = cell.header
        meta = cell.metadata

        # Skip j2 cells — they have their own format
        if meta.is_j2:
            continue

        # Check that header starts with "# %%"
        if not header.startswith("# %%"):
            findings.append(
                Finding(
                    severity="error",
                    category="format",
                    file=file_path,
                    line=cell.line_number,
                    message=f"Cell header does not start with '# %%': {header!r}",
                    suggestion="Cell headers must begin with '# %%'",
                )
            )
            continue

        # Check for malformed tags= attribute
        if "tags=" in header:
            tags_match = re.search(r"tags=\[([^\]]*)\]", header)
            if not tags_match:
                findings.append(
                    Finding(
                        severity="error",
                        category="format",
                        file=file_path,
                        line=cell.line_number,
                        message=f"Malformed tags attribute in header: {header!r}",
                        suggestion='Tags must use the format tags=["tag1", "tag2"]',
                    )
                )

        # Check for malformed lang= attribute
        if "lang=" in header:
            lang_match = re.search(r'lang="(\w+)"', header)
            if not lang_match:
                findings.append(
                    Finding(
                        severity="error",
                        category="format",
                        file=file_path,
                        line=cell.line_number,
                        message=f"Malformed lang attribute in header: {header!r}",
                        suggestion='Language must use the format lang="de" or lang="en"',
                    )
                )

    return findings


def _check_tags(cells: list[Cell], file_path: str) -> list[Finding]:
    """Check tag validity, unclosed start/completed pairs, and workshop constraints."""
    findings: list[Finding] = []

    # Workshop scope runs from the first ``workshop`` heading to end of file.
    workshop_start_idx = find_workshop_start_index(cells)
    workshop_start_line = (
        cells[workshop_start_idx].line_number if workshop_start_idx is not None else 0
    )

    # Track start/completed pairing
    pending_start: Cell | None = None

    for idx, cell in enumerate(cells):
        meta = cell.metadata
        if meta.is_j2:
            continue

        tags = meta.tags
        in_workshop = workshop_start_idx is not None and idx >= workshop_start_idx

        # Check for invalid tags
        if meta.cell_type == "code":
            expected = EXPECTED_CODE_TAGS
        else:
            expected = EXPECTED_MARKDOWN_TAGS

        for tag in tags:
            if tag not in expected:
                if tag in ALL_VALID_TAGS:
                    findings.append(
                        Finding(
                            severity="warning",
                            category="tags",
                            file=file_path,
                            line=cell.line_number,
                            message=f"Tag '{tag}' is not expected on a {meta.cell_type} cell",
                            suggestion=f"Valid tags for {meta.cell_type} cells: {sorted(expected)}",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            severity="error",
                            category="tags",
                            file=file_path,
                            line=cell.line_number,
                            message=f"Unrecognized tag '{tag}'",
                            suggestion=f"Valid tags: {sorted(ALL_VALID_TAGS)}",
                        )
                    )

        # Track start/completed pairing
        if "start" in tags:
            if pending_start is not None:
                # Previous start was never closed
                findings.append(
                    Finding(
                        severity="error",
                        category="tags",
                        file=file_path,
                        line=pending_start.line_number,
                        message="'start' tag at this line has no matching 'completed' cell",
                        suggestion="Add a cell with tag 'completed' after the 'start' cell",
                    )
                )
            pending_start = cell

            # Check for start/completed inside workshop
            if in_workshop:
                findings.append(
                    Finding(
                        severity="warning",
                        category="tags",
                        file=file_path,
                        line=cell.line_number,
                        message=(
                            f"start/completed pair found inside workshop section "
                            f"(workshop begins at line {workshop_start_line})"
                        ),
                        suggestion="Use plain # %% for workshop solutions, not start/completed",
                    )
                )

        if "completed" in tags:
            if pending_start is None:
                findings.append(
                    Finding(
                        severity="error",
                        category="tags",
                        file=file_path,
                        line=cell.line_number,
                        message="'completed' tag without a preceding 'start' cell",
                        suggestion="Add a cell with tag 'start' before this 'completed' cell",
                    )
                )
            pending_start = None

    # Check for unclosed start at end of file
    if pending_start is not None:
        findings.append(
            Finding(
                severity="error",
                category="tags",
                file=file_path,
                line=pending_start.line_number,
                message="'start' tag at this line has no matching 'completed' cell",
                suggestion="Add a cell with tag 'completed' after the 'start' cell",
            )
        )

    return findings


def _check_pairing(cells: list[Cell], file_path: str) -> list[Finding]:
    """Check DE/EN cell pairing: count, position, and tag consistency."""
    findings: list[Finding] = []

    # Collect language-tagged content cells (skip j2, notes, voiceover)
    de_cells: list[Cell] = []
    en_cells: list[Cell] = []

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.is_narrative:
            continue
        if meta.lang == "de":
            de_cells.append(cell)
        elif meta.lang == "en":
            en_cells.append(cell)

    # Count mismatch
    if len(de_cells) != len(en_cells):
        findings.append(
            Finding(
                severity="error",
                category="pairing",
                file=file_path,
                line=1,
                message=(
                    f"DE/EN cell count mismatch: {len(de_cells)} German, {len(en_cells)} English"
                ),
                suggestion="Each German cell should have a corresponding English cell",
            )
        )

    # Positional pairing — check tags match between pairs
    for i, (de, en) in enumerate(zip(de_cells, en_cells, strict=False)):
        de_tags = set(de.metadata.tags)
        en_tags = set(en.metadata.tags)

        if de_tags != en_tags:
            findings.append(
                Finding(
                    severity="warning",
                    category="pairing",
                    file=file_path,
                    line=de.line_number,
                    message=(
                        f"Tag mismatch in DE/EN pair {i + 1}: "
                        f"DE={sorted(de_tags)}, EN={sorted(en_tags)}"
                    ),
                    suggestion="Paired DE/EN cells should have the same tags",
                )
            )

        # Check cell type consistency
        if de.metadata.cell_type != en.metadata.cell_type:
            findings.append(
                Finding(
                    severity="error",
                    category="pairing",
                    file=file_path,
                    line=de.line_number,
                    message=(
                        f"Cell type mismatch in DE/EN pair {i + 1}: "
                        f"DE={de.metadata.cell_type}, EN={en.metadata.cell_type}"
                    ),
                    suggestion="Paired DE/EN cells should have the same cell type",
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Review material extraction (LLM-dependent checks)
# ---------------------------------------------------------------------------


def _extract_code_quality(cells: list[Cell], file_path: str) -> dict:
    """Extract potential code-quality issues for LLM review."""
    print_calls: list[dict] = []
    leading_comments: list[dict] = []

    for cell in cells:
        meta = cell.metadata
        if meta.cell_type != "code" or meta.is_j2:
            continue
        content = cell.content

        # Detect print() calls
        for match in re.finditer(r"\bprint\((.+?)\)", content):
            # Provide context about the print call
            print_calls.append(
                {
                    "file": file_path,
                    "line": cell.line_number,
                    "code": match.group(0),
                    "context": _brief_cell_context(cell),
                }
            )

        # Detect leading comments in code cells
        lines = content.strip().split("\n")
        if lines and lines[0].startswith("#") and not lines[0].startswith("# %%"):
            # First line is a comment — may be unnecessary
            preview = "\n".join(lines[:3])
            leading_comments.append(
                {
                    "file": file_path,
                    "line": cell.line_number,
                    "code": preview,
                    "context": "comment at start of code cell",
                }
            )

    result: dict = {}
    if print_calls:
        result["print_calls"] = print_calls
    if leading_comments:
        result["leading_comments"] = leading_comments
    return result


def _extract_voiceover_gaps(cells: list[Cell], file_path: str) -> list[dict]:
    """Extract cells that lack voiceover for LLM review.

    Uses per-language tracking so bilingual slide files are handled
    correctly. The canonical layout produced by ``normalize-slides`` is:

        [de content] [en content] [de voiceover] [en voiceover]

    A naive linear scan would see the EN content cell immediately after the
    DE content cell and flag the DE cell as "missing voiceover". Instead we
    track the most recent unmatched content cell in each language stream
    (``de``, ``en``, ``None``) and apply voiceover coverage per-stream:

    * A ``lang="de"`` voiceover covers the most recent unmatched ``lang="de"``
      content cell *and* the most recent unmatched ``lang``-less (shared)
      content cell, if any.
    * A ``lang="en"`` voiceover behaves symmetrically.
    * A ``lang``-less voiceover covers the most recent unmatched content
      cell in *every* live stream — a single shared voiceover can legitimately
      cover both halves of a bilingual slide.

    Once a content cell is "covered" its pointer is cleared, so a later
    voiceover can't cover it again. A new content cell in the same language
    stream overwrites the pointer, which means the previous cell is left
    uncovered (and will be reported as a gap). This matches the authoring
    rule that every slide/subslide and nontrivial code cell should have its
    own voiceover.
    """
    gaps: list[dict] = []

    content_cells: list[Cell] = []
    covered: set[int] = set()

    # Index (into ``content_cells``) of the most recent unmatched content
    # cell in each language stream. ``None`` means "no live content cell".
    last_de: int | None = None
    last_en: int | None = None
    last_any: int | None = None

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2:
            continue

        if meta.is_narrative:
            lang = meta.lang
            if lang == "de":
                if last_de is not None:
                    covered.add(last_de)
                    last_de = None
                if last_any is not None:
                    covered.add(last_any)
                    last_any = None
            elif lang == "en":
                if last_en is not None:
                    covered.add(last_en)
                    last_en = None
                if last_any is not None:
                    covered.add(last_any)
                    last_any = None
            else:
                # ``lang``-less narrative covers every live stream.
                if last_de is not None:
                    covered.add(last_de)
                    last_de = None
                if last_en is not None:
                    covered.add(last_en)
                    last_en = None
                if last_any is not None:
                    covered.add(last_any)
                    last_any = None
            continue

        # Content cell: track it and update the pointer for its language.
        idx = len(content_cells)
        content_cells.append(cell)
        if meta.lang == "de":
            last_de = idx
        elif meta.lang == "en":
            last_en = idx
        else:
            last_any = idx

    for idx, cell in enumerate(content_cells):
        if idx in covered:
            continue
        meta = cell.metadata
        # Only flag slides/subslides and code cells — not every cell needs voiceover
        if not (meta.is_slide or meta.is_subslide or meta.cell_type == "code"):
            continue
        entry: dict = {
            "file": file_path,
            "line": cell.line_number,
            "type": meta.cell_type,
            "lang": meta.lang,
            "has_voiceover": False,
        }
        if meta.cell_type == "markdown":
            heading = _extract_markdown_heading(cell.content)
            if heading:
                entry["heading"] = heading
        else:
            # Preview of code
            preview = cell.content[:60]
            if len(cell.content) > 60:
                preview += "..."
            entry["preview"] = preview
        gaps.append(entry)

    return gaps


def _extract_completeness(cells: list[Cell], file_path: str) -> dict:
    """Extract concepts and workshop coverage for LLM review."""
    slide_concepts: list[str] = []
    workshop_exercises: list[str] = []

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.is_narrative:
            continue

        # Extract headings as concept indicators
        if meta.cell_type == "markdown" and (meta.is_slide or meta.is_subslide):
            heading = _extract_markdown_heading(cell.content)
            if heading:
                if heading.lower().startswith("workshop"):
                    workshop_exercises.append(heading)
                else:
                    slide_concepts.append(heading)

    result: dict = {
        "file": file_path,
        "slide_concepts": slide_concepts,
        "workshop_exercises": workshop_exercises,
    }

    return result


def _extract_markdown_heading(content: str) -> str:
    """Extract the first markdown heading from a percent-format cell body.

    Cell content lines start with ``# `` (Python comment prefix).  A heading
    line looks like ``# ## My Title``.  We strip the leading comment prefix
    (exactly ``# `` or ``#``), then look for the markdown ``#`` heading marker.
    """
    for line in content.split("\n"):
        # Remove the Python comment prefix
        if line.startswith("# "):
            inner = line[2:]
        elif line.startswith("#"):
            inner = line[1:]
        else:
            continue
        inner = inner.strip()
        if inner.startswith("#"):
            return inner.lstrip("#").strip()
    return ""


def _brief_cell_context(cell: Cell) -> str:
    """Return a brief context string for a cell."""
    meta = cell.metadata
    parts = [meta.cell_type]
    if meta.lang:
        parts.append(f"lang={meta.lang}")
    if meta.tags:
        parts.append(f"tags={meta.tags}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_file(
    path: Path,
    checks: list[str] | None = None,
) -> ValidationResult:
    """Validate a single slide file.

    Args:
        path: Path to the ``.py`` slide file.
        checks: Which checks to run. Default: all.
            Deterministic: ``"format"``, ``"pairing"``, ``"tags"``.
            Review: ``"code_quality"``, ``"voiceover"``, ``"completeness"``.

    Returns:
        A :class:`ValidationResult` with findings and optional review material.
    """
    check_set = set(checks) if checks else set(ALL_CHECKS)
    file_str = str(path)

    text = path.read_text(encoding="utf-8")
    cells = parse_cells(text)

    findings: list[Finding] = []

    if "format" in check_set:
        findings.extend(_check_format(cells, file_str))
    if "tags" in check_set:
        findings.extend(_check_tags(cells, file_str))
    if "pairing" in check_set:
        findings.extend(_check_pairing(cells, file_str))

    # Review material extraction
    review_checks = check_set & ALL_REVIEW_CHECKS
    review_material: ReviewMaterial | None = None
    if review_checks:
        review_material = ReviewMaterial()
        if "code_quality" in review_checks:
            review_material.code_quality = _extract_code_quality(cells, file_str)
        if "voiceover" in review_checks:
            review_material.voiceover_gaps = _extract_voiceover_gaps(cells, file_str)
        if "completeness" in review_checks:
            review_material.completeness = _extract_completeness(cells, file_str)

    return ValidationResult(
        files_checked=1,
        findings=findings,
        review_material=review_material,
    )


def validate_quick(path: Path) -> ValidationResult:
    """Fast syntax-only validation for PostToolUse hooks.

    Checks only:
    - Cell header syntax
    - Valid tag names
    - Unclosed start/completed pairs

    Designed to complete in <2s for a single file.
    """
    file_str = str(path)
    text = path.read_text(encoding="utf-8")
    cells = parse_cells(text)

    findings: list[Finding] = []
    findings.extend(_check_format(cells, file_str))
    findings.extend(_check_tags(cells, file_str))

    return ValidationResult(
        files_checked=1,
        findings=findings,
    )


def validate_directory(
    path: Path,
    checks: list[str] | None = None,
) -> ValidationResult:
    """Validate all slide files in a topic directory.

    Args:
        path: Path to a topic directory.
        checks: Which checks to run (passed to :func:`validate_file`).
    """
    slide_files = find_slide_files(path)
    all_findings: list[Finding] = []
    combined_review = ReviewMaterial() if (not checks or set(checks) & ALL_REVIEW_CHECKS) else None

    for sf in slide_files:
        result = validate_file(sf, checks=checks)
        all_findings.extend(result.findings)
        if combined_review is not None and result.review_material is not None:
            _merge_review_material(combined_review, result.review_material)

    return ValidationResult(
        files_checked=len(slide_files),
        findings=all_findings,
        review_material=combined_review
        if combined_review and _has_review_data(combined_review)
        else None,
    )


def validate_course(
    course_spec_path: Path,
    slides_dir: Path,
    checks: list[str] | None = None,
) -> ValidationResult:
    """Validate all slides referenced by a course spec.

    Args:
        course_spec_path: Path to the course spec XML file.
        slides_dir: Path to the ``slides/`` directory.
        checks: Which checks to run (passed to :func:`validate_file`).
    """
    from clm.core.course_spec import CourseSpec

    spec = CourseSpec.from_file(course_spec_path)
    topic_map = build_topic_map(slides_dir)

    all_findings: list[Finding] = []
    files_checked = 0
    combined_review = ReviewMaterial() if (not checks or set(checks) & ALL_REVIEW_CHECKS) else None

    for section in spec.sections:
        for topic_spec in section.topics:
            matches = topic_map.get(topic_spec.id, [])
            for match in matches:
                slide_files = find_slide_files(match.path)
                for sf in slide_files:
                    result = validate_file(sf, checks=checks)
                    all_findings.extend(result.findings)
                    files_checked += 1
                    if combined_review is not None and result.review_material is not None:
                        _merge_review_material(combined_review, result.review_material)

    return ValidationResult(
        files_checked=files_checked,
        findings=all_findings,
        review_material=combined_review
        if combined_review and _has_review_data(combined_review)
        else None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _merge_review_material(target: ReviewMaterial, source: ReviewMaterial) -> None:
    """Merge source review material into target."""
    if source.code_quality is not None:
        if target.code_quality is None:
            target.code_quality = {}
        for key, items in source.code_quality.items():
            target.code_quality.setdefault(key, []).extend(items)
    if source.voiceover_gaps is not None:
        if target.voiceover_gaps is None:
            target.voiceover_gaps = []
        target.voiceover_gaps.extend(source.voiceover_gaps)
    if source.completeness is not None:
        if target.completeness is None:
            target.completeness = {}
        # For completeness, collect per-file entries
        file_key = source.completeness.get("file", "unknown")
        target.completeness[file_key] = source.completeness


def _has_review_data(rm: ReviewMaterial) -> bool:
    """Check if review material has any data."""
    if rm.code_quality:
        return True
    if rm.voiceover_gaps:
        return True
    if rm.completeness:
        return True
    return False
