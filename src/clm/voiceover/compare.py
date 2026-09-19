"""Legacy embedded-model comparison, reachable through harvest autopilot only.

Read-only sibling to :mod:`clm.voiceover.port`. Matches slides between
source and target, then asks the LLM to label the bullet-level
relationship on each matched pair (``covered`` / ``rewritten`` /
``added`` / ``dropped`` / ``manual_review``). The primitive here is
:func:`judge_slide_pair`; the higher-level ``clm harvest autopilot compare``
CLI command composes it across an entire file and aggregates
:class:`CompareReport`.

Shares the per-slide packing and structured-response schema with
:mod:`clm.voiceover.port` via :mod:`clm.voiceover.bullet_schema` — the
only things that differ between port and compare are the prompt
(``compare_{lang}.md`` vs. ``port_{lang}.md``) and the disposition of
the result (written vs. reported).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from clm.voiceover.bullet_schema import (
    BulletOutcome,
    PerSlidePack,
    parse_structured_response,
)
from clm.voiceover.compare_report import CompareReport, SlideComparison, render_markdown
from clm.voiceover.slide_matcher import MatchKind

__all__ = [
    "CompareReport",
    "SlideComparison",
    "render_markdown",
    "run_compare",
    "run_compare_async",
    "judge_slide_pair",
]

logger = logging.getLogger(__name__)

DEFAULT_COMPARE_MODEL = "anthropic/claude-sonnet-4-6"


def _load_compare_prompt(language: str) -> str:
    prompt_dir = Path(__file__).parent / "prompts"
    candidate = prompt_dir / f"compare_{language}.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    fallback = prompt_dir / "compare_en.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No compare prompt found for language '{language}'")


async def run_compare_async(
    *,
    source: Path,
    target: Path,
    lang: str,
    model: str | None = None,
    api_base: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> CompareReport:
    """Async library entry point for ``clm harvest autopilot compare``.

    Callable from a running event loop; the MCP surface does not use it. The
    sync :func:`run_compare` wrapper uses ``asyncio.run`` for CLI
    callers.  ``progress_cb`` receives short status strings; callers
    without a UI can pass ``None``.
    """
    from clm.core.slide_text.slide_parser import parse_slides
    from clm.voiceover.slide_matcher import match_slides

    source_groups = parse_slides(source, lang, include_header=True)
    target_groups = parse_slides(target, lang, include_header=True)
    matches = match_slides(source_groups, target_groups)

    if progress_cb is not None:
        progress_cb(
            f"Comparing {target.name} against {source.name} "
            f"({len(target_groups)} target / {len(source_groups)} source slides)"
        )

    judge_kwargs: dict = {}
    if model:
        judge_kwargs["model"] = model
    if api_base:
        judge_kwargs["api_base"] = api_base

    slides: list[SlideComparison] = []
    for match in matches:
        if match.kind in (
            MatchKind.REMOVED_AT_HEAD,
            MatchKind.NEW_AT_HEAD,
            MatchKind.MANUAL_REVIEW,
        ):
            slides.append(
                SlideComparison(
                    key=match.key,
                    kind=match.kind,
                    target_index=match.target_index,
                    source_index=match.source_index,
                    content_similarity=match.content_similarity,
                )
            )
            continue

        assert match.target_group is not None
        assert match.source_group is not None

        baseline = match.target_group.notes_text
        prior = match.source_group.notes_text
        outcomes, notes, err = await judge_slide_pair(
            prior_bullets=prior,
            baseline_bullets=baseline,
            slide_content_head=match.target_group.text_content,
            slide_content_prior=(
                match.source_group.text_content if match.content_changed else None
            ),
            language=lang,
            content_changed=match.content_changed,
            slide_id=f"{target.stem}/{match.target_index}",
            **judge_kwargs,
        )
        slides.append(
            SlideComparison(
                key=match.key,
                kind=match.kind,
                target_index=match.target_index,
                source_index=match.source_index,
                content_similarity=match.content_similarity,
                outcomes=outcomes,
                notes=notes,
                error=err,
            )
        )

    return CompareReport(source=source, target=target, language=lang, slides=slides)


def run_compare(
    *,
    source: Path,
    target: Path,
    lang: str,
    model: str | None = None,
    api_base: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> CompareReport:
    """Sync wrapper around :func:`run_compare_async` for CLI callers."""
    import asyncio

    return asyncio.run(
        run_compare_async(
            source=source,
            target=target,
            lang=lang,
            model=model,
            api_base=api_base,
            progress_cb=progress_cb,
        )
    )


async def judge_slide_pair(
    prior_bullets: str,
    baseline_bullets: str,
    slide_content_head: str,
    slide_content_prior: str | None,
    language: str,
    content_changed: bool,
    *,
    slide_id: str = "",
    model: str = DEFAULT_COMPARE_MODEL,
    temperature: float = 0.1,
    api_base: str | None = None,
    api_key: str | None = None,
    langfuse_context: dict | None = None,
) -> tuple[list[BulletOutcome], str | None, str | None]:
    """Ask the LLM to label the relationship between two bullet sets.

    Returns ``(outcomes, notes, error)``. When both sides are empty,
    no LLM call is made and the outcomes list is empty. ``error`` is
    populated on non-raising failures (network, malformed JSON); the
    caller should surface it in the final report rather than abort.
    """
    from clm.infrastructure.llm.client import LLMError, build_client

    if not prior_bullets.strip() and not baseline_bullets.strip():
        return [], None, None

    pack = PerSlidePack(
        slide_id=slide_id,
        language=language,
        baseline_bullets=baseline_bullets,
        prior_bullets=prior_bullets,
        slide_content_head=slide_content_head,
        slide_content_prior=slide_content_prior if content_changed else None,
        content_changed=content_changed,
    )
    system_prompt = _load_compare_prompt(language)
    user_message = pack.build_user_message()

    client = build_client(api_base=api_base, api_key=api_key)

    logger.debug(
        "judge_slide_pair slide=%s baseline=%d chars prior=%d chars changed=%s",
        slide_id,
        len(baseline_bullets),
        len(prior_bullets),
        content_changed,
    )

    create_kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if langfuse_context:
        create_kwargs.update(langfuse_context)

    try:
        response = await client.chat.completions.create(**create_kwargs)
    except Exception as exc:
        err = f"Compare LLM call failed for slide {slide_id}: {exc}"
        logger.warning("%s", err)
        if isinstance(exc, LLMError):
            raise
        return [], None, err

    raw = str(response.choices[0].message.content).strip()
    _, outcomes, notes = parse_structured_response(raw, default_bullets="")
    return outcomes, notes, None
