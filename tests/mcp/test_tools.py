"""Tests for MCP tool handlers.

Tests call the async handler functions directly (not via MCP protocol).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.mcp.tools import (
    handle_course_outline,
    handle_extract_voiceover,
    handle_get_language_view,
    handle_inline_voiceover,
    handle_normalize_slides,
    handle_resolve_topic,
    handle_search_slides,
    handle_suggest_sync,
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
