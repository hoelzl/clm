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

from clm.core.topic_resolver import (
    _group_paths_into_units,
    build_topic_map,
    find_slide_files,
    find_slide_files_recursive,
    matches_for_binding,
)
from clm.infrastructure.utils.path_utils import split_lang_suffix
from clm.notebooks.slide_parser import Cell, parse_cells
from clm.slides.pairing import (
    TITLE_SLIDE_ID,
    build_slide_groups,
    is_title_macro_cell,
)
from clm.slides.raw_cells import split_cells as split_raw_cells
from clm.slides.slug import (
    MAX_SLUG_LENGTH,
    is_valid_slug,
    strip_preserve_marker,
)
from clm.slides.split import _is_shared
from clm.slides.tags import ALL_VALID_TAGS, EXPECTED_CODE_TAGS, EXPECTED_MARKDOWN_TAGS
from clm.slides.workshop_scope import find_workshop_ranges, is_in_workshop

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
    """Check tag validity, unclosed start/completed pairs, and orphan end-workshop tags."""
    findings: list[Finding] = []

    # Track start/completed pairing per language stream. Both the cohesion
    # layout ``[DE_start, DE_completed, EN_start, EN_completed]`` and the
    # canonical interleaved layout ``[DE_start, EN_start, DE_completed,
    # EN_completed]`` are valid — the adjacency check (_check_ordering)
    # already accepts both, and the tag check must too. Cells without a
    # ``lang`` attribute share a single bucket keyed by ``None``.
    pending_starts: dict[str | None, Cell] = {}

    # Flag orphan ``end-workshop`` markdown cells that appear BEFORE any
    # ``workshop`` heading in the file — those have no effect on the
    # partition and almost always indicate a typo. After at least one
    # workshop has been opened, additional ``end-workshop`` markers are
    # tolerated: bilingual slide pairs typically carry the tag on both the
    # DE and EN heading cells, and the second one (after the first has
    # already closed the workshop) is harmless.
    seen_workshop = False
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "markdown":
            continue
        cell_tags = meta.tags
        if "workshop" in cell_tags:
            seen_workshop = True
        elif "end-workshop" in cell_tags and not seen_workshop:
            findings.append(
                Finding(
                    severity="warning",
                    category="tags",
                    file=file_path,
                    line=cell.line_number,
                    message=(
                        "'end-workshop' tag with no preceding 'workshop' heading "
                        "— the tag has no effect"
                    ),
                    suggestion=(
                        "Remove 'end-workshop' or add a 'workshop'-tagged "
                        "markdown cell earlier in the file"
                    ),
                )
            )

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2:
            continue

        tags = meta.tags

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

        # Track start/completed pairing (per-language)
        if "start" in tags:
            prior = pending_starts.get(meta.lang)
            if prior is not None:
                # Previous same-language start was never closed
                findings.append(
                    Finding(
                        severity="error",
                        category="tags",
                        file=file_path,
                        line=prior.line_number,
                        message="'start' tag at this line has no matching 'completed' cell",
                        suggestion="Add a cell with tag 'completed' after the 'start' cell",
                    )
                )
            pending_starts[meta.lang] = cell

        if "completed" in tags:
            if pending_starts.pop(meta.lang, None) is None:
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

    # Check for unclosed starts at end of file
    for prior in pending_starts.values():
        findings.append(
            Finding(
                severity="error",
                category="tags",
                file=file_path,
                line=prior.line_number,
                message="'start' tag at this line has no matching 'completed' cell",
                suggestion="Add a cell with tag 'completed' after the 'start' cell",
            )
        )

    # Flag workshop headings that aren't backed by a workshop scope (#78).
    findings.extend(_check_workshop_headings(cells, file_path))

    return findings


# Matches a markdown heading whose text begins with the word "Workshop",
# tolerating any number of leading ``#`` markers and surrounding whitespace.
# The leading ``#`` set is the markdown heading marker (the Python comment
# prefix ``# `` is stripped before matching). ``\bWorkshop\b`` matches
# "Workshop", "Workshop: …", "Workshop (Continued)", and "Workshop-Ziel"
# (the hyphen is a word boundary) while rejecting e.g. "Workshops".
# Case-sensitive: every workshop heading in the existing decks capitalizes
# "Workshop" (German uses the same word), so a lowercase match would only
# add false positives.
_WORKSHOP_HEADING_RE = re.compile(r"^#+\s*Workshop\b")


