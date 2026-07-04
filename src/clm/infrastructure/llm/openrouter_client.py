"""Shared OpenRouter (OpenAI-compatible) client construction.

CLM reaches OpenRouter through the OpenAI SDK — OpenRouter speaks the OpenAI
chat-completions wire format. Several paths do this: the voiceover
propagate / merge / compare steps, ``clm summarize``, ``clm slides polish``, and the
new-slide :class:`~clm.slides.sync_translate.OpenRouterSlideTranslator`. This
module centralizes the construction (:func:`build_openrouter_client`,
:func:`resolve_openrouter_api_key`) so every call site resolves the key and
base URL the same way.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI

logger = logging.getLogger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


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
    (e.g. ``clm slides translate``) to decide up front whether a remote model
    can run, before constructing it.
    """
    return bool(resolve_openrouter_api_key(explicit))


def build_openrouter_client(
    *,
    api_base: str | None = OPENROUTER_API_BASE,
    api_key: str | None = None,
    timeout: float | None = None,
) -> OpenAI:  # pragma: no cover - thin network adapter
    """Build a synchronous OpenAI client pointed at OpenRouter.

    Shared by the OpenRouter-backed model callers (e.g.
    :class:`~clm.slides.sync_translate.OpenRouterSlideTranslator`). ``api_key``
    falls back to the usual env vars via :func:`resolve_openrouter_api_key`.
    """
    import openai

    key = resolve_openrouter_api_key(api_key)
    if timeout is None:
        return openai.OpenAI(base_url=api_base, api_key=key)
    return openai.OpenAI(base_url=api_base, api_key=key, timeout=timeout)
