"""Resolve a course spec to the deck files it actually pulls in.

This is the operation a course-conversion agent had to reimplement as a
throwaway script (gap #1 in ``docs/claude/course-conversion-tooling-gaps.md``):
"which decks does this spec build?" The naive answer — guess from deck filename
stems — is **wrong**, because a ``<topic>`` resolves to a topic *directory* and
CLM builds **every** ``slides_*.py`` in it; the directory name often differs from
the deck filenames (e.g. topic ``properties`` →
``slides_properties.py`` *and* ``slides_property_setters.py``).

The functions here mirror the build's resolution semantics exactly so the
"shipping set" is correct:

- topic → decks via :func:`clm.core.topic_resolver.build_topic_map` /
  ``TopicMatch.slide_files`` (the same scan the build uses);
- ``(topic_id, effective_module)`` binding via
  :func:`clm.core.topic_resolver.matches_for_binding`;
- **first-occurrence-wins** when an unbound topic ID matches multiple modules,
  matching ``Course._build_topic_map``.

Used by ``clm spec decks`` and ``clm slides referenced-by``; intended to be the
single source of truth for the spec→deck operation that gaps #2 (deep validate),
#3 (course gate), and #7 (orphans) will also need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clm.core.course_spec import CourseSpec
from clm.core.topic_resolver import (
    TopicMatch,
    build_topic_map,
    matches_for_binding,
)


@dataclass
class TopicDeckResolution:
    """How one ``<topic>`` reference in a spec resolved to deck files."""

    topic_id: str
    section: str
    """Human-readable section name (English) the topic was declared under."""
    requested_module: str | None
    """The effective module binding from the spec (``None`` = unbound)."""
    path: Path | None = None
    """The chosen topic directory/file (``None`` when unresolved)."""
    resolved_module: str | None = None
    """The module of the chosen match (``None`` when unresolved)."""
    slide_files: list[Path] = field(default_factory=list)
    """Deck files the chosen topic contributes, in build order."""
    shadowed: list[TopicMatch] = field(default_factory=list)
    """Other matches *not* chosen because of first-occurrence-wins."""

    @property
    def found(self) -> bool:
        return self.path is not None


@dataclass
class SpecDeckResolution:
    """The full set of decks a single spec pulls in."""

    spec_path: Path
    slides_dir: Path
    topics: list[TopicDeckResolution] = field(default_factory=list)

    @property
    def unresolved(self) -> list[TopicDeckResolution]:
        """Topic references that matched no directory on disk."""
        return [t for t in self.topics if not t.found]

    @property
    def deck_files(self) -> list[Path]:
        """Unique resolved deck paths across all topics, sorted by path.

        A deck can legitimately be referenced by more than one topic only in
        pathological specs; dedup keeps the shipping set honest either way.
        """
        seen: dict[Path, None] = {}
        for topic in self.topics:
            for deck in topic.slide_files:
                seen.setdefault(deck, None)
        return sorted(seen, key=lambda p: str(p))


def resolve_spec_decks(
    spec: CourseSpec,
    slides_dir: Path,
    *,
    topic_map: dict[str, list[TopicMatch]] | None = None,
) -> SpecDeckResolution:
    """Resolve every ``<topic>`` in *spec* to its deck files.

    Args:
        spec: A parsed :class:`~clm.core.course_spec.CourseSpec`.
        slides_dir: The course's ``slides/`` directory.
        topic_map: A pre-built topic map (from
            :func:`~clm.core.topic_resolver.build_topic_map`). Pass this when
            resolving many specs against the same ``slides_dir`` to avoid
            rescanning the filesystem per spec.

    Returns:
        A :class:`SpecDeckResolution` describing each topic reference and the
        union of decks. Resolution mirrors the build: module-bound references
        pick the match in that module; unbound references with multiple matches
        pick the first (first-occurrence-wins), recording the rest as
        ``shadowed``.
    """
    full_map = topic_map if topic_map is not None else build_topic_map(slides_dir)

    topics: list[TopicDeckResolution] = []
    for binding in spec.iter_topic_bindings():
        topic_id = binding.topic_spec.id
        module = binding.effective_module
        candidates = matches_for_binding(full_map, topic_id, module)

        resolution = TopicDeckResolution(
            topic_id=topic_id,
            section=binding.section.name.en,
            requested_module=module,
        )
        if candidates:
            chosen = candidates[0]
            resolution.path = chosen.path
            resolution.resolved_module = chosen.module
            resolution.slide_files = list(chosen.slide_files)
            resolution.shadowed = list(candidates[1:])
        topics.append(resolution)

    return SpecDeckResolution(
        spec_path=spec_path_of(spec, slides_dir),
        slides_dir=slides_dir,
        topics=topics,
    )


def spec_path_of(spec: CourseSpec, slides_dir: Path) -> Path:
    """Best-effort path label for a spec.

    ``CourseSpec`` does not retain the file it was parsed from, so callers that
    need the originating path should set it themselves. This returns the
    ``slides_dir`` parent as a stable placeholder when nothing better is known.
    """
    return slides_dir.parent


@dataclass
class DeckReference:
    """A spec/topic that pulls a given deck into its shipping set."""

    spec_path: Path
    topic_id: str
    section: str
    resolved_module: str | None


def find_deck_references(
    deck: Path,
    spec_files: list[Path],
    slides_dir: Path,
    *,
    topic_map: dict[str, list[TopicMatch]] | None = None,
) -> list[DeckReference]:
    """Reverse lookup: which specs/topics pull *deck* into their shipping set.

    Args:
        deck: A deck file to look up.
        spec_files: Spec XML files to consider.
        slides_dir: The shared ``slides/`` directory.
        topic_map: Optional pre-built topic map (scanned once for all specs).

    Returns:
        One :class:`DeckReference` per (spec, topic) that resolves to *deck*.
        Empty when the deck is unreferenced.
    """
    full_map = topic_map if topic_map is not None else build_topic_map(slides_dir)
    target = deck.resolve()

    references: list[DeckReference] = []
    for spec_file in spec_files:
        spec = CourseSpec.from_file(spec_file)
        resolution = resolve_spec_decks(spec, slides_dir, topic_map=full_map)
        for topic in resolution.topics:
            if any(d.resolve() == target for d in topic.slide_files):
                references.append(
                    DeckReference(
                        spec_path=spec_file,
                        topic_id=topic.topic_id,
                        section=topic.section,
                        resolved_module=topic.resolved_module,
                    )
                )
    return references


def shipping_set(
    spec_files: list[Path],
    slides_dir: Path,
    *,
    topic_map: dict[str, list[TopicMatch]] | None = None,
) -> set[Path]:
    """The union of resolved deck paths across *spec_files*.

    The "shipping set": every deck reachable from at least one spec, resolved
    (so the members are absolute paths that compare equal to a deck found by a
    filesystem walk). Specs that fail to parse are skipped. Reuses the same
    build-faithful resolution as :func:`resolve_spec_decks`.
    """
    full_map = topic_map if topic_map is not None else build_topic_map(slides_dir)
    decks: set[Path] = set()
    for spec_file in spec_files:
        try:
            spec = CourseSpec.from_file(spec_file)
        except Exception:
            continue
        resolution = resolve_spec_decks(spec, slides_dir, topic_map=full_map)
        decks.update(d.resolve() for d in resolution.deck_files)
    return decks
