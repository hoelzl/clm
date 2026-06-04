"""Tests for recording -> slide-version provenance drift (issue #208, step 5)."""

from __future__ import annotations

from clm.core.provenance_manifest import manifest_topic_digest
from clm.recordings.provenance import (
    CHANGED,
    CURRENT,
    UNKNOWN,
    course_recording_drift,
    part_slide_drift,
)
from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart


def _manifest(topic_hash: str, topic_id: str = "intro") -> dict:
    return {"files": [{"path": "Sec/x.ipynb", "topic_id": topic_id, "content_hash": topic_hash}]}


def _part(topic_id: str | None, digest: str | None) -> RecordingPart:
    return RecordingPart(part=1, raw_file="r.mp4", topic_id=topic_id, slide_digest=digest)


class TestPartSlideDrift:
    def test_current_when_digest_matches(self):
        # Record against one manifest, compare against an INDEPENDENT manifest
        # with identical topic content — CURRENT must hold across instances
        # (the digest is content-derived, not manifest-instance-derived).
        recorded = manifest_topic_digest(_manifest("sha256:v1"), "intro")
        part = _part("intro", recorded)
        drift = part_slide_drift(part, _manifest("sha256:v1"))
        assert drift.status == CURRENT
        assert not drift.is_stale
        assert drift.recorded_digest == drift.current_digest

    def test_changed_when_source_moved_on(self):
        recorded_at_v1 = manifest_topic_digest(_manifest("sha256:v1"), "intro")
        part = _part("intro", recorded_at_v1)
        drift = part_slide_drift(part, _manifest("sha256:v2"))
        assert drift.status == CHANGED
        assert drift.is_stale
        assert drift.recorded_digest != drift.current_digest

    def test_unknown_without_topic(self):
        drift = part_slide_drift(_part(None, "sha256:x"), _manifest("sha256:v1"))
        assert drift.status == UNKNOWN
        assert not drift.is_stale

    def test_unknown_without_recorded_digest(self):
        drift = part_slide_drift(_part("intro", None), _manifest("sha256:v1"))
        assert drift.status == UNKNOWN

    def test_unknown_when_topic_absent_from_manifest(self):
        # Recorded against 'intro' but the current manifest has only 'other'.
        part = _part("intro", "sha256:whatever")
        drift = part_slide_drift(part, _manifest("sha256:v1", topic_id="other"))
        assert drift.status == UNKNOWN
        assert drift.current_digest is None


class TestCourseRecordingDrift:
    def _state(self, intro_digest: str, deep_digest: str) -> CourseRecordingState:
        return CourseRecordingState(
            course_id="c",
            lectures=[
                LectureState(
                    lecture_id="L1",
                    display_name="Intro",
                    parts=[_part("intro", intro_digest)],
                ),
                LectureState(
                    lecture_id="L2",
                    display_name="Deep",
                    parts=[
                        RecordingPart(
                            part=1, raw_file="d.mp4", topic_id="deep", slide_digest=deep_digest
                        )
                    ],
                ),
            ],
        )

    def test_rollup_reports_each_part(self):
        # intro recorded at v1; current manifest keeps intro at v1 but moves deep to v2.
        current = {
            "files": [
                {"path": "a", "topic_id": "intro", "content_hash": "sha256:intro-v1"},
                {"path": "b", "topic_id": "deep", "content_hash": "sha256:deep-v2"},
            ]
        }
        intro_recorded = manifest_topic_digest(current, "intro")  # matches -> current
        deep_recorded = manifest_topic_digest(
            {"files": [{"path": "b", "topic_id": "deep", "content_hash": "sha256:deep-v1"}]}, "deep"
        )  # recorded at v1 -> changed
        state = self._state(intro_recorded, deep_recorded)

        results = course_recording_drift(state, current)
        by_lecture = {r.lecture_id: r.drift.status for r in results}
        assert by_lecture == {"L1": CURRENT, "L2": CHANGED}

    def test_stale_only_filters_to_changed(self):
        current = {
            "files": [
                {"path": "a", "topic_id": "intro", "content_hash": "sha256:intro-v1"},
                {"path": "b", "topic_id": "deep", "content_hash": "sha256:deep-v2"},
            ]
        }
        intro_recorded = manifest_topic_digest(current, "intro")
        deep_recorded = manifest_topic_digest(
            {"files": [{"path": "b", "topic_id": "deep", "content_hash": "sha256:deep-v1"}]}, "deep"
        )
        state = self._state(intro_recorded, deep_recorded)

        stale = course_recording_drift(state, current, stale_only=True)
        assert [r.lecture_id for r in stale] == ["L2"]
        assert stale[0].drift.is_stale