def _is_workshop_heading_cell(cell: Cell) -> bool:
    """Return whether a markdown cell's first heading names a workshop.

    Strips the Python comment prefix (``# `` / ``#``) from each content line
    and matches the markdown heading marker against
    :data:`_WORKSHOP_HEADING_RE`. Only the first heading line is consulted,
    mirroring :func:`_extract_markdown_heading`.
    """
    if cell.metadata.cell_type != "markdown":
        return False
    for line in cell.content.split("\n"):
        if line.startswith("# "):
            inner = line[2:]
        elif line.startswith("#"):
            inner = line[1:]
        else:
            continue
        inner = inner.strip()
        if inner.startswith("#"):
            return bool(_WORKSHOP_HEADING_RE.match(inner))
    return False


def _check_workshop_headings(cells: list[Cell], file_path: str) -> list[Finding]:
    """Flag ``# Workshop`` headings that don't open or sit inside a workshop.

    The ``partial`` output kind relies on workshop scope to decide which code
    cells to leave empty for code-alongs. A workshop is opened by either a
    ``workshop`` tag or a slide-start cell whose ``slide_id`` starts with
    ``workshop-`` (see :mod:`clm.slides.workshop_scope`). When a markdown cell
    *looks* like a workshop heading (``# Workshop …``) but no workshop scope
    covers it, the ``partial`` build silently renders every code cell instead
    of leaving the exercise cells empty — issue #78.

    A heading is fine when its cell falls inside any detected workshop range:
    that covers both the opener itself and continuation headings like
    ``## Workshop (Continued)`` that live inside an already-open scope. Only
    workshop-looking headings *outside* every range are flagged.
    """
    findings: list[Finding] = []
    workshop_ranges = find_workshop_ranges(cells)

    for idx, cell in enumerate(cells):
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "markdown":
            continue
        if not _is_workshop_heading_cell(cell):
            continue
        if is_in_workshop(idx, workshop_ranges):
            continue
        findings.append(
            Finding(
                severity="warning",
                category="tags",
                file=file_path,
                line=cell.line_number,
                message=(
                    "markdown cell has a 'Workshop' heading but no workshop "
                    "scope covers it — the 'partial' output kind will render "
                    "all code cells instead of leaving the exercise empty"
                ),
                suggestion=(
                    "Add a 'workshop' tag to this heading cell, or give its "
                    "slide a slide_id starting with 'workshop-'. If this "
                    "heading continues an earlier workshop, add the missing "
                    "opener (or an 'end-workshop' marker was placed too early)."
                ),
            )
        )

    return findings


def _check_pairing(cells: list[Cell], file_path: str, *, is_split: bool = False) -> list[Finding]:
    """Check DE/EN cell pairing: count, position, and tag consistency.

    When ``is_split`` is set the file is a single-language split half
    (``*.de.py`` / ``*.en.py``), so the genuinely *bilingual* sub-checks —
    DE/EN count parity, positional tag/type pairing, and DE/EN adjacency —
    are skipped: a split file legitimately carries cells of only one
    language, and running them would fire a false count mismatch on every
    converted deck (issue #160). Cross-file shared-cell parity between the
    two halves is validated separately by :func:`_check_shared_cell_parity`.

    The per-file ``slide_id`` integrity checks (presence, slug format,
    uniqueness, narrative adjacency) always run — they apply equally to a
    single-language file and never misfire on one (no DE/EN slide groups
    form, so the pair-consistency clause is a no-op).
    """
    findings: list[Finding] = []

    if not is_split:
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
                        f"DE/EN cell count mismatch: "
                        f"{len(de_cells)} German, {len(en_cells)} English"
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

        # Adjacency check — paired DE/EN cells should not have other lang-tagged
        # or narrative cells between them. The canonical layout produced by
        # ``normalize-slides`` is::
        #
        #     [de content] [en content] [de voiceover] [en voiceover]
        #
        # A common violation introduced by classroom/video deck merges is
        # ``[de slide] [de voiceover] [en slide] [en voiceover]`` — voiceover
        # wedged between the DE and EN halves of a content pair.
        findings.extend(_check_ordering(cells, file_path))

    findings.extend(_check_slide_ids(cells, file_path))

    return findings


