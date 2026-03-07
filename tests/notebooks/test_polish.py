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

    @patch("litellm.acompletion")
    def test_basic_call(self, mock_acompletion):
        from clm.notebooks.polish import polish_text

        mock_acompletion.return_value = self._make_mock_response("- Polished text.")

        result = asyncio.run(polish_text("Raw notes.", "Slide content"))

        assert result == "- Polished text."
        mock_acompletion.assert_awaited_once()

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][1]["role"] == "user"
        assert "Raw notes." in call_kwargs["messages"][1]["content"]

    @patch("litellm.acompletion")
    def test_strips_code_fences(self, mock_acompletion):
        from clm.notebooks.polish import polish_text

        mock_acompletion.return_value = self._make_mock_response("```\n- Clean text.\n```")

        result = asyncio.run(polish_text("Raw notes."))
        assert result == "- Clean text."

    @patch("litellm.acompletion")
    def test_custom_model(self, mock_acompletion):
        from clm.notebooks.polish import polish_text

        mock_acompletion.return_value = self._make_mock_response("- Result.")

        asyncio.run(polish_text("Notes.", model="openai/gpt-4o"))

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["model"] == "openai/gpt-4o"

    @patch("litellm.acompletion")
    def test_api_base_and_key_passed(self, mock_acompletion):
        from clm.notebooks.polish import polish_text

        mock_acompletion.return_value = self._make_mock_response("- Result.")

        asyncio.run(polish_text("Notes.", api_base="http://localhost:8080", api_key="test-key"))

        call_kwargs = mock_acompletion.call_args[1]
        assert call_kwargs["api_base"] == "http://localhost:8080"
        assert call_kwargs["api_key"] == "test-key"

    @patch("litellm.acompletion")
    def test_llm_error_raised(self, mock_acompletion):
        from clm.infrastructure.llm.client import LLMError
        from clm.notebooks.polish import polish_text

        mock_acompletion.side_effect = RuntimeError("API down")

        with pytest.raises(LLMError, match="Polish LLM call failed"):
            asyncio.run(polish_text("Notes."))
