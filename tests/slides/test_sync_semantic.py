"""Unit tests for the semantic translation oracle (issue #448, P2).

Covers the parser (the trust-critical gate — a malformed / non-boolean verdict must
raise, never bank), the static double, and ``OpenRouterSemanticJudge.judge`` driven
against a mocked client (the success parse, the empty-content guard, and the
transport-error normalization to :class:`SemanticError`).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clm.slides.sync_semantic import (
    OpenRouterSemanticJudge,
    SemanticError,
    SemanticVerdict,
    StaticSemanticJudge,
    parse_semantic_verdict,
)

# ---------------------------------------------------------------------------
# parse_semantic_verdict — the trust gate
# ---------------------------------------------------------------------------


def test_parse_valid_true() -> None:
    v = parse_semantic_verdict('{"correct": true, "reason": "faithful"}')
    assert v.correct is True and v.reason == "faithful"


def test_parse_valid_false() -> None:
    v = parse_semantic_verdict('{"correct": false, "reason": "EN omits a bullet"}')
    assert v.correct is False and v.reason == "EN omits a bullet"


def test_parse_strips_fences() -> None:
    v = parse_semantic_verdict('```json\n{"correct": true}\n```')
    assert v.correct is True and v.reason == ""


def test_parse_rejects_non_boolean_correct() -> None:
    # A truthy non-bool must NOT bank — it raises so the caller leaves the slide cold.
    for bad in ('{"correct": "yes"}', '{"correct": 1}', '{"reason": "x"}', "{}", "[]"):
        with pytest.raises(SemanticError):
            parse_semantic_verdict(bad)


def test_parse_rejects_invalid_json() -> None:
    with pytest.raises(SemanticError):
        parse_semantic_verdict("not json at all")


def test_parse_ignores_non_string_reason() -> None:
    # Structured junk in `reason` is dropped (display-only), not stringified into the report.
    v = parse_semantic_verdict('{"correct": true, "reason": {"x": 1}}')
    assert v.correct is True and v.reason == ""


# ---------------------------------------------------------------------------
# StaticSemanticJudge
# ---------------------------------------------------------------------------


def test_static_judge_default_and_override() -> None:
    j = StaticSemanticJudge(verdicts={"DE": SemanticVerdict(False, "no")}, default=True)
    assert j.judge(de_body="DE", en_body="EN", role="slide").correct is False  # pinned
    assert j.judge(de_body="other", en_body="EN", role="slide").correct is True  # default
    assert j.calls == 2


def test_static_judge_raise() -> None:
    j = StaticSemanticJudge(raise_error=True)
    with pytest.raises(SemanticError):
        j.judge(de_body="x", en_body="y", role="slide")


# ---------------------------------------------------------------------------
# OpenRouterSemanticJudge.judge — driven against a mocked client (no network)
# ---------------------------------------------------------------------------


def _fake_client(content: str) -> SimpleNamespace:
    def create(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``call_with_retries`` with a single-shot pass-through (no backoff sleeps)."""
    import clm.slides.sync_semantic as sem

    monkeypatch.setattr(sem, "call_with_retries", lambda fn, **_kw: fn())


def test_openrouter_judge_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_retry(monkeypatch)
    j = OpenRouterSemanticJudge(model="m")
    monkeypatch.setattr(j, "_client", lambda: _fake_client('{"correct": true, "reason": "ok"}'))
    v = j.judge(de_body="Hallo", en_body="Hello", role="slide")
    assert v.correct is True and v.reason == "ok"


def test_openrouter_judge_empty_content_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_retry(monkeypatch)
    j = OpenRouterSemanticJudge(model="m")
    monkeypatch.setattr(j, "_client", lambda: _fake_client("   "))
    with pytest.raises(SemanticError, match="empty"):
        j.judge(de_body="x", en_body="y", role="slide")


def test_openrouter_judge_transport_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_retry(monkeypatch)
    j = OpenRouterSemanticJudge(model="m")

    def _boom() -> SimpleNamespace:
        raise RuntimeError("network down")

    monkeypatch.setattr(j, "_client", _boom)
    with pytest.raises(SemanticError, match="call failed"):
        j.judge(de_body="x", en_body="y", role="slide")


def test_openrouter_judge_prompt_version_folds_in_model() -> None:
    assert OpenRouterSemanticJudge(model="anthropic/claude-haiku-4-5").prompt_version == (
        "semantic-v1:anthropic/claude-haiku-4-5"
    )
