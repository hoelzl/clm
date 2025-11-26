"""Tests for DummyBackend module.

Tests cover the dummy/no-op backend implementation.
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clx.infrastructure.backends.dummy_backend import DummyBackend
from clx.infrastructure.utils.copy_dir_group_data import CopyDirGroupData
from clx.infrastructure.utils.copy_file_data import CopyFileData
from clx.infrastructure.utils.file import File


class TestDummyBackend:
    """Test DummyBackend class."""

    @pytest.mark.asyncio
    async def test_execute_operation_logs_and_returns(self, caplog):
        """Should log operation and return without executing."""
        mock_operation = MagicMock()
        mock_payload = MagicMock()

        with caplog.at_level(logging.INFO, logger="clx.infrastructure.backends.dummy_backend"):
            async with DummyBackend() as backend:
                await backend.execute_operation(mock_operation, mock_payload)

        assert "Skipping operation" in caplog.text

    @pytest.mark.asyncio
    async def test_wait_for_completion_returns_true(self, caplog):
        """Should log and return True."""
        with caplog.at_level(logging.INFO, logger="clx.infrastructure.backends.dummy_backend"):
            async with DummyBackend() as backend:
                result = await backend.wait_for_completion()

        assert result is True
        assert "Waiting for completion" in caplog.text

    @pytest.mark.asyncio
    async def test_copy_file_to_output_logs(self, tmp_path, caplog):
        """Should log copy operation without actually copying."""
        copy_data = CopyFileData(
            input_path=tmp_path / "input.txt",
            output_path=tmp_path / "output.txt",
            relative_input_path=Path("input.txt"),
        )

        with caplog.at_level(logging.INFO, logger="clx.infrastructure.backends.dummy_backend"):
            async with DummyBackend() as backend:
                await backend.copy_file_to_output(copy_data)

        assert "Copying file to output" in caplog.text

    @pytest.mark.asyncio
    async def test_copy_dir_group_to_output_logs(self, tmp_path, caplog):
        """Should log dir group copy without actually copying."""
        copy_data = CopyDirGroupData(
            name="test-group",
            source_dirs=(tmp_path,),
            relative_paths=(Path("test"),),
            lang="en",
            output_dir=tmp_path / "output",
        )

        with caplog.at_level(logging.INFO, logger="clx.infrastructure.backends.dummy_backend"):
            async with DummyBackend() as backend:
                await backend.copy_dir_group_to_output(copy_data)

        assert "Copying dir-group to output" in caplog.text

    @pytest.mark.asyncio
    async def test_delete_dependencies_logs(self, caplog):
        """Should log delete operation without actually deleting."""
        mock_file = MagicMock(spec=File)
        mock_file.path = Path("/test/file.txt")

        with caplog.at_level(logging.INFO, logger="clx.infrastructure.backends.dummy_backend"):
            async with DummyBackend() as backend:
                await backend.delete_dependencies(mock_file)

        assert "Deleting dependencies" in caplog.text

    @pytest.mark.asyncio
    async def test_delete_file_logs(self, caplog):
        """Should log file deletion without actually deleting."""
        file_path = Path("/test/file.txt")

        with caplog.at_level(logging.INFO, logger="clx.infrastructure.backends.dummy_backend"):
            async with DummyBackend() as backend:
                await backend.delete_file(file_path)

        assert "Deleting file" in caplog.text
