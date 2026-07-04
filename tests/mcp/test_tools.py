"""Tests for MCP tool handlers.

Tests call the async handler functions directly (not via MCP protocol).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.mcp.tools import (
    handle_course_authoring_rules,
    handle_course_context,
    handle_course_outline,
    handle_extract_voiceover,
    handle_get_language_view,
    handle_inline_voiceover,
    handle_normalize_slides,
    handle_resolve_topic,
    handle_search_slides,
    handle_suggest_sync,
    handle_sync_report,
    handle_validate_slides,
    handle_validate_spec,
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
        '# %% [markdown]\n# {{ header("Einführung", "Introduction") }}\n',
        encoding="utf-8",
    )

    t2 = m1 / "topic_020_variables"
    t2.mkdir(parents=True)
    (t2 / "slides_variables.py").write_text(
        '# %% [markdown]\n# {{ header("Variablen", "Variables") }}\n',
        encoding="utf-8",
    )

    # Module 2: advanced
    m2 = slides / "module_200_advanced"
    t3 = m2 / "topic_010_decorators"
    t3.mkdir(parents=True)
    (t3 / "slides_decorators.py").write_text(
        '# %% [markdown]\n# {{ header("Dekoratoren", "Decorators") }}\n',
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

    async def test_outline_include_disabled_default_hides(self, course_tree):
        """By default, disabled sections are omitted from the outline."""
        specs_dir = course_tree / "course-specs"
        specs_dir.mkdir(exist_ok=True)
        spec_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Test</de><en>Test Course</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>Aktiv</de><en>Active</en></name>
            <topics><topic>intro</topic></topics>
        </section>
        <section enabled="false" id="w99">
            <name><de>Deaktiviert</de><en>Disabled Section</en></name>
            <topics><topic>not_yet_implemented</topic></topics>
        </section>
    </sections>
</course>
"""
        spec_path = specs_dir / "with_disabled.xml"
        spec_path.write_text(spec_xml, encoding="utf-8")

        result = await handle_course_outline(str(spec_path), course_tree, language="en")
        data = json.loads(result)
        assert len(data["sections"]) == 1
        assert data["sections"][0]["name"] == "Active"
        assert data["sections"][0]["disabled"] is False

    async def test_outline_include_disabled_shows_disabled(self, course_tree):
        """With include_disabled=True, disabled sections appear with a marker."""
        specs_dir = course_tree / "course-specs"
        specs_dir.mkdir(exist_ok=True)
        spec_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Test</de><en>Test Course</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>Aktiv</de><en>Active</en></name>
            <topics><topic>intro</topic></topics>
        </section>
        <section enabled="false" id="w99">
            <name><de>Deaktiviert</de><en>Disabled Section</en></name>
            <topics><topic>not_yet_implemented</topic></topics>
        </section>
    </sections>
