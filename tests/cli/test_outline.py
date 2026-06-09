"""
Unit tests for the outline command.

Tests the outline command functionality including:
- Output to stdout
- Output to file
- Output to directory
- Language selection
- Filename generation with language suffixes
- Error handling
- --include-disabled flag
"""

import json
from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import CliRunner

from clm.cli.commands.outline import (
    generate_outline,
    get_output_filename,
    titles_are_identical,
)
from clm.cli.main import cli
from clm.core.course import Course
from clm.core.course_spec import CourseSpec


class TestOutlineCommandHelp:
    """Test outline command help and basic structure."""

    def test_outline_help(self):
        """Test outline command help text."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", "--help"])
        assert result.exit_code == 0
        assert "Generate an outline" in result.output
        assert "--output" in result.output
        assert "--output-dir" in result.output
        assert "--language" in result.output

    def test_outline_appears_in_main_help(self):
        """Test that the export group is in main help and outline lives under it."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "export" in result.output
        sub = runner.invoke(cli, ["export", "--help"])
        assert sub.exit_code == 0
        assert "outline" in sub.output


class TestOutlineCommandArgumentValidation:
    """Test argument parsing and validation."""

    def test_outline_requires_spec_file(self):
        """Test that outline command requires spec-file argument."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "Error" in result.output

    def test_outline_rejects_nonexistent_spec_file(self):
        """Test that outline command rejects non-existent spec files."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", "/nonexistent/spec.xml"])
        assert result.exit_code != 0
        assert "does not exist" in result.output.lower() or "error" in result.output.lower()

    def test_outline_rejects_output_and_output_dir_together(self):
        """Test that --output and --output-dir are mutually exclusive."""
        runner = CliRunner()
        # Use a real spec file to get past the file existence check
        spec_file = "tests/test-data/course-specs/test-spec-1.xml"
        result = runner.invoke(
            cli,
            ["export", "outline", spec_file, "-o", "out.md", "-d", "outdir"],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()


class TestOutlineCommandOutput:
    """Test outline command output modes with real spec files."""

    @pytest.fixture
    def test_spec_path(self):
        """Return path to test spec file with different en/de titles."""
        return Path("tests/test-data/course-specs/test-spec-1.xml")

    @pytest.fixture
    def test_spec_same_titles_path(self):
        """Return path to test spec file with identical en/de titles."""
        return Path("tests/test-data/course-specs/test-spec-2.xml")

    def test_outline_stdout_default_english(self, test_spec_path):
        """Test outline outputs English to stdout by default."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(test_spec_path)])
        assert result.exit_code == 0
        assert "# My Course" in result.output
        assert "## Week 1" in result.output
        assert "- Some Topic from Test 1" in result.output

    def test_outline_preserves_punctuation_in_titles(self, test_spec_path):
        """Test that punctuation in notebook titles is preserved."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(test_spec_path)])
        assert result.exit_code == 0
        # The test spec includes a topic with a question mark in the title
        assert "- Was this really ML?" in result.output

    def test_outline_preserves_punctuation_german(self, test_spec_path):
        """Test that punctuation in German titles is preserved."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(test_spec_path), "-L", "de"])
        assert result.exit_code == 0
        assert "- War das wirklich ML?" in result.output

    def test_outline_stdout_german(self, test_spec_path):
        """Test outline outputs German when -L de specified."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(test_spec_path), "-L", "de"])
        assert result.exit_code == 0
        assert "# Mein Kurs" in result.output
        assert "## Woche 1" in result.output

    def test_outline_to_file(self, test_spec_path, tmp_path):
        """Test outline writes to file with -o option."""
        output_file = tmp_path / "outline.md"
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(test_spec_path), "-o", str(output_file)]
        )
        assert result.exit_code == 0
        assert f"Written: {output_file}" in result.output
        assert output_file.exists()
        content = output_file.read_text()
        assert "# My Course" in content

    def test_outline_to_file_german(self, test_spec_path, tmp_path):
        """Test outline writes German to file when -L de specified."""
        output_file = tmp_path / "outline.md"
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(test_spec_path), "-o", str(output_file), "-L", "de"]
        )
        assert result.exit_code == 0
        content = output_file.read_text()
        assert "# Mein Kurs" in content

    def test_outline_to_directory_both_languages(self, test_spec_path, tmp_path):
        """Test outline writes both languages to directory."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(test_spec_path), "-d", str(tmp_path)])
        assert result.exit_code == 0

        # Check that both files were created (different titles, no suffix needed)
        files = list(tmp_path.glob("*.md"))
        assert len(files) == 2

        # Check English file
        en_file = tmp_path / "My Course.md"
        assert en_file.exists()
        assert "# My Course" in en_file.read_text()

        # Check German file
        de_file = tmp_path / "Mein Kurs.md"
        assert de_file.exists()
        assert "# Mein Kurs" in de_file.read_text()

    def test_outline_to_directory_single_language(self, test_spec_path, tmp_path):
        """Test outline writes single language to directory when -L specified."""
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(test_spec_path), "-d", str(tmp_path), "-L", "en"]
        )
        assert result.exit_code == 0

        files = list(tmp_path.glob("*.md"))
        assert len(files) == 1

        en_file = tmp_path / "My Course.md"
        assert en_file.exists()

    def test_outline_to_directory_identical_titles_adds_suffix(
        self, test_spec_same_titles_path, tmp_path
    ):
        """Test outline adds language suffix when titles are identical."""
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(test_spec_same_titles_path), "-d", str(tmp_path)]
        )
        assert result.exit_code == 0

        # Check that files have language suffixes
        en_file = tmp_path / "Kurs 2-en.md"
        de_file = tmp_path / "Kurs 2-de.md"
        assert en_file.exists(), f"Expected {en_file}, got {list(tmp_path.glob('*.md'))}"
        assert de_file.exists(), f"Expected {de_file}, got {list(tmp_path.glob('*.md'))}"

    def test_outline_creates_output_directory(self, test_spec_path, tmp_path):
        """Test outline creates output directory if it doesn't exist."""
        output_dir = tmp_path / "nested" / "output" / "dir"
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(test_spec_path), "-d", str(output_dir)]
        )
        assert result.exit_code == 0
        assert output_dir.exists()

    def test_outline_creates_output_file_directory(self, test_spec_path, tmp_path):
        """Test outline creates parent directories for output file."""
        output_file = tmp_path / "nested" / "dir" / "outline.md"
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(test_spec_path), "-o", str(output_file)]
        )
        assert result.exit_code == 0
        assert output_file.exists()


