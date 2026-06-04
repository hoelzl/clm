"""Recording -> slide-version provenance drift (issue #208, step 5).

A :class:`~clm.recordings.state.RecordingPart` stamps the section/topic it
records and that topic's build-output digest at record time
(``slide_digest``). This module answers the question that makes the stamp
useful: **has the slide changed since the video was shot?** — by diffing the
stamped digest against the topic's current digest in the build provenance
manifest (``.clm-manifest.json``). No video or slide bytes are re-read; it is a
pure manifest comparison, so it is cheap enough to run over a whole course.
"""

from __future__ import annotations

from typing import Any

from attrs import frozen

from clm.core.provenance_manifest import manifest_topic_digest
from clm.recordings.state import CourseRecordingState, RecordingPart

# Drift verdicts.
UNKNOWN = "unknown"  # no topic stamped, no digest stamped, or topic absent from manifest
CURRENT = "current"  # stamped digest matches the topic's current build digest
CHANGED = "changed"  # the topic's built output changed since recording -> possibly stale


@frozen
class SlideDrift:
    """Whether a recording's slide changed since it was shot.

    ``status`` is :data:`UNKNOWN` / :data:`CURRENT` / :data:`CHANGED`.
    ``UNKNOWN`` means the comparison could not be made (the recording predates
    step-5 provenance, or the topic is not in the supplied manifest) — it is
    deliberately distinct from ``CURRENT`` so a caller never reports an
    unprovenanced recording as up to date.
    """

    topic_id: str | None
    status: str
    recorded_digest: str | None
    current_digest: str | None

    @property
    def is_stale(self) -> bool:
        return self.status == CHANGED


def part_slide_drift(part: RecordingPart, manifest: dict[str, Any]) -> SlideDrift:
    """Compare one recording part's stamped slide digest to *manifest*."""
    recorded = part.slide_digest
    topic_id = part.topic_id
    if not topic_id or not recorded:
        return SlideDrift(topic_id, UNKNOWN, recorded, None)
    current = manifest_topic_digest(manifest, topic_id)
    if current is None:
        return SlideDrift(topic_id, UNKNOWN, recorded, None)
    status = CURRENT if current == recorded else CHANGED
    return SlideDrift(topic_id, status, recorded, current)


@frozen
class RecordingDrift:
    """A drift verdict tied back to its (lecture, part) location."""

    lecture_id: str
    part: int
    drift: SlideDrift


def course_recording_drift(
    state: CourseRecordingState,
    manifest: dict[str, Any],
    *,
    stale_only: bool = False,
) -> list[RecordingDrift]:
    """Drift verdict for every recorded part in *state* against *manifest*.

    Inspects each part's **active take** (superseded takes are history). With
    ``stale_only=True`` only the :data:`CHANGED` parts are returned — the answer
    to "which videos do I need to re-record after these slide edits?".
    """
    results: list[RecordingDrift] = []
    for lecture in state.lectures:
        for part in lecture.parts:
            drift = part_slide_drift(part, manifest)
            if stale_only and not drift.is_stale:
                continue
            results.append(RecordingDrift(lecture.lecture_id, part.part, drift))
    return results