</course>
"""
        spec_path = specs_dir / "with_disabled2.xml"
        spec_path.write_text(spec_xml, encoding="utf-8")

        result = await handle_course_outline(
            str(spec_path),
            course_tree,
            language="en",
            include_disabled=True,
        )
        data = json.loads(result)
        assert len(data["sections"]) == 2
        names = [s["name"] for s in data["sections"]]
        assert "Active" in names
        assert "Disabled Section" in names
        disabled = next(s for s in data["sections"] if s["disabled"])
        assert disabled["name"] == "Disabled Section"
        assert disabled["id"] == "w99"


# ---------------------------------------------------------------------------
# course_context
# ---------------------------------------------------------------------------


@pytest.fixture()
def real_spec(tmp_path):
    """The repo's test-spec-1 (absolute), with topics that actually resolve.

    data_dir is only used for the summary cache here (spec path is absolute),
    so a throwaway tmp_path is fine.
    """
    spec_path = Path("tests/test-data/course-specs/test-spec-1.xml").resolve()
    return tmp_path, spec_path


class TestHandleCourseContext:
    async def test_titles_default(self, real_spec):
        data_dir, spec_path = real_spec
        result = await handle_course_context(str(spec_path), data_dir, language="en")
        data = json.loads(result)
        assert data["level"] == "titles"
        assert data["course_name"] == "My Course"
        # titles level: no LLM, no per-slide summary/content
        slide = data["sections"][0]["topics"][0]["slides"][0]
        assert "summary" not in slide and "content" not in slide

    async def test_section_scope_through(self, real_spec):
        data_dir, spec_path = real_spec
        result = await handle_course_context(str(spec_path), data_dir, through="1")
        data = json.loads(result)
        assert [s["number"] for s in data["sections"]] == [1]

    async def test_topic_scope_upto(self, real_spec):
        data_dir, spec_path = real_spec
        full = json.loads(await handle_course_context(str(spec_path), data_dir))
        topic_ids = [t["topic_id"] for s in full["sections"] for t in s["topics"]]
        assert len(topic_ids) >= 2
        scoped = json.loads(
            await handle_course_context(str(spec_path), data_dir, upto=topic_ids[0])
        )
        kept = [t["topic_id"] for s in scoped["sections"] for t in s["topics"]]
        assert kept == [topic_ids[0]]

    async def test_full_level_has_content(self, real_spec):
        data_dir, spec_path = real_spec
        result = await handle_course_context(str(spec_path), data_dir, level="full", through="1")
        data = json.loads(result)
        assert data["level"] == "full"
        slide = data["sections"][0]["topics"][0]["slides"][0]
        assert "content" in slide and slide["content"]

    async def test_mutually_exclusive_selectors_error(self, real_spec):
        data_dir, spec_path = real_spec
        result = await handle_course_context(str(spec_path), data_dir, through="1", before="x")
        assert "error" in json.loads(result)

    async def test_unknown_level_error(self, real_spec):
        data_dir, spec_path = real_spec
        result = await handle_course_context(str(spec_path), data_dir, level="bogus")
        assert "error" in json.loads(result)

    async def test_unknown_topic_error(self, real_spec):
        data_dir, spec_path = real_spec
        result = await handle_course_context(str(spec_path), data_dir, upto="ghost")
        assert "error" in json.loads(result)

    async def test_summary_level_mocked_llm(self, real_spec):
        from unittest.mock import AsyncMock, patch

        data_dir, spec_path = real_spec
        mock = AsyncMock(return_value="AGENT SUMMARY")
        with patch("clm.infrastructure.llm.client.summarize_notebook", mock):
            result = await handle_course_context(
                str(spec_path), data_dir, level="summary", through="1", no_cache=True
            )
        data = json.loads(result)
        assert data["level"] == "summary"
        slide = data["sections"][0]["topics"][0]["slides"][0]
        assert slide["summary"] == "AGENT SUMMARY"
        assert all(c.kwargs["audience"] == "agent" for c in mock.await_args_list)


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


# ---------------------------------------------------------------------------
# validate_spec
# ---------------------------------------------------------------------------


@pytest.fixture()
def course_with_simple_spec(course_tree):
    """Course tree with a simple spec referencing existing topics."""
    specs_dir = course_tree / "course-specs"
    specs_dir.mkdir(exist_ok=True)
    spec_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Test</de><en>Test</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>Grundlagen</de><en>Basics</en></name>
            <topics>
                <topic>intro</topic>
                <topic>variables</topic>
            </topics>
        </section>
    </sections>
</course>
"""
    spec_path = specs_dir / "simple.xml"
    spec_path.write_text(spec_xml, encoding="utf-8")
    return course_tree, spec_path


class TestHandleValidateSpec:
    async def test_clean_spec(self, course_with_simple_spec):
        course_tree, spec_path = course_with_simple_spec
        result = await handle_validate_spec(str(spec_path), course_tree)
        data = json.loads(result)
        assert data["topics_total"] == 2
        assert data["findings"] == []

    async def test_unresolved_topic(self, course_tree):
        specs_dir = course_tree / "course-specs"
        specs_dir.mkdir(exist_ok=True)
        spec_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>T</de><en>T</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections><section>
        <name><de>S</de><en>S</en></name>
        <topics><topic>nonexistent</topic></topics>
    </section></sections>