class TestOutlineHelperFunctions:
    """Test helper functions used by the outline command."""

    @pytest.fixture
    def course_different_titles(self):
        """Create a course with different en/de titles."""
        spec_path = Path("tests/test-data/course-specs/test-spec-1.xml")
        spec = CourseSpec.from_file(spec_path)
        data_dir = spec_path.parents[1]
        return Course.from_spec(spec, data_dir, output_root=None)

    @pytest.fixture
    def course_same_titles(self):
        """Create a course with identical en/de titles."""
        spec_path = Path("tests/test-data/course-specs/test-spec-2.xml")
        spec = CourseSpec.from_file(spec_path)
        data_dir = spec_path.parents[1]
        return Course.from_spec(spec, data_dir, output_root=None)

    def test_titles_are_identical_false(self, course_different_titles):
        """Test titles_are_identical returns False for different titles."""
        assert titles_are_identical(course_different_titles) is False

    def test_titles_are_identical_true(self, course_same_titles):
        """Test titles_are_identical returns True for identical titles."""
        assert titles_are_identical(course_same_titles) is True

    def test_get_output_filename_no_suffix(self, course_different_titles):
        """Test filename generation without suffix."""
        filename = get_output_filename(course_different_titles, "en", needs_suffix=False)
        assert filename == "My Course.md"

    def test_get_output_filename_with_suffix(self, course_different_titles):
        """Test filename generation with language suffix."""
        filename_en = get_output_filename(course_different_titles, "en", needs_suffix=True)
        filename_de = get_output_filename(course_different_titles, "de", needs_suffix=True)
        assert filename_en == "My Course-en.md"
        assert filename_de == "Mein Kurs-de.md"

    def test_generate_outline_structure(self, course_different_titles):
        """Test generated outline has correct structure."""
        outline = generate_outline(course_different_titles, "en")

        # Check H1 course title
        assert outline.startswith("# My Course\n")

        # Check sections are H2
        assert "## Week 1\n" in outline
        assert "## Week 2\n" in outline

        # Check topics are bullet points
        assert "\n- Some Topic from Test 1\n" in outline

    def test_generate_outline_has_blank_line_after_section_headings(self, course_different_titles):
        """Test that there is a blank line between section heading and bullet list."""
        outline = generate_outline(course_different_titles, "en")

        # Verify blank line between heading and first bullet point
        # The pattern should be: ## Section\n\n- Topic
        assert "\n## Week 1\n\n-" in outline
        assert "\n## Week 2\n\n-" in outline

    def test_generate_outline_german(self, course_different_titles):
        """Test generated outline in German."""
        outline = generate_outline(course_different_titles, "de")
        assert "# Mein Kurs" in outline
        assert "## Woche 1" in outline


