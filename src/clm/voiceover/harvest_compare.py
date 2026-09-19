"""``clm harvest task --kind compare`` + ``compare-accept`` (#960).

The agent-first replacement for the embedded-LLM ``harvest compare``: the
deterministic pairing (:func:`~clm.voiceover.slide_matcher.match_slides`)
decides which slides correspond; each framed task hands the agent both
bullet sets and asks for relation labels; ``compare-accept`` validates the
verdict document (shape + freshness + coverage of every framed pair) and
writes the canonical compare-report JSON — the same shape
``clm harvest compare-report`` re-renders. Compare stays what it always
was: auditing. It writes a report artifact, never a deck.

Freshness is by file content: the framed ``source_fingerprint`` /
``target_fingerprint`` are sha256 over the slide files' bytes, echoed in
the answer and re-checked before anything is written.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from attrs import define, field

from clm.slides.agent_task import VALIDATORS, AnswerRejected
from clm.voiceover.bullet_schema import BulletOutcome, BulletStatus
from clm.voiceover.slide_matcher import MatchKind, match_slides

__all__ = [
    "COMPARE_ANSWER_SCHEMA",
    "COMPARE_ANSWER_VALIDATOR",
    "CompareAnswer",
    "CompareRejected",
    "Verdict",
    "build_compare_report_payload",
    "build_compare_tasks",
    "file_fingerprint",
    "parse_compare_answer",
]

#: The validator label framed compare tasks announce; registered below.
COMPARE_ANSWER_VALIDATOR = "harvest-compare"

#: Match kinds the agent judges. new_at_head / removed_at_head are
#: deterministic buckets with no bullets to relate; manual_review pairs
#: carry no source (the matcher discarded the candidates).
_JUDGED_KINDS = (MatchKind.UNCHANGED, MatchKind.MODIFIED)


class CompareRejected(AnswerRejected):
    """The compare answer was rejected; the message names the reason."""


@define(frozen=True)
class Verdict:
    """The agent's labels for one framed slide pair."""

    item: str  # the task's match key
    outcomes: list[BulletOutcome] = field(factory=list)
    notes: str | None = None


@define(frozen=True)
class CompareAnswer:
    """The validated compare answer document."""

    source_fingerprint: str
    target_fingerprint: str
    verdicts: list[Verdict]


