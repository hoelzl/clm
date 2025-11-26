"""Tests for image path utilities and path rewriting."""

from pathlib import Path

import pytest

from clx.infrastructure.utils.path_utils import relative_path_to_course_img


class TestRelativePathToCourseImg:
    """Tests for the relative_path_to_course_img function."""

    def test_one_level_deep(self):
        """Test output file one directory deep from course."""
        course_dir = Path("output/public/De/Kurs")
        output_file = Path("output/public/De/Kurs/Folien/notebook.html")

        result = relative_path_to_course_img(output_file, course_dir)

        assert result == "../img/"

    def test_two_levels_deep(self):
        """Test output file two directories deep from course."""
        course_dir = Path("output/public/De/Kurs")
        output_file = Path("output/public/De/Kurs/Folien/Html/notebook.html")

        result = relative_path_to_course_img(output_file, course_dir)

        assert result == "../../img/"

    def test_four_levels_deep(self):
        """Test output file in typical structure (Slides/Html/Code-Along/Section)."""
        course_dir = Path("output/public/De/Mein-Kurs")
        output_file = Path(
            "output/public/De/Mein-Kurs/Folien/Html/Code-Along/Section/01 Topic.html"
        )

        result = relative_path_to_course_img(output_file, course_dir)

        assert result == "../../../../img/"

    def test_same_directory(self):
        """Test output file directly in course directory."""
        course_dir = Path("output/public/De/Kurs")
        output_file = Path("output/public/De/Kurs/notebook.html")

        result = relative_path_to_course_img(output_file, course_dir)

        assert result == "img/"

    def test_output_not_under_course_dir(self):
        """Test fallback when output is not under course directory."""
        course_dir = Path("output/public/De/Kurs")
        output_file = Path("somewhere/else/notebook.html")

        result = relative_path_to_course_img(output_file, course_dir)

        # Should fall back to simple "img/"
        assert result == "img/"

    def test_absolute_paths(self, tmp_path):
        """Test with absolute paths."""
        course_dir = tmp_path / "output" / "public" / "De" / "Kurs"
        output_file = (
            tmp_path / "output" / "public" / "De" / "Kurs" / "Slides" / "Html" / "notebook.html"
        )

        result = relative_path_to_course_img(output_file, course_dir)

        assert result == "../../img/"


class TestImagePathRewriting:
    """Tests for image path rewriting in notebook processor."""

    def test_rewrite_simple_img_tag(self):
        """Test rewriting a simple img tag."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = '<img src="img/diagram.png">'
        result = NotebookProcessor._rewrite_image_paths(content, "../../img/")

        assert result == '<img src="../../img/diagram.png">'

    def test_rewrite_multiple_img_tags(self):
        """Test rewriting multiple img tags."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = """
        <img src="img/diagram1.png">
        Some text
        <img src="img/diagram2.png">
        """
        result = NotebookProcessor._rewrite_image_paths(content, "../img/")

        assert '<img src="../img/diagram1.png">' in result
        assert '<img src="../img/diagram2.png">' in result

    def test_no_rewrite_for_default_prefix(self):
        """Test that img/ prefix is not rewritten."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = '<img src="img/diagram.png">'
        result = NotebookProcessor._rewrite_image_paths(content, "img/")

        # Should be unchanged
        assert result == content

    def test_no_rewrite_external_urls(self):
        """Test that external URLs are not rewritten."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = '<img src="https://example.com/image.png">'
        result = NotebookProcessor._rewrite_image_paths(content, "../../img/")

        # Should be unchanged
        assert result == content

    def test_no_rewrite_absolute_paths(self):
        """Test that absolute paths are not rewritten."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = '<img src="/absolute/path/image.png">'
        result = NotebookProcessor._rewrite_image_paths(content, "../../img/")

        # Should be unchanged
        assert result == content

    def test_rewrite_with_attributes(self):
        """Test rewriting img tags with other attributes."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = '<img alt="Diagram" src="img/diagram.png" width="100">'
        result = NotebookProcessor._rewrite_image_paths(content, "../img/")

        assert 'src="../img/diagram.png"' in result
        assert 'alt="Diagram"' in result
        assert 'width="100"' in result

    def test_rewrite_single_quotes(self):
        """Test rewriting img tags with single quotes."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = "<img src='img/diagram.png'>"
        result = NotebookProcessor._rewrite_image_paths(content, "../../img/")

        assert result == "<img src='../../img/diagram.png'>"

    def test_preserve_non_img_content(self):
        """Test that non-img content is preserved."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = """
        # Header
        Some paragraph text.
        <img src="img/diagram.png">
        More text.
        ```python
        code_block = True
        ```
        """
        result = NotebookProcessor._rewrite_image_paths(content, "../img/")

        assert "# Header" in result
        assert "Some paragraph text." in result
        assert '<img src="../img/diagram.png">' in result
        assert "More text." in result
        assert "code_block = True" in result

    def test_nested_folder_in_img(self):
        """Test rewriting paths with nested folders in img/."""
        from clx.workers.notebook.notebook_processor import NotebookProcessor

        content = '<img src="img/subdir/diagram.png">'
        result = NotebookProcessor._rewrite_image_paths(content, "../../img/")

        # Should preserve the subdirectory structure
        assert result == '<img src="../../img/subdir/diagram.png">'
