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
from clm.notebooks.slide_parser import Cell, comment_token_for_path, parse_cells
from clm.slides.cpp_code_analysis import (
    DEFINITION_CATEGORIES,
    STATEMENT_CATEGORIES,
    classify_source,
)
from clm.slides.pairing import (
    TITLE_SLIDE_ID,
    build_slide_groups,
    is_title_macro_cell,
    split_twin_pair,
)
from clm.slides.raw_cells import RawCell
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
    category: str  # "format", "pairing", "tags", "code_export"
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

ALL_DETERMINISTIC_CHECKS = frozenset({"format", "pairing", "tags", "code_export"})
ALL_REVIEW_CHECKS = frozenset({"code_quality", "voiceover", "completeness"})
# Universe of valid check names — used to validate an explicit ``--checks`` /
# ``checks=`` request. A name in here is always *runnable* when asked for by
# name, even if it is excluded from the default bundle below.
ALL_CHECKS = ALL_DETERMINISTIC_CHECKS | ALL_REVIEW_CHECKS

# Checks that are valid to request explicitly but are never part of a default /
# "all" bundle. Voiceover coverage is opt-in because voiceover is optional per
# deck (issue #176) — running it by default floods voiceover-less decks with
# false-positive gap findings. Run it only by naming it: ``--checks voiceover``
# / ``checks=["voiceover"]``.
OPT_IN_CHECKS = frozenset({"voiceover"})

# The bundle used when the caller does not name specific checks: everything
# except the opt-in checks (i.e. format, pairing, tags, code_quality,
# completeness).
DEFAULT_CHECKS = ALL_CHECKS - OPT_IN_CHECKS

# Per-deck opt-in marker (#178): a deck that is meant to be fully narrated
# declares it with this directive comment in the file header (any line
# before the first cell marker), e.g. ``# clm: voiceover-coverage`` (or
# ``// clm: voiceover-coverage`` in a ``//``-comment-token deck). The
# default check bundle then includes the ``voiceover`` coverage check for
# THAT deck only — voiceover-less decks stay silent (#176). An explicit
# ``checks=[…]`` request is honored verbatim and ignores the marker.
VOICEOVER_COVERAGE_MARKER = "clm: voiceover-coverage"
_VOICEOVER_MARKER_RE = re.compile(r"^(?:#|//)\s*clm:\s*voiceover-coverage\s*$")

# Per-deck whitelist marker for the code-export conformance check (#331): a
# deck that intentionally defines ``main()`` (e.g. to discuss documentation of
# a complete program) declares it with ``// clm: allow-main`` in the file
# header. The compilable project export (#333) will skip generating its own
# ``main()`` for such decks.
ALLOW_MAIN_MARKER = "clm: allow-main"
_ALLOW_MAIN_MARKER_RE = re.compile(r"^(?:#|//)\s*clm:\s*allow-main\s*$")

# Header marker for decks whose code export legitimately cannot compile
# outside the kernel (e.g. xeus-specific includes, deliberate error
# demonstrations). The CMake generation (#333) marks such decks
# EXCLUDE_FROM_ALL: still buildable explicitly, skipped by "build all" and
# by the CI compile check.
NO_COMPILE_MARKER = "clm: no-compile"
_NO_COMPILE_MARKER_RE = re.compile(r"^(?:#|//)\s*clm:\s*no-compile\s*$")


def _has_header_marker(text: str, comment_token: str, marker_re: re.Pattern[str]) -> bool:
    """Whether the deck's file header contains a ``clm:`` directive comment.

    Scans only the file header — lines before the first ``<token> %%`` cell
    marker — so the directive is a per-file declaration, not cell content.
    """
    cell_marker = f"{comment_token} %%"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(cell_marker):
            break
        if marker_re.match(stripped):
            return True
    return False


def has_voiceover_coverage_marker(text: str, comment_token: str = "#") -> bool:
    """Whether the deck opts into voiceover coverage via its header (#178)."""
    return _has_header_marker(text, comment_token, _VOICEOVER_MARKER_RE)


def has_allow_main_marker(text: str, comment_token: str = "#") -> bool:
    """Whether the deck whitelists its ``main()`` definition via its header (#331)."""
    return _has_header_marker(text, comment_token, _ALLOW_MAIN_MARKER_RE)


def has_no_compile_marker(text: str, comment_token: str = "#") -> bool:
    """Whether the deck opts out of the default code-export build (#333)."""
    return _has_header_marker(text, comment_token, _NO_COMPILE_MARKER_RE)


def _check_malformed_markers(text: str, file_path: str, comment_token: str) -> list[Finding]:
    """Flag a near-miss cell marker — an extra leading comment char before ``%%``.

    The percent-format parser recognises a cell boundary via
    ``line.startswith(token + " %%")``. A typo with one extra leading comment
    char — ``## %% [markdown] …`` for the ``#`` token, ``/// %%`` for ``//`` — is
    NOT a boundary, so the line is swallowed into the previous cell's body, two
    cells merge, and the only downstream symptom is a misleading "unresolved
    duplicate slide_id …". ``_check_format`` only inspects parsed cells, so it
    never sees the swallowed line. Scan the RAW source for the typo signature at
    column 0 and report it directly with the line number.
    """
    findings: list[Finding] = []
    if comment_token == "#":
        # Two or more '#' before %% — but NOT a legit '# %%' (one hash). The
        # {2,} quantifier already excludes the single-hash boundary.
        near_miss = re.compile(r"^#{2,}\s*%%")
    elif comment_token == "//":
        # Three or more '/' before %% — '// %%' (two slashes) is the boundary.
        near_miss = re.compile(r"^/{3,}\s*%%")
    else:
        return findings

    for idx, line in enumerate(text.splitlines(), start=1):
        if near_miss.match(line):
            marker = line.split("%%", 1)[0].rstrip() + " %%"
            findings.append(
                Finding(
                    severity="error",
                    category="format",
                    file=file_path,
                    line=idx,
                    message=(
                        f"malformed cell marker {marker!r} at line {idx} — "
                        f"did you mean {comment_token + ' %%'!r}?"
                    ),
                    suggestion=(
                        f"Cell markers begin with {comment_token + ' %%'!r} "
                        "(a single comment token); remove the extra comment char."
                    ),
                )
            )
    return findings


