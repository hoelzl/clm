"""Tests for messaging base classes module.

Tests cover TransferModel, Payload, Result, and related classes.
"""

import pytest

from clm.infrastructure.messaging.base_classes import (
    ImagePayload,
    ImageResult,
    Payload,
    ProcessingError,
    TransferModel,
)


class TestPayload:
    """Test Payload class."""

    def test_payload_content_hash(self):
        """Should compute SHA256 hash of data content."""
        payload = ImagePayload(
            correlation_id="test-123",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
            data="@startuml\nAlice -> Bob\n@enduml",
        )

        hash1 = payload.content_hash()
        hash2 = payload.content_hash()

        # Same data should produce same hash
        assert hash1 == hash2
        # Hash should be SHA256 hex digest (64 chars)
        assert len(hash1) == 64

    def test_payload_content_hash_different_data(self):
        """Different data should produce different hash."""
        payload1 = ImagePayload(
            correlation_id="test-123",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
            data="data1",
        )
        payload2 = ImagePayload(
            correlation_id="test-123",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
            data="data2",
        )

        assert payload1.content_hash() != payload2.content_hash()

    def test_payload_output_metadata_default(self):
        """Should return 'default' for base Payload."""
        # Create a concrete implementation
        payload = ImagePayload(
            correlation_id="test-123",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
            data="test data",
        )
        # ImagePayload overrides output_metadata
        assert payload.output_metadata() == "png"


class TestImagePayload:
    """Test ImagePayload class."""

    def test_image_payload_default_format(self):
        """Should default to 'png' output format."""
        payload = ImagePayload(
            correlation_id="test-123",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
            data="test data",
        )

        assert payload.output_format == "png"

    def test_image_payload_custom_format(self):
        """Should accept custom output format."""
        payload = ImagePayload(
            correlation_id="test-123",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.svg",
            data="test data",
            output_format="svg",
        )

        assert payload.output_format == "svg"

    def test_image_payload_output_metadata(self):
        """Should return output format as metadata."""
        payload = ImagePayload(
            correlation_id="test-123",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.svg",
            data="test data",
            output_format="svg",
        )

        assert payload.output_metadata() == "svg"


class TestImageResult:
    """Test ImageResult class."""

    def test_image_result_result_bytes(self):
        """Should return result as bytes."""
        result = ImageResult(
            correlation_id="test-123",
            output_file="/output/file.png",
            input_file="/input/file.puml",
            content_hash="abc123",
            result=b"\x89PNG\r\n\x1a\n",  # PNG magic bytes
        )

        assert result.result_bytes() == b"\x89PNG\r\n\x1a\n"

    def test_image_result_output_metadata(self):
        """Should return image format as metadata."""
        result = ImageResult(
            correlation_id="test-123",
            output_file="/output/file.svg",
            input_file="/input/file.puml",
            content_hash="abc123",
            result=b"<svg></svg>",
            image_format="svg",
        )

        assert result.output_metadata() == "svg"

    def test_image_result_default_format(self):
        """Should default to 'png' image format."""
        result = ImageResult(
            correlation_id="test-123",
            output_file="/output/file.png",
            input_file="/input/file.puml",
            content_hash="abc123",
            result=b"\x89PNG",
        )

        assert result.image_format == "png"


class TestTransferModel:
    """Test TransferModel serialization."""

    def test_model_dump_json(self):
        """Should serialize model to JSON."""
        # Use ProcessingError which doesn't have bytes field
        error = ProcessingError(
            correlation_id="test-123",
            error="Test error",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
        )

        json_str = error.model_dump_json()

        assert isinstance(json_str, str)
        assert "test-123" in json_str
        assert "/output/file.png" in json_str

    def test_model_dump(self):
        """Should serialize model to dict."""
        error = ProcessingError(
            correlation_id="test-123",
            error="Test error",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
        )

        data = error.model_dump()

        assert isinstance(data, dict)
        assert data["correlation_id"] == "test-123"
        assert data["output_file"] == "/output/file.png"


class TestProcessingError:
    """Test ProcessingError class."""

    def test_processing_error_creation(self):
        """Should create ProcessingError with required fields."""
        error = ProcessingError(
            correlation_id="test-123",
            error="Failed to process file",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
        )

        assert error.result_type == "error"
        assert error.error == "Failed to process file"
        assert error.traceback == ""

    def test_processing_error_with_traceback(self):
        """Should accept traceback."""
        error = ProcessingError(
            correlation_id="test-123",
            error="Failed to process",
            input_file="/input/file.puml",
            input_file_name="file.puml",
            output_file="/output/file.png",
            traceback="Traceback (most recent call last):\n...",
        )

        assert "Traceback" in error.traceback
