"""Tests for the slide matcher."""

from __future__ import annotations

import pytest

from clm.notebooks.slide_parser import SlideGroup
from clm.voiceover.keyframes import TransitionEvent
from clm.voiceover.matcher import (
    _build_timeline,
    _pick_best_sequential,
    _sequential_align,
    match_frame_to_slides,
)


def _make_slide(index: int, title: str, text: str, slide_type: str = "subslide") -> SlideGroup:
    """Helper to create a minimal SlideGroup for testing."""
    return SlideGroup(
        index=index,
        slide_type=slide_type,
        lang="de",
        title=title,
        cells=[],
    )


def _make_slides_with_text(texts: list[tuple[str, str]]) -> list[SlideGroup]:
    """Create slides from (title, text_content) pairs."""
    slides = []
    for i, (title, text) in enumerate(texts):
        sg = _make_slide(i, title, text)
        # Override text_content by adding a mock cell
        sg._text_override = text
        slides.append(sg)
    return slides


class _SlideWithText(SlideGroup):
    """Test helper: SlideGroup with overridden text_content."""

    def __init__(self, index: int, title: str, text: str, slide_type: str = "subslide"):
        super().__init__(
            index=index,
            slide_type=slide_type,
            lang="de",
            title=title,
            cells=[],
        )
        self._text = text

    @property
    def text_content(self) -> str:
        return self._text


class TestMatchFrameToSlides:
    def test_exact_match(self):
        slides = [
            _SlideWithText(0, "Intro", "Introduction to Python programming"),
            _SlideWithText(1, "Variables", "Variables and data types in Python"),
            _SlideWithText(2, "Functions", "Defining and calling functions"),
        ]
        results = match_frame_to_slides("Variables and data types in Python", slides)
        assert results[0][0] == 1  # slide index 1 should be best
        assert results[0][1] > 90.0

    def test_partial_match(self):
        slides = [
            _SlideWithText(0, "Intro", "Introduction to Python programming"),
            _SlideWithText(1, "Variables", "Variables and data types in Python"),
        ]
        results = match_frame_to_slides("Variables data types", slides)
        assert results[0][0] == 1
        assert results[0][1] > 60.0

    def test_no_good_match(self):
        slides = [
            _SlideWithText(0, "Intro", "Introduction to Python"),
            _SlideWithText(1, "Variables", "Variables and data types"),
        ]
        results = match_frame_to_slides("Completely unrelated text about cooking", slides)
        # All scores should be low
        assert all(score < 50.0 for _, score in results)

    def test_case_insensitive(self):
        slides = [
            _SlideWithText(0, "Title", "Transformation von Listen"),
        ]
        results = match_frame_to_slides("TRANSFORMATION VON LISTEN", slides)
        assert results[0][1] > 90.0

    def test_empty_ocr_text(self):
        slides = [_SlideWithText(0, "Title", "Some content")]
        results = match_frame_to_slides("", slides)
        assert len(results) == 1


class TestPickBestSequential:
    def test_clear_winner(self):
        matches = [(3, 95.0), (1, 60.0), (2, 55.0)]
        idx, score = _pick_best_sequential(matches, prev_idx=2, min_idx=0)
        assert idx == 3
        assert score == 95.0

    def test_prefers_forward_progress(self):
        # Slide 1 and 4 have same title, similar scores
        matches = [(1, 95.0), (4, 93.0), (2, 50.0)]
        # Previous was slide 3: should prefer slide 4 (forward) over 1 (backward)
        idx, score = _pick_best_sequential(matches, prev_idx=3, min_idx=0)
        assert idx == 4

    def test_allows_backtracking(self):
        # Slide 5 scored much higher than anything forward
        matches = [(5, 98.0), (8, 60.0), (7, 55.0)]
        # Previous was slide 7: slide 5 is backward but much higher score
        idx, score = _pick_best_sequential(matches, prev_idx=7, min_idx=0)
        assert idx == 5

    def test_no_previous_context(self):
        matches = [(2, 95.0), (0, 90.0), (1, 85.0)]
        idx, score = _pick_best_sequential(matches, prev_idx=-1, min_idx=1)
        # Should pick slide 2 (best match >= min_idx)
        assert idx == 2

    def test_skips_header_when_min_idx_set(self):
        matches = [(0, 95.0), (1, 90.0)]
        idx, score = _pick_best_sequential(matches, prev_idx=-1, min_idx=1)
        assert idx == 1


