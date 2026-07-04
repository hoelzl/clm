"""MCP tool handler functions.

Thin async wrappers around CLM library functions.  Each handler
accepts validated parameters and returns a JSON string.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from clm.core.course import Course
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.topic_resolver import (
    ResolutionResult,
)
from clm.core.topic_resolver import (
    resolve_topic as _resolve_topic,
)
from clm.slides.authoring_rules import AuthoringRulesResult
from clm.slides.authoring_rules import get_authoring_rules as _get_authoring_rules
from clm.slides.doc_lenses import DocLensError
from clm.slides.doc_lenses import load_bundle as _load_bundle
from clm.slides.doc_report import diff_bundle as _diff_bundle
from clm.slides.doc_report import pair_payload as _pair_payload
from clm.slides.language_tools import SyncResult
from clm.slides.language_tools import get_language_view as _get_language_view
from clm.slides.language_tools import suggest_sync as _suggest_sync
from clm.slides.normalizer import NormalizationResult
from clm.slides.normalizer import normalize_course as _normalize_course
from clm.slides.normalizer import normalize_directory as _normalize_directory
from clm.slides.normalizer import normalize_file as _normalize_file
from clm.slides.pairing import derive_split_pair as _derive_split_pair
from clm.slides.pairing import derive_split_pair_from_stem as _derive_split_pair_from_stem
from clm.slides.search import SearchResult
from clm.slides.search import search_slides as _search_slides
from clm.slides.spec_validator import SpecValidationResult
from clm.slides.spec_validator import validate_spec as _validate_spec
from clm.slides.validator import ValidationResult
from clm.slides.validator import validate_course as _validate_course
from clm.slides.validator import validate_directory as _validate_directory
from clm.slides.validator import validate_file as _validate_file
from clm.slides.voiceover_tools import (
    ExtractionResult,
    InlineResult,
    PairedExtractionResult,
    VoiceoverError,
)
from clm.slides.voiceover_tools import extract_voiceover as _extract_voiceover
from clm.slides.voiceover_tools import extract_voiceover_pair as _extract_voiceover_pair
from clm.slides.voiceover_tools import inline_voiceover as _inline_voiceover

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory cache (keyed by directory mtime)
# ---------------------------------------------------------------------------

_topic_map_cache: dict[str, tuple[float, dict]] = {}
_course_cache: dict[str, tuple[float, Course]] = {}


def _slides_dir_mtime(slides_dir: Path) -> float:
    """Return the mtime of the slides directory for cache invalidation."""
    try:
        return slides_dir.stat().st_mtime
    except OSError:
        return 0.0


def _get_cached_course(spec_path: Path) -> Course:
    """Get a Course object, using a cache keyed by spec file mtime.

    Always parses with ``keep_disabled=False``; disabled sections cannot go
    through ``Course.from_spec`` because they may reference non-existent
    topic directories. Callers that need the full roadmap must call
    ``CourseSpec.from_file(..., keep_disabled=True)`` directly and work
    with the ``SectionSpec`` objects.
    """
    key = str(spec_path)
    try:
        current_mtime = spec_path.stat().st_mtime
    except OSError:
        current_mtime = 0.0

    cached = _course_cache.get(key)
    if cached and cached[0] == current_mtime:
        return cached[1]

    spec = CourseSpec.from_file(spec_path)
    data_dir, _ = resolve_course_paths(spec_path)
    course = Course.from_spec(spec, data_dir, output_root=None)
    _course_cache[key] = (current_mtime, course)
    return course


# ---------------------------------------------------------------------------
# resolve_topic
# ---------------------------------------------------------------------------


def _resolution_to_dict(result: ResolutionResult) -> dict:
    """Convert a ResolutionResult to a JSON-serializable dict."""
    d: dict = {"topic_id": result.topic_id}

    if result.glob:
        d["glob"] = True
        d["matches"] = [
            {
                "topic_id": m.topic_id,
                "path": str(m.path),
                "path_type": m.path_type,
                "module": m.module,
            }
            for m in result.matches
        ]
    else:
        d["path"] = str(result.path) if result.path else None
        d["path_type"] = result.path_type
        d["slide_files"] = [str(f) for f in result.slide_files]
        d["ambiguous"] = result.ambiguous
        if result.alternatives:
            d["alternatives"] = [
                {
                    "topic_id": a.topic_id,
                    "path": str(a.path),
                    "path_type": a.path_type,
                    "module": a.module,
                }
                for a in result.alternatives
            ]

    return d


async def handle_resolve_topic(
    topic_id: str,
    data_dir: Path,
    *,
    course_spec: str | None = None,
    module: str | None = None,
) -> str:
    """Resolve a topic ID or glob pattern to filesystem path(s).

    Args:
        topic_id: Topic identifier or glob pattern.
        data_dir: Root data directory (contains ``slides/``).
        course_spec: Optional path to a course spec file to scope resolution.
        module: Optional module directory name (e.g.,
            ``"module_545_ml_azav_cohort_2026_04"``). When set, resolution
            is restricted to topics in that module — useful when a topic
            ID exists in multiple modules (e.g., a frozen-cohort archive
            shares topic IDs with the live module).

    Returns:
        JSON string with resolution result.
    """
    slides_dir = data_dir / "slides"

    course_topic_bindings: set[tuple[str, str | None]] | None = None
    if course_spec:
        try:
            spec = CourseSpec.from_file(Path(course_spec))
            course_topic_bindings = spec.topic_bindings()
        except CourseSpecError:
            logger.warning("Failed to parse course spec: %s", course_spec)

    result = _resolve_topic(
        topic_id,
        slides_dir,
        course_topic_bindings=course_topic_bindings,
        module=module,
    )
    return json.dumps(_resolution_to_dict(result), indent=2)


# ---------------------------------------------------------------------------
# search_slides
# ---------------------------------------------------------------------------


def _search_result_to_dict(r: SearchResult) -> dict:
    """Convert a SearchResult to a JSON-serializable dict."""
    return {
        "score": r.score,
        "topic_id": r.topic_id,
        "directory": r.directory,
        "slides": [
            {"file": s.file, "title_de": s.title_de, "title_en": s.title_en} for s in r.slides
        ],
        "courses": r.courses,
    }


async def handle_search_slides(
    query: str,
    data_dir: Path,
    *,
    course_spec: str | None = None,
    language: str | None = None,
    max_results: int = 10,
) -> str:
    """Fuzzy search across topic names and slide titles.

    Args:
        query: Search query.
        data_dir: Root data directory (contains ``slides/``).
        course_spec: Optional course spec path to limit scope.
        language: Limit search to this language (``"de"`` or ``"en"``).
        max_results: Maximum results to return.

    Returns:
        JSON string with search results.
    """
    slides_dir = data_dir / "slides"
    spec_path = Path(course_spec) if course_spec else None

    results = _search_slides(
        query,
        slides_dir,
        course_spec_path=spec_path,
        language=language,
        max_results=max_results,
    )

    return json.dumps(
        {"results": [_search_result_to_dict(r) for r in results]},
        indent=2,
    )


# ---------------------------------------------------------------------------
# course_outline
# ---------------------------------------------------------------------------


async def handle_course_outline(
    spec_file: str,
    data_dir: Path,
    *,
    language: str = "en",
    include_disabled: bool = False,
) -> str:
    """Generate a structured JSON outline for a course.

    The outline doubles as the section -> source-deck mapping: each section's
    ``topics`` array carries the topic ``directory`` and a ``slides`` list of
    ``{file, title}`` (the source ``.py`` deck files).

    Args:
        spec_file: Path to the course spec file (absolute or relative to data_dir).
        data_dir: Root data directory.
        language: Language code (``"en"`` or ``"de"``).
        include_disabled: If True, include sections marked
            ``enabled="false"`` in the output with ``"disabled": true``
            markers. Default: disabled sections are omitted.

    Returns:
        JSON string with the course outline.
    """
    from clm.cli.commands.export.outline import generate_outline_json

    spec_path = Path(spec_file)
    if not spec_path.is_absolute():
        spec_path = data_dir / spec_path

    course = _get_cached_course(spec_path)

    disabled_sections = []
    if include_disabled:
        full_spec = CourseSpec.from_file(spec_path, keep_disabled=True)
        disabled_sections = [s for s in full_spec.sections if not s.enabled]

    # The MCP outline is a structured data view for agents, so it keeps showing
    # optional modules (its pre-export-group behavior); only ``include_disabled``
    # gates content here.
    outline = generate_outline_json(
        course, language, disabled_sections=disabled_sections, include_optional=True
    )
    return json.dumps(outline, indent=2)


# ---------------------------------------------------------------------------
# course_context
# ---------------------------------------------------------------------------


async def handle_course_context(
    spec_file: str,
    data_dir: Path,
    *,
    language: str = "en",
    level: str = "titles",
    through: str | None = None,
    from_section: str | None = None,
    before: str | None = None,
    upto: str | None = None,
    include_disabled: bool = False,
    model: str | None = None,
    no_cache: bool = False,
) -> str:
    """Generate an agent-audience course context, scoped to a cut point.

    Args:
        spec_file: Path to the course spec file (absolute or relative to data_dir).
        data_dir: Root data directory.
        language: Language code (``"en"`` or ``"de"``).
        level: ``"titles"`` (structure only, no LLM), ``"summary"`` (per-topic
            LLM summaries, cached), or ``"full"`` (raw extracted markdown+code).
        through / from_section: Section selectors (1-based number or section id).
        before / upto: Topic-anchor selectors (mutually exclusive with the
            section selectors).
        include_disabled: Include sections marked ``enabled="false"``, tagged.
        model: Override the LLM model (``summary`` level only).
        no_cache: Skip the summary cache (``summary`` level only).

    Returns:
        JSON string with the scoped course context, or an ``{"error": …}``
        object when a selector cannot be resolved.
    """
    from clm.cli.commands.export.context import (
        ScopeError,
        load_scoped_units,
        render_json,
    )

    if (through is not None or from_section is not None) and (
        before is not None or upto is not None
    ):
        return json.dumps(
            {
                "error": "section selectors (through/from_section) and topic "
                "selectors (before/upto) are mutually exclusive"
            },
            indent=2,
        )
    if before is not None and upto is not None:
        return json.dumps({"error": "before and upto are mutually exclusive"}, indent=2)
    if level not in ("titles", "summary", "full"):
        return json.dumps(
            {"error": f"unknown level {level!r}; use titles, summary, or full"}, indent=2
        )

    spec_path = Path(spec_file)
    if not spec_path.is_absolute():
        spec_path = data_dir / spec_path

    course = _get_cached_course(spec_path)

    try:
        units = load_scoped_units(
            course,
            spec_path,
            language,
            include_optional=True,
            include_disabled=include_disabled,
            through=through,
            from_section=from_section,
            before=before,
            upto=upto,
        )
    except (ScopeError, CourseSpecError) as exc:
        return json.dumps({"error": str(exc)}, indent=2)

    scope = {"through": through, "from": from_section, "before": before, "upto": upto}
    summaries = None
    if level == "summary":
        summaries = await _course_context_summaries(
            units, course, language, model=model, no_cache=no_cache, data_dir=data_dir
        )

    return json.dumps(
        render_json(course, units, language, level=level, scope=scope, summaries=summaries),
        indent=2,
    )


async def _course_context_summaries(
    units,
    course,
    language: str,
    *,
    model: str | None,
    no_cache: bool,
    data_dir: Path,
) -> dict[str, str]:
    """Run (cache-or-LLM) agent summaries for the MCP context tool."""
    from clm.cli.commands.export.context import _summaries_by_hash
    from clm.infrastructure.config import get_config
    from clm.infrastructure.llm.cache import SummaryCache

    llm_config = get_config().llm
    cache = None if no_cache else SummaryCache(data_dir / "clm_summaries.db")
    try:
        return await _summaries_by_hash(
            units,
            course,
            language,
            style="bullets",
            model=model or llm_config.model,
            temperature=llm_config.temperature,
            api_base=llm_config.api_base or None,
            api_key=llm_config.api_key or None,
            max_concurrent=llm_config.max_concurrent,
            cache=cache,
            no_cache=no_cache,
            progress=None,
        )
    finally:
        if cache:
            cache.close()


# ---------------------------------------------------------------------------
# validate_spec
# ---------------------------------------------------------------------------


def _spec_result_to_dict(result: SpecValidationResult) -> dict:
    """Convert a SpecValidationResult to a JSON-serializable dict."""
    return {
        "course_spec": result.course_spec,
        "topics_total": result.topics_total,
        "findings": [
            {
                k: v
                for k, v in {
                    "severity": f.severity,
                    "type": f.type,
                    "topic_id": f.topic_id,
                    "section": f.section,
                    "message": f.message,
                    "suggestion": f.suggestion or None,
                    "matches": f.matches or None,
                    "sections": f.sections or None,
                }.items()
                if v is not None
            }
            for f in result.findings
        ],
    }


async def handle_validate_spec(
    course_spec: str,
    data_dir: Path,
    *,
    include_disabled: bool = False,
) -> str:
    """Validate a course specification XML file.

    Args:
        course_spec: Path to the course spec file (absolute or relative
            to data_dir).
        data_dir: Root data directory (contains ``slides/``).
        include_disabled: If True, also validate sections marked
            ``enabled="false"``. Each finding from a disabled section has
            ``(disabled)`` appended to its message. Default: disabled
            sections are dropped at parse time and therefore invisible
            to validation.

    Returns:
        JSON string with validation results.
    """
    spec_path = Path(course_spec)
    if not spec_path.is_absolute():
        spec_path = data_dir / spec_path

    slides_dir = data_dir / "slides"
    result = _validate_spec(spec_path, slides_dir, include_disabled=include_disabled)
    return json.dumps(_spec_result_to_dict(result), indent=2)


# ---------------------------------------------------------------------------
# validate_slides
# ---------------------------------------------------------------------------


def _validation_result_to_dict(result: ValidationResult) -> dict:
    """Convert a ValidationResult to a JSON-serializable dict."""
    d: dict = {
        "files_checked": result.files_checked,
        "summary": result.summary,
        "findings": [
            {
                k: v
                for k, v in {
                    "severity": f.severity,
                    "category": f.category,
                    "file": f.file,
                    "line": f.line,
                    "message": f.message,
                    "suggestion": f.suggestion or None,
                }.items()
                if v is not None
            }
            for f in result.findings
        ],
    }
    if result.review_material is not None:
        rm = result.review_material
        review: dict = {}
        if rm.code_quality is not None:
            review["code_quality"] = rm.code_quality
        if rm.voiceover_gaps is not None:
            review["voiceover_gaps"] = rm.voiceover_gaps
        if rm.completeness is not None:
            review["completeness"] = rm.completeness
        if review:
            d["review_material"] = review
    return d


async def handle_validate_slides(
    path: str,
    data_dir: Path,
    *,
    checks: list[str] | None = None,
) -> str:
    """Validate slide files for format, tag, and pairing correctness.

    Args:
        path: Path to a slide file, topic directory, or course spec XML
            (absolute or relative to data_dir).
        data_dir: Root data directory (contains ``slides/``).
        checks: Which checks to run.  Default (``None``): all checks except
            the opt-in ``voiceover`` coverage check (issue #176) — pass
            ``checks=["voiceover"]`` to run it.

    Returns:
        JSON string with validation results.
    """
    target = Path(path)
    if not target.is_absolute():
        target = data_dir / target

    if target.is_file() and target.suffix == ".xml":
        slides_dir = data_dir / "slides"
        result = _validate_course(target, slides_dir, checks=checks)
    elif target.is_dir():
        result = _validate_directory(target, checks=checks)
    else:
        result = _validate_file(target, checks=checks)

    return json.dumps(_validation_result_to_dict(result), indent=2)


# ---------------------------------------------------------------------------
# normalize_slides
# ---------------------------------------------------------------------------


def _normalization_result_to_dict(result: NormalizationResult) -> dict:
    """Convert a NormalizationResult to a JSON-serializable dict."""
    d: dict = {
        "files_modified": result.files_modified,
        "status": result.status,
        "summary": result.summary,
        "changes": [
            {
                "file": c.file,
                "operation": c.operation,
                "line": c.line,
                "description": c.description,
            }
            for c in result.changes
        ],
    }
    if result.review_items:
        d["review_items"] = [
            {
                k: v
                for k, v in {
                    "file": r.file,
                    "issue": r.issue,
                    "suggestion": r.suggestion or None,
                    **r.details,
                }.items()
                if v is not None
            }
            for r in result.review_items
        ]
    return d


async def handle_normalize_slides(
    path: str,
    data_dir: Path,
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
    canonicalize_start_completed: bool = False,
) -> str:
    """Normalize slide files by applying mechanical fixes.

    Args:
        path: Path to a slide file, topic directory, or course spec XML
            (absolute or relative to data_dir).
        data_dir: Root data directory (contains ``slides/``).
        operations: Which operations to apply.  Default: all.
        dry_run: If ``True``, preview changes without modifying files.
        canonicalize_start_completed: Force start/completed cohesion pairs
            into the canonical DE/EN interleave so a subsequent split/unify
            round-trips byte-for-byte. Only affects the interleaving op.

    Returns:
        JSON string with normalization results.
    """
    target = Path(path)
    if not target.is_absolute():
        target = data_dir / target

    if target.is_file() and target.suffix == ".xml":
        slides_dir = data_dir / "slides"
        result = _normalize_course(
            target,
            slides_dir,
            operations=operations,
            dry_run=dry_run,
            canonicalize_start_completed=canonicalize_start_completed,
        )
    elif target.is_dir():
        result = _normalize_directory(
            target,
            operations=operations,
            dry_run=dry_run,
            canonicalize_start_completed=canonicalize_start_completed,
        )
    else:
        result = _normalize_file(
            target,
            operations=operations,
            dry_run=dry_run,
            canonicalize_start_completed=canonicalize_start_completed,
        )

    return json.dumps(_normalization_result_to_dict(result), indent=2)


# ---------------------------------------------------------------------------
# get_language_view
# ---------------------------------------------------------------------------


async def handle_get_language_view(
    file: str,
    data_dir: Path,
    *,
    language: str,
    include_voiceover: bool = False,
    include_notes: bool = False,
) -> str:
    """Extract a single-language view of a bilingual slide file.

    Args:
        file: Path to the slide file (absolute or relative to data_dir).
        data_dir: Root data directory.
        language: Which language to extract (``"de"`` or ``"en"``).
        include_voiceover: Include voiceover cells.
        include_notes: Include speaker-notes cells.

    Returns:
        Filtered file content with ``[original line N]`` annotations.
    """
    target = Path(file)
    if not target.is_absolute():
        target = data_dir / target

    return _get_language_view(
        target,
        language,
        include_voiceover=include_voiceover,
        include_notes=include_notes,
    )


# ---------------------------------------------------------------------------
# suggest_sync
# ---------------------------------------------------------------------------


def _sync_result_to_dict(result: SyncResult) -> dict:
    """Convert a SyncResult to a JSON-serializable dict."""
    return {
        "file": result.file,
        "source_language": result.source_language,
        "target_language": result.target_language,
        "pairing_method": result.pairing_method,
        "suggestions": [
            {
                k: v
                for k, v in {
                    "type": s.type,
                    "slide_id": s.slide_id,
                    "source_line": s.source_line,
                    "source_content": s.source_content,
                    "target_line": s.target_line,
                    "target_content_current": s.target_content_current,
                    "suggestion": s.suggestion,
                }.items()
                if v is not None
            }
            for s in result.suggestions
        ],
        "unmodified_pairs": result.unmodified_pairs,
        "sync_needed": result.sync_needed,
    }


async def handle_suggest_sync(
    file: str,
    data_dir: Path,
    *,
    source_language: str | None = None,
) -> str:
    """Compare a slide file against git HEAD and suggest sync updates.

    Args:
        file: Path to the slide file (absolute or relative to data_dir).
        data_dir: Root data directory.
        source_language: The language that was edited (``"de"`` or ``"en"``).
            If ``None``, auto-detects which language has more changes.

    Returns:
        JSON string with sync suggestions.
    """
    target = Path(file)
    if not target.is_absolute():
        target = data_dir / target

    result = _suggest_sync(target, source_language=source_language)
    return json.dumps(_sync_result_to_dict(result), indent=2)


# ---------------------------------------------------------------------------
# sync_report (split-pair reconciliation report — the agent contract)
# ---------------------------------------------------------------------------


def _resolve_split_pair(target: Path) -> tuple[Path, Path] | None:
    """A deck half OR a bilingual stem -> ordered ``(de_path, en_path)``, or ``None``.

    Mirrors the ``clm slides sync`` single-path contract: try the split-half form
    first (the twin is derived from disk), then the deck-stem form (both halves
    derived). ``None`` when ``target`` is neither.
    """
    pair = _derive_split_pair(target)
    if pair is not None:
        return pair
    return _derive_split_pair_from_stem(target)


async def handle_sync_report(file: str, data_dir: Path) -> str:
    """Produce the sync report for a split DE/EN deck pair (v3 engine, #520).

    Runs the *same* read verb as ``clm slides sync report --json`` and returns the
    schema-3 member table: per-member items (mechanical vs framed actions, each
    framed item carrying its decision-answer vocabulary) diffed against the
    committed per-topic ledger — the only trust store. This is the blessed agent
    contract for non-shell agents (the split-pair analogue of the legacy
    single-file ``slides_suggest_sync``). Read-only: no file is written, the
    ledger is not touched, and no model is called.

    Args:
        file: A deck half (``<deck>.de.<ext>`` / ``<deck>.en.<ext>``) or the bilingual
            deck stem (``<deck>.<ext>``); the twin / both halves are derived from disk.
        data_dir: Root data directory (a relative ``file`` resolves against it).

    Returns:
        JSON: the schema-3 pair payload (``de_path`` / ``en_path``, the ``items``
        rows, and ``is_clean`` / ``needs_model`` / ``needs_agent``), or an
        ``{"error": …}`` object.
    """
    target = Path(file)
    if not target.is_absolute():
        target = data_dir / target

    pair = _resolve_split_pair(target)
    if pair is None:
        return json.dumps(
            {
                "error": (
                    f"{target.name} is not a split-format deck half "
                    "(<deck>.de.<ext> / <deck>.en.<ext>) nor a deck stem with both "
                    "halves on disk; this tool reconciles a split DE/EN pair. For a "
                    "single bilingual file, use slides_suggest_sync."
                )
            },
            indent=2,
        )
    de_path, en_path = pair

    try:
        bundle = _load_bundle(de_path, en_path)
    except DocLensError as exc:
        return json.dumps({"error": str(exc)}, indent=2)
    payload = _pair_payload(bundle, _diff_bundle(bundle))
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# extract_voiceover
# ---------------------------------------------------------------------------


def _extraction_result_to_dict(result: ExtractionResult) -> dict:
    """Convert an ExtractionResult to a JSON-serializable dict."""
    return {
        "slide_file": result.slide_file,
        "companion_file": result.companion_file,
        "cells_extracted": result.cells_extracted,
        "ids_generated": result.ids_generated,
        "dry_run": result.dry_run,
        "summary": result.summary,
    }


def _paired_extraction_result_to_dict(result: PairedExtractionResult) -> dict:
    """Paired-extract JSON shape — byte-aligned with the CLI's
    ``_paired_extraction_to_dict`` (the two serializers are a contract). The
    ``"paired": true`` discriminator lets consumers branch; a single-file
    extract keeps emitting the flat dict (no ``paired`` key)."""
    return {
        "paired": True,
        "dry_run": result.dry_run,
        "ids_minted": result.ids_minted,
        "companions": [_extraction_result_to_dict(r) for r in result.results],
        "summary": result.summary,
    }


async def handle_extract_voiceover(
    file: str,
    data_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    both: bool = False,
    single: bool = False,
) -> str:
    """Extract voiceover cells from a slide file to a companion file.

    On a split half whose twin exists on disk, both companions are extracted in
    one EN-authority paired op by default (mirrors the CLI). ``single`` opts out;
    ``both`` forces the paired form (errors if there is no twin).

    Args:
        file: Path to the slide file (absolute or relative to data_dir).
        data_dir: Root data directory.
        force: Overwrite an existing companion file (rebuilds it from the
            slide's voiceover cells, discarding companion-only content).
        dry_run: If ``True``, preview without writing files.
        both: Force the paired extract (both companions of a split deck).
        single: Extract only ``file``'s own companion, even on a split half.

    Returns:
        JSON string with extraction results (a paired shape carries
        ``"paired": true``), or a ``{"error": ...}`` object when an existing
        companion would be clobbered without ``force`` or the pair is invalid.
    """
    target = Path(file)
    if not target.is_absolute():
        target = data_dir / target

    if both and single:
        return json.dumps({"error": "both and single are mutually exclusive"}, indent=2)

    pair = None if single else _derive_split_pair(target)
    if both and pair is None:
        return json.dumps(
            {"error": f"both requested but '{target.name}' has no split twin on disk"},
            indent=2,
        )

    try:
        if pair is not None:
            paired = _extract_voiceover_pair(pair[0], pair[1], force=force, dry_run=dry_run)
            return json.dumps(_paired_extraction_result_to_dict(paired), indent=2)
        result = _extract_voiceover(target, force=force, dry_run=dry_run)
    except VoiceoverError as e:
        return json.dumps({"error": str(e)}, indent=2)
    return json.dumps(_extraction_result_to_dict(result), indent=2)


# ---------------------------------------------------------------------------
# inline_voiceover
# ---------------------------------------------------------------------------


def _inline_result_to_dict(result: InlineResult) -> dict:
    """Convert an InlineResult to a JSON-serializable dict."""
    return {
        "slide_file": result.slide_file,
        "companion_file": result.companion_file,
        "cells_inlined": result.cells_inlined,
        "unmatched_cells": result.unmatched_cells,
        "relocated_cells": result.relocated_cells,
        "companion_deleted": result.companion_deleted,
        "companion_retained": result.companion_retained,
        "dry_run": result.dry_run,
        "summary": result.summary,
    }


async def handle_inline_voiceover(
    file: str,
    data_dir: Path,
    *,
    dry_run: bool = False,
) -> str:
    """Inline voiceover cells from a companion file back into a slide file.

    Args:
        file: Path to the slide file (absolute or relative to data_dir).
        data_dir: Root data directory.
        dry_run: If ``True``, preview without modifying files.

    Returns:
        JSON string with inline results.
    """
    target = Path(file)
    if not target.is_absolute():
        target = data_dir / target

    result = _inline_voiceover(target, dry_run=dry_run)
    return json.dumps(_inline_result_to_dict(result), indent=2)


# ---------------------------------------------------------------------------
# harvest_transcribe, harvest_identify_rev, harvest_compare,
# harvest_backfill_dry, harvest_cache_list, harvest_trace_show,
# harvest_report, harvest_task
# ---------------------------------------------------------------------------


def _resolve_under(data_dir: Path, p: str) -> Path:
    """Resolve ``p`` as absolute, or relative to ``data_dir`` otherwise."""
    target = Path(p)
    return target if target.is_absolute() else data_dir / target


async def handle_harvest_transcribe(
    video: str,
    data_dir: Path,
    *,
    lang: str | None = None,
    backend: str = "faster-whisper",
    whisper_model: str = "large-v3",
    device: str = "auto",
    no_cache: bool = False,
    refresh_cache: bool = False,
    cache_root: str | None = None,
) -> str:
    """Transcribe a video (using the artifact cache) and return a summary.

    Args:
        video: Path to the video file (absolute or relative to
            ``data_dir``).
        data_dir: Root data directory.
        lang: Whisper language hint (e.g. ``"de"``, ``"en"``); ``None``
            lets the backend auto-detect.
        backend: Transcription backend.
        whisper_model: Whisper model size.
        device: ``"auto" | "cpu" | "cuda"``.
        no_cache: Disable cache reads (writes still happen).
        refresh_cache: Force recompute + overwrite cache entry.
        cache_root: Override ``.clm/voiceover-cache`` location.

    Returns:
        JSON string with ``{cache_hit, language, duration_sec, segments,
        first_segment, last_segment}``.
    """
    from clm.voiceover.cache import CachePolicy, cached_transcribe
    from clm.voiceover.transcribe import transcribe_video

    video_path = _resolve_under(data_dir, video)
    policy = CachePolicy(
        enabled=not no_cache,
        refresh=refresh_cache,
        cache_root=Path(cache_root) if cache_root else None,
    )

    transcript, hit = cached_transcribe(
        video_path,
        policy=policy,
        transcribe_fn=lambda: transcribe_video(
            video_path,
            language=lang,
            backend_name=backend,
            model_size=whisper_model,
            device=device,
        ),
        backend_name=backend,
        model_size=whisper_model,
        language=lang,
        device=device,
    )

    segments = transcript.segments
    payload: dict = {
        "video": str(video_path),
        "cache_hit": hit,
        "language": transcript.language,
        "duration_sec": transcript.duration,
        "segment_count": len(segments),
        "first_segment": (
            {"start": segments[0].start, "end": segments[0].end, "text": segments[0].text}
            if segments
            else None
        ),
        "last_segment": (
            {"start": segments[-1].start, "end": segments[-1].end, "text": segments[-1].text}
            if segments
            else None
        ),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


async def handle_harvest_identify_rev(
    slide_file: str,
    videos: list[str],
    data_dir: Path,
    *,
    lang: str,
    top: int = 5,
    limit: int = 50,
    since: str | None = None,
    no_cache: bool = False,
    refresh_cache: bool = False,
    cache_root: str | None = None,
) -> str:
    """Identify the git revision ``slide_file`` was recorded against.

    Args:
        slide_file: Path to the slide file.
        videos: One or more video file paths.
        data_dir: Root data directory.
        lang: ``"de"`` or ``"en"``.
        top: Number of top-ranked revisions to return.
        limit: Maximum commits to score.
        since: git-log ``--since`` filter.
        no_cache / refresh_cache / cache_root: see
            :func:`handle_harvest_transcribe`.

    Returns:
        JSON string with ``{slide_file, fingerprint_labels,
        top_revisions: [...], accept_threshold}``.
    """
    from clm.voiceover.cache import CachePolicy
    from clm.voiceover.identify import identify_rev
    from clm.voiceover.rev_scorer import DEFAULT_ACCEPT_THRESHOLD

    sf = _resolve_under(data_dir, slide_file)
    vids = [_resolve_under(data_dir, v) for v in videos]
    policy = CachePolicy(
        enabled=not no_cache,
        refresh=refresh_cache,
        cache_root=Path(cache_root) if cache_root else None,
    )

    try:
        scored = identify_rev(sf, vids, lang=lang, top=top, limit=limit, since=since, policy=policy)
    except ValueError as exc:
        return json.dumps({"error": str(exc), "slide_file": str(sf)}, indent=2)

    payload = {
        "slide_file": str(sf),
        "language": lang,
        "accept_threshold": DEFAULT_ACCEPT_THRESHOLD,
        "top_revisions": [
            {
                "rev": r.rev,
                "date": r.date.isoformat() if r.date else None,
                "subject": r.subject,
                "base_score": r.base_score,
                "narrative_prior": r.narrative_prior,
                "score": r.score,
                "is_narrative_candidate": r.is_narrative_candidate,
                "run_id": r.run_id,
                "run_position": r.run_position,
            }
            for r in scored
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


async def handle_harvest_compare(
    source: str,
    target: str,
    data_dir: Path,
    *,
    lang: str,
    model: str | None = None,
    api_base: str | None = None,
) -> str:
    """Compare the voiceover of two slide files (read-only).

    Args:
        source: Older slide file (typically from ``sync-at-rev``).
        target: Current slide file.
        data_dir: Root data directory.
        lang: ``"de"`` or ``"en"``.
        model: Override the judge LLM model.
        api_base: Override the LLM API base URL.

    Returns:
        JSON string matching ``CompareReport.to_json()``.
    """
    from clm.voiceover.compare import run_compare_async

    src = _resolve_under(data_dir, source)
    tgt = _resolve_under(data_dir, target)
    report = await run_compare_async(
        source=src, target=tgt, lang=lang, model=model, api_base=api_base
    )
    return json.dumps(report.to_json(), indent=2, ensure_ascii=False)


async def handle_harvest_backfill_dry(
    slide_file: str,
    videos: list[str],
    data_dir: Path,
    *,
    lang: str,
    rev: str | None = None,
    auto: bool = True,
    force_rev: bool = False,
    top: int = 5,
    tag: str = "voiceover",
    whisper_model: str = "large-v3",
    backend: str = "faster-whisper",
    device: str = "auto",
    model: str | None = None,
    api_base: str | None = None,
) -> str:
    """Run the backfill pipeline in dry-run mode and return the diff.

    Invokes ``clm harvest backfill --dry-run`` as a subprocess so the
    full three-step composition (identify-rev → sync-at-rev →
    port) stays DRY. The working copy is never mutated.

    Args:
        slide_file: Path to the slide file at HEAD.
        videos: Recording video file paths.
        data_dir: Root data directory.
        lang: ``"de"`` or ``"en"``.
        rev: Skip identify-rev and use this SHA directly.
        auto: When ``True`` (default), pick the top-ranked rev
            automatically if ``rev`` is not supplied.
        force_rev: Proceed when the top score is below the accept
            threshold.
        top / tag / whisper_model / backend / device / model / api_base:
            Passed through to ``backfill``.

    Returns:
        JSON string with ``{returncode, stdout, stderr, command}``.
        Apply is intentionally unreachable — this tool never mutates.
    """
    import asyncio
    import shlex
    import sys

    sf = _resolve_under(data_dir, slide_file)
    vids = [_resolve_under(data_dir, v) for v in videos]

    cmd: list[str] = [
        sys.executable,
        "-m",
        "clm.cli.main",
        "harvest",
        "backfill",
        str(sf),
        *[str(v) for v in vids],
        "--lang",
        lang,
        "--dry-run",
        "--top",
        str(top),
        "--tag",
        tag,
        "--whisper-model",
        whisper_model,
        "--backend",
        backend,
        "--device",
        device,
    ]
    if rev is not None:
        cmd += ["--rev", rev]
    elif auto:
        cmd.append("--auto")
    if force_rev:
        cmd.append("--force-rev")
    if model:
        cmd += ["--model", model]
    if api_base:
        cmd += ["--api-base", api_base]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()

    payload = {
        "command": shlex.join(cmd),
        "returncode": proc.returncode,
        "stdout": stdout_b.decode("utf-8", errors="replace"),
        "stderr": stderr_b.decode("utf-8", errors="replace"),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


async def handle_harvest_cache_list(
    data_dir: Path,
    *,
    cache_root: str | None = None,
) -> str:
    """List artifact-cache entries for the current project.

    Args:
        data_dir: Root data directory.
        cache_root: Override the default ``.clm/voiceover-cache``.

    Returns:
        JSON string with ``{root, total_bytes, entries: [...]}``.
    """
    from clm.voiceover.cache import CachePolicy, iter_entries

    policy = CachePolicy(cache_root=Path(cache_root) if cache_root else None)
    root = policy.resolve_root(data_dir)

    if not root.exists():
        return json.dumps({"root": str(root), "entries": [], "total_bytes": 0}, indent=2)

    entries = iter_entries(root)
    total = sum(e.size for e in entries)
    payload = {
        "root": str(root),
        "total_bytes": total,
        "entries": [
            {"kind": e.subdir, "key": e.key, "size": e.size, "path": str(e.path)} for e in entries
        ],
    }
    return json.dumps(payload, indent=2)


async def handle_harvest_trace_show(
    path: str,
    data_dir: Path,
) -> str:
    """Read a voiceover trace log and return its entries as JSON.

    Args:
        path: Path to a ``.clm/voiceover-traces/*.jsonl`` file
            (absolute or relative to ``data_dir``).
        data_dir: Root data directory.

    Returns:
        JSON string with ``{path, schema_tags, entries: [...]}``.
    """
    from clm.voiceover.trace_log import read_trace_entries

    target = _resolve_under(data_dir, path)
    entries = read_trace_entries(target)
    schema_tags = sorted({e.get("schema", "<v0>") for e in entries})
    payload = {
        "path": str(target),
        "schema_tags": schema_tags,
        "entry_count": len(entries),
        "entries": entries,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


class _HarvestInputError(Exception):
    """A harvest report cannot be assembled (message = the reason)."""


def _assemble_harvest_report(
    slides: str,
    videos: list[str],
    data_dir: Path,
    *,
    lang: str,
    transcript: str | None,
    alignment: str | None,
    whisper_model: str,
    backend: str,
    device: str,
    no_cache: bool,
    refresh_cache: bool,
    cache_root: str | None,
):
    """Run the deterministic harvest tier and join it with the deck bundle.

    Mirrors the CLI's ``_load_bundle_or_exit`` + ``_build_report_data``
    (``clm.cli.commands.harvest``), but raises :class:`_HarvestInputError`
    instead of exiting so the MCP handlers can return ``{"error": ...}``.

    Returns:
        ``(bundle, report)`` — the loaded v3 bundle and the report envelope.
    """
    import click

    from clm.cli.commands.voiceover import (
        _load_alignment_override,
        _load_transcript_override,
    )
    from clm.notebooks.slide_parser import parse_slides
    from clm.slides.doc_lenses import DocLensError, load_bundle
    from clm.voiceover.cache import CachePolicy
    from clm.voiceover.harvest import HarvestUsageError, build_report, run_pipeline

    slides_path = _resolve_under(data_dir, slides)
    video_paths = [_resolve_under(data_dir, v) for v in videos]

    try:
        bundle = load_bundle(slides_path)
    except DocLensError as exc:
        raise _HarvestInputError(str(exc)) from exc
    if bundle.outcome.deck is None:
        refusal = bundle.outcome.refusal
        reasons = "; ".join(f"[{r.code}] {r.detail}" for r in refusal.reasons) if refusal else ""
        raise _HarvestInputError(
            "the deck bundle is not normalized"
            + (f": {reasons}" if reasons else "")
            + " — run `clm slides normalize` on the pair first"
        )

    # The recorded-language view the OCR matcher and aligner key on.
    slide_groups = parse_slides(slides_path, lang)

    policy = CachePolicy(
        enabled=not no_cache,
        refresh=refresh_cache,
        cache_root=Path(cache_root) if cache_root else None,
    )

    try:
        transcript_override = (
            _load_transcript_override(_resolve_under(data_dir, transcript)) if transcript else None
        )
        alignment_override = (
            _load_alignment_override(_resolve_under(data_dir, alignment)) if alignment else None
        )
        artifacts = run_pipeline(
            slides_path,
            video_paths,
            lang,
            slide_groups,
            policy=policy,
            backend_name=backend,
            whisper_model=whisper_model,
            device=device,
            transcript_override=transcript_override,
            alignment_override=alignment_override,
        )
    except (click.UsageError, HarvestUsageError) as exc:
        raise _HarvestInputError(str(exc)) from exc

    report = build_report(bundle, slide_groups, artifacts, lang=lang, video_paths=video_paths)
    return bundle, report


async def handle_harvest_report(
    slides: str,
    videos: list[str],
    data_dir: Path,
    *,
    lang: str,
    transcript: str | None = None,
    alignment: str | None = None,
    whisper_model: str = "large-v3",
    backend: str = "faster-whisper",
    device: str = "auto",
    no_cache: bool = False,
    refresh_cache: bool = False,
    cache_root: str | None = None,
) -> str:
    """What did the recording say, slide by slide? (read-only)

    The MCP twin of ``clm harvest report --json``: runs the cached
    deterministic tier (transcribe → detect → OCR-match → align) and joins
    it with the v3 deck bundle. No model, no key, no writes.

    Args:
        slides: The recorded-language deck half (absolute or relative to
            ``data_dir``).
        videos: Recording video file paths.
        data_dir: Root data directory.
        lang: The recorded (spoken) language (``"de"`` or ``"en"``).
        transcript: Load a precomputed transcript JSON from this path
            instead of running ASR (single-video only).
        alignment: Load a precomputed alignment JSON from this path,
            skipping ASR, detection, and matching (single-video only).
        whisper_model / backend / device: ASR knobs.
        no_cache / refresh_cache / cache_root: see
            :func:`handle_harvest_transcribe`.

    Returns:
        JSON string: the report envelope (``items`` keyed ``id:<slide>``,
        ``unmatched_speech``, ``summary``, ``video_fingerprint``), or an
        ``{"error": ...}`` object.
    """
    try:
        _, report = _assemble_harvest_report(
            slides,
            videos,
            data_dir,
            lang=lang,
            transcript=transcript,
            alignment=alignment,
            whisper_model=whisper_model,
            backend=backend,
            device=device,
            no_cache=no_cache,
            refresh_cache=refresh_cache,
            cache_root=cache_root,
        )
    except _HarvestInputError as exc:
        return json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False)
    return json.dumps(report, indent=2, ensure_ascii=False)


async def handle_harvest_task(
    slides: str,
    videos: list[str],
    data_dir: Path,
    *,
    lang: str,
    slide: str | None = None,
    kind: str = "curate",
    transcript: str | None = None,
    alignment: str | None = None,
    whisper_model: str = "large-v3",
    backend: str = "faster-whisper",
    device: str = "auto",
    no_cache: bool = False,
    refresh_cache: bool = False,
    cache_root: str | None = None,
) -> str:
    """Frame slide judgment tasks for the driving agent (read-only).

    The MCP twin of ``clm harvest task``: assembles the same report as
    :func:`handle_harvest_report`, then frames the curation/translation
    judgment per slide (instructions + inputs + ``answer_schema`` +
    freshness tokens). Writes go through ``clm harvest accept`` on the
    CLI — by design there is no MCP write path.

    Args:
        slides: The recorded-language deck half (absolute or relative to
            ``data_dir``).
        videos: Recording video file paths.
        data_dir: Root data directory.
        lang: The recorded (spoken) language (``"de"`` or ``"en"``).
        slide: Frame one slide (bare id or ``id:...`` handle). Omit to
            frame every actionable item.
        kind: ``"curate"`` (merge the recorded language) or
            ``"translate"`` (frame the twin side).
        transcript / alignment: precomputed-input overrides (see
            :func:`handle_harvest_report`).
        whisper_model / backend / device: ASR knobs.
        no_cache / refresh_cache / cache_root: see
            :func:`handle_harvest_transcribe`.

    Returns:
        JSON string ``{schema, tool, verb, video_fingerprint, tasks}``,
        or an ``{"error": ...}`` object when the named slide cannot be
        framed.
    """
    from clm.voiceover.harvest_task import TaskUnavailable, build_tasks

    try:
        bundle, report = _assemble_harvest_report(
            slides,
            videos,
            data_dir,
            lang=lang,
            transcript=transcript,
            alignment=alignment,
            whisper_model=whisper_model,
            backend=backend,
            device=device,
            no_cache=no_cache,
            refresh_cache=refresh_cache,
            cache_root=cache_root,
        )
    except _HarvestInputError as exc:
        return json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False)

    deck = bundle.outcome.deck
    assert deck is not None
    try:
        tasks = build_tasks(report, deck, kind=kind, slide=slide)
    except TaskUnavailable as exc:
        return json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False)

    payload = {
        "schema": 1,
        "tool": "harvest",
        "verb": "task",
        "video_fingerprint": report["video_fingerprint"],
        "tasks": tasks,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# course_authoring_rules
# ---------------------------------------------------------------------------


def _authoring_result_to_dict(result: AuthoringRulesResult) -> dict:
    """Convert an AuthoringRulesResult to a JSON-serializable dict."""
    d: dict = {
        "has_common_rules": result.common_rules is not None,
        "course_rules": [
            {"course_spec": e.course_spec, "rules": e.rules} for e in result.course_rules
        ],
        "merged": result.merged,
    }
    if result.notes:
        d["notes"] = result.notes
    return d


async def handle_course_authoring_rules(
    data_dir: Path,
    *,
    course_spec: str | None = None,
    slide_path: str | None = None,
) -> str:
    """Return merged authoring rules for a course or slide file.

    Args:
        data_dir: Root data directory (contains ``course-specs/``).
        course_spec: Course spec path or slug.
        slide_path: Path to a slide file (absolute or relative to
            data_dir).

    Returns:
        JSON string with authoring rules.
    """
    # Resolve relative slide_path against data_dir
    resolved_slide: str | None = None
    if slide_path:
        sp = Path(slide_path)
        if not sp.is_absolute():
            sp = data_dir / sp
        resolved_slide = str(sp)

    result = _get_authoring_rules(
        data_dir,
        course_spec=course_spec,
        slide_path=resolved_slide,
    )
    return json.dumps(_authoring_result_to_dict(result), indent=2)