_PAIRING_CATEGORIES = ("markdown", "code", "voiceover", "notes")


def _ordering_category(cell: Cell) -> str | None:
    """Classify a cell for DE/EN adjacency checking.

    Returns ``"markdown"``, ``"code"``, ``"voiceover"``, ``"notes"``, or
    ``None`` for cells that don't participate in DE/EN pairing (j2 cells,
    shared cells without ``lang``, lang-less narrative cells).
    """
    meta = cell.metadata
    if meta.is_j2:
        return None
    if meta.lang not in ("de", "en"):
        return None
    if meta.is_narrative:
        return "voiceover" if "voiceover" in meta.tags else "notes"
    return "code" if meta.cell_type == "code" else "markdown"


def _collapse_start_completed_pairs(indices: list[int], cells: list[Cell]) -> list[tuple[int, int]]:
    """Collapse a same-language ``start`` / ``completed`` pair into one unit.

    The ``start`` and ``completed`` cells of a same-language pair represent
    one logical cell shown in two output variants (code-along vs. completed/
    speaker). The authoring rules permit them to be visually adjacent inside
    a language stream — i.e., ``[DE_start, DE_completed, EN_start, EN_completed]``
    — even though that order separates the DE/EN pair across two cells per
    side.

    For DE/EN adjacency checking, treat such a same-language consecutive
    pair as one *unit* spanning ``(start_idx, completed_idx)``. A solo cell
    becomes a single-index unit ``(idx, idx)``.

    A pair is recognized only when the ``start`` cell is *immediately*
    followed by a ``completed`` cell of the *same language* in the file
    (i.e., ``completed_idx == start_idx + 1``). If anything else sits
    between them — even a shared/no-lang cell — they are not collapsed.
    """
    result: list[tuple[int, int]] = []
    i = 0
    while i < len(indices):
        idx = indices[i]
        cell = cells[idx]
        merged = False
        if "start" in cell.metadata.tags and i + 1 < len(indices):
            next_idx = indices[i + 1]
            next_cell = cells[next_idx]
            if (
                "completed" in next_cell.metadata.tags
                and next_cell.metadata.lang == cell.metadata.lang
                and next_idx == idx + 1
            ):
                result.append((idx, next_idx))
                i += 2
                merged = True
        if not merged:
            result.append((idx, idx))
            i += 1
    return result


def _check_ordering(cells: list[Cell], file_path: str) -> list[Finding]:
    """Verify each paired DE cell is adjacent to its EN partner.

    Only j2 cells and language-neutral (no ``lang``) shared cells are
    permitted between a DE cell and its paired EN cell. Any intervening
    cell with a ``lang`` attribute (de/en) or narrative tag (voiceover/
    notes) is flagged as an ordering violation.

    Same-language ``start``/``completed`` cell pairs are collapsed into a
    single logical unit before pairing — see
    :func:`_collapse_start_completed_pairs`. This permits the cohesion
    layout ``[DE_start, DE_completed, EN_start, EN_completed]`` without
    flagging the embedded ``DE_completed`` / ``EN_start`` as intervening.

    DE/EN units are paired positionally per category, matching what
    ``normalize-slides`` does. When counts are mismatched the pairing
    check already reports it; this check skips that category to avoid
    cascading noise.
    """
    findings: list[Finding] = []

    by_cat_lang: dict[tuple[str, str], list[int]] = {}
    for idx, cell in enumerate(cells):
        cat = _ordering_category(cell)
        if cat is None:
            continue
        lang = cell.metadata.lang
        # _ordering_category returned non-None, so lang is "de" or "en".
        assert lang in ("de", "en")
        by_cat_lang.setdefault((cat, lang), []).append(idx)

    for cat in _PAIRING_CATEGORIES:
        de_units = _collapse_start_completed_pairs(by_cat_lang.get((cat, "de"), []), cells)
        en_units = _collapse_start_completed_pairs(by_cat_lang.get((cat, "en"), []), cells)
        if len(de_units) != len(en_units):
            # Count mismatch is reported by _check_pairing; skip to keep
            # output uncluttered.
            continue
        for de_unit, en_unit in zip(de_units, en_units, strict=True):
            de_first, de_last = de_unit
            en_first, en_last = en_unit
            if de_first < en_first:
                gap_start = de_last + 1
                gap_end = en_first
            else:
                gap_start = en_last + 1
                gap_end = de_first
            for k in range(gap_start, gap_end):
                between = cells[k]
                bmeta = between.metadata
                if bmeta.is_j2:
                    continue
                if bmeta.lang is None and not bmeta.is_narrative:
                    # Shared (language-neutral) cell — fine between DE/EN.
                    continue
                de_cell = cells[de_first]
                findings.append(
                    Finding(
                        severity="warning",
                        category="pairing",
                        file=file_path,
                        line=de_cell.line_number,
                        message=(
                            f"DE/EN {cat} pair is not adjacent: "
                            f"intervening lang-tagged cell at line "
                            f"{between.line_number}"
                        ),
                        suggestion=(
                            "Paired DE/EN cells should be adjacent. "
                            "Canonical layout: [de content] [en content] "
                            "[de voiceover] [en voiceover]. "
                            "Same-language start/completed pairs are "
                            "permitted to stay grouped. "
                            "Run `clm normalize-slides` to fix mechanically."
                        ),
                    )
                )
                break  # one finding per pair

    return findings


