"""Tests for keyframe extraction and transition detection."""

from __future__ import annotations

import numpy as np
import pytest

from clm.voiceover.keyframes import (
    TransitionCandidate,
    TransitionEvent,
    cluster_transitions,
    compute_differences,
    detect_transitions,
    extract_frames,
    find_transition_candidates,
    iter_frames,
)


class TestComputeDifferences:
    def test_identical_frames(self):
        frame = np.zeros((100, 100), dtype=np.uint8)
        frames = [(0.0, frame), (0.5, frame.copy()), (1.0, frame.copy())]
        diffs = compute_differences(frames)
        assert len(diffs) == 2
        assert all(d == 0.0 for _, d in diffs)

    def test_completely_different_frames(self):
        black = np.zeros((100, 100), dtype=np.uint8)
        white = np.full((100, 100), 255, dtype=np.uint8)
        frames = [(0.0, black), (0.5, white)]
        diffs = compute_differences(frames)
        assert len(diffs) == 1
        assert diffs[0][1] == pytest.approx(1.0)

    def test_partial_change(self):
        frame1 = np.zeros((100, 100), dtype=np.uint8)
        frame2 = np.zeros((100, 100), dtype=np.uint8)
        # Change half the pixels to white
        frame2[:50, :] = 255
        frames = [(0.0, frame1), (0.5, frame2)]
        diffs = compute_differences(frames)
        assert diffs[0][1] == pytest.approx(0.5)

    def test_timestamps_preserved(self):
        frame = np.zeros((10, 10), dtype=np.uint8)
        frames = [(0.0, frame), (0.5, frame), (1.0, frame), (1.5, frame)]
        diffs = compute_differences(frames)
        timestamps = [ts for ts, _ in diffs]
        assert timestamps == [0.5, 1.0, 1.5]

    def test_empty_input(self):
        assert compute_differences([]) == []

    def test_single_frame(self):
        frame = np.zeros((10, 10), dtype=np.uint8)
        assert compute_differences([(0.0, frame)]) == []

    def test_generator_matches_two_pass(self):
        """Streaming diffs equal the original materialize-then-pair diffs (#1096)."""
        rng = np.random.default_rng(1096)
        frames = [(i * 0.5, rng.integers(0, 256, (90, 160), dtype=np.uint8)) for i in range(12)]
        expected = [
            (
                frames[i][0],
                float(
                    np.mean(
                        np.abs(
                            frames[i][1].astype(np.float32) - frames[i - 1][1].astype(np.float32)
                        )
                    )
                    / 255.0
                ),
            )
            for i in range(1, len(frames))
        ]
        assert compute_differences(iter(frames)) == expected
        assert compute_differences(frames) == expected


def _write_video(path, levels: list[int], fps: float = 10.0) -> None:
    """Write a tiny grayscale-level test video, one frame per entry in levels."""
    import cv2

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    assert writer.isOpened()
    for level in levels:
        writer.write(np.full((48, 64, 3), level, dtype=np.uint8))
    writer.release()


class TestIterFrames:
    def test_samples_at_requested_rate(self, tmp_path):
        video = tmp_path / "v.avi"
        _write_video(video, [0] * 10 + [255] * 10, fps=10.0)
        frames = list(iter_frames(video, sample_fps=2.0))
        assert [ts for ts, _ in frames] == pytest.approx([0.0, 0.5, 1.0, 1.5])
        assert all(f.ndim == 2 for _, f in frames)
        assert frames[0][1].mean() < 10 and frames[-1][1].mean() > 245

    def test_is_lazy_and_matches_extract_frames(self, tmp_path):
        video = tmp_path / "v.avi"
        _write_video(video, [0, 50, 100, 150, 200, 250] * 2, fps=4.0)
        gen = iter_frames(video, sample_fps=2.0)
        assert iter(gen) is gen
        streamed = list(gen)
        eager = extract_frames(video, sample_fps=2.0)
        assert [ts for ts, _ in streamed] == [ts for ts, _ in eager]
        assert all(np.array_equal(a, b) for (_, a), (_, b) in zip(streamed, eager, strict=True))

    def test_missing_video_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(iter_frames(tmp_path / "missing.avi"))

    def test_detect_transitions_finds_cut(self, tmp_path):
        video = tmp_path / "v.avi"
        _write_video(video, [20] * 40 + [230] * 40, fps=10.0)
        events, diffs = detect_transitions(video, sample_fps=2.0)
        assert len(diffs) == 15
        assert [e.timestamp for e in events] == pytest.approx([4.0])


