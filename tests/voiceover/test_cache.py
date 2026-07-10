"""Tests for the voiceover artifact cache module."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.voiceover.cache import (
    CACHE_DIRNAME,
    AlignmentsCache,
    CachePolicy,
    DetectConfig,
    MultiVideoKey,
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
    video_key_for,
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


class TestMultiVideoKey:
    def test_hash_covers_every_part_in_order(self, tmp_path):
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"part a")
        b.write_bytes(b"part b")
        key_ab = MultiVideoKey.from_paths([a, b])
        key_ba = MultiVideoKey.from_paths([b, a])
        assert key_ab.hash == MultiVideoKey.from_paths([a, b]).hash  # stable
        assert key_ab.hash != key_ba.hash  # order-sensitive
        assert len(key_ab.hash) == 16

    def test_hash_changes_when_any_part_changes(self, tmp_path):
        import os

        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"part a")
        b.write_bytes(b"part b")
        before = MultiVideoKey.from_paths([a, b]).hash
        b.write_bytes(b"part b, re-rendered longer")
        os.utime(b, ns=(1, 1))
        assert MultiVideoKey.from_paths([a, b]).hash != before

    def test_video_key_for_dispatch(self, tmp_path):
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"part a")
        b.write_bytes(b"part b")
        assert isinstance(video_key_for(a), VideoKey)
        assert isinstance(video_key_for([a]), VideoKey)
        assert isinstance(video_key_for([a, b]), MultiVideoKey)

    def test_multi_part_timeline_cache_round_trip(self, tmp_path):
        from clm.voiceover.cache import CachePolicy, cached_timeline
        from clm.voiceover.matcher import TimelineEntry

        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"part a")
        b.write_bytes(b"part b")
        slides = tmp_path / "slides_t.de.py"
        slides.write_text("# %% [markdown]\n# x\n", encoding="utf-8")
        policy = CachePolicy(cache_root=tmp_path / "cache")
        timeline = [TimelineEntry(slide_index=1, start_time=0.0, end_time=5.0, match_score=90.0)]
        calls = []

        def compute():
            calls.append(1)
            return timeline

        first, hit1 = cached_timeline(
            [a, b], slides, policy=policy, timeline_fn=compute, cfg={"lang": "de"}
        )
        second, hit2 = cached_timeline(
            [a, b], slides, policy=policy, timeline_fn=compute, cfg={"lang": "de"}
        )
        assert (hit1, hit2) == (False, True)
        assert len(calls) == 1
        assert second[0].slide_index == 1
        # A different part order is a different recording -> a miss.
        _, hit3 = cached_timeline(
            [b, a], slides, policy=policy, timeline_fn=compute, cfg={"lang": "de"}
        )
        assert hit3 is False


class TestTranscribeConfig:
    def test_normalize_device(self):
        assert TranscribeConfig.normalize_device("cuda") == "cuda"
        assert TranscribeConfig.normalize_device("cuda:0") == "cuda"
        assert TranscribeConfig.normalize_device("CPU") == "cpu"
        assert TranscribeConfig.normalize_device("auto") == "auto"


def _mark_project_root(path: Path) -> None:
    """Give *path* a project-root marker so the walk-up stops there."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text("[tool.other]\n", encoding="utf-8")


