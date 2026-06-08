"""Outline rendering tests for the ``<subsection>`` layer (issue #261)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.outline import generate_outline, generate_outline_json
from clm.cli.main import cli
from clm.core.course import Course
from clm.core.course_spec import CourseSpec

SPEC_PATH = Path("tests/test-data/course-specs/subsection-spec.xml")
# A plain spec with no <subsection> usage — used to assert unchanged behavior.
PLAIN_SPEC_PATH = Path("tests/test-data/course-specs/test-spec-1.xml")
# An enabled section containing one enabled and one disabled subsection.
DISABLED_SUB_SPEC_PATH = Path("tests/test-data/course-specs/subsection-disabled-spec.xml")


@pytest.fixture
def course() -> Course:
    spec = CourseSpec.from_file(SPEC_PATH)
    return Course.from_spec(spec, SPEC_PATH.parents[1], output_root=None)


@pytest.fixture
def plain_course() -> Course:
    spec = CourseSpec.from_file(PLAIN_SPEC_PATH)
    return Course.from_spec(spec, PLAIN_SPEC_PATH.parents[1], output_root=None)


class TestMarkdownSubsections:
    def test_subsections_render_as_bold_groups(self, course):
        out = generate_outline(course, "en")
        assert "## Week 1" in out
        assert "- **Monday**" in out
        assert "  - Some Topic from Test 1" in out
        assert "  - A Topic from Test 2" in out
        assert "- **Tuesday — Law**" in out
        assert "  - Was this really ML?" in out

    def test_bare_topic_rendered_before_subsection(self, course):
        out = generate_outline(course, "en")
        # Week 2 has a bare topic then a wed subsection.
        week2 = out.split("## Week 2", 1)[1]
        assert "- Another Topic from Test 1" in week2
        assert "- **Wednesday**" in week2
        assert week2.index("Another Topic from Test 1") < week2.index("**Wednesday**")

    def test_german_weekday_labels(self, course):
        out = generate_outline(course, "de")
        assert "- **Montag**" in out
        assert "- **Dienstag — Recht**" in out

    def test_plain_spec_unchanged(self, plain_course):
        """A spec without subsections renders flat bullets (no bold groups)."""
        out = generate_outline(plain_course, "en")
        assert "- Some Topic from Test 1" in out
        assert "**" not in out


class TestJsonSubsections:
    def test_json_includes_subsections(self, course):
        data = generate_outline_json(course, "en")
        week1 = data["sections"][0]
        assert "subsections" in week1
        subs = week1["subsections"]
        assert [s["weekday"] for s in subs] == ["mon", "tue"]
        assert subs[0]["label"] == "Monday"
        assert subs[0]["enabled"] is True
        assert [t["topic_id"] for t in subs[0]["topics"]] == [
            "some_topic_from_test_1",
            "a_topic_from_test_2",
        ]

    def test_json_topics_list_still_present(self, course):
        """The flat topics list is preserved alongside subsections (additive)."""
        data = generate_outline_json(course, "en")
        week1 = data["sections"][0]
        assert [t["topic_id"] for t in week1["topics"]] == [
            "some_topic_from_test_1",
            "a_topic_from_test_2",
            "punctuation_test",
        ]

    def test_json_plain_spec_has_no_subsections_key(self, plain_course):
        data = generate_outline_json(plain_course, "en")
        for section in data["sections"]:
            assert "subsections" not in section


class TestDisabledSubsectionInEnabledSection:
    """--include-disabled surfaces a disabled subsection nested in an enabled
    section (issue #261 requirement #3)."""

    def _course_and_full(self):
        spec = CourseSpec.from_file(DISABLED_SUB_SPEC_PATH)
        full = CourseSpec.from_file(DISABLED_SUB_SPEC_PATH, keep_disabled=True)
        course = Course.from_spec(spec, DISABLED_SUB_SPEC_PATH.parents[1], output_root=None)
        return course, full

    def test_default_hides_disabled_subsection(self):
        course, _full = self._course_and_full()
        out = generate_outline(course, "en")
        assert "- **Monday**" in out
        assert "Tuesday" not in out
        assert "(disabled)" not in out

    def test_include_disabled_shows_disabled_subsection(self):
        course, full = self._course_and_full()
        out = generate_outline(course, "en", full_sections=full.sections, include_disabled=True)
        assert "- **Monday**" in out
        assert "- **Tuesday** (disabled)" in out
        # Its deck title is resolved from the filesystem fallback.
        assert "Was this really ML? (disabled)" in out

    def test_include_disabled_json(self):
        course, full = self._course_and_full()
        data = generate_outline_json(
            course, "en", full_sections=full.sections, include_disabled=True
        )
        subs = data["sections"][0]["subsections"]
        assert [s["enabled"] for s in subs] == [True, False]
        assert subs[1]["weekday"] == "tue"

    def test_cli_include_disabled(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(DISABLED_SUB_SPEC_PATH), "--include-disabled"]
        )
        assert result.exit_code == 0, result.output
        assert "- **Tuesday** (disabled)" in result.output


class TestOutlineCliSubsections:
    def test_cli_markdown(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(SPEC_PATH)])
        assert result.exit_code == 0, result.output
        assert "- **Monday**" in result.output

    def test_cli_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(SPEC_PATH), "-f", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["sections"][0]["subsections"][0]["weekday"] == "mon"

    def test_cli_sections_only_omits_subsections(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(SPEC_PATH), "--sections-only"])
        assert result.exit_code == 0, result.output
        assert "**Monday**" not in result.output
        assert "## Week 1" in result.output
