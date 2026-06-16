"""The OpenRouter sync judge must request structured JSON output.

Cells containing markdown tables, fenced code, escaped newlines, or characters
like ``&lt;`` / ``⌃⌘`` used to make the judge return text that broke
``json.loads`` ("sync response is not valid JSON"). Passing a ``json_schema``
``response_format`` forces the model to emit valid JSON. These tests assert the
parameter is wired (without any network call) and that the schema is strict.
"""

from __future__ import annotations

from types import SimpleNamespace

from clm.infrastructure.llm import openrouter_client as orc
from clm.infrastructure.llm.openrouter_client import OpenRouterSyncJudge
from clm.infrastructure.llm.sync_prompts import (
    SYNC_RESPONSE_JSON_SCHEMA,
    SYNC_RESPONSE_SCHEMA,
)


def _fake_client(captured: dict, content: str):
    def fake_create(**kwargs):
        captured.update(kwargs)
        msg = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))


def test_judge_requests_json_schema_response_format(monkeypatch):
    captured: dict = {}
    content = '{"verdict": "update", "proposed_text": "# Hallo Welt", "reason": "translated"}'
    monkeypatch.setattr(
        orc, "build_openrouter_client", lambda **kw: _fake_client(captured, content)
    )

    judge = OpenRouterSyncJudge(api_key="test-key")
    proposal = judge.propose("# Hello World", "# Hallo", source_lang="en", target_lang="de")

    # The proposal parses cleanly...
    assert proposal.verdict == "update"
    assert proposal.proposed_text == "# Hallo Welt"

    # ...and the call constrained the output to our strict JSON schema.
    rf = captured["response_format"]
    assert rf == {"type": "json_schema", "json_schema": SYNC_RESPONSE_JSON_SCHEMA}
    assert rf["json_schema"]["strict"] is True


def test_response_schema_is_strict_compatible():
    # OpenAI/OpenRouter strict mode requires additionalProperties:false and
    # every property listed in `required`.
    assert SYNC_RESPONSE_SCHEMA["additionalProperties"] is False
    props = set(SYNC_RESPONSE_SCHEMA["properties"])
    assert props == {"verdict", "proposed_text", "reason"}
    assert set(SYNC_RESPONSE_SCHEMA["required"]) == props
    assert SYNC_RESPONSE_SCHEMA["properties"]["verdict"]["enum"] == ["in_sync", "update"]
