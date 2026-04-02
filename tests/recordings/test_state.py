"""Tests for the recording state manager."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.recordings.state import (
    CourseRecordingState,
    LectureState,
    RecordingPart,
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
