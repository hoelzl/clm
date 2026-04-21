"""Port voiceover content from one slide revision onto another.

Sibling to :mod:`clm.voiceover.merge` with different invariants: the
input is already clean (prior bullets were polished by an earlier sync),
so there's no noise filtering to do — the job is integration, not
cleanup. The module owns :func:`polish_and_port`, the primitive that
runs one per-slide LLM call; the higher-level ``port-voiceover`` CLI
command composes it across an entire file.

Shares the per-slide input packing and the structured response schema
with the future ``clm voiceover compare`` judge via
:mod:`clm.voiceover.bullet_schema`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from clm.voiceover.bullet_schema import (
    BulletOutcome,
    PerSlidePack,
    parse_structured_response,
)

logger = logging.getLogger(__name__)

DEFAULT_PORT_MODEL = "anthropic/claude-sonnet-4-6"


@dataclass
class PortResult:
    """Result of porting prior voiceover onto a HEAD slide.

    ``bullets`` is the merged bullet text ready to be written to the
    voiceover cell. ``outcomes`` is the per-bullet provenance list
    from the LLM. ``notes`` is an optional free-text summary. When the
    LLM call fails or returns an unusable response, ``bullets`` falls
    back to the baseline (or the prior, if baseline is empty) and
    ``error`` describes what happened.
    """

    slide_id: str
    bullets: str
    outcomes: list[BulletOutcome] = field(default_factory=list)
    notes: str | None = None
    error: str | None = None


def _load_system_prompt(language: str) -> str:
    prompt_dir = Path(__file__).parent / "prompts"
    candidate = prompt_dir / f"port_{language}.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    fallback = prompt_dir / "port_en.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No port prompt found for language '{language}'")


def _fallback_bullets(baseline: str, prior: str) -> str:
    """Pick the best-effort output when the LLM call is skipped or fails."""
    base = baseline.strip()
    pri = prior.strip()
    if base and pri:
        return base  # Conservative: keep HEAD, don't overwrite with unmerged prior.
    if base:
        return base
    return pri


async def polish_and_port(
    baseline_bullets: str,
    prior_voiceover: str,
    slide_content_head: str,
    slide_content_prior: str | None,
    language: str,
    content_changed: bool,
    *,
    slide_id: str = "",
    model: str = DEFAULT_PORT_MODEL,
    temperature: float = 0.3,
    api_base: str | None = None,
    api_key: str | None = None,
    langfuse_context: dict | None = None,
) -> PortResult:
    """Port prior voiceover onto a HEAD slide via one LLM call.

    Per the proposal §3.3: different invariants from ``polish_and_merge``
    — no noise filtering, because the prior voiceover is already clean.
    The call must preserve every substantive prior bullet (porting,
    not summarising) and must respect HEAD baseline content when
    present (merge around it rather than over it).

    Args:
        baseline_bullets: Voiceover already present at HEAD ("" when empty).
        prior_voiceover: The polished voiceover from the source revision.
        slide_content_head: HEAD slide text (for LLM context).
        slide_content_prior: Source-revision slide text. ``None`` means
            "unchanged or unavailable" — combined with ``content_changed``
            to decide whether to pass prior content to the prompt.
        language: Language code ("de" or "en").
        content_changed: True when the slide's visible content changed
            between source and target. Signals to the LLM that prior
            bullets may no longer fit.
        slide_id: Identifier for traceability.
        model: LLM model identifier.
        temperature: Sampling temperature.
        api_base: API base URL (e.g. OpenRouter).
        api_key: API key override.
        langfuse_context: Optional Langfuse kwargs forwarded to ``create()``.

    Returns:
        PortResult with merged bullets and per-bullet outcomes.
    """
    from clm.infrastructure.llm.client import LLMError, _build_client

    # No LLM call needed when both sides are empty.
    if not prior_voiceover.strip() and not baseline_bullets.strip():
        return PortResult(slide_id=slide_id, bullets="", outcomes=[])

    # If there's nothing to port, degrade to baseline unchanged.
    if not prior_voiceover.strip():
        return PortResult(slide_id=slide_id, bullets=baseline_bullets.strip(), outcomes=[])

    pack = PerSlidePack(
        slide_id=slide_id,
        language=language,
        baseline_bullets=baseline_bullets,
        prior_bullets=prior_voiceover,
        slide_content_head=slide_content_head,
        slide_content_prior=slide_content_prior if content_changed else None,
        content_changed=content_changed,
    )
    system_prompt = _load_system_prompt(language)
    user_message = pack.build_user_message()

    client = _build_client(api_base=api_base, api_key=api_key)

    logger.debug(
        "polish_and_port slide=%s baseline=%d chars prior=%d chars changed=%s",
        slide_id,
        len(baseline_bullets),
        len(prior_voiceover),
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
        err = f"Port LLM call failed for slide {slide_id}: {exc}"
        logger.warning("%s", err)
        fallback = _fallback_bullets(baseline_bullets, prior_voiceover)
        if isinstance(exc, LLMError):
            raise
        return PortResult(slide_id=slide_id, bullets=fallback, error=err)

    raw = str(response.choices[0].message.content).strip()
    fallback = _fallback_bullets(baseline_bullets, prior_voiceover)
    bullets, outcomes, notes = parse_structured_response(raw, default_bullets=fallback)

    if not bullets.strip():
        logger.warning("polish_and_port returned empty bullets for %s; using fallback", slide_id)
        bullets = fallback

    return PortResult(
        slide_id=slide_id,
        bullets=bullets,
        outcomes=outcomes,
        notes=notes,
    )
