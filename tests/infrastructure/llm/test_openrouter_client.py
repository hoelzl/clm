"""Unit tests for the shared OpenRouter client helpers + the remote sync judge.

These never touch the network: :func:`build_openrouter_client` is monkeypatched
to return a fake OpenAI-shaped client, and the key-resolution helpers are pure.
"""

from __future__ import annotations

import json

import pytest

from clm.infrastructure.llm import openrouter_client as orc
from clm.infrastructure.llm.ollama_client import OllamaError, SyncProposal

# ---------------------------------------------------------------------------
# Fakes (OpenAI client shape: client.chat.completions.create -> resp)
# ---------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        message = type("Msg", (), {"content": self._content})()
        choice = type("Choice", (), {"message": message})()
        return type("Resp", (), {"choices": [choice]})()


class _FakeChat:
    def __init__(self, completions: _FakeCompletions):
        self.completions = completions


class _FakeClient:
    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self.completions = _FakeCompletions(content, exc)
        self.chat = _FakeChat(self.completions)


def _patch_client(monkeypatch, **kw) -> _FakeClient:
    client = _FakeClient(**kw)
    monkeypatch.setattr(orc, "build_openrouter_client", lambda **_kwargs: client)
    return client


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


# ---------------------------------------------------------------------------
# OpenRouterSyncJudge.propose
# ---------------------------------------------------------------------------


class TestOpenRouterSyncJudge:
    def test_propose_parses_update(self, monkeypatch):
        payload = json.dumps(
            {"verdict": "update", "proposed_text": "# ## Hello", "reason": "drift"}
        )
        client = _patch_client(monkeypatch, content=payload)
        judge = orc.OpenRouterSyncJudge()

        proposal = judge.propose("# ## Quelle", "# ## Old", source_lang="de", target_lang="en")

        assert isinstance(proposal, SyncProposal)
        assert proposal.verdict == "update"
        assert proposal.proposed_text == "# ## Hello"
        # The call used the default model and carried a system + user message.
        sent = client.completions.calls[0]
        assert sent["model"] == orc.DEFAULT_SYNC_JUDGE_MODEL
        roles = [m["role"] for m in sent["messages"]]
        assert roles == ["system", "user"]

    def test_propose_tolerates_fenced_json(self, monkeypatch):
        body = json.dumps({"verdict": "in_sync", "proposed_text": "# ## Old", "reason": ""})
        _patch_client(monkeypatch, content=f"```json\n{body}\n```")
        judge = orc.OpenRouterSyncJudge(model="anthropic/claude-x")

        proposal = judge.propose("a", "# ## Old", source_lang="en", target_lang="de")

        assert proposal.verdict == "in_sync"

    def test_propose_empty_response_raises(self, monkeypatch):
        _patch_client(monkeypatch, content="")
        judge = orc.OpenRouterSyncJudge()
        with pytest.raises(OllamaError):
            judge.propose("a", "b", source_lang="de", target_lang="en")

    def test_propose_client_error_is_normalized(self, monkeypatch):
        _patch_client(monkeypatch, exc=RuntimeError("boom"))
        judge = orc.OpenRouterSyncJudge()
        with pytest.raises(OllamaError):
            judge.propose("a", "b", source_lang="de", target_lang="en")

    def test_prompt_version_matches_local_judge(self):
        # Same prompts as the Ollama judge → same version constant, so a proposal
        # is interchangeable regardless of backend.
        from clm.infrastructure.llm.sync_prompts import SYNC_PROMPT_VERSION

        assert orc.OpenRouterSyncJudge().prompt_version == SYNC_PROMPT_VERSION
