"""Tests for notebook messaging classes.

Tests cover NotebookPayload, NotebookResult, and helper functions.
"""

import pytest

from clx.infrastructure.messaging.notebook_classes import (
    NotebookPayload,
    NotebookResult,
    notebook_metadata,
    notebook_metadata_tags,
)


class TestNotebookMetadataFunctions:
    """Test notebook metadata helper functions."""

    def test_notebook_metadata(self):
        """Should format metadata as colon-separated string."""
        result = notebook_metadata("completed", "python", "en", "html")
        assert result == "completed:python:en:html"

    def test_notebook_metadata_tags(self):
        """Should return tuple of metadata tags."""
        result = notebook_metadata_tags("completed", "python", "en", "html")

        assert result == ("completed", "python", "en", "html")
        assert isinstance(result, tuple)


class TestNotebookPayload:
    """Test NotebookPayload class."""

    @pytest.fixture
    def sample_payload(self):
        """Create a sample NotebookPayload for testing."""
        return NotebookPayload(
            correlation_id="test-123",
            input_file="/path/to/input.ipynb",
            input_file_name="input.ipynb",
            output_file="/path/to/output.html",
            data="notebook content here",
            kind="completed",
            prog_lang="python",
            language="en",
            format="html",
        )

    def test_payload_creation(self, sample_payload):
        """Should create NotebookPayload with all fields."""
        assert sample_payload.correlation_id == "test-123"
        assert sample_payload.kind == "completed"
        assert sample_payload.prog_lang == "python"
        assert sample_payload.language == "en"
        assert sample_payload.format == "html"

    def test_payload_default_values(self, sample_payload):
        """Should have default values for optional fields."""
        assert sample_payload.template_dir == ""
        assert sample_payload.other_files == {}
        assert sample_payload.fallback_execute is False

    def test_notebook_text_property(self, sample_payload):
        """Should return data as notebook_text property."""
        assert sample_payload.notebook_text == "notebook content here"

    def test_content_hash(self, sample_payload):
        """Should compute content hash including metadata."""
        hash1 = sample_payload.content_hash()
        hash2 = sample_payload.content_hash()

        # Same payload should produce same hash
        assert hash1 == hash2
        # Hash should be SHA256 hex digest (64 chars)
        assert len(hash1) == 64

    def test_content_hash_differs_with_metadata(self):
        """Different metadata should produce different hash."""
        payload1 = NotebookPayload(
            correlation_id="test-1",
            input_file="/input.ipynb",
            input_file_name="input.ipynb",
            output_file="/output.html",
            data="same content",
            kind="completed",
            prog_lang="python",
            language="en",
            format="html",
        )
        payload2 = NotebookPayload(
            correlation_id="test-2",
            input_file="/input.ipynb",
            input_file_name="input.ipynb",
            output_file="/output.html",
            data="same content",  # Same content but different format
            kind="completed",
            prog_lang="python",
            language="en",
            format="slides",  # Different format
        )

        # Different metadata should produce different hash
        assert payload1.content_hash() != payload2.content_hash()

    def test_output_metadata(self, sample_payload):
        """Should return formatted metadata string."""
        assert sample_payload.output_metadata() == "completed:python:en:html"


class TestNotebookResult:
    """Test NotebookResult class."""

    @pytest.fixture
    def sample_result(self):
        """Create a sample NotebookResult for testing."""
        return NotebookResult(
            correlation_id="test-123",
            output_file="/path/to/output.html",
            input_file="/path/to/input.ipynb",
            content_hash="abc123def456",
            result="<html><body>Notebook content</body></html>",
            output_metadata_tags=("completed", "python", "en", "html"),
        )

    def test_result_creation(self, sample_result):
        """Should create NotebookResult with all fields."""
        assert sample_result.correlation_id == "test-123"
        assert sample_result.result_type == "result"
        assert sample_result.content_hash == "abc123def456"

    def test_result_bytes(self, sample_result):
        """Should return result as UTF-8 encoded bytes."""
        result_bytes = sample_result.result_bytes()

        assert isinstance(result_bytes, bytes)
        assert result_bytes == b"<html><body>Notebook content</body></html>"

    def test_output_metadata(self, sample_result):
        """Should return metadata tags joined by colon."""
        assert sample_result.output_metadata() == "completed:python:en:html"

    def test_output_metadata_tags_tuple(self, sample_result):
        """Should have output_metadata_tags as tuple."""
        tags = sample_result.output_metadata_tags

        assert isinstance(tags, tuple)
        assert len(tags) == 4
        assert tags == ("completed", "python", "en", "html")
