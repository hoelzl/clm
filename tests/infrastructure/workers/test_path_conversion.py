"""Unit tests for Docker path conversion utilities.

These tests verify that the convert_host_path_to_container function correctly
converts absolute host paths (Windows or Unix) to container paths that are
relative to the /workspace mount point.

This is critical for Docker worker functionality - without proper path conversion,
Docker workers cannot read input files or write output files.
"""

from pathlib import Path

import pytest

from clm.infrastructure.workers.worker_base import (
    CONTAINER_SOURCE,
    CONTAINER_WORKSPACE,
    convert_host_path_to_container,
    convert_input_path_to_container,
    convert_output_path_to_container,
)


class TestContainerConstants:
    """Tests for container mount point constants."""

    def test_container_workspace_is_workspace(self):
        """Container workspace should be /workspace."""
        assert CONTAINER_WORKSPACE == "/workspace"

    def test_container_source_is_source(self):
        """Container source should be /source."""
        assert CONTAINER_SOURCE == "/source"


class TestConvertHostPathToContainerWindows:
    """Tests for Windows path conversion."""

    def test_converts_windows_path_with_backslashes(self):
        """Should convert Windows path with backslashes to container path."""
        host_path = r"C:\Users\tc\workspace\output\file.ipynb"
        host_workspace = r"C:\Users\tc\workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_converts_windows_path_with_forward_slashes(self):
        """Should handle Windows paths that use forward slashes."""
        host_path = "C:/Users/tc/workspace/output/file.ipynb"
        host_workspace = "C:/Users/tc/workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_handles_nested_subdirectories_windows(self):
        """Should preserve nested directory structure from Windows path."""
        host_path = r"C:\workspace\public\De\Course\Slides\Notebooks\file.ipynb"
        host_workspace = r"C:\workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/public/De/Course/Slides/Notebooks/file.ipynb")

    def test_handles_different_drive_letters(self):
        """Should handle different Windows drive letters."""
        host_path = r"D:\projects\clx\output\file.ipynb"
        host_workspace = r"D:\projects\clx"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_handles_mixed_slashes_in_windows_path(self):
        """Should handle mixed forward and back slashes in Windows path."""
        host_path = r"C:\Users/tc\workspace/output\file.ipynb"
        host_workspace = r"C:\Users\tc\workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_windows_path_case_insensitivity(self):
        """Windows paths should be case-insensitive for workspace matching."""
        host_path = r"C:\USERS\TC\Workspace\output\file.ipynb"
        host_workspace = r"C:\Users\tc\workspace"

        # This should work on Windows due to case insensitivity
        # The function uses PureWindowsPath which handles this
        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")


class TestConvertHostPathToContainerUnix:
    """Tests for Unix path conversion."""

    def test_converts_unix_absolute_path(self):
        """Should convert Unix absolute path to container path."""
        host_path = "/home/user/workspace/output/file.ipynb"
        host_workspace = "/home/user/workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")

    def test_handles_nested_subdirectories_unix(self):
        """Should preserve nested directory structure from Unix path."""
        host_path = "/var/data/workspace/public/En/Course/Slides/file.ipynb"
        host_workspace = "/var/data/workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/public/En/Course/Slides/file.ipynb")

    def test_handles_root_workspace(self):
        """Should handle workspace at filesystem root."""
        host_path = "/workspace/output/file.ipynb"
        host_workspace = "/workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/output/file.ipynb")


class TestConvertHostPathToContainerErrors:
    """Tests for error handling in path conversion."""

    def test_raises_error_when_windows_path_not_under_workspace(self):
        """Should raise ValueError when Windows path is outside workspace."""
        host_path = r"C:\other\location\file.ipynb"
        host_workspace = r"C:\workspace"

        with pytest.raises(ValueError, match="is not under"):
            convert_host_path_to_container(host_path, host_workspace)

    def test_raises_error_when_unix_path_not_under_workspace(self):
        """Should raise ValueError when Unix path is outside workspace."""
        host_path = "/other/location/file.ipynb"
        host_workspace = "/home/user/workspace"

        with pytest.raises(ValueError, match="is not under"):
            convert_host_path_to_container(host_path, host_workspace)

    def test_raises_error_for_different_drive_letters(self):
        """Should raise error when Windows paths are on different drives."""
        host_path = r"D:\output\file.ipynb"
        host_workspace = r"C:\workspace"

        with pytest.raises(ValueError, match="is not under"):
            convert_host_path_to_container(host_path, host_workspace)

    def test_raises_error_for_partial_path_match(self):
        """Should not match partial directory names."""
        host_path = r"C:\workspace-old\output\file.ipynb"
        host_workspace = r"C:\workspace"

        with pytest.raises(ValueError, match="is not under"):
            convert_host_path_to_container(host_path, host_workspace)


