"""Tests for CLI text utilities.

Tests path formatting for CLI output:
- Path conversion (absolute to relative)
- Path truncation
- Error path formatting

ANSI stripping is covered in ``tests/infrastructure/utils/test_text_utils.py``
(the scrubber moved to infrastructure in #802/A2).
"""

import os
from pathlib import Path

import pytest

from clm.build.text_utils import (
    format_error_path,
    make_relative_path,
    truncate_path,
)


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
