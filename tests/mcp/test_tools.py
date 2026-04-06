"""Tests for MCP tool handlers.

Tests call the async handler functions directly (not via MCP protocol).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.mcp.tools import (
    handle_course_outline,
    handle_resolve_topic,
    handle_search_slides,
)


@pytest.fixture()
def course_tree(tmp_path):
    """Create a minimal course tree with slides/ and course-specs/."""
    slides = tmp_path / "slides"

    # Module 1: basics
    m1 = slides / "module_100_basics"
    t1 = m1 / "topic_010_intro"
    t1.mkdir(parents=True)
    (t1 / "slides_intro.py").write_text(
        "# %% [markdown]\n# # Introduction\n# ## Einführung\n",
        encoding="utf-8",
    )

    t2 = m1 / "topic_020_variables"
    t2.mkdir(parents=True)
    (t2 / "slides_variables.py").write_text(
        "# %% [markdown]\n# # Variables\n# ## Variablen\n",
        encoding="utf-8",
    )

    # Module 2: advanced
    m2 = slides / "module_200_advanced"
    t3 = m2 / "topic_010_decorators"
    t3.mkdir(parents=True)
    (t3 / "slides_decorators.py").write_text(
        "# %% [markdown]\n# # Decorators\n# ## Dekoratoren\n",
        encoding="utf-8",
    )

    return tmp_path


# ---------------------------------------------------------------------------
# resolve_topic
# ---------------------------------------------------------------------------


class TestHandleResolveTopic:
    async def test_exact_match(self, course_tree):
        result = await handle_resolve_topic("intro", course_tree)
        data = json.loads(result)
        assert data["topic_id"] == "intro"
        assert data["path"] is not None
        assert "topic_010_intro" in data["path"]

    async def test_not_found(self, course_tree):
        result = await handle_resolve_topic("nonexistent", course_tree)
        data = json.loads(result)
        assert data["path"] is None

    async def test_glob_pattern(self, course_tree):
        result = await handle_resolve_topic("*", course_tree)
        data = json.loads(result)
        assert data["glob"] is True
        topic_ids = {m["topic_id"] for m in data["matches"]}
        assert "intro" in topic_ids
        assert "variables" in topic_ids
        assert "decorators" in topic_ids

    async def test_slide_files_included(self, course_tree):
        result = await handle_resolve_topic("intro", course_tree)
        data = json.loads(result)
        assert any("slides_intro.py" in f for f in data["slide_files"])


# ---------------------------------------------------------------------------
# search_slides
# ---------------------------------------------------------------------------


class TestHandleSearchSlides:
    async def test_basic_search(self, course_tree):
        result = await handle_search_slides("intro", course_tree)
        data = json.loads(result)
        assert len(data["results"]) > 0
        assert data["results"][0]["topic_id"] == "intro"

    async def test_search_by_title(self, course_tree):
        result = await handle_search_slides("Decorators", course_tree)
        data = json.loads(result)
        assert any(r["topic_id"] == "decorators" for r in data["results"])

    async def test_no_results(self, course_tree):
        result = await handle_search_slides("xqzjkw", course_tree)
        data = json.loads(result)
        assert data["results"] == []

    async def test_max_results(self, course_tree):
        result = await handle_search_slides("*", course_tree, max_results=1)
        data = json.loads(result)
        assert len(data["results"]) <= 1

    async def test_language_filter(self, course_tree):
        result = await handle_search_slides("Einführung", course_tree, language="de")
        data = json.loads(result)
        assert len(data["results"]) > 0


# ---------------------------------------------------------------------------
# course_outline (requires a spec file — test with a real spec)
# ---------------------------------------------------------------------------


@pytest.fixture()
def course_with_spec(course_tree):
    """Add a minimal course spec XML to the course tree."""
    specs_dir = course_tree / "course-specs"
    specs_dir.mkdir()

    spec_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name>
        <de>Testkurs</de>
        <en>Test Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <sections>
        <section>
            <name>
                <de>Grundlagen</de>
                <en>Basics</en>
            </name>
            <topics>
                <dir-group>
                    <dir>module_100_basics</dir>
                    <topic>topic_010_intro</topic>
                    <topic>topic_020_variables</topic>
                </dir-group>
            </topics>
        </section>
    </sections>
</course>
"""
    spec_path = specs_dir / "test_course.xml"
    spec_path.write_text(spec_xml, encoding="utf-8")
    return course_tree, spec_path


class TestHandleCourseOutline:
    async def test_outline_json(self, course_with_spec):
        course_tree, spec_path = course_with_spec
        result = await handle_course_outline(str(spec_path), course_tree, language="en")
        data = json.loads(result)
        assert data["course_name"] == "Test Course"
        assert data["language"] == "en"
        assert len(data["sections"]) == 1
        assert data["sections"][0]["name"] == "Basics"

    async def test_outline_german(self, course_with_spec):
        course_tree, spec_path = course_with_spec
        result = await handle_course_outline(str(spec_path), course_tree, language="de")
        data = json.loads(result)
        assert data["course_name"] == "Testkurs"
        assert data["language"] == "de"

    async def test_outline_relative_path(self, course_with_spec):
        course_tree, spec_path = course_with_spec
        rel_path = str(spec_path.relative_to(course_tree))
        result = await handle_course_outline(rel_path, course_tree, language="en")
        data = json.loads(result)
        assert data["course_name"] == "Test Course"


# ---------------------------------------------------------------------------
# Data directory resolution
# ---------------------------------------------------------------------------


class TestDataDirResolution:
    async def test_resolve_with_explicit_dir(self, course_tree):
        """Tool works when data_dir is explicitly passed."""
        result = await handle_resolve_topic("intro", course_tree)
        data = json.loads(result)
        assert data["path"] is not None

    async def test_resolve_nonexistent_slides_dir(self, tmp_path):
        """Returns empty results when slides/ doesn't exist."""
        result = await handle_resolve_topic("anything", tmp_path)
        data = json.loads(result)
        assert data["path"] is None


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestCaching:
    async def test_course_cache_returns_same_object(self, course_with_spec):
        """Calling outline twice with same spec uses cached course."""
        from clm.mcp.tools import _course_cache, _get_cached_course

        course_tree, spec_path = course_with_spec

        _course_cache.clear()

        c1 = _get_cached_course(spec_path)
        c2 = _get_cached_course(spec_path)
        assert c1 is c2

    async def test_course_cache_invalidates_on_mtime(self, course_with_spec):
        """Cache miss when spec file is modified."""
        import time

        from clm.mcp.tools import _course_cache, _get_cached_course

        course_tree, spec_path = course_with_spec

        _course_cache.clear()

        c1 = _get_cached_course(spec_path)

        # Touch the file to change mtime
        time.sleep(0.05)
        spec_path.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")

        c2 = _get_cached_course(spec_path)
        assert c1 is not c2
