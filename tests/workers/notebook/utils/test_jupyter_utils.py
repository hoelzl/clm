"""Tests for jupyter_utils module.

This module tests all Jupyter notebook utility functions including:
- Cell type detection
- Tag operations
- Language filtering
- Cell classification
- Slide tag extraction
- File name sanitization
- Notebook title extraction
"""

import logging

import pytest

from clx.workers.notebook.utils.jupyter_utils import (
    TITLE_LINE1_REGEX,
    TITLE_LINE2_REGEX,
    TITLE_REGEX,
    find_notebook_titles,
    get_cell_language,
    get_cell_type,
    get_slide_tag,
    get_tags,
    has_tag,
    is_alternate_solution,
    is_answer_cell,
    is_cell_included_for_language,
    is_code_cell,
    is_deleted_cell,
    is_markdown_cell,
    is_private_cell,
    is_public_cell,
    is_starting_cell,
    sanitize_file_name,
    set_tags,
    warn_on_invalid_code_tags,
    warn_on_invalid_markdown_tags,
)


# Fixtures for creating mock cells
@pytest.fixture
def make_cell():
    """Factory fixture to create mock notebook cells."""

    def _make_cell(
        cell_type: str = "code", tags: list[str] | None = None, source: str = "", lang: str = ""
    ):
        metadata = {}
        if tags is not None:
            metadata["tags"] = tags
        if lang:
            metadata["lang"] = lang
        return {
            "cell_type": cell_type,
            "source": source,
            "metadata": metadata,
        }

    return _make_cell


class TestGetCellType:
    """Test the get_cell_type function."""

    def test_get_code_cell_type(self, make_cell):
        """Should return 'code' for code cells."""
        cell = make_cell("code")
        assert get_cell_type(cell) == "code"

    def test_get_markdown_cell_type(self, make_cell):
        """Should return 'markdown' for markdown cells."""
        cell = make_cell("markdown")
        assert get_cell_type(cell) == "markdown"

    def test_get_raw_cell_type(self, make_cell):
        """Should return 'raw' for raw cells."""
        cell = make_cell("raw")
        assert get_cell_type(cell) == "raw"


class TestIsCodeCell:
    """Test the is_code_cell function."""

    def test_code_cell_returns_true(self, make_cell):
        """Code cells should return True."""
        cell = make_cell("code")
        assert is_code_cell(cell) is True

    def test_markdown_cell_returns_false(self, make_cell):
        """Markdown cells should return False."""
        cell = make_cell("markdown")
        assert is_code_cell(cell) is False

    def test_raw_cell_returns_false(self, make_cell):
        """Raw cells should return False."""
        cell = make_cell("raw")
        assert is_code_cell(cell) is False


class TestIsMarkdownCell:
    """Test the is_markdown_cell function."""

    def test_markdown_cell_returns_true(self, make_cell):
        """Markdown cells should return True."""
        cell = make_cell("markdown")
        assert is_markdown_cell(cell) is True

    def test_code_cell_returns_false(self, make_cell):
        """Code cells should return False."""
        cell = make_cell("code")
        assert is_markdown_cell(cell) is False

    def test_raw_cell_returns_false(self, make_cell):
        """Raw cells should return False."""
        cell = make_cell("raw")
        assert is_markdown_cell(cell) is False


class TestGetTags:
    """Test the get_tags function."""

    def test_get_tags_returns_tags_list(self, make_cell):
        """Should return the tags list from metadata."""
        cell = make_cell("code", ["tag1", "tag2"])
        tags = get_tags(cell)
        assert tags == ["tag1", "tag2"]

    def test_get_tags_returns_empty_list_when_no_tags(self, make_cell):
        """Should return empty list when no tags key exists."""
        cell = make_cell("code")  # No tags specified
        tags = get_tags(cell)
        assert tags == []

    def test_get_tags_returns_empty_list_for_empty_tags(self, make_cell):
        """Should return empty list when tags is empty."""
        cell = make_cell("code", [])
        tags = get_tags(cell)
        assert tags == []

    def test_get_tags_preserves_order(self, make_cell):
        """Tags should be returned in order."""
        cell = make_cell("code", ["first", "second", "third"])
        tags = get_tags(cell)
        assert tags == ["first", "second", "third"]


