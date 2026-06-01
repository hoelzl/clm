"""Slide-content translation for the single-language authoring workflow.

Phase 3 of Issue #166. When the author adds a brand-new slide in one language,
the sync engine translates it into the other language so the decks stay at
parity (the issue's "full translation, not a stub" decision). This is distinct
from the edit ``SyncJudge`` (which reconciles an *existing* pair): here there is
no counterpart yet, so a stronger model is warranted.

The model is fixed to **Claude Sonnet via OpenRouter** for now
(:data:`DEFAULT_TRANSLATION_MODEL`), routed through a synchronous OpenAI client
so the apply engine stays synchronous. Per-purpose model configurability is a
separate investigation (#167). Prompt iteration is expected (#166 Decisions).

The engine depends only on the :class:`SlideTranslator` protocol, so tests drive
it with :class:`StaticSlideTranslator` and never touch the network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TRANSLATION_MODEL",
    "OpenRouterSlideTranslator",
    "SlideTranslator",
    "StaticSlideTranslator",
    "TranslationError",
]

# Claude Sonnet via OpenRouter — the voiceover propagate path's precedent.
DEFAULT_TRANSLATION_MODEL = "anthropic/claude-sonnet-4-6"


class TranslationError(Exception):
    """A new-slide translation could not be produced."""


@runtime_checkable
class SlideTranslator(Protocol):
    """Produces a target-language body for a brand-new source-language cell."""

    prompt_version: str

    def translate(
        self,
        *,
        source_body: str,
        source_lang: str,
        target_lang: str,
        role: str,
    ) -> str:
        """Return the translated cell body (percent-format, comment-prefixed).

        ``source_body`` is the cell body as the parser produces it (markdown
        lines prefixed with ``# ``). The return value must keep that shape so
        it slots straight into a percent-format cell. Raises
        :class:`TranslationError` on failure.
        """
        ...


@dataclass
class StaticSlideTranslator:
    """A deterministic translator for tests and offline runs.

    ``mapping`` keys on the exact ``source_body``; ``default`` is the fallback.
    With neither, :meth:`translate` raises — mirroring ``StaticSyncJudge``.
    """

    default: str | None = None
    mapping: dict[str, str] = field(default_factory=dict)
    prompt_version: str = "static"

    def translate(
        self,
        *,
        source_body: str,
        source_lang: str,
        target_lang: str,
        role: str,
    ) -> str:
        if source_body in self.mapping:
            return self.mapping[source_body]
        if self.default is not None:
            return self.default
        raise TranslationError(f"no static translation for {source_body!r}")


@dataclass
class OpenRouterSlideTranslator:
    """LLM-backed translator (synchronous OpenAI client, default Sonnet).

    A v1 implementation: the prompt is intentionally simple and expected to be
    tuned (#166 Decisions / design note §10). ``api_base`` defaults to
    OpenRouter; ``api_key`` falls back to the usual env vars in
    :meth:`_client`.
    """

    model: str = DEFAULT_TRANSLATION_MODEL
    api_base: str | None = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int = 4096
    prompt_version: str = "translate-v1"

    def _client(self):  # pragma: no cover - thin network adapter
        # Shared with the edit judge so key/base resolution stays in one place.
        from clm.infrastructure.llm.openrouter_client import build_openrouter_client

        return build_openrouter_client(api_base=self.api_base, api_key=self.api_key)

    def translate(
        self,
        *,
        source_body: str,
        source_lang: str,
        target_lang: str,
        role: str,
    ) -> str:  # pragma: no cover - exercised via mocked client / integration
        system = _SYSTEM_PROMPT.format(
            source_lang=_LANG_NAMES.get(source_lang, source_lang),
            target_lang=_LANG_NAMES.get(target_lang, target_lang),
            role=role,
        )
        try:
            response = self._client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": source_body},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            raise TranslationError(f"translation LLM call failed: {exc}") from exc
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise TranslationError("translation LLM returned empty content")
        # Strip BOTH ends: a leading newline would inject a blank first line
        # into the built cell; a trailing one would add a stray blank.
        return str(content).strip("\n")


_LANG_NAMES = {"de": "German", "en": "English"}

_SYSTEM_PROMPT = (
    "You translate a single slide cell from {source_lang} to {target_lang} for a "
    "programming course. The cell is a {role} cell in Jupyter percent-format: every "
    "line is prefixed with '# ' and may contain Markdown (headings like '# ## Title', "
    "bullet lists, inline code). Translate ONLY the natural-language prose. Preserve "
    "verbatim: the '# ' line prefixes, Markdown structure and heading levels, code "
    "spans and identifiers, URLs, and slide directives. Do not add, drop, or reorder "
    "lines. Return only the translated cell body, no commentary, no code fences."
)