class TestResolveCacheRoot:
    """The default root is shared and deck-independent (issue #568)."""

    def test_default_uses_cwd_project_root(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        _mark_project_root(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert resolve_cache_root() == tmp_path / ".clm-cache" / "voiceover"

    def test_base_dir_walks_up_to_project_root(self, tmp_path, monkeypatch):
        # The deck dir anchors the walk-up; the root's shared cache wins, so
        # every deck in the project resolves to the SAME location.
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        _mark_project_root(tmp_path)
        deck_a = tmp_path / "slides" / "module_410" / "topic_010"
        deck_b = tmp_path / "slides" / "module_550" / "topic_010_azav"
        deck_a.mkdir(parents=True)
        deck_b.mkdir(parents=True)
        shared = tmp_path / ".clm-cache" / "voiceover"
        assert resolve_cache_root(deck_a) == shared
        assert resolve_cache_root(deck_b) == shared

    def test_env_override(self, tmp_path, monkeypatch):
        target = tmp_path / "from-env"
        monkeypatch.setenv("CLM_CACHE_DIR", str(target))
        assert resolve_cache_root(tmp_path) == target / "voiceover"

    def test_pyproject_cache_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.clm]\ncache_dir = "custom-cache"\n', encoding="utf-8"
        )
        deck = tmp_path / "slides" / "topic_010"
        deck.mkdir(parents=True)
        assert resolve_cache_root(deck) == tmp_path / "custom-cache" / "voiceover"

    def test_legacy_cache_root_is_per_deck(self, tmp_path):
        from clm.voiceover.cache import legacy_cache_root

        assert legacy_cache_root(tmp_path) == tmp_path / CACHE_DIRNAME


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

    def test_resolve_root_defaults_to_shared_root(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        _mark_project_root(tmp_path)
        deck = tmp_path / "slides" / "topic_010"
        deck.mkdir(parents=True)
        policy = CachePolicy()
        assert policy.resolve_root(deck) == tmp_path / ".clm-cache" / "voiceover"


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


class TestLegacyPromotion:
    """Pre-#568 per-deck entries are found on a miss and promoted."""

    @staticmethod
    def _transcript():
        from clm.voiceover.transcribe import Transcript, TranscriptSegment

        return Transcript(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="hi")],
            language="de",
            duration=1.0,
        )

    @staticmethod
    def _seed_legacy_transcript(deck_dir: Path, video: Path, transcript) -> None:
        from clm.voiceover.cache import legacy_cache_root

        cfg = TranscribeConfig(
            backend="faster-whisper", model="large-v3", language="de", device_class="cpu"
        )
        TranscriptsCache(legacy_cache_root(deck_dir)).put(
            VideoKey.from_path(video), cfg, transcript
        )

    def test_transcript_promoted_from_per_deck_cache(self, tmp_path):
        video = _touch_video(tmp_path)
        deck = tmp_path / "topic_010"
        deck.mkdir()
        self._seed_legacy_transcript(deck, video, self._transcript())
        shared = tmp_path / "shared-cache"
        policy = CachePolicy(cache_root=shared)

        def must_not_run():
            raise AssertionError("legacy hit must not re-transcribe")

        kwargs = {
            "policy": policy,
            "base_dir": deck,
            "transcribe_fn": must_not_run,
            "backend_name": "faster-whisper",
            "model_size": "large-v3",
            "language": "de",
            "device": "cpu",
        }
        transcript, hit = cached_transcribe(video, **kwargs)
        assert hit is True
        assert transcript.segments[0].text == "hi"
        # Promoted: the shared root now holds the entry itself.
        key = VideoKey.from_path(video)
        assert (shared / "transcripts" / f"{key.hash}.json").is_file()
        _, hit2 = cached_transcribe(video, **kwargs)
        assert hit2 is True

    def test_refresh_skips_legacy_probe(self, tmp_path):
        video = _touch_video(tmp_path)
        deck = tmp_path / "topic_010"
        deck.mkdir()
        self._seed_legacy_transcript(deck, video, self._transcript())
        calls = {"n": 0}

        def fresh():
            calls["n"] += 1
            return self._transcript()

        cached_transcribe(
            video,
            policy=CachePolicy(cache_root=tmp_path / "shared-cache", refresh=True),
            base_dir=deck,
            transcribe_fn=fresh,
            backend_name="faster-whisper",
            model_size="large-v3",
            language="de",
            device="cpu",
        )
        assert calls["n"] == 1

    def test_no_probe_when_legacy_is_primary(self, tmp_path):
        from clm.voiceover.cache import legacy_cache_root

        # --cache-root pointing AT the old per-deck location: no self-probe,
        # plain read works.
        video = _touch_video(tmp_path)
        deck = tmp_path / "topic_010"
        deck.mkdir()
        self._seed_legacy_transcript(deck, video, self._transcript())
        _, hit = cached_transcribe(
            video,
            policy=CachePolicy(cache_root=legacy_cache_root(deck)),
            base_dir=deck,
            transcribe_fn=lambda: (_ for _ in ()).throw(AssertionError("must hit")),
            backend_name="faster-whisper",
            model_size="large-v3",
            language="de",
            device="cpu",
        )
        assert hit is True

    def test_detect_promoted_from_per_deck_cache(self, tmp_path):
        from clm.voiceover.cache import legacy_cache_root
        from clm.voiceover.keyframes import TransitionEvent

        video = _touch_video(tmp_path)
        deck = tmp_path / "topic_010"
        deck.mkdir()
        cfg = DetectConfig(sample_fps=2.0, threshold_factor=3.0, percentile=95.0, merge_window=3.0)
        events = [TransitionEvent(timestamp=5.0, peak_diff=0.4, confidence=2.0, num_frames=2)]
        TransitionsCache(legacy_cache_root(deck)).put(VideoKey.from_path(video), cfg, events)

        loaded, hit = cached_detect(
            video,
            policy=CachePolicy(cache_root=tmp_path / "shared-cache"),
            base_dir=deck,
            detect_fn=lambda: (_ for _ in ()).throw(AssertionError("must hit legacy")),
        )
        assert hit is True
        assert loaded[0].timestamp == 5.0

    def test_timeline_and_alignment_promoted(self, tmp_path):
        from clm.voiceover.aligner import AlignmentResult, SlideNotes
        from clm.voiceover.cache import legacy_cache_root
        from clm.voiceover.matcher import TimelineEntry

        video = _touch_video(tmp_path)
        deck = tmp_path / "topic_010"
        deck.mkdir()
        slides = deck / "slides.py"
        slides.write_text("x = 1\n", encoding="utf-8")
        video_key = VideoKey.from_path(video)
        slides_key = SlidesKey.from_path(slides)
        legacy = legacy_cache_root(deck)
        tl_cfg = {"lang": "de"}
        al_cfg = {"bias": 0.4}
        TimelinesCache(legacy).put(
            video_key,
            slides_key,
            tl_cfg,
            [TimelineEntry(slide_index=1, start_time=0.0, end_time=5.0, match_score=90.0)],
        )
        AlignmentsCache(legacy).put(
            video_key,
            slides_key,
            al_cfg,
            AlignmentResult(
                slide_notes={1: SlideNotes(slide_index=1, segments=["hi"])},
                unassigned_segments=[],
            ),
        )

        policy = CachePolicy(cache_root=tmp_path / "shared-cache")

        def boom():
            raise AssertionError("must hit legacy")

        timeline, tl_hit = cached_timeline(
            video, slides, policy=policy, base_dir=deck, timeline_fn=boom, cfg=tl_cfg
        )
        alignment, al_hit = cached_alignment(
            video, slides, policy=policy, base_dir=deck, alignment_fn=boom, cfg=al_cfg
        )
        assert (tl_hit, al_hit) == (True, True)
        assert timeline[0].slide_index == 1
        assert alignment.slide_notes[1].segments == ["hi"]


