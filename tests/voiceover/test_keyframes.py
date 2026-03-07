"""Tests for keyframe extraction and transition detection."""

from __future__ import annotations

import numpy as np
import pytest

from clm.voiceover.keyframes import (
    TransitionCandidate,
    TransitionEvent,
    cluster_transitions,
    compute_differences,
    find_transition_candidates,
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
