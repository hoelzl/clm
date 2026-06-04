"""Tests for the record-time provenance assembler (issue #208 follow-up).

``build_record_provenance`` stitches together the (section, topic) resolver,
the git capture, and the build-manifest digest. Each input degrades to
``None`` independently and the function never raises — these tests pin both
the happy path and the degraded paths.
"""

import json

from clm.core.provenance_manifest import MANIFEST_FILENAME, topic_digest_from_files
from clm.recordings.record_provenance import RecordProvenance, build_record_provenance


class _FakeCourse:
    """Stand-in for a built Course exposing only ``resolve_deck_topic``."""

    def __init__(self, mapping):
        self._mapping = mapping

    def resolve_deck_topic(self, section_name, deck_name, lang):
        return self._mapping.get((section_name, deck_name, lang), (None, None))


class _RaisingCourse:
    def resolve_deck_topic(self, *a, **k):
        raise RuntimeError("boom")


def _spec_with_manifest(tmp_path, topic_id):
    """Lay out a spec whose default output root holds a manifest for *topic_id*."""
    specs = tmp_path / "course-specs"
    specs.mkdir()
    spec = specs / "course.xml"
    spec.write_text("<course/>", encoding="utf-8")
    out = tmp_path / "output"
    out.mkdir()
    files = [
        {"path": "a", "topic_id": topic_id, "content_hash": "sha256:a"},
        {"path": "b", "topic_id": topic_id, "content_hash": "sha256:b"},
    ]
    (out / MANIFEST_FILENAME).write_text(json.dumps({"files": files}), encoding="utf-8")
    return spec, topic_digest_from_files(files)


def test_full_provenance(tmp_path, monkeypatch):
    spec, expected_digest = _spec_with_manifest(tmp_path, "topic-x")
    course = _FakeCourse({("Week 1", "00 Intro", "en"): ("sec-1", "topic-x")})
    monkeypatch.setattr(
        "clm.recordings.git_info.get_git_info",
        lambda p: {"commit": "deadbeef", "dirty": True},
    )

    prov = build_record_provenance(course, spec, "Week 1", "00 Intro", "en")

    assert prov == RecordProvenance(
        section_id="sec-1",
        topic_id="topic-x",
        slide_digest=expected_digest,
        git_commit="deadbeef",
        git_dirty=True,
    )


def test_no_manifest_leaves_digest_none(tmp_path, monkeypatch):
    specs = tmp_path / "course-specs"
    specs.mkdir()
    spec = specs / "course.xml"
    spec.write_text("<course/>", encoding="utf-8")
    course = _FakeCourse({("Week 1", "00 Intro", "en"): ("sec-1", "topic-x")})
    monkeypatch.setattr(
        "clm.recordings.git_info.get_git_info",
        lambda p: {"commit": "abc", "dirty": False},
    )

    prov = build_record_provenance(course, spec, "Week 1", "00 Intro", "en")

    assert prov.topic_id == "topic-x"
    assert prov.slide_digest is None
    assert prov.git_commit == "abc"
    assert prov.git_dirty is False


def test_unresolved_deck_has_no_topic_or_digest(tmp_path, monkeypatch):
    spec, _ = _spec_with_manifest(tmp_path, "topic-x")
    course = _FakeCourse({})  # nothing resolves
    monkeypatch.setattr(
        "clm.recordings.git_info.get_git_info", lambda p: {"commit": "c", "dirty": False}
    )

    prov = build_record_provenance(course, spec, "Week 1", "00 Intro", "en")

    assert prov.section_id is None
    assert prov.topic_id is None
    # No topic -> no digest, even though a manifest exists.
    assert prov.slide_digest is None
    assert prov.git_commit == "c"


def test_non_git_tree_leaves_git_none(tmp_path):
    spec, _ = _spec_with_manifest(tmp_path, "topic-x")
    course = _FakeCourse({("Week 1", "00 Intro", "en"): ("sec-1", "topic-x")})

    # tmp_path is not a git repo: get_git_info returns commit=None.
    prov = build_record_provenance(course, spec, "Week 1", "00 Intro", "en")

    assert prov.topic_id == "topic-x"
    assert prov.git_commit is None
    assert prov.git_dirty is False


def test_none_course_and_spec_is_all_none():
    prov = build_record_provenance(None, None, "Week 1", "00 Intro", "en")
    assert prov == RecordProvenance()


def test_resolver_exception_is_swallowed(tmp_path):
    spec, _ = _spec_with_manifest(tmp_path, "topic-x")
    prov = build_record_provenance(_RaisingCourse(), spec, "Week 1", "00 Intro", "en")
    # The resolver blew up but git/digest still computed without a topic.
    assert prov.section_id is None
    assert prov.topic_id is None
    assert prov.slide_digest is None
