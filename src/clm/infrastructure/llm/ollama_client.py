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

from clm.infrastructure.llm.prompts import (
    COVERAGE_PROMPT_VERSION,
    COVERAGE_SYSTEM_PROMPT,
    TITLE_PROMPT_VERSION,
    TITLE_SYSTEM_PROMPT,
    BulletVerdict,
    CoverageVerdict,
    build_coverage_user_prompt,
)

__all__ = [
    "BulletVerdict",
    "COVERAGE_PROMPT_VERSION",
    "CoverageVerdict",
    "DEFAULT_COVERAGE_MODEL",
    "DEFAULT_TITLE_MODEL",
    "TITLE_PROMPT_VERSION",
    "CoverageJudge",
    "OllamaCoverageJudge",
    "OllamaError",
    "OllamaTitleSuggester",
    "StaticCoverageJudge",
    "StaticTitleSuggester",
    "TitleSuggester",
    "coverage_key",
    "is_available",
    "parse_coverage_response",
]

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TITLE_MODEL = "qwen3:30b"
DEFAULT_COVERAGE_MODEL = "qwen3:30b"
# Cold-load on large local models can take a minute; warm calls are ~5s.
DEFAULT_TIMEOUT_SECONDS = 120.0

# The prompt texts, versions, and verdict dataclasses live in the
# model-free :mod:`clm.infrastructure.llm.prompts` so agent-toolkit read
# paths can frame them without importing a client (#963). Re-exported
# here for the existing call sites.
_COVERAGE_SYSTEM_PROMPT = COVERAGE_SYSTEM_PROMPT
_TITLE_SYSTEM_PROMPT = TITLE_SYSTEM_PROMPT
_build_coverage_user_prompt = build_coverage_user_prompt


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


def is_available(client: object | None) -> bool:
    """Whether ``client`` looks ready to respond to LLM calls.

    A best-effort liveness check for the real Ollama clients
    (:class:`OllamaTitleSuggester`, :class:`OllamaCoverageJudge`,
    :class:`OllamaCoverageJudge`); fakes and any other implementation are
    assumed available. The check pings ``/api/tags`` rather than firing
    a full generation request.
    """
    if client is None:
        return False
    if not isinstance(
        client,
        OllamaTitleSuggester | OllamaCoverageJudge,
    ):
        return True
    try:
        req = urllib.request.Request(f"{client.base_url}/api/tags")  # noqa: S310
        with urllib.request.urlopen(req, timeout=2.0):  # noqa: S310
            return True
    except (urllib.error.URLError, TimeoutError):
        return False


# ---------------------------------------------------------------------------
# Coverage judge (Phase 4 of the slide-format-redesign)
# ---------------------------------------------------------------------------


class CoverageJudge(Protocol):
    """Protocol for anything that can judge whether a voiceover covers slide bullets.

    The real implementation lives in :class:`OllamaCoverageJudge`. Tests
    pass a :class:`StaticCoverageJudge` so the coverage logic can be
    exercised without a running Ollama daemon.
    """

    prompt_version: str

    def judge(self, bullets: list[str], voiceover: str, *, lang: str) -> CoverageVerdict:
        """Decide whether ``voiceover`` semantically covers each entry in ``bullets``.

        Raises :class:`OllamaError` (or a protocol-specific equivalent)
        on failure. Callers should treat exceptions as "skip this pair
        with a warning", not as fatal.
        """
        ...


class StaticCoverageJudge:
    """In-memory :class:`CoverageJudge` for tests and bulk scripts.

    Supply a ``mapping`` keyed by a stable string (typically the
    deterministic content key returned by :func:`coverage_key`) to a
    :class:`CoverageVerdict`. Misses raise :class:`OllamaError` so the
    caller falls back to "could not check".

    A ``default_verdict`` may be supplied for the common "everything is
    covered" case; tests typically lean on explicit per-key mappings so
    they can assert which pair was queried.
    """

    def __init__(
        self,
        mapping: dict[str, CoverageVerdict] | None = None,
        *,
        default_verdict: CoverageVerdict | None = None,
        prompt_version: str = COVERAGE_PROMPT_VERSION,
    ):
        self._mapping = dict(mapping or {})
        self._default_verdict = default_verdict
        self.prompt_version = prompt_version
        self.calls: list[tuple[tuple[str, ...], str, str]] = []

    def judge(self, bullets: list[str], voiceover: str, *, lang: str) -> CoverageVerdict:
        key = coverage_key(bullets, voiceover, lang=lang)
        self.calls.append((tuple(bullets), voiceover, lang))
        if key in self._mapping:
            return self._mapping[key]
        if self._default_verdict is not None:
            return self._default_verdict
        raise OllamaError("no static coverage verdict configured for this pair")


def coverage_key(bullets: list[str], voiceover: str, *, lang: str) -> str:
    """Stable key used by :class:`StaticCoverageJudge` lookups and test asserts."""
    body = "\n".join(bullets) + "\n---\n" + voiceover + f"\n---\n{lang}"
    return body


class OllamaCoverageJudge:
    """:class:`CoverageJudge` that talks to a local Ollama daemon."""

    prompt_version = COVERAGE_PROMPT_VERSION

    def __init__(
        self,
        *,
        model: str = DEFAULT_COVERAGE_MODEL,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.model = model
        self.base_url = (base_url or os.environ.get("OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
        self.timeout = timeout

    def judge(self, bullets: list[str], voiceover: str, *, lang: str) -> CoverageVerdict:
        user_prompt = _build_coverage_user_prompt(bullets, voiceover, lang=lang)
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _COVERAGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": 0.1},
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
            raise OllamaError("Ollama returned empty coverage verdict")
        return parse_coverage_response(text, bullets)


def parse_coverage_response(text: str, bullets: list[str]) -> CoverageVerdict:
    """Parse the judge's raw response text into a :class:`CoverageVerdict`.

    Tolerates leading/trailing prose around the JSON body (some local
    models tack on a one-line preamble despite the system prompt) by
    locating the first ``{`` and the last ``}``.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip a fenced code block ```json ... ```
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise OllamaError(f"could not locate JSON object in coverage response: {text[:200]!r}")
    body = cleaned[start : end + 1]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"coverage response is not valid JSON: {exc}") from exc

    raw_bullets = data.get("bullets")
    if not isinstance(raw_bullets, list):
        raise OllamaError("coverage response missing 'bullets' list")

    parsed: list[BulletVerdict] = []
    for entry in raw_bullets:
        if not isinstance(entry, dict):
            continue
        parsed.append(
            BulletVerdict(
                text=str(entry.get("text", "")),
                covered=bool(entry.get("covered", False)),
                reason=str(entry.get("reason", "")),
            )
        )

    verdict = data.get("verdict")
    if verdict not in ("covered", "gaps"):
        verdict = "covered" if all(b.covered for b in parsed) else "gaps"

    return CoverageVerdict(verdict=verdict, bullets=tuple(parsed), raw=text)
