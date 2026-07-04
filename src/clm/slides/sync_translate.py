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
    "build_translation_system_prompt",
    "build_translation_user_prompt",
]

# Claude Sonnet via OpenRouter — the voiceover propagate path's precedent.
DEFAULT_TRANSLATION_MODEL = "anthropic/claude-sonnet-4-6"

# Base cache/prompt version. A guidance glossary folds a fingerprint onto this
# (see ``OpenRouterSlideTranslator.__post_init__``); the bare value is kept when
# no glossary is supplied so the existing Python cache stays valid.
_BASE_PROMPT_VERSION = "translate-v1"


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
    With neither, :meth:`translate` raises — mirroring the other static test doubles.
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
    # The deck's programming language (e.g. "python", "csharp", "cpp"). Drives the
    # comment prefix and language name named in the prompt, so a //-deck is told to
    # preserve "// " prefixes and a C# code cell is translated as C#, not Python.
    prog_lang: str = "python"
    # Optional target-language conventions (a style note + glossary), rendered as
    # prompt text by the caller and appended verbatim to the system prompt. clm
    # stays domain-agnostic: it knows nothing of "Sie" or "Dictionary" — the course
    # repo supplies the text (see ``clm slides translate --glossary``). When set, it
    # is folded into ``prompt_version`` so a different glossary keys a different cache
    # entry and editing the glossary invalidates by cache miss.
    #
    # ``guidance`` applies to a translation in **any** direction — the one-direction
    # ``translate`` / ``bootstrap`` case, which resolves a single target glossary.
    # ``guidance_by_lang`` (target_lang → conventions) is the **bidirectional**
    # ``sync`` case: a new DE slide is translated to EN with the EN conventions and a
    # new EN slide to DE with the DE conventions, in one pass. The two are
    # alternatives — when ``guidance_by_lang`` is non-empty it wins per target and
    # ``guidance`` is ignored; see :meth:`_guidance_for`.
    guidance: str = ""
    guidance_by_lang: dict[str, str] = field(default_factory=dict)
    # Derived in ``__post_init__`` from the base version + a guidance fingerprint.
    # ``init=False`` so it is never passed in; the cache wrapper reads it.
    prompt_version: str = field(init=False, default=_BASE_PROMPT_VERSION)

    def __post_init__(self) -> None:
        self.prompt_version = self._compute_prompt_version()

    def _compute_prompt_version(self) -> str:
        """The cache key version: ``translate-v1`` plus a guidance fingerprint.

        Two DISTINCT namespaces, so a single-string guidance and a per-language map
        can never share a key whatever their text:

        - per-language ``guidance_by_lang`` (bidirectional sync) → ``v1:gm<fp>`` over
          the whole cleaned map, ordered by language. It takes precedence when it has
          content, matching :meth:`_guidance_for`.
        - single-direction ``guidance`` (translate/bootstrap) → ``v1:g<sha(text)>``,
          the ORIGINAL shape, so an existing ``translate`` cache stays valid.
        - neither → bare ``v1`` (no glossary, no flag-day invalidation).

        The map is fingerprinted WHOLE (not per target), so the key is computed once
        at construction. ``clm slides sync`` runs this translator **uncached**, so the
        map key is inert there today; only the cached ``translate`` delegated-sync
        path keys on it, where editing one language's glossary also re-translates the
        other direction's adds (a cheap over-invalidation — incremental syncs add few
        slides). A future cached standalone sync wanting to avoid that would need a
        per-target signature (a ``prompt_version`` that varies per call).
        """
        cleaned = {k: v.strip() for k, v in self.guidance_by_lang.items() if v.strip()}
        if cleaned:
            # \x1e/\x1f are record/unit separators; the "gm" namespace keeps a
            # one-entry map (encoded "lang\x1etext") from ever colliding with a
            # single-string guidance of that exact text.
            joined = "\x1f".join(f"{k}\x1e{cleaned[k]}" for k in sorted(cleaned))
            fp = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
            return f"{_BASE_PROMPT_VERSION}:gm{fp}"
        guidance = self.guidance.strip()
        if guidance:
            fp = hashlib.sha256(guidance.encode("utf-8")).hexdigest()[:12]
            return f"{_BASE_PROMPT_VERSION}:g{fp}"
        return _BASE_PROMPT_VERSION

    def _guidance_for(self, target_lang: str) -> str:
        """The conventions text to append for a translation INTO ``target_lang``.

        Per-language ``guidance_by_lang`` (bidirectional sync) wins when it has any
        content; otherwise the single ``guidance`` string (one-direction
        translate/bootstrap) applies regardless of direction. Whitespace-only is
        treated as none, so an empty/absent glossary appends nothing.
        """
        if self.guidance_by_lang and any(v.strip() for v in self.guidance_by_lang.values()):
            return self.guidance_by_lang.get(target_lang, "").strip()
        return self.guidance.strip()

    def _system_message(self, role: str, source_lang: str, target_lang: str) -> str:
        """Assemble the system prompt for a cell ``role`` and direction.

        Delegates to the module-level :func:`build_translation_system_prompt` (the
        model-free seam shared with the agent-facing ``clm slides sync task``
        surface), resolving this translator's per-target glossary first via
        :meth:`_guidance_for`.
        """
        return build_translation_system_prompt(
            role=role,
            source_lang=source_lang,
            target_lang=target_lang,
            prog_lang=self.prog_lang,
            guidance=self._guidance_for(target_lang),
        )

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
        system = self._system_message(role, source_lang, target_lang)

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


