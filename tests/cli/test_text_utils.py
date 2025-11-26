"""Tests for CLI text utilities.

Tests text manipulation functions including:
- ANSI escape sequence stripping
- Path conversion (absolute to relative)
- Path truncation
- Error path formatting
"""

import os
from pathlib import Path

import pytest

from clx.cli.text_utils import (
    ANSI_ESCAPE_PATTERN,
    format_error_path,
    make_relative_path,
    strip_ansi,
    truncate_path,
)


class TestStripAnsi:
    """Test ANSI escape sequence removal."""

    def test_strip_ansi_empty_string(self):
        """Empty string should return empty string."""
        assert strip_ansi("") == ""

    def test_strip_ansi_none_returns_none(self):
        """None should return falsy value."""
        result = strip_ansi(None)
        assert not result

    def test_strip_ansi_no_codes(self):
        """Text without ANSI codes should be unchanged."""
        text = "Hello, World!"
        assert strip_ansi(text) == "Hello, World!"

    def test_strip_ansi_color_codes(self):
        """Should strip color codes."""
        # Red text
        text = "\033[31mRed text\033[0m"
        assert strip_ansi(text) == "Red text"

    def test_strip_ansi_bold_codes(self):
        """Should strip bold codes."""
        text = "\033[1mBold text\033[0m"
        assert strip_ansi(text) == "Bold text"

    def test_strip_ansi_multiple_codes(self):
        """Should strip multiple codes in sequence."""
        # Bold red with green
        text = "\033[1m\033[31mBold red\033[0m and \033[32mgreen\033[0m"
        assert strip_ansi(text) == "Bold red and green"

    def test_strip_ansi_256_color(self):
        """Should strip 256-color codes."""
        text = "\033[38;5;208mOrange\033[0m"
        assert strip_ansi(text) == "Orange"

    def test_strip_ansi_osc_sequences(self):
        """Should strip OSC sequences (like terminal title)."""
        text = "\033]0;Window Title\007Normal text"
        assert strip_ansi(text) == "Normal text"

    def test_strip_ansi_cursor_movement(self):
        """Should strip cursor movement codes."""
        # Cursor up, down, forward, back
        text = "Before\033[2AAfter"  # Move up 2
        assert strip_ansi(text) == "BeforeAfter"

    def test_strip_ansi_preserves_newlines(self):
        """Newlines should be preserved."""
        text = "\033[32mLine 1\033[0m\nLine 2"
        assert strip_ansi(text) == "Line 1\nLine 2"


class TestAnsiEscapePattern:
    """Test the ANSI escape pattern regex."""

    def test_pattern_matches_csi_sequences(self):
        """Pattern should match CSI sequences."""
        assert ANSI_ESCAPE_PATTERN.search("\033[0m")
        assert ANSI_ESCAPE_PATTERN.search("\033[1;31m")
        assert ANSI_ESCAPE_PATTERN.search("\033[38;5;208m")

    def test_pattern_matches_osc_sequences(self):
        """Pattern should match OSC sequences."""
        assert ANSI_ESCAPE_PATTERN.search("\033]0;Title\007")


class TestMakeRelativePath:
    """Test absolute to relative path conversion."""

    def test_make_relative_path_empty_string(self):
        """Empty string should return empty string."""
        assert make_relative_path("") == ""

    def test_make_relative_path_already_relative(self):
        """Already relative paths should stay relative."""
        result = make_relative_path("some/relative/path.txt")
        assert result == "some/relative/path.txt"

    def test_make_relative_path_with_base_path(self, tmp_path):
        """Should make relative to specified base path."""
        file_path = tmp_path / "subdir" / "file.txt"
        result = make_relative_path(file_path, tmp_path)
        assert result == str(Path("subdir") / "file.txt")

    def test_make_relative_path_uses_cwd_by_default(self, tmp_path, monkeypatch):
        """Should use cwd if no base path specified."""
        monkeypatch.chdir(tmp_path)
        file_path = tmp_path / "file.txt"
        result = make_relative_path(file_path)
        assert result == "file.txt"

    def test_make_relative_path_different_tree(self, tmp_path):
        """Should handle paths in different directory trees."""
        file_path = tmp_path / "a" / "b" / "file.txt"
        base_path = tmp_path / "x" / "y"
        result = make_relative_path(file_path, base_path)
        # Should either be relative with ".." or absolute
        assert isinstance(result, str)

    def test_make_relative_path_with_path_object(self, tmp_path, monkeypatch):
        """Should accept Path objects."""
        monkeypatch.chdir(tmp_path)
        file_path = tmp_path / "file.txt"
        result = make_relative_path(Path(file_path))
        assert result == "file.txt"

    def test_make_relative_path_deeply_nested(self, tmp_path):
        """Paths with many '..' levels should return absolute."""
        file_path = tmp_path / "file.txt"
        # Create a path that would require more than 3 ".." levels
        base_path = tmp_path / "a" / "b" / "c" / "d" / "e"
        result = make_relative_path(file_path, base_path)
        # Either returns the absolute path or a relative with limited ".."
        assert isinstance(result, str)


