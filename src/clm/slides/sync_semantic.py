"""The semantic translation oracle for the consistency ledger (issue #448, P2).

Design note: ``docs/claude/design/sync-consistency-ledger.md`` (§3 the ``semantic``
strictness rung, §8 P2, §9.4 cost discipline).

The ledger's two cheaper rungs are deterministic: ``structural`` trusts a pair whose
structure is sound (``verify``), ``assume`` inherits the watermark's word without a
check (``baseline seed``). Neither answers *is the EN half actually a correct
translation of the DE half?* — the question a legacy deck's never-agent-reconciled
slides need answered before they can be trusted. This module is that third,
**semantic** rung: a cheap LLM judges each ``(de_cell, en_cell)`` pair and the verdict
is written back into the ledger as ``confirmed_oracle=semantic:<model>``, so a slide
judged once becomes a free ledger hit forever (the trust-memoization payoff).

It lives in the **agent / model tier** — invoked only by ``clm slides sync baseline
establish`` (the model-bearing establish-the-ledger pass), never by the model-free
engine (epic #440 decision B). The recording itself
(:func:`clm.slides.sync_ledger.record_semantic`) takes a judge *by injection*, so the
ledger module stays model-free; this module supplies the concrete OpenRouter judge and
a static double for tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from clm.infrastructure.llm.retry import call_with_retries

#: The cheap "is this translation correct?" tier — same model the cold-pair
#: correspondence verifier (#216) uses. A yes/no translation-faithfulness check is well
#: within a cheap model, and §9.4's cost discipline argues for it. Overridable via
#: ``baseline establish --semantic-model``.
DEFAULT_SEMANTIC_MODEL = "anthropic/claude-haiku-4-5"
SEMANTIC_PROMPT_VERSION = "semantic-v1"


class SemanticError(Exception):
    """The semantic judge could not produce a verdict (transport / parse failure).

    The caller leaves that slide cold (un-banked) and continues — a failed call must
    never bank a slide nor abort the whole establish pass.
    """


@dataclass(frozen=True)
class SemanticVerdict:
    """One LLM judgement of whether a ``(de, en)`` pair is a faithful translation."""

    correct: bool
    reason: str = ""


@runtime_checkable
class SemanticJudge(Protocol):
    """Judges whether the EN half of a localized cell faithfully renders the DE half.

    ``prompt_version`` folds in the model (a future result-cache key); it is not used by
    the recorder, which stamps ``confirmed_oracle=semantic:<model>`` from the CLI model
    argument (per design §4.1). So the **model** is legible in the recorded provenance; a
    prompt-only revision at the same model is not currently distinguished there.
    """

    prompt_version: str

    def judge(self, *, de_body: str, en_body: str, role: str) -> SemanticVerdict:
        """Return the verdict for one pair. Raises :class:`SemanticError` on failure."""
        ...


@dataclass
class StaticSemanticJudge:
    """A deterministic judge for tests and offline runs.

    Returns ``verdicts[de_body]`` when pinned, else ``SemanticVerdict(default, …)`` — so
    a test can fix one cell's verdict by its DE body and let the rest fall through.
    ``raise_error`` exercises the safe-abort path; ``calls`` counts invocations so a test
    can assert which slides were (not) judged.
    """

    verdicts: dict[str, SemanticVerdict] = field(default_factory=dict)
    default: bool = True
    raise_error: bool = False
    prompt_version: str = "static"
    calls: int = 0

    def judge(self, *, de_body: str, en_body: str, role: str) -> SemanticVerdict:
        self.calls += 1
        if self.raise_error:
            raise SemanticError("static judge configured to fail")
        return self.verdicts.get(de_body, SemanticVerdict(self.default, "static"))


_SEMANTIC_SYSTEM_PROMPT = (
    "You verify one cell of a split bilingual programming-course slide deck: a German "
    "(DE) source cell and its English (EN) counterpart that should be a faithful "
    "translation of each other. You are given the DE body, the EN body, and the cell's "
    "role (a prose 'slide'/'subslide', a 'voiceover'/'notes' narration, or a "
    "'localized-code' cell whose comments and string literals are translated while the "
    "code itself stays identical).\n\n"
    'Return ONLY a JSON object: {"correct": <bool>, "reason": <short string>}.\n'
    "  - correct=true  when the EN cell faithfully renders the DE cell — same meaning, "
    "topic, and intent (for a code cell: identical code with equivalent translated "
    "comments / string literals);\n"
    "  - correct=false when it does NOT — content present in one half and missing from "
    "the other, a mistranslation, a stale half left un-updated when the other changed, "
    "or (for code) diverged code.\n\n"
    "Judge by meaning, not surface form: a faithful translation has different words but "
    "the same content. When genuinely unsure, return false — banking a wrong pairing as "
    "'in sync' silently hides a real divergence, which is worse than flagging it for the "
    "author. Keep 'reason' to one short clause. Return only the JSON object, no "
    "commentary, no code fences."
)

#: Public alias of the semantic system prompt (cf. ``CORRESPONDENCE_SYSTEM_PROMPT``).
SEMANTIC_SYSTEM_PROMPT = _SEMANTIC_SYSTEM_PROMPT


def build_semantic_user_prompt(*, de_body: str, en_body: str, role: str) -> str:
    """Render one pair for the judge: the role and both bodies, clearly delimited."""
    return f"ROLE: {role}\n\nDE (source):\n{de_body}\n\nEN (counterpart):\n{en_body}\n"


def _strip_fences(text: str) -> str:
    """Drop a leading/trailing ``` fence if the model wrapped its JSON in one."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def parse_semantic_verdict(content: str) -> SemanticVerdict:
    """Parse the model's ``{"correct": bool, "reason": str}`` response, or raise.

    Invalid JSON, a non-object, or a missing / non-boolean ``correct`` is a
    :class:`SemanticError` so a malformed response leaves the slide cold (never a crash
    nor a false 'correct').
    """
    try:
        raw = json.loads(_strip_fences(content))
    except (ValueError, TypeError) as exc:
        raise SemanticError(f"semantic verdict is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("correct"), bool):
        raise SemanticError("semantic verdict must be a JSON object with a boolean 'correct'")
    # ``reason`` is display-only (it surfaces in the rejection report). Keep only a string;
    # ignore a structured/non-string value rather than stringify JSON junk into the report.
    reason = raw.get("reason", "")
    return SemanticVerdict(correct=raw["correct"], reason=reason if isinstance(reason, str) else "")


@dataclass
class OpenRouterSemanticJudge:
    """LLM-backed semantic judge (Claude Haiku by default — the cheap yes/no tier).

    Mirrors :class:`~clm.slides.sync_recover.OpenRouterCorrespondenceVerifier`: a thin
    synchronous OpenAI-client adapter that sends one ``(de, en)`` pair and parses the
    ``{correct, reason}`` verdict. Raises :class:`SemanticError` on any transport / parse
    failure so the caller leaves that slide cold (never bank on a failed call).
    ``prompt_version`` folds in the model so the ``confirmed_oracle`` it stamps is
    legible (``semantic:<model>``).
    """

    model: str = DEFAULT_SEMANTIC_MODEL
    api_base: str | None = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = 512
    timeout: float = 60.0
    prompt_version: str = SEMANTIC_PROMPT_VERSION

    def __post_init__(self) -> None:
        if self.prompt_version == SEMANTIC_PROMPT_VERSION:
            self.prompt_version = f"{SEMANTIC_PROMPT_VERSION}:{self.model}"

    def _client(self):  # pragma: no cover - thin network adapter
        from clm.infrastructure.llm.openrouter_client import build_openrouter_client

        return build_openrouter_client(
            api_base=self.api_base, api_key=self.api_key, timeout=self.timeout
        )

    def judge(
        self, *, de_body: str, en_body: str, role: str
    ) -> SemanticVerdict:  # pragma: no cover - exercised via mocked client / integration
        user = build_semantic_user_prompt(de_body=de_body, en_body=en_body, role=role)

        def _create():
            return self._client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SEMANTIC_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )

        try:
            response = call_with_retries(_create, exc=Exception, label=f"semantic ({self.model})")
        except Exception as exc:  # noqa: BLE001 - normalize to the protocol's error
            raise SemanticError(f"semantic call failed: {exc}") from exc
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise SemanticError("semantic judge returned empty content")
        return parse_semantic_verdict(content)
