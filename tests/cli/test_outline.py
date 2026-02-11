"""
Unit tests for the outline command.

Tests the outline command functionality including:
- Output to stdout
- Output to file
- Output to directory
- Language selection
- Filename generation with language suffixes
- Error handling
"""

from pathlib import Path

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
        result = runner.invoke(cli, ["outline", "--help"])
        assert result.exit_code == 0
        assert "Generate a Markdown outline" in result.output
        assert "--output" in result.output
        assert "--output-dir" in result.output
        assert "--language" in result.output

    def test_outline_appears_in_main_help(self):
        """Test that outline command appears in main CLI help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "outline" in result.output


class TestOutlineCommandArgumentValidation:
    """Test argument parsing and validation."""

    def test_outline_requires_spec_file(self):
        """Test that outline command requires spec-file argument."""
        runner = CliRunner()
        result = runner.invoke(cli, ["outline"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "Error" in result.output

    def test_outline_rejects_nonexistent_spec_file(self):
        """Test that outline command rejects non-existent spec files."""
        runner = CliRunner()
        result = runner.invoke(cli, ["outline", "/nonexistent/spec.xml"])
        assert result.exit_code != 0
        assert "does not exist" in result.output.lower() or "error" in result.output.lower()

    def test_outline_rejects_output_and_output_dir_together(self):
        """Test that --output and --output-dir are mutually exclusive."""
        runner = CliRunner()
        # Use a real spec file to get past the file existence check
        spec_file = "tests/test-data/course-specs/test-spec-1.xml"
        result = runner.invoke(
            cli,
            ["outline", spec_file, "-o", "out.md", "-d", "outdir"],
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
        result = runner.invoke(cli, ["outline", str(test_spec_path)])
        assert result.exit_code == 0
        assert "# My Course" in result.output
        assert "## Week 1" in result.output
        assert "- Some Topic from Test 1" in result.output

    def test_outline_preserves_punctuation_in_titles(self, test_spec_path):
        """Test that punctuation in notebook titles is preserved."""
        runner = CliRunner()
        result = runner.invoke(cli, ["outline", str(test_spec_path)])
        assert result.exit_code == 0
        # The test spec includes a topic with a question mark in the title
        assert "- Was this really ML?" in result.output

    def test_outline_preserves_punctuation_german(self, test_spec_path):
        """Test that punctuation in German titles is preserved."""
        runner = CliRunner()
        result = runner.invoke(cli, ["outline", str(test_spec_path), "-L", "de"])
        assert result.exit_code == 0
        assert "- War das wirklich ML?" in result.output

    def test_outline_stdout_german(self, test_spec_path):
        """Test outline outputs German when -L de specified."""
        runner = CliRunner()
        result = runner.invoke(cli, ["outline", str(test_spec_path), "-L", "de"])
        assert result.exit_code == 0
        assert "# Mein Kurs" in result.output
        assert "## Woche 1" in result.output

    def test_outline_to_file(self, test_spec_path, tmp_path):
        """Test outline writes to file with -o option."""
        output_file = tmp_path / "outline.md"
        runner = CliRunner()
        result = runner.invoke(cli, ["outline", str(test_spec_path), "-o", str(output_file)])
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
            cli, ["outline", str(test_spec_path), "-o", str(output_file), "-L", "de"]
        )
        assert result.exit_code == 0
        content = output_file.read_text()
        assert "# Mein Kurs" in content

    def test_outline_to_directory_both_languages(self, test_spec_path, tmp_path):
        """Test outline writes both languages to directory."""
        runner = CliRunner()
        result = runner.invoke(cli, ["outline", str(test_spec_path), "-d", str(tmp_path)])
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
            cli, ["outline", str(test_spec_path), "-d", str(tmp_path), "-L", "en"]
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
            cli, ["outline", str(test_spec_same_titles_path), "-d", str(tmp_path)]
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
        result = runner.invoke(cli, ["outline", str(test_spec_path), "-d", str(output_dir)])
        assert result.exit_code == 0
        assert output_dir.exists()

    def test_outline_creates_output_file_directory(self, test_spec_path, tmp_path):
        """Test outline creates parent directories for output file."""
        output_file = tmp_path / "nested" / "dir" / "outline.md"
        runner = CliRunner()
        result = runner.invoke(cli, ["outline", str(test_spec_path), "-o", str(output_file)])
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
        result = runner.invoke(cli, ["outline", str(spec_file)])
        assert result.exit_code != 0
        assert "Error" in result.output or "error" in result.output.lower()

    def test_outline_invalid_spec_structure(self, tmp_path):
        """Test outline handles invalid spec structure gracefully."""
        spec_file = tmp_path / "bad-spec.xml"
        spec_file.write_text(
            '<?xml version="1.0"?>\n<course>\n  <invalid-element>test</invalid-element>\n</course>'
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["outline", str(spec_file)])
        assert result.exit_code != 0
