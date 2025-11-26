"""Tests for delete_file module.

This module tests the DeleteFileOperation including:
- File deletion
- Generated outputs tracking
- Async execution
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clx.core.operations.delete_file import DeleteFileOperation


@pytest.fixture
def mock_course_file():
    """Create a mock CourseFile."""
    mock = MagicMock()
    mock.generated_outputs = []
    return mock


@pytest.fixture
def temp_file(tmp_path):
    """Create a temporary file for testing."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")
    return file_path


class TestDeleteFileOperationInit:
    """Test DeleteFileOperation initialization."""

    def test_init_stores_file_and_path(self, mock_course_file, tmp_path):
        """Should store file and file_to_delete attributes."""
        file_path = tmp_path / "test.txt"
        op = DeleteFileOperation(file=mock_course_file, file_to_delete=file_path)

        assert op.file is mock_course_file
        assert op.file_to_delete == file_path

    def test_is_frozen(self, mock_course_file, tmp_path):
        """Operation should be frozen (immutable)."""
        file_path = tmp_path / "test.txt"
        op = DeleteFileOperation(file=mock_course_file, file_to_delete=file_path)

        with pytest.raises(AttributeError):
            op.file = MagicMock()  # type: ignore


class TestDeleteFileOperationExecute:
    """Test DeleteFileOperation execute method."""

    @pytest.mark.asyncio
    async def test_execute_deletes_file(self, mock_course_file, temp_file):
        """Execute should delete the specified file."""
        mock_course_file.generated_outputs = [temp_file]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=temp_file)

        assert temp_file.exists()
        await op.execute(backend=MagicMock())
        assert not temp_file.exists()

    @pytest.mark.asyncio
    async def test_execute_removes_from_generated_outputs(self, mock_course_file, temp_file):
        """Execute should remove file from generated_outputs list."""
        mock_course_file.generated_outputs = [temp_file, Path("/other/file.txt")]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=temp_file)

        await op.execute(backend=MagicMock())

        assert temp_file not in mock_course_file.generated_outputs
        assert Path("/other/file.txt") in mock_course_file.generated_outputs

    @pytest.mark.asyncio
    async def test_execute_runs_exec_sync(self, mock_course_file, temp_file):
        """Execute should run exec_sync to perform the deletion."""
        mock_course_file.generated_outputs = [temp_file]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=temp_file)

        # The execute method uses run_in_executor which ultimately calls exec_sync
        # Verify this by checking that the file is deleted after execute
        assert temp_file.exists()
        await op.execute(backend=MagicMock())
        assert not temp_file.exists()


class TestDeleteFileOperationExecSync:
    """Test DeleteFileOperation exec_sync method."""

    def test_exec_sync_deletes_file(self, mock_course_file, temp_file):
        """exec_sync should delete the file."""
        mock_course_file.generated_outputs = [temp_file]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=temp_file)

        assert temp_file.exists()
        op.exec_sync()
        assert not temp_file.exists()

    def test_exec_sync_removes_from_outputs(self, mock_course_file, temp_file):
        """exec_sync should remove file from generated_outputs."""
        mock_course_file.generated_outputs = [temp_file]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=temp_file)

        op.exec_sync()

        assert len(mock_course_file.generated_outputs) == 0

    def test_exec_sync_raises_for_nonexistent_file(self, mock_course_file, tmp_path):
        """exec_sync should raise FileNotFoundError for nonexistent file."""
        nonexistent = tmp_path / "nonexistent.txt"
        mock_course_file.generated_outputs = [nonexistent]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=nonexistent)

        with pytest.raises(FileNotFoundError):
            op.exec_sync()

    def test_exec_sync_logs_deletion(self, mock_course_file, temp_file, caplog):
        """exec_sync should log the deletion."""
        import logging

        mock_course_file.generated_outputs = [temp_file]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=temp_file)

        with caplog.at_level(logging.INFO, logger="clx.core.operations.delete_file"):
            op.exec_sync()

        assert "Deleting" in caplog.text
        assert str(temp_file) in caplog.text


class TestDeleteFileOperationEdgeCases:
    """Test edge cases for DeleteFileOperation."""

    def test_exec_sync_raises_when_not_in_outputs(self, mock_course_file, temp_file):
        """exec_sync should raise ValueError if file not in generated_outputs after deletion."""
        mock_course_file.generated_outputs = []  # File not in list

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=temp_file)

        # File is deleted first, then ValueError is raised when trying to remove from list
        with pytest.raises(ValueError):
            op.exec_sync()

        # Verify file was still deleted before the error
        assert not temp_file.exists()

    @pytest.mark.asyncio
    async def test_execute_with_none_backend(self, mock_course_file, temp_file):
        """Execute should work with None backend (not used)."""
        mock_course_file.generated_outputs = [temp_file]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=temp_file)

        # Should not raise
        await op.execute(backend=None)
        assert not temp_file.exists()

    def test_multiple_files_in_generated_outputs(self, mock_course_file, tmp_path):
        """Should only remove the specific file from generated_outputs."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file3 = tmp_path / "file3.txt"

        file1.write_text("1")
        file2.write_text("2")
        file3.write_text("3")

        mock_course_file.generated_outputs = [file1, file2, file3]

        op = DeleteFileOperation(file=mock_course_file, file_to_delete=file2)
        op.exec_sync()

        assert file1 in mock_course_file.generated_outputs
        assert file2 not in mock_course_file.generated_outputs
        assert file3 in mock_course_file.generated_outputs
        assert len(mock_course_file.generated_outputs) == 2