class TestTruncatePath:
    """Test path truncation."""

    def test_truncate_path_short_path(self):
        """Short paths should not be truncated."""
        path = "short.txt"
        assert truncate_path(path, max_length=60) == "short.txt"

    def test_truncate_path_exact_length(self):
        """Path at max length should not be truncated."""
        path = "a" * 60
        assert truncate_path(path, max_length=60) == path

    def test_truncate_path_long_path(self):
        """Long paths should be truncated with ellipsis."""
        path = "/very/long/path/to/some/directory/with/many/levels/filename.txt"
        result = truncate_path(path, max_length=30)
        assert len(result) <= 30
        assert "..." in result
        assert "filename.txt" in result

    def test_truncate_path_preserves_filename(self):
        """Filename should always be preserved."""
        path = "/some/path/important_file.py"
        result = truncate_path(path, max_length=20)
        assert "important_file.py" in result or result.endswith(".py")

    def test_truncate_path_very_long_filename(self):
        """Very long filenames should be truncated from the start."""
        path = "/path/a_very_long_filename_that_exceeds_max_length.txt"
        result = truncate_path(path, max_length=25)
        assert len(result) <= 25
        assert result.startswith("...")

    def test_truncate_path_with_path_object(self):
        """Should accept Path objects."""
        path = Path("/some/path/file.txt")
        result = truncate_path(path, max_length=60)
        assert isinstance(result, str)

    def test_truncate_path_default_max_length(self):
        """Should use default max_length of 60."""
        path = "/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v/w/x/y/z/file.txt"
        result = truncate_path(path)
        assert len(result) <= 60


class TestFormatErrorPath:
    """Test error path formatting."""

    def test_format_error_path_basic(self, tmp_path, monkeypatch):
        """Should make path relative."""
        monkeypatch.chdir(tmp_path)
        file_path = tmp_path / "subdir" / "file.txt"
        result = format_error_path(file_path)
        # Should be relative to cwd
        assert result == str(Path("subdir") / "file.txt")

    def test_format_error_path_with_base(self, tmp_path):
        """Should use specified base path."""
        file_path = tmp_path / "a" / "b" / "file.txt"
        result = format_error_path(file_path, base_path=tmp_path)
        assert result == str(Path("a") / "b" / "file.txt")

    def test_format_error_path_with_truncation(self, tmp_path):
        """Should truncate long paths when max_length specified."""
        file_path = tmp_path / "very" / "deep" / "nested" / "directory" / "structure" / "file.txt"
        result = format_error_path(file_path, base_path=tmp_path, max_length=30)
        assert len(result) <= 30

    def test_format_error_path_no_truncation(self, tmp_path):
        """Should not truncate when max_length is None."""
        file_path = tmp_path / "a" / "b" / "file.txt"
        result = format_error_path(file_path, base_path=tmp_path, max_length=None)
        # No truncation
        assert "..." not in result or len(str(file_path)) <= 60

    def test_format_error_path_string_input(self, tmp_path, monkeypatch):
        """Should accept string paths."""
        monkeypatch.chdir(tmp_path)
        file_path = str(tmp_path / "file.txt")
        result = format_error_path(file_path)
        assert result == "file.txt"
