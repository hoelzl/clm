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

        with caplog.at_level(logging.ERROR):
            async with ConcreteLocalOpsBackend() as backend:
                await backend.copy_dir_group_to_output(copy_data)

        assert "Source directory does not exist" in caplog.text

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
