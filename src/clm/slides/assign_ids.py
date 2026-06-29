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
  ``--llm-suggest`` opt into auto-acceptance. A bare-expression code cell
  (no heading, no extractable construct — e.g. ``(1 + 1j) * (1 + 1j)``)
  is covered by the opt-in ``--accept-code-derived`` first-code-line
  fallback (#251). Only genuinely empty / pure-punctuation cells still
  require manual authorship.
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

from clm.infrastructure.utils.path_utils import split_lang_suffix
from clm.notebooks.slide_parser import comment_token_for_path, parse_cell_header
from clm.slides.code_cell_extract import extract_from_code
from clm.slides.headingless import (
    Category,
    Extraction,
    cell_text_for_llm,
    classify,
)
from clm.slides.pairing import (
    TITLE_SLIDE_ID,
    build_slide_groups,
    build_slide_pairs,
    is_title_macro_cell,
    split_twin,
)
from clm.slides.raw_cells import RawCell as _Cell
from clm.slides.raw_cells import reconstruct as _reconstruct
from clm.slides.raw_cells import split_cells as _split_cells
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

__all__ = [
    "TITLE_SLIDE_ID",
    "AssignOptions",
    "AssignResult",
    "AssignedId",
    "Refusal",
    "assign_ids_for_cells",
    "assign_ids_for_text",
    "assign_ids_in_directory",
    "assign_ids_in_file",
    "assign_ids_in_split_pair",
]


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass
class AssignedId:
    """An id that was written (or would be written in --report-only mode)."""

    file: str
    line: int
    slide_id: str
    # Which strategy produced the id, for the report. One of:
    # "heading" / "sibling-heading" / "title-macro" / "voiceover-inherit" /
    # "voiceover-reinherit" (a <deck-stem>-cell-N placeholder re-pointed, #233) /
    # "llm" / "paired" / "twin" / "content:<extractor>" (markdown bullet/bold/
    # img_alt/img_src/prose or the code AST extractors
    # code:class/def/assign/import/call/for/expr) /
    # "code:line" (the opt-in first-code-line fallback, #251).
    source: str


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
# Cell representation
#
# ``_Cell``, ``_split_cells``, ``_reconstruct`` are imported as aliases of
# the shared primitives in :mod:`clm.slides.raw_cells`. They preserve the
# original lines verbatim so the round-trip ``text ==
# _reconstruct(*_split_cells(text))`` holds for any cell-shaped input,
# which is what keeps the on-disk diff minimal here and what Phase 5's
# ``split``/``unify`` build on.
# ---------------------------------------------------------------------------


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
    cell.metadata = parse_cell_header(new_header, cell.metadata.comment_token)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class AssignOptions:
    """Knobs for one assign-ids pass.

    ``accept_content_derived`` bulk-accepts the markdown/AST content
    extractors (first bullet, bold, img alt, prose, ``code:class``/``def``/
    ``assign``/``import``/``call``). ``accept_code_derived`` (#251)
    separately opts into the last-resort first-code-line fallback for bare
    expression code cells that have no extractable construct; it is a
    distinct knob so the content-derived funnels never start minting opaque
    code-line slugs by accident.

    ``llm_suggester`` is the mockable :class:`TitleSuggester` from
    :mod:`clm.infrastructure.llm.ollama_client`. Passing ``None`` skips
    LLM use even when ``llm_suggest`` is true (the protocol-level escape
    hatch for "Ollama is not running" — Phase 2 acceptance criteria
    allows fail-soft here).
    """

    force: bool = False
    accept_content_derived: bool = False
    accept_code_derived: bool = False
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
    if is_title_macro_cell(cell):
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


def _extract_from_cell(cell: _Cell, comment_token: str, accept_code_derived: bool) -> Extraction:
    """Run the full extractor pipeline on a single cell.

    Markdown signals win first via :func:`classify`. When the cell is a
    code cell and markdown found nothing, the AST/first-code-line extractor
    in :mod:`clm.slides.code_cell_extract` gets a turn — ``comment_token``
    lets its first-code-line fallback recognize comment lines per prog_lang,
    and ``accept_code_derived`` gates that fallback. Falls back to
    ``NON_EXTRACTABLE`` if neither path produces a proposal.
    """
    extraction = classify(cell.body)
    if extraction.category == Category.NON_EXTRACTABLE and cell.metadata.cell_type == "code":
        code_extraction = extract_from_code(
            cell.body,
            comment_token,
            accept_code_derived=accept_code_derived,
            display_exprs=True,
        )
        if code_extraction is not None:
            return code_extraction
    return extraction


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Source labels produced by the opt-in first-code-line fallback (#251), direct
# and via the DE/EN sibling fallback. Kept bare (not ``content:``-prefixed) so
# they route through their own ``accept_code_derived`` write-gate clause rather
# than the ``accept_content_derived`` one the markdown/AST extractors use.
_CODE_LINE_SOURCES = ("code:line", "sibling-code:line")


def assign_ids_for_cells(
    cells: list[_Cell],
    file_path: Path,
    options: AssignOptions,
    *,
    twin_ids: list[str | None] | None = None,
) -> AssignResult:
    """Apply the assign-ids policy to an existing cell list.

    Mutates ``cells`` in place (unless ``options.report_only``) and
    returns an :class:`AssignResult` carrying the assignments and
    refusals. The result's ``files_visited`` / ``files_modified``
    counters are left at zero — the caller is responsible for the
    file-level accounting and for writing the reconstructed text back to
    disk. This is the seam :mod:`clm.slides.normalizer` uses to fold
    assign-ids into a larger multi-operation pass without re-parsing the
    file.

    ``twin_ids`` (the #162 defensive) is the positional list of bare
    slide_ids of the *sibling* split half's slide/subslide cells (``None``
    where the twin has no id). When supplied, an **id-less** slide on this
    half adopts ``twin_ids[n]`` for the n-th slide instead of minting a
    divergent slug from its own heading — keeping ``de_id == en_id`` across
    a split pair. ``assign_ids_in_file`` supplies it only when a twin exists
    on disk and the two halves have matching slide counts.
    """
    result = AssignResult()

    # First pass: collect every id already on the page (bare form). This
    # is what we use to detect collisions when generating new slugs.
    used_ids: set[str] = set()
    for cell in cells:
        existing = cell.metadata.slide_id
        if existing:
            used_ids.add(strip_preserve_marker(existing))

    pairs = build_slide_pairs(cells)
    # Map slug-source idx -> the OTHER member of the same DE/EN group
    # (or None for solo slides). Used to fall back to the sibling when
    # the primary slug source has no extractable content (Phase 3).
    alternate_of: dict[int, int | None] = {}
    for group in build_slide_groups(cells):
        if len(group) == 1:
            alternate_of[group[0]] = None
        else:
            a, b = group
            en_idx = a if cells[a].metadata.lang == "en" else b
            alternate_of[en_idx] = b if a == en_idx else a
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
    # Line-comment token of the deck's prog_lang ("#" python/rust, "//"
    # c/c++/c#/java/ts) — needed by the first-code-line fallback so it skips
    # comments per language. Derived once from the path extension.
    comment_token = comment_token_for_path(file_path)
    slide_seen = 0  # index among slide/subslide cells (for twin_ids correspondence)

    for idx, cell in enumerate(cells):
        role = _classify_for_assignment(cell)

        if role == "title-macro":
            current_slide_id = (
                _handle_title_macro(cell, options, file_str, result) or current_slide_id
            )
            continue

        if role == "slide":
            twin_id = (
                twin_ids[slide_seen]
                if twin_ids is not None and slide_seen < len(twin_ids)
                else None
            )
            slide_seen += 1
            # Defensive (#162): on a split half, an id-less slide adopts the
            # twin's id for the positionally-corresponding slide rather than
            # minting a divergent slug from this half's heading. Only id-less
            # cells are touched — existing ids keep their preserve/--force
            # semantics below.
            if twin_id is not None and not cell.metadata.slide_id:
                if not options.report_only:
                    _write_slide_id(cell, twin_id)
                used_ids.add(twin_id)
                result.assignments.append(
                    AssignedId(
                        file=file_str,
                        line=cell.line_number,
                        slide_id=twin_id,
                        source="twin",
                    )
                )
                current_slide_id = twin_id
                continue

            slug_source_idx = pairs.get(idx, idx)
            slug_source = cells[slug_source_idx]
            alt_idx = alternate_of.get(slug_source_idx)
            alternate_cell = cells[alt_idx] if alt_idx is not None else None
            new_id = _handle_slide(
                cell,
                slug_source,
                options,
                used_ids,
                file_str,
                result,
                group_slug,
                slug_source_idx=slug_source_idx,
                alternate_cell=alternate_cell,
                comment_token=comment_token,
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
            _handle_narrative(cell, current_slide_id, options, file_path, file_str, result)
            continue

        # role == "skip": unchanged.

    return result


def assign_ids_for_text(
    text: str,
    file_path: Path,
    options: AssignOptions,
    *,
    twin_ids: list[str | None] | None = None,
) -> tuple[str, AssignResult]:
    """Apply the assign-ids policy to one file's text.

    Returns ``(new_text, result)``. ``new_text == text`` when nothing was
    written (refusals only, or no changes needed). In ``--report-only``
    mode the new text always equals the input but the result still lists
    *proposed* assignments and refusals.

    ``twin_ids`` is forwarded to :func:`assign_ids_for_cells` (the #162
    defensive split-half id reuse — see its docstring).
    """
    preamble, cells = _split_cells(text, comment_token_for_path(file_path))
    result = assign_ids_for_cells(cells, file_path, options, twin_ids=twin_ids)
    result.files_visited = 1

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
    alternate_cell: _Cell | None = None,
    comment_token: str = "#",
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
    # ``_extract_from_cell`` combines the markdown classifier and the
    # code-cell AST/first-code-line fallback into a single proposal.
    extraction = _extract_from_cell(slug_source, comment_token, options.accept_code_derived)

    # Phase 3 fallback: if the EN slug source has nothing to slug from
    # but the DE sibling does, slug from the DE sibling. Transliteration
    # in :mod:`clm.slides.slug` keeps the result ASCII and uniqueness is
    # still enforced by the existing collision suffix machinery. We do
    # NOT consult the LLM in this branch — the LLM should propose
    # English titles, and the sibling content is German-side.
    sibling_fallback = False
    if extraction.category == Category.NON_EXTRACTABLE and alternate_cell is not None:
        alt_extraction = _extract_from_cell(
            alternate_cell, comment_token, options.accept_code_derived
        )
        if alt_extraction.category != Category.NON_EXTRACTABLE:
            extraction = Extraction(
                alt_extraction.category,
                alt_extraction.text,
                f"sibling-{alt_extraction.source}",
            )
            sibling_fallback = True

    proposed_slug: str = ""
    proposed_title: str | None = None
    source: str = ""

    if extraction.category == Category.HEADED:
        proposed_title = extraction.text
        proposed_slug = _proposed_slug_from_extraction(extraction, local_used)
        # ``extraction.source`` is "heading" for a direct heading and
        # "sibling-heading" when Phase 3 fell back to the DE sibling.
        source = extraction.source

    elif extraction.category == Category.EXTRACTABLE:
        # LLM path first (if requested) — its suggestion replaces the
        # content-derived proposal because the title is usually more
        # readable than the raw first bullet. Use the slug source's body
        # (EN sibling) so the LLM sees English content. Skip the LLM
        # when we fell back to the DE sibling: the prompt would target
        # German content and produce a title in the wrong language.
        if not sibling_fallback:
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
            # The first-code-line fallback keeps a bare label so it gets its
            # own accept gate; every other content extractor is "content:"-tagged.
            if extraction.source in _CODE_LINE_SOURCES:
                source = extraction.source
            else:
                source = f"content:{extraction.source}"

    else:
        # NON_EXTRACTABLE: Phase 4 last-resort LLM. Without this fallback
        # ``--llm-suggest`` would silently no-op on the entire hard-refusal
        # set — those cells never reached the LLM via the EXTRACTABLE
        # branch. ``_try_llm_suggestion`` self-guards on
        # ``options.llm_suggest`` and on suggester availability, so the
        # call is safe regardless of CLI flags.
        llm_title = _try_llm_suggestion(slug_source, options, file_str, result)
        if llm_title:
            base = slugify(llm_title, max_length=MAX_SLUG_LENGTH)
            if base:
                proposed_slug = resolve_collision(base, local_used)
                proposed_title = llm_title
                source = "llm"
                # Promote the category so the shared write-decision
                # below treats this exactly like an LLM-on-EXTRACTABLE
                # outcome (write under ``source == "llm"``).
                extraction = Extraction(Category.EXTRACTABLE, llm_title, "llm")

        if extraction.category == Category.NON_EXTRACTABLE:
            # No LLM suggestion either — hard refuse. If --force is off
            # and the cell already has an id, we returned above;
            # otherwise the cell genuinely has nothing.
            if existing:
                # --force is on but we have no proposal. Per §2.3
                # baseline rule: leave the existing id alone.
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

    # Idempotency short-circuit: if the cell already carries the exact id
    # we would propose, the cell is in the desired state — no write, no
    # refusal — regardless of --accept-content-derived. Without this the
    # EXTRACTABLE branch would refuse a cell whose author already accepted
    # the content-derived slug on a previous run.
    if existing and proposed_slug and strip_preserve_marker(existing) == proposed_slug:
        group_slug[slug_source_idx] = proposed_slug
        return proposed_slug

    # We have a proposal. Decide whether to write it or refuse.
    is_code_line = source in _CODE_LINE_SOURCES
    if extraction.category == Category.HEADED:
        write = True
    elif extraction.category != Category.EXTRACTABLE:
        write = False
    elif source == "llm":
        write = True
    elif is_code_line:
        write = options.accept_code_derived
    else:  # content-derived (markdown bullet/bold/img/prose or code AST extractors)
        write = options.accept_content_derived

    if not write:
        accept_flag = "--accept-code-derived" if is_code_line else "--accept-content-derived"
        result.refusals.append(
            Refusal(
                file=file_str,
                line=cell.line_number,
                severity="soft",
                reason=f"headingless slide; pass {accept_flag} to accept",
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


# Conversion-era placeholder ids stamped on voiceover/notes cells:
# ``<deck-stem-ish>-cell-<N>`` (e.g. ``simple_chatbot-cell-1`` in deck
# ``slides_030v_simple_chatbot``). Sequential counters, not slide
# references — never authoritative (#233).
_PLACEHOLDER_CELL_RE = re.compile(r"^(?P<prefix>.+)-cell-\d+$")


def _normalize_for_stem_match(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _is_placeholder_narrative_id(existing: str, file_path: Path) -> bool:
    """Whether ``existing`` is a ``<deck-stem>-cell-N`` conversion placeholder.

    Both conditions must hold: the id matches the ``…-cell-<N>`` shape AND
    its prefix is the deck's filename stem (or a ``-``-boundary suffix of
    it, after normalizing ``_``/``-``). A hand-written id that merely ends
    in a number, or a ``…-cell-N`` id unrelated to this deck's name, is
    left alone.
    """
    m = _PLACEHOLDER_CELL_RE.match(strip_preserve_marker(existing))
    if m is None:
        return False
    prefix = _normalize_for_stem_match(m.group("prefix"))
    if not prefix:
        return False
    stem = file_path.stem
    for lang_suffix in (".de", ".en"):
        if stem.endswith(lang_suffix):
            stem = stem[: -len(lang_suffix)]
    stem = _normalize_for_stem_match(stem)
    return stem == prefix or stem.endswith(f"-{prefix}")


def _handle_narrative(
    cell: _Cell,
    current_slide_id: str | None,
    options: AssignOptions,
    file_path: Path,
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

    if existing and strip_preserve_marker(existing) == bare:
        return  # idempotent

    # An existing id normally wins without --force — except a
    # ``<deck-stem>-cell-N`` conversion placeholder, which is never
    # authoritative and is re-pointed by the normal inherit pass (#233).
    placeholder = existing is not None and _is_placeholder_narrative_id(existing, file_path)
    if existing and not options.force and not placeholder:
        return

    if not options.report_only:
        _write_slide_id(cell, bare)
    result.assignments.append(
        AssignedId(
            file=file_str,
            line=cell.line_number,
            slide_id=bare,
            source="voiceover-reinherit" if placeholder else "voiceover-inherit",
        )
    )


# ---------------------------------------------------------------------------
# File / directory drivers
# ---------------------------------------------------------------------------


def _slide_start_ids(cells: list[_Cell]) -> list[str | None]:
    """Ordered bare slide_ids of slide/subslide cells (``None`` where absent)."""
    out: list[str | None] = []
    for cell in cells:
        if _classify_for_assignment(cell) == "slide":
            sid = cell.metadata.slide_id
            out.append(strip_preserve_marker(sid) if sid else None)
    return out


def _twin_ids_for(path: Path, text: str) -> list[str | None] | None:
    """Positional twin slide_ids to reuse (#162 defensive), or ``None``.

    Reuse applies only when the twin exists *and* both halves have the same
    number of slide/subslide cells. When the counts differ the halves are
    structurally misaligned and positional reuse could stamp the wrong id —
    so we mint normally and let the validator's #162 detective flag the
    divergence instead.
    """
    twin = split_twin(path)
    if twin is None:
        return None
    _, own_cells = _split_cells(text, comment_token_for_path(path))
    own_ids = _slide_start_ids(own_cells)
    _, twin_cells = _split_cells(twin.read_text(encoding="utf-8"), comment_token_for_path(twin))
    twin_ids = _slide_start_ids(twin_cells)
    if len(twin_ids) != len(own_ids):
        return None
    return twin_ids


def assign_ids_in_file(path: Path, options: AssignOptions) -> AssignResult:
    """Process one ``.py`` slide file end-to-end.

    On a split half (``*.de.py`` / ``*.en.py``) whose twin exists on disk and
    has the same number of slide/subslide cells, an id-less slide adopts the
    twin's id for the corresponding slide instead of minting a divergent slug
    (#162 defensive). Run order decides which half's slug wins when *both* are
    id-less, but the two halves always end up in slide_id parity.
    """
    text = path.read_text(encoding="utf-8")
    twin_ids = _twin_ids_for(path, text)
    new_text, result = assign_ids_for_text(text, path, options, twin_ids=twin_ids)
    if not options.report_only and new_text != text:
        path.write_text(new_text, encoding="utf-8", newline="\n")
    return result


def assign_ids_in_split_pair(
    de_path: Path, en_path: Path, options: AssignOptions
) -> AssignResult | None:
    """Generative #162: mint **EN-authority** slide_ids onto *both* halves of a
    split pair at once.

    Reconstructs the bilingual deck (``unify``), runs the normal paired
    assign-ids over it — which already derives each slide's slug from its EN
    cell and stamps the *same* id onto the DE/EN twin — then routes the ids back
    onto the two halves (``split``). Unlike the per-file defensive (which
    preserves parity but lets the first-assigned half's slug win), this is
    deterministic EN-authority regardless of order, matching how ids are minted
    in a bilingual file.

    Returns ``None`` when the pair is not unifiable (structurally misaligned or
    divergent shared cells) so the caller can fall back to the per-file
    defensive path; the validator's #162 detective then surfaces any residual
    divergence.
    """
    from clm.slides.split import SplitError, UnifyError, split_text, unify_texts

    comment_token = comment_token_for_path(en_path)
    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")
    try:
        unified = unify_texts(de_text, en_text, comment_token)
        # Proceed only when unify is *byte-faithful*: split(unify(de, en)) == (de, en).
        # ``unify`` is best-effort for solo / misaligned cells, so a structurally
        # divergent pair can unify without raising — but then the assign->split
        # round-trip could reorder or move cells. Verifying the id-less round-trip
        # guarantees that adding ids and splitting back cannot corrupt the files;
        # otherwise fall back to the per-file defensive (the #162 detective then
        # surfaces the residual divergence).
        rt_de, rt_en = split_text(unified, comment_token)
    except (SplitError, UnifyError):
        return None
    if (rt_de, rt_en) != (de_text, en_text):
        return None

    unified_new, result = assign_ids_for_text(unified, en_path, options)
    result.files_visited = 2
    result.files_modified = 0
    if options.report_only or unified_new == unified:
        return result

    try:
        de_new, en_new = split_text(unified_new, comment_token)
    except SplitError:  # pragma: no cover - unify succeeded, split should too
        return None

    if de_new != de_text:
        de_path.write_text(de_new, encoding="utf-8", newline="\n")
        result.files_modified += 1
    if en_new != en_text:
        en_path.write_text(en_new, encoding="utf-8", newline="\n")
        result.files_modified += 1
    return result


def _merge_result(combined: AssignResult, result: AssignResult) -> None:
    combined.files_visited += result.files_visited
    combined.files_modified += result.files_modified
    combined.assignments.extend(result.assignments)
    combined.refusals.extend(result.refusals)


def assign_ids_in_directory(path: Path, options: AssignOptions) -> AssignResult:
    """Recurse over a directory and process every slide file we find.

    Split ``.de.py`` / ``.en.py`` pairs are minted **EN-authority** across both
    halves at once (:func:`assign_ids_in_split_pair`); a pair that is not
    unifiable falls back to processing each half with the per-file twin-aware
    path. Bilingual and unpaired files go through :func:`assign_ids_in_file`.
    """
    from clm.core.topic_resolver import find_slide_files_recursive

    return assign_ids_in_files(list(find_slide_files_recursive(path)), options)


def assign_ids_in_files(files: list[Path], options: AssignOptions) -> AssignResult:
    """Process an explicit list of slide files (the directory-walk body).

    Factored out of :func:`assign_ids_in_directory` so callers that have already
    selected a subset of decks — e.g. ``clm slides assign-ids --only bilingual``
    / ``--exclude`` / ``--shipping-only`` — get the same split-pair-aware minting
    without a second filesystem walk. Split pairs are still detected *within* the
    given set: if only one half is present (e.g. its twin was excluded), that
    half takes the per-file twin-aware path and the absent twin is never written.
    """
    combined = AssignResult()
    fileset = set(files)
    handled: set[Path] = set()

    for slide_file in files:
        if slide_file in handled:
            continue
        twin = split_twin(slide_file)
        if twin is not None and twin in fileset:
            de_path, en_path = (
                (slide_file, twin) if split_lang_suffix(slide_file) == "de" else (twin, slide_file)
            )
            pair_result = assign_ids_in_split_pair(de_path, en_path, options)
            if pair_result is not None:
                _merge_result(combined, pair_result)
            else:
                # Not unifiable — fall back to the per-file defensive on each.
                _merge_result(combined, assign_ids_in_file(de_path, options))
                _merge_result(combined, assign_ids_in_file(en_path, options))
            handled.add(de_path)
            handled.add(en_path)
        else:
            _merge_result(combined, assign_ids_in_file(slide_file, options))
            handled.add(slide_file)

    return combined
