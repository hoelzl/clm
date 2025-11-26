"""Tests for File class module.

Tests cover the base File class and its methods.
"""

from pathlib import Path

import pytest

from clx.infrastructure.operation import NoOperation
from clx.infrastructure.utils.file import File


class TestFile:
    """Test File class."""

    def test_file_creation(self, tmp_path):
        """Should create File with path."""
        file_path = tmp_path / "test.txt"
        f = File(path=file_path)

        assert f.path == file_path

    @pytest.mark.asyncio
    async def test_get_processing_operation_returns_no_operation(self, tmp_path):
        """Should return NoOperation by default."""
        file_path = tmp_path / "test.txt"
        f = File(path=file_path)

        result = await f.get_processing_operation(tmp_path)

        assert isinstance(result, NoOperation)

    @pytest.mark.asyncio
    async def test_get_processing_operation_with_stage(self, tmp_path):
        """Should return NoOperation regardless of stage."""
        file_path = tmp_path / "test.txt"
        f = File(path=file_path)

        result = await f.get_processing_operation(tmp_path, stage=1)

        assert isinstance(result, NoOperation)

    @pytest.mark.asyncio
    async def test_get_processing_operation_different_target_dir(self, tmp_path):
        """Should accept different target directories."""
        file_path = tmp_path / "test.txt"
        target_dir = tmp_path / "output"
        f = File(path=file_path)

        result = await f.get_processing_operation(target_dir)

        assert isinstance(result, NoOperation)
