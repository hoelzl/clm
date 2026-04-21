"""Artifact cache for the voiceover pipeline.

Stages in the voiceover pipeline — transcription, transition detection,
OCR-based timeline matching, and transcript-to-slide alignment — produce
intermediate artifacts that are deterministic with respect to their inputs.
Re-running the pipeline with the same inputs is expensive (especially ASR on
multi-GB videos) but should produce identical results, which makes these
artifacts good cache candidates.

This module provides a local filesystem cache layered under the working
directory's ``.clm/voiceover-cache/`` tree. Keys are derived from cheap
fingerprints (path + mtime + size for videos; normalized source hash for
slide files) so cache writes/reads stay fast even on large inputs.

The cache is consulted at four points:

- :class:`TranscriptsCache` — keyed by ``video_hash`` and backend config.
- :class:`TransitionsCache` — keyed by ``video_hash`` and detection config.
- :class:`TimelinesCache` — keyed by ``video_hash`` + ``slides_hash``.
- :class:`AlignmentsCache` — keyed by ``video_hash`` + ``slides_hash``.

Corrupt or unreadable entries are treated as a miss (the cache overwrites on
the next successful write). Config mismatch is likewise a miss, not an
error — callers may cache alternate configurations side by side.

Usage::

    cache_root = resolve_cache_root()                   # .clm/voiceover-cache/
    video_key = VideoKey.from_path(video_path)
    slides_key = SlidesKey.from_path(slide_path)
    tx_cfg = TranscribeConfig(backend="faster-whisper", model="large-v3",
                              language="de", device_class="cuda")

    store = TranscriptsCache(cache_root)
    cached = store.get(video_key, tx_cfg)
    if cached is None:
        transcript = transcribe_video(...)
        store.put(video_key, tx_cfg, transcript)
    else:
        transcript = cached
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

CACHE_DIRNAME = ".clm/voiceover-cache"
CACHE_SUBDIRS = ("transcripts", "transitions", "timelines", "alignments")


# ---------------------------------------------------------------------------
# Keys and configs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoKey:
    """Fingerprint for a video file used as a cache key.

    Uses path + mtime + size rather than full-file SHA because videos can
    be several GB; hashing the entire file on every invocation would defeat
    the cache. The fingerprint is stable across invocations of the same
    file and invalidates cleanly when the file is replaced or edited.
    """

    abspath: str
    mtime_ns: int
    size: int

    @classmethod
    def from_path(cls, path: str | Path) -> VideoKey:
        p = Path(path).resolve()
        stat = p.stat()
        return cls(abspath=str(p), mtime_ns=stat.st_mtime_ns, size=stat.st_size)

    @property
    def hash(self) -> str:
        h = hashlib.sha1(
            f"{self.abspath}|{self.mtime_ns}|{self.size}".encode(),
            usedforsecurity=False,
        )
        return h.hexdigest()[:16]


@dataclass(frozen=True)
class SlidesKey:
    """Fingerprint for a slide source file used as a cache key.

    Hashes the file's bytes after stripping trailing whitespace per line.
    This ignores line-ending churn and trailing-space edits (which don't
    change pipeline outputs) without being so aggressive that semantic
    changes get lost.
    """

    hash: str

    @classmethod
    def from_path(cls, path: str | Path) -> SlidesKey:
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_text(text)

    @classmethod
    def from_text(cls, text: str) -> SlidesKey:
        normalized = "\n".join(line.rstrip() for line in text.splitlines())
        h = hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False)
        return cls(hash=h.hexdigest()[:16])


@dataclass(frozen=True)
class TranscribeConfig:
    """Subset of transcription parameters that affect the output.

    Device is collapsed to a coarse class ("cuda"/"cpu") rather than the
    exact device string because "auto" and "cuda:0" should share cache
    entries on the same machine.
    """

    backend: str
    model: str
    language: str | None
    device_class: str

    @classmethod
    def normalize_device(cls, device: str) -> str:
        d = device.lower()
        if d.startswith("cuda"):
            return "cuda"
        if d == "auto":
            # "auto" resolves at runtime; we treat it as a separate class
            # so cached cuda entries aren't incorrectly served for
            # explicit cpu calls on the same machine.
            return "auto"
        return "cpu"


@dataclass(frozen=True)
class DetectConfig:
    """Parameters that affect transition detection output."""

    sample_fps: float
    threshold_factor: float
    percentile: float
    merge_window: float


# ---------------------------------------------------------------------------
# Cache primitives
# ---------------------------------------------------------------------------


def resolve_cache_root(base_dir: str | Path | None = None) -> Path:
    """Resolve the default cache root under ``base_dir`` (or cwd)."""
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    return base / CACHE_DIRNAME


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON to *path* atomically via temp-file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile keeps the file open on Windows, which blocks
    # rename; use mkstemp + manual write instead.
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict | None:
    """Read JSON from *path*. Returns None on miss or corruption."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Corrupt cache entry %s: %s — treating as miss", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Unexpected cache payload %s (not a dict) — treating as miss", path)
        return None
    return data