def build_translation_system_prompt(
    *,
    role: str,
    source_lang: str,
    target_lang: str,
    prog_lang: str = "python",
    guidance: str = "",
) -> str:
    """Assemble the new-slide translation system prompt for a cell ``role``/direction.

    The model-free seam shared by the embedded :class:`OpenRouterSlideTranslator` and
    the agent-facing ``clm slides sync task`` builder (epic #440, decision B): pick the
    role-specific base prompt (code / title / prose), fill the language and
    comment-token descriptors for ``prog_lang``, then append any caller-supplied
    ``guidance`` (a target-language glossary / style note) **after** ``.format()`` so
    braces in the glossary (JSON, f-strings, code) are never read as format fields.
    Pure and network-free — both callers and a unit test can use it directly.
    """
    comment_prefix, prog_lang_name = _prog_lang_descriptors(prog_lang)
    system = _system_prompt_for(role).format(
        source_lang=_LANG_NAMES.get(source_lang, source_lang),
        target_lang=_LANG_NAMES.get(target_lang, target_lang),
        role=role,
        comment_prefix=comment_prefix,
        prog_lang_name=prog_lang_name,
    )
    guidance = guidance.strip()
    if guidance:
        system = f"{system}\n\n{guidance}"
    return system


def build_translation_user_prompt(source_body: str) -> str:
    """The user-side message for a new-slide translation: the source cell body verbatim.

    The translator sends the source body to the model unchanged — every instruction
    lives in the system prompt — so this thin builder simply names that contract,
    keeping the agent-facing ``task`` surface and the embedded translator in lockstep.
    """
    return source_body


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


# The deck title is the bare string argument of the ``header_<lang>("…")`` macro —
# NOT a percent-format cell body. Running it through ``_SYSTEM_PROMPT`` (which
# announces "every line is prefixed with '# '") makes the model hallucinate a
# leading "# " and treat the title as a directive to preserve, so it comes back
# untranslated and prefixed (e.g. ``header_de("# Your First Web Service")``). A
# dedicated title prompt fixes both: plain phrase in, plain translated phrase out.
_TITLE_SYSTEM_PROMPT = (
    "You translate a single slide-deck title from {source_lang} to {target_lang} for a "
    "{prog_lang_name} programming course. Return ONLY the translated title as a short "
    "plain phrase: no Markdown, no leading '{comment_prefix}' or other comment prefix, "
    "no surrounding quotes, and no commentary. Preserve the title's own terminal "
    "punctuation — keep a trailing '?' or '!' if the source title has one (but do not "
    "add any). Keep code identifiers, library names, and product names unchanged."
)


def _system_prompt_for(role: str) -> str:
    """Pick the system prompt for a cell ``role``.

    Code cells get the identifier-preserving code prompt; the ``"title"`` pseudo-role
    (the ``header_<lang>`` macro argument) gets the bare-phrase title prompt; all other
    roles use the Markdown prose prompt.
    """
    if role == "code":
        return _CODE_SYSTEM_PROMPT
    if role == "title":
        return _TITLE_SYSTEM_PROMPT
    return _SYSTEM_PROMPT


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
