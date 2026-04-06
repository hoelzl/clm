"""Standalone topic resolution for CLM slide repositories.

Extracts and generalizes the topic-resolution logic from
:meth:`Course._build_topic_map` so it can be used without constructing
a full ``Course`` object (e.g., by the MCP server, CLI commands, and
validation tools).
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path

from clm.infrastructure.utils.path_utils import (
    is_ignored_dir_for_course,
    is_slides_file,
    simplify_ordered_name,
)

logger = logging.getLogger(__name__)


@dataclass
class TopicMatch:
    """A single topic found on the filesystem."""

    topic_id: str
    path: Path
    path_type: str  # "directory" or "file"
    module: str  # parent module directory name
    slide_files: list[Path] = field(default_factory=list)


@dataclass
class GlobMatchEntry:
    """One entry in a glob-mode result."""

    topic_id: str
    path: Path
    path_type: str
    module: str


@dataclass
class ResolutionResult:
    """Result of resolving a topic ID (or glob pattern)."""

    topic_id: str
    path: Path | None = None
    path_type: str | None = None
    slide_files: list[Path] = field(default_factory=list)
    ambiguous: bool = False
    alternatives: list[TopicMatch] = field(default_factory=list)
    glob: bool = False
    matches: list[GlobMatchEntry] = field(default_factory=list)


def build_topic_map(slides_dir: Path) -> dict[str, list[TopicMatch]]:
    """Scan a ``slides/`` directory and return all topics found.

    Unlike :meth:`Course._build_topic_map` which keeps only the first
    occurrence of each topic ID, this function returns **all** occurrences
    so callers can detect ambiguity.

    Args:
        slides_dir: Path to the ``slides/`` directory (should contain
            ``module_*/`` subdirectories).

    Returns:
        Mapping from topic ID to a list of :class:`TopicMatch` objects.
        Most topic IDs will have exactly one match.  Multiple matches
        indicate ambiguity (same ID in different modules).
    """
    topic_map: dict[str, list[TopicMatch]] = {}

    if not slides_dir.is_dir():
        logger.warning("Slides directory does not exist: %s", slides_dir)
        return topic_map

    for module_dir in sorted(slides_dir.iterdir()):
        if not module_dir.is_dir():
            continue
        if is_ignored_dir_for_course(module_dir):
            continue

        for topic_path in sorted(module_dir.iterdir()):
            topic_id = simplify_ordered_name(topic_path.name)
            if not topic_id:
                continue

            path_type = "directory" if topic_path.is_dir() else "file"
            slide_files = find_slide_files(topic_path)

            match = TopicMatch(
                topic_id=topic_id,
                path=topic_path.resolve(),
                path_type=path_type,
                module=module_dir.name,
                slide_files=slide_files,
            )

            topic_map.setdefault(topic_id, []).append(match)

    return topic_map


def find_slide_files(topic_path: Path) -> list[Path]:
    """Return all slide files within a topic path.

    For directory topics, returns all ``slides_*.py``, ``topic_*.py``,
    and ``project_*.py`` files (detected via :func:`is_slides_file`).
    For file topics, returns ``[topic_path]`` if it is a slide file.

    Args:
        topic_path: Path to a topic directory or single file.

    Returns:
        List of slide file paths, sorted by name.
    """
    if topic_path.is_file():
        if is_slides_file(topic_path):
            return [topic_path.resolve()]
        return []

    if not topic_path.is_dir():
        return []

    return sorted(f.resolve() for f in topic_path.iterdir() if f.is_file() and is_slides_file(f))


def resolve_topic(
    topic_id: str,
    slides_dir: Path,
    *,
    course_topic_ids: set[str] | None = None,
) -> ResolutionResult:
    """Resolve a topic ID (or glob pattern) to filesystem path(s).

    Matching semantics:

    - **Exact match**: The topic ID must match exactly the portion of the
      directory/file name after ``topic_NNN_`` (computed by
      :func:`simplify_ordered_name`).
    - **Glob match**: If the query contains ``*`` or ``?``, all topic IDs
      matching the pattern are returned.

    Args:
        topic_id: Topic identifier or glob pattern (e.g., ``"what_is_ml"``
            or ``"what_is_ml*"``).
        slides_dir: Path to the ``slides/`` directory.
        course_topic_ids: When provided, only topics whose ID is in this
            set are considered.  Use this to scope resolution to topics
            referenced by a particular course spec.

    Returns:
        A :class:`ResolutionResult` describing the match outcome.
    """
    is_glob = "*" in topic_id or "?" in topic_id

    full_map = build_topic_map(slides_dir)

    # Filter to course-scoped topics if requested.
    if course_topic_ids is not None:
        full_map = {tid: matches for tid, matches in full_map.items() if tid in course_topic_ids}

    if is_glob:
        return _resolve_glob(topic_id, full_map)
    return _resolve_exact(topic_id, full_map)


def _resolve_exact(topic_id: str, topic_map: dict[str, list[TopicMatch]]) -> ResolutionResult:
    """Resolve an exact topic ID."""
    matches = topic_map.get(topic_id, [])

    if not matches:
        return ResolutionResult(topic_id=topic_id)

    if len(matches) == 1:
        m = matches[0]
        return ResolutionResult(
            topic_id=topic_id,
            path=m.path,
            path_type=m.path_type,
            slide_files=m.slide_files,
        )

    # Ambiguous: same ID in multiple modules.
    return ResolutionResult(
        topic_id=topic_id,
        ambiguous=True,
        alternatives=matches,
    )


def _resolve_glob(pattern: str, topic_map: dict[str, list[TopicMatch]]) -> ResolutionResult:
    """Resolve a glob pattern against topic IDs."""
    entries: list[GlobMatchEntry] = []

    for tid, matches in sorted(topic_map.items()):
        if fnmatch.fnmatch(tid, pattern):
            for m in matches:
                entries.append(
                    GlobMatchEntry(
                        topic_id=tid,
                        path=m.path,
                        path_type=m.path_type,
                        module=m.module,
                    )
                )

    return ResolutionResult(
        topic_id=pattern,
        glob=True,
        matches=entries,
    )


def get_course_topic_ids(course_spec) -> set[str]:
    """Extract the set of topic IDs referenced by a course spec.

    Args:
        course_spec: A :class:`CourseSpec` instance.

    Returns:
        Set of topic ID strings from all sections in the spec.
    """
    return {topic.id for section in course_spec.sections for topic in section.topics}
