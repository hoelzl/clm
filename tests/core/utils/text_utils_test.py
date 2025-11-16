import sys
from pathlib import Path

from clx.core.utils.text_utils import Text, as_dir_name, sanitize_file_name, sanitize_path


def test_text_getitem():
    unit = Text(de="De", en="En")
    assert unit["de"] == "De"
    assert unit["en"] == "En"


def test_as_dir_name():
    assert as_dir_name("slides", "de") == "Folien"
    assert as_dir_name("slides", "en") == "Slides"


def test_sanitize_file_name_basic():
    """Test basic filename sanitization."""
    assert sanitize_file_name("simple_file") == "simple_file"
    assert sanitize_file_name("file with spaces") == "file with spaces"


def test_sanitize_file_name_special_chars():
    """Test sanitization of filesystem-unsafe characters."""
    # Characters replaced with underscore: /\$#%&<>*=^€|
    assert sanitize_file_name("file/path") == "file_path"
    assert sanitize_file_name("file\\path") == "file_path"
    assert sanitize_file_name("file$test") == "file_test"
    assert sanitize_file_name("file#tag") == "file_tag"
    assert sanitize_file_name("100%") == "100_"
    assert sanitize_file_name("foo&bar") == "foo_bar"
    assert sanitize_file_name("<test>") == "_test_"
    assert sanitize_file_name("file*wildcard") == "file_wildcard"
    assert sanitize_file_name("a=b") == "a_b"
    assert sanitize_file_name("x^2") == "x_2"
    assert sanitize_file_name("10€") == "10_"
    assert sanitize_file_name("a|b") == "a_b"


def test_sanitize_file_name_brackets():
    """Test bracket replacement."""
    # Braces and brackets replaced with parentheses: {}[] -> ()()
    assert sanitize_file_name("test{1}") == "test(1)"
    assert sanitize_file_name("test[1]") == "test(1)"
    assert sanitize_file_name("a{b}c[d]") == "a(b)c(d)"


def test_sanitize_file_name_deleted_chars():
    """Test characters that are deleted."""
    # Deleted: ;!?"'`.:
    assert sanitize_file_name("test;file") == "testfile"
    assert sanitize_file_name("wow!") == "wow"
    assert sanitize_file_name("what?") == "what"
    assert sanitize_file_name('test"quote"') == "testquote"
    assert sanitize_file_name("test'quote") == "testquote"
    assert sanitize_file_name("test`backtick") == "testbacktick"
    assert sanitize_file_name("file.name.txt") == "filenametxt"
    assert sanitize_file_name("test:colon") == "testcolon"


def test_sanitize_file_name_csharp():
    """Test C# special handling."""
    assert sanitize_file_name("C#") == "CSharp"
    assert sanitize_file_name("MyC#File") == "MyCSharpFile"


def test_sanitize_file_name_whitespace():
    """Test whitespace trimming."""
    assert sanitize_file_name("  test  ") == "test"
    assert sanitize_file_name("\ttest\n") == "test"


def test_sanitize_path_relative_simple():
    """Test sanitizing simple relative paths."""
    result = sanitize_path(Path("foo/bar/file.txt"))
    assert result == Path("foo/bar/file.txt")


def test_sanitize_path_relative_special_chars():
    """Test sanitizing relative paths with special characters."""
    result = sanitize_path(Path("foo/bar: test/file?.txt"))
    assert result == Path("foo/bar test/file.txt")

    result = sanitize_path(Path("section{1}/topic[2]/file!.txt"))
    assert result == Path("section(1)/topic(2)/file.txt")


def test_sanitize_path_relative_unsafe_chars():
    """Test sanitizing relative paths with filesystem-unsafe characters."""
    # Path separators in component names should be replaced
    result = sanitize_path(Path("foo$/bar#/file*.txt"))
    assert result == Path("foo_/bar_/file_.txt")

    # Multiple problematic characters
    result = sanitize_path(Path("dir<test>/file&name.txt"))
    assert result == Path("dir_test_/file_name.txt")


def test_sanitize_path_absolute_unix():
    """Test sanitizing absolute Unix-style paths."""
    if sys.platform == "win32":
        # On Windows, Unix-style absolute paths are treated as relative
        # and get a "_" prefix to make them valid
        result = sanitize_path(Path("/home/user/test: file.txt"))
        assert result == Path("_/home/user/test file.txt")

        result = sanitize_path(Path("/var/log/file?.log"))
        assert result == Path("_/var/log/file.log")
    else:
        # On Unix, these are true absolute paths
        result = sanitize_path(Path("/home/user/test: file.txt"))
        assert result == Path("/home/user/test file.txt")

        result = sanitize_path(Path("/var/log/file?.log"))
        assert result == Path("/var/log/file.log")


def test_sanitize_path_absolute_windows():
    """Test sanitizing absolute Windows-style paths."""
    result = sanitize_path(Path("C:/Users/test: file/doc.txt"))

    if sys.platform == "win32":
        # On Windows, "C:" is recognized as a drive letter
        expected_parts = ["C:\\", "Users", "test file", "doc.txt"]
    else:
        # On Unix, "C:" becomes a regular path component
        expected_parts = ["C", "Users", "test file", "doc.txt"]

    assert list(result.parts) == expected_parts


def test_sanitize_path_empty():
    """Test sanitizing empty paths."""
    result = sanitize_path(Path("."))
    assert result == Path(".")


def test_sanitize_path_single_component():
    """Test sanitizing single component paths."""
    result = sanitize_path(Path("file?.txt"))
    assert result == Path("file.txt")


def test_sanitize_path_preserves_extension():
    """Test that file extensions are preserved."""
    result = sanitize_path(Path("test/file!name.txt"))
    assert result == Path("test/filename.txt")
    assert result.suffix == ".txt"


def test_sanitize_path_complex_example():
    """Test complex real-world example."""
    # Simulating a PlantUML file with problematic name
    original = Path("course/pu/diagram: sequence {v1}.pu")
    result = sanitize_path(original)
    assert result == Path("course/pu/diagram sequence (v1).pu")

    # Simulating output path with problematic directory names
    original = Path("output/Section #1: Intro/file?.png")
    result = sanitize_path(original)
    assert result == Path("output/Section _1 Intro/file.png")

