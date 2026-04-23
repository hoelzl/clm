"""Tests for notebook messaging classes.

Tests cover NotebookPayload, NotebookResult, and helper functions.
"""

import pytest

from clm.infrastructure.messaging.notebook_classes import (
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


class TestExecutionCacheHash:
    """Test execution_cache_hash folds cassette contents when replay is active."""

    def _payload(self, **overrides):
        defaults = {
            "correlation_id": "cid",
            "input_file": "/slides.py",
            "input_file_name": "slides.py",
            "output_file": "/slides.html",
            "data": "cell contents",
            "kind": "speaker",
            "prog_lang": "python",
            "language": "en",
            "format": "html",
        }
        defaults.update(overrides)
        return NotebookPayload(**defaults)

    def test_hash_stable_without_replay(self):
        """Identical payloads without replay must produce identical hashes."""
        p1 = self._payload()
        p2 = self._payload()
        assert p1.execution_cache_hash() == p2.execution_cache_hash()

    def test_hash_changes_when_cassette_bytes_change(self):
        """Refreshing the cassette must invalidate the cache key."""
        p_old = self._payload(
            http_replay_mode="replay",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={"slides.http-cassette.yaml": b"old-cassette-bytes"},
        )
        p_new = self._payload(
            http_replay_mode="replay",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={"slides.http-cassette.yaml": b"new-cassette-bytes"},
        )
        assert p_old.execution_cache_hash() != p_new.execution_cache_hash()

    def test_hash_ignores_cassette_when_mode_disabled(self):
        """``disabled`` mode must not affect the hash."""
        p_none = self._payload()
        p_disabled = self._payload(
            http_replay_mode="disabled",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={"slides.http-cassette.yaml": b"does-not-matter"},
        )
        assert p_none.execution_cache_hash() == p_disabled.execution_cache_hash()

    def test_hash_differs_between_replay_and_no_replay(self):
        """Turning replay on must change the hash even with same source."""
        p_plain = self._payload()
        p_replay = self._payload(
            http_replay_mode="replay",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={"slides.http-cassette.yaml": b"cassette"},
        )
        assert p_plain.execution_cache_hash() != p_replay.execution_cache_hash()


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