class TestForkedDecksShareCache:
    def test_second_deck_hits_first_decks_transcript(self, tmp_path, monkeypatch):
        """The issue #568 scenario: a forked deck reuses the original's ASR."""
        from clm.voiceover.transcribe import Transcript

        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        _mark_project_root(tmp_path)
        deck_a = tmp_path / "slides" / "module_410" / "topic_010"
        deck_b = tmp_path / "slides" / "module_550" / "topic_010_azav"
        deck_a.mkdir(parents=True)
        deck_b.mkdir(parents=True)
        video = _touch_video(tmp_path, name="recording.mp4")
        calls = {"n": 0}

        def fake_transcribe():
            calls["n"] += 1
            return Transcript(segments=[], language="de", duration=1.0)

        kwargs = {
            "policy": CachePolicy(),
            "transcribe_fn": fake_transcribe,
            "backend_name": "faster-whisper",
            "model_size": "large-v3",
            "language": "de",
            "device": "cpu",
        }
        _, hit_a = cached_transcribe(video, base_dir=deck_a, **kwargs)
        _, hit_b = cached_transcribe(video, base_dir=deck_b, **kwargs)
        assert (hit_a, hit_b) == (False, True)
        assert calls["n"] == 1
        assert (tmp_path / ".clm-cache" / "voiceover" / "transcripts").is_dir()


@pytest.fixture(autouse=True)
def _no_cv2_required():
    """Cache tests don't need cv2; importing keyframes/matcher lazily is fine."""
    yield
