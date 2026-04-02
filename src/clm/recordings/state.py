"""Per-course recording state manager.

Manages JSON state files that track which recordings belong to which
lectures in a course. Each course has its own state file stored under
the CLM config directory.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import platformdirs
from loguru import logger
from pydantic import BaseModel, Field

RecordingStatus = Literal["pending", "processing", "processed", "failed"]


class RecordingPart(BaseModel):
    """A single recording part for a lecture."""

    part: int
    raw_file: str
    processed_file: str | None = None
    git_commit: str | None = None
    git_dirty: bool = False
    recorded_at: str = ""
    status: RecordingStatus = "pending"


class LectureState(BaseModel):
    """Recording state for a single lecture."""

    lecture_id: str
    display_name: str
    parts: list[RecordingPart] = Field(default_factory=list)

    @property
    def is_recorded(self) -> bool:
        return len(self.parts) > 0

    @property
    def latest_status(self) -> RecordingStatus | None:
        if not self.parts:
            return None
        return self.parts[-1].status


class CourseRecordingState(BaseModel):
    """Complete recording state for a course."""

    course_id: str
    lectures: list[LectureState] = Field(default_factory=list)
    next_lecture_index: int = 0
    continue_current_lecture: bool = False

    def save(self, path: Path) -> None:
        """Save state to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        logger.debug("Saved recording state to {}", path)

    @classmethod
    def load(cls, path: Path) -> CourseRecordingState:
        """Load state from a JSON file."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def get_lecture(self, lecture_id: str) -> LectureState | None:
        """Find a lecture by ID."""
        for lecture in self.lectures:
            if lecture.lecture_id == lecture_id:
                return lecture
        return None

    def get_next_lecture(self) -> LectureState | None:
        """Get the next lecture to record based on next_lecture_index."""
        if self.next_lecture_index < len(self.lectures):
            return self.lectures[self.next_lecture_index]
        return None

    def get_current_lecture(self) -> LectureState | None:
        """Get the current lecture (the one being recorded, i.e. index - 1)."""
        idx = self.next_lecture_index - 1
        if 0 <= idx < len(self.lectures):
            return self.lectures[idx]
        return None

    def assign_recording(
        self,
        raw_file: str,
        *,
        lecture_id: str | None = None,
        git_commit: str | None = None,
        git_dirty: bool = False,
    ) -> tuple[str, int]:
        """Assign a raw recording file to a lecture.

        If lecture_id is given, assigns to that specific lecture.
        Otherwise, uses continue_current_lecture and next_lecture_index
        to determine assignment automatically.

        Returns:
            Tuple of (lecture_id, part_number) for the assignment.

        Raises:
            ValueError: If no lecture is available for assignment.
        """
        if lecture_id:
            lecture = self.get_lecture(lecture_id)
            if not lecture:
                raise ValueError(f"Lecture not found: {lecture_id}")
        elif self.continue_current_lecture:
            lecture = self.get_current_lecture()
            if not lecture:
                raise ValueError("No current lecture to continue")
        else:
            lecture = self.get_next_lecture()
            if not lecture:
                raise ValueError("No more lectures to record")
            # Advance to next lecture for the next recording.
            self.next_lecture_index += 1

        part_number = len(lecture.parts) + 1
        part = RecordingPart(
            part=part_number,
            raw_file=raw_file,
            git_commit=git_commit,
            git_dirty=git_dirty,
            recorded_at=datetime.now().isoformat(timespec="seconds"),
        )
        lecture.parts.append(part)

        logger.info(
            "Assigned {} to {} part {}",
            Path(raw_file).name,
            lecture.lecture_id,
            part_number,
        )
        return lecture.lecture_id, part_number

    def reassign_recording(
        self,
        raw_file: str,
        target_lecture_id: str,
    ) -> tuple[str, int]:
        """Move a recording from its current lecture to a different one.

        Args:
            raw_file: The raw file path to find and move.
            target_lecture_id: The lecture to move the recording to.

        Returns:
            Tuple of (new_lecture_id, new_part_number).

        Raises:
            ValueError: If the recording or target lecture is not found.
        """
        # Find and remove from current lecture.
        source_part: RecordingPart | None = None
        for lecture in self.lectures:
            for part in lecture.parts:
                if part.raw_file == raw_file:
                    source_part = part
                    lecture.parts.remove(part)
                    logger.debug(
                        "Removed {} from {}",
                        Path(raw_file).name,
                        lecture.lecture_id,
                    )
                    break
            if source_part:
                break

        if not source_part:
            raise ValueError(f"Recording not found: {raw_file}")

        # Add to target lecture.
        target = self.get_lecture(target_lecture_id)
        if not target:
            raise ValueError(f"Target lecture not found: {target_lecture_id}")

        new_part_number = len(target.parts) + 1
        source_part.part = new_part_number
        target.parts.append(source_part)

        logger.info(
            "Reassigned {} to {} part {}",
            Path(raw_file).name,
            target_lecture_id,
            new_part_number,
        )
        return target_lecture_id, new_part_number

    def update_recording_status(
        self,
        raw_file: str,
        status: RecordingStatus,
        *,
        processed_file: str | None = None,
    ) -> None:
        """Update the status of a recording part.

        Args:
            raw_file: The raw file path to identify the recording.
            status: New status value.
            processed_file: Path to processed output (set when status='processed').

        Raises:
            ValueError: If the recording is not found.
        """
        for lecture in self.lectures:
            for part in lecture.parts:
                if part.raw_file == raw_file:
                    part.status = status
                    if processed_file is not None:
                        part.processed_file = processed_file
                    logger.debug("Updated {} status to {}", Path(raw_file).name, status)
                    return

        raise ValueError(f"Recording not found: {raw_file}")

    @property
    def progress(self) -> tuple[int, int]:
        """Return (recorded_count, total_count) for progress tracking."""
        total = len(self.lectures)
        recorded = sum(1 for lec in self.lectures if lec.is_recorded)
        return recorded, total


def get_recordings_dir() -> Path:
    """Get the recordings state directory under CLM's config dir."""
    config_dir = Path(platformdirs.user_config_dir("clm", appauthor=False))
    return config_dir / "recordings"


def get_state_path(course_id: str) -> Path:
    """Get the path to a course's recording state file."""
    return get_recordings_dir() / f"{course_id}.json"


def load_state(course_id: str) -> CourseRecordingState | None:
    """Load a course's recording state, or None if it doesn't exist."""
    path = get_state_path(course_id)
    if not path.is_file():
        return None
    return CourseRecordingState.load(path)


def save_state(state: CourseRecordingState) -> Path:
    """Save a course's recording state and return the file path."""
    path = get_state_path(state.course_id)
    state.save(path)
    return path
