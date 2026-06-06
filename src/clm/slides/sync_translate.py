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

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from clm.infrastructure.llm.retry import call_with_retries

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import TranslationCache

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TRANSLATION_MODEL",
    "CachingSlideTranslator",
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
    prog_lang: str = "python"  # protocol-uniformity; StaticSlideTranslator ignores it

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
    # The deck's programming language (e.g. "python", "csharp", "cpp"). Drives the
    # comment prefix and language name named in the prompt, so a //-deck is told to
    # preserve "// " prefixes and a C# code cell is translated as C#, not Python.
    prog_lang: str = "python"

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
        comment_prefix, prog_lang_name = _prog_lang_descriptors(self.prog_lang)
        system = _system_prompt_for(role).format(
            source_lang=_LANG_NAMES.get(source_lang, source_lang),
            target_lang=_LANG_NAMES.get(target_lang, target_lang),
            role=role,
            comment_prefix=comment_prefix,
            prog_lang_name=prog_lang_name,
        )

        def _create():
            return self._client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": source_body},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

        # Retry transient failures so one flaky call does not defer a new slide.
        try:
            response = call_with_retries(
                _create, exc=Exception, label=f"slide translation ({self.model})"
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
    "line is prefixed with '{comment_prefix}' and may contain Markdown (headings like "
    "'{comment_prefix}## Title', bullet lists, inline code). Translate ONLY the "
    "natural-language prose. Preserve verbatim: the '{comment_prefix}' line prefixes, "
    "Markdown structure and heading levels, code spans and identifiers, URLs, and slide "
    "directives. Do not add, drop, or reorder lines. Return only the translated cell "
    "body, no commentary, no code fences."
)

# Code cells are NOT comment-prefixed: they are runnable source. Only the
# human-facing text inside them (string literals shown to the learner, comments)
# differs across languages; the code itself is language-neutral and must stay
# byte-identical so the cell still runs and the two decks share one logic.
_CODE_SYSTEM_PROMPT = (
    "You localize a single {prog_lang_name} code cell of a {source_lang} "
    "programming-course slide deck into its {target_lang} counterpart. Return runnable "
    "{prog_lang_name}, NOT Markdown. Translate ONLY human-facing natural-language text: "
    "the contents of string literals that are shown to a learner (e.g. example prompts, "
    "questions, user-visible messages) and code comments. Keep EVERYTHING else "
    "byte-identical: all identifiers, function/variable/class names, keywords, imports, "
    "attribute and method names, dict keys, operators, numbers, structure, indentation, "
    "and string literals that are technical (model names, URLs, format strings, JSON "
    'keys like "role"/"content"/"system"/"user"). Do not add, drop, reorder, '
    "or reformat lines. Return only the cell body, no commentary, no code fences."
)


def _system_prompt_for(role: str) -> str:
    """Pick the system prompt for a cell ``role`` (code cells are not Markdown)."""
    return _CODE_SYSTEM_PROMPT if role == "code" else _SYSTEM_PROMPT


def _prog_lang_descriptors(prog_lang: str) -> tuple[str, str]:
    """Return ``(comment_prefix, prog_lang_name)`` for prompt templating.

    e.g. ``"python"`` -> ``("# ", "Python")``; ``"csharp"`` -> ``("// ", "C#")``.
    Falls back to ``("# ", <prog_lang>)`` for an unknown language.
    """
    from clm.workers.notebook.utils.prog_lang_utils import language_info, line_comment_for

    try:
        prefix = line_comment_for(prog_lang) + " "
    except (KeyError, ValueError):
        prefix = "# "
    try:
        name = str(language_info(prog_lang).get("name", prog_lang))
    except (KeyError, ValueError):
        name = prog_lang
    # language_info names are inconsistently cased ("python"/"C#"/"Java"); title
    # only the all-lowercase ones so "C#"/"C++" keep their casing.
    if name.islower():
        name = name.capitalize()
    return prefix, name


@dataclass
class CachingSlideTranslator:
    """Wrap a :class:`SlideTranslator` with a persistent :class:`TranslationCache`.

    Satisfies the ``SlideTranslator`` protocol itself, so it is a drop-in for the
    engine: a cache hit skips the network, a miss calls ``inner`` and stores the
    successful result. The cache key folds the wrapped translator's ``model`` (if
    any) into ``prompt_version`` so two models — or a future prompt revision —
    never share an entry. Used by ``clm slides translate`` so a re-run (or a
    bootstrap of a deck that shares cells with a previously translated one) is
    cheap; the engine stays cache-agnostic (it depends only on the protocol).
    """

    inner: SlideTranslator
    cache: TranslationCache
    # A settable attribute (not a property) so this satisfies the SlideTranslator
    # protocol's ``prompt_version: str``. Folds the wrapped model into the version
    # so a model switch invalidates by cache miss rather than returning the other
    # model's translation. Computed once at construction.
    prompt_version: str = field(init=False)

    def __post_init__(self) -> None:
        model = getattr(self.inner, "model", "")
        base = f"{self.inner.prompt_version}:{model}" if model else self.inner.prompt_version
        # Fold the deck's prog_lang into the key so a C# code cell and a Python one
        # with identical source never share an entry. Keep "python" un-suffixed so
        # the existing Python cache stays valid (no flag-day invalidation).
        prog_lang = getattr(self.inner, "prog_lang", "python")
        self.prompt_version = f"{base}:{prog_lang}" if prog_lang and prog_lang != "python" else base

    def translate(
        self,
        *,
        source_body: str,
        source_lang: str,
        target_lang: str,
        role: str,
    ) -> str:
        content_hash = hashlib.sha256(source_body.encode("utf-8")).hexdigest()
        version = self.prompt_version
        hit = self.cache.get(content_hash, version, source_lang, target_lang, role)
        if hit is not None:
            return hit
        result = self.inner.translate(
            source_body=source_body,
            source_lang=source_lang,
            target_lang=target_lang,
            role=role,
        )
        # Only successful results reach here (inner raises TranslationError on
        # failure), so the cache never stores a bad translation.
        self.cache.put(content_hash, version, source_lang, target_lang, role, result)
        return result