class TestOutlineErrorHandling:
    """Test error handling in outline command."""

    def test_outline_invalid_xml(self, tmp_path):
        """Test outline handles invalid XML gracefully."""
        spec_file = tmp_path / "invalid.xml"
        spec_file.write_text("not valid xml <><>")

        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(spec_file)])
        assert result.exit_code != 0
        assert "Error" in result.output or "error" in result.output.lower()

    def test_outline_invalid_spec_structure(self, tmp_path):
        """Test outline handles invalid spec structure gracefully."""
        spec_file = tmp_path / "bad-spec.xml"
        spec_file.write_text(
            '<?xml version="1.0"?>\n<course>\n  <invalid-element>test</invalid-element>\n</course>'
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(spec_file)])
        assert result.exit_code != 0


class TestOutlineIncludeDisabled:
    """Tests for the --include-disabled flag on `clm outline`.

    The fixture builds a spec that declares one enabled section whose topics
    exist in the shared test-data slides directory, plus one disabled section
    that references a topic which does not exist. Without --include-disabled
    the disabled section is invisible; with the flag it appears with a
    (disabled) marker.
    """

    @pytest.fixture
    def spec_with_disabled_section(self, request):
        """Create a spec with one enabled and one disabled section under the
        shared test-data root so topic resolution finds the enabled topic.

        File name is per-test (request.node.name) so concurrent xdist workers
        do not race the same shared path.
        """
        data_dir = Path("tests/test-data")
        specs_dir = data_dir / "course-specs"
        spec_file = specs_dir / f"test-spec-with-disabled-{request.node.name}.xml"
        # Keep spec file inside tests/test-data/course-specs so
        # resolve_course_paths can locate the shared slides/ sibling.
        spec_file.write_text(
            dedent("""\
                <course>
                  <name><de>Mini-Kurs</de><en>Mini Course</en></name>
                  <prog-lang>python</prog-lang>
                  <description><de>Demo</de><en>Demo</en></description>
                  <certificate><de>.</de><en>.</en></certificate>
                  <sections>
                    <section>
                      <name>
                        <de>Woche 1 aktiv</de>
                        <en>Week 1 active</en>
                      </name>
                      <topics>
                        <topic>some_topic_from_test_1</topic>
                      </topics>
                    </section>
                    <section enabled="false" id="w99">
                      <name>
                        <de>Woche 99 Roadmap</de>
                        <en>Week 99 Roadmap</en>
                      </name>
                      <topics>
                        <topic>not_yet_implemented_topic</topic>
                      </topics>
                    </section>
                  </sections>
                </course>
                """),
            encoding="utf-8",
        )
        yield spec_file
        spec_file.unlink(missing_ok=True)

    def test_outline_default_hides_disabled(self, spec_with_disabled_section):
        """Default outline should not mention the disabled section."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(spec_with_disabled_section)])
        assert result.exit_code == 0, result.output
        assert "Week 1 active" in result.output
        assert "Week 99 Roadmap" not in result.output
        assert "(disabled)" not in result.output

    def test_outline_include_disabled_markdown_shows_marker(self, spec_with_disabled_section):
        """With --include-disabled the disabled section appears with marker."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export", "outline", str(spec_with_disabled_section), "--include-disabled"],
        )
        assert result.exit_code == 0, result.output
        assert "Week 1 active" in result.output
        assert "Week 99 Roadmap (disabled)" in result.output
        assert "- not_yet_implemented_topic (disabled)" in result.output

    def test_outline_include_disabled_json(self, spec_with_disabled_section):
        """JSON output includes disabled section with disabled=true marker."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "export",
                "outline",
                str(spec_with_disabled_section),
                "--include-disabled",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        names = [s["name"] for s in data["sections"]]
        assert "Week 1 active" in names
        assert "Week 99 Roadmap" in names
        disabled_entry = next(s for s in data["sections"] if s["disabled"])
        assert disabled_entry["name"] == "Week 99 Roadmap"
        assert disabled_entry["id"] == "w99"
        # Enabled section carries disabled=False
        enabled_entry = next(s for s in data["sections"] if not s["disabled"])
        assert enabled_entry["name"] == "Week 1 active"

    def test_outline_json_default_hides_disabled(self, spec_with_disabled_section):
        """Default JSON output should not include the disabled section."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export", "outline", str(spec_with_disabled_section), "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        names = [s["name"] for s in data["sections"]]
        assert names == ["Week 1 active"]


