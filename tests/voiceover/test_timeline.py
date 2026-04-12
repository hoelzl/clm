"""Tests for multi-part video timeline construction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from clm.voiceover.keyframes import TransitionEvent
from clm.voiceover.timeline import (
    VideoPart,
    build_parts,
    merge_transcripts,
    offset_events,
    offset_transcript,
    probe_duration,
)
from clm.voiceover.transcribe import Transcript, TranscriptSegment

# ---------------------------------------------------------------------------
# probe_duration
# ---------------------------------------------------------------------------


class TestProbeDuration:
    def test_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError, match="Video not found"):
            probe_duration(Path("/nonexistent/video.mp4"))

    @patch("clm.voiceover.timeline.subprocess.run")
    def test_ffprobe_failure_raises(self, mock_run, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        mock_run.return_value = type("R", (), {"returncode": 1, "stderr": "error", "stdout": ""})()
        with pytest.raises(RuntimeError, match="ffprobe failed"):
            probe_duration(video)

    @patch("clm.voiceover.timeline.subprocess.run")
    def test_non_numeric_output_raises(self, mock_run, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        mock_run.return_value = type("R", (), {"returncode": 0, "stderr": "", "stdout": "N/A"})()
        with pytest.raises(RuntimeError, match="non-numeric duration"):
            probe_duration(video)

    @patch("clm.voiceover.timeline.subprocess.run")
    def test_returns_duration(self, mock_run, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        mock_run.return_value = type(
            "R", (), {"returncode": 0, "stderr": "", "stdout": "123.45\n"}
        )()
        assert probe_duration(video) == pytest.approx(123.45)


# ---------------------------------------------------------------------------
# build_parts
# ---------------------------------------------------------------------------


class TestBuildParts:
    @patch("clm.voiceover.timeline.probe_duration")
    def test_single_part(self, mock_probe):
        mock_probe.return_value = 60.0
        parts = build_parts([Path("video.mp4")])
        assert len(parts) == 1
        assert parts[0].index == 0
        assert parts[0].offset == pytest.approx(0.0)
        assert parts[0].duration == pytest.approx(60.0)

    @patch("clm.voiceover.timeline.probe_duration")
    def test_three_parts_offsets(self, mock_probe):
        mock_probe.side_effect = [100.0, 200.0, 150.0]
        paths = [Path("p1.mp4"), Path("p2.mp4"), Path("p3.mp4")]
        parts = build_parts(paths)

        assert len(parts) == 3
        assert parts[0].offset == pytest.approx(0.0)
        assert parts[1].offset == pytest.approx(100.0)
        assert parts[2].offset == pytest.approx(300.0)

    @patch("clm.voiceover.timeline.probe_duration")
    def test_preserves_path_order(self, mock_probe):
        mock_probe.return_value = 10.0
        paths = [Path("z.mp4"), Path("a.mp4"), Path("m.mp4")]
        parts = build_parts(paths)
        assert [p.path for p in parts] == paths

    @patch("clm.voiceover.timeline.probe_duration")
    def test_part_index_in_error(self, mock_probe):
        mock_probe.side_effect = [10.0, FileNotFoundError("gone")]
        paths = [Path("ok.mp4"), Path("missing.mp4")]
        with pytest.raises(FileNotFoundError, match="part 1"):
            build_parts(paths)


# ---------------------------------------------------------------------------
# offset_transcript
# ---------------------------------------------------------------------------


class TestOffsetTranscript:
    def test_offsets_applied(self):
        transcript = Transcript(
            segments=[
                TranscriptSegment(start=0.0, end=5.0, text="hello"),
                TranscriptSegment(start=5.0, end=10.0, text="world"),
            ],
            language="de",
            duration=10.0,
        )
        part = VideoPart(index=2, path=Path("p2.mp4"), duration=10.0, offset=100.0)
        result = offset_transcript(transcript, part)

        assert result.segments[0].start == pytest.approx(100.0)
        assert result.segments[0].end == pytest.approx(105.0)
        assert result.segments[1].start == pytest.approx(105.0)
        assert result.segments[1].end == pytest.approx(110.0)

    def test_source_part_index_tagged(self):
        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=5.0, text="hi")],
            language="en",
            duration=5.0,
        )
        part = VideoPart(index=3, path=Path("p3.mp4"), duration=5.0, offset=50.0)
        result = offset_transcript(transcript, part)
        assert result.segments[0].source_part_index == 3

    def test_text_preserved(self):
        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=5.0, text="preserve me")],
            language="de",
            duration=5.0,
        )
        part = VideoPart(index=0, path=Path("p0.mp4"), duration=5.0, offset=0.0)
        result = offset_transcript(transcript, part)
        assert result.segments[0].text == "preserve me"

    def test_zero_offset_first_part(self):
        transcript = Transcript(
            segments=[TranscriptSegment(start=1.0, end=2.0, text="a")],
            language="de",
            duration=2.0,
        )
        part = VideoPart(index=0, path=Path("p0.mp4"), duration=2.0, offset=0.0)
        result = offset_transcript(transcript, part)
        assert result.segments[0].start == pytest.approx(1.0)
        assert result.segments[0].source_part_index == 0


# ---------------------------------------------------------------------------
# offset_events
# ---------------------------------------------------------------------------


class TestOffsetEvents:
    def test_timestamps_offset(self):
        events = [
            TransitionEvent(timestamp=5.0, peak_diff=0.5, confidence=3.0, num_frames=2),
            TransitionEvent(timestamp=15.0, peak_diff=0.7, confidence=4.0, num_frames=3),
        ]
        part = VideoPart(index=1, path=Path("p1.mp4"), duration=20.0, offset=100.0)
        result = offset_events(events, part)

        assert result[0].timestamp == pytest.approx(105.0)
        assert result[1].timestamp == pytest.approx(115.0)

    def test_local_timestamp_preserved(self):
        events = [
            TransitionEvent(timestamp=5.0, peak_diff=0.5, confidence=3.0, num_frames=2),
        ]
        part = VideoPart(index=1, path=Path("p1.mp4"), duration=20.0, offset=100.0)
        result = offset_events(events, part)
        assert result[0].local_timestamp == pytest.approx(5.0)

    def test_source_part_index_set(self):
        events = [
            TransitionEvent(timestamp=5.0, peak_diff=0.5, confidence=3.0, num_frames=2),
        ]
        part = VideoPart(index=2, path=Path("p2.mp4"), duration=20.0, offset=200.0)
        result = offset_events(events, part)
        assert result[0].source_part_index == 2

    def test_peak_diff_and_confidence_preserved(self):
        events = [
            TransitionEvent(timestamp=5.0, peak_diff=0.42, confidence=3.7, num_frames=5),
        ]
        part = VideoPart(index=0, path=Path("p0.mp4"), duration=20.0, offset=0.0)
        result = offset_events(events, part)
        assert result[0].peak_diff == pytest.approx(0.42)
        assert result[0].confidence == pytest.approx(3.7)
        assert result[0].num_frames == 5


# ---------------------------------------------------------------------------
# merge_transcripts
# ---------------------------------------------------------------------------


class TestMergeTranscripts:
    def test_merge_two(self):
        t1 = Transcript(
            segments=[TranscriptSegment(start=0.0, end=5.0, text="a", source_part_index=0)],
            language="de",
            duration=10.0,
        )
        t2 = Transcript(
            segments=[TranscriptSegment(start=10.0, end=15.0, text="b", source_part_index=1)],
            language="de",
            duration=10.0,
        )
        merged = merge_transcripts([t1, t2])
        assert len(merged.segments) == 2
        assert merged.duration == pytest.approx(20.0)
        assert merged.language == "de"

    def test_segments_in_order(self):
        t1 = Transcript(
            segments=[
                TranscriptSegment(start=0.0, end=3.0, text="first"),
                TranscriptSegment(start=3.0, end=6.0, text="second"),
            ],
            language="en",
            duration=10.0,
        )
        t2 = Transcript(
            segments=[
                TranscriptSegment(start=10.0, end=13.0, text="third"),
            ],
            language="en",
            duration=10.0,
        )
        merged = merge_transcripts([t1, t2])
        texts = [s.text for s in merged.segments]
        assert texts == ["first", "second", "third"]

    def test_timestamps_monotonic_across_parts(self):
        """Merged timestamps should be monotonically increasing."""
        t1 = Transcript(
            segments=[
                TranscriptSegment(start=0.0, end=5.0, text="a", source_part_index=0),
                TranscriptSegment(start=5.0, end=10.0, text="b", source_part_index=0),
            ],
            language="de",
            duration=10.0,
        )
        t2 = Transcript(
            segments=[
                TranscriptSegment(start=10.0, end=15.0, text="c", source_part_index=1),
                TranscriptSegment(start=15.0, end=20.0, text="d", source_part_index=1),
            ],
            language="de",
            duration=10.0,
        )
        merged = merge_transcripts([t1, t2])
        starts = [s.start for s in merged.segments]
        assert starts == sorted(starts)

    def test_total_duration_is_sum(self):
        parts = [
            Transcript(segments=[], language="de", duration=100.0),
            Transcript(segments=[], language="de", duration=200.0),
            Transcript(segments=[], language="de", duration=150.0),
        ]
        merged = merge_transcripts(parts)
        assert merged.duration == pytest.approx(450.0)


# ---------------------------------------------------------------------------
# Integration: full per-part pipeline
# ---------------------------------------------------------------------------


class TestMultiPartPipeline:
    """Integration test using the timeline module end-to-end."""

    @patch("clm.voiceover.timeline.probe_duration")
    def test_three_part_offset_chain(self, mock_probe):
        """Three parts produce correctly offset segments and events."""
        mock_probe.side_effect = [60.0, 120.0, 90.0]
        paths = [Path("p0.mp4"), Path("p1.mp4"), Path("p2.mp4")]
        parts = build_parts(paths)

        # Simulate per-part transcripts and events
        transcripts = []
        all_events = []

        for part in parts:
            seg = TranscriptSegment(start=1.0, end=2.0, text=f"part{part.index}")
            t = Transcript(segments=[seg], language="de", duration=part.duration)
            transcripts.append(offset_transcript(t, part))

            ev = TransitionEvent(timestamp=0.5, peak_diff=0.5, confidence=3.0, num_frames=1)
            all_events.extend(offset_events([ev], part))

        merged = merge_transcripts(transcripts)

        # Check segment offsets
        assert merged.segments[0].start == pytest.approx(1.0)  # part 0: offset=0
        assert merged.segments[1].start == pytest.approx(61.0)  # part 1: offset=60
        assert merged.segments[2].start == pytest.approx(181.0)  # part 2: offset=180

        # Check event offsets
        assert all_events[0].timestamp == pytest.approx(0.5)  # part 0
        assert all_events[1].timestamp == pytest.approx(60.5)  # part 1
        assert all_events[2].timestamp == pytest.approx(180.5)  # part 2

        # Check local timestamps
        assert all_events[0].local_timestamp == pytest.approx(0.5)
        assert all_events[1].local_timestamp == pytest.approx(0.5)
        assert all_events[2].local_timestamp == pytest.approx(0.5)

        # Check source_part_index
        assert [s.source_part_index for s in merged.segments] == [0, 1, 2]
        assert [e.source_part_index for e in all_events] == [0, 1, 2]

        # Check total duration
        assert merged.duration == pytest.approx(270.0)

    @patch("clm.voiceover.timeline.probe_duration")
    def test_single_part_matches_direct_behavior(self, mock_probe):
        """Single-part should produce same timestamps as direct (no offset)."""
        mock_probe.return_value = 300.0
        parts = build_parts([Path("single.mp4")])

        seg = TranscriptSegment(start=10.0, end=20.0, text="content")
        transcript = Transcript(segments=[seg], language="en", duration=300.0)
        result = offset_transcript(transcript, parts[0])

        # With offset=0, timestamps are unchanged
        assert result.segments[0].start == pytest.approx(10.0)
        assert result.segments[0].end == pytest.approx(20.0)
        assert result.segments[0].source_part_index == 0