class TestConvertHostPathToContainerEdgeCases:
    """Tests for edge cases in path conversion."""

    def test_handles_file_directly_in_workspace(self):
        """Should handle file directly in workspace root."""
        host_path = r"C:\workspace\file.ipynb"
        host_workspace = r"C:\workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/file.ipynb")

    def test_handles_deeply_nested_path(self):
        """Should handle very deeply nested paths."""
        host_path = r"C:\ws\a\b\c\d\e\f\g\h\i\j\file.ipynb"
        host_workspace = r"C:\ws"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/a/b/c/d/e/f/g/h/i/j/file.ipynb")

    def test_handles_special_characters_in_path(self):
        """Should handle paths with spaces and special characters."""
        host_path = r"C:\My Workspace\Output Files\file (1).ipynb"
        host_workspace = r"C:\My Workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/Output Files/file (1).ipynb")

    def test_handles_unicode_in_path(self):
        """Should handle paths with unicode characters."""
        host_path = r"C:\workspace\Übungen\Lösung.ipynb"
        host_workspace = r"C:\workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        assert result == Path("/workspace/Übungen/Lösung.ipynb")

    def test_result_path_starts_with_workspace(self):
        """Result should be under /workspace directory."""
        host_path = r"C:\workspace\dir\subdir\file.ipynb"
        host_workspace = r"C:\workspace"

        result = convert_host_path_to_container(host_path, host_workspace)

        # Check that the path is rooted at /workspace
        # Note: Path.parts gives us platform-independent path components
        assert result.parts[0] == "/" or result.parts[0] == "\\"
        assert result.parts[1] == "workspace"
        assert result.parts[-1] == "file.ipynb"

        # Use as_posix() to verify the POSIX representation
        posix_str = result.as_posix()
        assert posix_str == "/workspace/dir/subdir/file.ipynb"


class TestConvertHostPathToContainerIntegration:
    """Integration-style tests for path conversion in realistic scenarios."""

    def test_typical_windows_course_build_output_path(self):
        """Should handle typical Windows CLX course build output path."""
        host_path = (
            r"C:\Users\tc\Programming\Cpp\CppCourses\output\public\De"
            r"\C++ Best Practice\Slides\Notebooks\Code-Along\01_intro.ipynb"
        )
        host_workspace = r"C:\Users\tc\Programming\Cpp\CppCourses\output"

        result = convert_host_path_to_container(host_path, host_workspace)

        expected = Path(
            "/workspace/public/De/C++ Best Practice/Slides/Notebooks/Code-Along/01_intro.ipynb"
        )
        assert result == expected

    def test_typical_unix_course_build_output_path(self):
        """Should handle typical Unix CLX course build output path."""
        host_path = (
            "/home/developer/courses/output/speaker/En/Python ML/Slides/Html/02_data_prep.html"
        )
        host_workspace = "/home/developer/courses/output"

        result = convert_host_path_to_container(host_path, host_workspace)

        expected = Path("/workspace/speaker/En/Python ML/Slides/Html/02_data_prep.html")
        assert result == expected


class TestConvertInputPathToContainer:
    """Tests for input path conversion (source files)."""

    def test_converts_windows_input_path(self):
        """Should convert Windows input path to /source container path."""
        host_path = (
            r"C:\Users\tc\Programming\Cpp\CppCourses\slides\module_120\topic_if\slides_if.cpp"
        )
        host_data_dir = r"C:\Users\tc\Programming\Cpp\CppCourses"

        result = convert_input_path_to_container(host_path, host_data_dir)

        assert result == Path("/source/slides/module_120/topic_if/slides_if.cpp")

    def test_converts_unix_input_path(self):
        """Should convert Unix input path to /source container path."""
        host_path = "/home/user/courses/slides/module_100/topic_intro/slides_intro.cpp"
        host_data_dir = "/home/user/courses"

        result = convert_input_path_to_container(host_path, host_data_dir)

        assert result == Path("/source/slides/module_100/topic_intro/slides_intro.cpp")

    def test_handles_nested_topic_structure(self):
        """Should handle deeply nested topic directory structure."""
        host_path = r"C:\data\slides\module_500_ml\topic_200_neural_nets\img\diagram.drawio"
        host_data_dir = r"C:\data"

        result = convert_input_path_to_container(host_path, host_data_dir)

        assert result == Path(
            "/source/slides/module_500_ml/topic_200_neural_nets/img/diagram.drawio"
        )

    def test_raises_error_when_not_under_data_dir(self):
        """Should raise ValueError when input path is outside data directory."""
        host_path = r"C:\other\location\file.cpp"
        host_data_dir = r"C:\data"

        with pytest.raises(ValueError, match="is not under"):
            convert_input_path_to_container(host_path, host_data_dir)

    def test_typical_windows_slide_file_path(self):
        """Should handle typical Windows CLX slide file path."""
        host_path = (
            r"C:\Users\tc\Programming\Cpp\CppCourses\slides"
            r"\module_120_basics\topic_162_if\slides_if.cpp"
        )
        host_data_dir = r"C:\Users\tc\Programming\Cpp\CppCourses"

        result = convert_input_path_to_container(host_path, host_data_dir)

        expected = Path("/source/slides/module_120_basics/topic_162_if/slides_if.cpp")
        assert result == expected

    def test_typical_unix_slide_file_path(self):
        """Should handle typical Unix CLX slide file path."""
        host_path = "/home/developer/courses/slides/module_100_intro/topic_010_welcome/slides.cpp"
        host_data_dir = "/home/developer/courses"

        result = convert_input_path_to_container(host_path, host_data_dir)

        expected = Path("/source/slides/module_100_intro/topic_010_welcome/slides.cpp")
        assert result == expected


