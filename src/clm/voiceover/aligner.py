"""Assign transcript segments to slides based on the timeline.

This module takes timestamped transcript segments (from transcribe.py) and a
slide timeline (from matcher.py), and produces a mapping of slide index to
speaker notes text.

Key behaviors:
- Segments are assigned to the slide visible during most of their duration
- When a segment straddles a boundary, it biases towards the previous slide
- Backtracking (revisiting an earlier slide) inserts a **[Revisited]** marker
- Header slides (index 0 with is_header=True) receive no transcript text

Every assignment decision is recorded as a :class:`SegmentAssignment` with
its overlap fractions and runner-up, so `harvest align report` can frame the
uncertain ones for agent review (#960) instead of letting the heuristic pick
vanish.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from clm.voiceover.matcher import TimelineEntry
from clm.voiceover.transcribe import Transcript, TranscriptSegment

logger = logging.getLogger(__name__)

# Bias factor for previous-slide assignment when a segment straddles
# a boundary. A segment is assigned to the previous slide if at least
# this fraction of its duration falls there (default: 40%, meaning
# the previous slide wins in ambiguous 50/50 cases).
PREVIOUS_SLIDE_BIAS = 0.4


@dataclass
class SlideNotes:
    """Accumulated notes text for a single slide."""

    slide_index: int
    segments: list[str] = field(default_factory=list)
    revisited_segments: list[list[str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Format the notes as a single text block with revisit markers."""
        parts: list[str] = []
        if self.segments:
            parts.extend(self.segments)
        for revisit in self.revisited_segments:
            if revisit:
                parts.append("")
                parts.append("**[Revisited]**")
                parts.extend(revisit)
        return "\n".join(parts)


@dataclass
class SegmentAssignment:
    """One segment's assignment decision, with the evidence behind it.

    ``slide_index`` is ``None`` for unassigned segments (``reason`` says
    why). ``overlap_fraction`` is the share of the segment's duration
    overlapping the chosen slide's timeline entry; ``runner_up_*`` carry
    the second-best candidate when one exists. ``reason``:

    * ``clear`` — one overlapping entry, or a dominant one
    * ``previous_slide_bias`` — straddle resolved by the bias rule
    * ``largest_overlap`` — straddle resolved by raw overlap
    * ``no_overlap`` — unassigned: outside every timeline entry
    * ``header_slide`` — unassigned: overlapped only the header slide
    """

    segment: TranscriptSegment
    slide_index: int | None
    reason: str
    overlap_fraction: float | None = None
    runner_up_index: int | None = None
    runner_up_fraction: float | None = None


@dataclass
class AlignmentResult:
    """Complete result of transcript-to-slide alignment."""

    slide_notes: dict[int, SlideNotes]
    unassigned_segments: list[TranscriptSegment] = field(default_factory=list)
    #: Every assignment decision, in transcript order — the confidence
    #: trail ``harvest align report`` frames (#960). Empty for alignments
    #: decoded from older cache entries.
    assignments: list[SegmentAssignment] = field(default_factory=list)

    def get_notes_text(self, slide_index: int) -> str | None:
        """Get formatted notes text for a slide, or None if no notes."""
        if slide_index in self.slide_notes:
            text = self.slide_notes[slide_index].text
            return text if text.strip() else None
        return None


def _compute_overlap(
    seg_start: float,
    seg_end: float,
    entry_start: float,
    entry_end: float,
) -> float:
    """Compute temporal overlap between a segment and a timeline entry."""
    overlap_start = max(seg_start, entry_start)
    overlap_end = min(seg_end, entry_end)
    return max(0.0, overlap_end - overlap_start)


def _assign_segment(
    segment: TranscriptSegment,
    timeline: list[TimelineEntry],
) -> SegmentAssignment:
    """Assign one segment to its slide, recording the evidence.

    Same rule as :func:`_find_best_slide` (temporal overlap with
    previous-slide bias), but returns the full decision record instead of
    only the index.
    """
    if not timeline:
        return SegmentAssignment(segment=segment, slide_index=None, reason="no_overlap")

    overlaps: list[tuple[int, float, int]] = []  # (slide_index, overlap, position)
    for i, entry in enumerate(timeline):
        overlap = _compute_overlap(segment.start, segment.end, entry.start_time, entry.end_time)
        if overlap > 0:
            overlaps.append((entry.slide_index, overlap, i))

    if not overlaps:
        return SegmentAssignment(segment=segment, slide_index=None, reason="no_overlap")

    seg_duration = segment.duration
    if len(overlaps) == 1 or seg_duration <= 0:
        idx = overlaps[0][0]
        return SegmentAssignment(
            segment=segment,
            slide_index=idx,
            reason="clear",
            overlap_fraction=(overlaps[0][1] / seg_duration if seg_duration > 0 else None),
        )

    # Multiple overlapping entries: apply previous-slide bias
    overlaps.sort(key=lambda x: x[2])
    first_idx, first_overlap, _ = overlaps[0]
    by_overlap = sorted(overlaps, key=lambda x: x[1], reverse=True)
    runner_up_idx, runner_up_overlap = (
        (by_overlap[1][0], by_overlap[1][1]) if len(by_overlap) > 1 else (None, None)
    )

    def _fraction(o: float | None) -> float | None:
        return None if o is None else o / seg_duration

    first_fraction = first_overlap / seg_duration
    if first_fraction >= PREVIOUS_SLIDE_BIAS:
        return SegmentAssignment(
            segment=segment,
            slide_index=first_idx,
            reason="previous_slide_bias",
            overlap_fraction=first_fraction,
            runner_up_index=runner_up_idx,
            runner_up_fraction=_fraction(runner_up_overlap),
        )
    best_idx, best_overlap = by_overlap[0][0], by_overlap[0][1]
    return SegmentAssignment(
        segment=segment,
        slide_index=best_idx,
        reason="largest_overlap",
        overlap_fraction=_fraction(best_overlap),
        runner_up_index=runner_up_idx,
        runner_up_fraction=_fraction(runner_up_overlap),
    )