T = TypeVar("T")


class _JsonCache(Generic[T]):
    """Base class for JSON-backed caches under a named subdirectory."""

    subdir: str

    def __init__(
        self,
        cache_root: Path,
        *,
        encoder: Callable[[T], Any],
        decoder: Callable[[Any], T],
    ):
        self._root = cache_root / self.subdir
        self._encoder = encoder
        self._decoder = decoder

    @property
    def directory(self) -> Path:
        return self._root

    def _entry_path(self, key: str) -> Path:
        return self._root / f"{key}.json"

    def _load(self, key: str) -> dict | None:
        return _read_json(self._entry_path(key))

    def _store(self, key: str, payload: dict) -> None:
        _atomic_write_json(self._entry_path(key), payload)

    def _config_matches(self, entry: dict, expected: dict) -> bool:
        stored = entry.get("config")
        if not isinstance(stored, dict):
            return False
        return stored == expected


# ---------------------------------------------------------------------------
# Typed caches
# ---------------------------------------------------------------------------


class TranscriptsCache(_JsonCache):
    """Cache for :class:`clm.voiceover.transcribe.Transcript` artifacts."""

    subdir = "transcripts"

    def __init__(self, cache_root: Path):
        from clm.voiceover.transcribe import Transcript

        super().__init__(
            cache_root,
            encoder=lambda t: t.to_dict(),
            decoder=Transcript.from_dict,
        )

    def get(self, video: VideoKey, cfg: TranscribeConfig):
        entry = self._load(video.hash)
        if entry is None:
            return None
        if not self._config_matches(entry, asdict(cfg)):
            return None
        artifact = entry.get("artifact")
        if not isinstance(artifact, dict):
            return None
        try:
            return self._decoder(artifact)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Cannot decode cached transcript %s: %s", video.hash, exc)
            return None

    def put(self, video: VideoKey, cfg: TranscribeConfig, transcript) -> None:
        payload = {
            "version": 1,
            "kind": "transcript",
            "video": asdict(video),
            "config": asdict(cfg),
            "artifact": self._encoder(transcript),
        }
        self._store(video.hash, payload)


class TransitionsCache(_JsonCache):
    """Cache for lists of :class:`TransitionEvent`."""

    subdir = "transitions"

    def __init__(self, cache_root: Path):
        super().__init__(
            cache_root,
            encoder=_encode_transitions,
            decoder=_decode_transitions,
        )

    def get(self, video: VideoKey, cfg: DetectConfig):
        entry = self._load(video.hash)
        if entry is None:
            return None
        if not self._config_matches(entry, asdict(cfg)):
            return None
        artifact = entry.get("artifact")
        if not isinstance(artifact, list):
            return None
        try:
            return self._decoder(artifact)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Cannot decode cached transitions %s: %s", video.hash, exc)
            return None

    def put(self, video: VideoKey, cfg: DetectConfig, events) -> None:
        payload = {
            "version": 1,
            "kind": "transitions",
            "video": asdict(video),
            "config": asdict(cfg),
            "artifact": self._encoder(events),
        }
        self._store(video.hash, payload)


