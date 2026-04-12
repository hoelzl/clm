"""Tests for deck recording status scanning."""

from __future__ import annotations

from pathlib import Path

from clm.recordings.workflow.deck_status import (
    DeckRecordingState,
    scan_deck_status,
    scan_section_deck_statuses,
)
from clm.recordings.workflow.directories import ensure_root, final_dir, to_process_dir


class TestScanDeckStatus:
    def test_no_recording(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        status = scan_deck_status(root, "course", "section", "03 Intro")
        assert status.state == DeckRecordingState.NO_RECORDING
        assert status.parts == []
        assert not status.has_raw
        assert not status.has_final

    def test_raw_video_only(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "course" / "section"
        td.mkdir(parents=True)
        (td / "03 Intro--RAW.mkv").write_bytes(b"video")

        status = scan_deck_status(root, "course", "section", "03 Intro")
        assert status.state == DeckRecordingState.RECORDED
        assert status.parts == [0]
        assert status.has_raw
        assert len(status.raw_paths) == 1

    def test_raw_pair_returns_ready(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "course" / "section"
        td.mkdir(parents=True)
        (td / "03 Intro--RAW.mkv").write_bytes(b"video")
        (td / "03 Intro--RAW.wav").write_bytes(b"audio")

        status = scan_deck_status(root, "course", "section", "03 Intro")
        assert status.state == DeckRecordingState.READY
        assert status.has_pair

    def test_final_exists_returns_completed(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        fd = final_dir(root) / "course" / "section"
        fd.mkdir(parents=True)
        (fd / "03 Intro.mkv").write_bytes(b"final")

        status = scan_deck_status(root, "course", "section", "03 Intro")
        assert status.state == DeckRecordingState.COMPLETED
        assert status.has_final

    def test_failed_job_returns_failed(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)

        status = scan_deck_status(
            root,
            "course",
            "section",
            "03 Intro",
            failed_jobs={"03 Intro": "job-123"},
        )
        assert status.state == DeckRecordingState.FAILED
        assert status.failed_job_id == "job-123"

    def test_completed_takes_precedence_over_recorded(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "course" / "section"
        td.mkdir(parents=True)
        (td / "03 Intro--RAW.mkv").write_bytes(b"video")
        fd = final_dir(root) / "course" / "section"
        fd.mkdir(parents=True)
        (fd / "03 Intro.mkv").write_bytes(b"final")

        status = scan_deck_status(root, "course", "section", "03 Intro")
        assert status.state == DeckRecordingState.COMPLETED

    def test_multiple_parts_detected(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "course" / "section"
        td.mkdir(parents=True)
        (td / "03 Intro (part 1)--RAW.mkv").write_bytes(b"p1")
        (td / "03 Intro (part 2)--RAW.mkv").write_bytes(b"p2")

        status = scan_deck_status(root, "course", "section", "03 Intro")
        assert status.parts == [1, 2]
        assert len(status.raw_paths) == 2

    def test_failed_with_raw_prefers_recorded(self, tmp_path: Path):
        """When raw exists and job failed, raw state (recorded) takes precedence."""
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "course" / "section"
        td.mkdir(parents=True)
        (td / "03 Intro--RAW.mkv").write_bytes(b"video")

        status = scan_deck_status(
            root,
            "course",
            "section",
            "03 Intro",
            failed_jobs={"03 Intro": "job-456"},
        )
        # Recorded takes priority since raw file exists
        assert status.state == DeckRecordingState.RECORDED


class TestScanSectionDeckStatuses:
    def test_batch_scan(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "course" / "section"
        td.mkdir(parents=True)
        (td / "01 Intro--RAW.mkv").write_bytes(b"video")

        result = scan_section_deck_statuses(root, "course", "section", ["01 Intro", "02 Loops"])
        assert "01 Intro" in result
        assert "02 Loops" in result
        assert result["01 Intro"].state == DeckRecordingState.RECORDED
        assert result["02 Loops"].state == DeckRecordingState.NO_RECORDING