class TestSetTags:
    """Test the set_tags function."""

    def test_set_tags_adds_tags(self, make_cell):
        """Should add tags to cell metadata."""
        cell = make_cell("code")
        set_tags(cell, ["new_tag"])
        assert cell["metadata"]["tags"] == ["new_tag"]

    def test_set_tags_replaces_existing_tags(self, make_cell):
        """Should replace existing tags."""
        cell = make_cell("code", ["old_tag"])
        set_tags(cell, ["new_tag"])
        assert cell["metadata"]["tags"] == ["new_tag"]

    def test_set_tags_removes_tags_when_empty(self, make_cell):
        """Should remove tags key when setting empty list."""
        cell = make_cell("code", ["existing_tag"])
        set_tags(cell, [])
        assert "tags" not in cell["metadata"]

    def test_set_tags_handles_none_gracefully(self, make_cell):
        """Should remove tags when passed empty list (None-like behavior)."""
        cell = make_cell("code", ["tag"])
        set_tags(cell, [])
        assert "tags" not in cell["metadata"]


class TestHasTag:
    """Test the has_tag function."""

    def test_has_tag_returns_true_when_present(self, make_cell):
        """Should return True when tag is present."""
        cell = make_cell("code", ["target", "other"])
        assert has_tag(cell, "target") is True

    def test_has_tag_returns_false_when_absent(self, make_cell):
        """Should return False when tag is not present."""
        cell = make_cell("code", ["other"])
        assert has_tag(cell, "target") is False

    def test_has_tag_returns_false_when_no_tags(self, make_cell):
        """Should return False when no tags exist."""
        cell = make_cell("code")
        assert has_tag(cell, "target") is False

    def test_has_tag_is_case_sensitive(self, make_cell):
        """Tag matching should be case-sensitive."""
        cell = make_cell("code", ["Tag"])
        assert has_tag(cell, "tag") is False
        assert has_tag(cell, "Tag") is True


class TestGetCellLanguage:
    """Test the get_cell_language function."""

    def test_get_cell_language_returns_language(self, make_cell):
        """Should return language from metadata."""
        cell = make_cell("code", lang="de")
        assert get_cell_language(cell) == "de"

    def test_get_cell_language_returns_empty_when_missing(self, make_cell):
        """Should return empty string when no language set."""
        cell = make_cell("code")
        assert get_cell_language(cell) == ""


class TestIsCellIncludedForLanguage:
    """Test the is_cell_included_for_language function."""

    def test_cell_without_language_included_for_all(self, make_cell):
        """Cells without language should be included for all languages."""
        cell = make_cell("code")
        assert is_cell_included_for_language(cell, "en") is True
        assert is_cell_included_for_language(cell, "de") is True
        assert is_cell_included_for_language(cell, "fr") is True

    def test_cell_with_matching_language_included(self, make_cell):
        """Cells with matching language should be included."""
        cell = make_cell("code", lang="en")
        assert is_cell_included_for_language(cell, "en") is True

    def test_cell_with_different_language_excluded(self, make_cell):
        """Cells with different language should be excluded."""
        cell = make_cell("code", lang="de")
        assert is_cell_included_for_language(cell, "en") is False

    def test_cell_with_empty_language_included_for_all(self, make_cell):
        """Cells with empty language string should be included for all."""
        cell = make_cell("code")
        cell["metadata"]["lang"] = ""
        assert is_cell_included_for_language(cell, "en") is True


class TestIsDeletedCell:
    """Test the is_deleted_cell function."""

    def test_cell_with_del_tag_is_deleted(self, make_cell):
        """Cell with 'del' tag should be marked as deleted."""
        cell = make_cell("code", ["del"])
        assert is_deleted_cell(cell) is True

    def test_cell_without_del_tag_not_deleted(self, make_cell):
        """Cell without 'del' tag should not be marked as deleted."""
        cell = make_cell("code", ["other"])
        assert is_deleted_cell(cell) is False

    def test_cell_without_tags_not_deleted(self, make_cell):
        """Cell without any tags should not be marked as deleted."""
        cell = make_cell("code")
        assert is_deleted_cell(cell) is False