class TestConvertOutputPathToContainer:
    """Tests for smart output path conversion that tries workspace first, then data_dir."""

    def test_prefers_workspace_when_path_is_under_workspace(self):
        """Should use /workspace when path is under host_workspace."""
        host_path = "/home/user/workspace/output/file.png"
        host_workspace = "/home/user/workspace"
        host_data_dir = "/home/user/data"

        result = convert_output_path_to_container(host_path, host_workspace, host_data_dir)

        assert result == Path("/workspace/output/file.png")

    def test_falls_back_to_data_dir_when_not_under_workspace(self):
        """Should use /source when path is under data_dir but not workspace."""
        host_path = "/home/user/data/slides/module/img/diagram.png"
        host_workspace = "/home/user/workspace"
        host_data_dir = "/home/user/data"

        result = convert_output_path_to_container(host_path, host_workspace, host_data_dir)

        assert result == Path("/source/slides/module/img/diagram.png")

    def test_handles_plantuml_output_in_source_tree(self):
        """Should handle PlantUML output images that go in the source tree."""
        host_path = r"C:\data\slides\module_100\topic_200\img\my_diagram.png"
        host_workspace = r"C:\output"
        host_data_dir = r"C:\data"

        result = convert_output_path_to_container(host_path, host_workspace, host_data_dir)

        assert result == Path("/source/slides/module_100/topic_200/img/my_diagram.png")

    def test_handles_drawio_output_in_source_tree(self):
        """Should handle DrawIO output images that go in the source tree."""
        host_path = "/tmp/pytest/test-data/slides/module/topic/img/flowchart.png"
        host_workspace = "/tmp/pytest/output"
        host_data_dir = "/tmp/pytest/test-data"

        result = convert_output_path_to_container(host_path, host_workspace, host_data_dir)

        assert result == Path("/source/slides/module/topic/img/flowchart.png")

    def test_workspace_only_mode(self):
        """Should work with only workspace (no data_dir)."""
        host_path = "/home/user/workspace/output/file.ipynb"
        host_workspace = "/home/user/workspace"

        result = convert_output_path_to_container(host_path, host_workspace, None)

        assert result == Path("/workspace/output/file.ipynb")

    def test_data_dir_only_mode(self):
        """Should work with only data_dir (no workspace)."""
        host_path = "/home/user/data/slides/module/img/diagram.png"
        host_data_dir = "/home/user/data"

        result = convert_output_path_to_container(host_path, None, host_data_dir)

        assert result == Path("/source/slides/module/img/diagram.png")

    def test_raises_error_when_neither_mount_matches(self):
        """Should raise ValueError when path is not under workspace or data_dir."""
        host_path = "/other/location/file.png"
        host_workspace = "/home/user/workspace"
        host_data_dir = "/home/user/data"

        with pytest.raises(ValueError, match="is not under"):
            convert_output_path_to_container(host_path, host_workspace, host_data_dir)

    def test_raises_error_when_no_mounts_provided(self):
        """Should raise ValueError when neither workspace nor data_dir is provided."""
        host_path = "/some/path/file.png"

        with pytest.raises(ValueError, match="neither host_workspace nor host_data_dir"):
            convert_output_path_to_container(host_path, None, None)

    def test_typical_ci_test_scenario(self):
        """Should handle typical CI test scenario with tmp directories."""
        # This mimics the actual error scenario from the CI failure
        host_path = (
            "/tmp/pytest-of-runner/pytest-0/test_foo/test-data"
            "/slides/module_000/topic_100/img/my_diag.png"
        )
        host_workspace = "/tmp/pytest-of-runner/pytest-0/test_foo/output"
        host_data_dir = "/tmp/pytest-of-runner/pytest-0/test_foo/test-data"

        result = convert_output_path_to_container(host_path, host_workspace, host_data_dir)

        # Should fall back to data_dir since path is under test-data, not output
        assert result == Path("/source/slides/module_000/topic_100/img/my_diag.png")
