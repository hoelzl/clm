"""Tests for Langfuse tracing integration (Phase 3).

Tests env-var gating, client wrapping/fallback, flush behavior,
and merge-context forwarding.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _langfuse_configured
# ---------------------------------------------------------------------------
class TestLangfuseConfigured:
    """Env-var gating for Langfuse integration."""

    def _configured(self) -> bool:
        from clm.infrastructure.llm.client import _langfuse_configured

        return _langfuse_configured()

    def test_all_vars_set(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert self._configured() is True

    def test_missing_host(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert self._configured() is False

    def test_missing_public_key(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert self._configured() is False

    def test_missing_secret_key(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        assert self._configured() is False

    def test_empty_host_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "")
        monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert self._configured() is False

    def test_empty_public_key_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert self._configured() is False

    def test_base_url_accepted_instead_of_host(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        monkeypatch.setenv("LANGFUSE_BASE_URL", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        assert self._configured() is True

    def test_no_vars_at_all(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        assert self._configured() is False


# ---------------------------------------------------------------------------
# _build_client — Langfuse wrapping
# ---------------------------------------------------------------------------
class TestBuildClientLangfuse:
    """_build_client returns Langfuse-wrapped client when configured."""

    def _no_langfuse_env(self, monkeypatch):
        """Clear all Langfuse env vars."""
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    def _set_langfuse_env(self, monkeypatch):
        """Set all required Langfuse env vars."""
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    def test_plain_client_when_no_env_vars(self, monkeypatch):
        import openai

        from clm.infrastructure.llm.client import _build_client

        self._no_langfuse_env(monkeypatch)
        client = _build_client()
        assert type(client) is openai.AsyncOpenAI

    def test_langfuse_client_when_configured(self, monkeypatch):
        from clm.infrastructure.llm.client import _build_client

        self._set_langfuse_env(monkeypatch)

        # Create a fake langfuse.openai module with a mock AsyncOpenAI
        mock_async_openai_cls = MagicMock(name="LangfuseAsyncOpenAI")
        mock_instance = MagicMock(name="langfuse_client_instance")
        mock_async_openai_cls.return_value = mock_instance

        fake_module = ModuleType("langfuse.openai")
        fake_module.AsyncOpenAI = mock_async_openai_cls  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"langfuse.openai": fake_module}):
            client = _build_client(api_base="http://api.example.com", api_key="test-key")

        assert client is mock_instance
        mock_async_openai_cls.assert_called_once_with(
            base_url="http://api.example.com", api_key="test-key"
        )

    def test_falls_back_when_langfuse_not_installed(self, monkeypatch):
        import openai

        from clm.infrastructure.llm.client import _build_client

        self._set_langfuse_env(monkeypatch)

        # Make langfuse.openai import raise ImportError
        with patch.dict(sys.modules, {"langfuse.openai": None}):
            # None in sys.modules causes ImportError on import
            client = _build_client()

        assert type(client) is openai.AsyncOpenAI

    def test_falls_back_on_langfuse_init_error(self, monkeypatch):
        import openai

        from clm.infrastructure.llm.client import _build_client

        self._set_langfuse_env(monkeypatch)

        # langfuse.openai.AsyncOpenAI() raises during construction
        fake_module = ModuleType("langfuse.openai")
        fake_module.AsyncOpenAI = MagicMock(  # type: ignore[attr-defined]
            side_effect=RuntimeError("Langfuse init failed")
        )

        with patch.dict(sys.modules, {"langfuse.openai": fake_module}):
            client = _build_client()

        assert type(client) is openai.AsyncOpenAI

    def test_api_base_forwarded_to_langfuse_client(self, monkeypatch):
        from clm.infrastructure.llm.client import _build_client

        self._set_langfuse_env(monkeypatch)

        mock_cls = MagicMock()
        fake_module = ModuleType("langfuse.openai")
        fake_module.AsyncOpenAI = mock_cls  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"langfuse.openai": fake_module}):
            _build_client(api_base="https://openrouter.ai/api/v1")

        mock_cls.assert_called_once_with(base_url="https://openrouter.ai/api/v1")


# ---------------------------------------------------------------------------
# flush_langfuse
# ---------------------------------------------------------------------------
class TestFlushLangfuse:
    """flush_langfuse is best-effort and never raises."""

    def test_noop_when_not_configured(self, monkeypatch):
        from clm.infrastructure.llm.client import flush_langfuse

        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

        # Should not raise
        flush_langfuse()

    def test_calls_flush_when_configured(self, monkeypatch):
        from clm.infrastructure.llm.client import flush_langfuse

        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        mock_client = MagicMock()
        mock_get_client = MagicMock(return_value=mock_client)

        fake_langfuse = ModuleType("langfuse")
        fake_langfuse.get_client = mock_get_client  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"langfuse": fake_langfuse}):
            flush_langfuse()

        mock_get_client.assert_called_once()
        mock_client.flush.assert_called_once()

    def test_swallows_exception(self, monkeypatch):
        from clm.infrastructure.llm.client import flush_langfuse

        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        fake_langfuse = ModuleType("langfuse")
        fake_langfuse.get_client = MagicMock(  # type: ignore[attr-defined]
            side_effect=RuntimeError("connection refused")
        )

        with patch.dict(sys.modules, {"langfuse": fake_langfuse}):
            # Must not raise
            flush_langfuse()


# ---------------------------------------------------------------------------
# Merge context forwarding
# ---------------------------------------------------------------------------
class TestMergeContextForwarding:
    """Langfuse context is forwarded to create() calls."""

    def _make_mock_client(self, response_json: str) -> MagicMock:
        """Build a mock OpenAI client that returns canned JSON."""
        mock_message = MagicMock()
        mock_message.content = response_json
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        return mock_client

    def test_polish_and_merge_forwards_langfuse_context(self):
        import json

        from clm.voiceover.merge import polish_and_merge

        response = json.dumps(
            {
                "slide_id": "test/0",
                "merged_bullets": "- bullet",
                "rewrites": [],
                "dropped_from_transcript": [],
            }
        )
        mock_client = self._make_mock_client(response)
        langfuse_ctx = {
            "name": "test_generation",
            "trace_id": "trace-abc-123",
            "metadata": {
                "langfuse_session_id": "session-xyz",
                "langfuse_tags": ["test"],
            },
        }

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = asyncio.run(
                polish_and_merge(
                    "- existing",
                    "transcript text",
                    slide_id="test/0",
                    langfuse_context=langfuse_ctx,
                )
            )

        # Verify create() was called with langfuse kwargs
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("name") == "test_generation"
        assert call_kwargs.kwargs.get("trace_id") == "trace-abc-123"
        assert call_kwargs.kwargs.get("metadata") == langfuse_ctx["metadata"]
        assert result.merged_bullets == "- bullet"

    def test_polish_and_merge_no_context_when_none(self):
        import json

        from clm.voiceover.merge import polish_and_merge

        response = json.dumps(
            {
                "slide_id": "test/0",
                "merged_bullets": "- bullet",
                "rewrites": [],
                "dropped_from_transcript": [],
            }
        )
        mock_client = self._make_mock_client(response)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            asyncio.run(
                polish_and_merge(
                    "- existing",
                    "transcript text",
                    slide_id="test/0",
                    langfuse_context=None,
                )
            )

        # Verify create() was NOT called with langfuse kwargs
        call_kwargs = mock_client.chat.completions.create.call_args
        assert "name" not in call_kwargs.kwargs
        assert "trace_id" not in call_kwargs.kwargs
        assert "metadata" not in call_kwargs.kwargs

    def test_merge_batch_forwards_langfuse_context(self):
        import json

        from clm.voiceover.merge import SlideInput, merge_batch

        response = json.dumps(
            [
                {
                    "slide_id": "test/0",
                    "merged_bullets": "- a",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                },
                {
                    "slide_id": "test/1",
                    "merged_bullets": "- b",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                },
            ]
        )
        mock_client = self._make_mock_client(response)
        langfuse_ctx = {
            "name": "voiceover_merge_batch",
            "trace_id": "trace-batch-001",
            "metadata": {"langfuse_session_id": "sess-001"},
        }

        slides = [
            SlideInput(slide_id="test/0", baseline="- old", transcript="new", slide_content=""),
            SlideInput(slide_id="test/1", baseline="- old2", transcript="new2", slide_content=""),
        ]

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            results = asyncio.run(merge_batch(slides, language="en", langfuse_context=langfuse_ctx))

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("name") == "voiceover_merge_batch"
        assert call_kwargs.kwargs.get("trace_id") == "trace-batch-001"
        assert len(results) == 2

    def test_merge_batch_single_slide_forwards_context(self):
        """Single-slide batch delegates to polish_and_merge with context."""
        import json

        from clm.voiceover.merge import SlideInput, merge_batch

        response = json.dumps(
            {
                "slide_id": "test/0",
                "merged_bullets": "- result",
                "rewrites": [],
                "dropped_from_transcript": [],
            }
        )
        mock_client = self._make_mock_client(response)
        langfuse_ctx = {"name": "test", "trace_id": "t-single"}

        slides = [
            SlideInput(slide_id="test/0", baseline="- old", transcript="new", slide_content=""),
        ]

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            results = asyncio.run(merge_batch(slides, language="en", langfuse_context=langfuse_ctx))

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("trace_id") == "t-single"
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Trace log records langfuse_trace_id
# ---------------------------------------------------------------------------
class TestTraceLogLangfuseId:
    """Verify trace log records langfuse_trace_id when provided."""

    def test_trace_id_written_to_jsonl(self, tmp_path):
        import json

        from clm.voiceover.trace_log import TraceLog

        trace = TraceLog(
            path=tmp_path / "test.jsonl",
            slide_file="slides.py",
            git_head="abc123",
        )
        trace.log_merge_call(
            slide_id="test/0",
            language="de",
            baseline="- old",
            transcript="new text",
            llm_merged="- merged",
            rewrites=[],
            dropped_from_transcript=[],
            langfuse_trace_id="langfuse-trace-xyz-789",
        )

        lines = trace.path.read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["langfuse_trace_id"] == "langfuse-trace-xyz-789"

    def test_trace_id_omitted_when_none(self, tmp_path):
        import json

        from clm.voiceover.trace_log import TraceLog

        trace = TraceLog(
            path=tmp_path / "test.jsonl",
            slide_file="slides.py",
            git_head="abc123",
        )
        trace.log_merge_call(
            slide_id="test/0",
            language="de",
            baseline="- old",
            transcript="new text",
            llm_merged="- merged",
            rewrites=[],
            dropped_from_transcript=[],
            langfuse_trace_id=None,
        )

        lines = trace.path.read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[0])
        assert "langfuse_trace_id" not in entry