class TimelinesCache(_JsonCache):
    """Cache for slide-timelines (output of ``match_events_to_slides``)."""

    subdir = "timelines"

    def __init__(self, cache_root: Path):
        super().__init__(
            cache_root,
            encoder=_encode_timeline,
            decoder=_decode_timeline,
        )

    @staticmethod
    def _compose_key(video: VideoKey, slides: SlidesKey) -> str:
        return f"{video.hash}_{slides.hash}"

    def get(self, video: VideoKey, slides: SlidesKey, cfg: dict[str, Any]):
        entry = self._load(self._compose_key(video, slides))
        if entry is None:
            return None
        if not self._config_matches(entry, cfg):
            return None
        artifact = entry.get("artifact")
        if not isinstance(artifact, list):
            return None
        try:
            return self._decoder(artifact)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Cannot decode cached timeline %s: %s",
                self._compose_key(video, slides),
                exc,
            )
            return None

    def put(
        self,
        video: VideoKey,
        slides: SlidesKey,
        cfg: dict[str, Any],
        timeline,
    ) -> None:
        payload = {
            "version": 1,
            "kind": "timeline",
            "video": asdict(video),
            "slides": asdict(slides),
            "config": cfg,
            "artifact": self._encoder(timeline),
        }
        self._store(self._compose_key(video, slides), payload)


class AlignmentsCache(_JsonCache):
    """Cache for :class:`AlignmentResult` objects."""

    subdir = "alignments"

    def __init__(self, cache_root: Path):
        super().__init__(
            cache_root,
            encoder=_encode_alignment,
            decoder=_decode_alignment,
        )

    @staticmethod
    def _compose_key(video: VideoKey, slides: SlidesKey) -> str:
        return f"{video.hash}_{slides.hash}"

    def get(self, video: VideoKey, slides: SlidesKey, cfg: dict[str, Any]):
        entry = self._load(self._compose_key(video, slides))
        if entry is None:
            return None
        if not self._config_matches(entry, cfg):
            return None
        artifact = entry.get("artifact")
        if not isinstance(artifact, dict):
            return None
        try:
            return self._decoder(artifact)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Cannot decode cached alignment %s: %s",
                self._compose_key(video, slides),
                exc,
            )
            return None

    def put(
        self,
        video: VideoKey,
        slides: SlidesKey,
        cfg: dict[str, Any],
        alignment,
    ) -> None:
        payload = {
            "version": 1,
            "kind": "alignment",
            "video": asdict(video),
            "slides": asdict(slides),
            "config": cfg,
            "artifact": self._encoder(alignment),
        }
        self._store(self._compose_key(video, slides), payload)


# ---------------------------------------------------------------------------
# Cache inspection / housekeeping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheEntrySummary:
    """Describes a single cache entry for the ``cache list`` command."""

    subdir: str
    key: str
    path: Path
    size: int


def iter_entries(cache_root: Path) -> list[CacheEntrySummary]:
    """Return a flat list of cache entries across all known subdirectories."""
    summaries: list[CacheEntrySummary] = []
    for sub in CACHE_SUBDIRS:
        d = cache_root / sub
        if not d.exists():
            continue
        for entry in sorted(d.glob("*.json")):
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            summaries.append(CacheEntrySummary(subdir=sub, key=entry.stem, path=entry, size=size))
    return summaries


def clear(cache_root: Path) -> int:
    """Remove all cache entries. Returns the number of files deleted."""
    removed = 0
    for entry in iter_entries(cache_root):
        try:
            entry.path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Cannot remove %s: %s", entry.path, exc)
    return removed