class TestOutlineDisabledShowsRealTitles:
    """Tests that --include-disabled emits notebook H1 titles for resolvable topics.

    Earlier the disabled-section branch hard-coded the topic id as the bullet
    label, so even when the referenced topic existed on disk the outline showed
    the directory stem (e.g. ``some_topic_from_test_1``) instead of the H1
    title (``Some Topic from Test 1``).
    """

    @pytest.fixture
    def spec_with_resolvable_disabled_section(self, request):
        """Spec whose disabled section references a topic that exists on disk."""
        data_dir = Path("tests/test-data")
        specs_dir = data_dir / "course-specs"
        spec_file = specs_dir / f"test-spec-disabled-resolvable-{request.node.name}.xml"
        spec_file.write_text(
            dedent("""\
                <course>
                  <name><de>Mini-Kurs</de><en>Mini Course</en></name>
                  <prog-lang>python</prog-lang>
                  <description><de>Demo</de><en>Demo</en></description>
                  <certificate><de>.</de><en>.</en></certificate>
                  <sections>
                    <section>
                      <name>
                        <de>Woche 1 aktiv</de>
                        <en>Week 1 active</en>
                      </name>
                      <topics>
                        <topic>a_topic_from_test_2</topic>
                      </topics>
                    </section>
                    <section enabled="false">
                      <name>
                        <de>Woche 2 abgelegt</de>
                        <en>Week 2 archived</en>
                      </name>
                      <topics>
                        <topic>some_topic_from_test_1</topic>
                        <topic>punctuation_test</topic>
                      </topics>
                    </section>
                  </sections>
                </course>
                """),
            encoding="utf-8",
        )
        yield spec_file
        spec_file.unlink(missing_ok=True)

    def test_disabled_section_emits_real_titles_markdown(
        self, spec_with_resolvable_disabled_section
    ):
        """Disabled-section bullets should show the H1 header, not the topic id."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export", "outline", str(spec_with_resolvable_disabled_section), "--include-disabled"],
        )
        assert result.exit_code == 0, result.output
        assert "## Week 2 archived (disabled)" in result.output
        # H1 title is rendered, not the topic id.
        assert "- Some Topic from Test 1 (disabled)" in result.output
        assert "- Was this really ML? (disabled)" in result.output
        # Make sure the legacy "topic id" rendering does not also appear.
        assert "- some_topic_from_test_1 (disabled)" not in result.output
        assert "- punctuation_test (disabled)" not in result.output

    def test_disabled_section_emits_real_titles_german(self, spec_with_resolvable_disabled_section):
        """German rendering pulls the de side of the header() macro."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "export",
                "outline",
                str(spec_with_resolvable_disabled_section),
                "--include-disabled",
                "-L",
                "de",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "- Folien von Test 1 (disabled)" in result.output
        assert "- War das wirklich ML? (disabled)" in result.output

    def test_disabled_section_emits_real_titles_json(self, spec_with_resolvable_disabled_section):
        """JSON disabled-section topics include populated slide titles."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "export",
                "outline",
                str(spec_with_resolvable_disabled_section),
                "--include-disabled",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        disabled_entry = next(s for s in data["sections"] if s["disabled"])
        topic_ids = [t["topic_id"] for t in disabled_entry["topics"]]
        assert topic_ids == ["some_topic_from_test_1", "punctuation_test"]
        first_topic = disabled_entry["topics"][0]
        assert first_topic["directory"] is not None
        titles = [s["title"] for s in first_topic["slides"]]
        assert "Some Topic from Test 1" in titles
        second_topic = disabled_entry["topics"][1]
        assert any(s["title"] == "Was this really ML?" for s in second_topic["slides"])


class TestOutlineSectionsOnly:
    """Tests for the --sections-only flag.

    With --sections-only the outline must only emit section headings and skip
    the per-topic bullet list (markdown) or omit the topics array (JSON).
    """

    @pytest.fixture
    def test_spec_path(self):
        return Path("tests/test-data/course-specs/test-spec-1.xml")

    def test_sections_only_markdown_omits_topic_bullets(self, test_spec_path):
        """Markdown sections-only output has no '- ' bullet lines."""
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "outline", str(test_spec_path), "--sections-only"])
        assert result.exit_code == 0, result.output
        assert "# My Course" in result.output
        assert "## Week 1" in result.output
        assert "## Week 2" in result.output
        # No topic bullets present anywhere in the output.
        for line in result.output.splitlines():
            assert not line.startswith("- "), f"Unexpected bullet line: {line!r}"
        # And in particular, no notebook titles leak in.
        assert "Some Topic from Test 1" not in result.output

    def test_sections_only_json_omits_topics_array(self, test_spec_path):
        """JSON sections-only entries do not carry a 'topics' key."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export", "outline", str(test_spec_path), "--sections-only", "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert [s["name"] for s in data["sections"]] == ["Week 1", "Week 2"]
        for section in data["sections"]:
            assert "topics" not in section
            assert section["disabled"] is False

    def test_sections_only_with_include_disabled_markdown(self, request):
        """--sections-only also suppresses topic bullets for disabled sections."""
        data_dir = Path("tests/test-data")
        specs_dir = data_dir / "course-specs"
        spec_file = specs_dir / f"test-spec-sections-only-disabled-{request.node.name}.xml"
        spec_file.write_text(
            dedent("""\
                <course>
                  <name><de>Mini-Kurs</de><en>Mini Course</en></name>
                  <prog-lang>python</prog-lang>
                  <description><de>Demo</de><en>Demo</en></description>
                  <certificate><de>.</de><en>.</en></certificate>
                  <sections>
                    <section>
                      <name><de>Woche 1</de><en>Week 1</en></name>
                      <topics>
                        <topic>a_topic_from_test_2</topic>
                      </topics>
                    </section>
                    <section enabled="false">
                      <name><de>Woche 2</de><en>Week 2 archived</en></name>
                      <topics>
                        <topic>some_topic_from_test_1</topic>
                      </topics>
                    </section>
                  </sections>
                </course>
                """),
            encoding="utf-8",
        )
        try:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "export",
                    "outline",
                    str(spec_file),
                    "--sections-only",
                    "--include-disabled",
                ],
            )
            assert result.exit_code == 0, result.output
            assert "## Week 1" in result.output
            assert "## Week 2 archived (disabled)" in result.output
            for line in result.output.splitlines():
                assert not line.startswith("- "), f"Unexpected bullet line: {line!r}"
        finally:
            spec_file.unlink(missing_ok=True)

    def test_sections_only_with_include_disabled_json(self, request):
        """JSON sections-only output keeps disabled flag but drops topics."""
        data_dir = Path("tests/test-data")
        specs_dir = data_dir / "course-specs"
        spec_file = specs_dir / f"test-spec-sections-only-disabled-json-{request.node.name}.xml"
        spec_file.write_text(
            dedent("""\
                <course>
                  <name><de>Mini-Kurs</de><en>Mini Course</en></name>
                  <prog-lang>python</prog-lang>
                  <description><de>Demo</de><en>Demo</en></description>
                  <certificate><de>.</de><en>.</en></certificate>
                  <sections>
                    <section>
                      <name><de>Woche 1</de><en>Week 1</en></name>
                      <topics>
                        <topic>a_topic_from_test_2</topic>
                      </topics>
                    </section>
                    <section enabled="false">
                      <name><de>Woche 2</de><en>Week 2 archived</en></name>
                      <topics>
                        <topic>some_topic_from_test_1</topic>
                      </topics>
                    </section>
                  </sections>
                </course>
                """),
            encoding="utf-8",
        )
        try:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "export",
                    "outline",
                    str(spec_file),
                    "--sections-only",
                    "--include-disabled",
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            disabled = [s for s in data["sections"] if s["disabled"]]
            enabled = [s for s in data["sections"] if not s["disabled"]]
            assert len(disabled) == 1
            assert len(enabled) == 1
            for section in data["sections"]:
                assert "topics" not in section
        finally:
            spec_file.unlink(missing_ok=True)


