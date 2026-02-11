"""Tests for course path resolution."""

from pathlib import Path

import pytest

from clm.core.course_paths import resolve_course_paths


class TestResolveCoursePaths:
    """Tests for resolve_course_paths function."""

    def test_spec_in_subdirectory(self, tmp_path: Path):
        """Spec in course-specs/ should resolve to grandparent."""
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"
        spec_file.touch()

        course_root, output_root = resolve_course_paths(spec_file)

        assert course_root == tmp_path
        assert output_root == tmp_path / "output"

    def test_spec_in_deeply_nested_subdirectory(self, tmp_path: Path):
        """Spec in deeply nested dir should resolve to grandparent."""
        nested = tmp_path / "some" / "nested" / "dir"
        nested.mkdir(parents=True)
        spec_file = nested / "test.xml"
        spec_file.touch()

        course_root, output_root = resolve_course_paths(spec_file)

        # Should go up 2 levels from spec file
        assert course_root == tmp_path / "some" / "nested"
        assert output_root == tmp_path / "some" / "nested" / "output"

    def test_explicit_data_dir_override(self, tmp_path: Path):
        """Explicit data_dir should override automatic resolution."""
        spec_file = tmp_path / "course-specs" / "test.xml"
        spec_file.parent.mkdir(parents=True)
        spec_file.touch()

        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()

        course_root, output_root = resolve_course_paths(spec_file, data_dir=custom_dir)

        assert course_root == custom_dir
        assert output_root == custom_dir / "output"

    def test_returns_absolute_paths(self, tmp_path: Path, monkeypatch):
        """Should return absolute paths even with relative input."""
        monkeypatch.chdir(tmp_path)
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"
        spec_file.touch()

        course_root, output_root = resolve_course_paths(Path("course-specs/test.xml"))

        assert course_root.is_absolute()
        assert output_root.is_absolute()

    def test_data_dir_override_is_not_made_absolute(self, tmp_path: Path, monkeypatch):
        """data_dir override is used as-is (not made absolute)."""
        monkeypatch.chdir(tmp_path)
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"
        spec_file.touch()

        # Create a relative path for data_dir
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()

        course_root, output_root = resolve_course_paths(spec_file, data_dir=custom_dir)

        # Since data_dir is absolute, result should be absolute
        assert course_root == custom_dir
        assert output_root == custom_dir / "output"

    def test_consistency_with_various_spec_subdirectories(self, tmp_path: Path):
        """Test that different subdirectory names all work consistently."""
        for subdir_name in ["course-specs", "specs", "config", "courses"]:
            subdir = tmp_path / subdir_name
            subdir.mkdir(exist_ok=True)
            spec_file = subdir / "test.xml"
            spec_file.touch()

            course_root, output_root = resolve_course_paths(spec_file)

            assert course_root == tmp_path, f"Failed for subdirectory: {subdir_name}"
            assert output_root == tmp_path / "output", f"Failed for subdirectory: {subdir_name}"
