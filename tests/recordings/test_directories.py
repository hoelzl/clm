"""Tests for recordings workflow directory management."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.recordings.workflow.directories import (
    SUBDIRS,
    PendingPair,
    archive_dir,
    ensure_root,
    final_dir,
    find_pending_pairs,
    superseded_dir,
    takes_dir,
    to_process_dir,
    validate_root,
)


class TestEnsureRoot:
    def test_creates_all_subdirs(self, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        for name in SUBDIRS:
            assert (root / name).is_dir()

    def test_idempotent(self, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        ensure_root(root)  # second call should not fail
        for name in SUBDIRS:
            assert (root / name).is_dir()

    def test_preserves_existing_content(self, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        marker = root / "to-process" / "marker.txt"
        marker.write_text("keep me")
        ensure_root(root)
        assert marker.read_text() == "keep me"


class TestValidateRoot:
    def test_valid_root(self, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        assert validate_root(root) == []

    def test_missing_root(self, tmp_path: Path):
        root = tmp_path / "nonexistent"
        errors = validate_root(root)
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_missing_subdir(self, tmp_path: Path):
        root = tmp_path / "recordings"
        root.mkdir()
        for name in SUBDIRS:
            if name == "archive":
                continue
            (root / name).mkdir()
        # archive/ intentionally missing
        errors = validate_root(root)
        assert len(errors) == 1
        assert "archive" in errors[0]

    def test_all_subdirs_missing(self, tmp_path: Path):
        root = tmp_path / "recordings"
        root.mkdir()
        errors = validate_root(root)
        assert len(errors) == len(SUBDIRS)


class TestDirHelpers:
    def test_to_process_dir(self, tmp_path: Path):
        assert to_process_dir(tmp_path) == tmp_path / "to-process"

    def test_final_dir(self, tmp_path: Path):
        assert final_dir(tmp_path) == tmp_path / "final"

    def test_archive_dir(self, tmp_path: Path):
        assert archive_dir(tmp_path) == tmp_path / "archive"

    def test_superseded_dir(self, tmp_path: Path):
        assert superseded_dir(tmp_path) == tmp_path / "superseded"

    def test_takes_dir(self, tmp_path: Path):
        assert takes_dir(tmp_path) == tmp_path / "takes"

    def test_ensure_root_creates_takes(self, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        assert takes_dir(root).is_dir()


class TestPendingPair:
    def test_base_name(self, tmp_path: Path):
        pair = PendingPair(
            video=tmp_path / "topic--RAW.mp4",
            audio=tmp_path / "topic--RAW.wav",
            relative_dir=Path("course/section"),
        )
        assert pair.base_name == "topic"

    def test_base_name_no_suffix(self, tmp_path: Path):
        pair = PendingPair(
            video=tmp_path / "topic.mp4",
            audio=tmp_path / "topic.wav",
            relative_dir=Path("course/section"),
        )
        assert pair.base_name == "topic"


class TestFindPendingPairs:
    def _make_pair(self, tp: Path, rel_dir: str, stem: str) -> None:
        """Create a raw video + audio pair in the to-process tree."""
        d = tp / rel_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}--RAW.mp4").write_bytes(b"video")
        (d / f"{stem}--RAW.wav").write_bytes(b"audio")

    def test_finds_pair(self, tmp_path: Path):
        tp = tmp_path / "to-process"
        self._make_pair(tp, "course/section", "topic")
        pairs = find_pending_pairs(tp)
        assert len(pairs) == 1
        assert pairs[0].base_name == "topic"
        assert pairs[0].relative_dir == Path("course/section")

    def test_ignores_video_without_audio(self, tmp_path: Path):
        tp = tmp_path / "to-process"
        d = tp / "course" / "section"
        d.mkdir(parents=True)
        (d / "topic--RAW.mp4").write_bytes(b"video")
        # No .wav file
        assert find_pending_pairs(tp) == []

    def test_ignores_audio_without_video(self, tmp_path: Path):
        tp = tmp_path / "to-process"
        d = tp / "course" / "section"
        d.mkdir(parents=True)
        (d / "topic--RAW.wav").write_bytes(b"audio")
        # No video file
        assert find_pending_pairs(tp) == []

    def test_ignores_non_raw_files(self, tmp_path: Path):
        tp = tmp_path / "to-process"
        d = tp / "course" / "section"
        d.mkdir(parents=True)
        (d / "topic.mp4").write_bytes(b"video")
        (d / "topic.wav").write_bytes(b"audio")
        assert find_pending_pairs(tp) == []

    def test_multiple_pairs_sorted(self, tmp_path: Path):
        tp = tmp_path / "to-process"
        self._make_pair(tp, "course/s2", "beta")
        self._make_pair(tp, "course/s1", "alpha")
        pairs = find_pending_pairs(tp)
        assert len(pairs) == 2
        assert pairs[0].base_name == "alpha"
        assert pairs[1].base_name == "beta"

    def test_mkv_extension(self, tmp_path: Path):
        tp = tmp_path / "to-process"
        d = tp / "course" / "section"
        d.mkdir(parents=True)
        (d / "topic--RAW.mkv").write_bytes(b"video")
        (d / "topic--RAW.wav").write_bytes(b"audio")
        pairs = find_pending_pairs(tp)
        assert len(pairs) == 1

    def test_custom_suffix(self, tmp_path: Path):
        tp = tmp_path / "to-process"
        d = tp / "course" / "section"
        d.mkdir(parents=True)
        (d / "topic__ORIG.mp4").write_bytes(b"video")
        (d / "topic__ORIG.wav").write_bytes(b"audio")
        pairs = find_pending_pairs(tp, raw_suffix="__ORIG")
        assert len(pairs) == 1
        assert pairs[0].base_name == "topic"

    def test_empty_directory(self, tmp_path: Path):
        tp = tmp_path / "to-process"
        tp.mkdir()
        assert find_pending_pairs(tp) == []

    def test_flat_structure(self, tmp_path: Path):
        """Pairs directly in to-process/ (no subdirectories)."""
        tp = tmp_path / "to-process"
        tp.mkdir()
        (tp / "lecture--RAW.mp4").write_bytes(b"video")
        (tp / "lecture--RAW.wav").write_bytes(b"audio")
        pairs = find_pending_pairs(tp)
        assert len(pairs) == 1
        assert pairs[0].relative_dir == Path(".")

    def test_ignores_takes_sibling_directory(self, tmp_path: Path):
        """Files under a sibling ``takes/`` tree are never returned.

        ``find_pending_pairs`` only ever scans the ``to-process/`` root it's
        given, but the regression guard documents the guarantee: putting a
        raw file in ``takes/`` never makes it eligible for processing.
        """
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = to_process_dir(root)
        self._make_pair(tp, "course/section", "topic")

        takes = takes_dir(root) / "course" / "section"
        takes.mkdir(parents=True)
        (takes / "topic (part 1, take 1)--RAW.mp4").write_bytes(b"v")
        (takes / "topic (part 1, take 1)--RAW.wav").write_bytes(b"a")

        pairs = find_pending_pairs(tp)
        assert len(pairs) == 1
        assert pairs[0].base_name == "topic"
