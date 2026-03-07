"""Tests for the transcript-to-slide aligner."""

from __future__ import annotations

import pytest

from clm.voiceover.aligner import (
    AlignmentResult,
    SlideNotes,
    _compute_overlap,
    _find_best_slide,
    align_transcript,
)
from clm.voiceover.matcher import TimelineEntry
from clm.voiceover.transcribe import Transcript, TranscriptSegment


def _seg(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def _entry(slide: int, start: float, end: float, score: float = 90.0, is_header: bool = False):
    return TimelineEntry(
        slide_index=slide,
        start_time=start,
        end_time=end,
        match_score=score,
        is_header=is_header,
    )


class TestComputeOverlap:
    def test_full_overlap(self):
        assert _compute_overlap(10.0, 20.0, 10.0, 20.0) == pytest.approx(10.0)

    def test_partial_overlap(self):
        assert _compute_overlap(10.0, 20.0, 15.0, 25.0) == pytest.approx(5.0)

    def test_no_overlap(self):
        assert _compute_overlap(10.0, 20.0, 25.0, 35.0) == pytest.approx(0.0)

    def test_segment_inside_entry(self):
        assert _compute_overlap(12.0, 18.0, 10.0, 20.0) == pytest.approx(6.0)

    def test_entry_inside_segment(self):
        assert _compute_overlap(10.0, 30.0, 15.0, 20.0) == pytest.approx(5.0)

    def test_touching_boundaries(self):
        assert _compute_overlap(10.0, 20.0, 20.0, 30.0) == pytest.approx(0.0)


class TestFindBestSlide:
    def test_single_entry(self):
        timeline = [_entry(1, 10.0, 30.0)]
        assert _find_best_slide(_seg(15.0, 20.0, "hello"), timeline) == 1

    def test_no_overlap(self):
        timeline = [_entry(1, 10.0, 30.0)]
        assert _find_best_slide(_seg(35.0, 40.0, "hello"), timeline) is None

    def test_empty_timeline(self):
        assert _find_best_slide(_seg(10.0, 20.0, "hello"), []) is None

    def test_majority_overlap(self):
        timeline = [_entry(1, 10.0, 20.0), _entry(2, 20.0, 30.0)]
        # 8s in slide 1, 2s in slide 2 -> slide 1
        assert _find_best_slide(_seg(12.0, 22.0, "hello"), timeline) == 1

    def test_previous_slide_bias(self):
        timeline = [_entry(1, 10.0, 20.0), _entry(2, 20.0, 30.0)]
        # 4s in slide 1, 6s in slide 2 -> slide 1 wins with bias (40% threshold)
        assert _find_best_slide(_seg(16.0, 26.0, "hello"), timeline) == 1

    def test_strong_next_slide_wins(self):
        timeline = [_entry(1, 10.0, 20.0), _entry(2, 20.0, 30.0)]
        # 2s in slide 1, 8s in slide 2 -> slide 2 (bias not enough)
        assert _find_best_slide(_seg(18.0, 28.0, "hello"), timeline) == 2

    def test_fully_in_second_entry(self):
        timeline = [_entry(1, 10.0, 20.0), _entry(2, 20.0, 30.0)]
        assert _find_best_slide(_seg(22.0, 28.0, "hello"), timeline) == 2


class TestSlideNotes:
    def test_simple_text(self):
        notes = SlideNotes(slide_index=1, segments=["First sentence.", "Second sentence."])
        assert notes.text == "First sentence.\nSecond sentence."

    def test_with_revisit(self):
        notes = SlideNotes(
            slide_index=1,
            segments=["Original text."],
            revisited_segments=[["Revisited text."]],
        )
        expected = "Original text.\n\n**[Revisited]**\nRevisited text."
        assert notes.text == expected

    def test_multiple_revisits(self):
        notes = SlideNotes(
            slide_index=1,
            segments=["Original."],
            revisited_segments=[["First revisit."], ["Second revisit."]],
        )
        assert "**[Revisited]**\nFirst revisit." in notes.text
        assert "**[Revisited]**\nSecond revisit." in notes.text

    def test_empty(self):
        notes = SlideNotes(slide_index=1)
        assert notes.text == ""


class TestAlignTranscript:
    def test_simple_alignment(self):
        transcript = Transcript(
            segments=[
                _seg(12.0, 18.0, "First slide content."),
                _seg(22.0, 28.0, "Second slide content."),
                _seg(32.0, 38.0, "Third slide content."),
            ],
            language="de",
            duration=40.0,
        )
        timeline = [
            _entry(1, 10.0, 20.0),
            _entry(2, 20.0, 30.0),
            _entry(3, 30.0, 40.0),
        ]
        result = align_transcript(transcript, timeline)

        assert result.get_notes_text(1) == "First slide content."
        assert result.get_notes_text(2) == "Second slide content."
        assert result.get_notes_text(3) == "Third slide content."
        assert result.unassigned_segments == []

    def test_multiple_segments_per_slide(self):
        transcript = Transcript(
            segments=[
                _seg(12.0, 15.0, "Part one."),
                _seg(15.0, 18.0, "Part two."),
                _seg(22.0, 28.0, "Next slide."),
            ],
            language="de",
            duration=30.0,
        )
        timeline = [
            _entry(1, 10.0, 20.0),
            _entry(2, 20.0, 30.0),
        ]
        result = align_transcript(transcript, timeline)

        assert result.get_notes_text(1) == "Part one.\nPart two."
        assert result.get_notes_text(2) == "Next slide."

    def test_header_slide_excluded(self):
        transcript = Transcript(
            segments=[
                _seg(2.0, 8.0, "Welcome to the course."),
                _seg(12.0, 18.0, "First real content."),
            ],
            language="de",
            duration=20.0,
        )
        timeline = [
            _entry(0, 0.0, 10.0, is_header=True),
            _entry(1, 10.0, 20.0),
        ]
        result = align_transcript(transcript, timeline)

        assert result.get_notes_text(0) is None
        assert result.get_notes_text(1) == "First real content."
        assert len(result.unassigned_segments) == 1
        assert result.unassigned_segments[0].text == "Welcome to the course."

    def test_backtracking_revisit_marker(self):
        transcript = Transcript(
            segments=[
                _seg(12.0, 18.0, "First time on slide 1."),
                _seg(22.0, 28.0, "On slide 2."),
                _seg(32.0, 38.0, "On slide 3."),
                _seg(42.0, 48.0, "Back on slide 2 again."),
                _seg(52.0, 58.0, "On slide 4."),
            ],
            language="de",
            duration=60.0,
        )
        timeline = [
            _entry(1, 10.0, 20.0),
            _entry(2, 20.0, 30.0),
            _entry(3, 30.0, 40.0),
            _entry(2, 40.0, 50.0),  # backtrack to slide 2
            _entry(4, 50.0, 60.0),
        ]
        result = align_transcript(transcript, timeline)

        notes_2 = result.get_notes_text(2)
        assert notes_2 is not None
        assert "On slide 2." in notes_2
        assert "**[Revisited]**" in notes_2
        assert "Back on slide 2 again." in notes_2

    def test_empty_timeline(self):
        transcript = Transcript(
            segments=[_seg(1.0, 2.0, "hello")],
            language="de",
            duration=3.0,
        )
        result = align_transcript(transcript, [])
        assert len(result.unassigned_segments) == 1
        assert result.slide_notes == {}

    def test_empty_transcript(self):
        transcript = Transcript(segments=[], language="de", duration=0.0)
        timeline = [_entry(1, 10.0, 20.0)]
        result = align_transcript(transcript, timeline)
        assert result.slide_notes == {}
        assert result.unassigned_segments == []

    def test_segment_before_timeline(self):
        transcript = Transcript(
            segments=[_seg(1.0, 5.0, "Before timeline.")],
            language="de",
            duration=30.0,
        )
        timeline = [_entry(1, 10.0, 20.0)]
        result = align_transcript(transcript, timeline)
        assert len(result.unassigned_segments) == 1

    def test_segment_after_timeline(self):
        transcript = Transcript(
            segments=[_seg(25.0, 30.0, "After timeline.")],
            language="de",
            duration=30.0,
        )
        timeline = [_entry(1, 10.0, 20.0)]
        result = align_transcript(transcript, timeline)
        assert len(result.unassigned_segments) == 1

    def test_previous_slide_bias_in_alignment(self):
        """Segment straddling boundary should go to previous slide with bias."""
        transcript = Transcript(
            segments=[
                _seg(12.0, 18.0, "Clearly slide 1."),
                # This segment: 4s in slide 1, 6s in slide 2
                # With 40% bias, slide 1 wins (4/10 = 40% >= threshold)
                _seg(16.0, 26.0, "Straddling segment."),
                _seg(27.0, 29.0, "Clearly slide 2."),
            ],
            language="de",
            duration=30.0,
        )
        timeline = [
            _entry(1, 10.0, 20.0),
            _entry(2, 20.0, 30.0),
        ]
        result = align_transcript(transcript, timeline)

        assert "Straddling segment." in result.get_notes_text(1)

    def test_get_notes_text_missing_slide(self):
        result = AlignmentResult(slide_notes={})
        assert result.get_notes_text(99) is None

    def test_whitespace_only_segments_excluded(self):
        transcript = Transcript(
            segments=[
                _seg(12.0, 18.0, "Real text."),
                _seg(18.0, 19.0, "   "),
            ],
            language="de",
            duration=20.0,
        )
        timeline = [_entry(1, 10.0, 20.0)]
        result = align_transcript(transcript, timeline)
        assert result.get_notes_text(1) == "Real text."