class TestSequentialAlign:
    def _make_event(self, ts: float, diff: float = 0.02) -> TransitionEvent:
        return TransitionEvent(timestamp=ts, peak_diff=diff, confidence=3.0, num_frames=1)

    def test_simple_forward_sequence(self):
        slides = [
            _SlideWithText(0, "Header", "Course Title", "header"),
            _SlideWithText(1, "Slide A", "Content about topic A"),
            _SlideWithText(2, "Slide B", "Content about topic B"),
            _SlideWithText(3, "Slide C", "Content about topic C"),
        ]
        raw_matches = [
            (self._make_event(10.0), [(1, 95.0), (2, 50.0)]),
            (self._make_event(30.0), [(2, 93.0), (1, 48.0)]),
            (self._make_event(50.0), [(3, 91.0), (2, 45.0)]),
        ]
        aligned = _sequential_align(raw_matches, slides)
        indices = [idx for _, idx, _ in aligned]
        assert indices == [1, 2, 3]

    def test_duplicate_titles_resolved_by_sequence(self):
        slides = [
            _SlideWithText(0, "Header", "Title", "header"),
            _SlideWithText(1, "Transform", "Transformation of lists"),
            _SlideWithText(2, "Filter", "Filtering elements"),
            _SlideWithText(3, "Comprehension", "List comprehension syntax"),
            _SlideWithText(4, "Transform", "Transformation of lists"),  # same as 1
            _SlideWithText(5, "Filter", "Filtering elements"),  # same as 2
        ]
        # After seeing slide 3, the next "Transform" should be 4, not 1
        raw_matches = [
            (self._make_event(10.0), [(1, 95.0), (4, 95.0), (2, 50.0)]),
            (self._make_event(30.0), [(2, 93.0), (5, 93.0), (1, 48.0)]),
            (self._make_event(50.0), [(3, 91.0), (2, 45.0)]),
            (self._make_event(70.0), [(4, 95.0), (1, 95.0), (5, 50.0)]),
            (self._make_event(90.0), [(5, 93.0), (2, 93.0), (3, 48.0)]),
        ]
        aligned = _sequential_align(raw_matches, slides)
        indices = [idx for _, idx, _ in aligned]
        assert indices == [1, 2, 3, 4, 5]

    def test_header_detected_at_start(self):
        slides = [
            _SlideWithText(0, "Header", "Course Title", "header"),
            _SlideWithText(1, "First", "First slide content"),
        ]
        # Low-confidence match at start -> header
        raw_matches = [
            (self._make_event(5.0), [(1, 50.0), (0, 30.0)]),
            (self._make_event(30.0), [(1, 95.0), (0, 30.0)]),
        ]
        aligned = _sequential_align(raw_matches, slides)
        indices = [idx for _, idx, _ in aligned]
        assert indices[0] == 0  # header
        assert indices[1] == 1  # first real slide


class TestBuildTimeline:
    def _make_event(self, ts: float) -> TransitionEvent:
        return TransitionEvent(timestamp=ts, peak_diff=0.02, confidence=3.0, num_frames=1)

    def test_simple_timeline(self):
        aligned = [
            (self._make_event(10.0), 1, 95.0),
            (self._make_event(30.0), 2, 93.0),
            (self._make_event(50.0), 3, 91.0),
        ]
        timeline = _build_timeline(aligned, video_duration=70.0)
        assert len(timeline) == 3
        assert timeline[0].slide_index == 1
        assert timeline[0].start_time == 10.0
        assert timeline[0].end_time == 30.0
        assert timeline[1].start_time == 30.0
        assert timeline[1].end_time == 50.0
        assert timeline[2].end_time == 70.0

    def test_merges_adjacent_same_slide(self):
        # Two events on the same slide (within-slide changes)
        aligned = [
            (self._make_event(10.0), 1, 95.0),
            (self._make_event(15.0), 1, 90.0),  # same slide
            (self._make_event(30.0), 2, 93.0),
        ]
        timeline = _build_timeline(aligned, video_duration=50.0)
        assert len(timeline) == 2
        assert timeline[0].slide_index == 1
        assert timeline[0].start_time == 10.0
        assert timeline[0].end_time == 30.0
        assert timeline[0].match_score == 95.0  # keeps higher score

    def test_empty_input(self):
        assert _build_timeline([], video_duration=100.0) == []

    def test_single_entry(self):
        aligned = [(self._make_event(5.0), 1, 95.0)]
        timeline = _build_timeline(aligned, video_duration=60.0)
        assert len(timeline) == 1
        assert timeline[0].start_time == 5.0
        assert timeline[0].end_time == 60.0
