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
from typing import Literal

from clm.infrastructure.utils.path_utils import (
    is_ignored_dir_for_course,
    is_private_dir_name,
    is_slides_file,
    simplify_ordered_name,
    slide_family_key,
    split_lang_suffix,
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

    Underscore-prefixed directories (``_archive``, ``_drafts``, …) are
    invisible to discovery at both the module and the topic level: parked
    content must never shadow a live topic ID via first-occurrence-wins
    resolution (issue #318).

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
        if is_private_dir_name(module_dir.name):
            continue

        for topic_path in sorted(module_dir.iterdir()):
            if is_private_dir_name(topic_path.name):
                continue
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

    This function only looks at *direct children* of ``topic_path``. To
    walk module or course-level paths recursively, use
    :func:`find_slide_files_recursive`.

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


@dataclass
class SlideUnit:
    """One slide-file routing unit derived from a topic directory.

    Each family of slide files sharing a stem (e.g. ``slides_foo.py`` and
    its split companions ``slides_foo.de.py`` / ``slides_foo.en.py``)
    collapses into one ``SlideUnit``. The build pipeline consumes units
    and decides how to route them per Phase 6 of the slide-format
    redesign:

    * ``kind="bilingual"`` — a bare bilingual file fans out to both
      DE and EN per-cell language filtering.
    * ``kind="split"`` — a complete pair routes ``.de.py`` through the
      DE pipeline only and ``.en.py`` through the EN pipeline only.
    * ``kind="dual_format"`` — both a bare file and at least one split
      companion exist for the same family; the build must refuse.
    * ``kind="half_pair"`` — only one of a split pair exists; the build
      must refuse.
    """

    kind: Literal["bilingual", "split", "dual_format", "half_pair"]
    bilingual_path: Path | None = None
    de_path: Path | None = None
    en_path: Path | None = None

    def iter_paths(self) -> list[Path]:
        """Return every concrete on-disk path this unit covers."""
        out: list[Path] = []
        if self.bilingual_path is not None:
            out.append(self.bilingual_path)
        if self.de_path is not None:
            out.append(self.de_path)
        if self.en_path is not None:
            out.append(self.en_path)
        return out

    @property
    def is_error(self) -> bool:
        return self.kind in ("dual_format", "half_pair")


def find_slide_units(topic_path: Path) -> list[SlideUnit]:
    """Group slide files in *topic_path* into :class:`SlideUnit`s.

    Walks the same direct-children set as :func:`find_slide_files` but
    collapses split pairs (``foo.de.py`` + ``foo.en.py``) into a single
    unit and surfaces the two error shapes that Phase 6 must reject
    (dual-format presence, half pair).

    For non-slide and bilingual paths the unit is one-to-one with the
    file. For split families both the de and en companions appear once
    in the returned list under the same :class:`SlideUnit`.
    """
    slide_paths = find_slide_files(topic_path)
    return _group_paths_into_units(slide_paths)


def _group_paths_into_units(slide_paths: list[Path]) -> list[SlideUnit]:
    """Group a flat list of resolved slide paths into :class:`SlideUnit`s.

    Order is preserved: the first path in ``slide_paths`` to reference a
    family determines that family's position in the output list. This
    keeps the build/processing order stable relative to the directory
    listing, matching pre-Phase-6 behaviour for bilingual decks.
    """
    families: dict[str, dict[str, Path]] = {}
    family_order: list[str] = []
    for path in slide_paths:
        key = slide_family_key(path)
        if key is None:
            continue
        bucket = families.get(key)
        if bucket is None:
            bucket = {}
            families[key] = bucket
            family_order.append(key)
        lang = split_lang_suffix(path)
        slot: str = lang if lang is not None else "bilingual"
        bucket[slot] = path

    units: list[SlideUnit] = []
    for key in family_order:
        bucket = families[key]
        bilingual = bucket.get("bilingual")
        de = bucket.get("de")
        en = bucket.get("en")
        if bilingual is not None and (de is not None or en is not None):
            units.append(
                SlideUnit(
                    kind="dual_format",
                    bilingual_path=bilingual,
                    de_path=de,
                    en_path=en,
                )
            )
            continue
        if bilingual is not None:
            units.append(SlideUnit(kind="bilingual", bilingual_path=bilingual))
            continue
        if de is not None and en is not None:
            units.append(SlideUnit(kind="split", de_path=de, en_path=en))
            continue
        # Exactly one of the split companions: a half pair.
        units.append(SlideUnit(kind="half_pair", de_path=de, en_path=en))
    return units


def find_slide_files_recursive(path: Path) -> list[Path]:
    """Return slide files at ``path``, recursing into subdirectories if needed.

    Behaviour by input shape:

    * Single file → returns ``[path]`` if it is a slide file, else ``[]``.
    * Topic directory (has direct slide files) → identical to
      :func:`find_slide_files` (no descent into nested subdirs, since topic
      directories are not expected to contain them).
    * Module / course-root / arbitrary parent directory (no direct slide
      files) → walks the full subtree and returns every file matching
      :func:`is_slides_file`.

    The early-exit on direct-children matches existed before this helper
    was lifted out of ``normalizer._find_slide_files_recursive``; it
    preserves topic-resolver semantics for topic dirs while still letting
    callers operate on coarser paths.

    Underscore-prefixed directories *below* ``path`` are pruned from the
    walk, matching :func:`build_topic_map` discovery (issue #318): a deck
    parked under ``slides/_archive/`` is invisible when walking ``slides/``.
    The prune is applied to each file's path relative to ``path``, so an
    explicitly named root (``find_slide_files_recursive(Path("_archive"))``)
    is still honoured.
    """
    if path.is_file():
        if is_slides_file(path):
            return [path.resolve()]
        return []

    if not path.is_dir():
        return []

    direct = find_slide_files(path)
    if direct:
        return direct

    # rglob("*") (not "*.py") so the is_slides_file filter — which already accepts
    # every SUPPORTED_PROG_LANG_EXTENSIONS — finds .cs/.cpp/.java/.ts decks too.
    return sorted(
        f.resolve()
        for f in path.rglob("*")
        if f.is_file()
        and is_slides_file(f)
        and not any(is_private_dir_name(part) for part in f.relative_to(path).parts[:-1])
    )


def resolve_topic(
    topic_id: str,
    slides_dir: Path,
    *,
    course_topic_ids: set[str] | None = None,
    course_topic_bindings: set[tuple[str, str | None]] | None = None,
    module: str | None = None,
) -> ResolutionResult:
    """Resolve a topic ID (or glob pattern) to filesystem path(s).

    Matching semantics:

    - **Exact match**: The topic ID must match exactly the portion of the
      directory/file name after ``topic_NNN_`` (computed by
      :func:`simplify_ordered_name`).
    - **Glob match**: If the query contains ``*`` or ``?``, all topic IDs
      matching the pattern are returned.
    - **Module binding**: When ``module`` is provided, resolution is
      restricted to topics whose parent module directory equals ``module``.
      This is used by course specs that bind ``<section>`` or ``<topic>``
      references to a specific module so duplicate topic IDs across modules
      are unambiguous (e.g., frozen-cohort archives that share topic IDs
      with the live module).

    Args:
        topic_id: Topic identifier or glob pattern (e.g., ``"what_is_ml"``
            or ``"what_is_ml*"``).
        slides_dir: Path to the ``slides/`` directory.
        course_topic_ids: When provided, only topics whose ID is in this
            set are considered. Drops module bindings — kept for backward
            compatibility; new callers should pass ``course_topic_bindings``
            so cohort archives that share topic IDs with the live module
            are correctly disambiguated.
        course_topic_bindings: When provided, only topics whose
            ``(topic_id, module)`` is referenced by the spec are returned.
            A spec entry ``(tid, None)`` (unbound) matches any module of
            ``tid``; a bound entry ``(tid, X)`` matches only matches in
            module ``X``. Takes precedence over ``course_topic_ids`` if
            both are supplied.
        module: When provided, only topics whose parent module directory
            equals this name are considered. Used for module-bound topic
            references in course specs.

    Returns:
        A :class:`ResolutionResult` describing the match outcome.
    """
    is_glob = "*" in topic_id or "?" in topic_id

    full_map = build_topic_map(slides_dir)

    # Filter to course-scoped topics if requested. Module-aware bindings
    # win over the legacy ID-only set so cohort archives are correctly
    # disambiguated.
    if course_topic_bindings is not None:
        full_map = {
            tid: [
                m
                for m in matches
                if (tid, m.module) in course_topic_bindings or (tid, None) in course_topic_bindings
            ]
            for tid, matches in full_map.items()
        }
        full_map = {tid: matches for tid, matches in full_map.items() if matches}
    elif course_topic_ids is not None:
        full_map = {tid: matches for tid, matches in full_map.items() if tid in course_topic_ids}

    # Filter to a specific module if requested. Drop entries whose match
    # list becomes empty so resolution treats them as not-found.
    if module is not None:
        full_map = {
            tid: [m for m in matches if m.module == module] for tid, matches in full_map.items()
        }
        full_map = {tid: matches for tid, matches in full_map.items() if matches}

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

    Drops module bindings — two sections that bind the same topic ID to
    different modules collapse into a single entry. Use
    :meth:`clm.core.course_spec.CourseSpec.topic_bindings` instead when
    cohort archives or per-section ``module=`` bindings need to be
    respected.

    Args:
        course_spec: A :class:`CourseSpec` instance.

    Returns:
        Set of topic ID strings from all sections in the spec.
    """
    return {topic.id for section in course_spec.sections for topic in section.topics}


def matches_for_binding(
    topic_map: dict[str, list[TopicMatch]],
    topic_id: str,
    module: str | None,
) -> list[TopicMatch]:
    """Look up *topic_id* in *topic_map*, optionally filtered by *module*.

    Centralizes the "filter ``topic_map[id]`` by effective module" step so
    every spec consumer (build, validate, normalize, search) applies it
    consistently. When *module* is ``None`` the binding is unbound and
    every match is returned (the caller then deals with ambiguity per its
    own policy — e.g. first-occurrence-wins for build, or an "ambiguous"
    finding for the spec validator).
    """
    matches = topic_map.get(topic_id, [])
    if module is not None:
        matches = [m for m in matches if m.module == module]
    return matches
