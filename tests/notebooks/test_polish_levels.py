"""Tests for the PolishLevel enum and polish_levels package."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.notebooks.polish_levels import PolishLevel, load_prompt

# ---------------------------------------------------------------------------
# PolishLevel enum
# ---------------------------------------------------------------------------


class TestPolishLevelEnum:
    def test_all_members_present(self):
        assert set(PolishLevel) == {
            PolishLevel.verbatim,
            PolishLevel.light,
            PolishLevel.standard,
            PolishLevel.heavy,
            PolishLevel.rewrite,
        }

    def test_str_value_equals_name(self):
        for level in PolishLevel:
            assert str(level) == level.value
            assert str(level) == level.name

    def test_roundtrip_from_string(self):
        for level in PolishLevel:
            assert PolishLevel(level.value) is level

    def test_standard_is_default_string(self):
        assert PolishLevel("standard") is PolishLevel.standard

    def test_verbatim_roundtrip(self):
        assert PolishLevel("verbatim") is PolishLevel.verbatim

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            PolishLevel("extreme")


# ---------------------------------------------------------------------------
# load_prompt
# ---------------------------------------------------------------------------


class TestLoadPrompt:
    @pytest.mark.parametrize(
        "level", [PolishLevel.light, PolishLevel.standard, PolishLevel.heavy, PolishLevel.rewrite]
    )
    def test_returns_non_empty_string(self, level: PolishLevel):
        prompt = load_prompt(level)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_verbatim_raises_value_error(self):
        with pytest.raises(ValueError, match="verbatim"):
            load_prompt(PolishLevel.verbatim)

    def test_standard_prompt_has_filler_words_instruction(self):
        prompt = load_prompt(PolishLevel.standard)
        assert "filler" in prompt.lower()

    def test_light_prompt_has_filler_words_instruction(self):
        prompt = load_prompt(PolishLevel.light)
        assert "filler" in prompt.lower()

    def test_heavy_prompt_different_from_standard(self):
        assert load_prompt(PolishLevel.heavy) != load_prompt(PolishLevel.standard)

    def test_rewrite_prompt_different_from_standard(self):
        assert load_prompt(PolishLevel.rewrite) != load_prompt(PolishLevel.standard)

    def test_all_levels_differ_from_each_other(self):
        non_verbatim = [lvl for lvl in PolishLevel if lvl != PolishLevel.verbatim]
        prompts = [load_prompt(lvl) for lvl in non_verbatim]
        assert len(set(prompts)) == len(prompts), "Each level's prompt must be unique"


# ---------------------------------------------------------------------------
# polish_text with explicit polish levels
# ---------------------------------------------------------------------------


class TestPolishTextWithLevels:
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

    @pytest.mark.parametrize(
        "level",
        [PolishLevel.light, PolishLevel.standard, PolishLevel.heavy, PolishLevel.rewrite],
    )
    def test_non_verbatim_level_calls_llm(self, level: PolishLevel):
        from clm.notebooks.polish import polish_text

        mock_create = AsyncMock(return_value=self._make_mock_response("- Polished."))

        with self._patch_client(mock_create):
            result = asyncio.run(polish_text("Raw notes.", polish_level=level))

        assert result == "- Polished."
        mock_create.assert_awaited_once()

    @pytest.mark.parametrize(
        "level",
        [PolishLevel.light, PolishLevel.standard, PolishLevel.heavy, PolishLevel.rewrite],
    )
    def test_system_prompt_matches_level(self, level: PolishLevel):
        from clm.notebooks.polish import polish_text

        expected_prompt = load_prompt(level)
        mock_create = AsyncMock(return_value=self._make_mock_response("- Done."))

        with self._patch_client(mock_create):
            asyncio.run(polish_text("Notes.", polish_level=level))

        call_kwargs = mock_create.call_args[1]
        system_content = call_kwargs["messages"][0]["content"]
        assert system_content == expected_prompt

    def test_verbatim_returns_input_unchanged(self):
        from clm.notebooks.polish import polish_text

        mock_create = AsyncMock(return_value=self._make_mock_response("should not be called"))

        with self._patch_client(mock_create):
            result = asyncio.run(polish_text("My raw notes.", polish_level=PolishLevel.verbatim))

        assert result == "My raw notes."
        mock_create.assert_not_awaited()

    def test_verbatim_makes_zero_llm_calls(self):
        from clm.notebooks.polish import polish_text

        mock_create = AsyncMock()

        with self._patch_client(mock_create):
            asyncio.run(
                polish_text("text", slide_content="slide", polish_level=PolishLevel.verbatim)
            )

        mock_create.assert_not_called()

    def test_default_level_is_standard(self):
        from clm.notebooks.polish import polish_text

        standard_prompt = load_prompt(PolishLevel.standard)
        mock_create = AsyncMock(return_value=self._make_mock_response("- Result."))

        with self._patch_client(mock_create):
            asyncio.run(polish_text("Notes."))

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["messages"][0]["content"] == standard_prompt
