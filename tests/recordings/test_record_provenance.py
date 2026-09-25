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


# ---------------------------------------------------------------------------
# Source anchor for the recordings ledger (#1004)
# ---------------------------------------------------------------------------

_HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel\") }}\n\n"
_HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title\") }}\n\n"
_DE = _HEADER_DE + '# %% [markdown] lang="de" tags=["slide"] slide_id="s0"\n#\n# # Titel\n'
_EN = _HEADER_EN + '# %% [markdown] lang="en" tags=["slide"] slide_id="s0"\n#\n# # Title\n'


class _DeckCourse(_FakeCourse):
    """A course that also resolves the deck's source file."""

    def __init__(self, mapping, files):
        super().__init__(mapping)
        self._files = files

    def resolve_deck_file(self, section_name, deck_name, lang):
        return self._files.get((section_name, deck_name, lang))


def _split_pair(folder):
    folder.mkdir(parents=True)
    (folder / "slides_t.de.py").write_text(_DE, encoding="utf-8")
    (folder / "slides_t.en.py").write_text(_EN, encoding="utf-8")
    return folder / "slides_t.de.py"


def test_deck_path_and_members_resolved_for_the_recorded_language(tmp_path):
    de = _split_pair(tmp_path / "topic_x")
    course = _DeckCourse(
        {("Week 1", "00 Intro", "de"): ("sec-1", "topic-x")},
        {("Week 1", "00 Intro", "de"): de},
    )

    prov = build_record_provenance(course, None, "Week 1", "00 Intro", "de")

    assert prov.deck_path == de
    assert prov.members is not None and "id:s0" in prov.members
    from clm.recordings.ledger import deck_members

    assert prov.members == deck_members(de, "de")


def test_course_without_deck_file_resolver_leaves_anchor_unset(tmp_path):
    course = _FakeCourse({("Week 1", "00 Intro", "en"): ("sec-1", "topic-x")})
    prov = build_record_provenance(course, None, "Week 1", "00 Intro", "en")
    assert prov.deck_path is None
    assert prov.members is None


def test_unparseable_deck_keeps_path_with_empty_members(tmp_path):
    folder = tmp_path / "topic_x"
    folder.mkdir()
    lone = folder / "slides_t.de.py"
    lone.write_text(_DE, encoding="utf-8")  # no twin: not a split pair
    course = _DeckCourse({}, {("Week 1", "00 Intro", "de"): lone})

    prov = build_record_provenance(course, None, "Week 1", "00 Intro", "de")

    assert prov.deck_path == lone
    assert prov.members == {}
