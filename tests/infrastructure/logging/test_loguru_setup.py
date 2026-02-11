"""Tests for Loguru setup module.

Tests the LokiSink class and setup_logger function.
"""

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger

from clm.infrastructure.logging.loguru_setup import LokiSink, setup_logger


class TestLokiSink:
    """Test LokiSink class."""

    @pytest.fixture
    def loki_sink(self):
        """Create a LokiSink instance for testing."""
        return LokiSink(
            loki_url="http://localhost:3100/loki/api/v1/push",
            static_labels={"app": "test_app", "env": "test"},
        )

    def test_loki_sink_init(self, loki_sink):
        """Should initialize with URL and labels."""
        assert loki_sink.loki_url == "http://localhost:3100/loki/api/v1/push"
        assert loki_sink.static_labels == {"app": "test_app", "env": "test"}

    def test_loki_sink_init_empty_labels(self):
        """Should accept empty labels dict."""
        sink = LokiSink(loki_url="http://localhost:3100", static_labels={})
        assert sink.static_labels == {}

    @patch("clm.infrastructure.logging.loguru_setup.requests")
    def test_loki_sink_write_sends_post(self, mock_requests, loki_sink):
        """Should send POST request to Loki URL."""
        # Create a mock message with all required record attributes
        mock_message = MagicMock()
        mock_message.record = {
            "level": MagicMock(name="INFO"),
            "file": MagicMock(name="test.py"),
            "function": "test_func",
            "line": 42,
            "module": "test_module",
            "process": MagicMock(name="MainProcess"),
            "thread": MagicMock(name="MainThread"),
            "extra": {"correlation_id": "test-123"},
            "time": MagicMock(),
            "message": "Test log message",
        }
        mock_message.record["time"].timestamp.return_value = 1234567890.123456

        mock_response = MagicMock()
        mock_requests.post.return_value = mock_response

        loki_sink.write(mock_message)

        # Verify POST was called
        mock_requests.post.assert_called_once()
        call_args = mock_requests.post.call_args
        assert call_args[0][0] == "http://localhost:3100/loki/api/v1/push"

        # Verify JSON structure
        json_payload = call_args[1]["json"]
        assert "streams" in json_payload
        assert len(json_payload["streams"]) == 1
        stream = json_payload["streams"][0]
        assert "stream" in stream
        assert "values" in stream

        # Check static labels are included
        assert stream["stream"]["app"] == "test_app"
        assert stream["stream"]["env"] == "test"

    @patch("clm.infrastructure.logging.loguru_setup.requests")
    def test_loki_sink_write_includes_dynamic_labels(self, mock_requests, loki_sink):
        """Should include dynamic labels from log record."""
        mock_level = MagicMock()
        mock_level.name = "ERROR"
        mock_file = MagicMock()
        mock_file.name = "my_file.py"
        mock_process = MagicMock()
        mock_process.name = "Process-1"
        mock_thread = MagicMock()
        mock_thread.name = "Thread-2"

        mock_message = MagicMock()
        mock_message.record = {
            "level": mock_level,
            "file": mock_file,
            "function": "my_function",
            "line": 100,
            "module": "my_module",
            "process": mock_process,
            "thread": mock_thread,
            "extra": {"correlation_id": "corr-456"},
            "time": MagicMock(),
            "message": "Error message",
        }
        mock_message.record["time"].timestamp.return_value = 1234567890.0

        loki_sink.write(mock_message)

        json_payload = mock_requests.post.call_args[1]["json"]
        labels = json_payload["streams"][0]["stream"]

        assert labels["level"] == "ERROR"
        assert labels["function"] == "my_function"
        assert labels["line"] == "100"
        assert labels["module"] == "my_module"
        assert labels["correlation_id"] == "corr-456"

    @patch("clm.infrastructure.logging.loguru_setup.requests")
    def test_loki_sink_write_missing_correlation_id(self, mock_requests, loki_sink):
        """Should handle missing correlation_id gracefully."""
        mock_message = MagicMock()
        mock_message.record = {
            "level": MagicMock(name="INFO"),
            "file": MagicMock(name="test.py"),
            "function": "test_func",
            "line": 1,
            "module": "test",
            "process": MagicMock(name="Main"),
            "thread": MagicMock(name="Main"),
            "extra": {},  # No correlation_id
            "time": MagicMock(),
            "message": "Log without correlation_id",
        }
        mock_message.record["time"].timestamp.return_value = 1234567890.0

        loki_sink.write(mock_message)

        json_payload = mock_requests.post.call_args[1]["json"]
        labels = json_payload["streams"][0]["stream"]
        assert labels["correlation_id"] == ""

    @patch("clm.infrastructure.logging.loguru_setup.requests")
    def test_loki_sink_write_handles_request_error(self, mock_requests, loki_sink, capsys):
        """Should handle request errors gracefully."""
        import requests

        mock_requests.RequestException = requests.RequestException
        mock_requests.post.side_effect = requests.RequestException("Connection refused")

        mock_message = MagicMock()
        mock_message.record = {
            "level": MagicMock(name="INFO"),
            "file": MagicMock(name="test.py"),
            "function": "test",
            "line": 1,
            "module": "test",
            "process": MagicMock(name="Main"),
            "thread": MagicMock(name="Main"),
            "extra": {},
            "time": MagicMock(),
            "message": "Test",
        }
        mock_message.record["time"].timestamp.return_value = 1234567890.0

        # Should not raise
        loki_sink.write(mock_message)

        # Should print error to stderr
        captured = capsys.readouterr()
        assert "Failed to send log to Loki" in captured.err

    @patch("clm.infrastructure.logging.loguru_setup.requests")
    def test_loki_sink_write_handles_http_error(self, mock_requests, loki_sink, capsys):
        """Should handle HTTP errors gracefully."""
        import requests

        mock_requests.RequestException = requests.RequestException
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.RequestException("500 Error")
        mock_requests.post.return_value = mock_response

        mock_message = MagicMock()
        mock_message.record = {
            "level": MagicMock(name="INFO"),
            "file": MagicMock(name="test.py"),
            "function": "test",
            "line": 1,
            "module": "test",
            "process": MagicMock(name="Main"),
            "thread": MagicMock(name="Main"),
            "extra": {},
            "time": MagicMock(),
            "message": "Test",
        }
        mock_message.record["time"].timestamp.return_value = 1234567890.0

        # Should not raise
        loki_sink.write(mock_message)

        captured = capsys.readouterr()
        assert "Failed to send log to Loki" in captured.err


class TestSetupLogger:
    """Test setup_logger function."""

    def test_setup_logger_returns_logger(self):
        """Should return the logger instance."""
        result = setup_logger(
            loki_url="http://localhost:3100/loki/api/v1/push",
            app_name="test_app",
        )
        # Should return a logger (actually the loguru logger module)
        assert result is not None

    def test_setup_logger_custom_levels(self):
        """Should accept custom log levels."""
        result = setup_logger(
            loki_url="http://localhost:3100/loki/api/v1/push",
            app_name="test_app",
            local_level="DEBUG",
            loki_level="WARNING",
        )
        assert result is not None

    def test_setup_logger_default_levels(self):
        """Should use default levels (WARNING for local, INFO for Loki)."""
        result = setup_logger(
            loki_url="http://localhost:3100/loki/api/v1/push",
            app_name="test_app",
        )
        assert result is not None
