"""Assign stable ``slide_id`` metadata to slide cells.

This is the engine behind ``clm slides assign-ids`` (Phase 2 of the
slide-format-redesign feature). See ``handover-slide-format-redesign-clm.md``
§2.3 for the full design.

Rules in one paragraph:

- IDs are EN-derived, lowercase-kebab, ASCII-only, capped at 30 chars.
  Numeric suffix on file-internal collision.
- Cells already carrying an id are left alone except under ``--force``.
- ``!``-prefixed ids are the *preserve marker*: never regenerated, even
  under ``--force``. The ``!`` is purely source-level — references
  elsewhere always use the bare form.
- Title slides emitted by ``# {{ header(...) }}`` always get
  ``slide_id="title"`` without author input.
- Headed cells get a slug from the heading. Headingless-but-extractable
  cells are refused by default; ``--accept-content-derived`` or
  ``--llm-suggest`` opt into auto-acceptance. Hard-refusal cells (no
  extractable content) always require manual authorship.
- Voiceover and notes cells inherit the slide_id of the most recent
  preceding slide/subslide cell (1:N relationship). They are *never*
  written from an extracted heading of their own.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clm.notebooks.slide_parser import CellMetadata, parse_cell_header
from clm.slides.headingless import (
    Category,
    Extraction,
    cell_text_for_llm,
    classify,
)
from clm.slides.slug import (
    MAX_SLUG_LENGTH,
    is_preserved,
    resolve_collision,
    slugify,
    strip_preserve_marker,
)

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import TitleSuggestionCache
    from clm.infrastructure.llm.ollama_client import TitleSuggester

logger = logging.getLogger(__name__)

# Header macro emitted in title slides:
#   # {{ header("DE Title", "EN Title") }}
_HEADER_MACRO_RE = re.compile(r'\{\{\s*header\s*\(\s*"[^"]*"\s*,\s*"([^"]*)"\s*\)\s*\}\}')

TITLE_SLIDE_ID = "title"


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass
class AssignedId:
    """An id that was written (or would be written in --report-only mode)."""

    file: str
    line: int
    slide_id: str
    source: str  # "heading" / "title-macro" / "voiceover-inherit" / "llm" / "content"


@dataclass
class Refusal:
    """A slide where the algorithm declined to assign an id.

    ``severity`` is ``"soft"`` for the headingless-but-extractable case
    (which ``--accept-content-derived`` would turn into an assignment) and
    ``"hard"`` for cells with nothing to extract.
    """

    file: str
    line: int
    severity: str  # "soft" / "hard"
    reason: str
    proposed_slug: str | None = None
    proposed_title: str | None = None


@dataclass
class AssignResult:
    """Outcome of an assign-ids run over one or more files."""

    files_modified: int = 0
    assignments: list[AssignedId] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    files_visited: int = 0

    @property
    def has_refusals(self) -> bool:
        return any(r.severity != "info" for r in self.refusals)

    @property
    def has_hard_refusals(self) -> bool:
        return any(r.severity == "hard" for r in self.refusals)


# ---------------------------------------------------------------------------
# Cell representation (preserve original text for lossless round-trip)
# ---------------------------------------------------------------------------


def _is_cell_boundary(line: str) -> bool:
    return line.startswith("# %%") or line.startswith("# j2 ") or line.startswith("# {{ ")


@dataclass
class _Cell:
    lines: list[str]
    line_number: int  # 1-based, line of the header
    metadata: CellMetadata

    @property
    def header(self) -> str:
        return self.lines[0]

    @header.setter
    def header(self, value: str) -> None:
        self.lines[0] = value

    @property
    def body(self) -> str:
        return "\n".join(self.lines[1:])


def _split_cells(text: str) -> tuple[str, list[_Cell]]:
    """Split file text into ``(preamble, cells)`` — same shape as the normalizer."""
    lines = text.split("\n")
    cells: list[_Cell] = []
    preamble: list[str] = []
    current: list[str] = []
    current_line = 0
    in_cell = False
    for i, line in enumerate(lines):
        if _is_cell_boundary(line):
            if in_cell:
                cells.append(
                    _Cell(
                        lines=current,
                        line_number=current_line,
                        metadata=parse_cell_header(current[0]),
                    )
                )
            current = [line]
            current_line = i + 1
            in_cell = True
        else:
            if in_cell:
                current.append(line)
            else:
                preamble.append(line)
    if in_cell:
        cells.append(
            _Cell(
                lines=current,
                line_number=current_line,
                metadata=parse_cell_header(current[0]),
            )
        )
    return ("\n".join(preamble), cells)


def _reconstruct(preamble: str, cells: list[_Cell]) -> str:
    parts: list[str] = []
    if preamble:
        parts.append(preamble)
    for cell in cells:
        parts.append("\n".join(cell.lines))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Cell mutation
# ---------------------------------------------------------------------------


_SLIDE_ID_RE = re.compile(r'\s*slide_id="[^"]*"')


def _strip_existing_slide_id(header: str) -> str:
    return _SLIDE_ID_RE.sub("", header)


def _write_slide_id(cell: _Cell, slide_id: str) -> None:
    """Rewrite the cell header to carry ``slide_id="…"``."""
    existing = cell.header
    stripped = _strip_existing_slide_id(existing).rstrip()
    new_header = f'{stripped} slide_id="{slide_id}"'
    cell.lines[0] = new_header
    cell.metadata = parse_cell_header(new_header)


# ---------------------------------------------------------------------------
# Title slide detection
# ---------------------------------------------------------------------------


def _is_title_macro_cell(cell: _Cell) -> bool:
    if not cell.metadata.is_j2:
        return False
    return bool(_HEADER_MACRO_RE.search(cell.header))


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class AssignOptions:
    """Knobs for one assign-ids pass.

    ``llm_suggester`` is the mockable :class:`TitleSuggester` from
    :mod:`clm.infrastructure.llm.ollama_client`. Passing ``None`` skips
    LLM use even when ``llm_suggest`` is true (the protocol-level escape
    hatch for "Ollama is not running" — Phase 2 acceptance criteria
    allows fail-soft here).
    """

    force: bool = False
    accept_content_derived: bool = False
    llm_suggest: bool = False
    report_only: bool = False
    llm_suggester: TitleSuggester | None = None
    llm_cache: TitleSuggestionCache | None = None


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def _classify_for_assignment(cell: _Cell) -> str:
    """Return the assignment role for a cell.

    ``"slide"`` — slide/subslide markdown cell that may receive an id
    ``"narrative"`` — voiceover/notes cell that *inherits* an id
    ``"title-macro"`` — the j2 header() macro cell
    ``"skip"`` — j2 directives, code cells, shared cells, etc.
    """
    meta = cell.metadata
    if _is_title_macro_cell(cell):
        return "title-macro"
    if meta.is_j2:
        return "skip"
    if meta.is_slide_start:
        return "slide"
    if meta.is_narrative and meta.lang is not None:
        return "narrative"
    return "skip"


def _proposed_slug_from_extraction(
    extraction: Extraction,
    used_ids,
) -> str:
    base = slugify(extraction.text, max_length=MAX_SLUG_LENGTH)
    if not base:
        return ""
    return resolve_collision(base, used_ids)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_slide_pairs(cells: list[_Cell]) -> dict[int, int]:
    """Map every slide-cell index to the cell that *drives* its slug.

    EN-derived policy (§2.3): when a DE slide cell sits next to an EN
    slide cell in the source order, both cells share the slug derived
    from the EN heading. The map gives every slide cell the index of the
    cell to slug from — itself if solo, the EN sibling if paired.
    """
    slide_indices = [i for i, c in enumerate(cells) if c.metadata.is_slide_start]
    pairs: dict[int, int] = {}
    i = 0
    while i < len(slide_indices):
        a = slide_indices[i]
        if i + 1 < len(slide_indices):
            b = slide_indices[i + 1]
            lang_a = cells[a].metadata.lang
            lang_b = cells[b].metadata.lang
            if lang_a and lang_b and lang_a != lang_b:
                # Paired. Pick the EN cell as the slug source.
                en_idx = a if lang_a == "en" else b
                pairs[a] = en_idx
                pairs[b] = en_idx
                i += 2
                continue
        pairs[a] = a  # solo
        i += 1
    return pairs


def assign_ids_for_text(
    text: str,
    file_path: Path,
    options: AssignOptions,
) -> tuple[str, AssignResult]:
    """Apply the assign-ids policy to one file's text.

    Returns ``(new_text, result)``. ``new_text == text`` when nothing was
    written (refusals only, or no changes needed). In ``--report-only``
    mode the new text always equals the input but the result still lists
    *proposed* assignments and refusals.
    """
    result = AssignResult(files_visited=1)
    preamble, cells = _split_cells(text)

    # First pass: collect every id already on the page (bare form). This
    # is what we use to detect collisions when generating new slugs.
    used_ids: set[str] = set()
    for cell in cells:
        existing = cell.metadata.slide_id
        if existing:
            used_ids.add(strip_preserve_marker(existing))

    pairs = _build_slide_pairs(cells)
    # Cache the slug we resolve for each "slug source" cell so paired
    # DE/EN cells always get the *exact same* id (collision suffix
    # included). Otherwise the second visit would observe its own
    # sibling's id already in used_ids and bump the counter.
    group_slug: dict[int, str | None] = {}

    # Track the most recent slide_id (bare form) by source order so that
    # narrative cells (voiceover/notes) inherit from the preceding
    # slide/subslide.
    current_slide_id: str | None = None
    file_str = str(file_path)

    for idx, cell in enumerate(cells):
        role = _classify_for_assignment(cell)

        if role == "title-macro":
            current_slide_id = (
                _handle_title_macro(cell, options, file_str, result) or current_slide_id
            )
            continue

        if role == "slide":
            slug_source = cells[pairs.get(idx, idx)]
            new_id = _handle_slide(
                cell,
                slug_source,
                options,
                used_ids,
                file_str,
                result,
                group_slug,
                slug_source_idx=pairs.get(idx, idx),
            )
            if new_id is not None:
                current_slide_id = new_id
            elif cell.metadata.slide_id:
                current_slide_id = strip_preserve_marker(cell.metadata.slide_id)
            # If we refused, leave current_slide_id untouched — the next
            # narrative cell will still inherit from the previous slide,
            # which is the least-surprising behavior in a partially
            # broken file.
            continue

        if role == "narrative":
            _handle_narrative(cell, current_slide_id, options, file_str, result)
            continue

        # role == "skip": unchanged.

    new_text = text
    if not options.report_only and result.assignments:
        candidate = _reconstruct(preamble, cells)
        if candidate != text:
            new_text = candidate
            result.files_modified = 1

    return new_text, result


def _handle_title_macro(
    cell: _Cell,
    options: AssignOptions,
    file_str: str,
    result: AssignResult,
) -> str | None:
    """The j2 header macro line itself does not carry slide_id metadata,
    but it *anchors* the title slide. We expose ``"title"`` via the
    return value so following narrative cells inherit it. No cell text
    is written here — the macro line stays untouched.
    """
    return TITLE_SLIDE_ID


def _handle_slide(
    cell: _Cell,
    slug_source: _Cell,
    options: AssignOptions,
    used_ids: set[str],
    file_str: str,
    result: AssignResult,
    group_slug: dict[int, str | None],
    slug_source_idx: int,
) -> str | None:
    """Assign or preserve a slide_id on one slide/subslide cell.

    ``slug_source`` is the cell whose heading/content drives the slug for
    this DE/EN group (per §2.3, the EN cell when a pair exists; otherwise
    ``cell`` itself). ``group_slug`` caches the resolved slug per group so
    the second cell of a pair receives the exact same id (collision
    suffix and all) instead of bumping past its own sibling.

    Returns the bare id that ended up on the cell (for narrative
    inheritance), or ``None`` when we refused.
    """
    existing = cell.metadata.slide_id

    # Preserve marker — never touched, not even under --force.
    if existing and is_preserved(existing):
        # Also lock the group's slug to the preserved bare form so the
        # sibling, if any, doesn't pick something else.
        group_slug.setdefault(slug_source_idx, strip_preserve_marker(existing))
        return strip_preserve_marker(existing)

    # No --force: existing id wins.
    if existing and not options.force:
        group_slug.setdefault(slug_source_idx, strip_preserve_marker(existing))
        return strip_preserve_marker(existing)

    # Group already resolved by the sibling cell — reuse that slug.
    if slug_source_idx in group_slug:
        cached = group_slug[slug_source_idx]
        if cached is not None:
            _maybe_write_cached(cell, cached, options, file_str, result)
            return cached
        # Sibling resolution failed (refusal). Mirror the refusal.
        # We still record a soft refusal for this cell so the report
        # reflects every affected cell.
        result.refusals.append(
            Refusal(
                file=file_str,
                line=cell.line_number,
                severity="soft",
                reason="sibling cell refused; both cells in the pair need a manual id",
            )
        )
        return None

    # Under --force we may replace this cell's id. Use a local view of
    # used_ids that excludes the cell's own existing id *and* its
    # sibling's existing id, so the regenerated slug can legitimately
    # reclaim its natural form. The real used_ids is only mutated when
    # we commit to a write.
    existing_bare = strip_preserve_marker(existing) if existing else None
    free: set[str] = set()
    if existing_bare:
        free.add(existing_bare)
    if slug_source is not cell and slug_source.metadata.slide_id:
        free.add(strip_preserve_marker(slug_source.metadata.slide_id))
    local_used = used_ids - free if free else used_ids

    # Slug is derived from the slug-source cell (EN sibling, or self).
    extraction = classify(slug_source.body)

    proposed_slug: str = ""
    proposed_title: str | None = None
    source: str = ""

    if extraction.category == Category.HEADED:
        proposed_title = extraction.text
        proposed_slug = _proposed_slug_from_extraction(extraction, local_used)
        source = "heading"

    elif extraction.category == Category.EXTRACTABLE:
        # LLM path first (if requested) — its suggestion replaces the
        # content-derived proposal because the title is usually more
        # readable than the raw first bullet. Use the slug source's body
        # (EN sibling) so the LLM sees English content.
        llm_title = _try_llm_suggestion(slug_source, options, file_str, result)
        if llm_title:
            proposed_title = llm_title
            base = slugify(llm_title, max_length=MAX_SLUG_LENGTH)
            if base:
                proposed_slug = resolve_collision(base, local_used)
                source = "llm"
        if not proposed_slug:
            proposed_title = extraction.text
            proposed_slug = _proposed_slug_from_extraction(extraction, local_used)
            source = f"content:{extraction.source}"

    else:
        # NON_EXTRACTABLE: hard refuse — only if we are even being asked
        # to assign. If --force is off and the cell already has an id,
        # we returned above; otherwise the cell genuinely has nothing.
        if existing:
            # --force is on but we have no proposal. Per §2.3 baseline
            # rule: leave the existing id alone.
            group_slug.setdefault(slug_source_idx, strip_preserve_marker(existing))
            return strip_preserve_marker(existing)
        result.refusals.append(
            Refusal(
                file=file_str,
                line=cell.line_number,
                severity="hard",
                reason="cell has no heading and no extractable content",
            )
        )
        group_slug[slug_source_idx] = None
        return None

    # We have a proposal. Decide whether to write it or refuse.
    if extraction.category == Category.HEADED:
        write = True
    elif extraction.category == Category.EXTRACTABLE and (
        options.accept_content_derived or source == "llm"
    ):
        write = True
    else:
        write = False

    if not write:
        result.refusals.append(
            Refusal(
                file=file_str,
                line=cell.line_number,
                severity="soft",
                reason="headingless slide; pass --accept-content-derived to accept",
                proposed_slug=proposed_slug,
                proposed_title=proposed_title,
            )
        )
        # Don't claim the slug — another extractable cell might want it.
        # Mark the group as refused so the sibling mirrors the decision.
        group_slug[slug_source_idx] = None
        return strip_preserve_marker(existing) if existing else None

    if not proposed_slug:
        # Slug fell out empty (e.g. text was punctuation-only). Treat as
        # a soft refusal so the author can review.
        result.refusals.append(
            Refusal(
                file=file_str,
                line=cell.line_number,
                severity="soft",
                reason="could not derive a usable slug from content",
                proposed_title=proposed_title,
            )
        )
        group_slug[slug_source_idx] = None
        return strip_preserve_marker(existing) if existing else None

    # Idempotency: skip the write if the id is already what we'd write.
    if existing and strip_preserve_marker(existing) == proposed_slug:
        group_slug[slug_source_idx] = proposed_slug
        return proposed_slug

    if not options.report_only:
        _write_slide_id(cell, proposed_slug)
    if existing_bare and existing_bare != proposed_slug:
        used_ids.discard(existing_bare)
    used_ids.add(proposed_slug)
    group_slug[slug_source_idx] = proposed_slug
    result.assignments.append(
        AssignedId(
            file=file_str,
            line=cell.line_number,
            slide_id=proposed_slug,
            source=source,
        )
    )
    return proposed_slug


def _maybe_write_cached(
    cell: _Cell,
    cached_slug: str,
    options: AssignOptions,
    file_str: str,
    result: AssignResult,
) -> None:
    """Apply a slug already resolved by a sibling cell.

    Called only when the *sibling* (slug-source) cell has already been
    processed and committed to ``cached_slug``. Honors the same
    preserve/idempotency/--force rules as the primary path, but doesn't
    need to recompute the slug.
    """
    existing = cell.metadata.slide_id

    if existing and is_preserved(existing):
        return  # preserve marker wins
    if existing and not options.force:
        return  # without --force, the existing id stays
    if existing and strip_preserve_marker(existing) == cached_slug:
        return  # already correct, idempotent no-op

    if not options.report_only:
        _write_slide_id(cell, cached_slug)
    result.assignments.append(
        AssignedId(
            file=file_str,
            line=cell.line_number,
            slide_id=cached_slug,
            source="paired",
        )
    )


def _try_llm_suggestion(
    cell: _Cell,
    options: AssignOptions,
    file_str: str,
    result: AssignResult,
) -> str | None:
    """Run the LLM suggester (cache-first) for a headingless cell.

    Returns ``None`` when LLM use is disabled, no suggester is wired in,
    or the call fails. Failures are logged at INFO and surfaced via the
    refusal mechanism upstream — we deliberately fail soft here.
    """
    if not options.llm_suggest:
        return None
    suggester = options.llm_suggester
    if suggester is None:
        return None

    content = cell_text_for_llm(cell.body)
    if not content.strip():
        return None
    content_hash = _content_hash(content)
    prompt_version = getattr(suggester, "prompt_version", "v1")
    lang = cell.metadata.lang or "en"

    cache = options.llm_cache
    if cache is not None:
        cached = cache.get(content_hash, prompt_version, lang)
        if cached:
            return cached

    try:
        title = suggester.suggest(content)
    except Exception as exc:  # OllamaError or anything stack-deep
        logger.warning("LLM title suggestion failed (cell line %d): %s", cell.line_number, exc)
        return None
    if not title:
        return None

    if cache is not None:
        cache.put(content_hash, prompt_version, title, lang)
    return title


def _handle_narrative(
    cell: _Cell,
    current_slide_id: str | None,
    options: AssignOptions,
    file_str: str,
    result: AssignResult,
) -> None:
    """Voiceover/notes cells inherit the most recent slide_id by adjacency."""
    existing = cell.metadata.slide_id

    if existing and is_preserved(existing):
        return  # preserve marker wins

    if current_slide_id is None:
        # No preceding slide yet (file starts with voiceover for the
        # title slide); we *can* sometimes still know the answer when
        # the title-macro is detected. Skip otherwise.
        return

    bare = current_slide_id

    if existing and not options.force:
        return

    if existing and strip_preserve_marker(existing) == bare:
        return  # idempotent

    if not options.report_only:
        _write_slide_id(cell, bare)
    result.assignments.append(
        AssignedId(
            file=file_str,
            line=cell.line_number,
            slide_id=bare,
            source="voiceover-inherit",
        )
    )


# ---------------------------------------------------------------------------
# File / directory drivers
# ---------------------------------------------------------------------------


def assign_ids_in_file(path: Path, options: AssignOptions) -> AssignResult:
    """Process one ``.py`` slide file end-to-end."""
    text = path.read_text(encoding="utf-8")
    new_text, result = assign_ids_for_text(text, path, options)
    if not options.report_only and new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return result


def assign_ids_in_directory(path: Path, options: AssignOptions) -> AssignResult:
    """Recurse over a directory and process every slide file we find."""
    from clm.core.topic_resolver import find_slide_files_recursive

    combined = AssignResult()
    for slide_file in find_slide_files_recursive(path):
        result = assign_ids_in_file(slide_file, options)
        combined.files_visited += result.files_visited
        combined.files_modified += result.files_modified
        combined.assignments.extend(result.assignments)
        combined.refusals.extend(result.refusals)
    return combined