def prune(cache_root: Path, *, max_age_days: float | None = None) -> int:
    """Remove cache entries older than *max_age_days*.

    If *max_age_days* is ``None`` the function is a no-op and returns 0.
    """
    if max_age_days is None:
        return 0

    import time

    cutoff = time.time() - max_age_days * 86400.0
    removed = 0
    for entry in iter_entries(cache_root):
        try:
            if entry.path.stat().st_mtime < cutoff:
                entry.path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Cannot prune %s: %s", entry.path, exc)
    return removed


# ---------------------------------------------------------------------------
# Encode/decode helpers
# ---------------------------------------------------------------------------


def _encode_transitions(events) -> list[dict]:
    return [
        {
            "timestamp": e.timestamp,
            "peak_diff": e.peak_diff,
            "confidence": e.confidence,
            "num_frames": e.num_frames,
            "source_part_index": e.source_part_index,
            "local_timestamp": e.local_timestamp,
        }
        for e in events
    ]


def _decode_transitions(data: list[dict]):
    from clm.voiceover.keyframes import TransitionEvent

    return [
        TransitionEvent(
            timestamp=d["timestamp"],
            peak_diff=d["peak_diff"],
            confidence=d["confidence"],
            num_frames=d["num_frames"],
            source_part_index=d.get("source_part_index", 0),
            local_timestamp=d.get("local_timestamp"),
        )
        for d in data
    ]


def _encode_timeline(timeline) -> list[dict]:
    return [
        {
            "slide_index": e.slide_index,
            "start_time": e.start_time,
            "end_time": e.end_time,
            "match_score": e.match_score,
            "is_header": e.is_header,
        }
        for e in timeline
    ]


def _decode_timeline(data: list[dict]):
    from clm.voiceover.matcher import TimelineEntry

    return [
        TimelineEntry(
            slide_index=d["slide_index"],
            start_time=d["start_time"],
            end_time=d["end_time"],
            match_score=d["match_score"],
            is_header=d.get("is_header", False),
        )
        for d in data
    ]


def _encode_alignment(alignment) -> dict:
    return {
        "slide_notes": {
            str(idx): {
                "slide_index": notes.slide_index,
                "segments": list(notes.segments),
                "revisited_segments": [list(r) for r in notes.revisited_segments],
            }
            for idx, notes in alignment.slide_notes.items()
        },
        "unassigned_segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "source_part_index": s.source_part_index,
            }
            for s in alignment.unassigned_segments
        ],
    }


def _decode_alignment(data: dict):
    from clm.voiceover.aligner import AlignmentResult, SlideNotes
    from clm.voiceover.transcribe import TranscriptSegment

    slide_notes = {}
    for idx_str, payload in data.get("slide_notes", {}).items():
        idx = int(idx_str)
        slide_notes[idx] = SlideNotes(
            slide_index=payload["slide_index"],
            segments=list(payload.get("segments", [])),
            revisited_segments=[list(r) for r in payload.get("revisited_segments", [])],
        )

    unassigned = [
        TranscriptSegment(
            start=s["start"],
            end=s["end"],
            text=s["text"],
            source_part_index=s.get("source_part_index", 0),
        )
        for s in data.get("unassigned_segments", [])
    ]

    return AlignmentResult(slide_notes=slide_notes, unassigned_segments=unassigned)


# ---------------------------------------------------------------------------
# Policy + cache-aware pipeline helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CachePolicy:
    """Controls cache participation during a pipeline invocation.

    - ``enabled=False`` bypasses the cache entirely (no reads, no writes).
    - ``refresh=True`` forces a miss on read but still writes the new result.
    - ``cache_root`` overrides the default ``.clm/voiceover-cache/`` location.
    """

    enabled: bool = True
    refresh: bool = False
    cache_root: Path | None = None

    @classmethod
    def disabled(cls) -> CachePolicy:
        return cls(enabled=False)

    def resolve_root(self, base_dir: str | Path | None = None) -> Path:
        if self.cache_root is not None:
            return self.cache_root
        return resolve_cache_root(base_dir)


