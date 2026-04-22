"""Evaluate voiceover content between two slide-file revisions.

Read-only sibling to :mod:`clm.voiceover.port`. Matches slides between
source and target, then asks the LLM to label the bullet-level
relationship on each matched pair (``covered`` / ``rewritten`` /
``added`` / ``dropped`` / ``manual_review``). The primitive here is
:func:`judge_slide_pair`; the higher-level ``clm voiceover compare``
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
from dataclasses import dataclass, field
from pathlib import Path

from clm.voiceover.bullet_schema import (
    BulletOutcome,
    BulletStatus,
    PerSlidePack,
    parse_structured_response,
)
from clm.voiceover.slide_matcher import MatchKind

logger = logging.getLogger(__name__)

DEFAULT_COMPARE_MODEL = "anthropic/claude-sonnet-4-6"


@dataclass
class SlideComparison:
    """Per-slide result of a compare run.

    ``outcomes`` is empty when no LLM call happened (both sides empty,
    unmatched slide, or an error). ``error`` records a non-raising
    LLM failure so the report can flag it without aborting.
    """

    key: str
    kind: MatchKind
    target_index: int | None
    source_index: int | None
    content_similarity: float = 0.0
    outcomes: list[BulletOutcome] = field(default_factory=list)
    notes: str | None = None
    error: str | None = None

    def status_counts(self) -> dict[BulletStatus, int]:
        tot: dict[BulletStatus, int] = dict.fromkeys(BulletStatus, 0)
        for outcome in self.outcomes:
            tot[outcome.status] = tot.get(outcome.status, 0) + 1
        return tot

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "target_index": self.target_index,
            "source_index": self.source_index,
            "content_similarity": self.content_similarity,
            "outcomes": [o.to_json() for o in self.outcomes],
            "notes": self.notes,
            "error": self.error,
        }


@dataclass
class CompareReport:
    """Aggregated result of ``clm voiceover compare``."""

    source: Path
    target: Path
    language: str
    slides: list[SlideComparison] = field(default_factory=list)

    def status_totals(self) -> dict[BulletStatus, int]:
        tot: dict[BulletStatus, int] = dict.fromkeys(BulletStatus, 0)
        for comp in self.slides:
            for outcome in comp.outcomes:
                tot[outcome.status] = tot.get(outcome.status, 0) + 1
        return tot

    def kind_totals(self) -> dict[MatchKind, int]:
        tot: dict[MatchKind, int] = dict.fromkeys(MatchKind, 0)
        for comp in self.slides:
            tot[comp.kind] = tot.get(comp.kind, 0) + 1
        return tot

    def to_json(self) -> dict:
        totals = self.status_totals()
        kinds = self.kind_totals()
        return {
            "source": str(self.source),
            "target": str(self.target),
            "language": self.language,
            "slide_count": len(self.slides),
            "status_totals": {s.value: totals[s] for s in BulletStatus},
            "kind_totals": {k.value: kinds[k] for k in MatchKind},
            "slides": [c.to_json() for c in self.slides],
        }


def _load_compare_prompt(language: str) -> str:
    prompt_dir = Path(__file__).parent / "prompts"
    candidate = prompt_dir / f"compare_{language}.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    fallback = prompt_dir / "compare_en.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No compare prompt found for language '{language}'")


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
    from clm.infrastructure.llm.client import LLMError, _build_client

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

    client = _build_client(api_base=api_base, api_key=api_key)

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