</course>
"""
        spec_path = specs_dir / "bad.xml"
        spec_path.write_text(spec_xml, encoding="utf-8")

        result = await handle_validate_spec(str(spec_path), course_tree)
        data = json.loads(result)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["type"] == "unresolved_topic"

    async def test_relative_path(self, course_with_simple_spec):
        course_tree, spec_path = course_with_simple_spec
        rel_path = str(spec_path.relative_to(course_tree))
        result = await handle_validate_spec(rel_path, course_tree)
        data = json.loads(result)
        assert data["topics_total"] == 2
        assert data["findings"] == []

    async def test_include_disabled_default_skips_disabled(self, course_tree):
        """Default validate-spec drops disabled sections at parse time."""
        specs_dir = course_tree / "course-specs"
        specs_dir.mkdir(exist_ok=True)
        spec_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>T</de><en>T</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>Aktiv</de><en>Active</en></name>
            <topics><topic>intro</topic></topics>
        </section>
        <section enabled="false">
            <name><de>Aus</de><en>Off</en></name>
            <topics><topic>not_yet_implemented</topic></topics>
        </section>
    </sections>
</course>
"""
        spec_path = specs_dir / "validate_disabled.xml"
        spec_path.write_text(spec_xml, encoding="utf-8")

        result = await handle_validate_spec(str(spec_path), course_tree)
        data = json.loads(result)
        assert data["topics_total"] == 1
        assert data["findings"] == []

    async def test_include_disabled_reports_disabled_findings(self, course_tree):
        """With include_disabled=True, findings from disabled sections are
        reported with a ``(disabled)`` suffix on the message."""
        specs_dir = course_tree / "course-specs"
        specs_dir.mkdir(exist_ok=True)
        spec_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>T</de><en>T</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>Aktiv</de><en>Active</en></name>
            <topics><topic>intro</topic></topics>
        </section>
        <section enabled="false">
            <name><de>Aus</de><en>Off</en></name>
            <topics><topic>not_yet_implemented</topic></topics>
        </section>
    </sections>
