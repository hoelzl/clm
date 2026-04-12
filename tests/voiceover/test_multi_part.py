"""Tests for multi-part support across voiceover modules.

Covers: source_part_index on data classes, matcher frame extraction
routing, CLI argument order, and serialization round-trips.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.voiceover.keyframes import TransitionEvent
from clm.voiceover.transcribe import Transcript, TranscriptSegment

# ---------------------------------------------------------------------------
# TranscriptSegment.source_part_index
# ---------------------------------------------------------------------------


class TestTranscriptSegmentPartIndex:
    def test_default_is_zero(self):
        seg = TranscriptSegment(start=0.0, end=1.0, text="hello")
        assert seg.source_part_index == 0

    def test_explicit_part_index(self):
        seg = TranscriptSegment(start=0.0, end=1.0, text="hello", source_part_index=3)
        assert seg.source_part_index == 3

    def test_frozen(self):
        seg = TranscriptSegment(start=0.0, end=1.0, text="hello", source_part_index=1)
        with pytest.raises(AttributeError):
            seg.source_part_index = 2  # type: ignore[misc]

    def test_to_dict_omits_zero(self):
        seg = TranscriptSegment(start=0.0, end=1.0, text="hi")
        d = seg.to_dict()
        assert "source_part_index" not in d

    def test_to_dict_includes_nonzero(self):
        seg = TranscriptSegment(start=0.0, end=1.0, text="hi", source_part_index=2)
        d = seg.to_dict()
        assert d["source_part_index"] == 2

    def test_from_dict_without_part_index(self):
        seg = TranscriptSegment.from_dict({"start": 0.0, "end": 1.0, "text": "hi"})
        assert seg.source_part_index == 0

    def test_from_dict_with_part_index(self):
        seg = TranscriptSegment.from_dict(
            {"start": 0.0, "end": 1.0, "text": "hi", "source_part_index": 5}
        )
        assert seg.source_part_index == 5

    def test_roundtrip_with_part_index(self):
        original = TranscriptSegment(start=1.5, end=3.0, text="test", source_part_index=2)
        restored = TranscriptSegment.from_dict(original.to_dict())
        assert restored == original

    def test_roundtrip_without_part_index(self):
        original = TranscriptSegment(start=1.5, end=3.0, text="test")
        restored = TranscriptSegment.from_dict(original.to_dict())
        assert restored == original


# ---------------------------------------------------------------------------
# TransitionEvent.source_part_index and .local_timestamp
# ---------------------------------------------------------------------------


class TestTransitionEventPartFields:
    def test_defaults(self):
        event = TransitionEvent(timestamp=5.0, peak_diff=0.5, confidence=3.0, num_frames=2)
        assert event.source_part_index == 0
        assert event.local_timestamp is None

    def test_explicit_values(self):
        event = TransitionEvent(
            timestamp=105.0,
            peak_diff=0.5,
            confidence=3.0,
            num_frames=2,
            source_part_index=1,
            local_timestamp=5.0,
        )
        assert event.source_part_index == 1
        assert event.local_timestamp == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Matcher: _extract_event_frame routing
# ---------------------------------------------------------------------------


class TestExtractEventFrame:
    def test_single_video_uses_event_timestamp(self):
        from clm.voiceover.matcher import _extract_event_frame

        event = TransitionEvent(timestamp=10.0, peak_diff=0.5, confidence=3.0, num_frames=1)
        mock_frame = MagicMock()

        with patch("clm.voiceover.matcher.get_frame_at", return_value=mock_frame) as mock_get:
            result = _extract_event_frame(event, Path("video.mp4"), None, 1.0)
            mock_get.assert_called_once_with(Path("video.mp4"), 10.0, offset=1.0)
            assert result is mock_frame

    def test_multi_part_uses_local_timestamp(self):
        from clm.voiceover.matcher import _extract_event_frame

        event = TransitionEvent(
            timestamp=110.0,
            peak_diff=0.5,
            confidence=3.0,
            num_frames=1,
            source_part_index=1,
            local_timestamp=10.0,
        )
        video_paths = [Path("p0.mp4"), Path("p1.mp4"), Path("p2.mp4")]
        mock_frame = MagicMock()

        with patch("clm.voiceover.matcher.get_frame_at", return_value=mock_frame) as mock_get:
            result = _extract_event_frame(event, Path("ignored.mp4"), video_paths, 1.0)
            mock_get.assert_called_once_with(Path("p1.mp4"), 10.0, offset=1.0)
            assert result is mock_frame

    def test_multi_part_without_local_timestamp_falls_back(self):
        """If local_timestamp is None, fall back to single-video behavior."""
        from clm.voiceover.matcher import _extract_event_frame

        event = TransitionEvent(timestamp=10.0, peak_diff=0.5, confidence=3.0, num_frames=1)
        mock_frame = MagicMock()

        with patch("clm.voiceover.matcher.get_frame_at", return_value=mock_frame) as mock_get:
            result = _extract_event_frame(event, Path("fallback.mp4"), [Path("p0.mp4")], 1.0)
            mock_get.assert_called_once_with(Path("fallback.mp4"), 10.0, offset=1.0)
            assert result is mock_frame


# ---------------------------------------------------------------------------
# Matcher: total_duration parameter
# ---------------------------------------------------------------------------


class TestMatcherTotalDuration:
    def test_total_duration_used_in_timeline(self):
        """When total_duration is provided, it sets the last entry's end_time."""
        from clm.voiceover.matcher import _build_timeline

        aligned = [
            (
                TransitionEvent(timestamp=5.0, peak_diff=0.5, confidence=3.0, num_frames=1),
                1,
                90.0,
            ),
        ]
        timeline = _build_timeline(aligned, video_duration=300.0)
        assert timeline[-1].end_time == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# CLI argument order
# ---------------------------------------------------------------------------


class TestSyncCliSignature:
    def test_help_shows_slides_first(self):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["sync", "--help"])
        assert result.exit_code == 0
        # SLIDES should appear before VIDEOS in the usage line
        usage_line = result.output.split("\n")[0]
        slides_pos = usage_line.find("SLIDES")
        videos_pos = usage_line.find("VIDEOS")
        assert slides_pos < videos_pos, (
            f"SLIDES should appear before VIDEOS in usage. Got: {usage_line}"
        )

    def test_sync_requires_at_least_one_video(self):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        runner = CliRunner()
        # Only slides, no videos — should fail
        result = runner.invoke(voiceover_group, ["sync", "slides.py", "--lang", "de"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Transcript serialization with source_part_index
# ---------------------------------------------------------------------------


class TestTranscriptSerializationWithParts:
    def test_roundtrip_preserves_part_indices(self):
        transcript = Transcript(
            segments=[
                TranscriptSegment(start=0.0, end=5.0, text="a", source_part_index=0),
                TranscriptSegment(start=60.0, end=65.0, text="b", source_part_index=1),
            ],
            language="de",
            duration=120.0,
        )
        data = transcript.to_dict()
        restored = Transcript.from_dict(data)
        assert restored.segments[0].source_part_index == 0
        assert restored.segments[1].source_part_index == 1