def _check_slide_ids(cells: list[Cell], file_path: str) -> list[Finding]:
    """Verify slide_id metadata: presence, format, uniqueness, adjacency, pair-consistency.

    Per handover §3 / option B rollout, *missing* slide_ids land at
    ``warning`` severity (slated for promotion to ``error`` in CLM 1.7
    once the PythonCourses migration sweep completes). All other rules
    — duplicate ids, narrative-cell adjacency mismatch, slug-format
    violations, DE/EN pair mismatch — fire at the severity their
    semantics warrant (errors for content bugs, warnings for
    style/format drift).

    The ``!`` preserve marker is stripped before every comparison except
    the slug-format check, where it's permitted as a single leading
    character that does not count toward the length cap. The j2
    ``header()`` macro line anchors :data:`TITLE_SLIDE_ID` for any
    narrative cells that follow it — those cells validate clean even
    with no preceding ``slide``/``subslide`` cell of their own.
    """
    findings: list[Finding] = []

    # Map cell index -> slide-group index. A paired DE/EN slide group
    # shares a single logical slide_id, so the duplicate check must
    # treat both members as one occurrence — keying on group identity
    # avoids flagging the EN sibling as a duplicate of the DE cell.
    slide_groups = build_slide_groups(cells)
    cell_to_group: dict[int, int] = {}
    for gi, group in enumerate(slide_groups):
        for ci in group:
            cell_to_group[ci] = gi

    # Bare slide_id -> (group index, line) of first sighting. A second
    # occurrence in the *same* group is the pair sharing the id and is
    # fine; in a *different* group it's a real duplicate.
    bare_first_seen: dict[str, tuple[int, int]] = {}

    # Most recent slide_id (bare) seen in source order. Updated by
    # ``slide``/``subslide`` cells and by the j2 ``header()`` macro
    # line (which anchors "title" without carrying a slide_id itself).
    # Narrative cells consult this anchor; intervening code/shared/j2
    # cells do not reset it.
    current_slide_id: str | None = None

    for idx, cell in enumerate(cells):
        meta = cell.metadata

        # The j2 header() macro anchors the title slide without carrying
        # a slide_id of its own. Detect and update the anchor first; the
        # is_j2 skip below would otherwise hide it.
        if is_title_macro_cell(cell):
            current_slide_id = TITLE_SLIDE_ID
            continue

        if meta.is_j2:
            continue

        if meta.is_slide_start:
            sid = meta.slide_id
            if sid is None:
                findings.append(
                    Finding(
                        severity="warning",
                        category="pairing",
                        file=file_path,
                        line=cell.line_number,
                        message="slide/subslide cell missing slide_id",
                        suggestion=(
                            "Run `clm slides assign-ids` to add stable identifiers. "
                            "This will become an error in CLM 1.7."
                        ),
                    )
                )
                # Don't touch current_slide_id — keep the previous anchor
                # so narrative cells after this hole still validate.
                continue

            bare = strip_preserve_marker(sid)
            findings.extend(_check_slug_format(sid, cell, file_path))

            gi = cell_to_group[idx]
            prev = bare_first_seen.get(bare)
            if prev is None:
                bare_first_seen[bare] = (gi, cell.line_number)
            elif prev[0] != gi:
                findings.append(
                    Finding(
                        severity="error",
                        category="pairing",
                        file=file_path,
                        line=cell.line_number,
                        message=(f"duplicate slide_id {bare!r} (first seen at line {prev[1]})"),
                        suggestion="slide_ids must be unique within a file (bare form, ignoring `!`).",
                    )
                )
            # Same-group repeat (DE/EN pair sharing the id): no finding.

            current_slide_id = bare
            continue

        if meta.is_narrative:
            sid = meta.slide_id
            if sid is None:
                # Narrative cells without slide_id are not flagged at
                # this phase — assign-ids fills them by adjacency.
                continue

            bare = strip_preserve_marker(sid)
            findings.extend(_check_slug_format(sid, cell, file_path))

            if current_slide_id is None:
                findings.append(
                    Finding(
                        severity="error",
                        category="pairing",
                        file=file_path,
                        line=cell.line_number,
                        message=(
                            f"voiceover/notes cell carries slide_id={bare!r} but no "
                            "preceding slide/subslide anchor"
                        ),
                        suggestion=(
                            "Voiceover/notes cells inherit the slide_id of the "
                            "preceding slide/subslide cell."
                        ),
                    )
                )
            elif bare != current_slide_id:
                findings.append(
                    Finding(
                        severity="error",
                        category="pairing",
                        file=file_path,
                        line=cell.line_number,
                        message=(
                            f"voiceover/notes slide_id={bare!r} does not match "
                            f"preceding slide {current_slide_id!r}"
                        ),
                        suggestion=(
                            "Voiceover/notes slide_id must match the immediately "
                            "preceding slide/subslide (likely a stale id left by "
                            "copy-paste)."
                        ),
                    )
                )
            continue

    # Pair-mismatch check: when a DE slide cell sits next to an EN slide
    # cell in source order, the EN-derived slug policy (handover §2.3)
    # requires both to carry the same bare slide_id. Skip pairs where
    # either side is missing an id — the missing-id warning above
    # already covers that case.
    for group in slide_groups:
        if len(group) != 2:
            continue
        a, b = group
        sid_a = cells[a].metadata.slide_id
        sid_b = cells[b].metadata.slide_id
        if sid_a is None or sid_b is None:
            continue
        bare_a = strip_preserve_marker(sid_a)
        bare_b = strip_preserve_marker(sid_b)
        if bare_a == bare_b:
            continue
        findings.append(
            Finding(
                severity="warning",
                category="pairing",
                file=file_path,
                line=cells[a].line_number,
                message=(
                    f"DE/EN slide pair has mismatched slide_id: "
                    f"{bare_a!r} (line {cells[a].line_number}) vs "
                    f"{bare_b!r} (line {cells[b].line_number})"
                ),
                suggestion=(
                    "Paired DE/EN slides must share the EN-derived slug. "
                    "Run `clm slides assign-ids --force` to resync."
                ),
            )
        )

    return findings


