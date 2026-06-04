"""Assemble slide-version provenance for a recording at arm time (issue #208).

The recordings state stores, per recorded part, the section/topic it records,
that topic's build-output digest, and the course repo's git commit/dirty state
(:mod:`clm.recordings.state`). Those fields are stamped by the *caller* — the
dashboard at ``/arm`` time, where the built :class:`~clm.core.course.Course`,
the spec file, and the course repo are all in hand. This module is that
assembly step.

Everything here is best-effort: a deck armed without a resolvable course
identity (CLI/tests), an unbuilt course (no ``.clm-manifest.json``), or a
non-git source tree must still record. Each field independently degrades to
``None``/``False`` and recording is never blocked. In particular this is the
first place the long-existing ``git_commit``/``git_dirty`` fields are actually
populated in the live flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class RecordProvenance:
    """Slide-version provenance stamped onto a recorded part.

    All fields are optional. ``section_id``/``topic_id`` come from resolving
    the armed deck against the course; ``slide_digest`` is the topic's
    build-output digest in the provenance manifest (``None`` when the course
    has not been built with a manifest); ``git_commit``/``git_dirty`` describe
    the course repo at record time.
    """

    section_id: str | None = None
    topic_id: str | None = None
    slide_digest: str | None = None
    git_commit: str | None = None
    git_dirty: bool = False


def build_record_provenance(
    course: Any,
    spec_file: Path | None,
    section_name: str,
    deck_name: str,
    lang: str,
) -> RecordProvenance:
    """Assemble :class:`RecordProvenance` for a deck about to be recorded.

    ``course`` is the built :class:`~clm.core.course.Course` (typed loosely so
    the web layer can pass its ``app.state.course`` without importing core).
    ``spec_file`` is the course spec path; it yields both the git repo root
    (via :func:`resolve_course_paths`) and the build-output root holding the
    provenance manifest. Any missing input leaves the corresponding fields
    unset — this function never raises.
    """
    section_id, topic_id = _resolve_section_topic(course, section_name, deck_name, lang)
    git_commit, git_dirty = _resolve_git_info(spec_file)
    slide_digest = _resolve_slide_digest(spec_file, topic_id)
    return RecordProvenance(
        section_id=section_id,
        topic_id=topic_id,
        slide_digest=slide_digest,
        git_commit=git_commit,
        git_dirty=git_dirty,
    )


def _resolve_section_topic(
    course: Any, section_name: str, deck_name: str, lang: str
) -> tuple[str | None, str | None]:
    if course is None:
        return None, None
    try:
        return course.resolve_deck_topic(section_name, deck_name, lang)  # type: ignore[no-any-return]
    except Exception as exc:  # pragma: no cover — defensive; resolver is total
        logger.debug("Could not resolve topic for {!r}/{!r}: {}", section_name, deck_name, exc)
        return None, None


def _resolve_git_info(spec_file: Path | None) -> tuple[str | None, bool]:
    if spec_file is None:
        return None, False
    try:
        from clm.core.course_paths import resolve_course_paths
        from clm.recordings.git_info import get_git_info

        course_root, _ = resolve_course_paths(spec_file)
        info = get_git_info(course_root)
        return info.get("commit"), bool(info.get("dirty"))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Could not capture git info for {}: {}", spec_file, exc)
        return None, False


def _resolve_slide_digest(spec_file: Path | None, topic_id: str | None) -> str | None:
    if spec_file is None or not topic_id:
        return None
    try:
        from clm.core.provenance_manifest import (
            find_course_manifest_path,
            load_manifest,
            manifest_topic_digest,
        )

        manifest_path = find_course_manifest_path(spec_file)
        if manifest_path is None:
            return None
        return manifest_topic_digest(load_manifest(manifest_path), topic_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Could not compute slide digest for topic {}: {}", topic_id, exc)
        return None
