"""Tests for :mod:`clm.slides.deck_scope`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.deck_scope import (
    course_root_for_path,
    filter_decks,
)

DECKS = [
    Path("slides/module_100/topic_010/slides_bi.py"),
    Path("slides/module_100/topic_020/slides_x.de.py"),
    Path("slides/module_100/topic_020/slides_x.en.py"),
    Path("slides/module_100/_archive/topic_900/slides_old.py"),
]


class TestFilterDecks:
    def test_only_bilingual(self):
        kept = filter_decks(DECKS, only="bilingual")
        assert [p.name for p in kept] == ["slides_bi.py", "slides_old.py"]

    def test_only_split(self):
        kept = filter_decks(DECKS, only="split")
        assert [p.name for p in kept] == ["slides_x.de.py", "slides_x.en.py"]

    def test_exclude_by_component(self):
        kept = filter_decks(DECKS, exclude=["_archive"])
        assert all("_archive" not in p.parts for p in kept)
        assert len(kept) == 3

    def test_exclude_glob(self):
        kept = filter_decks(DECKS, exclude=["*.de.py"])
        assert all(not p.name.endswith(".de.py") for p in kept)

    def test_combined_only_and_exclude(self):
        kept = filter_decks(DECKS, only="bilingual", exclude=["_archive"])
        assert [p.name for p in kept] == ["slides_bi.py"]

    def test_shipping_filter(self, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("x", encoding="utf-8")
        b.write_text("y", encoding="utf-8")
        kept = filter_decks([a, b], shipping={a.resolve()})
        assert kept == [a]

    def test_unknown_only_raises(self):
        with pytest.raises(ValueError, match="Unknown --only"):
            filter_decks(DECKS, only="bogus")

    def test_no_filters_keeps_all(self):
        assert filter_decks(DECKS) == DECKS


class TestCourseRootForPath:
    def test_slides_root(self, tmp_path):
        slides = tmp_path / "slides"
        slides.mkdir()
        assert course_root_for_path(slides) == tmp_path.resolve()

    def test_under_slides(self, tmp_path):
        deep = tmp_path / "slides" / "module_100" / "topic_010"
        deep.mkdir(parents=True)
        assert course_root_for_path(deep) == tmp_path.resolve()

    def test_no_slides_ancestor(self, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        assert course_root_for_path(other) is None
