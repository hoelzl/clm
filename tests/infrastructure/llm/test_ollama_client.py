"""Tests for the protocol shape of :mod:`clm.infrastructure.llm.ollama_client`.

The real :class:`OllamaTitleSuggester` is exercised by manual fixtures; in
the test suite we only verify the protocol's mockable behavior so the
assign-ids tests can rely on :class:`StaticTitleSuggester`.
"""

from __future__ import annotations

import pytest

from clm.infrastructure.llm.ollama_client import (
    TITLE_PROMPT_VERSION,
    OllamaError,
    OllamaTitleSuggester,
    StaticTitleSuggester,
    _clean_title,
    is_available,
)


class TestStaticTitleSuggester:
    def test_mapping_lookup(self):
        s = StaticTitleSuggester({"content-a": "Title A"})
        assert s.suggest("content-a") == "Title A"

    def test_default_used_for_unknown(self):
        s = StaticTitleSuggester(default="Default")
        assert s.suggest("anything") == "Default"

    def test_raises_when_no_match(self):
        s = StaticTitleSuggester()
        with pytest.raises(OllamaError):
            s.suggest("missing")

    def test_records_calls(self):
        s = StaticTitleSuggester(default="x")
        s.suggest("first")
        s.suggest("second")
        assert s.calls == ["first", "second"]

    def test_prompt_version_default(self):
        s = StaticTitleSuggester()
        assert s.prompt_version == TITLE_PROMPT_VERSION

    def test_prompt_version_override(self):
        s = StaticTitleSuggester(prompt_version="v99")
        assert s.prompt_version == "v99"


class TestCleanTitle:
    def test_strips_double_quotes(self):
        assert _clean_title('"My Title"') == "My Title"

    def test_strips_single_quotes(self):
        assert _clean_title("'My Title'") == "My Title"

    def test_takes_first_line(self):
        assert _clean_title("My Title\nExplanation follows") == "My Title"

    def test_strips_trailing_period(self):
        assert _clean_title("My Title.") == "My Title"

    def test_passthrough(self):
        assert _clean_title("Clean Output") == "Clean Output"


class TestIsAvailable:
    def test_static_suggester_always_available(self):
        assert is_available(StaticTitleSuggester()) is True

    def test_none_unavailable(self):
        assert is_available(None) is False

    def test_real_suggester_unavailable_with_bad_url(self):
        # Point at a port nothing listens on. The check should fail soft
        # rather than raise.
        s = OllamaTitleSuggester(base_url="http://127.0.0.1:1")
        assert is_available(s) is False
