"""Multi-part video timeline construction.

This module provides helpers for processing multiple video parts into a
single logical timeline. Each part is transcribed and keyframe-detected
independently; results are merged using running offsets so the aligner
sees one continuous timeline without any on-disk concatenation.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clm.voiceover.keyframes import TransitionEvent
from clm.voiceover.transcribe import Transcript, TranscriptSegment

logger = logging.getLogger(__name__)


@dataclass
class VideoPart:
    """A single video part in a multi-part recording."""

    index: int  # 0, 1, 2, ...
    path: Path
    duration: float  # seconds, from ffprobe
    offset: float  # cumulative start time (Sigma duration of parts 0..i-1)


def probe_duration(video_path: Path) -> float:
    """Probe the duration of a video file using ffprobe.

    Args:
        video_path: Path to the video file.

    Returns:
        Duration in seconds.

    Raises:
        FileNotFoundError: If the video file does not exist.
        RuntimeError: If ffprobe fails or returns no duration.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {video_path.name} "
            f"(exit {result.returncode}): {result.stderr[:500]}"
        )

    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(
            f"ffprobe returned non-numeric duration for {video_path.name}: "
            f"{result.stdout.strip()!r}"
        ) from None


def build_parts(video_paths: list[Path]) -> list[VideoPart]:
    """Probe durations and build VideoPart list with running offsets.

    Args:
        video_paths: Ordered list of video file paths. Order is
            authoritative -- parts are NOT sorted by name or mtime.

    Returns:
        List of VideoPart with computed offsets.

    Raises:
        FileNotFoundError: If any video file does not exist (includes
            the part index in the error message).
        RuntimeError: If ffprobe fails on any part.
    """
    parts: list[VideoPart] = []
    cumulative_offset = 0.0

    for i, path in enumerate(video_paths):
        try:
            duration = probe_duration(path)
        except FileNotFoundError:
            raise FileNotFoundError(f"Video part {i} not found: {path}") from None
        except RuntimeError as exc:
            raise RuntimeError(f"Failed to probe video part {i} ({path.name}): {exc}") from exc

        parts.append(
            VideoPart(
                index=i,
                path=path,
                duration=duration,
                offset=cumulative_offset,
            )
        )
        cumulative_offset += duration

    logger.info(
        "Built %d video parts, total duration %.1fs",
        len(parts),
        cumulative_offset,
    )
    return parts


def offset_transcript(transcript: Transcript, part: VideoPart) -> Transcript:
    """Apply time offset to a transcript's segments and tag with part index.

    Creates a new Transcript with all segment timestamps shifted by the
    part's offset and ``source_part_index`` set.

    Args:
        transcript: Per-part transcript with local timestamps.
        part: The video part this transcript belongs to.

    Returns:
        New Transcript with offset timestamps.
    """
    offset_segments = [
        TranscriptSegment(
            start=seg.start + part.offset,
            end=seg.end + part.offset,
            text=seg.text,
            source_part_index=part.index,
        )
        for seg in transcript.segments
    ]
    return Transcript(
        segments=offset_segments,
        language=transcript.language,
        duration=transcript.duration,
    )


def offset_events(
    events: list[TransitionEvent],
    part: VideoPart,
) -> list[TransitionEvent]:
    """Apply time offset to transition events and tag with part index.

    Each event's ``timestamp`` is shifted by the part's offset.
    The original timestamp is preserved in ``local_timestamp`` for
    per-part frame extraction by the matcher.

    Args:
        events: Per-part transition events with local timestamps.
        part: The video part these events belong to.

    Returns:
        New list of TransitionEvent with offset timestamps.
    """
    return [
        TransitionEvent(
            timestamp=event.timestamp + part.offset,
            peak_diff=event.peak_diff,
            confidence=event.confidence,
            num_frames=event.num_frames,
            source_part_index=part.index,
            local_timestamp=event.timestamp,
        )
        for event in events
    ]


def merge_transcripts(
    transcripts: list[Transcript],
) -> Transcript:
    """Merge multiple (already-offset) transcripts into one.

    Args:
        transcripts: Offset transcripts, one per part, in order.

    Returns:
        Single merged Transcript spanning all parts.
    """
    all_segments: list[TranscriptSegment] = []
    total_duration = 0.0
    language = transcripts[0].language if transcripts else "unknown"

    for transcript in transcripts:
        all_segments.extend(transcript.segments)
        total_duration += transcript.duration

    return Transcript(
        segments=all_segments,
        language=language,
        duration=total_duration,
    )