class TestFindTransitionCandidates:
    def _make_diffs(self, scores: list[float], interval: float = 0.5) -> list[tuple[float, float]]:
        """Helper to create diff data from a list of scores."""
        return [(i * interval, s) for i, s in enumerate(scores)]

    def test_no_spikes(self):
        diffs = self._make_diffs([0.001] * 20)
        candidates = find_transition_candidates(diffs, min_absolute=0.01)
        assert len(candidates) == 0

    def test_clear_spike(self):
        # 20 low values, one spike
        scores = [0.001] * 10 + [0.05] + [0.001] * 9
        diffs = self._make_diffs(scores)
        candidates = find_transition_candidates(diffs, min_absolute=0.005)
        assert len(candidates) >= 1
        # The spike should be the highest-confidence candidate
        assert candidates[0].timestamp == pytest.approx(5.0)
        assert candidates[0].diff_score == pytest.approx(0.05)

    def test_multiple_spikes(self):
        scores = [0.001] * 10 + [0.03] + [0.001] * 10 + [0.04] + [0.001] * 10
        diffs = self._make_diffs(scores)
        candidates = find_transition_candidates(diffs, min_absolute=0.005)
        assert len(candidates) >= 2

    def test_auto_calibrate_threshold(self):
        # With percentile-based calibration, the threshold adapts
        scores = [0.0001] * 100
        scores[50] = 0.002  # Small spike, but well above the noise
        diffs = self._make_diffs(scores)
        candidates = find_transition_candidates(diffs, min_absolute=None, percentile=95.0)
        # Should detect the spike even though it's absolutely small
        assert len(candidates) >= 1

    def test_sorted_by_confidence(self):
        scores = [0.001] * 10 + [0.02] + [0.001] * 10 + [0.05] + [0.001] * 10
        diffs = self._make_diffs(scores)
        candidates = find_transition_candidates(diffs, min_absolute=0.005)
        assert len(candidates) >= 2
        # Highest confidence first
        assert candidates[0].confidence >= candidates[1].confidence

    def test_empty_input(self):
        assert find_transition_candidates([]) == []


class TestClusterTransitions:
    def test_single_candidate(self):
        candidates = [TransitionCandidate(10.0, 0.03, 5.0)]
        events = cluster_transitions(candidates, merge_window=3.0)
        assert len(events) == 1
        assert events[0].timestamp == 10.0
        assert events[0].num_frames == 1

    def test_nearby_candidates_merged(self):
        candidates = [
            TransitionCandidate(10.0, 0.02, 4.0),
            TransitionCandidate(10.5, 0.03, 5.0),
            TransitionCandidate(11.0, 0.01, 3.0),
        ]
        events = cluster_transitions(candidates, merge_window=3.0)
        assert len(events) == 1
        assert events[0].timestamp == 10.5  # peak
        assert events[0].peak_diff == 0.03
        assert events[0].num_frames == 3

    def test_distant_candidates_separate(self):
        candidates = [
            TransitionCandidate(10.0, 0.03, 5.0),
            TransitionCandidate(20.0, 0.02, 4.0),
        ]
        events = cluster_transitions(candidates, merge_window=3.0)
        assert len(events) == 2
        assert events[0].timestamp == 10.0
        assert events[1].timestamp == 20.0

    def test_chronological_order(self):
        # Input not sorted by time
        candidates = [
            TransitionCandidate(20.0, 0.02, 4.0),
            TransitionCandidate(10.0, 0.03, 5.0),
            TransitionCandidate(30.0, 0.01, 3.0),
        ]
        events = cluster_transitions(candidates, merge_window=3.0)
        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps)

    def test_empty_input(self):
        assert cluster_transitions([]) == []

    def test_custom_merge_window(self):
        candidates = [
            TransitionCandidate(10.0, 0.03, 5.0),
            TransitionCandidate(12.0, 0.02, 4.0),  # 2s apart
        ]
        # With 1s window: separate
        events_narrow = cluster_transitions(candidates, merge_window=1.0)
        assert len(events_narrow) == 2
        # With 3s window: merged
        events_wide = cluster_transitions(candidates, merge_window=3.0)
        assert len(events_wide) == 1
