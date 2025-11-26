"""Tests for correlation ID management.

Tests the correlation ID tracking system including:
- Creating new correlation IDs
- Tracking dependencies
- Removing correlation IDs
- Cleaning up stale IDs
"""

import asyncio
from time import time
from unittest.mock import MagicMock

import pytest

from clx.infrastructure.messaging.correlation_ids import (
    CorrelationData,
    active_correlation_ids,
    all_correlation_ids,
    clear_correlation_ids,
    format_dependency,
    new_correlation_id,
    note_correlation_id_dependency,
    remove_correlation_id,
    remove_stale_correlation_ids,
)
from clx.infrastructure.messaging.notebook_classes import NotebookPayload, NotebookResult


@pytest.fixture(autouse=True)
async def clear_ids():
    """Clear correlation IDs before and after each test."""
    await clear_correlation_ids()
    yield
    await clear_correlation_ids()


class TestFormatDependency:
    """Test format_dependency function."""

    def test_format_notebook_result(self):
        """Should format NotebookResult correctly."""
        result = NotebookResult(
            output_file="/path/to/output.html",
            input_file="/path/to/input.ipynb",
            content_hash="abc123",
            correlation_id="test-123",
            result="<html>content</html>",
            output_metadata_tags=("completed", "python", "en", "html"),
        )
        formatted = format_dependency(result)
        assert "NR(" in formatted
        assert "output.html" in formatted

    def test_format_notebook_payload(self):
        """Should format NotebookPayload correctly."""
        payload = NotebookPayload(
            input_file="/path/to/input.ipynb",
            input_file_name="input.ipynb",
            output_file="/path/to/output.html",
            data="notebook content",
            correlation_id="test-123",
            kind="completed",
            prog_lang="python",
            language="en",
            format="html",
        )
        formatted = format_dependency(payload)
        assert "NP(" in formatted
        assert "output.html" in formatted
        assert "completed" in formatted
        assert "python" in formatted

    def test_format_unknown_type(self):
        """Should return type name for unknown types."""
        mock_dep = MagicMock()
        mock_dep.output_file = "/path/to/file"
        formatted = format_dependency(mock_dep)
        assert "MagicMock" in formatted


class TestCorrelationData:
    """Test CorrelationData class."""

    def test_correlation_data_creation(self):
        """Should create CorrelationData with required fields."""
        data = CorrelationData(correlation_id="test-123")
        assert data.correlation_id == "test-123"
        assert data.task is None
        assert isinstance(data.start_time, float)
        assert data.dependencies == []

    def test_correlation_data_with_task(self):
        """Should accept task parameter."""
        mock_task = MagicMock()
        data = CorrelationData(correlation_id="test-123", task=mock_task)
        assert data.task == mock_task

    def test_format_dependencies_empty(self):
        """Should return empty string for no dependencies."""
        data = CorrelationData(correlation_id="test-123")
        assert data.format_dependencies() == ""

    def test_format_dependencies_with_items(self):
        """Should format multiple dependencies."""
        result = NotebookResult(
            output_file="/path/to/output1.html",
            input_file="/path/to/input1.ipynb",
            content_hash="abc123",
            correlation_id="test-1",
            result="<html>content</html>",
            output_metadata_tags=("completed", "python", "en", "html"),
        )
        payload = NotebookPayload(
            input_file="/path/to/input.ipynb",
            input_file_name="input.ipynb",
            output_file="/path/to/output2.html",
            data="notebook content",
            correlation_id="test-2",
            kind="completed",
            prog_lang="python",
            language="en",
            format="html",
        )
        data = CorrelationData(
            correlation_id="test-123",
            dependencies=[result, payload],
        )
        formatted = data.format_dependencies()
        assert "NR(" in formatted
        assert "NP(" in formatted
        assert ", " in formatted  # Items separated by comma


class TestNewCorrelationId:
    """Test new_correlation_id function."""

    @pytest.mark.asyncio
    async def test_creates_unique_id(self):
        """Should create a unique correlation ID."""
        cid1 = await new_correlation_id()
        cid2 = await new_correlation_id()
        assert cid1 != cid2

    @pytest.mark.asyncio
    async def test_adds_to_active_ids(self):
        """Should add ID to active_correlation_ids."""
        cid = await new_correlation_id()
        assert cid in active_correlation_ids

    @pytest.mark.asyncio
    async def test_adds_to_all_ids(self):
        """Should add ID to all_correlation_ids."""
        cid = await new_correlation_id()
        assert cid in all_correlation_ids

    @pytest.mark.asyncio
    async def test_stores_correlation_data(self):
        """Should store CorrelationData with the ID."""
        cid = await new_correlation_id()
        data = active_correlation_ids[cid]
        assert isinstance(data, CorrelationData)
        assert data.correlation_id == cid

    @pytest.mark.asyncio
    async def test_accepts_custom_task(self):
        """Should accept a custom task parameter."""
        mock_task = MagicMock()
        cid = await new_correlation_id(task=mock_task)
        data = active_correlation_ids[cid]
        assert data.task == mock_task


