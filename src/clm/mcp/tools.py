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
    """Get a Course object, using a cache keyed by spec file mtime."""
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
) -> str:
    """Generate a structured JSON outline for a course.

    Args:
        spec_file: Path to the course spec file (absolute or relative to data_dir).
        data_dir: Root data directory.
        language: Language code (``"en"`` or ``"de"``).

    Returns:
        JSON string with the course outline.
    """
    from clm.cli.commands.outline import generate_outline_json

    spec_path = Path(spec_file)
    if not spec_path.is_absolute():
        spec_path = data_dir / spec_path

    course = _get_cached_course(spec_path)
    outline = generate_outline_json(course, language)
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
) -> str:
    """Validate a course specification XML file.

    Args:
        course_spec: Path to the course spec file (absolute or relative
            to data_dir).
        data_dir: Root data directory (contains ``slides/``).

    Returns:
        JSON string with validation results.
    """
    spec_path = Path(course_spec)
    if not spec_path.is_absolute():
        spec_path = data_dir / spec_path

    slides_dir = data_dir / "slides"
    result = _validate_spec(spec_path, slides_dir)
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
