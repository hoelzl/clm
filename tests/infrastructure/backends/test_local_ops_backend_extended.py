"""Extended tests for local_ops_backend module.

Tests cover error handling, file operations, and edge cases.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clx.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clx.infrastructure.messaging.base_classes import Payload
from clx.infrastructure.operation import Operation
from clx.infrastructure.utils.copy_dir_group_data import CopyDirGroupData
from clx.infrastructure.utils.copy_file_data import CopyFileData


class ConcreteLocalOpsBackend(LocalOpsBackend):
    """Concrete implementation for testing."""

    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        pass

    async def wait_for_completion(self) -> bool:
        return True


class TestCopyFileToOutput:
    """Test copy_file_to_output method."""

    @pytest.mark.asyncio
    async def test_copy_creates_parent_directory(self, tmp_path):
        """Should create parent directory if it doesn't exist."""
        infile = tmp_path / "input.txt"
        outfile = tmp_path / "nested" / "dir" / "output.txt"
        infile.write_text("content")

        copy_data = CopyFileData(
            input_path=infile,
            output_path=outfile,
            relative_input_path=infile.relative_to(tmp_path),
        )

        async with ConcreteLocalOpsBackend() as backend:
            await backend.copy_file_to_output(copy_data)

        assert outfile.exists()
        assert outfile.read_text() == "content"

    @pytest.mark.asyncio
    async def test_copy_raises_on_missing_source(self, tmp_path):
        """Should raise FileNotFoundError for missing source file."""
        infile = tmp_path / "missing.txt"
        outfile = tmp_path / "output.txt"

        copy_data = CopyFileData(
            input_path=infile,
            output_path=outfile,
            relative_input_path=Path("missing.txt"),
        )

        async with ConcreteLocalOpsBackend() as backend:
            with pytest.raises(FileNotFoundError) as excinfo:
                await backend.copy_file_to_output(copy_data)

        assert "Source file does not exist" in str(excinfo.value)
        assert "conversion step failed" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_copy_handles_exception(self, tmp_path):
        """Should log and re-raise exceptions during copy."""
        infile = tmp_path / "input.txt"
        outfile = tmp_path / "output.txt"
        infile.write_text("content")

        copy_data = CopyFileData(
            input_path=infile,
            output_path=outfile,
            relative_input_path=Path("input.txt"),
        )

        with patch("clx.infrastructure.backends.local_ops_backend.shutil.copyfile") as mock_copy:
            mock_copy.side_effect = PermissionError("Permission denied")

            async with ConcreteLocalOpsBackend() as backend:
                with pytest.raises(PermissionError):
                    await backend.copy_file_to_output(copy_data)


