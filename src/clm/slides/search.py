"""Fuzzy search across topic names and slide file titles."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.topic_resolver import (
    TopicMatch,
    build_topic_map,
)
from clm.core.utils.notebook_utils import find_notebook_titles

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz as _fuzz

    def _score(query: str, text: str) -> float:
        return float(_fuzz.token_set_ratio(query.lower(), text.lower()))

except ImportError:

    def _score(query: str, text: str) -> float:
        """Substring fallback when rapidfuzz is not installed."""
        q = query.lower()
        t = text.lower()
        if q == t:
            return 100.0
        if q in t:
            return 80.0
        # Check individual words
        words = q.split()
        if words and all(w in t for w in words):
            return 60.0
        if any(w in t for w in words):
            return 40.0
        return 0.0


@dataclass
class SlideInfo:
    file: str
    title_de: str
    title_en: str


@dataclass
class SearchResult:
    score: float
    topic_id: str
    directory: str
    slides: list[SlideInfo] = field(default_factory=list)
    courses: list[str] = field(default_factory=list)


def search_slides(
    query: str,
    slides_dir: Path,
    *,
    course_spec_path: Path | None = None,
    language: str | None = None,
    max_results: int = 10,
) -> list[SearchResult]:
    """Fuzzy search across topic names and slide file titles.

    Searches topic directory names, slide file names, and bilingual
    titles extracted from header macros.

    Args:
        query: Search query (e.g., "decorators", "RAG introduction").
        slides_dir: Path to the ``slides/`` directory.
        course_spec_path: Optional course spec to limit search scope.
        language: When set, only score titles in this language.
        max_results: Maximum number of results to return.

    Returns:
        List of :class:`SearchResult` sorted by score descending.
    """
    topic_map = build_topic_map(slides_dir)

    # Load course spec for scoping and course-membership info. ``scope`` is
    # the set of (topic_id, module) pairs the spec actually references —
    # using bare topic IDs would let cohort-archive copies leak into
    # results when the spec deliberately binds sections to one module.
    scope: set[tuple[str, str | None]] | None = None

    if course_spec_path:
        try:
            spec = CourseSpec.from_file(course_spec_path)
            scope = spec.topic_bindings()
        except CourseSpecError:
            logger.warning("Failed to parse course spec: %s", course_spec_path)

    # Also scan all spec files to build course membership, keyed by
    # ``(topic_id, module)`` so a course bound to module X is not listed
    # for the same topic ID found in module Y.
    membership: dict[tuple[str, str | None], list[str]] = {}
    specs_dir = slides_dir.parent / "course-specs"
    if specs_dir.is_dir():
        membership = _build_course_membership(specs_dir)

    results: list[SearchResult] = []

    for topic_id, matches in topic_map.items():
        for match in matches:
            if scope is not None and not _binding_in_scope(scope, topic_id, match.module):
                continue

            score, slides = _score_topic(query, match, language)
            if score < 20.0:
                continue

            results.append(
                SearchResult(
                    score=round(score, 1),
                    topic_id=topic_id,
                    directory=str(match.path),
                    slides=slides,
                    courses=_courses_for_match(membership, topic_id, match.module),
                )
            )

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


def _binding_in_scope(
    scope: set[tuple[str, str | None]],
    topic_id: str,
    module: str,
) -> bool:
    """Match a filesystem topic against a spec's ``topic_bindings()`` set.

    A bound entry ``(tid, X)`` matches the *specific* module ``X``. An
    unbound entry ``(tid, None)`` matches *any* module — preserves the
    long-standing first-occurrence-wins resolution for specs that don't
    pin their topics to a module.
    """
    return (topic_id, module) in scope or (topic_id, None) in scope


def _score_topic(
    query: str,
    match: TopicMatch,
    language: str | None,
) -> tuple[float, list[SlideInfo]]:
    """Score a topic against a query. Returns (best_score, slide_infos)."""
    scores: list[float] = []

    # Score against topic_id (underscores -> spaces for matching)
    topic_text = match.topic_id.replace("_", " ")
    scores.append(_score(query, topic_text))

    # Score against slide file titles
    slide_infos: list[SlideInfo] = []
    for slide_file in match.slide_files:
        try:
            text = slide_file.read_text(encoding="utf-8")
            titles = find_notebook_titles(text, default=slide_file.stem)
        except Exception:
            titles = None

        if titles:
            info = SlideInfo(
                file=slide_file.name,
                title_de=titles.de,
                title_en=titles.en,
            )
            slide_infos.append(info)

            if language == "de" or language is None:
                scores.append(_score(query, titles.de))
            if language == "en" or language is None:
                scores.append(_score(query, titles.en))
        else:
            # Fall back to filename matching
            stem = slide_file.stem.replace("_", " ")
            scores.append(_score(query, stem))
            slide_infos.append(SlideInfo(file=slide_file.name, title_de="", title_en=""))

    best_score = max(scores) if scores else 0.0
    return best_score, slide_infos


def _build_course_membership(specs_dir: Path) -> dict[tuple[str, str | None], list[str]]:
    """Scan course spec XMLs and return ``(topic_id, module) -> spec names``.

    Module-aware so a search hit in module X does not list courses that
    bind the same topic ID to module Y. Unbound bindings get a ``None``
    module entry that :func:`_courses_for_match` treats as wildcard.
    """
    membership: dict[tuple[str, str | None], list[str]] = {}

    for spec_file in sorted(specs_dir.iterdir()):
        if not spec_file.suffix == ".xml":
            continue
        try:
            spec = CourseSpec.from_file(spec_file)
            for tid, module in spec.topic_bindings():
                membership.setdefault((tid, module), []).append(spec_file.name)
        except Exception:
            logger.debug("Skipping unparseable spec: %s", spec_file)

    return membership


def _courses_for_match(
    membership: dict[tuple[str, str | None], list[str]],
    topic_id: str,
    module: str,
) -> list[str]:
    """Combine bound (``topic_id``, ``module``) and unbound entries.

    A course's binding ``(tid, X)`` is listed only for matches in module
    ``X``. An unbound binding ``(tid, None)`` is listed for every match
    of ``tid`` since unbound resolution can land in any module
    (first-occurrence-wins).
    """
    bound = membership.get((topic_id, module), [])
    unbound = membership.get((topic_id, None), [])
    if not unbound:
        return bound
    if not bound:
        return unbound
    seen: set[str] = set()
    combined: list[str] = []
    for name in (*bound, *unbound):
        if name in seen:
            continue
        seen.add(name)
        combined.append(name)
    return combined
