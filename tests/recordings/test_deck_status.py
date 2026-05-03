"""Tests for deck recording status scanning."""

from __future__ import annotations

from pathlib import Path

from clm.recordings.workflow.deck_status import (
    DeckRecordingState,
    scan_deck_status,
    scan_section_deck_statuses,
    scan_section_takes,
    scan_take_files,
)
from clm.recordings.workflow.directories import (
    ensure_root,
    final_dir,
    takes_dir,
    to_process_dir,
)


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
        assert status.final_parts == [0]
        assert status.parts == [0]

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

    def test_partial_completion_shows_recorded(self, tmp_path: Path):
        """When some parts are in final/ but raw files remain, state is RECORDED."""
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "course" / "section"
        td.mkdir(parents=True)
        (td / "03 Intro (part 2)--RAW.mkv").write_bytes(b"raw p2")
        fd = final_dir(root) / "course" / "section"
        fd.mkdir(parents=True)
        (fd / "03 Intro (part 1).mkv").write_bytes(b"final p1")

        status = scan_deck_status(root, "course", "section", "03 Intro")
        assert status.state == DeckRecordingState.RECORDED
        assert status.final_parts == [1]
        assert status.raw_parts == [2]
        assert status.parts == [1, 2]
        assert len(status.raw_paths) == 1

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

    def test_active_job_returns_processing(self, tmp_path: Path):
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
            active_jobs={"03 Intro": "job-789"},
        )
        assert status.state == DeckRecordingState.PROCESSING

    def test_processing_takes_precedence_over_recorded(self, tmp_path: Path):
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
            failed_jobs={"03 Intro": "job-old"},
            active_jobs={"03 Intro": "job-new"},
        )
        assert status.state == DeckRecordingState.PROCESSING


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


