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
from collections.abc import Callable
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


def render_markdown(report_json: dict) -> str:
    """Render a ``CompareReport.to_json()`` payload as Markdown.

    Operates on the JSON dict rather than the :class:`CompareReport`
    object so the renderer can consume reports loaded back from disk
    (``clm voiceover report REPORT.json``).  Output follows the shape
    sketched in ``CLM_VOICEOVER_COMPARE_SPEC.md`` §3.4.1: a header,
    per-slide summary table, then grouped sections for each bucket
    (dropped / added / rewritten / manual_review / covered).
    """

    def _escape(s: str | None) -> str:
        if s is None:
            return ""
        return s.replace("|", r"\|").replace("\n", " ")

    source = report_json.get("source", "")
    target = report_json.get("target", "")
    language = report_json.get("language", "")
    status_totals = report_json.get("status_totals", {})
    kind_totals = report_json.get("kind_totals", {})
    slides = report_json.get("slides", [])

    lines: list[str] = []
    title = Path(target).stem if target else "compare report"
    lines.append(f"# Voiceover Comparison — {title}")
    lines.append("")
    lines.append(f"- Source: `{source}`")
    lines.append(f"- Target: `{target}`")
    lines.append(f"- Language: `{language}`")
    lines.append(f"- Slides: {report_json.get('slide_count', len(slides))}")

    totals_cells = [
        f"{status}={status_totals[status]}" for status in status_totals if status_totals[status]
    ]
    if totals_cells:
        lines.append(f"- Bullet totals: {', '.join(totals_cells)}")
    kind_cells = [f"{kind}={kind_totals[kind]}" for kind in kind_totals if kind_totals[kind]]
    if kind_cells:
        lines.append(f"- Slide buckets: {', '.join(kind_cells)}")
    lines.append("")

    # Per-slide summary table.
    lines.append("## Summary per slide")
    lines.append("")
    lines.append("| # | Slide | Kind | Covered | Rewritten | Added | Dropped | Review |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|")
    for idx, slide in enumerate(slides, start=1):
        counts: dict[str, int] = dict.fromkeys(
            ("covered", "rewritten", "added", "dropped", "manual_review"), 0
        )
        for outcome in slide.get("outcomes", []):
            status = outcome.get("status")
            if status in counts:
                counts[status] += 1
        lines.append(
            "| {i} | `{key}` | {kind} | {c} | {rw} | {a} | {d} | {mr} |".format(
                i=idx,
                key=_escape(slide.get("key", "")),
                kind=slide.get("kind", ""),
                c=counts["covered"] or "",
                rw=counts["rewritten"] or "",
                a=counts["added"] or "",
                d=counts["dropped"] or "",
                mr=counts["manual_review"] or "",
            )
        )
    lines.append("")

    # Grouped per-bucket sections. Order matters: drift the user cares
    # about most first (dropped = lost content, added = new narration).
    bucket_order = [
        ("dropped", "Dropped in current slides (present in source, absent in target)"),
        ("added", "Added in current slides (absent in source)"),
        ("rewritten", "Rewritten (same concept, substantively different wording)"),
        ("manual_review", "Manual review (LLM could not classify confidently)"),
    ]
    for status, heading in bucket_order:
        matching_outcomes: list[tuple[dict, dict]] = []
        for slide in slides:
            for outcome in slide.get("outcomes", []):
                if outcome.get("status") == status:
                    matching_outcomes.append((slide, outcome))
        if not matching_outcomes:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        for slide, outcome in matching_outcomes:
            lines.append(f"### `{slide.get('key', '?')}`")
            if outcome.get("source"):
                lines.append(f"- **source:** {outcome['source']}")
            if outcome.get("target"):
                lines.append(f"- **target:** {outcome['target']}")
            if outcome.get("note"):
                lines.append(f"- _{outcome['note']}_")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


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
    """Async library entry point for ``clm voiceover compare``.

    Callable from any running event loop (e.g. the MCP server); the
    sync :func:`run_compare` wrapper uses ``asyncio.run`` for CLI
    callers.  ``progress_cb`` receives short status strings; callers
    without a UI can pass ``None``.
    """
    from clm.notebooks.slide_parser import parse_slides
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