def cached_transcribe(
    video_path: str | Path,
    *,
    policy: CachePolicy,
    base_dir: str | Path | None = None,
    transcribe_fn: Callable[[], Any],
    backend_name: str,
    model_size: str,
    language: str | None,
    device: str,
):
    """Run transcription with cache lookup + write-back.

    ``transcribe_fn`` is a thunk that returns a fresh ``Transcript`` when
    invoked. It's only called on a miss. Keeps this helper agnostic to
    the underlying ASR backend.
    """
    cfg = TranscribeConfig(
        backend=backend_name,
        model=model_size,
        language=language,
        device_class=TranscribeConfig.normalize_device(device),
    )

    if not policy.enabled:
        return transcribe_fn(), False

    video_key = VideoKey.from_path(video_path)
    cache = TranscriptsCache(policy.resolve_root(base_dir))

    if not policy.refresh:
        hit = cache.get(video_key, cfg)
        if hit is not None:
            logger.info("Transcript cache hit for %s", Path(video_path).name)
            return hit, True

    transcript = transcribe_fn()
    cache.put(video_key, cfg, transcript)
    return transcript, False


def cached_detect(
    video_path: str | Path,
    *,
    policy: CachePolicy,
    base_dir: str | Path | None = None,
    detect_fn: Callable[[], Any],
    sample_fps: float = 2.0,
    threshold_factor: float = 3.0,
    percentile: float = 95.0,
    merge_window: float = 3.0,
):
    """Run transition detection with cache lookup + write-back.

    ``detect_fn`` returns the list of :class:`TransitionEvent` objects on a
    cache miss.
    """
    cfg = DetectConfig(
        sample_fps=sample_fps,
        threshold_factor=threshold_factor,
        percentile=percentile,
        merge_window=merge_window,
    )

    if not policy.enabled:
        return detect_fn(), False

    video_key = VideoKey.from_path(video_path)
    cache = TransitionsCache(policy.resolve_root(base_dir))

    if not policy.refresh:
        hit = cache.get(video_key, cfg)
        if hit is not None:
            logger.info("Transitions cache hit for %s", Path(video_path).name)
            return hit, True

    events = detect_fn()
    cache.put(video_key, cfg, events)
    return events, False


def cached_timeline(
    video_path: str | Path,
    slide_path: str | Path,
    *,
    policy: CachePolicy,
    base_dir: str | Path | None = None,
    timeline_fn: Callable[[], Any],
    cfg: dict[str, Any],
):
    """Run timeline matching with cache lookup + write-back.

    The cache key combines video and slides fingerprints. ``cfg`` is the
    match configuration (lang, frame_offset, etc.) serialized as a dict.
    """
    if not policy.enabled:
        return timeline_fn(), False

    video_key = VideoKey.from_path(video_path)
    slides_key = SlidesKey.from_path(slide_path)
    cache = TimelinesCache(policy.resolve_root(base_dir))

    if not policy.refresh:
        hit = cache.get(video_key, slides_key, cfg)
        if hit is not None:
            logger.info("Timeline cache hit for %s", Path(video_path).name)
            return hit, True

    timeline = timeline_fn()
    cache.put(video_key, slides_key, cfg, timeline)
    return timeline, False


def cached_alignment(
    video_path: str | Path,
    slide_path: str | Path,
    *,
    policy: CachePolicy,
    base_dir: str | Path | None = None,
    alignment_fn: Callable[[], Any],
    cfg: dict[str, Any],
):
    """Run transcript-to-slide alignment with cache lookup + write-back."""
    if not policy.enabled:
        return alignment_fn(), False

    video_key = VideoKey.from_path(video_path)
    slides_key = SlidesKey.from_path(slide_path)
    cache = AlignmentsCache(policy.resolve_root(base_dir))

    if not policy.refresh:
        hit = cache.get(video_key, slides_key, cfg)
        if hit is not None:
            logger.info("Alignment cache hit for %s", Path(video_path).name)
            return hit, True

    alignment = alignment_fn()
    cache.put(video_key, slides_key, cfg, alignment)
    return alignment, False