class TestIsPrivateCell:
    """Test the is_private_cell function."""

    def test_cell_with_notes_tag_is_private(self, make_cell):
        """Cell with 'notes' tag should be private."""
        cell = make_cell("markdown", ["notes"])
        assert is_private_cell(cell) is True

    def test_cell_with_private_tag_is_private(self, make_cell):
        """Cell with 'private' tag should be private."""
        cell = make_cell("code", ["private"])
        assert is_private_cell(cell) is True

    def test_cell_without_private_tags_not_private(self, make_cell):
        """Cell without private tags should not be private."""
        cell = make_cell("code", ["other"])
        assert is_private_cell(cell) is False

    def test_cell_without_tags_not_private(self, make_cell):
        """Cell without any tags should not be private."""
        cell = make_cell("code")
        assert is_private_cell(cell) is False


class TestIsPublicCell:
    """Test the is_public_cell function."""

    def test_cell_without_private_tags_is_public(self, make_cell):
        """Cell without private tags should be public."""
        cell = make_cell("code", ["keep"])
        assert is_public_cell(cell) is True

    def test_cell_with_notes_tag_not_public(self, make_cell):
        """Cell with 'notes' tag should not be public."""
        cell = make_cell("markdown", ["notes"])
        assert is_public_cell(cell) is False

    def test_cell_with_private_tag_not_public(self, make_cell):
        """Cell with 'private' tag should not be public."""
        cell = make_cell("code", ["private"])
        assert is_public_cell(cell) is False

    def test_public_is_opposite_of_private(self, make_cell):
        """is_public_cell should return opposite of is_private_cell."""
        for tags in [[], ["keep"], ["notes"], ["private"], ["notes", "private"]]:
            cell = make_cell("code", tags)
            assert is_public_cell(cell) == (not is_private_cell(cell))


class TestIsStartingCell:
    """Test the is_starting_cell function."""

    def test_cell_with_start_tag_is_starting(self, make_cell):
        """Cell with 'start' tag should be a starting cell."""
        cell = make_cell("code", ["start"])
        assert is_starting_cell(cell) is True

    def test_cell_without_start_tag_not_starting(self, make_cell):
        """Cell without 'start' tag should not be a starting cell."""
        cell = make_cell("code", ["other"])
        assert is_starting_cell(cell) is False

    def test_cell_without_tags_not_starting(self, make_cell):
        """Cell without any tags should not be a starting cell."""
        cell = make_cell("code")
        assert is_starting_cell(cell) is False


class TestIsAlternateSolution:
    """Test the is_alternate_solution function."""

    def test_cell_with_alt_tag_is_alternate(self, make_cell):
        """Cell with 'alt' tag should be an alternate solution."""
        cell = make_cell("code", ["alt"])
        assert is_alternate_solution(cell) is True

    def test_cell_without_alt_tag_not_alternate(self, make_cell):
        """Cell without 'alt' tag should not be an alternate solution."""
        cell = make_cell("code", ["other"])
        assert is_alternate_solution(cell) is False


class TestIsAnswerCell:
    """Test the is_answer_cell function."""

    def test_code_cell_without_keep_is_answer(self, make_cell):
        """Code cell without 'keep' tag should be an answer cell."""
        cell = make_cell("code", [])
        assert is_answer_cell(cell) is True

    def test_code_cell_with_keep_not_answer(self, make_cell):
        """Code cell with 'keep' tag should not be an answer cell."""
        cell = make_cell("code", ["keep"])
        assert is_answer_cell(cell) is False

    def test_code_cell_with_start_not_answer(self, make_cell):
        """Code cell with 'start' tag should not be an answer cell."""
        cell = make_cell("code", ["start"])
        assert is_answer_cell(cell) is False

    def test_markdown_cell_with_answer_tag_is_answer(self, make_cell):
        """Markdown cell with 'answer' tag should be an answer cell."""
        cell = make_cell("markdown", ["answer"])
        assert is_answer_cell(cell) is True

    def test_markdown_cell_without_answer_tag_not_answer(self, make_cell):
        """Markdown cell without 'answer' tag should not be an answer cell."""
        cell = make_cell("markdown", [])
        assert is_answer_cell(cell) is False