def _check_shared_cell_parity(de_path: Path, en_path: Path) -> list[Finding]:
    """Verify shared cells are byte-identical between a split slide pair.

    A split slide file (``.de.py`` / ``.en.py``) carries its tagged
    language-specific cells plus the *shared* (no-``lang``) cells copied
    verbatim from the bilingual source. The build pipeline routes each
    file to its own per-language pipeline (Phase 6, §2.6), so any drift
    between the two shared-cell streams produces silently divergent
    DE and EN output — exactly what the split format is meant to prevent.

    Reuses :func:`clm.slides.split._is_shared` to classify cells (so the
    rule stays aligned with Phase 5's split semantics) and compares the
    raw cell bytes via :class:`~clm.slides.raw_cells.RawCell`. The check
    is positional within the shared-cell stream: shared cell *i* in the
    DE file must be byte-identical to shared cell *i* in the EN file.
    Length mismatches surface as a single finding on the DE side so the
    error message can name the bilingual companion path cleanly.
    """
    findings: list[Finding] = []
    de_file = str(de_path)

    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")
    _, de_cells = split_raw_cells(de_text)
    _, en_cells = split_raw_cells(en_text)

    de_shared = [c for c in de_cells if _is_shared(c)]
    en_shared = [c for c in en_cells if _is_shared(c)]

    if len(de_shared) != len(en_shared):
        findings.append(
            Finding(
                severity="error",
                category="pairing",
                file=de_file,
                line=de_shared[0].line_number if de_shared else 1,
                message=(
                    f"split pair shared-cell count mismatch: "
                    f"DE has {len(de_shared)} shared cells, "
                    f"EN ({en_path.name}) has {len(en_shared)}"
                ),
                suggestion=(
                    "Shared (no-lang) cells must appear in the same order "
                    "in both '.de.py' and '.en.py'. Re-run "
                    "`clm slides unify` followed by `clm slides split` to "
                    "regenerate consistent split companions."
                ),
            )
        )
        return findings

    for i, (de_cell, en_cell) in enumerate(zip(de_shared, en_shared, strict=True)):
        if de_cell.lines == en_cell.lines:
            continue
        findings.append(
            Finding(
                severity="error",
                category="pairing",
                file=de_file,
                line=de_cell.line_number,
                message=(
                    f"split pair shared cell {i + 1} diverges between "
                    f"'.de.py' (line {de_cell.line_number}) and "
                    f"'.en.py' (line {en_cell.line_number} in "
                    f"{en_path.name})"
                ),
                suggestion=(
                    "Shared cells must be byte-identical between the DE "
                    "and EN companions. Reconcile the edit manually or "
                    "regenerate via `clm slides unify` + `clm slides split`."
                ),
            )
        )

    return findings


