"""Library entry point for ``clm voiceover identify-rev``.

Composes keyframe detection + OCR + revision scoring into a single call
so the CLI, the backfill pipeline, and the MCP tool share one code path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from clm.voiceover.cache import CachePolicy, cached_detect
from clm.voiceover.keyframes import detect_transitions, get_frame_at
from clm.voiceover.matcher import ocr_frame
from clm.voiceover.rev_scorer import RevisionScore, score_revisions

logger = logging.getLogger(__name__)


def build_video_fingerprint(
    videos: list[Path],
    *,
    lang: str,
    policy: CachePolicy | None = None,
    base_dir: Path | None = None,
    frame_offset: float = 1.0,
    progress_cb: Callable[[str], None] | None = None,
) -> list[str]:
    """Run keyframe detection + OCR across ``videos`` and return label order.

    Each detected transition yields one OCR label; empty results are
    dropped. Uses the artifact cache when ``policy.enabled`` so repeat
    invocations are cheap. ``base_dir`` resolves ``policy``'s cache root
    relative to a project (usually the slide file's parent).
    """
    if policy is None:
        policy = CachePolicy()

    ocr_lang = "deu+eng" if lang == "de" else "eng+deu"
    labels: list[str] = []
    for video in videos:

        def _do_detect(v=video):
            return detect_transitions(v)[0]

        events, det_hit = cached_detect(
            video,
            policy=policy,
            base_dir=base_dir,
            detect_fn=_do_detect,
        )
        if det_hit and progress_cb is not None:
            progress_cb(f"{video.name}: transitions cache hit")
        for event in events:
            try:
                frame = get_frame_at(video, event.timestamp, offset=frame_offset)
                text = ocr_frame(frame, lang=ocr_lang).strip()
            except (ValueError, FileNotFoundError) as exc:
                logger.warning("Skipping frame at %.1fs: %s", event.timestamp, exc)
                continue
            if text:
                labels.append(text)
    return labels


def identify_rev(
    slide_file: Path,
    videos: list[Path],
    *,
    lang: str,
    top: int = 5,
    limit: int = 50,
    since: str | None = None,
    policy: CachePolicy | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> list[RevisionScore]:
    """Identify the most likely historical revision for ``slide_file``.

    Returns up to ``top`` highest-scoring ``RevisionScore`` entries.
    Raises ``ValueError`` when the video fingerprint is empty (no OCR
    text extracted) or when the scored list is empty — callers surface
    these as user-facing errors.
    """
    if policy is None:
        policy = CachePolicy()

    labels = build_video_fingerprint(
        videos,
        lang=lang,
        policy=policy,
        base_dir=slide_file.parent,
        progress_cb=progress_cb,
    )
    if not labels:
        raise ValueError("video fingerprint is empty (no OCR text extracted)")

    scored = score_revisions(slide_file, labels, lang=lang, limit=limit, since=since)
    if not scored:
        raise ValueError(f"no historical revisions found for {slide_file.name}")

    return scored[:top]