class TestGetSlideTag:
    """Test the get_slide_tag function."""

    def test_cell_with_slide_tag(self, make_cell):
        """Should return 'slide' when slide tag is present."""
        cell = make_cell("code", ["slide"])
        assert get_slide_tag(cell) == "slide"

    def test_cell_with_subslide_tag(self, make_cell):
        """Should return 'subslide' when subslide tag is present."""
        cell = make_cell("code", ["subslide"])
        assert get_slide_tag(cell) == "subslide"

    def test_cell_with_notes_slide_tag(self, make_cell):
        """Should return 'notes' when notes tag is present."""
        cell = make_cell("markdown", ["notes"])
        assert get_slide_tag(cell) == "notes"

    def test_cell_without_slide_tag(self, make_cell):
        """Should return None when no slide tag is present."""
        cell = make_cell("code", ["other"])
        assert get_slide_tag(cell) is None

    def test_cell_without_any_tags(self, make_cell):
        """Should return None when no tags are present."""
        cell = make_cell("code")
        assert get_slide_tag(cell) is None

    def test_cell_with_multiple_slide_tags_warns(self, make_cell, caplog):
        """Should warn and return one tag when multiple slide tags present."""
        cell = make_cell("code", ["slide", "subslide"])
        with caplog.at_level(logging.WARNING):
            result = get_slide_tag(cell)
        assert result in ("slide", "subslide")
        assert "more than one slide tag" in caplog.text


class TestWarnOnInvalidTags:
    """Test tag validation warning functions."""

    def test_warn_on_invalid_code_tags_warns(self, caplog):
        """Should warn for invalid code cell tags."""
        with caplog.at_level(logging.WARNING):
            warn_on_invalid_code_tags(["invalid_tag"])
        assert "Unknown tag for code cell" in caplog.text
        assert "invalid_tag" in caplog.text

    def test_warn_on_invalid_code_tags_accepts_valid_tags(self, caplog):
        """Should not warn for valid code cell tags."""
        valid_tags = ["keep", "start", "del", "slide", "subslide", "notes", "private", "alt"]
        with caplog.at_level(logging.WARNING):
            warn_on_invalid_code_tags(valid_tags)
        assert "Unknown tag" not in caplog.text

    def test_warn_on_invalid_markdown_tags_warns(self, caplog):
        """Should warn for invalid markdown cell tags."""
        with caplog.at_level(logging.WARNING):
            warn_on_invalid_markdown_tags(["invalid_tag"])
        assert "Unknown tag for markdown cell" in caplog.text
        assert "invalid_tag" in caplog.text

    def test_warn_on_invalid_markdown_tags_accepts_valid_tags(self, caplog):
        """Should not warn for valid markdown cell tags."""
        valid_tags = ["notes", "answer", "del", "slide", "subslide", "private", "alt"]
        with caplog.at_level(logging.WARNING):
            warn_on_invalid_markdown_tags(valid_tags)
        assert "Unknown tag" not in caplog.text


class TestSanitizeFileName:
    """Test the sanitize_file_name function."""

    def test_sanitize_removes_leading_trailing_whitespace(self):
        """Should strip leading and trailing whitespace."""
        assert sanitize_file_name("  hello  ") == "hello"

    def test_sanitize_replaces_slashes(self):
        """Should replace slashes with underscores."""
        assert sanitize_file_name("path/to/file") == "path_to_file"
        assert sanitize_file_name("path\\to\\file") == "path_to_file"

    def test_sanitize_replaces_special_chars(self):
        """Should replace special characters."""
        assert sanitize_file_name("file$name") == "file_name"
        assert sanitize_file_name("file#name") == "file_name"
        assert sanitize_file_name("file%name") == "file_name"
        assert sanitize_file_name("file&name") == "file_name"
        assert sanitize_file_name("file<name") == "file_name"
        assert sanitize_file_name("file>name") == "file_name"
        assert sanitize_file_name("file*name") == "file_name"
        assert sanitize_file_name("file=name") == "file_name"
        assert sanitize_file_name("file^name") == "file_name"
        assert sanitize_file_name("file€name") == "file_name"
        assert sanitize_file_name("file|name") == "file_name"

    def test_sanitize_deletes_punctuation(self):
        """Should delete certain punctuation characters."""
        assert sanitize_file_name("file;name") == "filename"
        assert sanitize_file_name("file!name") == "filename"
        assert sanitize_file_name('file"name') == "filename"
        assert sanitize_file_name("file'name") == "filename"
        assert sanitize_file_name("file`name") == "filename"
        assert sanitize_file_name("file.name") == "filename"
        assert sanitize_file_name("file:name") == "filename"
        assert sanitize_file_name("file?name") == "filename"

    def test_sanitize_replaces_brackets(self):
        """Should replace brackets with parentheses."""
        assert sanitize_file_name("file{name}") == "file(name)"
        assert sanitize_file_name("file[name]") == "file(name)"

    def test_sanitize_complex_input(self):
        """Should handle complex input with multiple special characters."""
        result = sanitize_file_name("  My $File: Part 1/2  ")
        assert result == "My _File Part 1_2"

    def test_sanitize_preserves_normal_text(self):
        """Should preserve normal alphanumeric text."""
        assert sanitize_file_name("normal_file_name_123") == "normal_file_name_123"