</course>
"""
        spec_path = specs_dir / "validate_disabled2.xml"
        spec_path.write_text(spec_xml, encoding="utf-8")

        result = await handle_validate_spec(str(spec_path), course_tree, include_disabled=True)
        data = json.loads(result)
        assert data["topics_total"] == 2
        unresolved = [f for f in data["findings"] if f["type"] == "unresolved_topic"]
        assert len(unresolved) == 1
        assert unresolved[0]["topic_id"] == "not_yet_implemented"
        assert "(disabled)" in unresolved[0]["message"]


# ---------------------------------------------------------------------------
# validate_slides
# ---------------------------------------------------------------------------


class TestHandleValidateSlides:
    async def test_clean_file(self, course_tree):
        slides_dir = course_tree / "slides"
        slide_file = slides_dir / "module_100_basics" / "topic_010_intro" / "slides_intro.py"
        result = await handle_validate_slides(str(slide_file), course_tree)
        data = json.loads(result)
        assert data["files_checked"] == 1
        assert data["findings"] == []

    async def test_file_with_errors(self, course_tree):
        slides_dir = course_tree / "slides"
        bad_file = slides_dir / "module_100_basics" / "topic_010_intro" / "slides_bad.py"
        bad_file.write_text('# %% tags=["bogus_tag"]\nx = 1\n', encoding="utf-8")

        result = await handle_validate_slides(str(bad_file), course_tree)
        data = json.loads(result)
        assert len(data["findings"]) >= 1
        assert data["findings"][0]["category"] == "tags"

    async def test_relative_path(self, course_tree):
        slide_file = (
            course_tree / "slides" / "module_100_basics" / "topic_010_intro" / "slides_intro.py"
        )
        rel = str(slide_file.relative_to(course_tree))
        result = await handle_validate_slides(rel, course_tree)
        data = json.loads(result)
        assert data["files_checked"] == 1

    async def test_directory_validation(self, course_tree):
        topic_dir = course_tree / "slides" / "module_100_basics" / "topic_010_intro"
        result = await handle_validate_slides(str(topic_dir), course_tree)
        data = json.loads(result)
        assert data["files_checked"] >= 1

    async def test_with_review_checks(self, course_tree):
        slide_file = (
            course_tree / "slides" / "module_100_basics" / "topic_010_intro" / "slides_intro.py"
        )
        result = await handle_validate_slides(str(slide_file), course_tree, checks=["code_quality"])
        data = json.loads(result)
        assert data["files_checked"] == 1
        # No deterministic findings expected
        assert data["findings"] == []

    async def test_voiceover_opt_in_default_excludes_gaps(self, course_tree):
        # Issue #176: voiceover coverage is opt-in, so the default MCP path
        # (checks=None) must not surface voiceover gaps even for a deck that
        # has obvious ones.
        topic = course_tree / "slides" / "module_100_basics" / "topic_010_intro"
        gappy = topic / "slides_no_vo.py"
        gappy.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n',
            encoding="utf-8",
        )
        result = await handle_validate_slides(str(gappy), course_tree)
        data = json.loads(result)
        review = data.get("review_material", {})
        assert "voiceover_gaps" not in review

    async def test_voiceover_opt_in_explicit_runs(self, course_tree):
        topic = course_tree / "slides" / "module_100_basics" / "topic_010_intro"
        gappy = topic / "slides_no_vo.py"
        gappy.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n',
            encoding="utf-8",
        )
        result = await handle_validate_slides(str(gappy), course_tree, checks=["voiceover"])
        data = json.loads(result)
        gaps = data["review_material"]["voiceover_gaps"]
        assert len(gaps) > 0


# ---------------------------------------------------------------------------
# normalize_slides
# ---------------------------------------------------------------------------


class TestHandleNormalizeSlides:
    async def test_tag_migration(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_needs_norm.py"
        slide.write_text(
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n',
            encoding="utf-8",
        )
        result = await handle_normalize_slides(
            str(slide), course_tree, operations=["tag_migration"]
        )
        data = json.loads(result)
        assert data["status"] == "applied"
        assert len(data["changes"]) == 1
        assert data["changes"][0]["operation"] == "tag_migration"

    async def test_dry_run(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_needs_norm.py"
        text = '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n'
        slide.write_text(text, encoding="utf-8")
        result = await handle_normalize_slides(str(slide), course_tree, dry_run=True)
        data = json.loads(result)
        assert data["files_modified"] == 0
        assert len(data["changes"]) >= 1
        # File should not be modified
        assert slide.read_text(encoding="utf-8") == text

    async def test_relative_path(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_needs_norm.py"
        slide.write_text(
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n',
            encoding="utf-8",
        )
        rel = str(slide.relative_to(course_tree))
        result = await handle_normalize_slides(rel, course_tree)
        data = json.loads(result)
        assert data["status"] == "applied"

    async def test_clean_file(self, course_tree):
        slide = course_tree / "slides" / "module_100_basics" / "topic_010_intro" / "slides_intro.py"
        result = await handle_normalize_slides(str(slide), course_tree)
        data = json.loads(result)
        assert data["status"] == "clean"


# ---------------------------------------------------------------------------
# get_language_view
# ---------------------------------------------------------------------------


class TestHandleGetLanguageView:
    async def test_de_view(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_bilingual.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Hallo\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Hello\n\n'
            '# %% tags=["keep"]\nx = 1\n',
            encoding="utf-8",
        )
        result = await handle_get_language_view(str(slide), course_tree, language="de")
        assert "Hallo" in result
        assert "Hello" not in result
        assert "x = 1" in result

    async def test_relative_path(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_bilingual.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Hallo\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Hello\n',
            encoding="utf-8",
        )
        rel = str(slide.relative_to(course_tree))
        result = await handle_get_language_view(rel, course_tree, language="en")
        assert "Hello" in result
        assert "Hallo" not in result

    async def test_line_annotations(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_bilingual.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Hallo\n\n# %% tags=["keep"]\nx = 1\n',
            encoding="utf-8",
        )
        result = await handle_get_language_view(str(slide), course_tree, language="de")
        assert "# [original line" in result

    async def test_voiceover_exclusion(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_vo.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO text.\n',
            encoding="utf-8",
        )
        result = await handle_get_language_view(str(slide), course_tree, language="de")
        assert "VO text" not in result

        result_with = await handle_get_language_view(
            str(slide), course_tree, language="de", include_voiceover=True
        )
        assert "VO text" in result_with


class TestSuggestSync:
    async def test_returns_json_with_sync_fields(self, course_tree):
        """Handler returns valid JSON with expected fields."""
        import subprocess

        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_sync.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Hallo\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Hello\n',
            encoding="utf-8",
        )

        # Init git repo and commit
        subprocess.run(["git", "init"], cwd=str(course_tree), capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=str(course_tree), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )

        result = await handle_suggest_sync(str(slide), course_tree, source_language="de")
        data = json.loads(result)
        assert "sync_needed" in data
        assert "pairing_method" in data
        assert "suggestions" in data

    async def test_relative_path_resolution(self, course_tree):
        """Relative paths are resolved against data_dir."""
        import subprocess

        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_rel.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Hallo\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Hello\n',
            encoding="utf-8",
        )

        subprocess.run(["git", "init"], cwd=str(course_tree), capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=str(course_tree), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )

        rel = str(slide.relative_to(course_tree))
        result = await handle_suggest_sync(rel, course_tree, source_language="de")
        data = json.loads(result)
        assert data["source_language"] == "de"
        assert data["target_language"] == "en"

    async def test_detects_modification(self, course_tree):
        """Handler detects when DE is modified but EN is not."""
        import subprocess

        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_mod.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Hallo\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Hello\n',
            encoding="utf-8",
        )

        subprocess.run(["git", "init"], cwd=str(course_tree), capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=str(course_tree), capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(course_tree),
            capture_output=True,
            check=True,
        )

        # Modify only DE
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Neues Hallo\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Hello\n',
            encoding="utf-8",
        )

        result = await handle_suggest_sync(str(slide), course_tree, source_language="de")
        data = json.loads(result)
        assert data["sync_needed"] is True
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["type"] == "modified"


class TestSyncReport:
    """`handle_sync_report` runs the v3 engine and returns the schema-3 pair payload.

    This is the split-pair (two-file) analogue of `slides_suggest_sync`, exposing the
    same member table `clm slides sync report --json` emits, diffed against the
    committed per-topic ledger.
    """

    _DE = (
        '# %% [markdown] lang="de" tags=["slide"] slide_id="s1"\n# Hallo\n\n'
        '# %% tags=["keep"]\nx = 1\n'
    )
    _EN = (
        '# %% [markdown] lang="en" tags=["slide"] slide_id="s1"\n# Hello\n\n'
        '# %% tags=["keep"]\nx = 1\n'
    )

    def _pair(self, tmp_path: Path) -> tuple[Path, Path]:
        de_path = tmp_path / "deck.de.py"
        en_path = tmp_path / "deck.en.py"
        de_path.write_text(self._DE, encoding="utf-8")
        en_path.write_text(self._EN, encoding="utf-8")
        return de_path, en_path

    @staticmethod
    def _record(de_path: Path, en_path: Path) -> None:
        """Bless the current pair state in the committed topic ledger."""
        from clm.slides import doc_ledger
        from clm.slides.doc_lenses import load_bundle

        bundle = load_bundle(de_path, en_path)
        path = doc_ledger.ledger_path_for(de_path)
        ledger = doc_ledger.load(path)
        doc_ledger.record_deck_snapshot(
            ledger, doc_ledger.deck_key_for(de_path), bundle.outcome.deck, provenance="record"
        )
        doc_ledger.save(ledger, path)

    async def test_recorded_pair_is_clean(self, tmp_path):
        de_path, en_path = self._pair(tmp_path)
        self._record(de_path, en_path)
        data = json.loads(await handle_sync_report(str(de_path), tmp_path))
        assert data["schema"] == 3
        assert data["engine"] == "v3"
        assert data["is_clean"] is True
        assert data["needs_model"] is False
        assert data["needs_agent"] is False
        assert data["de_path"] == str(de_path)
        assert data["en_path"] == str(en_path)

    async def test_unrecorded_pair_is_cold(self, tmp_path):
        # A deck never recorded in the ledger reports every member cold —
        # framed `verify_cold` items, never silently trusted.
        de_path, _ = self._pair(tmp_path)
        data = json.loads(await handle_sync_report(str(de_path), tmp_path))
        assert data["is_clean"] is False
        assert data["needs_agent"] is True
        assert {i["action"] for i in data["items"]} == {"verify_cold"}
        assert all(i["answers"] == ["confirm"] for i in data["items"])

    async def test_localized_edit_needs_model(self, tmp_path):
        de_path, en_path = self._pair(tmp_path)
        self._record(de_path, en_path)
        # Modify only the DE localized cell → one translate item, DE → EN.
        de_path.write_text(self._DE.replace("# Hallo", "# Neues Hallo"), encoding="utf-8")
        data = json.loads(await handle_sync_report(str(de_path), tmp_path))
        assert data["is_clean"] is False
        assert data["needs_model"] is True
        actions = {
            (i["action"], i["direction"]) for i in data["items"] if i["outcome"] != "in_sync"
        }
        assert actions == {("translate_edit", "de_to_en")}

    async def test_stem_relative_path_resolution(self, tmp_path):
        self._pair(tmp_path)
        # The bilingual STEM (no .de/.en tag), passed relative — both halves derive.
        data = json.loads(await handle_sync_report("deck.py", tmp_path))
        assert data["schema"] == 3 and "is_clean" in data

    async def test_non_split_file_errors(self, tmp_path):
        lone = tmp_path / "bilingual.py"  # no .de/.en tag, no twin on disk
        lone.write_text('# %% [markdown] lang="de" tags=["slide"]\nHallo\n', encoding="utf-8")
        data = json.loads(await handle_sync_report(str(lone), tmp_path))
        assert "error" in data


class TestExtractVoiceover:
    async def test_extract_returns_json(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_vo.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO text.\n',
            encoding="utf-8",
        )

        result = await handle_extract_voiceover(str(slide), course_tree)
        data = json.loads(result)
        assert data["cells_extracted"] == 1
        assert "companion_file" in data

    async def test_extract_relative_path(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_vo2.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO.\n',
            encoding="utf-8",
        )

        rel = str(slide.relative_to(course_tree))
        result = await handle_extract_voiceover(rel, course_tree)
        data = json.loads(result)
        assert data["cells_extracted"] == 1

    async def test_extract_dry_run(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_vo3.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO.\n',
            encoding="utf-8",
        )

        result = await handle_extract_voiceover(str(slide), course_tree, dry_run=True)
        data = json.loads(result)
        assert data["dry_run"] is True
        assert data["cells_extracted"] == 1

    @staticmethod
    def _split_pair(course_tree):
        topic = course_tree / "slides" / "module_100_basics" / "topic_010_intro"
        de = topic / "slides_pair.de.py"
        en = topic / "slides_pair.en.py"
        de.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO DE\n',
            encoding="utf-8",
        )
        en.write_text(
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Topic\n\n'
            '# %% [markdown] lang="en" tags=["voiceover"]\n# VO EN\n',
            encoding="utf-8",
        )
        return de, en

    async def test_extract_auto_pairs_split_half(self, course_tree):
        from clm.slides.voiceover_tools import resolve_companion

        de, en = self._split_pair(course_tree)
        result = await handle_extract_voiceover(str(de), course_tree)
        data = json.loads(result)
        assert data["paired"] is True
        assert len(data["companions"]) == 2
        assert resolve_companion(de) is not None
        assert resolve_companion(en) is not None

    async def test_extract_single_opts_out(self, course_tree):
        de, _en = self._split_pair(course_tree)
        result = await handle_extract_voiceover(str(de), course_tree, single=True)
        data = json.loads(result)
        assert "paired" not in data
        assert data["cells_extracted"] == 1
        assert not (de.parent / "voiceover_pair.en.py").exists()

    def test_paired_serializer_matches_cli(self, tmp_path):
        # The MCP and CLI paired serializers are a contract — keep them byte-equal.
        from clm.cli.commands.voiceover import (
            _paired_extraction_to_dict as cli_dict,
        )
        from clm.mcp.tools import _paired_extraction_result_to_dict as mcp_dict
        from clm.slides.voiceover_tools import extract_voiceover_pair

        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO DE\n',
            encoding="utf-8",
        )
        en.write_text(
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Topic\n\n'
            '# %% [markdown] lang="en" tags=["voiceover"]\n# VO EN\n',
            encoding="utf-8",
        )
        result = extract_voiceover_pair(de, en)
        assert cli_dict(result) == mcp_dict(result)


class TestInlineVoiceover:
    async def test_inline_returns_json(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_inline.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO text.\n',
            encoding="utf-8",
        )

        # Extract first
        from clm.slides.voiceover_tools import extract_voiceover

        extract_voiceover(slide)

        result = await handle_inline_voiceover(str(slide), course_tree)
        data = json.loads(result)
        assert data["cells_inlined"] == 1
        assert data["companion_deleted"] is True

    async def test_inline_no_companion(self, course_tree):
        slides_dir = course_tree / "slides"
        topic = slides_dir / "module_100_basics" / "topic_010_intro"
        slide = topic / "slides_no_comp.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n',
            encoding="utf-8",
        )

        result = await handle_inline_voiceover(str(slide), course_tree)
        data = json.loads(result)
        assert data["cells_inlined"] == 0


# ---------------------------------------------------------------------------
# course_authoring_rules
# ---------------------------------------------------------------------------


@pytest.fixture()
def course_with_authoring(course_tree):
    """Add course specs and authoring rules to the course tree."""
    specs_dir = course_tree / "course-specs"
    specs_dir.mkdir(exist_ok=True)

    (specs_dir / "_common.authoring.md").write_text(
        "## Common Rules\n\n- Format slides properly.\n",
        encoding="utf-8",
    )
    (specs_dir / "test-course.authoring.md").write_text(
        "## Test Course Rules\n\n- Keep it simple.\n",
        encoding="utf-8",
    )
    (specs_dir / "test-course.xml").write_text(
        """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Test</de><en>Test</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>S</de><en>S</en></name>
            <topics>
                <topic>intro</topic>
                <topic>variables</topic>
            </topics>
        </section>
    </sections>