def _slide_files_to_split_pairs(slide_files: list[Path]) -> list[tuple[Path, Path]]:
    """Return every detected ``(de_path, en_path)`` pair in ``slide_files``.

    Reuses :func:`clm.core.topic_resolver._group_paths_into_units` so the
    grouping rule stays in lock-step with the build-time routing. Only
    units classified as ``split`` produce pairs; dual-format and
    half-pair units are surfaced as routing errors at build time and are
    skipped here.
    """
    pairs: list[tuple[Path, Path]] = []
    for unit in _group_paths_into_units(slide_files):
        if unit.kind != "split":
            continue
        assert unit.de_path is not None and unit.en_path is not None
        pairs.append((unit.de_path, unit.en_path))
    return pairs


def _check_slug_format(sid: str, cell: Cell, file_path: str) -> list[Finding]:
    """Single-cell slug format check. Empty list when the slug is valid."""
    if is_valid_slug(sid):
        return []
    return [
        Finding(
            severity="warning",
            category="pairing",
            file=file_path,
            line=cell.line_number,
            message=f"slide_id {sid!r} is not a valid kebab-case ASCII slug",
            suggestion=(
                "slide_id must match [!]?[a-z0-9]+(-[a-z0-9]+)*, "
                f"max {MAX_SLUG_LENGTH} chars (the leading `!` preserve marker "
                "is optional and does not count toward the cap)."
            ),
        )
    ]


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

    Cells inside a workshop range (markdown cell tagged ``workshop`` up to
    the next ``end-workshop`` / next ``workshop`` / EOF) are suppressed from
    the gap report — workshops are narrated live by the trainer, not via
    voiceover. The exception is the workshop heading itself (the cell
    carrying the ``workshop`` tag), which still requires voiceover so the
    recorded video has an intro to the exercise. See
    ``docs/claude/design/validator-workshop-voiceover-suppression.md``.
    """
    workshop_ranges = find_workshop_ranges(cells)

    gaps: list[dict] = []

    content_cells: list[Cell] = []
    content_origin: list[int] = []
    covered: set[int] = set()

    # Index (into ``content_cells``) of the most recent unmatched content
    # cell in each language stream. ``None`` means "no live content cell".
    last_de: int | None = None
    last_en: int | None = None
    last_any: int | None = None

    for orig_idx, cell in enumerate(cells):
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
        content_origin.append(orig_idx)
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
        # Workshops are narrated live, so suppress gap entries for their
        # internal cells. The workshop-entry cell itself (carrying the
        # ``workshop`` tag) is still checked.
        orig_idx = content_origin[idx]
        if is_in_workshop(orig_idx, workshop_ranges) and "workshop" not in meta.tags:
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
        # Split halves (``*.de.py`` / ``*.en.py``) carry a single language,
        # so the bilingual DE/EN count/adjacency checks don't apply — see
        # _check_pairing (issue #160). Cross-file parity is validated by the
        # directory/course entrypoints via _check_shared_cell_parity.
        is_split = split_lang_suffix(path) is not None
        findings.extend(_check_pairing(cells, file_str, is_split=is_split))

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
    - DE/EN cell adjacency (ordering)
    - slide_id presence / format / uniqueness / narrative adjacency
      (per-cell walks, so partial edits don't trigger false positives)

    The full pairing check is deliberately excluded — count and tag
    mismatches produce false positives during in-progress edits (e.g.,
    after editing the DE cell but before the EN counterpart). The
    ordering check is included because it skips categories with
    mismatched counts, so partial edits don't trigger it. The
    slide_id check walks each cell independently and is safe to run
    on in-progress files.

    Designed to complete in <2s for a single file.
    """
    file_str = str(path)
    text = path.read_text(encoding="utf-8")
    cells = parse_cells(text)

    findings: list[Finding] = []
    findings.extend(_check_format(cells, file_str))
    findings.extend(_check_tags(cells, file_str))
    findings.extend(_check_ordering(cells, file_str))
    findings.extend(_check_slide_ids(cells, file_str))

    return ValidationResult(
        files_checked=1,
        findings=findings,
    )


