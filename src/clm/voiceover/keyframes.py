"""Frame extraction and slide-transition detection from video files.

This module samples frames from a video at a configurable rate, computes
frame-to-frame difference scores, and identifies transition candidates
(moments where the visual content changes significantly, suggesting a
slide change).

Transition detection uses a hybrid approach:
- Frame differencing identifies *candidate* moments (spikes in the signal)
- Nearby candidates are clustered into single transition events
- OCR confirmation (in the matcher module) determines which candidates
  are real slide transitions

See ``docs/claude/voiceover-prototype-findings.md`` for the empirical
validation of this approach.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    """Basic video metadata."""

    fps: float
    total_frames: int
    duration: float  # seconds
    width: int
    height: int


@dataclass
class TransitionCandidate:
    """A candidate moment where the slide may have changed."""

    timestamp: float  # seconds
    diff_score: float  # normalized mean absolute difference (0.0–1.0)
    confidence: float  # how far above threshold (higher = more confident)


@dataclass
class TransitionEvent:
    """A clustered transition: one or more nearby candidates merged."""

    timestamp: float  # timestamp of the peak candidate
    peak_diff: float  # highest diff score in the cluster
    confidence: float  # confidence of the peak candidate
    num_frames: int  # number of raw candidates in the cluster
    source_part_index: int = 0  # which video part this event came from
    local_timestamp: float | None = None  # pre-offset timestamp for frame extraction


def get_video_info(video_path: str | Path) -> VideoInfo:
    """Read basic metadata from a video file."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return VideoInfo(
            fps=fps,
            total_frames=total,
            duration=total / fps if fps > 0 else 0,
            width=width,
            height=height,
        )
    finally:
        cap.release()


