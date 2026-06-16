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
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TITLE_MODEL = "qwen3:30b"
DEFAULT_COVERAGE_MODEL = "qwen3:30b"
DEFAULT_SYNC_MODEL = "qwen3:30b"
# Cold-load on large local models can take a minute; warm calls are ~5s.
DEFAULT_TIMEOUT_SECONDS = 120.0

# Bumped whenever the prompt or system message changes in a way that
# invalidates cached suggestions. Embedded into the cache key per §2.3.
TITLE_PROMPT_VERSION = "v1"

# Bumped whenever the coverage prompt changes in a way that invalidates
# cached verdicts. Embedded into the cache key per §2.5.
COVERAGE_PROMPT_VERSION = "v1"

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


def is_available(client: object | None) -> bool:
    """Whether ``client`` looks ready to respond to LLM calls.

    A best-effort liveness check for the real Ollama clients
    (:class:`OllamaTitleSuggester`, :class:`OllamaCoverageJudge`,
    :class:`OllamaSyncJudge`); fakes and any other implementation are
    assumed available. The check pings ``/api/tags`` rather than firing
    a full generation request.
    """
    if client is None:
        return False
    if not isinstance(
        client,
        OllamaTitleSuggester | OllamaCoverageJudge | OllamaSyncJudge,
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


_COVERAGE_SYSTEM_PROMPT = (
    "You check whether a voiceover script covers all of the bullet points "
    "from a slide. For each bullet, decide whether the voiceover already "
    "mentions or explains it. Do not require word-for-word match — "
    "semantic coverage is enough. A bullet is covered when a reasonable "
    "listener of the voiceover would hear the idea behind it; a bullet "
    "is uncovered when the voiceover never addresses it.\n\n"
    "Reply with a single JSON object and no other text. The object has "
    "two keys:\n"
    '  "bullets": a list of objects, one per slide bullet, each with '
    '"text" (the bullet, verbatim), "covered" (true or false), and '
    '"reason" (one short sentence).\n'
    '  "verdict": either "covered" (every bullet is covered) or "gaps" '
    "(at least one bullet is uncovered)."
)


@dataclass(frozen=True)
class BulletVerdict:
    """One bullet's coverage assessment."""

    text: str
    covered: bool
    reason: str = ""


@dataclass(frozen=True)
class CoverageVerdict:
    """A judge's verdict for one (slide, voiceover) pair.

    ``verdict`` is ``"covered"`` when every bullet is covered or
    ``"gaps"`` when at least one is missing. ``bullets`` lists the
    per-bullet decisions in slide order. ``raw`` is the verbatim
    response from the LLM, retained for debugging / ``--dump``.
    """

    verdict: str
    bullets: tuple[BulletVerdict, ...] = field(default_factory=tuple)
    raw: str = ""

    @property
    def has_gaps(self) -> bool:
        return self.verdict != "covered"

    @property
    def uncovered_bullets(self) -> tuple[BulletVerdict, ...]:
        return tuple(b for b in self.bullets if not b.covered)

    def to_json(self) -> str:
        """Serialize for storage in the cache's ``gap_details`` column."""
        return json.dumps(
            {
                "verdict": self.verdict,
                "bullets": [
                    {"text": b.text, "covered": b.covered, "reason": b.reason} for b in self.bullets
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, payload: str) -> CoverageVerdict:
        """Reconstruct a verdict from cache JSON."""
        data = json.loads(payload)
        bullets = tuple(
            BulletVerdict(
                text=str(item.get("text", "")),
                covered=bool(item.get("covered", False)),
                reason=str(item.get("reason", "")),
            )
            for item in data.get("bullets", [])
        )
        return cls(verdict=str(data.get("verdict", "gaps")), bullets=bullets)


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


def _build_coverage_user_prompt(bullets: list[str], voiceover: str, *, lang: str) -> str:
    lines = [f"Language: {lang}", "", "Slide bullets:"]
    for i, bullet in enumerate(bullets, start=1):
        lines.append(f"{i}. {bullet}")
    lines.append("")
    lines.append("Voiceover:")
    lines.append(voiceover.strip() or "(no voiceover)")
    return "\n".join(lines)


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


# ---------------------------------------------------------------------------
# Sync judge (Phase 7 of the slide-format-redesign)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncProposal:
    """The LLM's verdict + (when an update is needed) the proposed text.

    ``verdict`` is ``"in_sync"`` when the target cell already adequately
    reflects the source cell (no edit needed) or ``"update"`` when the
    target should be replaced by ``proposed_text``. For the
    ``"in_sync"`` case, ``proposed_text`` echoes the current target —
    callers compare it against the live cell to confirm no diff.

    ``reason`` is a one-line LLM explanation for diagnostics / report
    output. ``raw`` is the verbatim model response retained for
    debugging.
    """

    verdict: str
    proposed_text: str
    reason: str = ""
    raw: str = ""

    @property
    def needs_update(self) -> bool:
        return self.verdict == "update"

    def to_json(self) -> str:
        """Serialize for storage in the cache."""
        return json.dumps(
            {
                "verdict": self.verdict,
                "proposed_text": self.proposed_text,
                "reason": self.reason,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, payload: str) -> SyncProposal:
        data = json.loads(payload)
        verdict = data.get("verdict")
        if verdict not in ("in_sync", "update"):
            verdict = "update" if data.get("proposed_text") else "in_sync"
        return cls(
            verdict=str(verdict),
            proposed_text=str(data.get("proposed_text", "")),
            reason=str(data.get("reason", "")),
        )


class SyncJudge(Protocol):
    """Protocol for anything that can propose a cross-language sync edit.

    Real implementation: :class:`OllamaSyncJudge`. Tests pass a
    :class:`StaticSyncJudge` so the pair-walker and diff producer can be
    exercised without a running Ollama daemon.
    """

    prompt_version: str

    def propose(
        self,
        source_text: str,
        target_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> SyncProposal:
        """Propose a target-cell body that reflects edits made on the source side.

        Raises :class:`OllamaError` (or a protocol-specific equivalent)
        on failure. Callers treat exceptions as "skip this pair with a
        warning", not as fatal.
        """
        ...


class StaticSyncJudge:
    """In-memory :class:`SyncJudge` for tests and bulk scripts.

    Supply a ``mapping`` keyed by a stable string (typically the
    :func:`sync_key` of the request) to a :class:`SyncProposal`. Misses
    raise :class:`OllamaError` so the caller falls back to "could not
    propose". A ``default_proposal`` may be supplied for the "I don't
    care, just give me *something*" case used by bulk scripts.
    """

    def __init__(
        self,
        mapping: dict[str, SyncProposal] | None = None,
        *,
        default_proposal: SyncProposal | None = None,
        prompt_version: str | None = None,
    ):
        # Imported here to avoid a top-level circular import (sync_prompts
        # eventually imports from this module for the user-prompt builder
        # signature). The constant has no runtime cost beyond a lookup.
        from clm.infrastructure.llm.sync_prompts import SYNC_PROMPT_VERSION

        self._mapping = dict(mapping or {})
        self._default_proposal = default_proposal
        self.prompt_version = prompt_version or SYNC_PROMPT_VERSION
        self.calls: list[tuple[str, str, str, str]] = []

    def propose(
        self,
        source_text: str,
        target_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> SyncProposal:
        key = sync_key(
            source_text,
            target_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        self.calls.append((source_text, target_text, source_lang, target_lang))
        if key in self._mapping:
            return self._mapping[key]
        if self._default_proposal is not None:
            return self._default_proposal
        raise OllamaError("no static sync proposal configured for this pair")


def sync_key(
    source_text: str,
    target_text: str,
    *,
    source_lang: str,
    target_lang: str,
) -> str:
    """Stable key used by :class:`StaticSyncJudge` lookups and test asserts."""
    return f"{source_lang}->{target_lang}\n---\n{source_text}\n---\n{target_text}"


class OllamaSyncJudge:
    """:class:`SyncJudge` that talks to a local Ollama daemon."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_SYNC_MODEL,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        # Lazy import keeps the prompt module decoupled from this file's
        # other clients and lets prompt-only consumers import without
        # paying for urllib.
        from clm.infrastructure.llm.sync_prompts import SYNC_PROMPT_VERSION

        self.prompt_version = SYNC_PROMPT_VERSION
        self.model = model
        self.base_url = (base_url or os.environ.get("OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
        self.timeout = timeout

    def propose(
        self,
        source_text: str,
        target_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> SyncProposal:
        from clm.infrastructure.llm.sync_prompts import (
            SYNC_SYSTEM_PROMPT,
            build_sync_user_prompt,
        )

        user_prompt = build_sync_user_prompt(
            source_text=source_text,
            target_text=target_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": SYNC_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
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
            raise OllamaError("Ollama returned empty sync proposal")
        return parse_sync_response(text)


# The only top-level keys the judge contract defines (mirrors the
# ``additionalProperties: False`` of :data:`sync_prompts.SYNC_RESPONSE_SCHEMA`).
# Enforced in :func:`parse_sync_response` as a *truncation guard*: see its
# docstring for why an unexpected key means silent data loss, not just noise.
_SYNC_RESPONSE_KEYS = frozenset({"verdict", "proposed_text", "reason"})


def _decode_sync_object(cleaned: str, raw: str) -> dict[str, Any]:
    """Decode the judge reply to a JSON object, strict-first (Issue #377).

    A faithful reply is a JSON object that parses *whole*, so try that first —
    this keeps a clean body that legitimately contains ``{`` / ``}`` (markdown,
    fenced code) from being re-carved. Only when the strict parse fails do we
    fall back to the lenient ``find("{")`` / ``rfind("}")`` span, for models that
    wrap the object in a prose preamble. Either way the result must be an object;
    a parse failure or a non-object raises :class:`OllamaError` (a surfaced hard
    error, not a silent recovery).
    """
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise OllamaError(
                f"could not locate JSON object in sync response: {raw[:200]!r}"
            ) from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OllamaError(f"sync response is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise OllamaError(f"sync response is not a JSON object: {raw[:200]!r}")
    return parsed


def parse_sync_response(text: str) -> SyncProposal:
    """Parse the judge's raw response into a :class:`SyncProposal`.

    Hardened against **silent truncation / data loss** (Issue #377). The judge
    returns the reconciled cell body as a JSON string; when a hosted model emits
    an *unescaped* inner ``"`` (e.g. wrapping an English term inside German
    ``„ … "`` quotes), the string value terminates early. The old parser carved
    the body with ``find("{")`` / ``rfind("}")`` and ran a tolerant
    ``json.loads`` — which could turn that malformed reply into a *parseable but
    truncated* object (the dropped lines swallowed as extra keys, or a smaller
    balanced span carved out). The truncated prefix was then written to disk with
    ``0 error(s)`` reported. Two defenses, both turning that into a surfaced hard
    error (``OllamaError`` → the apply engine blocks the edit and rolls back
    atomically, the safe path other cells already got):

    1. **Strict-first parse.** A well-formed reply parses whole, so the lenient
       ``find/rfind`` carve only runs as a fallback for models that wrap the
       object in prose — never on a clean (or clean-looking-but-truncated) reply.
    2. **No unexpected top-level keys.** Mirroring the structured-output schema's
       ``additionalProperties: False``: when truncation swallows the dropped
       content into bogus keys, the object now *fails* instead of yielding a
       short ``proposed_text``.

    Still tolerates the benign cases it always did: a code-fenced object, a
    leading prose preamble, and an omitted ``verdict`` (inferred from
    ``proposed_text``).
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    data = _decode_sync_object(cleaned, text)

    # Truncation guard: a faithful reply carries exactly the contract's keys, so
    # any extra one means the value boundaries shifted (an unescaped inner quote
    # closed a string early and the remainder re-parsed as spurious keys). Refuse
    # rather than write a possibly-truncated body. See this function's docstring.
    extra = set(data) - _SYNC_RESPONSE_KEYS
    if extra:
        raise OllamaError(
            "sync response has unexpected top-level keys "
            f"{sorted(extra)!r} — refusing to write a possibly-truncated cell"
        )

    proposed_text = data.get("proposed_text")
    if not isinstance(proposed_text, str):
        raise OllamaError("sync response missing 'proposed_text' string")

    verdict = data.get("verdict")
    if verdict not in ("in_sync", "update"):
        # Tolerate models that omit the verdict but supply a clear
        # proposed_text — default to "update" when text is non-empty,
        # "in_sync" otherwise.
        verdict = "update" if proposed_text.strip() else "in_sync"

    reason = data.get("reason")
    return SyncProposal(
        verdict=verdict,
        proposed_text=proposed_text,
        reason=str(reason) if isinstance(reason, str) else "",
        raw=text,
    )
