"""``clm harvest align report`` / ``align accept`` — reviewable alignment (#960).

The deterministic pipeline's heuristic judgement points — transcript→slide
assignment (straddles resolved by the previous-slide bias), OCR→slide
matches the sequential constraint overruled — used to be invisible: the
agent got a finished report and could only override wholesale via the
``--alignment`` injection file. This module surfaces them.

* ``report`` frames every uncertain decision as an item with its evidence
  (overlap fractions, runner-up, the reason code) and freshness tokens.
* ``accept`` validates a reassignment answer against the live alignment
  (freshness: the alignment fingerprint must match), applies it, rebuilds
  the per-slide notes (the ``[Revisited]`` grouping is derived, never
  hand-edited), and writes a **full alignment file** the next
  ``report``/``task`` run loads via the existing ``--alignment`` injection.
  No new pipeline machinery, no cache-key changes.

Model-free: the engine frames and validates; the agent judges.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from clm.slides.agent_task import VALIDATORS, AnswerRejected, envelope
from clm.voiceover.aligner import AlignmentResult, group_assignments
from clm.voiceover.matcher import UNAMBIGUOUS_GAP

__all__ = [
    "ALIGN_ANSWER_SCHEMA",
    "ALIGN_ANSWER_VALIDATOR",
    "AlignAnswer",
    "AlignRejected",
    "Reassignment",
    "alignment_fingerprint",
    "apply_reassignments",
    "build_align_report",
    "parse_align_answer",
]

#: The validator label framed align reports announce; registered below.
ALIGN_ANSWER_VALIDATOR = "harvest-align"

#: A segment assigned with less than this share of its duration on the
#: chosen slide straddles a boundary — framed for review.
STRADDLE_FRACTION = 0.6

#: Chosen-minus-runner-up overlap below this is a close call — framed.
CLOSE_CALL_GAP = 0.2

#: OCR matches scoring below this are weak evidence — framed.
WEAK_MATCH_SCORE = 50.0


class AlignRejected(AnswerRejected):
    """The align answer was rejected; the message names the reason."""


@dataclass(frozen=True)
class Reassignment:
    """Move one segment: ``to_slide=None`` unassigns it."""

    segment_index: int  # index into the alignment's assignment records
    to_slide: int | None


@dataclass(frozen=True)
class AlignAnswer:
    """The validated align answer document."""

    video_fingerprint: str
    alignment_fingerprint: str
    reassignments: list[Reassignment] = field(default_factory=list)


def alignment_fingerprint(alignment: AlignmentResult) -> str:
    """The freshness token over the whole alignment (canonical encoding)."""
    from clm.voiceover.cache import _encode_alignment

    canonical = json.dumps(_encode_alignment(alignment), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# report — frame the uncertain decisions
# ---------------------------------------------------------------------------


def _slide_titles(slide_groups) -> dict[int, str]:
    return {sg.index: sg.title for sg in slide_groups}


def _uncertain_segment(item_index: int, a) -> bool:
    if a.reason in ("previous_slide_bias", "largest_overlap"):
        return True  # a heuristic rule resolved a straddle — always visible
    if a.overlap_fraction is not None and a.overlap_fraction < STRADDLE_FRACTION:
        return True
    return bool(
        a.runner_up_fraction is not None
        and a.overlap_fraction is not None
        and a.overlap_fraction - a.runner_up_fraction < CLOSE_CALL_GAP
    )


def build_align_report(
    artifacts,
    slide_groups,
    video_paths,
) -> dict[str, Any]:
    """Frame the pipeline's uncertain alignment/matching decisions as items.

    Read-only. Exit-code semantics (CLI-side): 0 no uncertain items,
    1 items framed, 2 error.
    """
    from clm.voiceover.harvest import video_fingerprint

    alignment: AlignmentResult = artifacts.alignment
    titles = _slide_titles(slide_groups)
    items: list[dict[str, Any]] = []

    for i, a in enumerate(alignment.assignments):
        if a.slide_index is None:
            # Unassigned segments are framed with their assignment-record
            # index so an answer can assign them (segment_index + to_slide).
            items.append(
                {
                    "kind": "unassigned_segment",
                    "segment_index": i,
                    "segment": {
                        "start": a.segment.start,
                        "end": a.segment.end,
                        "text": a.segment.text,
                        "source_part_index": a.segment.source_part_index,
                    },
                    "reason": a.reason,
                }
            )
            continue
        if not _uncertain_segment(i, a):
            continue
        items.append(
            {
                "kind": "segment_assignment",
                "segment_index": i,
                "segment": {
                    "start": a.segment.start,
                    "end": a.segment.end,
                    "text": a.segment.text,
                    "source_part_index": a.segment.source_part_index,
                },
                "assigned_slide": a.slide_index,
                "assigned_title": titles.get(a.slide_index),
                "reason": a.reason,
                "overlap_fraction": a.overlap_fraction,
                "runner_up_slide": a.runner_up_index,
                "runner_up_title": (
                    titles.get(a.runner_up_index) if a.runner_up_index is not None else None
                ),
                "runner_up_fraction": a.runner_up_fraction,
            }
        )

    # Alignments without assignment records (old cache, hand-written
    # injection): frame the unassigned segments without an index — accept
    # refuses index-less answers for them (see the note below).
    if not alignment.assignments:
        for seg in alignment.unassigned_segments:
            items.append(
                {
                    "kind": "unassigned_segment",
                    "segment_index": None,
                    "segment": {
                        "start": seg.start,
                        "end": seg.end,
                        "text": seg.text,
                        "source_part_index": seg.source_part_index,
                    },
                    "reason": None,
                }
            )

    for entry in artifacts.timeline or []:
        gap = (
            entry.match_score - entry.runner_up_score if entry.runner_up_score is not None else None
        )
        uncertain = (
            entry.overridden_by_sequential
            or entry.match_score < WEAK_MATCH_SCORE
            or (gap is not None and gap < UNAMBIGUOUS_GAP)
        )
        if not uncertain:
            continue
        items.append(
            {
                "kind": "slide_match",
                "slide_index": entry.slide_index,
                "slide_title": titles.get(entry.slide_index),
                "start_time": entry.start_time,
                "end_time": entry.end_time,
                "match_score": entry.match_score,
                "runner_up_slide": entry.runner_up_index,
                "runner_up_title": (
                    titles.get(entry.runner_up_index) if entry.runner_up_index is not None else None
                ),
                "runner_up_score": entry.runner_up_score,
                "raw_best_slide": entry.raw_best_index,
                "overridden_by_sequential": entry.overridden_by_sequential,
                "note": (
                    "a wrong slide match misassigns every segment in its "
                    "window — answer by reassigning those segments"
                ),
            }
        )

    body: dict[str, Any] = {
        "video_fingerprint": video_fingerprint(video_paths),
        "alignment_fingerprint": alignment_fingerprint(alignment),
        "validator": ALIGN_ANSWER_VALIDATOR,
        "assignment_count": len(alignment.assignments),
        "items": items,
        "answer_schema": ALIGN_ANSWER_SCHEMA,
    }
    if not alignment.assignments:
        body["note"] = (
            "this alignment carries no assignment records (cached before the "
            "assignment trail existed, or injected by hand) — re-run with "
            "--refresh-cache to frame segment-level items"
        )
    return envelope(1, tool="harvest", verb="align-report", body=body)


# ---------------------------------------------------------------------------
# accept — validate and apply reassignments
# ---------------------------------------------------------------------------

#: The answer contract framed in every align report.
ALIGN_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "video_fingerprint",
        "alignment_fingerprint",
        "reassignments",
    ],
    "properties": {
        "video_fingerprint": {"type": "string"},
        "alignment_fingerprint": {
            "type": "string",
            "description": "echoed verbatim from the align report",
        },
        "reassignments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["segment_index", "to_slide"],
                "properties": {
                    "segment_index": {
                        "type": "integer",
                        "description": "index into the report's assignment records",
                    },
                    "to_slide": {
                        "type": ["integer", "null"],
                        "description": "target slide index, or null to unassign",
                    },
                },
            },
        },
    },
}


def parse_align_answer(payload: Any) -> AlignAnswer:
    """Validate the answer shape; raise :class:`AlignRejected` on violation."""
    if not isinstance(payload, dict):
        raise AlignRejected("the answer must be a JSON object (see the report's answer_schema)")
    video_fp = payload.get("video_fingerprint")
    if not isinstance(video_fp, str) or not video_fp:
        raise AlignRejected("'video_fingerprint' must be the string echoed from the report")
    align_fp = payload.get("alignment_fingerprint")
    if not isinstance(align_fp, str) or not align_fp:
        raise AlignRejected("'alignment_fingerprint' must be the string echoed from the report")
    raw = payload.get("reassignments")
    if not isinstance(raw, list) or not raw:
        raise AlignRejected("'reassignments' must be a non-empty list")
    reassignments: list[Reassignment] = []
    seen: set[int] = set()
    for i, entry in enumerate(raw):
        where = f"reassignments[{i}]"
        if not isinstance(entry, dict):
            raise AlignRejected(f"{where} must be an object with 'segment_index' and 'to_slide'")
        index = entry.get("segment_index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise AlignRejected(f"{where}.segment_index must be a non-negative integer")
        if index in seen:
            raise AlignRejected(f"{where}: duplicate reassignment of segment {index}")
        seen.add(index)
        to_slide = entry.get("to_slide")
        if to_slide is not None and (not isinstance(to_slide, int) or isinstance(to_slide, bool)):
            raise AlignRejected(f"{where}.to_slide must be a slide index or null (unassign)")
        reassignments.append(Reassignment(segment_index=index, to_slide=to_slide))
    return AlignAnswer(
        video_fingerprint=video_fp,
        alignment_fingerprint=align_fp,
        reassignments=reassignments,
    )


def apply_reassignments(
    alignment: AlignmentResult,
    answer: AlignAnswer,
    *,
    valid_slides: set[int],
    header_indices: set[int],
) -> AlignmentResult:
    """Apply the answer to the alignment and rebuild the slide notes.

    The notes (incl. the ``[Revisited]`` grouping) are derived from the
    assignment order, so a move re-derives them — never hand-edited.
    Raises :class:`AlignRejected` on any invalid target; the caller checks
    freshness first.
    """
    if not alignment.assignments:
        raise AlignRejected(
            "this alignment carries no assignment records (cached before the "
            "assignment trail existed, or injected by hand) — re-run the "
            "pipeline with --refresh-cache, then frame a fresh align report"
        )
    moves = {r.segment_index: r.to_slide for r in answer.reassignments}
    for r in answer.reassignments:
        if r.segment_index >= len(alignment.assignments):
            raise AlignRejected(
                f"segment_index {r.segment_index} is out of range "
                f"({len(alignment.assignments)} assignment records)"
            )
        if r.to_slide is not None and r.to_slide not in valid_slides:
            raise AlignRejected(
                f"to_slide {r.to_slide} is not a slide in this deck "
                f"(known: {', '.join(str(s) for s in sorted(valid_slides))})"
            )
        if r.to_slide is not None and r.to_slide in header_indices:
            raise AlignRejected(
                f"to_slide {r.to_slide} is the header slide — it receives no "
                "transcript text (unassign instead, or pick the first content slide)"
            )

    decisions = [
        replace(a, slide_index=moves[i]) if i in moves else a
        for i, a in enumerate(alignment.assignments)
    ]
    assigned = [(a.segment, a.slide_index) for a in decisions if a.slide_index is not None]
    unassigned = [a.segment for a in decisions if a.slide_index is None]
    return AlignmentResult(
        slide_notes=group_assignments(assigned),
        unassigned_segments=unassigned,
        assignments=decisions,
    )


VALIDATORS.register(ALIGN_ANSWER_VALIDATOR, parse_align_answer)
