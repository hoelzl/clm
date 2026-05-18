"""Thin wrapper around the local Ollama HTTP API.

Used by ``clm slides assign-ids --llm-suggest`` (Phase 2) and reserved for
the coverage check (Phase 4) and sync (Phase 7). All call sites take a
:class:`TitleSuggester` (Phase 2) or analogous protocol, so tests can
substitute an in-memory fake and the real network client stays unused.

The wire format follows Ollama's native ``/api/chat`` endpoint:
https://github.com/ollama/ollama/blob/main/docs/api.md. We deliberately do
*not* go through the OpenAI-compatibility shim — the native endpoint is
stable, simpler, and avoids dragging the ``openai`` package into the import
graph for callers who only need Ollama.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TITLE_MODEL = "qwen3:30b"
# Cold-load on large local models can take a minute; warm calls are ~5s.
DEFAULT_TIMEOUT_SECONDS = 120.0

# Bumped whenever the prompt or system message changes in a way that
# invalidates cached suggestions. Embedded into the cache key per §2.3.
TITLE_PROMPT_VERSION = "v1"

_TITLE_SYSTEM_PROMPT = (
    "You are helping an instructor pick a short title for a slide that "
    "currently has no heading. The slide content will be given to you. "
    "Reply with a single English title of 3-7 words, written in title "
    "case. Do not include quotes, punctuation, or any other text — just "
    "the bare title on one line."
)


class OllamaError(RuntimeError):
    """The Ollama call failed (connection, timeout, parse, or model error)."""


class TitleSuggester(Protocol):
    """Protocol for anything that can suggest a slide title from cell content.

    The real implementation lives in :class:`OllamaTitleSuggester`. Tests
    pass an in-memory fake (see ``tests/slides/test_assign_ids.py`` and
    :class:`StaticTitleSuggester` below) so the slug + assign-ids core can
    be exercised without a running Ollama daemon.
    """

    prompt_version: str

    def suggest(self, content: str) -> str:
        """Return a short English title for ``content``.

        Raises :class:`OllamaError` (or a protocol-specific equivalent) if
        the suggestion is unavailable. Callers should treat exceptions as
        "fall back to refusal", not as fatal.
        """
        ...


class StaticTitleSuggester:
    """In-memory :class:`TitleSuggester` for tests and bulk scripts.

    Supply a mapping from a stable key (e.g. content excerpt or content
    hash) to the title. When ``suggest`` is called the lookup is exact;
    misses raise :class:`OllamaError` so the caller falls back to refusal.

    A ``default`` may be passed for the "I don't care, just give me
    *something*" case; tests typically lean on explicit per-key mappings
    so they can assert which cell was queried.
    """

    def __init__(
        self,
        mapping: dict[str, str] | None = None,
        *,
        default: str | None = None,
        prompt_version: str = TITLE_PROMPT_VERSION,
    ):
        self._mapping = dict(mapping or {})
        self._default = default
        self.prompt_version = prompt_version
        self.calls: list[str] = []  # exposed for test assertions

    def suggest(self, content: str) -> str:
        self.calls.append(content)
        if content in self._mapping:
            return self._mapping[content]
        if self._default is not None:
            return self._default
        raise OllamaError("no static suggestion configured for content excerpt")


class OllamaTitleSuggester:
    """:class:`TitleSuggester` that talks to a local Ollama daemon."""

    prompt_version = TITLE_PROMPT_VERSION

    def __init__(
        self,
        *,
        model: str = DEFAULT_TITLE_MODEL,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.model = model
        self.base_url = (base_url or os.environ.get("OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
        self.timeout = timeout

    def suggest(self, content: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "options": {"temperature": 0.2},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — localhost only
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc
        except TimeoutError as exc:
            raise OllamaError(f"Ollama request timed out after {self.timeout}s") from exc

        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama returned non-JSON response: {raw[:200]!r}") from exc

        message = response.get("message") or {}
        text = (message.get("content") or "").strip()
        if not text:
            raise OllamaError("Ollama returned empty title")
        return _clean_title(text)


def _clean_title(text: str) -> str:
    """Sanitize the raw model output.

    Local LLMs sometimes wrap the title in quotes or append a trailing
    explanation despite the system prompt. We take the first non-empty
    line and strip a single layer of surrounding quotes/backticks. Slug
    derivation downstream will discard any remaining punctuation.
    """
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first_line:
        return text.strip()
    # Drop one layer of wrapping quotes/backticks.
    for opener, closer in (('"', '"'), ("'", "'"), ("`", "`")):
        if first_line.startswith(opener) and first_line.endswith(closer) and len(first_line) >= 2:
            first_line = first_line[1:-1]
            break
    return first_line.strip().rstrip(".")


def is_available(suggester: TitleSuggester | None) -> bool:
    """Whether ``suggester`` looks ready to respond to ``suggest`` calls.

    A best-effort liveness check for the real :class:`OllamaTitleSuggester`;
    other implementations (including :class:`StaticTitleSuggester`) are
    assumed available. The check pings ``/api/tags`` rather than firing a
    full generation request.
    """
    if suggester is None:
        return False
    if not isinstance(suggester, OllamaTitleSuggester):
        return True
    try:
        req = urllib.request.Request(f"{suggester.base_url}/api/tags")  # noqa: S310
        with urllib.request.urlopen(req, timeout=2.0):  # noqa: S310
            return True
    except (urllib.error.URLError, TimeoutError):
        return False