class TestTitleRegexPatterns:
    """Test the title extraction regex patterns."""

    def test_title_regex_single_line(self):
        """Should match single-line title pattern."""
        text = '{{ header("German Title", "English Title") }}'
        match = TITLE_REGEX.search(text)
        assert match is not None
        assert match.group(1) == "German Title"
        assert match.group(2) == "English Title"

    def test_title_regex_with_single_quotes(self):
        """Should match with single quotes."""
        text = "{{ header('German', 'English') }}"
        match = TITLE_REGEX.search(text)
        assert match is not None
        assert match.group(1) == "German"
        assert match.group(2) == "English"

    def test_title_regex_with_extra_spaces(self):
        """Should match with extra spaces."""
        text = '{{  header(  "German"  ,  "English"  )  }}'
        match = TITLE_REGEX.search(text)
        assert match is not None

    def test_title_line1_regex(self):
        """Should match first line of two-line pattern."""
        text = '{{ header("German",\n'
        match = TITLE_LINE1_REGEX.search(text)
        assert match is not None
        assert match.group(1) == "German"

    def test_title_line2_regex(self):
        """Should match second line of two-line pattern."""
        text = '// "English") }}'
        match = TITLE_LINE2_REGEX.match(text)
        assert match is not None
        assert match.group(1) == "English"


class TestFindNotebookTitles:
    """Test the find_notebook_titles function."""

    def test_find_titles_single_line(self):
        """Should extract titles from single-line header."""
        text = '# {{ header("Deutscher Titel", "English Title") }}'
        titles = find_notebook_titles(text, "default")
        assert titles == {"de": "Deutscher Titel", "en": "English Title"}

    def test_find_titles_with_sanitization(self):
        """Should sanitize extracted titles."""
        text = '{{ header("Titel: Part 1/2", "Title: Part 1/2") }}'
        titles = find_notebook_titles(text, "default")
        assert titles["de"] == "Titel Part 1_2"
        assert titles["en"] == "Title Part 1_2"

    def test_find_titles_multiline(self):
        """Should extract titles from multi-line header."""
        text = """# {{ header("German Title",
# "English Title") }}"""
        titles = find_notebook_titles(text, "default")
        assert titles == {"de": "German Title", "en": "English Title"}

    def test_find_titles_returns_default_when_not_found(self):
        """Should return default when no header found."""
        text = "# Regular header without template"
        titles = find_notebook_titles(text, "Default Name")
        assert titles == {"de": "Default Name", "en": "Default Name"}

    def test_find_titles_with_comment_prefix(self):
        """Should match titles with various comment prefixes."""
        text = """// {{ header("German",
// "English") }}"""
        titles = find_notebook_titles(text, "default")
        assert titles["de"] == "German"
        assert titles["en"] == "English"

    def test_find_titles_empty_text(self):
        """Should return default for empty text."""
        titles = find_notebook_titles("", "default")
        assert titles == {"de": "default", "en": "default"}


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_cell_with_all_slide_type_tags(self, make_cell):
        """Cell should be able to have slide tag among other tags."""
        cell = make_cell("code", ["slide", "keep", "alt"])
        slide_tag = get_slide_tag(cell)
        assert slide_tag == "slide"

    def test_empty_metadata_handling(self):
        """Should handle cells with minimal metadata."""
        cell = {"cell_type": "code", "source": "", "metadata": {}}
        assert get_tags(cell) == []
        assert get_cell_language(cell) == ""
        assert is_code_cell(cell) is True

    def test_multiple_private_tags(self, make_cell):
        """Cell with multiple private tags should still be private."""
        cell = make_cell("markdown", ["notes", "private"])
        assert is_private_cell(cell) is True
        assert is_public_cell(cell) is False
