"""Assign transcript segments to slides based on the timeline.

This module takes timestamped transcript segments (from transcribe.py) and a
slide timeline (from matcher.py), and produces a mapping of slide index to
speaker notes text.

Key behaviors:
- Segments are assigned to the slide visible during most of their duration
- When a segment straddles a boundary, it biases towards the previous slide
- Backtracking (revisiting an earlier slide) inserts a **[Revisited]** marker
- Header slides (index 0 with is_header=True) receive no transcript text
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
class AlignmentResult:
    """Complete result of transcript-to-slide alignment."""

    slide_notes: dict[int, SlideNotes]
    unassigned_segments: list[TranscriptSegment] = field(default_factory=list)

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
    if not timeline:
        return None

    overlaps: list[tuple[int, float, int]] = []  # (slide_index, overlap, position)

    for i, entry in enumerate(timeline):
        overlap = _compute_overlap(segment.start, segment.end, entry.start_time, entry.end_time)
        if overlap > 0:
            overlaps.append((entry.slide_index, overlap, i))

    if not overlaps:
        return None

    if len(overlaps) == 1:
        return overlaps[0][0]

    # Multiple overlapping entries: apply previous-slide bias
    seg_duration = segment.duration
    if seg_duration <= 0:
        return overlaps[0][0]

    # Sort by timeline position (earlier first)
    overlaps.sort(key=lambda x: x[2])

    # The earliest overlapping entry gets the bias
    first_idx, first_overlap, _ = overlaps[0]
    first_fraction = first_overlap / seg_duration

    if first_fraction >= PREVIOUS_SLIDE_BIAS:
        return first_idx

    # Otherwise, pick the entry with the largest overlap
    best = max(overlaps, key=lambda x: x[1])
    return best[0]


def align_transcript(
    transcript: Transcript,
    timeline: list[TimelineEntry],
) -> AlignmentResult:
    """Assign transcript segments to slides.

    Args:
        transcript: Timestamped transcript from ASR.
        timeline: Slide timeline from the matcher.

    Returns:
        AlignmentResult with per-slide notes and any unassigned segments.
    """
    if not timeline:
        return AlignmentResult(
            slide_notes={},
            unassigned_segments=list(transcript.segments),
        )

    # Track which slides are header slides
    header_indices = {e.slide_index for e in timeline if e.is_header}

    # Build initial assignment: segment -> slide_index
    assignments: list[tuple[TranscriptSegment, int]] = []
    unassigned: list[TranscriptSegment] = []

    for segment in transcript.segments:
        slide_idx = _find_best_slide(segment, timeline)
        if slide_idx is None or slide_idx in header_indices:
            unassigned.append(segment)
        else:
            assignments.append((segment, slide_idx))

    # Build per-slide notes with backtracking detection
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

    return AlignmentResult(slide_notes=slide_notes, unassigned_segments=unassigned)


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