def _find_best_slide(
    segment: TranscriptSegment,
    timeline: list[TimelineEntry],
) -> int | None:
    """Find the best slide for a transcript segment.

    Uses temporal overlap with previous-slide bias: when a segment
    straddles a slide boundary, it is assigned to the earlier slide
    if at least PREVIOUS_SLIDE_BIAS of its duration overlaps.

    Returns:
        Slide index, or None if the segment doesn't overlap any timeline entry.
    """
    return _assign_segment(segment, timeline).slide_index


def align_transcript(
    transcript: Transcript,
    timeline: list[TimelineEntry],
) -> AlignmentResult:
    """Assign transcript segments to slides.

    Args:
        transcript: Timestamped transcript from ASR.
        timeline: Slide timeline from the matcher.

    Returns:
        AlignmentResult with per-slide notes, any unassigned segments, and
        the per-segment assignment records (the confidence trail).
    """
    if not timeline:
        return AlignmentResult(
            slide_notes={},
            unassigned_segments=list(transcript.segments),
            assignments=[
                SegmentAssignment(segment=s, slide_index=None, reason="no_overlap")
                for s in transcript.segments
            ],
        )

    # Track which slides are header slides
    header_indices = {e.slide_index for e in timeline if e.is_header}

    # Build initial assignment: segment -> slide_index
    decisions: list[SegmentAssignment] = []
    assignments: list[tuple[TranscriptSegment, int]] = []
    unassigned: list[TranscriptSegment] = []

    for segment in transcript.segments:
        decision = _assign_segment(segment, timeline)
        if decision.slide_index is not None and decision.slide_index in header_indices:
            decision = SegmentAssignment(
                segment=segment,
                slide_index=None,
                reason="header_slide",
                overlap_fraction=decision.overlap_fraction,
                runner_up_index=decision.runner_up_index,
                runner_up_fraction=decision.runner_up_fraction,
            )
        decisions.append(decision)
        if decision.slide_index is None:
            unassigned.append(segment)
        else:
            assignments.append((segment, decision.slide_index))

    return AlignmentResult(
        slide_notes=group_assignments(assignments),
        unassigned_segments=unassigned,
        assignments=decisions,
    )


def group_assignments(
    assignments: list[tuple[TranscriptSegment, int]],
) -> dict[int, SlideNotes]:
    """Group assigned segments into per-slide notes with revisit markers.

    Extracted from :func:`align_transcript` so ``harvest align accept``
    (#960) can rebuild the notes after reassigning segments — the
    ``[Revisited]`` grouping is derived, never hand-edited.
    """
    slide_notes: dict[int, SlideNotes] = {}
    max_slide_seen = -1

    for segment, slide_idx in assignments:
        if slide_idx not in slide_notes:
            slide_notes[slide_idx] = SlideNotes(slide_index=slide_idx)

        notes = slide_notes[slide_idx]
        text = segment.text.strip()
        if not text:
            continue

        is_revisit = slide_idx < max_slide_seen

        if is_revisit:
            # Start a new revisit group if this is the first segment
            # of a new revisit, or append to the current one
            if not notes.revisited_segments or (
                # Check if we've moved away and come back again
                # by seeing if the last assignment to this slide was also a revisit
                _is_new_revisit_group(assignments, segment, slide_idx)
            ):
                notes.revisited_segments.append([text])
            else:
                notes.revisited_segments[-1].append(text)
        else:
            notes.segments.append(text)

        if slide_idx > max_slide_seen:
            max_slide_seen = slide_idx

    return slide_notes


def _is_new_revisit_group(
    assignments: list[tuple[TranscriptSegment, int]],
    current_segment: TranscriptSegment,
    slide_idx: int,
) -> bool:
    """Check if this segment starts a new revisit group.

    A new revisit group starts when we return to a slide after having
    been at a different slide since the last visit.
    """
    # Walk backwards through assignments to find the previous assignment
    # to this same slide
    found_different = False
    for seg, idx in reversed(assignments):
        if seg is current_segment:
            continue
        if seg.start >= current_segment.start:
            continue
        if idx == slide_idx:
            return found_different
        found_different = True
    # First time seeing this slide in revisit context
    return True