class TestClearCorrelationIds:
    """Test clear_correlation_ids function."""

    @pytest.mark.asyncio
    async def test_clears_active_ids(self):
        """Should clear active_correlation_ids."""
        await new_correlation_id()
        assert len(active_correlation_ids) > 0

        await clear_correlation_ids()
        assert len(active_correlation_ids) == 0

    @pytest.mark.asyncio
    async def test_clears_all_ids(self):
        """Should clear all_correlation_ids."""
        await new_correlation_id()
        assert len(all_correlation_ids) > 0

        await clear_correlation_ids()
        assert len(all_correlation_ids) == 0


class TestNoteCorrelationIdDependency:
    """Test note_correlation_id_dependency function."""

    @pytest.mark.asyncio
    async def test_adds_dependency(self):
        """Should add dependency to correlation data."""
        cid = await new_correlation_id()
        dep = NotebookResult(
            output_file="/path/to/output.html",
            input_file="/path/to/input.ipynb",
            content_hash="abc123",
            correlation_id="dep-1",
            result="<html>content</html>",
            output_metadata_tags=("completed", "python", "en", "html"),
        )

        await note_correlation_id_dependency(cid, dep)

        data = active_correlation_ids[cid]
        assert dep in data.dependencies

    @pytest.mark.asyncio
    async def test_ignores_nonexistent_id(self, caplog):
        """Should log error for non-existent correlation ID."""
        import logging

        with caplog.at_level(logging.ERROR, logger="clx.infrastructure.messaging.correlation_ids"):
            await note_correlation_id_dependency("nonexistent-id", MagicMock())

        assert "non-existent correlation_id" in caplog.text

    @pytest.mark.asyncio
    async def test_warns_inactive_id(self, caplog):
        """Should warn when adding dependency to inactive ID."""
        import logging

        cid = await new_correlation_id()
        await remove_correlation_id(cid)
        # ID is now in all_correlation_ids but not active_correlation_ids

        dep = MagicMock()
        dep.output_file = "/path/to/file"

        with caplog.at_level(
            logging.WARNING, logger="clx.infrastructure.messaging.correlation_ids"
        ):
            await note_correlation_id_dependency(cid, dep)

        assert "inactive correlation ID" in caplog.text

    @pytest.mark.asyncio
    async def test_no_duplicate_dependencies(self):
        """Should not add duplicate dependencies."""
        cid = await new_correlation_id()
        dep = NotebookResult(
            output_file="/path/to/output.html",
            input_file="/path/to/input.ipynb",
            content_hash="abc123",
            correlation_id="dep-1",
            result="<html>content</html>",
            output_metadata_tags=("completed", "python", "en", "html"),
        )

        await note_correlation_id_dependency(cid, dep)
        await note_correlation_id_dependency(cid, dep)

        data = active_correlation_ids[cid]
        assert len(data.dependencies) == 1


class TestRemoveCorrelationId:
    """Test remove_correlation_id function."""

    @pytest.mark.asyncio
    async def test_removes_from_active(self):
        """Should remove from active_correlation_ids."""
        cid = await new_correlation_id()
        assert cid in active_correlation_ids

        await remove_correlation_id(cid)
        assert cid not in active_correlation_ids

    @pytest.mark.asyncio
    async def test_keeps_in_all(self):
        """Should keep ID in all_correlation_ids."""
        cid = await new_correlation_id()
        await remove_correlation_id(cid)
        assert cid in all_correlation_ids

    @pytest.mark.asyncio
    async def test_handles_none_id(self, caplog):
        """Should log error for None correlation ID."""
        import logging

        with caplog.at_level(logging.ERROR, logger="clx.infrastructure.messaging.correlation_ids"):
            await remove_correlation_id(None)

        assert "Missing correlation ID" in caplog.text

    @pytest.mark.asyncio
    async def test_handles_missing_id(self, caplog):
        """Should handle non-existent ID gracefully."""
        import logging

        # Should not raise
        with caplog.at_level(logging.DEBUG, logger="clx.infrastructure.messaging.correlation_ids"):
            await remove_correlation_id("nonexistent-id")

        assert "does not exist" in caplog.text

    @pytest.mark.asyncio
    async def test_without_lock(self):
        """Should work without locking when specified."""
        cid = await new_correlation_id()
        await remove_correlation_id(cid, lock_correlation_ids=False)
        assert cid not in active_correlation_ids


class TestRemoveStaleCorrelationIds:
    """Test remove_stale_correlation_ids function."""

    @pytest.mark.asyncio
    async def test_removes_old_ids(self):
        """Should remove IDs older than max_lifetime."""
        cid = await new_correlation_id()

        # Make the ID appear old
        active_correlation_ids[cid].start_time = time() - 2000

        await remove_stale_correlation_ids(max_lifetime=1200.0)

        assert cid not in active_correlation_ids

    @pytest.mark.asyncio
    async def test_keeps_recent_ids(self):
        """Should keep IDs newer than max_lifetime."""
        cid = await new_correlation_id()

        await remove_stale_correlation_ids(max_lifetime=1200.0)

        assert cid in active_correlation_ids

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self, caplog):
        """Should handle errors gracefully."""
        import logging

        # This should not raise even if something goes wrong
        await remove_stale_correlation_ids(max_lifetime=1200.0)