class TestCopyDirGroupToOutput:
    """Test copy_dir_group_to_output method."""

    @pytest.mark.asyncio
    async def test_copy_skips_missing_source_directory(self, tmp_path, caplog):
        """Should skip and log when source directory doesn't exist."""
        import logging

        output_dir = tmp_path / "output"
        copy_data = CopyDirGroupData(
            name="test-group",
            source_dirs=(tmp_path / "missing",),
            relative_paths=(Path("missing"),),
            lang="en",
            output_dir=output_dir,
        )

        with caplog.at_level(logging.WARNING):
            async with ConcreteLocalOpsBackend() as backend:
                await backend.copy_dir_group_to_output(copy_data)

        assert "Source directory does not exist" in caplog.text

    @pytest.mark.asyncio
    async def test_copy_returns_warnings_for_missing_source_directory(self, tmp_path):
        """Should return warnings when source directory doesn't exist."""
        output_dir = tmp_path / "output"
        missing_dir = tmp_path / "missing_subdir"
        copy_data = CopyDirGroupData(
            name="test-group",
            source_dirs=(missing_dir,),
            relative_paths=(Path("missing_subdir"),),
            lang="en",
            output_dir=output_dir,
        )

        async with ConcreteLocalOpsBackend() as backend:
            warnings = await backend.copy_dir_group_to_output(copy_data)

        assert len(warnings) == 1
        assert "missing_subdir" in warnings[0].message
        assert warnings[0].category == "missing_directory"
        assert warnings[0].severity == "high"

    @pytest.mark.asyncio
    async def test_copy_returns_warnings_for_multiple_missing_directories(self, tmp_path):
        """Should return warnings for each missing source directory."""
        output_dir = tmp_path / "output"
        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()
        (existing_dir / "file.txt").write_text("content")

        missing_dir1 = tmp_path / "missing1"
        missing_dir2 = tmp_path / "missing2"

        copy_data = CopyDirGroupData(
            name="test-group",
            source_dirs=(existing_dir, missing_dir1, missing_dir2),
            relative_paths=(Path("existing"), Path("missing1"), Path("missing2")),
            lang="en",
            output_dir=output_dir,
        )

        async with ConcreteLocalOpsBackend() as backend:
            warnings = await backend.copy_dir_group_to_output(copy_data)

        # Should have 2 warnings (one for each missing directory)
        assert len(warnings) == 2
        warning_messages = [w.message for w in warnings]
        assert any("missing1" in msg for msg in warning_messages)
        assert any("missing2" in msg for msg in warning_messages)

        # Existing directory should have been copied
        assert (output_dir / "existing" / "file.txt").exists()

    @pytest.mark.asyncio
    async def test_copy_returns_empty_warnings_when_all_directories_exist(self, tmp_path):
        """Should return empty warnings list when all directories exist."""
        output_dir = tmp_path / "output"
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("content")

        copy_data = CopyDirGroupData(
            name="test-group",
            source_dirs=(source_dir,),
            relative_paths=(Path("source"),),
            lang="en",
            output_dir=output_dir,
        )

        async with ConcreteLocalOpsBackend() as backend:
            warnings = await backend.copy_dir_group_to_output(copy_data)

        assert len(warnings) == 0
        assert (output_dir / "source" / "file.txt").exists()

    @pytest.mark.asyncio
    async def test_copy_dir_group_error_handling(self, tmp_path):
        """Should log and re-raise exceptions during dir group copy."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("content")

        output_dir = tmp_path / "output"
        copy_data = CopyDirGroupData(
            name="test-group",
            source_dirs=(source_dir,),
            relative_paths=(Path("source"),),
            lang="en",
            output_dir=output_dir,
        )

        with patch("clx.infrastructure.backends.local_ops_backend.shutil.copytree") as mock_copy:
            mock_copy.side_effect = PermissionError("Permission denied")

            async with ConcreteLocalOpsBackend() as backend:
                with pytest.raises(PermissionError):
                    await backend.copy_dir_group_to_output(copy_data)

    @pytest.mark.asyncio
    async def test_copy_dir_group_with_include_root_files(self, tmp_path):
        """Should copy files from base_path when include-root-files is set."""
        # Create directory structure with root files and subdirectories
        base_dir = tmp_path / "code" / "completed"
        base_dir.mkdir(parents=True)

        # Create root files
        (base_dir / "CMakeLists.txt").write_text("cmake content")
        (base_dir / "README.md").write_text("readme content")

        # Create subdirectories with their own files
        subdir1 = base_dir / "Example_1"
        subdir1.mkdir()
        (subdir1 / "main.cpp").write_text("main cpp content")

        subdir2 = base_dir / "Example_2"
        subdir2.mkdir()
        (subdir2 / "util.cpp").write_text("util cpp content")

        output_dir = tmp_path / "output"

        copy_data = CopyDirGroupData(
            name="Code/Completed",
            source_dirs=(subdir1, subdir2),
            relative_paths=(Path("Example_1"), Path("Example_2")),
            lang="en",
            output_dir=output_dir,
            base_path=base_dir,
        )

        async with ConcreteLocalOpsBackend() as backend:
            warnings = await backend.copy_dir_group_to_output(copy_data)

        # Should have no warnings
        assert len(warnings) == 0

        # Root files should be copied to output_dir
        assert (output_dir / "CMakeLists.txt").exists()
        assert (output_dir / "CMakeLists.txt").read_text() == "cmake content"
        assert (output_dir / "README.md").exists()
        assert (output_dir / "README.md").read_text() == "readme content"

        # Subdirectories should be copied
        assert (output_dir / "Example_1" / "main.cpp").exists()
        assert (output_dir / "Example_2" / "util.cpp").exists()

    @pytest.mark.asyncio
    async def test_copy_dir_group_include_root_files_missing_base_path(self, tmp_path, caplog):
        """Should return warning when base_path doesn't exist."""
        import logging

        output_dir = tmp_path / "output"
        missing_base = tmp_path / "missing_base"

        copy_data = CopyDirGroupData(
            name="test-group",
            source_dirs=(),
            relative_paths=(),
            lang="en",
            output_dir=output_dir,
            base_path=missing_base,
        )

        with caplog.at_level(logging.WARNING):
            async with ConcreteLocalOpsBackend() as backend:
                warnings = await backend.copy_dir_group_to_output(copy_data)

        assert len(warnings) == 1
        assert "Base directory does not exist" in warnings[0].message
        assert warnings[0].category == "missing_directory"

    @pytest.mark.asyncio
    async def test_copy_dir_group_include_root_files_only_copies_files_not_dirs(self, tmp_path):
        """Should only copy files (not directories) from base_path."""
        base_dir = tmp_path / "base"
        base_dir.mkdir()

        # Create a file and a directory in the base
        (base_dir / "root_file.txt").write_text("root file content")
        nested_dir = base_dir / "nested_dir"
        nested_dir.mkdir()
        (nested_dir / "nested_file.txt").write_text("nested content")

        output_dir = tmp_path / "output"

        copy_data = CopyDirGroupData(
            name="test-group",
            source_dirs=(),
            relative_paths=(),
            lang="en",
            output_dir=output_dir,
            base_path=base_dir,
        )

        async with ConcreteLocalOpsBackend() as backend:
            warnings = await backend.copy_dir_group_to_output(copy_data)

        assert len(warnings) == 0

        # Root file should be copied
        assert (output_dir / "root_file.txt").exists()

        # Nested directory should NOT be copied (only files from base_path)
        assert not (output_dir / "nested_dir").exists()


