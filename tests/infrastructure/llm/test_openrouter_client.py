"""Unit tests for the shared OpenRouter client helpers.

These never touch the network: the key-resolution helpers are pure.
"""

from __future__ import annotations

from clm.infrastructure.llm import openrouter_client as orc

# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


class TestKeyResolution:
    def test_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-or")
        monkeypatch.setenv("OPENAI_API_KEY", "env-oa")
        assert orc.resolve_openrouter_api_key("explicit") == "explicit"

    def test_openrouter_preferred_over_openai(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "env-oa")
        assert orc.resolve_openrouter_api_key() == "env-oa"
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-or")
        assert orc.resolve_openrouter_api_key() == "env-or"

    def test_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert orc.resolve_openrouter_api_key() is None
        assert orc.has_openrouter_api_key() is False

    def test_has_key_true_when_set(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "x")
        assert orc.has_openrouter_api_key() is True
