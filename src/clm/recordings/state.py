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


class TakeRecord(BaseModel):
    """A superseded take of a recording part.

    Held on :class:`RecordingPart.takes` to preserve history when a
    user retakes a part. The active fields on ``RecordingPart`` always
    describe the current best take; older takes are demoted into this
    list along with their filesystem paths (typically under ``takes/``).
    """

    take: int
    raw_file: str
    processed_file: str | None = None
    git_commit: str | None = None
    git_dirty: bool = False
    recorded_at: str = ""
    status: RecordingStatus = "pending"
    superseded_at: str | None = None


class RecordingPart(BaseModel):
    """A single recording part for a lecture.

    The unadorned fields (``raw_file``, ``processed_file``, …) describe
    the *active* take. Superseded takes are kept on :attr:`takes`; the
    active take's number is tracked by :attr:`active_take` so superseded
    files can reason about their history without scanning disk.
    """

    part: int
    raw_file: str
    processed_file: str | None = None
    git_commit: str | None = None
    git_dirty: bool = False
    recorded_at: str = ""
    status: RecordingStatus = "pending"
    takes: list[TakeRecord] = Field(default_factory=list)
    active_take: int = 1


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

    def ensure_lecture(self, lecture_id: str, display_name: str) -> LectureState:
        """Return the lecture for *lecture_id*, creating it if absent.

        The web flow calls this the first time a deck is armed so the
        course-level state.json always has a row to hang parts/takes off.
        The ``display_name`` is only used when creating a fresh lecture;
        an existing lecture's name is not clobbered.
        """
        lecture = self.get_lecture(lecture_id)
        if lecture is None:
            lecture = LectureState(lecture_id=lecture_id, display_name=display_name)
            self.lectures.append(lecture)
            logger.info("Registered lecture {} ({}).", lecture_id, display_name)
        return lecture

    def ensure_part(
        self,
        lecture_id: str,
        part_number: int,
        raw_file: str,
        *,
        display_name: str | None = None,
        git_commit: str | None = None,
        git_dirty: bool = False,
        processed_file: str | None = None,
    ) -> RecordingPart:
        """Return the part for *(lecture_id, part_number)*, creating it if absent.

        Idempotent: if the part already exists, its ``raw_file`` is
        updated to the new *raw_file* (the caller has just moved the
        active take into ``takes/`` and placed a fresh file in its
        slot). If the lecture does not yet exist, it is created with
        *display_name* (or ``lecture_id`` when no display name is
        given).
        """
        lecture = self.get_lecture(lecture_id)
        if lecture is None:
            lecture = self.ensure_lecture(lecture_id, display_name or lecture_id)

        for part in lecture.parts:
            if part.part == part_number:
                part.raw_file = raw_file
                part.processed_file = processed_file
                part.git_commit = git_commit
                part.git_dirty = git_dirty
                part.recorded_at = datetime.now().isoformat(timespec="seconds")
                part.status = "pending"
                return part

        part = RecordingPart(
            part=part_number,
            raw_file=raw_file,
            processed_file=processed_file,
            git_commit=git_commit,
            git_dirty=git_dirty,
            recorded_at=datetime.now().isoformat(timespec="seconds"),
        )
        lecture.parts.append(part)
        lecture.parts.sort(key=lambda p: p.part)
        logger.info("Registered {} part {} ({}).", lecture_id, part_number, Path(raw_file).name)
        return part

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

    def _find_part(self, lecture_id: str, part_number: int) -> RecordingPart:
        """Return the ``RecordingPart`` for *lecture_id* / *part_number*.

        Raises ``ValueError`` if either the lecture or the part is absent.
        """
        lecture = self.get_lecture(lecture_id)
        if lecture is None:
            raise ValueError(f"Lecture not found: {lecture_id}")
        for part in lecture.parts:
            if part.part == part_number:
                return part
        raise ValueError(f"Part {part_number} not found in lecture {lecture_id}")

    def record_retake(
        self,
        lecture_id: str,
        part_number: int,
        new_raw_file: str,
        *,
        git_commit: str | None = None,
        git_dirty: bool = False,
        new_processed_file: str | None = None,
    ) -> TakeRecord:
        """Demote the part's current active take into ``takes`` and install a new one.

        The caller is responsible for the corresponding filesystem moves
        (typically into ``takes/``). The returned :class:`TakeRecord`
        describes the take that was just demoted — useful for the caller
        to know the old paths.

        Raises:
            ValueError: If the lecture or part does not exist.
        """
        part = self._find_part(lecture_id, part_number)

        demoted = TakeRecord(
            take=part.active_take,
            raw_file=part.raw_file,
            processed_file=part.processed_file,
            git_commit=part.git_commit,
            git_dirty=part.git_dirty,
            recorded_at=part.recorded_at,
            status=part.status,
            superseded_at=datetime.now().isoformat(timespec="seconds"),
        )
        part.takes.append(demoted)

        part.active_take = demoted.take + 1
        part.raw_file = new_raw_file
        part.processed_file = new_processed_file
        part.git_commit = git_commit
        part.git_dirty = git_dirty
        part.recorded_at = datetime.now().isoformat(timespec="seconds")
        part.status = "pending"

        logger.info(
            "Recorded retake {} for {} part {}",
            part.active_take,
            lecture_id,
            part_number,
        )
        return demoted

    def restore_take(self, lecture_id: str, part_number: int, take: int) -> None:
        """Swap take *take* with the current active take.

        The previous active take becomes a :class:`TakeRecord` entry,
        and the requested historical take is promoted to active. Always
        a swap — the caller never loses data. The caller is responsible
        for moving the corresponding files on disk.

        Raises:
            ValueError: If the lecture, part, or take does not exist,
                or if *take* is already the active one.
        """
        part = self._find_part(lecture_id, part_number)

        if take == part.active_take:
            raise ValueError(f"Take {take} is already active for part {part_number}")

        target: TakeRecord | None = None
        for existing in part.takes:
            if existing.take == take:
                target = existing
                break
        if target is None:
            raise ValueError(f"Take {take} not found in part {part_number} of lecture {lecture_id}")

        demoted = TakeRecord(
            take=part.active_take,
            raw_file=part.raw_file,
            processed_file=part.processed_file,
            git_commit=part.git_commit,
            git_dirty=part.git_dirty,
            recorded_at=part.recorded_at,
            status=part.status,
            superseded_at=datetime.now().isoformat(timespec="seconds"),
        )

        part.takes.remove(target)
        part.takes.append(demoted)
        part.takes.sort(key=lambda t: t.take)

        part.active_take = target.take
        part.raw_file = target.raw_file
        part.processed_file = target.processed_file
        part.git_commit = target.git_commit
        part.git_dirty = target.git_dirty
        part.recorded_at = target.recorded_at
        part.status = target.status

        logger.info(
            "Restored take {} for {} part {}",
            take,
            lecture_id,
            part_number,
        )

    def rename_recording_paths(
        self,
        old_raw: str,
        new_raw: str,
        *,
        old_processed: str | None = None,
        new_processed: str | None = None,
    ) -> None:
        """Update ``raw_file`` / ``processed_file`` references after a filesystem rename.

        Scans every lecture, part, and take. No-op if neither ``old_raw``
        nor ``old_processed`` matches any tracked path — a cascade may
        have acted on files not yet assigned to the state.json model.
        """
        for lecture in self.lectures:
            for part in lecture.parts:
                if part.raw_file == old_raw:
                    part.raw_file = new_raw
                if old_processed is not None and part.processed_file == old_processed:
                    part.processed_file = new_processed
                for take in part.takes:
                    if take.raw_file == old_raw:
                        take.raw_file = new_raw
                    if old_processed is not None and take.processed_file == old_processed:
                        take.processed_file = new_processed

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


def load_or_create(course_id: str) -> CourseRecordingState:
    """Load a course's recording state, creating a fresh empty one if absent.

    Used by the web dashboard to lazily instantiate a state file the
    first time a course is armed. The new state is **not** persisted
    until a mutation triggers :func:`save_state` — that avoids leaving
    empty sentinel files for courses the user merely browsed to.
    """
    existing = load_state(course_id)
    if existing is not None:
        return existing
    return CourseRecordingState(course_id=course_id)


def save_state(state: CourseRecordingState) -> Path:
    """Save a course's recording state and return the file path."""
    path = get_state_path(state.course_id)
    state.save(path)
    return path
