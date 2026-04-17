"""Tests for the recording state manager."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.recordings.state import (
    CourseRecordingState,
    LectureState,
    RecordingPart,
    TakeRecord,
)


@pytest.fixture
def sample_state() -> CourseRecordingState:
    """A sample course recording state with 3 lectures."""
    return CourseRecordingState(
        course_id="test-course",
        lectures=[
            LectureState(lecture_id="010-intro", display_name="Introduction"),
            LectureState(lecture_id="020-variables", display_name="Variables"),
            LectureState(lecture_id="030-functions", display_name="Functions"),
        ],
        next_lecture_index=0,
    )


class TestCourseRecordingState:
    def test_get_lecture(self, sample_state: CourseRecordingState):
        lecture = sample_state.get_lecture("020-variables")
        assert lecture is not None
        assert lecture.display_name == "Variables"

    def test_get_lecture_not_found(self, sample_state: CourseRecordingState):
        assert sample_state.get_lecture("999-missing") is None

    def test_get_next_lecture(self, sample_state: CourseRecordingState):
        lecture = sample_state.get_next_lecture()
        assert lecture is not None
        assert lecture.lecture_id == "010-intro"

    def test_get_next_lecture_past_end(self, sample_state: CourseRecordingState):
        sample_state.next_lecture_index = 3
        assert sample_state.get_next_lecture() is None

    def test_progress(self, sample_state: CourseRecordingState):
        recorded, total = sample_state.progress
        assert recorded == 0
        assert total == 3


class TestAssignRecording:
    def test_assign_to_next_lecture(self, sample_state: CourseRecordingState):
        lecture_id, part = sample_state.assign_recording("/obs/rec1.mkv")
        assert lecture_id == "010-intro"
        assert part == 1
        assert sample_state.next_lecture_index == 1

    def test_assign_advances_index(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv")
        sample_state.assign_recording("/obs/rec2.mkv")

        assert sample_state.next_lecture_index == 2
        assert sample_state.lectures[0].parts[0].raw_file == "/obs/rec1.mkv"
        assert sample_state.lectures[1].parts[0].raw_file == "/obs/rec2.mkv"

    def test_assign_to_specific_lecture(self, sample_state: CourseRecordingState):
        lecture_id, part = sample_state.assign_recording(
            "/obs/rec1.mkv", lecture_id="030-functions"
        )
        assert lecture_id == "030-functions"
        assert part == 1
        # next_lecture_index should NOT advance when assigning to specific lecture
        assert sample_state.next_lecture_index == 0

    def test_assign_continue_current_lecture(self, sample_state: CourseRecordingState):
        # First, assign one recording to advance past the first lecture
        sample_state.assign_recording("/obs/rec1.mkv")
        assert sample_state.next_lecture_index == 1

        # Enable continue mode
        sample_state.continue_current_lecture = True

        # Next assignment should go to the current (first) lecture as part 2
        lecture_id, part = sample_state.assign_recording("/obs/rec2.mkv")
        assert lecture_id == "010-intro"
        assert part == 2

    def test_assign_captures_git_info(self, sample_state: CourseRecordingState):
        sample_state.assign_recording(
            "/obs/rec1.mkv",
            git_commit="abc123",
            git_dirty=True,
        )
        recording = sample_state.lectures[0].parts[0]
        assert recording.git_commit == "abc123"
        assert recording.git_dirty is True

    def test_assign_sets_recorded_at(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv")
        recording = sample_state.lectures[0].parts[0]
        assert recording.recorded_at != ""

    def test_assign_no_more_lectures(self, sample_state: CourseRecordingState):
        sample_state.next_lecture_index = 3
        with pytest.raises(ValueError, match="No more lectures"):
            sample_state.assign_recording("/obs/rec1.mkv")

    def test_assign_to_unknown_lecture(self, sample_state: CourseRecordingState):
        with pytest.raises(ValueError, match="Lecture not found"):
            sample_state.assign_recording("/obs/rec1.mkv", lecture_id="999-missing")


class TestReassignRecording:
    def test_reassign(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv")
        assert sample_state.lectures[0].is_recorded

        lecture_id, part = sample_state.reassign_recording("/obs/rec1.mkv", "030-functions")
        assert lecture_id == "030-functions"
        assert part == 1
        # Source lecture should now be empty
        assert not sample_state.lectures[0].is_recorded
        # Target lecture should have the recording
        assert sample_state.lectures[2].is_recorded
        assert sample_state.lectures[2].parts[0].raw_file == "/obs/rec1.mkv"

    def test_reassign_not_found(self, sample_state: CourseRecordingState):
        with pytest.raises(ValueError, match="Recording not found"):
            sample_state.reassign_recording("/obs/nonexistent.mkv", "010-intro")

    def test_reassign_target_not_found(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv")
        with pytest.raises(ValueError, match="Target lecture not found"):
            sample_state.reassign_recording("/obs/rec1.mkv", "999-missing")


class TestUpdateRecordingStatus:
    def test_update_status(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv")
        sample_state.update_recording_status("/obs/rec1.mkv", "processing")
        assert sample_state.lectures[0].parts[0].status == "processing"

    def test_update_status_with_processed_file(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv")
        sample_state.update_recording_status(
            "/obs/rec1.mkv",
            "processed",
            processed_file="/out/010-intro_part1.mp4",
        )
        part = sample_state.lectures[0].parts[0]
        assert part.status == "processed"
        assert part.processed_file == "/out/010-intro_part1.mp4"

    def test_update_status_not_found(self, sample_state: CourseRecordingState):
        with pytest.raises(ValueError, match="Recording not found"):
            sample_state.update_recording_status("/obs/nonexistent.mkv", "processed")


class TestStatePersistence:
    def test_save_and_load(self, tmp_path: Path, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv", git_commit="abc123")

        path = tmp_path / "test-course.json"
        sample_state.save(path)

        loaded = CourseRecordingState.load(path)
        assert loaded.course_id == "test-course"
        assert len(loaded.lectures) == 3
        assert loaded.lectures[0].parts[0].raw_file == "/obs/rec1.mkv"
        assert loaded.lectures[0].parts[0].git_commit == "abc123"
        assert loaded.next_lecture_index == 1

    def test_save_creates_directories(self, tmp_path: Path, sample_state: CourseRecordingState):
        path = tmp_path / "sub" / "dir" / "state.json"
        sample_state.save(path)
        assert path.is_file()


class TestLectureState:
    def test_is_recorded(self):
        lecture = LectureState(lecture_id="010-intro", display_name="Intro")
        assert not lecture.is_recorded

        lecture.parts.append(RecordingPart(part=1, raw_file="/obs/rec1.mkv"))
        assert lecture.is_recorded

    def test_latest_status(self):
        lecture = LectureState(lecture_id="010-intro", display_name="Intro")
        assert lecture.latest_status is None

        lecture.parts.append(RecordingPart(part=1, raw_file="/obs/rec1.mkv", status="pending"))
        assert lecture.latest_status == "pending"

        lecture.parts.append(RecordingPart(part=2, raw_file="/obs/rec2.mkv", status="processed"))
        assert lecture.latest_status == "processed"


class TestTakeSchemaDefaults:
    def test_new_part_has_empty_takes_and_active_1(self):
        part = RecordingPart(part=1, raw_file="/obs/rec1.mkv")
        assert part.takes == []
        assert part.active_take == 1

    def test_state_json_without_takes_field_loads(self, tmp_path: Path):
        """Old schema state.json must still load (backcompat)."""
        path = tmp_path / "old.json"
        path.write_text(
            """
            {
                "course_id": "legacy",
                "lectures": [
                    {
                        "lecture_id": "010-intro",
                        "display_name": "Intro",
                        "parts": [
                            {
                                "part": 1,
                                "raw_file": "/obs/rec1.mkv",
                                "processed_file": "/out/rec1.mp4",
                                "status": "processed"
                            }
                        ]
                    }
                ]
            }
            """,
            encoding="utf-8",
        )
        loaded = CourseRecordingState.load(path)
        part = loaded.lectures[0].parts[0]
        assert part.takes == []
        assert part.active_take == 1


class TestRecordRetake:
    def test_demotes_active_to_takes(self, sample_state: CourseRecordingState):
        sample_state.assign_recording(
            "/obs/rec1.mkv", lecture_id="010-intro", git_commit="old-commit"
        )
        sample_state.update_recording_status(
            "/obs/rec1.mkv", "processed", processed_file="/final/rec1.mp4"
        )

        demoted = sample_state.record_retake(
            "010-intro",
            1,
            "/obs/rec1-take2.mkv",
            git_commit="new-commit",
            git_dirty=True,
        )

        assert demoted.take == 1
        assert demoted.raw_file == "/obs/rec1.mkv"
        assert demoted.processed_file == "/final/rec1.mp4"
        assert demoted.status == "processed"
        assert demoted.superseded_at != ""

        part = sample_state.lectures[0].parts[0]
        assert len(part.takes) == 1
        assert part.takes[0].take == 1
        assert part.active_take == 2
        assert part.raw_file == "/obs/rec1-take2.mkv"
        assert part.processed_file is None
        assert part.git_commit == "new-commit"
        assert part.git_dirty is True
        assert part.status == "pending"

    def test_multiple_retakes_append_in_order(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv", lecture_id="010-intro")
        sample_state.record_retake("010-intro", 1, "/obs/rec1-t2.mkv")
        sample_state.record_retake("010-intro", 1, "/obs/rec1-t3.mkv")

        part = sample_state.lectures[0].parts[0]
        assert [t.take for t in part.takes] == [1, 2]
        assert part.active_take == 3
        assert part.raw_file == "/obs/rec1-t3.mkv"

    def test_rejects_unknown_lecture(self, sample_state: CourseRecordingState):
        with pytest.raises(ValueError, match="Lecture not found"):
            sample_state.record_retake("999-missing", 1, "/obs/x.mkv")

    def test_rejects_unknown_part(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv", lecture_id="010-intro")
        with pytest.raises(ValueError, match="Part 99 not found"):
            sample_state.record_retake("010-intro", 99, "/obs/x.mkv")


class TestRestoreTake:
    def test_swaps_active_and_history(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv", lecture_id="010-intro")
        sample_state.update_recording_status(
            "/obs/rec1.mkv", "processed", processed_file="/final/rec1-t1.mp4"
        )
        sample_state.record_retake("010-intro", 1, "/obs/rec1-t2.mkv")
        sample_state.update_recording_status(
            "/obs/rec1-t2.mkv", "processed", processed_file="/final/rec1-t2.mp4"
        )

        sample_state.restore_take("010-intro", 1, 1)

        part = sample_state.lectures[0].parts[0]
        assert part.active_take == 1
        assert part.raw_file == "/obs/rec1.mkv"
        assert part.processed_file == "/final/rec1-t1.mp4"
        # The previously-active take 2 now lives in history.
        assert len(part.takes) == 1
        assert part.takes[0].take == 2
        assert part.takes[0].raw_file == "/obs/rec1-t2.mkv"

    def test_rejects_already_active_take(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv", lecture_id="010-intro")
        with pytest.raises(ValueError, match="already active"):
            sample_state.restore_take("010-intro", 1, 1)

    def test_rejects_unknown_take(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec1.mkv", lecture_id="010-intro")
        with pytest.raises(ValueError, match="Take 99 not found"):
            sample_state.restore_take("010-intro", 1, 99)


class TestRenameRecordingPaths:
    def test_renames_active_raw(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/old.mkv", lecture_id="010-intro")
        sample_state.rename_recording_paths("/obs/old.mkv", "/obs/new.mkv")

        assert sample_state.lectures[0].parts[0].raw_file == "/obs/new.mkv"

    def test_renames_processed_path(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec.mkv", lecture_id="010-intro")
        sample_state.update_recording_status(
            "/obs/rec.mkv", "processed", processed_file="/final/old.mp4"
        )
        sample_state.rename_recording_paths(
            "/obs/rec.mkv",
            "/obs/rec.mkv",
            old_processed="/final/old.mp4",
            new_processed="/final/new.mp4",
        )

        part = sample_state.lectures[0].parts[0]
        assert part.processed_file == "/final/new.mp4"

    def test_renames_inside_takes(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec.mkv", lecture_id="010-intro")
        sample_state.update_recording_status(
            "/obs/rec.mkv", "processed", processed_file="/final/t1.mp4"
        )
        sample_state.record_retake("010-intro", 1, "/obs/rec-t2.mkv")

        sample_state.rename_recording_paths(
            "/obs/rec.mkv",
            "/takes/rec (take 1).mkv",
            old_processed="/final/t1.mp4",
            new_processed="/takes/t1 (take 1).mp4",
        )

        take = sample_state.lectures[0].parts[0].takes[0]
        assert take.raw_file == "/takes/rec (take 1).mkv"
        assert take.processed_file == "/takes/t1 (take 1).mp4"

    def test_noop_when_old_path_absent(self, sample_state: CourseRecordingState):
        sample_state.assign_recording("/obs/rec.mkv", lecture_id="010-intro")
        sample_state.rename_recording_paths("/obs/nothing.mkv", "/obs/elsewhere.mkv")

        # Unchanged — no match found anywhere.
        assert sample_state.lectures[0].parts[0].raw_file == "/obs/rec.mkv"


class TestTakeRecordModel:
    def test_round_trip_serialization(self, tmp_path: Path):
        state = CourseRecordingState(
            course_id="serial",
            lectures=[
                LectureState(
                    lecture_id="010",
                    display_name="A",
                    parts=[
                        RecordingPart(
                            part=1,
                            raw_file="/raw.mkv",
                            active_take=2,
                            takes=[
                                TakeRecord(
                                    take=1,
                                    raw_file="/takes/t1.mkv",
                                    processed_file="/takes/t1.mp4",
                                    status="processed",
                                    superseded_at="2026-04-17T10:00:00",
                                ),
                            ],
                        )
                    ],
                )
            ],
        )
        path = tmp_path / "s.json"
        state.save(path)
        loaded = CourseRecordingState.load(path)
        part = loaded.lectures[0].parts[0]
        assert part.active_take == 2
        assert len(part.takes) == 1
        assert part.takes[0].processed_file == "/takes/t1.mp4"
