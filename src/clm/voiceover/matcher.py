"""Match video frames to slide content using OCR and fuzzy text matching.

This module takes transition events (from keyframes.py) and a parsed slide
list (from slide_parser.py), and produces a timeline mapping each moment in
the video to a specific slide.

The matching uses a two-pass approach:
1. OCR each transition frame and fuzzy-match against slide content
2. Apply sequential ordering constraint to resolve ambiguity

See ``docs/claude/voiceover-prototype-findings.md`` for empirical validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

from clm.notebooks.slide_parser import SlideGroup
from clm.voiceover.keyframes import TransitionEvent, get_frame_at

logger = logging.getLogger(__name__)

# Minimum OCR match score to consider a match valid
MIN_MATCH_SCORE = 30.0

# Score gap required between best and runner-up to consider the match
# unambiguous without sequential constraint
UNAMBIGUOUS_GAP = 15.0


@dataclass
class SlideMatch:
    """A match between a video timestamp and a source slide."""

    slide_index: int
    score: float  # fuzzy match score (0–100)
    runner_up_index: int
    runner_up_score: float


@dataclass
class TimelineEntry:
    """A segment of the video mapped to a specific slide."""

    slide_index: int  # index into the slide group list
    start_time: float  # seconds
    end_time: float  # seconds
    match_score: float  # OCR match confidence (0–100)
    is_header: bool = False  # True for the title/header slide


@dataclass
class MatchResult:
    """Complete result of the matching process."""

    timeline: list[TimelineEntry]
    slides: list[SlideGroup]
    unmatched_events: list[TransitionEvent] = field(default_factory=list)


def ocr_frame(frame: np.ndarray, lang: str = "deu+eng") -> str:
    """Run Tesseract OCR on a grayscale frame.

    Args:
        frame: Grayscale image as numpy array.
        lang: Tesseract language(s) to use.

    Returns:
        Extracted text, stripped of leading/trailing whitespace.
    """
    import cv2
    import pytesseract

    # Binarize for better OCR (white background, dark text)
    _, binary = cv2.threshold(frame, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    text: str = pytesseract.image_to_string(binary, lang=lang)
    return text.strip()


def match_frame_to_slides(
    ocr_text: str,
    slides: list[SlideGroup],
) -> list[tuple[int, float]]:
    """Match OCR text against all slides using fuzzy matching.

    Args:
        ocr_text: Text extracted from a video frame via OCR.
        slides: Parsed slide groups.

    Returns:
        List of (slide_index, score) sorted by score descending.
        Score is 0–100 (rapidfuzz token_set_ratio).
    """
    from rapidfuzz import fuzz

    results: list[tuple[int, float]] = []
    ocr_lower = ocr_text.lower()

    for slide in slides:
        slide_text = slide.text_content.lower()
        if not slide_text:
            results.append((slide.index, 0.0))
            continue
        score = fuzz.token_set_ratio(ocr_lower, slide_text)
        results.append((slide.index, float(score)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def match_events_to_slides(
    events: list[TransitionEvent],
    slides: list[SlideGroup],
    video_path: str | Path,
    *,
    lang: str = "de",
    frame_offset: float = 1.0,
) -> MatchResult:
    """Match transition events to slides, producing a timeline.

    This is the main entry point for the matching pipeline.

    Algorithm:
    1. For each transition event, OCR the stabilized frame and compute
       fuzzy match scores against all slides.
    2. Apply sequential ordering constraint: matches should be roughly
       monotonic (allowing local backtracking).
    3. Handle header slides (low-confidence matches at the start).
    4. Build a timeline of (slide_index, start_time, end_time) entries.

    Args:
        events: Transition events from keyframes detection, sorted
            chronologically.
        slides: Parsed slide groups from slide_parser.
        video_path: Path to the video file (for frame extraction).
        lang: Video language ("de" or "en"). Affects OCR language setting.
        frame_offset: Seconds after transition peak to capture frame
            (default: 1.0, for stabilization after animation).

    Returns:
        MatchResult with the complete timeline.
    """
    if not events or not slides:
        return MatchResult(timeline=[], slides=slides)

    ocr_lang = "deu+eng" if lang == "de" else "eng+deu"

    # Phase 1: OCR each event and compute raw matches
    raw_matches: list[tuple[TransitionEvent, list[tuple[int, float]]]] = []

    for event in events:
        try:
            frame = get_frame_at(video_path, event.timestamp, offset=frame_offset)
        except (ValueError, FileNotFoundError) as e:
            logger.warning("Could not get frame at %.1fs: %s", event.timestamp, e)
            continue

        ocr_text = ocr_frame(frame, lang=ocr_lang)
        matches = match_frame_to_slides(ocr_text, slides)
        raw_matches.append((event, matches))
        logger.debug(
            "Event @%.1fs: best=[%d] score=%.1f, runner=[%d] score=%.1f",
            event.timestamp,
            matches[0][0],
            matches[0][1],
            matches[1][0] if len(matches) > 1 else -1,
            matches[1][1] if len(matches) > 1 else 0,
        )

    # Phase 2: Sequential alignment
    aligned = _sequential_align(raw_matches, slides)

    # Phase 3: Build timeline
    header_indices = {s.index for s in slides if s.slide_type == "header"}
    video_duration = events[-1].timestamp + 30.0  # approximate end
    timeline = _build_timeline(aligned, video_duration, header_indices=header_indices)

    return MatchResult(timeline=timeline, slides=slides)


def _sequential_align(
    raw_matches: list[tuple[TransitionEvent, list[tuple[int, float]]]],
    slides: list[SlideGroup],
) -> list[tuple[TransitionEvent, int, float]]:
    """Apply sequential ordering constraint to resolve ambiguous matches.

    Given a sequence of OCR match results, find the best assignment of
    events to slides such that:
    - The assignment is roughly monotonically increasing (forward progress)
    - Local backtracking is allowed (going back 1-2 slides)
    - The header slide (index 0, if it exists) is only matched at the start
    - When multiple slides have similar scores, prefer the one that
      maintains forward progress from the previous match

    Returns:
        List of (event, slide_index, score) tuples.
    """
    if not raw_matches:
        return []

    has_header = any(s.slide_type == "header" for s in slides)
    first_content_idx = 1 if has_header else 0

    aligned: list[tuple[TransitionEvent, int, float]] = []
    prev_slide_idx = -1

    for event, matches in raw_matches:
        if not matches:
            continue

        best_idx, best_score = matches[0]
        runner_up_score = matches[1][1] if len(matches) > 1 else 0.0

        # Special case: detect header slide at the beginning
        if prev_slide_idx == -1 and has_header and best_score < 70.0:
            # Low-confidence match at the start -> likely the header slide
            aligned.append((event, 0, best_score))
            prev_slide_idx = 0
            continue

        # If the best match is unambiguous (large gap to runner-up), use it
        if best_score - runner_up_score > UNAMBIGUOUS_GAP:
            chosen_idx = best_idx
            chosen_score = best_score
        else:
            # Ambiguous: prefer the match that maintains forward progress
            chosen_idx, chosen_score = _pick_best_sequential(
                matches, prev_slide_idx, first_content_idx
            )

        # Skip header slide for non-initial events
        if chosen_idx == 0 and has_header and prev_slide_idx > 0:
            # This shouldn't match the header after we've moved past it;
            # pick the next best non-header match
            for idx, score in matches:
                if idx != 0:
                    chosen_idx = idx
                    chosen_score = score
                    break

        aligned.append((event, chosen_idx, chosen_score))

        # Track progress (but don't let header reset progress)
        if chosen_idx > 0 or not has_header:
            prev_slide_idx = chosen_idx

    return aligned


def _pick_best_sequential(
    matches: list[tuple[int, float]],
    prev_idx: int,
    min_idx: int,
) -> tuple[int, float]:
    """Pick the best match that respects sequential ordering.

    Prefers slides that are at or after the previous slide. Falls back
    to the raw best match if no forward candidate is close enough.

    Args:
        matches: (slide_index, score) pairs sorted by score descending.
        prev_idx: Previously matched slide index (-1 if none).
        min_idx: Minimum valid slide index (skips header).

    Returns:
        (slide_index, score) of the chosen match.
    """
    best_idx, best_score = matches[0]

    if prev_idx < 0:
        # No previous context: use best raw match, but skip header
        for idx, score in matches:
            if idx >= min_idx:
                return idx, score
        return best_idx, best_score

    # Look for the best forward match (at or after prev_idx)
    # and the best nearby-backward match (up to 2 slides back)
    min_forward = prev_idx
    min_backward = max(min_idx, prev_idx - 2)

    best_forward: tuple[int, float] | None = None
    best_nearby: tuple[int, float] | None = None

    for idx, score in matches:
        if idx >= min_forward:
            if best_forward is None or score > best_forward[1]:
                best_forward = (idx, score)
        if min_backward <= idx < min_forward:
            if best_nearby is None or score > best_nearby[1]:
                best_nearby = (idx, score)

    # Prefer forward progress if within 10 points of the raw best
    if best_forward is not None and best_score - best_forward[1] <= 10.0:
        return best_forward

    # Allow backtracking if the backward match is clearly better
    if best_nearby is not None and best_score - best_nearby[1] <= 10.0:
        return best_nearby

    # Fallback: use raw best match (may indicate backtracking)
    return best_idx, best_score


def _build_timeline(
    aligned: list[tuple[TransitionEvent, int, float]],
    video_duration: float,
    *,
    header_indices: set[int] | None = None,
) -> list[TimelineEntry]:
    """Build a timeline from aligned matches.

    Each entry covers the time from one transition to the next.
    Adjacent entries with the same slide_index are merged (these are
    within-slide changes, not real transitions).
    """
    if not aligned:
        return []

    header_indices = header_indices or set()

    # First, create raw entries
    raw_entries: list[TimelineEntry] = []
    for i, (event, slide_idx, score) in enumerate(aligned):
        start = event.timestamp
        if i + 1 < len(aligned):
            end = aligned[i + 1][0].timestamp
        else:
            end = video_duration
        raw_entries.append(
            TimelineEntry(
                slide_index=slide_idx,
                start_time=start,
                end_time=end,
                match_score=score,
                is_header=slide_idx in header_indices,
            )
        )

    # Merge adjacent entries with the same slide
    merged: list[TimelineEntry] = [raw_entries[0]]
    for entry in raw_entries[1:]:
        if entry.slide_index == merged[-1].slide_index:
            # Extend the previous entry
            merged[-1].end_time = entry.end_time
            # Keep the higher match score
            merged[-1].match_score = max(merged[-1].match_score, entry.match_score)
        else:
            merged.append(entry)

    return merged
