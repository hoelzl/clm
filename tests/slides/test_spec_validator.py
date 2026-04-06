"""Tests for clm.slides.spec_validator."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.slides.spec_validator import SpecFinding, SpecValidationResult, validate_spec


def _write_spec(tmp_path: Path, sections_xml: str, dir_groups_xml: str = "") -> Path:
    """Write a minimal course spec XML and return its path."""
    spec_file = tmp_path / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        dedent(f"""\
        <course>
          <name><de>Test</de><en>Test</en></name>
          <prog-lang>python</prog-lang>
          <description><de></de><en></en></description>
          <certificate><de></de><en></en></certificate>
          {sections_xml}
          {dir_groups_xml}
        </course>
        """),
        encoding="utf-8",
    )
    return spec_file


def _make_topic(tmp_path: Path, module: str, topic: str) -> Path:
    """Create a topic directory with a slide file inside slides/."""
    slides_dir = tmp_path / "slides"
    topic_dir = slides_dir / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "slides_intro.py").write_text("# %% [markdown]\n# Hello\n")
    return topic_dir


class TestValidateSpecClean:
    """Spec with no issues."""

    def test_clean_spec(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _make_topic(tmp_path, "module_100_basics", "topic_020_variables")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>Grundlagen</de><en>Basics</en></name>
              <topics>
                <topic>intro</topic>
                <topic>variables</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert result.topics_total == 2
        assert result.findings == []

    def test_result_properties(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )
        result = validate_spec(spec_file, tmp_path / "slides")
        assert result.errors == []
        assert result.warnings == []


class TestUnresolvedTopic:
    """Topic ID not found on the filesystem."""

    def test_unresolved_topic_error(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>intro</topic>
                <topic>nonexistent</topic>
              </topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.errors) == 1
        f = result.errors[0]
        assert f.type == "unresolved_topic"
        assert f.topic_id == "nonexistent"
        assert f.severity == "error"

    def test_near_match_suggestion(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_linear_regression")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>linar_regression</topic></topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.errors) == 1
        assert "linear_regression" in result.errors[0].suggestion

    def test_no_suggestion_for_distant_name(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>zzz_completely_different</topic></topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.errors) == 1
        assert result.errors[0].suggestion == ""


class TestAmbiguousTopic:
    """Same topic ID in multiple modules."""

    def test_ambiguous_topic_error(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_decorators")
        _make_topic(tmp_path, "module_200_advanced", "topic_010_decorators")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>decorators</topic></topics>
            </section></sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.errors) == 1
        f = result.errors[0]
        assert f.type == "ambiguous_topic"
        assert f.topic_id == "decorators"
        assert len(f.matches) == 2


class TestDuplicateTopic:
    """Same topic referenced in multiple sections."""

    def test_duplicate_topic_warning(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section>
                <name><de>S1</de><en>Section 1</en></name>
                <topics><topic>intro</topic></topics>
              </section>
              <section>
                <name><de>S2</de><en>Section 2</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.warnings) == 1
        f = result.warnings[0]
        assert f.type == "duplicate_topic"
        assert f.topic_id == "intro"
        assert f.sections == ["Section 1", "Section 2"]


class TestEmptySection:
    """Section with no topics."""

    def test_empty_section_warning(self, tmp_path):
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>Leer</de><en>Empty</en></name>
              <topics></topics>
            </section></sections>""",
        )
        (tmp_path / "slides").mkdir(parents=True, exist_ok=True)

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.warnings) == 1
        assert result.warnings[0].type == "empty_section"


class TestMissingDirGroup:
    """Dir-group path does not exist."""

    def test_missing_dir_group_warning(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
            dir_groups_xml="""\
            <dir-groups>
              <dir-group>
                <name><de>Extras</de><en>Extras</en></name>
                <path>div/nonexistent</path>
              </dir-group>
            </dir-groups>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert len(result.warnings) == 1
        f = result.warnings[0]
        assert f.type == "missing_dir_group"
        assert "nonexistent" in f.message

    def test_existing_dir_group_ok(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        (tmp_path / "div" / "extras").mkdir(parents=True)

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
            dir_groups_xml="""\
            <dir-groups>
              <dir-group>
                <name><de>Extras</de><en>Extras</en></name>
                <path>div/extras</path>
              </dir-group>
            </dir-groups>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")
        assert result.findings == []


class TestCombinedFindings:
    """Multiple issues in a single spec."""

    def test_multiple_issues(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _make_topic(tmp_path, "module_100_basics", "topic_020_lists")
        _make_topic(tmp_path, "module_200_advanced", "topic_010_lists")

        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section>
                <name><de>S1</de><en>Section 1</en></name>
                <topics>
                  <topic>intro</topic>
                  <topic>lists</topic>
                  <topic>nonexistent</topic>
                </topics>
              </section>
              <section>
                <name><de>S2</de><en>Section 2</en></name>
                <topics><topic>intro</topic></topics>
              </section>
            </sections>""",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        types = {f.type for f in result.findings}
        assert "ambiguous_topic" in types
        assert "unresolved_topic" in types
        assert "duplicate_topic" in types
        assert result.topics_total == 4
