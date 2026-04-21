"""Tests for the voiceover artifact cache module."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.voiceover.cache import (
    CACHE_DIRNAME,
    AlignmentsCache,
    CachePolicy,
    DetectConfig,
    SlidesKey,
    TimelinesCache,
    TranscribeConfig,
    TranscriptsCache,
    TransitionsCache,
    VideoKey,
    cached_alignment,
    cached_detect,
    cached_timeline,
    cached_transcribe,
    clear,
    iter_entries,
    prune,
    resolve_cache_root,
)


def _touch_video(tmp_path: Path, name: str = "video.mp4", size: int = 1024) -> Path:
    p = tmp_path / name
    p.write_bytes(b"x" * size)
    return p


class TestVideoKey:
    def test_from_path_produces_stable_hash(self, tmp_path):
        video = _touch_video(tmp_path)
        k1 = VideoKey.from_path(video)
        k2 = VideoKey.from_path(video)
        assert k1 == k2
        assert k1.hash == k2.hash

    def test_hash_changes_when_content_changes(self, tmp_path):
        video = _touch_video(tmp_path, size=1024)
        hash1 = VideoKey.from_path(video).hash

        # Rewrite with different size
        video.write_bytes(b"y" * 2048)
        hash2 = VideoKey.from_path(video).hash

        assert hash1 != hash2

    def test_hash_length(self, tmp_path):
        video = _touch_video(tmp_path)
        assert len(VideoKey.from_path(video).hash) == 16


class TestSlidesKey:
    def test_from_text_ignores_trailing_whitespace(self):
        k1 = SlidesKey.from_text("line one\nline two\n")
        k2 = SlidesKey.from_text("line one   \nline two   \n")
        assert k1 == k2

    def test_distinguishes_content_changes(self):
        k1 = SlidesKey.from_text("line one\n")
        k2 = SlidesKey.from_text("line two\n")
        assert k1 != k2

    def test_from_path(self, tmp_path):
        p = tmp_path / "slides.py"
        p.write_text("x = 1\n", encoding="utf-8")
        key = SlidesKey.from_path(p)
        assert key == SlidesKey.from_text("x = 1\n")


class TestTranscribeConfig:
    def test_normalize_device(self):
        assert TranscribeConfig.normalize_device("cuda") == "cuda"
        assert TranscribeConfig.normalize_device("cuda:0") == "cuda"
        assert TranscribeConfig.normalize_device("CPU") == "cpu"
        assert TranscribeConfig.normalize_device("auto") == "auto"


class TestResolveCacheRoot:
    def test_default_uses_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        root = resolve_cache_root()
        assert root == tmp_path / CACHE_DIRNAME

    def test_explicit_base_dir(self, tmp_path):
        root = resolve_cache_root(tmp_path)
        assert root == tmp_path / CACHE_DIRNAME


class TestTranscriptsCache:
    def test_round_trip(self, tmp_path):
        from clm.voiceover.transcribe import Transcript, TranscriptSegment

        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        cfg = TranscribeConfig(
            backend="faster-whisper",
            model="large-v3",
            language="de",
            device_class="cuda",
        )

        transcript = Transcript(
            segments=[
                TranscriptSegment(start=0.0, end=2.0, text="hallo"),
                TranscriptSegment(start=2.0, end=4.0, text="welt", source_part_index=1),
            ],
            language="de",
            duration=4.0,
        )

        cache = TranscriptsCache(tmp_path / "cache")
        assert cache.get(video_key, cfg) is None

        cache.put(video_key, cfg, transcript)
        loaded = cache.get(video_key, cfg)
        assert loaded is not None
        assert loaded.language == "de"
        assert loaded.duration == 4.0
        assert len(loaded.segments) == 2
        assert loaded.segments[0].text == "hallo"
        assert loaded.segments[1].source_part_index == 1

    def test_config_mismatch_is_miss(self, tmp_path):
        from clm.voiceover.transcribe import Transcript

        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        cfg_de = TranscribeConfig(
            backend="faster-whisper", model="large-v3", language="de", device_class="cuda"
        )
        cfg_en = TranscribeConfig(
            backend="faster-whisper", model="large-v3", language="en", device_class="cuda"
        )

        cache = TranscriptsCache(tmp_path / "cache")
        cache.put(video_key, cfg_de, Transcript(segments=[], language="de", duration=1.0))

        assert cache.get(video_key, cfg_en) is None
        assert cache.get(video_key, cfg_de) is not None

    def test_corrupt_entry_is_miss(self, tmp_path):
        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        cfg = TranscribeConfig(
            backend="faster-whisper", model="large-v3", language="de", device_class="cuda"
        )
        cache = TranscriptsCache(tmp_path / "cache")

        # Write garbage at the expected entry path
        cache.directory.mkdir(parents=True, exist_ok=True)
        (cache.directory / f"{video_key.hash}.json").write_text("not json{", encoding="utf-8")

        assert cache.get(video_key, cfg) is None


class TestTransitionsCache:
    def test_round_trip(self, tmp_path):
        from clm.voiceover.keyframes import TransitionEvent

        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        cfg = DetectConfig(sample_fps=2.0, threshold_factor=3.0, percentile=95.0, merge_window=3.0)

        events = [
            TransitionEvent(
                timestamp=10.0,
                peak_diff=0.5,
                confidence=2.0,
                num_frames=3,
                source_part_index=0,
                local_timestamp=10.0,
            ),
            TransitionEvent(timestamp=20.0, peak_diff=0.6, confidence=3.0, num_frames=2),
        ]

        cache = TransitionsCache(tmp_path / "cache")
        assert cache.get(video_key, cfg) is None

        cache.put(video_key, cfg, events)
        loaded = cache.get(video_key, cfg)
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0].timestamp == 10.0
        assert loaded[0].num_frames == 3
        assert loaded[0].local_timestamp == 10.0
        assert loaded[1].local_timestamp is None

    def test_config_mismatch_is_miss(self, tmp_path):
        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        cfg_a = DetectConfig(
            sample_fps=2.0, threshold_factor=3.0, percentile=95.0, merge_window=3.0
        )
        cfg_b = DetectConfig(
            sample_fps=4.0, threshold_factor=3.0, percentile=95.0, merge_window=3.0
        )
        cache = TransitionsCache(tmp_path / "cache")
        cache.put(video_key, cfg_a, [])
        assert cache.get(video_key, cfg_b) is None


class TestTimelinesCache:
    def test_round_trip(self, tmp_path):
        from clm.voiceover.matcher import TimelineEntry

        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        slides_key = SlidesKey.from_text("x = 1\n")
        cfg = {"lang": "de"}

        timeline = [
            TimelineEntry(
                slide_index=1,
                start_time=0.0,
                end_time=10.0,
                match_score=95.0,
                is_header=False,
            ),
            TimelineEntry(
                slide_index=0,
                start_time=10.0,
                end_time=20.0,
                match_score=88.0,
                is_header=True,
            ),
        ]

        cache = TimelinesCache(tmp_path / "cache")
        assert cache.get(video_key, slides_key, cfg) is None

        cache.put(video_key, slides_key, cfg, timeline)
        loaded = cache.get(video_key, slides_key, cfg)
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0].slide_index == 1
        assert loaded[1].is_header is True

    def test_slides_hash_scopes_key(self, tmp_path):
        from clm.voiceover.matcher import TimelineEntry

        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        slides_v1 = SlidesKey.from_text("version one")
        slides_v2 = SlidesKey.from_text("version two")
        cfg = {"lang": "de"}

        timeline = [TimelineEntry(slide_index=1, start_time=0.0, end_time=5.0, match_score=90.0)]
        cache = TimelinesCache(tmp_path / "cache")
        cache.put(video_key, slides_v1, cfg, timeline)

        assert cache.get(video_key, slides_v1, cfg) is not None
        assert cache.get(video_key, slides_v2, cfg) is None


class TestAlignmentsCache:
    def test_round_trip(self, tmp_path):
        from clm.voiceover.aligner import AlignmentResult, SlideNotes
        from clm.voiceover.transcribe import TranscriptSegment

        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        slides_key = SlidesKey.from_text("x = 1")
        cfg = {"bias": 0.4}

        alignment = AlignmentResult(
            slide_notes={
                1: SlideNotes(
                    slide_index=1,
                    segments=["first", "second"],
                    revisited_segments=[["third"]],
                ),
            },
            unassigned_segments=[
                TranscriptSegment(start=0.0, end=1.0, text="noise"),
            ],
        )

        cache = AlignmentsCache(tmp_path / "cache")
        cache.put(video_key, slides_key, cfg, alignment)
        loaded = cache.get(video_key, slides_key, cfg)

        assert loaded is not None
        assert set(loaded.slide_notes) == {1}
        notes = loaded.slide_notes[1]
        assert notes.segments == ["first", "second"]
        assert notes.revisited_segments == [["third"]]
        assert len(loaded.unassigned_segments) == 1
        assert loaded.unassigned_segments[0].text == "noise"


class TestHousekeeping:
    def test_iter_entries_lists_all_subdirs(self, tmp_path):
        from clm.voiceover.transcribe import Transcript

        cache_root = tmp_path / "cache"
        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        cfg = TranscribeConfig(
            backend="faster-whisper", model="large-v3", language="de", device_class="cpu"
        )

        tc = TranscriptsCache(cache_root)
        tc.put(video_key, cfg, Transcript(segments=[], language="de", duration=1.0))

        entries = iter_entries(cache_root)
        assert len(entries) == 1
        assert entries[0].subdir == "transcripts"
        assert entries[0].size > 0

    def test_clear_removes_entries(self, tmp_path):
        from clm.voiceover.transcribe import Transcript

        cache_root = tmp_path / "cache"
        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        cfg = TranscribeConfig(
            backend="faster-whisper", model="large-v3", language="de", device_class="cpu"
        )
        tc = TranscriptsCache(cache_root)
        tc.put(video_key, cfg, Transcript(segments=[], language="de", duration=1.0))

        removed = clear(cache_root)
        assert removed == 1
        assert iter_entries(cache_root) == []

    def test_prune_respects_max_age(self, tmp_path):
        import os
        import time

        from clm.voiceover.transcribe import Transcript

        cache_root = tmp_path / "cache"
        video = _touch_video(tmp_path)
        video_key = VideoKey.from_path(video)
        cfg = TranscribeConfig(
            backend="faster-whisper", model="large-v3", language="de", device_class="cpu"
        )
        tc = TranscriptsCache(cache_root)
        tc.put(video_key, cfg, Transcript(segments=[], language="de", duration=1.0))

        entry_path = iter_entries(cache_root)[0].path
        # Backdate the file by 10 days
        old = time.time() - 10 * 86400
        os.utime(entry_path, (old, old))

        # 7-day window: removes it
        assert prune(cache_root, max_age_days=7) == 1
        assert iter_entries(cache_root) == []

    def test_prune_none_is_noop(self, tmp_path):
        cache_root = tmp_path / "cache"
        cache_root.mkdir()
        assert prune(cache_root, max_age_days=None) == 0


class TestCachePolicy:
    def test_disabled_factory(self):
        policy = CachePolicy.disabled()
        assert policy.enabled is False

    def test_resolve_root_uses_override(self, tmp_path):
        override = tmp_path / "explicit-cache"
        policy = CachePolicy(cache_root=override)
        assert policy.resolve_root(Path("/some/other/base")) == override

    def test_resolve_root_falls_back_to_base_dir(self, tmp_path):
        policy = CachePolicy()
        assert policy.resolve_root(tmp_path) == tmp_path / CACHE_DIRNAME


class TestCachedTranscribe:
    def _make(self, tmp_path):
        from clm.voiceover.transcribe import Transcript, TranscriptSegment

        return Transcript(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="hi")],
            language="de",
            duration=1.0,
        )

    def test_miss_invokes_backend_and_writes(self, tmp_path):
        video = _touch_video(tmp_path)
        calls = {"n": 0}

        def fake_transcribe():
            calls["n"] += 1
            return self._make(tmp_path)

        policy = CachePolicy(cache_root=tmp_path / "cache")
        transcript, hit = cached_transcribe(
            video,
            policy=policy,
            transcribe_fn=fake_transcribe,
            backend_name="faster-whisper",
            model_size="large-v3",
            language="de",
            device="cuda:0",
        )
        assert hit is False
        assert calls["n"] == 1
        assert transcript.duration == 1.0

    def test_second_call_is_cache_hit(self, tmp_path):
        video = _touch_video(tmp_path)
        calls = {"n": 0}

        def fake_transcribe():
            calls["n"] += 1
            return self._make(tmp_path)

        policy = CachePolicy(cache_root=tmp_path / "cache")
        kwargs = {
            "policy": policy,
            "transcribe_fn": fake_transcribe,
            "backend_name": "faster-whisper",
            "model_size": "large-v3",
            "language": "de",
            "device": "cuda",
        }

        cached_transcribe(video, **kwargs)
        _, hit = cached_transcribe(video, **kwargs)

        assert hit is True
        assert calls["n"] == 1

    def test_disabled_policy_skips_cache(self, tmp_path):
        video = _touch_video(tmp_path)
        calls = {"n": 0}

        def fake_transcribe():
            calls["n"] += 1
            return self._make(tmp_path)

        policy = CachePolicy.disabled()
        cached_transcribe(
            video,
            policy=policy,
            transcribe_fn=fake_transcribe,
            backend_name="faster-whisper",
            model_size="large-v3",
            language="de",
            device="cpu",
        )
        cached_transcribe(
            video,
            policy=policy,
            transcribe_fn=fake_transcribe,
            backend_name="faster-whisper",
            model_size="large-v3",
            language="de",
            device="cpu",
        )
        assert calls["n"] == 2

    def test_refresh_forces_miss_but_writes(self, tmp_path):
        video = _touch_video(tmp_path)
        calls = {"n": 0}

        def fake_transcribe():
            calls["n"] += 1
            return self._make(tmp_path)

        policy = CachePolicy(cache_root=tmp_path / "cache")
        kwargs = {
            "transcribe_fn": fake_transcribe,
            "backend_name": "faster-whisper",
            "model_size": "large-v3",
            "language": "de",
            "device": "cpu",
        }

        cached_transcribe(video, policy=policy, **kwargs)
        cached_transcribe(
            video, policy=CachePolicy(cache_root=tmp_path / "cache", refresh=True), **kwargs
        )
        # Third call with normal policy should hit (refresh wrote the entry)
        _, hit = cached_transcribe(video, policy=policy, **kwargs)
        assert calls["n"] == 2
        assert hit is True


class TestCachedDetect:
    def test_round_trip(self, tmp_path):
        from clm.voiceover.keyframes import TransitionEvent

        video = _touch_video(tmp_path)
        calls = {"n": 0}

        def fake_detect():
            calls["n"] += 1
            return [TransitionEvent(timestamp=5.0, peak_diff=0.4, confidence=2.0, num_frames=2)]

        policy = CachePolicy(cache_root=tmp_path / "cache")

        events, hit = cached_detect(video, policy=policy, detect_fn=fake_detect)
        assert hit is False
        _, hit2 = cached_detect(video, policy=policy, detect_fn=fake_detect)
        assert hit2 is True
        assert calls["n"] == 1
        assert events[0].timestamp == 5.0


class TestCachedTimelineAndAlignment:
    def test_timeline_cached(self, tmp_path):
        from clm.voiceover.matcher import TimelineEntry

        video = _touch_video(tmp_path)
        slides = tmp_path / "slides.py"
        slides.write_text("x = 1\n", encoding="utf-8")
        calls = {"n": 0}

        def fake_timeline():
            calls["n"] += 1
            return [TimelineEntry(slide_index=1, start_time=0.0, end_time=5.0, match_score=90.0)]

        policy = CachePolicy(cache_root=tmp_path / "cache")
        cfg = {"lang": "de", "frame_offset": 1.0}

        cached_timeline(video, slides, policy=policy, timeline_fn=fake_timeline, cfg=cfg)
        _, hit = cached_timeline(video, slides, policy=policy, timeline_fn=fake_timeline, cfg=cfg)
        assert hit is True
        assert calls["n"] == 1

    def test_alignment_cached(self, tmp_path):
        from clm.voiceover.aligner import AlignmentResult, SlideNotes

        video = _touch_video(tmp_path)
        slides = tmp_path / "slides.py"
        slides.write_text("x = 1\n", encoding="utf-8")
        calls = {"n": 0}

        def fake_align():
            calls["n"] += 1
            return AlignmentResult(
                slide_notes={1: SlideNotes(slide_index=1, segments=["hi"])},
                unassigned_segments=[],
            )

        policy = CachePolicy(cache_root=tmp_path / "cache")
        cfg = {"bias": 0.4}

        cached_alignment(video, slides, policy=policy, alignment_fn=fake_align, cfg=cfg)
        _, hit = cached_alignment(video, slides, policy=policy, alignment_fn=fake_align, cfg=cfg)
        assert hit is True
        assert calls["n"] == 1


@pytest.fixture(autouse=True)
def _no_cv2_required():
    """Cache tests don't need cv2; importing keyframes/matcher lazily is fine."""
    yield