def validate_directory(
    path: Path,
    checks: list[str] | None = None,
) -> ValidationResult:
    """Validate all slide files at or under ``path``.

    Accepts a topic directory (validates direct slide files), a module
    directory, the ``slides/`` root, or any other parent directory
    (walks the subtree to find slide files). Use a course spec XML
    instead of a directory if the validation should be scoped to a
    specific course.

    Args:
        path: Path to a directory containing slide files (at any depth).
        checks: Which checks to run (passed to :func:`validate_file`).
    """
    slide_files = find_slide_files_recursive(path)
    all_findings: list[Finding] = []
    combined_review = ReviewMaterial() if (not checks or set(checks) & ALL_REVIEW_CHECKS) else None

    for sf in slide_files:
        result = validate_file(sf, checks=checks)
        all_findings.extend(result.findings)
        if combined_review is not None and result.review_material is not None:
            _merge_review_material(combined_review, result.review_material)

    # Phase 6: surface divergent shared cells between split pairs as
    # ``pairing`` errors. This runs even when only ``pairing`` checks are
    # requested via ``checks=`` because the parity is a pairing property.
    check_set = set(checks) if checks else set(ALL_CHECKS)
    if "pairing" in check_set:
        for de_path, en_path in _slide_files_to_split_pairs(slide_files):
            all_findings.extend(_check_shared_cell_parity(de_path, en_path))

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

    check_set = set(checks) if checks else set(ALL_CHECKS)

    for binding in spec.iter_topic_bindings():
        matches = matches_for_binding(topic_map, binding.topic_id, binding.effective_module)
        for match in matches:
            slide_files = find_slide_files(match.path)
            for sf in slide_files:
                result = validate_file(sf, checks=checks)
                all_findings.extend(result.findings)
                files_checked += 1
                if combined_review is not None and result.review_material is not None:
                    _merge_review_material(combined_review, result.review_material)

            # Phase 6 shared-cell parity per topic: walk the topic's split
            # pairs and emit pairing-error findings for divergent shared
            # cells. Scoped per-topic so different topics don't share state.
            if "pairing" in check_set:
                for de_path, en_path in _slide_files_to_split_pairs(slide_files):
                    all_findings.extend(_check_shared_cell_parity(de_path, en_path))

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
