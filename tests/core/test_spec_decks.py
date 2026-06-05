"""Tests for :mod:`clm.core.spec_decks` (spec → deck resolution)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.core.course_spec import CourseSpec
from clm.core.spec_decks import (
    find_deck_references,
    resolve_spec_decks,
)


def _write_spec(tmp_path: Path, sections_xml: str, name: str = "test.xml") -> Path:
    spec_file = tmp_path / "course-specs" / name
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        dedent(f"""\
        <course>
          <name><de>Test</de><en>Test</en></name>
          <prog-lang>python</prog-lang>
          <description><de></de><en></en></description>
          <certificate><de></de><en></en></certificate>
          {sections_xml}
        </course>
        """),
        encoding="utf-8",
    )
    return spec_file


def _topic(tmp_path: Path, module: str, topic: str, *decks: str) -> Path:
    topic_dir = tmp_path / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    for deck in decks:
        (topic_dir / deck).write_text("# %% [markdown]\n# Hello\n", encoding="utf-8")
    return topic_dir


@pytest.fixture()
def slides_dir(tmp_path: Path) -> Path:
    return tmp_path / "slides"


class TestResolveSpecDecks:
    def test_topic_pulls_in_every_deck_in_its_directory(self, tmp_path, slides_dir):
        # The whole point of gap #1: one topic dir → multiple decks.
        _topic(
            tmp_path,
            "module_100_basics",
            "topic_010_props",
            "slides_properties.py",
            "slides_property_setters.py",
        )
        spec = CourseSpec.from_file(
            _write_spec(
                tmp_path,
                "<sections><section><name><de>S</de><en>S</en></name>"
                "<topics><topic>props</topic></topics></section></sections>",
            )
        )

        resolution = resolve_spec_decks(spec, slides_dir)

        names = sorted(d.name for d in resolution.deck_files)
        assert names == ["slides_properties.py", "slides_property_setters.py"]
        assert resolution.unresolved == []

    def test_unresolved_topic_is_reported_not_dropped(self, tmp_path, slides_dir):
        _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        spec = CourseSpec.from_file(
            _write_spec(
                tmp_path,
                "<sections><section><name><de>S</de><en>S</en></name>"
                "<topics><topic>intro</topic><topic>ghost</topic></topics>"
                "</section></sections>",
            )
        )

        resolution = resolve_spec_decks(spec, slides_dir)

        assert [t.topic_id for t in resolution.unresolved] == ["ghost"]
        assert len(resolution.deck_files) == 1

    def test_unbound_duplicate_is_first_occurrence_wins(self, tmp_path, slides_dir):
        # Same topic id in two modules, unbound reference → build picks the
        # first (sorted) module and records the other as shadowed.
        _topic(tmp_path, "module_100_a", "topic_010_intro", "slides_a.py")
        _topic(tmp_path, "module_200_b", "topic_010_intro", "slides_b.py")
        spec = CourseSpec.from_file(
            _write_spec(
                tmp_path,
                "<sections><section><name><de>S</de><en>S</en></name>"
                "<topics><topic>intro</topic></topics></section></sections>",
            )
        )

        resolution = resolve_spec_decks(spec, slides_dir)

        assert [d.name for d in resolution.deck_files] == ["slides_a.py"]
        topic = resolution.topics[0]
        assert topic.resolved_module == "module_100_a"
        assert [m.module for m in topic.shadowed] == ["module_200_b"]

    def test_module_bound_reference_picks_that_module(self, tmp_path, slides_dir):
        _topic(tmp_path, "module_100_a", "topic_010_intro", "slides_a.py")
        _topic(tmp_path, "module_200_b", "topic_010_intro", "slides_b.py")
        spec = CourseSpec.from_file(
            _write_spec(
                tmp_path,
                '<sections><section module="module_200_b">'
                "<name><de>S</de><en>S</en></name>"
                "<topics><topic>intro</topic></topics></section></sections>",
            )
        )

        resolution = resolve_spec_decks(spec, slides_dir)

        assert [d.name for d in resolution.deck_files] == ["slides_b.py"]
        assert resolution.topics[0].resolved_module == "module_200_b"

    def test_prebuilt_topic_map_is_reused(self, tmp_path, slides_dir):
        from clm.core.topic_resolver import build_topic_map

        _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        spec = CourseSpec.from_file(
            _write_spec(
                tmp_path,
                "<sections><section><name><de>S</de><en>S</en></name>"
                "<topics><topic>intro</topic></topics></section></sections>",
            )
        )
        topic_map = build_topic_map(slides_dir)

        resolution = resolve_spec_decks(spec, slides_dir, topic_map=topic_map)

        assert [d.name for d in resolution.deck_files] == ["slides_intro.py"]


class TestFindDeckReferences:
    def test_reverse_lookup_finds_referencing_spec(self, tmp_path, slides_dir):
        topic_dir = _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        deck = topic_dir / "slides_intro.py"
        spec_file = _write_spec(
            tmp_path,
            "<sections><section><name><de>S</de><en>S</en></name>"
            "<topics><topic>intro</topic></topics></section></sections>",
        )

        refs = find_deck_references(deck, [spec_file], slides_dir)

        assert len(refs) == 1
        assert refs[0].topic_id == "intro"
        assert refs[0].spec_path == spec_file

    def test_unreferenced_deck_returns_empty(self, tmp_path, slides_dir):
        topic_dir = _topic(tmp_path, "module_100_basics", "topic_010_orphan", "slides_orphan.py")
        deck = topic_dir / "slides_orphan.py"
        spec_file = _write_spec(
            tmp_path,
            "<sections><section><name><de>S</de><en>S</en></name>"
            "<topics></topics></section></sections>",
        )

        refs = find_deck_references(deck, [spec_file], slides_dir)

        assert refs == []