def _check_format(cells: list[Cell], file_path: str) -> list[Finding]:
    """Check cell header syntax and structure."""
    findings: list[Finding] = []

    for cell in cells:
        header = cell.header
        meta = cell.metadata

        # Skip j2 cells — they have their own format
        if meta.is_j2:
            continue

        # Check that header starts with the language's "<token> %%" marker.
        marker = f"{cell.comment_token} %%"
        if not header.startswith(marker):
            findings.append(
                Finding(
                    severity="error",
                    category="format",
                    file=file_path,
                    line=cell.line_number,
                    message=f"Cell header does not start with {marker!r}: {header!r}",
                    suggestion=f"Cell headers must begin with {marker!r}",
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


def _ends_with_blank_line(cell: RawCell) -> bool:
    """True iff ``cell``'s body ends with at least one blank line.

    Inter-cell separation is stored as trailing empty body lines on the *preceding*
    cell (``reconstruct`` joins cells with a single ``\\n``), so a preceding cell
    that ends with a blank line is what puts a blank line before the next cell.
    """
    return len(cell.lines) > 1 and cell.lines[-1].strip() == ""


def _is_blank_comment(line: str) -> bool:
    """True iff ``line`` is a markdown blank-comment line (just ``#`` or ``//``)."""
    return line.strip() in ("#", "//")


def _check_cell_separation(raw_cells: list[RawCell], file_path: str) -> list[Finding]:
    """Warn when a cell is not separated from the previous one by a blank line.

    A blank line is required before every cell **except a j2 cell** — the canonical
    title-header block writes ``# j2 ... import header`` immediately followed by
    ``# {{ header(...) }}`` with no blank between the two directives, so j2 cells
    tight-couple and are exempt. Cells run together are valid percent-format but
    render and diff poorly. Operates on :class:`RawCell` because the parsed ``Cell``
    model strips inter-cell whitespace.
    """
    findings: list[Finding] = []
    for prev, cur in zip(raw_cells, raw_cells[1:], strict=False):
        if cur.metadata.is_j2:
            continue
        if not _ends_with_blank_line(prev):
            findings.append(
                Finding(
                    severity="warning",
                    category="format",
                    file=file_path,
                    line=cur.line_number,
                    message="cell is not separated from the previous cell by a blank line",
                    suggestion="Insert a blank line between cells (`clm slides normalize` fixes this).",
                )
            )
    return findings


def _check_markdown_blank_lead(raw_cells: list[RawCell], file_path: str) -> list[Finding]:
    """Warn when a markdown cell body does not start with a blank comment line.

    A markdown cell should open with a ``#`` line before its content
    (``# %% [markdown]`` / ``#`` / ``# <content>``). The leading ``#`` is what makes
    content that starts with a bullet or heading render correctly. j2 cells (the
    title macro) are exempt and empty-body cells are skipped.
    """
    findings: list[Finding] = []
    for cell in raw_cells:
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "markdown":
            continue
        body = cell.lines[1:]
        if not body:
            continue
        if not _is_blank_comment(body[0]):
            findings.append(
                Finding(
                    severity="warning",
                    category="format",
                    file=file_path,
                    line=cell.line_number,
                    message="markdown cell body does not start with a blank comment line (`#`)",
                    suggestion=(
                        "Begin the markdown cell with a `#` line "
                        "(`clm slides normalize` fixes this)."
                    ),
                )
            )
    return findings


def _check_preamble_code(
    raw_cells: list[RawCell], file_path: str, comment_token: str = "#"
) -> list[Finding]:
    """Warn when executable code is folded into a leading j2 (header) cell body.

    Code that sits between the ``# {{ header(...) }}`` macro call and the first
    ``%% `` cell has no cell marker of its own, so jupytext folds it into the
    header cell. At build time it lands in the title markdown — silently dropped
    from a DE build (it rides the EN title in the bilingual macro) yet kept in
    the split DE half, so bilingual and split builds diverge (issue #253). A
    *warning*, not an error: the source still round-trips through split/unify,
    and escalating it would block the 1.8 validator gate. Operates on
    :class:`RawCell` because the parsed ``Cell`` model discards the j2 cell body.
    """
    from clm.slides.preamble_code import find_preamble_code

    findings: list[Finding] = []
    for finding in find_preamble_code(raw_cells, comment_token):
        findings.append(
            Finding(
                severity="warning",
                category="format",
                file=file_path,
                line=finding.first_code_line,
                message=(
                    "executable code appears before the first `%% ` cell marker; "
                    "it is folded into the header cell and is silently dropped or "
                    "mis-rendered across bilingual and split builds (issue #253)"
                ),
                suggestion=(
                    "Move the code into its own `%% ` code cell "
                    "(`clm slides normalize` fixes this automatically)."
                ),
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

    # The most recent code cell per language stream — lets the orphaned
    # 'completed' finding hint at a mis-tagged 'keep' predecessor (#233).
    last_code_cell: dict[str | None, Cell] = {}
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
                # An incremental class/function build whose "before" cell was
                # tagged 'keep' instead of 'start' is a recurring authoring
                # slip (#233) — point straight at it when the shape matches.
                prev_code = last_code_cell.get(meta.lang)
                if (
                    meta.cell_type == "code"
                    and prev_code is not None
                    and "keep" in prev_code.metadata.tags
                ):
                    suggestion = (
                        f"The preceding code cell (line {prev_code.line_number}) is "
                        "tagged 'keep' — did you mean 'start'? Otherwise add a "
                        "'start' cell before this 'completed' cell."
                    )
                else:
                    suggestion = "Add a cell with tag 'start' before this 'completed' cell"
                findings.append(
                    Finding(
                        severity="error",
                        category="tags",
                        file=file_path,
                        line=cell.line_number,
                        message="'completed' tag without a preceding 'start' cell",
                        suggestion=suggestion,
                    )
                )

        if meta.cell_type == "code":
            last_code_cell[meta.lang] = cell

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
    token = cell.comment_token
    for line in cell.content.split("\n"):
        if line.startswith(token + " "):
            inner = line[len(token) + 1 :]
        elif line.startswith(token):
            inner = line[len(token) :]
        else:
            continue
        inner = inner.strip()
        if inner.startswith("#"):  # markdown heading marker (always "#")
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


def _check_workshop_tag_symmetry(cells: list[Cell], file_path: str) -> list[Finding]:
    """Flag DE/EN slide-heading pairs that disagree on a slide-scoped tag.

    ``workshop`` and ``end-workshop`` mark a *slide* (a DE/EN pair) as a
    workshop boundary; they belong to the slide, not a single language. The
    notebook build derives workshop ranges from these tags to decide which
    code cells to blank in Code-Along/Partial output.

    A *bilingual* build tolerates the tag on only one language's heading: its
    range scan runs over the interleaved stream, so a tag on either half
    establishes a range that — by index — also covers the other language. A
    single-language *split* build does not: the untagged language loses the
    workshop range and leaks solution code into Code-Along/Partial. This check
    flags the asymmetry on the bilingual source so it is fixed before
    splitting. On a single-language file there are no DE/EN pairs, so it is a
    no-op. Run ``clm slides normalize`` to symmetrize the tags automatically.
    """
    findings: list[Finding] = []
    for group in build_slide_groups(cells):
        if len(group) != 2:
            continue
        i, j = group
        for tag in ("workshop", "end-workshop"):
            i_has = tag in cells[i].metadata.tags
            j_has = tag in cells[j].metadata.tags
            if i_has == j_has:
                continue
            tagged, untagged = (cells[i], cells[j]) if i_has else (cells[j], cells[i])
            findings.append(
                Finding(
                    severity="warning",
                    category="pairing",
                    file=file_path,
                    line=untagged.line_number,
                    message=(
                        f"slide-heading pair disagrees on the '{tag}' tag: the "
                        f"{tagged.metadata.lang or '?'} heading has it but the "
                        f"{untagged.metadata.lang or '?'} heading does not — a "
                        f"split build of the untagged language will miss the "
                        f"workshop range and may leak solution code"
                    ),
                    suggestion=(
                        f"Add the '{tag}' tag to the "
                        f"{untagged.metadata.lang or '?'} heading, or run "
                        f"`clm slides normalize` to symmetrize it."
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
        # ``slides normalize`` is::
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
    ``slides normalize`` does. When counts are mismatched the pairing
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
                        severity="error",
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
                            "Run `clm slides normalize` to fix mechanically."
                        ),
                    )
                )
                break  # one finding per pair

    return findings


def _check_slide_ids(cells: list[Cell], file_path: str) -> list[Finding]:
    """Verify slide_id metadata: presence, format, uniqueness, adjacency, pair-consistency.

    A *missing* slide_id on a ``slide``/``subslide`` cell is an
    ``error`` as of CLM 1.8 (it was a warning through 1.7, during the
    PythonCourses migration sweep). All other rules — duplicate ids,
    narrative-cell id rules, slug-format violations, DE/EN pair
    mismatch — fire at the severity their semantics warrant
    (errors for content bugs, warnings for style/format drift).

    Narrative (voiceover/notes) ids accept **two** conventions: the legacy
    inherited form (id equals the preceding slide/subslide anchor's id) and
    the sync-v3 *own-id* form (§12.1 / #520 — a unique id of the
    narrative's own, minted by ``clm slides normalize --stamp-ids``). An
    own id participates in the duplicate check — keyed on the adjacent
    DE/EN narrative twin group, exactly like slide pairs — so the stale
    copy-paste id this rule has always guarded against (an id that equals
    some *other* cell's id) still errors, as a duplicate.

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
    cell_to_group: dict[int, str] = {}
    for gi, group in enumerate(slide_groups):
        for ci in group:
            cell_to_group[ci] = f"slide:{gi}"

    # Narrative twin groups: directly-adjacent voiceover/notes cells with
    # differing langs and the same role are one logical narrative (the
    # DE/EN pair shares its own id, like slide pairs). Direct adjacency is
    # the interleave convention `normalize --stamp-ids` stamps under.
    for i in range(len(cells) - 1):
        ma, mb = cells[i].metadata, cells[i + 1].metadata
        if (
            ma.is_narrative
            and mb.is_narrative
            and not ma.is_j2
            and not mb.is_j2
            and ma.lang
            and mb.lang
            and ma.lang != mb.lang
            and ("voiceover" in ma.tags) == ("voiceover" in mb.tags)
            and i not in cell_to_group
            and i + 1 not in cell_to_group
        ):
            cell_to_group[i] = cell_to_group[i + 1] = f"narr:{i}"

    # Bare slide_id -> (group key, line) of first sighting. A second
    # occurrence in the *same* group is the pair sharing the id and is
    # fine; in a *different* group it's a real duplicate.
    bare_first_seen: dict[str, tuple[str, int]] = {}

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
                        severity="error",
                        category="pairing",
                        file=file_path,
                        line=cell.line_number,
                        message="slide/subslide cell missing slide_id",
                        suggestion=(
                            "Run `clm slides assign-ids <dir>` (EN-authority pair "
                            "minting) — or `clm slides sync` for a split deck — to add "
                            "stable identifiers; avoid per-file `assign-ids` on a single "
                            "split half (#162)."
                        ),
                    )
                )
                # Don't touch current_slide_id — keep the previous anchor
                # so narrative cells after this hole still validate.
                continue

            bare = strip_preserve_marker(sid)
            findings.extend(_check_slug_format(sid, cell, file_path))

            group_key = cell_to_group[idx]
            prev = bare_first_seen.get(bare)
            if prev is None:
                bare_first_seen[bare] = (group_key, cell.line_number)
            elif prev[0] != group_key:
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
                # The sync-v3 own-id convention (§12.1 / #520): a narrative id
                # of the cell's own is legal iff it is unique. Register it in
                # the duplicate map keyed on the narrative twin group — a
                # stale copy-paste id (one that equals some other slide's or
                # narrative's id) still errors, now as a duplicate.
                group_key = cell_to_group.get(idx, f"narr-solo:{idx}")
                prev = bare_first_seen.get(bare)
                if prev is None:
                    bare_first_seen[bare] = (group_key, cell.line_number)
                elif prev[0] != group_key:
                    findings.append(
                        Finding(
                            severity="error",
                            category="pairing",
                            file=file_path,
                            line=cell.line_number,
                            message=(
                                f"voiceover/notes slide_id={bare!r} duplicates an id "
                                f"first seen at line {prev[1]} (and does not match the "
                                f"preceding slide {current_slide_id!r})"
                            ),
                            suggestion=(
                                "A narrative id is either the preceding slide's id "
                                "(legacy inherit) or a unique id of its own "
                                "(`clm slides normalize --stamp-ids`); this one is "
                                "neither — likely a stale id left by copy-paste."
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
    _, de_cells = split_raw_cells(de_text, comment_token_for_path(de_path))
    _, en_cells = split_raw_cells(en_text, comment_token_for_path(en_path))

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


def _check_split_tag_parity(de_path: Path, en_path: Path) -> list[Finding]:
    """Flag any tag-set asymmetry between the cross-language twins of a split pair.

    A split deck's two halves carry the same cells in the same order — shared
    (no-``lang``) cells byte-identical, localized cells as language twins. Tags
    are language-independent, so each cell and its twin must carry the same tag
    set. A one-sided tag edit (e.g. adding ``keep`` to one half) is invisible to
    the content *and* to :func:`_check_shared_cell_parity` (which compares only
    shared cells, so it never sees a localized cell's tags). This check pairs the
    halves' non-j2 cells positionally and warns on any tag-set mismatch — covering
    localized markdown *and* id-less localized code (the cell the #198 report hit).

    When the two non-j2 streams differ in length — a structural edit mid-flight,
    or an add/remove not yet synced — it stays silent rather than mis-pair across
    the offset; the count itself is a separate concern surfaced by the shared-cell
    parity check. Issue #198: ``clm slides sync`` mirrors a *recent* one-sided tag
    edit automatically; this check is the safety net for a committed asymmetry sync
    can no longer attribute to one side.
    """
    findings: list[Finding] = []
    de_token = comment_token_for_path(de_path)
    en_token = comment_token_for_path(en_path)
    _, de_cells = split_raw_cells(de_path.read_text(encoding="utf-8"), de_token)
    _, en_cells = split_raw_cells(en_path.read_text(encoding="utf-8"), en_token)
    de_nonj2 = [c for c in de_cells if not c.metadata.is_j2]
    en_nonj2 = [c for c in en_cells if not c.metadata.is_j2]
    if len(de_nonj2) != len(en_nonj2):
        return findings  # structural mismatch — not a tag-parity question

    for i, (de_cell, en_cell) in enumerate(zip(de_nonj2, en_nonj2, strict=True)):
        de_tags = set(de_cell.metadata.tags)
        en_tags = set(en_cell.metadata.tags)
        if de_tags == en_tags:
            continue
        only_de = sorted(de_tags - en_tags)
        only_en = sorted(en_tags - de_tags)
        detail = ""
        if only_de:
            detail += f"; only on DE: {only_de}"
        if only_en:
            detail += f"; only on EN: {only_en}"
        findings.append(
            Finding(
                severity="warning",
                category="pairing",
                file=str(de_path),
                line=de_cell.line_number,
                message=(
                    f"split pair cell {i + 1} has mismatched tags between "
                    f"'.de.py' (line {de_cell.line_number}, {sorted(de_tags)}) and "
                    f"'.en.py' (line {en_cell.line_number} in {en_path.name}, "
                    f"{sorted(en_tags)}){detail}"
                ),
                suggestion=(
                    "Tags are language-independent and must match across the DE/EN "
                    "twins. Run `clm slides sync` to mirror a recent one-sided tag "
                    "edit, or align the tags manually."
                ),
            )
        )
    return findings


def _check_split_slide_id_parity(de_path: Path, en_path: Path) -> list[Finding]:
    """Verify the ``.de.py`` / ``.en.py`` halves carry the same slide_id sequence.

    ``slide_id`` is the cross-language join key (issue #162): voiceover
    ``for_slide`` resolution, ``clm slides unify`` (which requires
    ``de_id == en_id``), and ``extract`` / ``inline`` all rely on the two halves
    agreeing on the **set and order** of slide ids. A born-split deck, a per-file
    ``clm slides assign-ids`` on one half, or a hand-edited id silently diverges
    them. This is the **detective** that makes that loud — the core check of the
    #162 pre-commit gate.

    Compares the bare (preserve-marker-stripped) slide_ids of slide-start cells
    in source order. Findings are ``warning`` severity, consistent with the rest
    of the slide_id family (the gate runs with ``--fail-on warning``); they may
    become ``error`` in CLM 1.8 alongside the other slide_id checks.
    """
    findings: list[Finding] = []
    de_file = str(de_path)

    de_cells = parse_cells(de_path.read_text(encoding="utf-8"), comment_token_for_path(de_path))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"), comment_token_for_path(en_path))

    def _ids(cells: list[Cell]) -> list[str]:
        return [
            strip_preserve_marker(c.metadata.slide_id)
            for c in cells
            if c.metadata.is_slide_start and c.metadata.slide_id
        ]

    de_ids = _ids(de_cells)
    en_ids = _ids(en_cells)
    anchor_line = next(
        (c.line_number for c in de_cells if c.metadata.is_slide_start and c.metadata.slide_id),
        1,
    )
    de_set, en_set = set(de_ids), set(en_ids)

    if de_set != en_set:
        only_de = sorted(de_set - en_set)
        only_en = sorted(en_set - de_set)
        detail = ""
        if only_de:
            detail += f"; only on DE: {only_de}"
        if only_en:
            detail += f"; only on EN: {only_en}"
        findings.append(
            Finding(
                severity="warning",
                category="pairing",
                file=de_file,
                line=anchor_line,
                message=(
                    f"split pair slide_id sets diverge between '.de.py' and "
                    f"'.en.py' ({en_path.name}){detail}"
                ),
                suggestion=(
                    "The .de.py / .en.py halves must carry the same slide_id set — "
                    "it is the cross-language join key for voiceover for_slide, "
                    "`clm slides unify`, and extract/inline. Route structural "
                    "changes through `clm slides sync` (which mints/migrates ids "
                    "onto both halves); avoid per-file `clm slides assign-ids` on a "
                    "split half."
                ),
            )
        )
    elif de_ids != en_ids:
        findings.append(
            Finding(
                severity="warning",
                category="pairing",
                file=de_file,
                line=anchor_line,
                message=(
                    f"split pair slide_id order diverges: DE order {de_ids} != EN "
                    f"order {en_ids} ({en_path.name})"
                ),
                suggestion=(
                    "The slide_id sequence must match across the DE/EN halves. Run "
                    "`clm slides sync` to mirror the reordering onto both."
                ),
            )
        )

    return findings


def _check_split_companion_for_slide_parity(de_path: Path, en_path: Path) -> list[Finding]:
    """Verify the voiceover companions of a split pair narrate the same slides.

    Separated voiceover lives in sibling companion files
    (``voiceover_X.de.py`` / ``voiceover_X.en.py``); every narration cell is
    bound to its owning slide by ``for_slide`` (= that slide's ``slide_id``).
    The build merge resolves ``for_slide`` against the *same-language* deck
    half, so if one companion narrates a slide its twin does not, that language
    ships with missing narration and nothing says so. This is the
    **both-language voiceover compatibility check** — the companion arm of the
    #162 detective and part of the pre-commit gate (design §9/§10). It is the
    natural extension of :func:`_check_split_slide_id_parity`: the slide_id
    parity guards the join key on the deck; this guards the same key on the
    companions that reference it.

    Compares the *set* of ``for_slide`` references (preserve-marker stripped),
    not their order or multiplicity — one language may legitimately split a
    slide's narration across a different number of cells. Fires only when a
    companion exists on at least one side; a deck pair with no voiceover at all
    is clean. A one-sided companion (one language has voiceover, the other
    none) is surfaced too — that is just the degenerate divergence where one
    set is empty. Findings are ``warning`` severity, consistent with the rest
    of the slide_id family (the gate runs with ``--fail-on warning``).
    """
    # ``companion_path`` lives in the voiceover layer, which sits *above* the
    # validator/split layer; import it lazily (as ``split.py`` does for the
    # companion seam) so the validator carries no hard dependency on the
    # optional voiceover tooling.
    from clm.slides.voiceover_tools import companion_path, resolve_companion

    # Resolve the *existing* companion in either layout (``voiceover/`` subdir or
    # sibling); fall back to the nominal sibling name only for messaging.
    de_comp = resolve_companion(de_path)
    en_comp = resolve_companion(en_path)
    de_exists, en_exists = de_comp is not None, en_comp is not None
    if not de_exists and not en_exists:
        return []

    # One-sided companion: the language without a companion ships no narration.
    if de_exists != en_exists:
        if de_exists:
            assert de_comp is not None
            present, absent_name, missing_lang = de_comp, companion_path(en_path).name, "EN"
        else:
            assert en_comp is not None
            present, absent_name, missing_lang = en_comp, companion_path(de_path).name, "DE"
        return [
            Finding(
                severity="warning",
                category="pairing",
                file=str(present),
                line=1,
                message=(
                    f"voiceover companion {present.name} exists but its twin "
                    f"{absent_name} does not — the {missing_lang} half ships "
                    f"without narration"
                ),
                suggestion=(
                    "Separated voiceover must be present (or absent) on both "
                    "halves of a split deck. Extract voiceover for the missing "
                    f"language ({missing_lang}) too, or remove the lone "
                    "companion."
                ),
            )
        ]

    def _for_slide_set(path: Path) -> set[str]:
        return {
            strip_preserve_marker(c.metadata.for_slide)
            for c in parse_cells(path.read_text(encoding="utf-8"), comment_token_for_path(path))
            if c.metadata.for_slide
        }

    # Both companions exist here (the one-sided branch above returned).
    assert de_comp is not None and en_comp is not None
    de_set = _for_slide_set(de_comp)
    en_set = _for_slide_set(en_comp)
    if de_set == en_set:
        return []

    only_de = sorted(de_set - en_set)
    only_en = sorted(en_set - de_set)
    detail = ""
    if only_de:
        detail += f"; only on DE: {only_de}"
    if only_en:
        detail += f"; only on EN: {only_en}"
    return [
        Finding(
            severity="warning",
            category="pairing",
            file=str(de_comp),
            line=1,
            message=(
                f"split pair voiceover companion for_slide sets diverge between "
                f"{de_comp.name} and {en_comp.name}{detail}"
            ),
            suggestion=(
                "Each narration cell's for_slide is the slide_id of the slide it "
                "narrates; the DE/EN companions must cover the same set of slides, "
                "or one language ships with missing voiceover. Run `clm slides sync` "
                "to propagate the missing narration to the other language (it now "
                "reconciles separated voiceover companions), or add it by hand."
            ),
        )
    ]


def _check_companion_location_ambiguity(path: Path) -> list[Finding]:
    """Warn when a slide's voiceover companion exists in *both* layouts.

    A companion present at ``voiceover/<name>`` *and* the sibling ``<name>`` is
    ambiguous: the build's ``resolve_companion`` silently prefers the relocated
    (subdir) copy, so the sibling's narration would be ignored. Surface it so the
    duplicate can be reconciled to a single companion per slide. Warning
    severity, consistent with the rest of the companion-pairing family.
    """
    from clm.slides.voiceover_tools import companion_locations

    locations = companion_locations(path)
    if len(locations) < 2:
        return []
    winner, *shadowed = locations  # resolve_companion order: voiceover/ before sibling
    shadowed_names = ", ".join(p.name for p in shadowed)
    return [
        Finding(
            severity="warning",
            category="pairing",
            file=str(path),
            line=1,
            message=(
                f"voiceover companion for '{path.name}' exists in two locations — "
                f"the build uses '{winner}' and ignores '{shadowed_names}'"
            ),
            suggestion=(
                "Keep a single companion per slide: remove the stale copy in the "
                "other location so the narration is unambiguous."
            ),
        )
    ]


def _check_companion_for_slide_resolves(path: Path) -> list[Finding]:
    """Flag companion narration whose ``for_slide`` matches no slide in its deck.

    The build's voiceover merge silently **drops** any companion cell whose
    ``for_slide`` resolves to no ``slide_id`` in the slide it accompanies — a lost
    speaker-notes / voiceover, usually because a ``slide_id`` was renamed or the
    slide moved to another deck. The build escalates each drop to a ``voiceover``
    error (failing under ``--fail-on-error``), so today the loss is only discovered
    at build time. This is the static analogue, caught by ``clm slides validate``
    before the (expensive) build.

    The companion-vs-deck resolution reuses the build's own
    :func:`~clm.slides.voiceover_tools.merge_voiceover_text`, so the match honors
    every fallback the build applies — the ``for_slide="title"`` greeting convention
    and the ``vo_anchor`` placement — and the validator can never disagree with the
    build about what is dropped (in particular, ``for_slide="title"`` is not a false
    positive). A deck with no companion (``resolve_companion`` ``None``) yields
    nothing, so a voiceover-less deck stays silent.
    """
    from clm.slides.voiceover_tools import merge_voiceover_text, resolve_companion

    companion = resolve_companion(path)
    if companion is None:
        return []
    _, unmatched = merge_voiceover_text(
        path.read_text(encoding="utf-8"),
        companion.read_text(encoding="utf-8"),
        comment_token_for_path(path),
    )
    findings: list[Finding] = []
    for for_slide in unmatched:
        target = "(cell has no for_slide)" if for_slide == "<no for_slide>" else repr(for_slide)
        findings.append(
            Finding(
                severity="error",
                category="pairing",
                file=str(companion),
                line=1,
                message=(
                    f"voiceover companion {companion.name}: for_slide {target} matches no "
                    f"slide_id in {path.name} — the build drops this narration from output"
                ),
                suggestion=(
                    "A slide_id was renamed, or its slide moved to another deck. Re-align "
                    "the for_slide to an existing slide_id in this deck, or move the "
                    "narration into the companion of the deck that now owns the slide. "
                    "(`clm voiceover inline` then re-extract also re-aligns it.)"
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

        # Detect leading comments in code cells (either comment family)
        lines = content.strip().split("\n")
        if (
            lines
            and lines[0].startswith(("#", "//"))
            and not lines[0].startswith(("# %%", "// %%"))
        ):
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
    correctly. The canonical layout produced by ``slides normalize`` is:

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

        if "voiceover" in meta.tags:
            # Only real voiceover cells count as coverage. ``notes`` cells are
            # also ``is_narrative`` but must not satisfy the voiceover
            # requirement (issue #360).
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
        # Remove the comment prefix (either comment family)
        if line.startswith("# "):
            inner = line[2:]
        elif line.startswith("// "):
            inner = line[3:]
        elif line.startswith("#"):
            inner = line[1:]
        elif line.startswith("//"):
            inner = line[2:]
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
# Code-export conformance (#331)
# ---------------------------------------------------------------------------

# Jinja templating inside a code-cell *body* (the j2 header cells are a
# separate cell type). ``{%`` cannot appear in valid C++; ``{{`` can (nested
# brace-init), so the expression form additionally requires the whitespace
# the course macros use: ``{{ header(...) }}``. The string-literal escape
# idiom ``{{'{{...}}'}}`` (Jinja emitting literal braces) is intentionally
# not flagged: it expands to plain C++ before the notebook pipeline and the
# code export ever see it.
_JINJA_IN_CODE_RE = re.compile(r"\{%|\{\{\s")


def _check_code_export(
    cells: list[Cell], file_path: str, *, allow_main: bool = False
) -> list[Finding]:
    """Check the structural invariants the compilable C++ export relies on.

    The export (#333) turns each (language × kind) view of a deck into one
    translation unit, so within such a view no variable, function (same
    normalized signature incl. const-ness), or type may be defined twice —
    which is also what xeus-cpp requires at runtime. Checks run per language
    view: a ``lang="de"`` cell and its ``lang="en"`` sibling never coexist in
    one output, so paired definitions are NOT redefinitions; untagged cells
    count for both views. The view checked is the *completed* one (``start``
    cells are replaced by their ``completed``/``alt`` twin there; ``del``
    cells appear in no output).

    Also flags ``main()`` definitions (the export generates its own ``main``
    unless the deck carries the ``clm: allow-main`` header marker), Jinja
    directives inside code cells (untransformable), and — informationally —
    cells mixing definitions with statements.
    """
    findings: list[Finding] = []
    # (lang, kind, key) -> line of first definition, in the completed view.
    seen: dict[tuple[str, str, str], int] = {}
    # (kind, display-name, first line, line) -> langs, to merge the DE and EN
    # findings of an untagged-cell redefinition into one.
    redefs: dict[tuple[str, str, int, int], set[str]] = {}

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "code":
            continue
        source = cell.content
        if not source.strip():
            continue

        if _JINJA_IN_CODE_RE.search(source):
            findings.append(
                Finding(
                    severity="warning",
                    category="code_export",
                    file=file_path,
                    line=cell.line_number,
                    message="Jinja directive inside a code cell",
                    suggestion=(
                        "The code export classifies raw cell source and cannot "
                        "transform templated code; move the Jinja part into a "
                        "markdown cell or expand it manually."
                    ),
                )
            )
            continue

        items = classify_source(source)

        categories = {item.category for item in items}
        has_statement = bool(categories & STATEMENT_CATEGORIES)
        has_definition = bool(categories & DEFINITION_CATEGORIES) or "main_def" in categories
        has_variable = "var_decl" in categories
        if has_statement and (has_definition or has_variable):
            findings.append(
                Finding(
                    severity="info",
                    category="code_export",
                    file=file_path,
                    line=cell.line_number,
                    message="Code cell mixes definitions and statements",
                    suggestion=(
                        "The export splits such cells automatically, but separate "
                        "definition and usage cells read better on slides."
                    ),
                )
            )

        in_completed_view = "start" not in meta.tags and "del" not in meta.tags
        langs = (meta.lang,) if meta.lang else ("de", "en")
        for item in items:
            if item.category == "main_def" and not allow_main:
                findings.append(
                    Finding(
                        severity="error",
                        category="code_export",
                        file=file_path,
                        line=cell.line_number,
                        message="main() defined in a deck without the allow-main marker",
                        suggestion=(
                            "The code export generates its own main(). If this deck "
                            f"intentionally defines main(), add a `{ALLOW_MAIN_MARKER}` "
                            "comment to the file header (before the first cell)."
                        ),
                    )
                )
            if not in_completed_view:
                continue
            if item.category == "var_decl" and item.name:
                kind, key, display = "variable", item.name, item.name
            elif item.category in ("fn_def", "member_fn_def") and item.signature:
                kind, key, display = "function", item.signature, item.signature
            elif item.category == "type_def" and item.name:
                kind, key, display = "type", item.name, item.name
            else:
                continue
            for lang in langs:
                first = seen.setdefault((lang, kind, key), cell.line_number)
                if first != cell.line_number:
                    redefs.setdefault((kind, display, first, cell.line_number), set()).add(lang)

    for (kind, display, first_line, line), langs_hit in sorted(
        redefs.items(), key=lambda kv: (kv[0][3], kv[0][0], kv[0][1])
    ):
        view = (
            "both language views" if len(langs_hit) > 1 else f'lang="{next(iter(langs_hit))}" view'
        )
        findings.append(
            Finding(
                severity="error",
                category="code_export",
                file=file_path,
                line=line,
                message=(
                    f"{kind} '{display}' redefined (first defined at line {first_line}, {view})"
                ),
                suggestion=(
                    "xeus-cpp forbids redefinition and the code export emits one "
                    "translation unit per deck; rename the entity or remove the "
                    "duplicate definition."
                ),
            )
        )

    return sorted(findings, key=lambda f: f.line)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_file(
    path: Path,
    checks: list[str] | None = None,
    *,
    cross_file_parity: bool = True,
    marker_opt_in: bool | None = None,
) -> ValidationResult:
    """Validate a single slide file.

    Args:
        path: Path to the ``.py`` slide file.
        checks: Which checks to run. Default (``None``): every check except
            the opt-in ones — ``format``, ``pairing``, ``tags``,
            ``code_quality``, ``completeness``.
            Deterministic: ``"format"``, ``"pairing"``, ``"tags"``,
            ``"code_export"`` (the latter applies to ``.cpp`` decks only and
            is a no-op elsewhere, #331).
            Review: ``"code_quality"``, ``"voiceover"``, ``"completeness"``.
            ``"voiceover"`` is **opt-in** (issue #176): voiceover is optional
            per deck, so coverage runs only when you name it explicitly in
            ``checks`` — never as part of the default bundle — or when the
            deck itself opts in via the ``clm: voiceover-coverage`` header
            marker (#178, see ``marker_opt_in``).
        cross_file_parity: When ``True`` (the default for standalone callers —
            the CLI single-file path, MCP, the pre-commit gate) and ``path`` is
            a split half whose twin exists on disk, run the #162 cross-file
            parity detectives against the twin: ``slide_id`` set/order parity on
            the deck, and ``for_slide`` set parity on the voiceover companions
            (the both-language voiceover compatibility check). Directory/course
            entrypoints pass ``False`` and run those once at their own scope
            instead, so a directory run does not duplicate the findings.
        marker_opt_in: Whether the per-deck ``clm: voiceover-coverage`` header
            marker (#178) may promote ``voiceover`` into the effective check
            set. ``None`` (the default) resolves to ``checks is None`` — the
            marker applies on a default-bundle run and an explicit ``checks``
            list is honored verbatim. The CLI passes ``True`` for its own
            default run (which names the deterministic checks explicitly) and
            ``False`` when the user gave ``--checks``.

    Returns:
        A :class:`ValidationResult` with findings and optional review material.
    """
    file_str = str(path)
    comment_token = comment_token_for_path(path)

    text = path.read_text(encoding="utf-8")
    cells = parse_cells(text, comment_token)

    check_set = set(checks) if checks else set(DEFAULT_CHECKS)
    honor_marker = (checks is None) if marker_opt_in is None else marker_opt_in
    if honor_marker and "voiceover" not in check_set:
        if has_voiceover_coverage_marker(text, comment_token):
            check_set.add("voiceover")

    findings: list[Finding] = []

    if "format" in check_set:
        findings.extend(_check_format(cells, file_str))
        # A near-miss cell marker (`## %%`, `/// %%`) is swallowed as body before
        # parsing, so it never reaches a parsed Cell — scan the raw source for it.
        findings.extend(_check_malformed_markers(text, file_str, comment_token))
        # Cell spacing runs on the raw (whitespace-preserving) cells, since the
        # parsed Cell model strips the blank lines these checks inspect.
        _, raw_cells = split_raw_cells(text, comment_token)
        findings.extend(_check_cell_separation(raw_cells, file_str))
        findings.extend(_check_markdown_blank_lead(raw_cells, file_str))
        findings.extend(_check_preamble_code(raw_cells, file_str, comment_token))
    if "tags" in check_set:
        findings.extend(_check_tags(cells, file_str))
    if "pairing" in check_set:
        # Split halves (``*.de.py`` / ``*.en.py``) carry a single language,
        # so the bilingual DE/EN count/adjacency checks don't apply — see
        # _check_pairing (issue #160). Cross-file parity is validated by the
        # directory/course entrypoints via _check_shared_cell_parity.
        is_split = split_lang_suffix(path) is not None
        findings.extend(_check_pairing(cells, file_str, is_split=is_split))
        # A voiceover companion duplicated across both layouts (voiceover/ +
        # sibling) is ambiguous — the build silently prefers one. Flag it.
        findings.extend(_check_companion_location_ambiguity(path))
        # A companion narration cell whose for_slide matches no slide_id in its
        # deck is silently dropped by the build (a lost voiceover) — catch it here,
        # statically, instead of only at (expensive) build time.
        findings.extend(_check_companion_for_slide_resolves(path))
        # workshop/end-workshop tags must match across a DE/EN heading pair;
        # the asymmetry only manifests on the bilingual source (a split half
        # has no DE/EN pairs to compare).
        if not is_split:
            findings.extend(_check_workshop_tag_symmetry(cells, file_str))
        # #162 detective: when validating a split half standalone and its twin
        # exists on disk, check cross-file slide_id parity (the join key). A
        # directory/course run handles this once at that scope instead
        # (cross_file_parity=False) so the finding is not duplicated per file.
        if cross_file_parity and is_split:
            pair = split_twin_pair(path)
            if pair is not None:
                findings.extend(_check_split_slide_id_parity(*pair))
                findings.extend(_check_split_companion_for_slide_parity(*pair))
    if "code_export" in check_set and path.suffix == ".cpp":
        # Structural invariants of the compilable C++ project export (#331).
        # C++-only: the classifier heuristics are C++-specific, and other
        # languages have no code-export backend.
        findings.extend(
            _check_code_export(
                cells, file_str, allow_main=has_allow_main_marker(text, comment_token)
            )
        )

    # Review material extraction
    review_checks = check_set & ALL_REVIEW_CHECKS
    review_material: ReviewMaterial | None = None
    if review_checks:
        review_material = ReviewMaterial()
        if "code_quality" in review_checks:
            review_material.code_quality = _extract_code_quality(cells, file_str)
        if "voiceover" in review_checks:
            # Merge a separated voiceover companion (if any) into the cell
            # stream before checking coverage. Without this, the default 1.8
            # layout (voiceover cells in voiceover/*.py) produces a false
            # positive for every slide (issue #360).
            voiceover_cells = cells
            from clm.slides.voiceover_tools import merge_voiceover_text, resolve_companion

            companion = resolve_companion(path)
            if companion is not None:
                merged_text, _unmatched = merge_voiceover_text(
                    text, companion.read_text(encoding="utf-8"), comment_token
                )
                voiceover_cells = parse_cells(merged_text, comment_token)
            review_material.voiceover_gaps = _extract_voiceover_gaps(voiceover_cells, file_str)
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
    cells = parse_cells(text, comment_token_for_path(path))

    findings: list[Finding] = []
    findings.extend(_check_format(cells, file_str))
    findings.extend(_check_malformed_markers(text, file_str, comment_token_for_path(path)))
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
    *,
    marker_opt_in: bool | None = None,
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
            ``None`` runs the default bundle, which excludes the opt-in
            ``voiceover`` coverage check (issue #176) unless a deck opts in
            via the ``clm: voiceover-coverage`` header marker (#178).
        marker_opt_in: Passed to :func:`validate_file` (#178).
    """
    return validate_files(
        find_slide_files_recursive(path), checks=checks, marker_opt_in=marker_opt_in
    )


def validate_files(
    slide_files: list[Path],
    checks: list[str] | None = None,
    *,
    marker_opt_in: bool | None = None,
) -> ValidationResult:
    """Validate an explicit list of slide files (same logic as a directory walk).

    Factored out of :func:`validate_directory` so callers that have already
    computed a file set — e.g. ``clm validate --shipping-only``, which filters a
    directory down to the decks reachable from course specs — get identical
    per-file checks *and* the once-per-pair split-pair parity, without a second
    filesystem walk.

    Args:
        slide_files: The slide files to validate.
        checks: Which checks to run (passed to :func:`validate_file`).
            ``None`` runs the default bundle, which excludes the opt-in
            ``voiceover`` coverage check (issue #176) unless a deck opts in
            via the ``clm: voiceover-coverage`` header marker (#178).
        marker_opt_in: Passed to :func:`validate_file` (#178).
    """
    all_findings: list[Finding] = []
    # Created lazily on the first file that produced review material, so a
    # marker-driven voiceover pass (#178) is collected even when ``checks``
    # is an explicit deterministic list.
    combined_review: ReviewMaterial | None = None

    for sf in slide_files:
        # Cross-file parity is run once per pair below (not per file), so the
        # per-file pass skips it to avoid duplicate findings.
        result = validate_file(
            sf, checks=checks, cross_file_parity=False, marker_opt_in=marker_opt_in
        )
        all_findings.extend(result.findings)
        if result.review_material is not None:
            if combined_review is None:
                combined_review = ReviewMaterial()
            _merge_review_material(combined_review, result.review_material)

    # Phase 6: surface divergent shared cells between split pairs as
    # ``pairing`` errors. This runs even when only ``pairing`` checks are
    # requested via ``checks=`` because the parity is a pairing property.
    check_set = set(checks) if checks else set(DEFAULT_CHECKS)
    if "pairing" in check_set:
        for de_path, en_path in _slide_files_to_split_pairs(slide_files):
            all_findings.extend(_check_shared_cell_parity(de_path, en_path))
            all_findings.extend(_check_split_tag_parity(de_path, en_path))
            all_findings.extend(_check_split_slide_id_parity(de_path, en_path))
            all_findings.extend(_check_split_companion_for_slide_parity(de_path, en_path))

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
    *,
    marker_opt_in: bool | None = None,
) -> ValidationResult:
    """Validate all slides referenced by a course spec.

    Args:
        course_spec_path: Path to the course spec XML file.
        slides_dir: Path to the ``slides/`` directory.
        checks: Which checks to run (passed to :func:`validate_file`).
            ``None`` runs the default bundle, which excludes the opt-in
            ``voiceover`` coverage check (issue #176) unless a deck opts in
            via the ``clm: voiceover-coverage`` header marker (#178).
        marker_opt_in: Passed to :func:`validate_file` (#178).
    """
    from clm.core.course_spec import CourseSpec

    spec = CourseSpec.from_file(course_spec_path)
    topic_map = build_topic_map(slides_dir)

    all_findings: list[Finding] = []
    files_checked = 0
    # Lazy, like validate_files — see the comment there (#178).
    combined_review: ReviewMaterial | None = None

    check_set = set(checks) if checks else set(DEFAULT_CHECKS)

    for binding in spec.iter_topic_bindings():
        matches = matches_for_binding(topic_map, binding.topic_id, binding.effective_module)
        for match in matches:
            slide_files = find_slide_files(match.path)
            for sf in slide_files:
                # Cross-file parity runs once per pair below, not per file.
                result = validate_file(
                    sf, checks=checks, cross_file_parity=False, marker_opt_in=marker_opt_in
                )
                all_findings.extend(result.findings)
                files_checked += 1
                if result.review_material is not None:
                    if combined_review is None:
                        combined_review = ReviewMaterial()
                    _merge_review_material(combined_review, result.review_material)

            # Phase 6 shared-cell parity per topic: walk the topic's split
            # pairs and emit pairing-error findings for divergent shared
            # cells. Scoped per-topic so different topics don't share state.
            if "pairing" in check_set:
                for de_path, en_path in _slide_files_to_split_pairs(slide_files):
                    all_findings.extend(_check_shared_cell_parity(de_path, en_path))
                    all_findings.extend(_check_split_tag_parity(de_path, en_path))
                    all_findings.extend(_check_split_slide_id_parity(de_path, en_path))
                    all_findings.extend(_check_split_companion_for_slide_parity(de_path, en_path))

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
