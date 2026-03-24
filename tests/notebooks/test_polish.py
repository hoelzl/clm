"""Tests for the polish module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.notebooks.polish import SYSTEM_PROMPT, _build_user_prompt


class TestBuildUserPrompt:
    def test_with_slide_content(self):
        prompt = _build_user_prompt("Some notes.", "Slide about Python")
        assert "Some notes." in prompt
        assert "Slide about Python" in prompt
        assert "context" in prompt.lower()

    def test_without_slide_content(self):
        prompt = _build_user_prompt("Some notes.", "")
        assert "Some notes." in prompt
        assert "context" not in prompt.lower()

    def test_strips_whitespace(self):
        prompt = _build_user_prompt("  notes  ", "  content  ")
        assert "notes" in prompt
        assert "content" in prompt


class TestSystemPrompt:
    def test_contains_key_instructions(self):
        assert "filler words" in SYSTEM_PROMPT.lower()
        assert "technical terms" in SYSTEM_PROMPT.lower()
        assert "[Revisited]" in SYSTEM_PROMPT


class TestPolishText:
    def _make_mock_response(self, content: str) -> MagicMock:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = content
        return mock_response

    def _patch_client(self, mock_create):
        """Patch _build_client to return a mock AsyncOpenAI client."""
        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create
        return patch("clm.infrastructure.llm.client._build_client", return_value=mock_client)

    def test_basic_call(self):
        from clm.notebooks.polish import polish_text

        mock_create = AsyncMock(return_value=self._make_mock_response("- Polished text."))

        with self._patch_client(mock_create):
            result = asyncio.run(polish_text("Raw notes.", "Slide content"))

        assert result == "- Polished text."
        mock_create.assert_awaited_once()

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][1]["role"] == "user"
        assert "Raw notes." in call_kwargs["messages"][1]["content"]

    def test_strips_code_fences(self):
        from clm.notebooks.polish import polish_text

        mock_create = AsyncMock(return_value=self._make_mock_response("```\n- Clean text.\n```"))

        with self._patch_client(mock_create):
            result = asyncio.run(polish_text("Raw notes."))
        assert result == "- Clean text."

    def test_custom_model(self):
        from clm.notebooks.polish import polish_text

        mock_create = AsyncMock(return_value=self._make_mock_response("- Result."))

        with self._patch_client(mock_create):
            asyncio.run(polish_text("Notes.", model="openai/gpt-4o"))

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "openai/gpt-4o"

    def test_api_base_and_key_passed(self):
        from clm.notebooks.polish import polish_text

        mock_create = AsyncMock(return_value=self._make_mock_response("- Result."))

        with patch("clm.infrastructure.llm.client._build_client") as mock_build:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            mock_build.return_value = mock_client

            asyncio.run(polish_text("Notes.", api_base="http://localhost:8080", api_key="test-key"))

            mock_build.assert_called_once_with(api_base="http://localhost:8080", api_key="test-key")

    def test_llm_error_raised(self):
        from clm.infrastructure.llm.client import LLMError
        from clm.notebooks.polish import polish_text

        mock_create = AsyncMock(side_effect=RuntimeError("API down"))

        with self._patch_client(mock_create):
            with pytest.raises(LLMError, match="Polish LLM call failed"):
                asyncio.run(polish_text("Notes."))
