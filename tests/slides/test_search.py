"""Tests for clm.slides.search — fuzzy search across slides."""

from __future__ import annotations

import pytest

from clm.slides.search import SearchResult, search_slides


@pytest.fixture()
def slides_dir(tmp_path):
    """Slides directory with titled slide files."""
    root = tmp_path / "slides"

    m1 = root / "module_100_basics"
    t1 = m1 / "topic_010_introduction"
    t1.mkdir(parents=True)
    (t1 / "slides_introduction.py").write_text(
        "# j2 from 'macros.j2' import header\n"
        '# {{ header("Einführung", "Introduction") }}\n'
        "# %% [markdown]\n# Content\n",
        encoding="utf-8",
    )

    t2 = m1 / "topic_020_variables"
    t2.mkdir(parents=True)
    (t2 / "slides_variables.py").write_text(
        '# j2 from \'macros.j2\' import header\n# {{ header("Variablen", "Variables") }}\n',
        encoding="utf-8",
    )

    m2 = root / "module_200_oop"
    t3 = m2 / "topic_010_classes"
    t3.mkdir(parents=True)
    (t3 / "slides_classes.py").write_text(
        '# j2 from \'macros.j2\' import header\n# {{ header("Klassen", "Classes") }}\n',
        encoding="utf-8",
    )

    t4 = m2 / "topic_020_decorators"
    t4.mkdir(parents=True)
    (t4 / "slides_decorators.py").write_text(
        '# j2 from \'macros.j2\' import header\n# {{ header("Dekoratoren", "Decorators") }}\n',
        encoding="utf-8",
    )

    return root


class TestSearchSlides:
    def test_exact_match(self, slides_dir):
        results = search_slides("decorators", slides_dir)
        assert len(results) >= 1
        assert results[0].topic_id == "decorators"
        assert results[0].score > 50

    def test_title_match_en(self, slides_dir):
        results = search_slides("Introduction", slides_dir)
        assert len(results) >= 1
        assert results[0].topic_id == "introduction"

    def test_title_match_de(self, slides_dir):
        results = search_slides("Einführung", slides_dir, language="de")
        assert len(results) >= 1
        assert results[0].topic_id == "introduction"

    def test_no_results(self, slides_dir):
        results = search_slides("qxjzwkf", slides_dir)
        assert len(results) == 0

    def test_max_results(self, slides_dir):
        results = search_slides("a", slides_dir, max_results=2)
        assert len(results) <= 2

    def test_slide_info_populated(self, slides_dir):
        results = search_slides("decorators", slides_dir)
        assert len(results) >= 1
        r = results[0]
        assert len(r.slides) >= 1
        assert r.slides[0].file == "slides_decorators.py"
        assert r.slides[0].title_en == "Decorators"
        assert r.slides[0].title_de == "Dekoratoren"

    def test_empty_slides_dir(self, tmp_path):
        empty = tmp_path / "slides"
        empty.mkdir()
        results = search_slides("anything", empty)
        assert results == []