OPTIONAL_SPEC_PATH = Path("tests/test-data/course-specs/subsection-optional-spec.xml")


class TestOutlineIncludeOptional:
    """Tests for the --include-optional flag on `clm export outline`.

    The fixture spec has an optional Wednesday subsection in Week 1 and an
    optional Week 2 (whole section). By default both are hidden; the topic of a
    hidden subsection must not leak out as a bare bullet.
    """

    def test_optional_hidden_by_default_no_bare_leak(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(OPTIONAL_SPEC_PATH), "-L", "en", "--weekdays", "always"]
        )
        assert result.exit_code == 0, result.output
        assert "**Monday, Tuesday**" in result.output
        # Optional Wednesday subsection, its topic, and optional Week 2 are gone.
        assert "Wednesday" not in result.output
        assert "## Week 2" not in result.output
        assert "A Topic from Test 2" not in result.output

    def test_include_optional_shows_modules(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "export",
                "outline",
                str(OPTIONAL_SPEC_PATH),
                "-L",
                "en",
                "--include-optional",
                "--weekdays",
                "always",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "**Wednesday**" in result.output
        assert "A Topic from Test 2" in result.output
        assert "## Week 2" in result.output

    def test_optional_hidden_in_json_topics_and_subsections(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "outline", str(OPTIONAL_SPEC_PATH), "-L", "en", "-f", "json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["sections"]) == 1  # optional Week 2 omitted
        week1 = data["sections"][0]
        topic_ids = {t["topic_id"] for t in week1["topics"]}
        assert topic_ids == {"some_topic_from_test_1"}  # optional topic excluded
        labels = {ss["label"] for ss in week1.get("subsections", [])}
        assert "Wednesday" not in labels

    def test_include_optional_json_includes_modules(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "export",
                "outline",
                str(OPTIONAL_SPEC_PATH),
                "-L",
                "en",
                "-f",
                "json",
                "--include-optional",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["sections"]) == 2
        topic_ids = {t["topic_id"] for t in data["sections"][0]["topics"]}
        assert "a_topic_from_test_2" in topic_ids