class TestDeleteDependencies:
    """Test delete_dependencies method."""

    @pytest.mark.asyncio
    async def test_delete_dependencies_clears_generated_outputs(self, tmp_path):
        """Should delete generated outputs and clear the list."""
        from clx.core.course_file import CourseFile

        # Create a mock CourseFile with generated outputs as a MagicMock list
        mock_file = MagicMock(spec=CourseFile)
        mock_file.path = tmp_path / "test.ipynb"
        output1 = tmp_path / "output1.html"
        output2 = tmp_path / "output2.html"
        output1.write_text("content1")
        output2.write_text("content2")

        # Use MagicMock list to track clear() calls
        mock_outputs = MagicMock()
        mock_outputs.__iter__ = MagicMock(return_value=iter([output1, output2]))
        mock_file.generated_outputs = mock_outputs

        async with ConcreteLocalOpsBackend() as backend:
            await backend.delete_dependencies(mock_file)

        # generated_outputs.clear() should have been called
        mock_outputs.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_dependencies_non_course_file(self, tmp_path):
        """Should handle non-CourseFile gracefully."""
        from clx.infrastructure.utils.file import File

        # Create a simple File mock (not a CourseFile)
        mock_file = MagicMock(spec=File)
        mock_file.path = tmp_path / "test.txt"

        # Should not raise
        async with ConcreteLocalOpsBackend() as backend:
            await backend.delete_dependencies(mock_file)


class TestDeleteFile:
    """Test delete_file method."""

    @pytest.mark.asyncio
    async def test_delete_file_removes_existing_file(self, tmp_path):
        """Should delete existing file."""
        file_path = tmp_path / "to_delete.txt"
        file_path.write_text("content")

        assert file_path.exists()

        async with ConcreteLocalOpsBackend() as backend:
            await backend.delete_file(file_path)

        assert not file_path.exists()

    @pytest.mark.asyncio
    async def test_delete_file_handles_missing_file(self, tmp_path):
        """Should not raise for missing file (missing_ok=True)."""
        file_path = tmp_path / "nonexistent.txt"

        async with ConcreteLocalOpsBackend() as backend:
            # Should not raise
            await backend.delete_file(file_path)


class TestTaskGroupShim:
    """Test TaskGroup shim for Python 3.10 compatibility."""

    @pytest.mark.asyncio
    async def test_taskgroup_shim_executes_tasks(self):
        """Test that TaskGroup shim works for concurrent task execution."""
        import sys

        # This test exercises the TaskGroup usage in delete_dependencies
        # The actual implementation uses TaskGroup from asyncio (3.11+) or shim
        results = []

        async def track_task(value):
            results.append(value)

        # Create mock CourseFile with generated outputs
        from clx.core.course_file import CourseFile

        mock_file = MagicMock(spec=CourseFile)
        mock_file.path = Path("/test/file.ipynb")
        mock_file.generated_outputs = [Path("/test/out1.html"), Path("/test/out2.html")]

        # The delete_dependencies method uses TaskGroup internally
        async with ConcreteLocalOpsBackend() as backend:
            # Mock delete_file to track calls
            original_delete = backend.delete_file
            call_count = 0

            async def mock_delete(path):
                nonlocal call_count
                call_count += 1

            backend.delete_file = mock_delete
            await backend.delete_dependencies(mock_file)

        assert call_count == 2  # Two files should have been "deleted"