</course>
""",
        encoding="utf-8",
    )
    return course_tree


class TestHandleCourseAuthoringRules:
    async def test_by_slug(self, course_with_authoring):
        result = await handle_course_authoring_rules(
            course_with_authoring, course_spec="test-course"
        )
        data = json.loads(result)
        assert data["has_common_rules"] is True
        assert len(data["course_rules"]) == 1
        assert "Common Rules" in data["merged"]
        assert "Test Course Rules" in data["merged"]

    async def test_by_slide_path(self, course_with_authoring):
        slide = (
            course_with_authoring
            / "slides"
            / "module_100_basics"
            / "topic_010_intro"
            / "slides_intro.py"
        )
        result = await handle_course_authoring_rules(course_with_authoring, slide_path=str(slide))
        data = json.loads(result)
        assert data["has_common_rules"] is True
        assert any(e["course_spec"] == "test-course" for e in data["course_rules"])

    async def test_relative_slide_path(self, course_with_authoring):
        rel = "slides/module_100_basics/topic_020_variables/slides_variables.py"
        result = await handle_course_authoring_rules(course_with_authoring, slide_path=rel)
        data = json.loads(result)
        assert any(e["course_spec"] == "test-course" for e in data["course_rules"])

    async def test_no_matching_course(self, course_with_authoring):
        result = await handle_course_authoring_rules(
            course_with_authoring, course_spec="nonexistent"
        )
        data = json.loads(result)
        assert len(data["course_rules"]) == 0
        assert "notes" in data


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