class TestFinalParts:
    """``final_parts`` must count video files only.

    Regression: Auphonic writes an ``.edl`` companion alongside each
    ``.mp4`` output. Earlier versions iterated ``final/`` unfiltered,
    so a single-part deck showed ``final_parts == [0, 0]`` and the
    lectures page rendered ``done: 0, 0; raw: 1``.
    """

    def test_edl_companion_does_not_double_count(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        fd = final_dir(root) / "course" / "section"
        fd.mkdir(parents=True)
        (fd / "03 Intro.mp4").write_bytes(b"final video")
        (fd / "03 Intro.edl").write_text("# Auphonic edit decision list")

        status = scan_deck_status(root, "course", "section", "03 Intro")

        assert status.final_parts == [0]
        assert status.parts == [0]

    def test_multiple_parts_with_companions(self, tmp_path: Path):
        """Two parts + two .edl companions should still yield exactly two final parts."""
        root = tmp_path / "rec"
        ensure_root(root)
        fd = final_dir(root) / "course" / "section"
        fd.mkdir(parents=True)
        (fd / "03 Intro (part 1).mp4").write_bytes(b"p1 video")
        (fd / "03 Intro (part 1).edl").write_text("edl 1")
        (fd / "03 Intro (part 2).mp4").write_bytes(b"p2 video")
        (fd / "03 Intro (part 2).edl").write_text("edl 2")

        status = scan_deck_status(root, "course", "section", "03 Intro")

        assert status.final_parts == [1, 2]


class TestPartsStatus:
    """The chip strip in the lectures UI consumes ``DeckStatus.parts_status``.

    Per-part state is computed from raw_parts/final_parts plus per-(deck,
    part) job maps. Phase C added these fields; the legacy deck-level
    badge is preserved for backwards compatibility.
    """

    def test_parts_status_marks_processed_part(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        fd = final_dir(root) / "c" / "s"
        fd.mkdir(parents=True)
        (fd / "03 Intro.mp4").write_bytes(b"final")

        status = scan_deck_status(root, "c", "s", "03 Intro")

        assert len(status.parts_status) == 1
        assert status.parts_status[0].part == 0
        assert status.parts_status[0].state == "processed"

    def test_parts_status_marks_recorded_when_raw_only(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "03 Intro (part 1)--RAW.mkv").write_bytes(b"raw")

        status = scan_deck_status(root, "c", "s", "03 Intro")

        assert [(p.part, p.state) for p in status.parts_status] == [(1, "recorded")]

    def test_parts_status_marks_per_part_processing(self, tmp_path: Path):
        """A per-(deck, part) active job marks its chip as processing."""
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "03 Intro (part 1)--RAW.mkv").write_bytes(b"raw")
        (td / "03 Intro (part 2)--RAW.mkv").write_bytes(b"raw")

        status = scan_deck_status(
            root,
            "c",
            "s",
            "03 Intro",
            active_jobs_per_part={("03 Intro", 1): "job-1"},
        )

        by_part = {p.part: p.state for p in status.parts_status}
        assert by_part == {1: "processing", 2: "recorded"}

    def test_parts_status_marks_failed_retry_dot(self, tmp_path: Path):
        """A processed part with a most-recent FAILED job sets has_failed_retry."""
        root = tmp_path / "rec"
        ensure_root(root)
        fd = final_dir(root) / "c" / "s"
        fd.mkdir(parents=True)
        (fd / "03 Intro.mp4").write_bytes(b"final")

        status = scan_deck_status(
            root,
            "c",
            "s",
            "03 Intro",
            failed_jobs_per_part={("03 Intro", 0): "job-fail"},
        )

        assert status.parts_status[0].state == "processed"
        assert status.parts_status[0].has_failed_retry is True

    def test_parts_status_take_count_includes_active(self, tmp_path: Path):
        """A part with two superseded takes in takes/ reports take_count == 3."""
        root = tmp_path / "rec"
        ensure_root(root)
        td = to_process_dir(root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "03 Intro (part 1)--RAW.mkv").write_bytes(b"raw")
        tk = takes_dir(root) / "c" / "s"
        tk.mkdir(parents=True)
        (tk / "03 Intro (part 1, take 1).mp4").write_bytes(b"old")
        (tk / "03 Intro (part 1, take 1)--RAW.mkv").write_bytes(b"old raw")
        (tk / "03 Intro (part 1, take 2).mp4").write_bytes(b"old")

        result = scan_section_deck_statuses(root, "c", "s", ["03 Intro"])
        status = result["03 Intro"]
        assert status.parts_status[0].take_count == 3


class TestScanSectionTakes:
    def test_returns_empty_for_decks_with_no_takes(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)

        result = scan_section_takes(root, "c", "s", ["A", "B"])
        assert result == {"A": {}, "B": {}}

    def test_collects_take_numbers_per_part(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        tk = takes_dir(root) / "c" / "s"
        tk.mkdir(parents=True)
        (tk / "Intro (part 1, take 1).mp4").write_bytes(b"")
        (tk / "Intro (part 1, take 2).mp4").write_bytes(b"")
        (tk / "Intro (part 2, take 1).mp4").write_bytes(b"")
        (tk / "Other (take 1).mp4").write_bytes(b"")

        result = scan_section_takes(root, "c", "s", ["Intro", "Other"])
        assert result["Intro"] == {1: [1, 2], 2: [1]}
        assert result["Other"] == {0: [1]}

    def test_dedupes_raw_and_final_for_same_take(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        tk = takes_dir(root) / "c" / "s"
        tk.mkdir(parents=True)
        # Both raw and final for take 1 — must count once.
        (tk / "Intro (part 1, take 1).mp4").write_bytes(b"")
        (tk / "Intro (part 1, take 1)--RAW.mp4").write_bytes(b"")

        result = scan_section_takes(root, "c", "s", ["Intro"])
        assert result["Intro"] == {1: [1]}


class TestScanTakeFiles:
    def test_pairs_raw_and_final_into_one_take(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        tk = takes_dir(root) / "c" / "s"
        tk.mkdir(parents=True)
        (tk / "Intro (part 2, take 1).mp4").write_bytes(b"final")
        (tk / "Intro (part 2, take 1)--RAW.mp4").write_bytes(b"raw")

        takes = scan_take_files(root, "c", "s", "Intro", part=2)
        assert len(takes) == 1
        assert takes[0].take == 1
        assert takes[0].final_path is not None
        assert takes[0].raw_path is not None

    def test_filters_to_requested_part(self, tmp_path: Path):
        root = tmp_path / "rec"
        ensure_root(root)
        tk = takes_dir(root) / "c" / "s"
        tk.mkdir(parents=True)
        (tk / "Intro (part 1, take 1).mp4").write_bytes(b"")
        (tk / "Intro (part 2, take 1).mp4").write_bytes(b"")

        result = scan_take_files(root, "c", "s", "Intro", part=2)
        assert len(result) == 1
        assert result[0].take == 1
        assert "part 2" in result[0].display_stem