def extract_frames(
    video_path: str | Path,
    sample_fps: float = 2.0,
) -> list[tuple[float, np.ndarray]]:
    """Extract grayscale frames from a video at the given sample rate.

    Args:
        video_path: Path to the video file.
        sample_fps: How many frames per second to sample (default: 2.0).

    Returns:
        List of (timestamp_seconds, grayscale_frame) tuples.
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    try:
        video_fps = cap.get(cv2.CAP_PROP_FPS)
        frame_interval = max(1, int(video_fps / sample_fps))

        frames: list[tuple[float, np.ndarray]] = []
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % frame_interval == 0:
                timestamp = frame_idx / video_fps
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                frames.append((timestamp, gray))
            frame_idx += 1

        return frames
    finally:
        cap.release()


def compute_differences(
    frames: list[tuple[float, np.ndarray]],
) -> list[tuple[float, float]]:
    """Compute frame-to-frame difference scores.

    Uses normalized mean absolute pixel difference (0.0 = identical,
    1.0 = completely different).

    Returns:
        List of (timestamp, difference_score) tuples. The timestamp
        corresponds to the second frame in each pair.
    """
    import numpy as np

    diffs: list[tuple[float, float]] = []
    for i in range(1, len(frames)):
        ts, frame = frames[i]
        _, prev_frame = frames[i - 1]
        diff = float(
            np.mean(np.abs(frame.astype(np.float32) - prev_frame.astype(np.float32))) / 255.0
        )
        diffs.append((ts, diff))
    return diffs


def find_transition_candidates(
    diffs: list[tuple[float, float]],
    *,
    window_size: int = 10,
    threshold_factor: float = 3.0,
    min_absolute: float | None = None,
    percentile: float = 95.0,
) -> list[TransitionCandidate]:
    """Find transition candidates as spikes in the difference signal.

    A candidate is a frame where the difference exceeds both:
    - ``threshold_factor * rolling_median`` (relative threshold)
    - An absolute minimum (auto-calibrated from the signal if not specified)

    Args:
        diffs: Frame difference data from :func:`compute_differences`.
        window_size: Rolling window size for local median computation.
        threshold_factor: Multiplier for the rolling median threshold.
        min_absolute: Fixed absolute threshold. If None, auto-calibrate
            using the given percentile of the difference distribution.
        percentile: Percentile for auto-calibrating min_absolute
            (default: 95.0). Only used when min_absolute is None.

    Returns:
        List of TransitionCandidate, sorted by confidence descending.
    """
    import numpy as np

    if not diffs:
        return []

    scores = np.array([d for _, d in diffs])
    timestamps = [ts for ts, _ in diffs]

    # Auto-calibrate absolute threshold if not specified
    if min_absolute is None:
        min_absolute = float(np.percentile(scores, percentile))
        # Ensure a sane lower bound — if the video is nearly static,
        # the percentile might be near zero
        min_absolute = max(min_absolute, 1e-4)

    candidates: list[TransitionCandidate] = []

    for i in range(len(scores)):
        # Rolling median over a window centered on i
        start = max(0, i - window_size // 2)
        end = min(len(scores), i + window_size // 2 + 1)
        median = float(np.median(scores[start:end]))

        threshold = max(median * threshold_factor, min_absolute)

        if scores[i] > threshold:
            confidence = float(scores[i] / max(threshold, 1e-6))
            candidates.append(
                TransitionCandidate(
                    timestamp=timestamps[i],
                    diff_score=float(scores[i]),
                    confidence=confidence,
                )
            )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


def cluster_transitions(
    candidates: list[TransitionCandidate],
    *,
    merge_window: float = 3.0,
) -> list[TransitionEvent]:
    """Cluster nearby transition candidates into single events.

    RISE slide transitions produce 2-3 consecutive high-difference frames
    spanning ~1-1.5 seconds. This function merges candidates within a
    time window into single transition events.

    Args:
        candidates: Raw candidates from :func:`find_transition_candidates`.
        merge_window: Maximum gap (seconds) between candidates in the same
            cluster (default: 3.0).

    Returns:
        List of TransitionEvent, sorted chronologically.
    """
    if not candidates:
        return []

    # Sort by timestamp for clustering
    sorted_candidates = sorted(candidates, key=lambda c: c.timestamp)

    clusters: list[list[TransitionCandidate]] = []
    for c in sorted_candidates:
        if clusters and c.timestamp - clusters[-1][-1].timestamp < merge_window:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    events: list[TransitionEvent] = []
    for cluster in clusters:
        peak = max(cluster, key=lambda c: c.diff_score)
        events.append(
            TransitionEvent(
                timestamp=peak.timestamp,
                peak_diff=peak.diff_score,
                confidence=peak.confidence,
                num_frames=len(cluster),
            )
        )

    return events


def get_frame_at(
    video_path: str | Path,
    timestamp: float,
    *,
    offset: float = 1.0,
) -> np.ndarray:
    """Extract a single grayscale frame from a video at a given time.

    Args:
        video_path: Path to the video file.
        timestamp: Base timestamp in seconds.
        offset: Additional offset in seconds (default: 1.0). The frame
            is captured at ``timestamp + offset`` to get a stabilized
            frame after the transition animation.

    Returns:
        Grayscale frame as a numpy array.
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_num = int((timestamp + offset) * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        if not ret:
            raise ValueError(
                f"Cannot read frame at {timestamp + offset:.1f}s "
                f"(frame {frame_num}) from {video_path}"
            )
        result: np.ndarray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return result
    finally:
        cap.release()


def detect_transitions(
    video_path: str | Path,
    *,
    sample_fps: float = 2.0,
    threshold_factor: float = 3.0,
    percentile: float = 95.0,
    merge_window: float = 3.0,
) -> tuple[list[TransitionEvent], list[tuple[float, float]]]:
    """Full transition detection pipeline: extract, diff, detect, cluster.

    This is the main entry point for transition detection.

    Args:
        video_path: Path to the video file.
        sample_fps: Frame sampling rate.
        threshold_factor: Spike detection sensitivity.
        percentile: Percentile for auto-calibrating the absolute threshold.
        merge_window: Clustering window in seconds.

    Returns:
        Tuple of (transition_events, raw_diffs) where raw_diffs can be
        used for diagnostics/plotting.
    """
    frames = extract_frames(video_path, sample_fps=sample_fps)
    diffs = compute_differences(frames)
    candidates = find_transition_candidates(
        diffs,
        threshold_factor=threshold_factor,
        percentile=percentile,
    )
    events = cluster_transitions(candidates, merge_window=merge_window)
    return events, diffs
