"""Shared OpenRouter (OpenAI-compatible) client + the remote sync edit judge.

CLM reaches OpenRouter through the OpenAI SDK — OpenRouter speaks the OpenAI
chat-completions wire format. Several paths already do this: the voiceover
propagate / merge / compare steps, ``clm summarize``, ``clm slides polish``, and the
new-slide :class:`~clm.slides.sync_translate.OpenRouterSlideTranslator`. This
module centralizes the construction (:func:`build_openrouter_client`,
:func:`resolve_openrouter_api_key`) so every call site resolves the key and
base URL the same way.

It also adds :class:`OpenRouterSyncJudge` — a remote (Claude Sonnet by default)
implementation of the edit-reconciliation
:class:`~clm.infrastructure.llm.ollama_client.SyncJudge`. ``clm slides sync``
defaults to this judge because the local Ollama model is slow; ``--provider
local`` selects the offline Ollama path. This is a step toward per-purpose
model configurability (Issue #167).

The judge reuses the *exact* prompts and JSON parser as the local
:class:`~clm.infrastructure.llm.ollama_client.OllamaSyncJudge`
(:data:`SYNC_SYSTEM_PROMPT`, :func:`build_sync_user_prompt`,
:func:`parse_sync_response`) so a proposal is identical regardless of backend,
and raises :class:`OllamaError` on failure so the apply engine
(``sync_apply._apply_edit``) handles both backends through one ``except``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from clm.infrastructure.llm.ollama_client import (
    DEFAULT_TIMEOUT_SECONDS,
    OllamaError,
    SyncProposal,
    parse_sync_response,
)
from clm.infrastructure.llm.retry import call_with_retries
from clm.infrastructure.llm.sync_prompts import (
    SYNC_PROMPT_VERSION,
    SYNC_SYSTEM_PROMPT,
    build_sync_user_prompt,
)

if TYPE_CHECKING:
    from openai import OpenAI

logger = logging.getLogger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"

# Claude Sonnet via OpenRouter — same default model the new-slide translator
# uses (:data:`clm.slides.sync_translate.DEFAULT_TRANSLATION_MODEL`). Kept as a
# distinct constant so the edit judge and the translator can be tuned
# independently (Issue #167).
DEFAULT_SYNC_JUDGE_MODEL = "anthropic/claude-sonnet-4-6"


def resolve_openrouter_api_key(explicit: str | None = None) -> str | None:
    """Return the OpenRouter/OpenAI key, preferring ``explicit`` then env vars.

    Resolution order matches every other OpenRouter call site in CLM:
    ``explicit`` → ``$OPENROUTER_API_KEY`` → ``$OPENAI_API_KEY``. Returns
    ``None`` when none is set, so callers can warn and degrade gracefully
    rather than crash.
    """
    return explicit or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")


def has_openrouter_api_key(explicit: str | None = None) -> bool:
    """Whether an OpenRouter/OpenAI key is configured.

    Thin predicate over :func:`resolve_openrouter_api_key` used by callers
    (e.g. ``clm slides sync``) to decide up front whether a remote judge can
    run, before constructing it.
    """
    return bool(resolve_openrouter_api_key(explicit))


def build_openrouter_client(
    *,
    api_base: str | None = OPENROUTER_API_BASE,
    api_key: str | None = None,
    timeout: float | None = None,
) -> OpenAI:  # pragma: no cover - thin network adapter
    """Build a synchronous OpenAI client pointed at OpenRouter.

    Shared by :class:`OpenRouterSyncJudge` and
    :class:`~clm.slides.sync_translate.OpenRouterSlideTranslator`. ``api_key``
    falls back to the usual env vars via :func:`resolve_openrouter_api_key`.
    """
    import openai

    key = resolve_openrouter_api_key(api_key)
    if timeout is None:
        return openai.OpenAI(base_url=api_base, api_key=key)
    return openai.OpenAI(base_url=api_base, api_key=key, timeout=timeout)


@dataclass
class OpenRouterSyncJudge:
    """Remote edit-reconciliation judge (OpenRouter, Claude Sonnet by default).

    A drop-in for :class:`~clm.infrastructure.llm.ollama_client.OllamaSyncJudge`:
    same :meth:`propose` signature, same prompts, same JSON parser, and the same
    :class:`OllamaError` on failure — so ``clm slides sync`` can swap backends
    without the apply engine knowing. The command defaults to this judge because
    the local Ollama model is slow; pass ``--provider local`` for the offline
    path. See Issue #167 (per-purpose model configurability).
    """

    model: str = DEFAULT_SYNC_JUDGE_MODEL
    api_base: str | None = OPENROUTER_API_BASE
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    prompt_version: str = SYNC_PROMPT_VERSION

    def propose(
        self,
        source_text: str,
        target_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> SyncProposal:
        """Propose a target-cell body reflecting edits made on the source side.

        Mirrors :meth:`OllamaSyncJudge.propose`. Raises :class:`OllamaError` on
        any transport/parse failure (the protocol's standard error), so callers
        treat it as "skip this pair with an error", not as fatal.
        """
        user_prompt = build_sync_user_prompt(
            source_text=source_text,
            target_text=target_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )

        def _create():  # pragma: no cover - thin network adapter
            return build_openrouter_client(
                api_base=self.api_base,
                api_key=self.api_key,
                timeout=self.timeout,
            ).chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYNC_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

        # Retry transient failures (rate-limit / connection blip / 5xx) so one
        # flaky call does not drop the edit; a persistent failure still raises.
        try:
            response = call_with_retries(
                _create, exc=Exception, label=f"OpenRouter sync judge ({self.model})"
            )
        except Exception as exc:  # noqa: BLE001 - normalize to the protocol's error type
            raise OllamaError(f"OpenRouter sync judge call failed: {exc}") from exc

        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise OllamaError("OpenRouter sync judge returned an empty response")
        return parse_sync_response(text)
