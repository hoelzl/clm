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
    get_course_topic_ids,
)
from clm.core.topic_resolver import (
    resolve_topic as _resolve_topic,
)
from clm.slides.authoring_rules import AuthoringRulesResult
from clm.slides.authoring_rules import get_authoring_rules as _get_authoring_rules
from clm.slides.language_tools import SyncResult
from clm.slides.language_tools import get_language_view as _get_language_view
from clm.slides.language_tools import suggest_sync as _suggest_sync
from clm.slides.normalizer import NormalizationResult
from clm.slides.normalizer import normalize_course as _normalize_course
from clm.slides.normalizer import normalize_directory as _normalize_directory
from clm.slides.normalizer import normalize_file as _normalize_file
from clm.slides.search import SearchResult
from clm.slides.search import search_slides as _search_slides
from clm.slides.spec_validator import SpecValidationResult
from clm.slides.spec_validator import validate_spec as _validate_spec
from clm.slides.validator import ValidationResult
from clm.slides.validator import validate_course as _validate_course
from clm.slides.validator import validate_directory as _validate_directory
from clm.slides.validator import validate_file as _validate_file
from clm.slides.voiceover_tools import ExtractionResult, InlineResult
from clm.slides.voiceover_tools import extract_voiceover as _extract_voiceover
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
) -> str:
    """Resolve a topic ID or glob pattern to filesystem path(s).

    Args:
        topic_id: Topic identifier or glob pattern.
        data_dir: Root data directory (contains ``slides/``).
        course_spec: Optional path to a course spec file to scope resolution.

    Returns:
        JSON string with resolution result.
    """
    slides_dir = data_dir / "slides"

    course_topic_ids: set[str] | None = None
    if course_spec:
        try:
            spec = CourseSpec.from_file(Path(course_spec))
            course_topic_ids = get_course_topic_ids(spec)
        except CourseSpecError:
            logger.warning("Failed to parse course spec: %s", course_spec)

    result = _resolve_topic(topic_id, slides_dir, course_topic_ids=course_topic_ids)
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
    from clm.cli.commands.outline import generate_outline_json

    spec_path = Path(spec_file)
    if not spec_path.is_absolute():
        spec_path = data_dir / spec_path

    course = _get_cached_course(spec_path)

    disabled_sections = []
    if include_disabled:
        full_spec = CourseSpec.from_file(spec_path, keep_disabled=True)
        disabled_sections = [s for s in full_spec.sections if not s.enabled]

    outline = generate_outline_json(course, language, disabled_sections=disabled_sections)
    return json.dumps(outline, indent=2)


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
        checks: Which checks to run.  Default: all.

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
) -> str:
    """Normalize slide files by applying mechanical fixes.

    Args:
        path: Path to a slide file, topic directory, or course spec XML
            (absolute or relative to data_dir).
        data_dir: Root data directory (contains ``slides/``).
        operations: Which operations to apply.  Default: all.
        dry_run: If ``True``, preview changes without modifying files.

    Returns:
        JSON string with normalization results.
    """
    target = Path(path)
    if not target.is_absolute():
        target = data_dir / target

    if target.is_file() and target.suffix == ".xml":
        slides_dir = data_dir / "slides"
        result = _normalize_course(target, slides_dir, operations=operations, dry_run=dry_run)
    elif target.is_dir():
        result = _normalize_directory(target, operations=operations, dry_run=dry_run)
    else:
        result = _normalize_file(target, operations=operations, dry_run=dry_run)

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


async def handle_extract_voiceover(
    file: str,
    data_dir: Path,
    *,
    dry_run: bool = False,
) -> str:
    """Extract voiceover cells from a slide file to a companion file.

    Args:
        file: Path to the slide file (absolute or relative to data_dir).
        data_dir: Root data directory.
        dry_run: If ``True``, preview without writing files.

    Returns:
        JSON string with extraction results.
    """
    target = Path(file)
    if not target.is_absolute():
        target = data_dir / target

    result = _extract_voiceover(target, dry_run=dry_run)
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
        "companion_deleted": result.companion_deleted,
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
# voiceover_transcribe, voiceover_identify_rev, voiceover_compare,
# voiceover_backfill_dry, voiceover_cache_list, voiceover_trace_show
# ---------------------------------------------------------------------------


def _resolve_under(data_dir: Path, p: str) -> Path:
    """Resolve ``p`` as absolute, or relative to ``data_dir`` otherwise."""
    target = Path(p)
    return target if target.is_absolute() else data_dir / target


async def handle_voiceover_transcribe(
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


async def handle_voiceover_identify_rev(
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
            :func:`handle_voiceover_transcribe`.

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


async def handle_voiceover_compare(
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


async def handle_voiceover_backfill_dry(
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

    Invokes ``clm voiceover backfill --dry-run`` as a subprocess so the
    full three-step composition (identify-rev → sync-at-rev →
    port-voiceover) stays DRY. The working copy is never mutated.

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
        "voiceover",
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


async def handle_voiceover_cache_list(
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


async def handle_voiceover_trace_show(
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
