"""Tests for the reviewable-alignment engine (#960).

Covers the confidence trail the pipeline now keeps (aligner assignments,
matcher evidence), its cache round-trip, the framed align report, the
answer validator, and the reassignment rebuild.
"""

from __future__ import annotations

import pytest

from clm.slides.agent_task import VALIDATORS
from clm.voiceover.aligner import (
    AlignmentResult,
    SegmentAssignment,
    SlideNotes,
    align_transcript,
)
from clm.voiceover.harvest import PipelineArtifacts
from clm.voiceover.harvest_align import (
    AlignAnswer,
    AlignRejected,
    Reassignment,
    alignment_fingerprint,
    apply_reassignments,
    build_align_report,
    parse_align_answer,
)
from clm.voiceover.matcher import TimelineEntry
from clm.voiceover.transcribe import Transcript, TranscriptSegment


def _seg(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def _entry(idx: int, start: float, end: float, score: float = 90.0, **kw) -> TimelineEntry:
    return TimelineEntry(slide_index=idx, start_time=start, end_time=end, match_score=score, **kw)


def _alignment(
    assignments: list[SegmentAssignment],
) -> AlignmentResult:
    """Rebuild an alignment from assignment records (the accept shape)."""
    from clm.voiceover.aligner import group_assignments

    assigned = [(a.segment, a.slide_index) for a in assignments if a.slide_index is not None]
    return AlignmentResult(
        slide_notes=group_assignments(assigned),
        unassigned_segments=[a.segment for a in assignments if a.slide_index is None],
        assignments=assignments,
    )


class TestAssignmentTrail:
    def test_clear_assignment_records_full_overlap(self):
        transcript = Transcript(segments=[_seg(1.0, 3.0, "hello")], language="de", duration=10.0)
        timeline = [_entry(1, 0.0, 10.0)]
        result = align_transcript(transcript, timeline)
        assert len(result.assignments) == 1
        a = result.assignments[0]
        assert a.slide_index == 1
        assert a.reason == "clear"
        assert a.overlap_fraction == pytest.approx(1.0)

    def test_straddle_records_bias_and_runner_up(self):
        # Segment straddles the boundary: 60% on slide 1 (>= bias), 40% on 2.
        transcript = Transcript(
            segments=[_seg(8.0, 18.0, "straddle")], language="de", duration=30.0
        )
        timeline = [_entry(1, 0.0, 14.0), _entry(2, 14.0, 30.0)]
        result = align_transcript(transcript, timeline)
        (a,) = result.assignments
        assert a.slide_index == 1
        assert a.reason == "previous_slide_bias"
        assert a.overlap_fraction == pytest.approx(0.6)
        assert a.runner_up_index == 2
        assert a.runner_up_fraction == pytest.approx(0.4)

    def test_largest_overlap_wins_past_the_bias(self):
        # Only 30% on the first overlapping slide -> largest overlap wins.
        transcript = Transcript(
            segments=[_seg(8.0, 18.0, "straddle")], language="de", duration=30.0
        )
        timeline = [_entry(1, 0.0, 11.0), _entry(2, 11.0, 30.0)]
        (a,) = align_transcript(transcript, timeline).assignments
        assert a.slide_index == 2
        assert a.reason == "largest_overlap"
        assert a.runner_up_index == 1

    def test_no_overlap_and_header_reasons(self):
        transcript = Transcript(
            segments=[_seg(50.0, 55.0, "after the end"), _seg(1.0, 2.0, "on the header")],
            language="de",
            duration=60.0,
        )
        timeline = [_entry(0, 0.0, 10.0, is_header=True), _entry(1, 10.0, 40.0)]
        result = align_transcript(transcript, timeline)
        reasons = {a.segment.text: a.reason for a in result.assignments}
        assert reasons == {"after the end": "no_overlap", "on the header": "header_slide"}
        assert all(a.slide_index is None for a in result.assignments)
        assert len(result.unassigned_segments) == 2

    def test_empty_timeline_records_every_segment(self):
        transcript = Transcript(segments=[_seg(0.0, 1.0, "x")], language="de", duration=1.0)
        result = align_transcript(transcript, [])
        assert [a.reason for a in result.assignments] == ["no_overlap"]


class TestCacheRoundTrip:
    def test_alignment_with_assignments_round_trips(self):
        from clm.voiceover.cache import decode_alignment, encode_alignment

        original = _alignment(
            [
                SegmentAssignment(
                    segment=_seg(0.0, 2.0, "intro"),
                    slide_index=1,
                    reason="clear",
                    overlap_fraction=1.0,
                ),
                SegmentAssignment(
                    segment=_seg(2.0, 5.0, "straddle"),
                    slide_index=1,
                    reason="previous_slide_bias",
                    overlap_fraction=0.45,
                    runner_up_index=2,
                    runner_up_fraction=0.4,
                ),
                SegmentAssignment(
                    segment=_seg(9.0, 9.5, "noise"), slide_index=None, reason="no_overlap"
                ),
            ]
        )
        decoded = decode_alignment(encode_alignment(original))
        assert len(decoded.assignments) == 3
        a = decoded.assignments[1]
        assert a.slide_index == 1
        assert a.reason == "previous_slide_bias"
        assert a.runner_up_index == 2
        assert a.runner_up_fraction == pytest.approx(0.4)
        assert decoded.get_notes_text(1) == original.get_notes_text(1)
        assert [s.text for s in decoded.unassigned_segments] == ["noise"]

    def test_old_cache_entry_decodes_with_empty_assignments(self):
        from clm.voiceover.cache import decode_alignment

        legacy = {
            "slide_notes": {"1": {"slide_index": 1, "segments": ["old"], "revisited_segments": []}},
            "unassigned_segments": [],
        }
        decoded = decode_alignment(legacy)
        assert decoded.assignments == []
        assert decoded.get_notes_text(1) == "old"

    def test_timeline_evidence_round_trips(self):
        from clm.voiceover.cache import _decode_timeline, _encode_timeline

        entry = _entry(
            3,
            10.0,
            20.0,
            62.0,
            runner_up_index=4,
            runner_up_score=58.0,
            raw_best_index=4,
            overridden_by_sequential=True,
        )
        (decoded,) = _decode_timeline(_encode_timeline([entry]))
        assert decoded.runner_up_index == 4
        assert decoded.runner_up_score == pytest.approx(58.0)
        assert decoded.raw_best_index == 4
        assert decoded.overridden_by_sequential is True

    def test_old_timeline_entry_decodes_with_defaults(self):
        from clm.voiceover.cache import _decode_timeline

        legacy = [{"slide_index": 1, "start_time": 0.0, "end_time": 5.0, "match_score": 90.0}]
        (decoded,) = _decode_timeline(legacy)
        assert decoded.runner_up_index is None
        assert decoded.overridden_by_sequential is False

    def test_fingerprint_stable_across_round_trip(self):
        from clm.voiceover.cache import decode_alignment, encode_alignment

        alignment = _alignment(
            [
                SegmentAssignment(segment=_seg(0.0, 2.0, "a"), slide_index=1, reason="clear"),
            ]
        )
        assert alignment_fingerprint(alignment) == alignment_fingerprint(
            decode_alignment(encode_alignment(alignment))
        )


class _Group:
    """Minimal slide-group stand-in (index, title, slide_type)."""

    def __init__(self, index: int, title: str, slide_type: str = "subslide"):
        self.index = index
        self.title = title
        self.slide_type = slide_type


def _report_for(alignment, timeline=None):
    artifacts = PipelineArtifacts(alignment=alignment, timeline=timeline, transcript_language="de")
    groups = [_Group(0, "Header", "header"), _Group(1, "Alpha"), _Group(2, "Beta")]
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        video = Path(f.name)
        return build_align_report(artifacts, groups, [video])


class TestAlignReport:
    def test_straddle_framed_with_evidence(self):
        alignment = _alignment(
            [
                SegmentAssignment(
                    segment=_seg(8.0, 18.0, "straddle"),
                    slide_index=1,
                    reason="previous_slide_bias",
                    overlap_fraction=0.6,
                    runner_up_index=2,
                    runner_up_fraction=0.4,
                )
            ]
        )
        report = _report_for(alignment)
        assert report["schema"] == 1
        assert report["tool"] == "harvest"
        assert report["verb"] == "align-report"
        (item,) = report["items"]
        assert item["kind"] == "segment_assignment"
        assert item["segment_index"] == 0
        assert item["assigned_slide"] == 1
        assert item["assigned_title"] == "Alpha"
        assert item["runner_up_slide"] == 2
        assert item["runner_up_fraction"] == pytest.approx(0.4)
        assert report["alignment_fingerprint"]
        assert report["validator"] == "harvest-align"

    def test_clear_assignment_not_framed(self):
        alignment = _alignment(
            [
                SegmentAssignment(
                    segment=_seg(1.0, 3.0, "clear"),
                    slide_index=1,
                    reason="clear",
                    overlap_fraction=1.0,
                )
            ]
        )
        report = _report_for(alignment)
        assert report["items"] == []

    def test_unassigned_framed_with_assignment_index(self):
        alignment = _alignment(
            [
                SegmentAssignment(
                    segment=_seg(9.0, 9.5, "noise"), slide_index=None, reason="no_overlap"
                )
            ]
        )
        report = _report_for(alignment)
        (item,) = report["items"]
        assert item["kind"] == "unassigned_segment"
        assert item["segment_index"] == 0
        assert item["reason"] == "no_overlap"

    def test_slide_match_items(self):
        timeline = [
            _entry(1, 0.0, 10.0, 95.0),  # clean
            _entry(2, 10.0, 20.0, 40.0),  # weak score
            _entry(1, 20.0, 30.0, 70.0, overridden_by_sequential=True, raw_best_index=2),
        ]
        alignment = AlignmentResult(slide_notes={})
        report = _report_for(alignment, timeline=timeline)
        matches = [i for i in report["items"] if i["kind"] == "slide_match"]
        assert [m["slide_index"] for m in matches] == [2, 1]
        assert matches[1]["overridden_by_sequential"] is True
        assert matches[1]["raw_best_slide"] == 2
        assert "reassigning" in matches[0]["note"]

    def test_missing_assignment_records_noted(self):
        alignment = AlignmentResult(slide_notes={1: SlideNotes(1)})
        report = _report_for(alignment)
        assert "refresh-cache" in report["note"]
        assert report["assignment_count"] == 0


class TestParseAlignAnswer:
    def _valid(self) -> dict:
        return {
            "video_fingerprint": "vf",
            "alignment_fingerprint": "af",
            "reassignments": [{"segment_index": 2, "to_slide": 5}],
        }

    def test_valid(self):
        answer = parse_align_answer(self._valid())
        assert answer.reassignments == [Reassignment(segment_index=2, to_slide=5)]

    def test_to_slide_null_unassigns(self):
        payload = self._valid()
        payload["reassignments"][0]["to_slide"] = None
        assert parse_align_answer(payload).reassignments[0].to_slide is None

    @pytest.mark.parametrize(
        "mutation, match",
        [
            (lambda p: p.pop("video_fingerprint"), "video_fingerprint"),
            (lambda p: p.pop("alignment_fingerprint"), "alignment_fingerprint"),
            (lambda p: p.update(reassignments=[]), "non-empty"),
            (lambda p: p["reassignments"][0].update(segment_index=-1), "non-negative"),
            (lambda p: p["reassignments"][0].update(to_slide="x"), "slide index or null"),
            (
                lambda p: p["reassignments"].append({"segment_index": 2, "to_slide": 1}),
                "duplicate",
            ),
        ],
    )
    def test_rejections(self, mutation, match):
        payload = self._valid()
        mutation(payload)
        with pytest.raises(AlignRejected, match=match):
            parse_align_answer(payload)

    def test_registered_in_the_kit_registry(self):
        assert VALIDATORS.get("harvest-align") is parse_align_answer


class TestApplyReassignments:
    def _three_segment_alignment(self) -> AlignmentResult:
        return _alignment(
            [
                SegmentAssignment(segment=_seg(0.0, 2.0, "one"), slide_index=1, reason="clear"),
                SegmentAssignment(segment=_seg(2.0, 4.0, "two"), slide_index=2, reason="clear"),
                SegmentAssignment(
                    segment=_seg(4.0, 6.0, "noise"), slide_index=None, reason="no_overlap"
                ),
            ]
        )

    def _answer(self, *moves: tuple[int, int | None]) -> AlignAnswer:
        return AlignAnswer(
            video_fingerprint="vf",
            alignment_fingerprint="af",
            reassignments=[Reassignment(segment_index=i, to_slide=t) for i, t in moves],
        )

    def test_move_segment_to_other_slide(self):
        corrected = apply_reassignments(
            self._three_segment_alignment(),
            self._answer((1, 1)),
            valid_slides={1, 2},
            header_indices=set(),
        )
        assert corrected.slide_notes[1].segments == ["one", "two"]
        assert 2 not in corrected.slide_notes

    def test_unassign(self):
        corrected = apply_reassignments(
            self._three_segment_alignment(),
            self._answer((0, None)),
            valid_slides={1, 2},
            header_indices=set(),
        )
        assert 1 not in corrected.slide_notes
        assert [s.text for s in corrected.unassigned_segments] == ["one", "noise"]

    def test_assign_previously_unassigned(self):
        corrected = apply_reassignments(
            self._three_segment_alignment(),
            self._answer((2, 2)),
            valid_slides={1, 2},
            header_indices=set(),
        )
        assert corrected.slide_notes[2].segments == ["two", "noise"]
        assert corrected.unassigned_segments == []

    def test_move_to_earlier_slide_rederives_revisit_grouping(self):
        # a→1, b→2, c→2; moving c to slide 1 makes it a revisit (slide 2
        # was already seen), and the [Revisited] group must be re-derived,
        # never carried over.
        alignment = _alignment(
            [
                SegmentAssignment(segment=_seg(0.0, 2.0, "a"), slide_index=1, reason="clear"),
                SegmentAssignment(segment=_seg(2.0, 4.0, "b"), slide_index=2, reason="clear"),
                SegmentAssignment(segment=_seg(4.0, 6.0, "c"), slide_index=2, reason="clear"),
            ]
        )
        corrected = apply_reassignments(
            alignment,
            self._answer((2, 1)),
            valid_slides={1, 2},
            header_indices=set(),
        )
        assert corrected.slide_notes[1].segments == ["a"]
        assert corrected.slide_notes[1].revisited_segments == [["c"]]
        assert corrected.slide_notes[2].segments == ["b"]

    def test_out_of_range_rejected(self):
        with pytest.raises(AlignRejected, match="out of range"):
            apply_reassignments(
                self._three_segment_alignment(),
                self._answer((9, 1)),
                valid_slides={1, 2},
                header_indices=set(),
            )

    def test_unknown_slide_rejected(self):
        with pytest.raises(AlignRejected, match="not a slide"):
            apply_reassignments(
                self._three_segment_alignment(),
                self._answer((0, 7)),
                valid_slides={1, 2},
                header_indices=set(),
            )

    def test_header_target_rejected(self):
        with pytest.raises(AlignRejected, match="header slide"):
            apply_reassignments(
                self._three_segment_alignment(),
                self._answer((0, 0)),
                valid_slides={0, 1, 2},
                header_indices={0},
            )

    def test_missing_assignment_records_rejected(self):
        with pytest.raises(AlignRejected, match="no assignment records"):
            apply_reassignments(
                AlignmentResult(slide_notes={}),
                self._answer((0, 1)),
                valid_slides={1},
                header_indices=set(),
            )