def file_fingerprint(path: Path) -> str:
    """Freshness token over a slide file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _compare_instructions(lang: str) -> str:
    prompt = Path(__file__).parent / "prompts" / f"compare_{lang}.md"
    if not prompt.exists():
        prompt = Path(__file__).parent / "prompts" / "compare_en.md"
    return prompt.read_text(encoding="utf-8")


#: The per-pair verdict contract framed in every compare task.
COMPARE_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["item", "outcomes"],
    "properties": {
        "item": {"type": "string", "description": "the task's match key, e.g. id:intro"},
        "outcomes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["status"],
                "properties": {
                    "status": {"enum": [s.value for s in BulletStatus]},
                    "target": {"type": ["string", "null"]},
                    "source": {"type": ["string", "null"]},
                    "note": {"type": ["string", "null"]},
                },
            },
        },
        "notes": {"type": ["string", "null"]},
    },
}


def _frame_compare(match, *, lang: str) -> dict[str, Any]:
    target = match.target_group
    source = match.source_group
    assert target is not None and source is not None
    inputs: dict[str, Any] = {
        "language": lang,
        "baseline_bullets": target.notes_text,
        "prior_bullets": source.notes_text,
        "slide": {"title": target.title, "content": target.text_content},
        "content_changed": match.content_changed,
        "content_similarity": match.content_similarity,
    }
    if match.content_changed:
        inputs["prior_slide"] = {"title": source.title, "content": source.text_content}
    return {
        "item": match.key,
        "kind": "compare",
        "class": match.kind.value,
        "validator": COMPARE_ANSWER_VALIDATOR,
        "language": lang,
        "instructions": _compare_instructions(lang),
        "inputs": inputs,
        "answer_schema": COMPARE_ANSWER_SCHEMA,
    }


def _is_framed(match) -> bool:
    """The pairs a compare task frames — the report builder's coverage
    check applies the same rule (a both-sides-empty pair has nothing to
    label and rows in the report with empty outcomes)."""
    if match.kind not in _JUDGED_KINDS:
        return False
    assert match.target_group is not None and match.source_group is not None
    return bool(match.target_group.notes_text.strip() or match.source_group.notes_text.strip())


def build_compare_tasks(source_groups, target_groups, *, lang: str) -> list[dict[str, Any]]:
    """Frame one labeling task per judged slide pair (read-only).

    ``unchanged`` and ``modified`` pairs are framed (bullet sets can drift
    even when slide content did not); the deterministic buckets and
    ``manual_review`` pairs are not — they appear in the accepted report
    with empty outcomes, exactly as the embedded-LLM compare produced.
    """
    return [
        _frame_compare(match, lang=lang)
        for match in match_slides(source_groups, target_groups)
        if _is_framed(match)
    ]


def parse_compare_answer(payload: Any) -> CompareAnswer:
    """Validate the verdict document; raise :class:`CompareRejected`."""
    if not isinstance(payload, dict):
        raise CompareRejected("the answer must be a JSON object")
    if payload.get("kind") != "compare":
        raise CompareRejected("'kind' must be \"compare\"")
    source_fp = payload.get("source_fingerprint")
    target_fp = payload.get("target_fingerprint")
    if not isinstance(source_fp, str) or not source_fp:
        raise CompareRejected(
            "'source_fingerprint' must be the string echoed from the task envelope"
        )
    if not isinstance(target_fp, str) or not target_fp:
        raise CompareRejected(
            "'target_fingerprint' must be the string echoed from the task envelope"
        )
    raw_verdicts = payload.get("verdicts")
    if not isinstance(raw_verdicts, list) or not raw_verdicts:
        raise CompareRejected("'verdicts' must be a non-empty list of per-pair entries")
    verdicts: list[Verdict] = []
    seen: set[str] = set()
    valid_statuses = {s.value for s in BulletStatus}
    for i, entry in enumerate(raw_verdicts):
        where = f"verdicts[{i}]"
        if not isinstance(entry, dict):
            raise CompareRejected(f"{where} must be an object with 'item' and 'outcomes'")
        item = entry.get("item")
        if not isinstance(item, str) or not item:
            raise CompareRejected(f"{where}.item must be the task's match key")
        if item in seen:
            raise CompareRejected(f"{where}: duplicate verdict for {item}")
        seen.add(item)
        raw_outcomes = entry.get("outcomes")
        if not isinstance(raw_outcomes, list):
            raise CompareRejected(f"{where}.outcomes must be a list (may be empty)")
        outcomes: list[BulletOutcome] = []
        for j, row in enumerate(raw_outcomes):
            if not isinstance(row, dict) or row.get("status") not in valid_statuses:
                raise CompareRejected(
                    f"{where}.outcomes[{j}].status must be one of "
                    f"{', '.join(sorted(valid_statuses))}"
                )
            outcomes.append(BulletOutcome.from_json(row))
        notes = entry.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise CompareRejected(f"{where}.notes must be a string or null")
        verdicts.append(Verdict(item=item, outcomes=outcomes, notes=notes))
    return CompareAnswer(
        source_fingerprint=source_fp, target_fingerprint=target_fp, verdicts=verdicts
    )


def build_compare_report_payload(
    source_path: Path,
    target_path: Path,
    source_groups,
    target_groups,
    answer: CompareAnswer,
    *,
    lang: str,
) -> dict[str, Any]:
    """Assemble the canonical compare-report payload from the verdicts.

    Re-runs the deterministic pairing (cheap) so the deterministic buckets
    appear exactly as the embedded-LLM compare reported them; the agent's
    verdicts fill the judged pairs. Raises :class:`CompareRejected` when a
    framed pair is unanswered or an answer names an unframed pair.
    """
    from clm.voiceover.compare import CompareReport, SlideComparison

    matches = match_slides(source_groups, target_groups)
    by_item = {v.item: v for v in answer.verdicts}
    slides: list[SlideComparison] = []
    unanswered: list[str] = []
    for match in matches:
        comparison = SlideComparison(
            key=match.key,
            kind=match.kind,
            target_index=match.target_index,
            source_index=match.source_index,
            content_similarity=match.content_similarity,
        )
        if _is_framed(match):
            verdict = by_item.pop(match.key, None)
            if verdict is None:
                unanswered.append(match.key)
            else:
                comparison.outcomes = verdict.outcomes
                comparison.notes = verdict.notes
        slides.append(comparison)
    if unanswered:
        raise CompareRejected(
            "no verdict for framed pair(s): "
            + ", ".join(unanswered)
            + " — answer every framed pair (an empty outcomes list is a valid verdict)"
        )
    if by_item:
        raise CompareRejected("verdict(s) for unframed pair(s): " + ", ".join(sorted(by_item)))
    report = CompareReport(source=source_path, target=target_path, language=lang, slides=slides)
    return report.to_json()


VALIDATORS.register(COMPARE_ANSWER_VALIDATOR, parse_compare_answer)
