"""Tests for the recordings job domain types."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clm.recordings.workflow.jobs import (
    TERMINAL_STATES,
    BackendCapabilities,
    JobState,
    ProcessingJob,
    ProcessingOptions,
)


class TestJobState:
    def test_values_are_lowercase_strings(self):
        assert JobState.QUEUED.value == "queued"
        assert JobState.UPLOADING.value == "uploading"
        assert JobState.COMPLETED.value == "completed"

    def test_terminal_states_complete_set(self):
        assert TERMINAL_STATES == frozenset(
            {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
        )


class TestProcessingOptions:
    def test_defaults_are_conservative(self):
        opts = ProcessingOptions()
        assert opts.request_cut_list is False
        assert opts.apply_cuts is False
        assert opts.custom_preset is None
        assert opts.title is None
        assert opts.extra == {}

    def test_round_trip_via_model_dump(self):
        opts = ProcessingOptions(
            request_cut_list=True,
            title="My Lecture",
            extra={"key": "value"},
        )
        copy = ProcessingOptions.model_validate(opts.model_dump())
        assert copy == opts


class TestBackendCapabilities:
    def test_required_fields(self):
        caps = BackendCapabilities(name="onnx", display_name="Local")
        assert caps.name == "onnx"
        assert caps.display_name == "Local"
        assert caps.is_synchronous is True
        assert caps.video_in_video_out is False
        assert caps.supports_cut_lists is False

    def test_default_extensions_are_video(self):
        caps = BackendCapabilities(name="x", display_name="x")
        assert ".mp4" in caps.supported_input_extensions
        assert ".mkv" in caps.supported_input_extensions


class TestProcessingJob:
    def test_defaults(self, tmp_path: Path):
        raw = tmp_path / "lecture--RAW.mp4"
        final = tmp_path / "final" / "lecture.mp4"

        job = ProcessingJob(
            backend_name="onnx",
            raw_path=raw,
            final_path=final,
            relative_dir=Path("py/week01"),
        )

        assert job.state == JobState.QUEUED
        assert job.progress == 0.0
        assert job.message == ""
        assert job.error is None
        assert job.artifacts == {}
        assert job.backend_ref is None
        assert job.id  # auto-generated
        assert job.is_terminal is False

    def test_id_is_unique_per_instance(self, tmp_path: Path):
        a = ProcessingJob(
            backend_name="x",
            raw_path=tmp_path / "a.mp4",
            final_path=tmp_path / "a.mp4",
            relative_dir=Path(),
        )
        b = ProcessingJob(
            backend_name="x",
            raw_path=tmp_path / "b.mp4",
            final_path=tmp_path / "b.mp4",
            relative_dir=Path(),
        )
        assert a.id != b.id

    def test_timestamps_default_to_utc_now(self, tmp_path: Path):
        before = datetime.now(timezone.utc)
        job = ProcessingJob(
            backend_name="x",
            raw_path=tmp_path / "a.mp4",
            final_path=tmp_path / "a.mp4",
            relative_dir=Path(),
        )
        after = datetime.now(timezone.utc)

        assert job.created_at.tzinfo is not None
        assert before <= job.created_at <= after
        assert before <= job.updated_at <= after
        assert job.started_at is None
        assert job.completed_at is None

    @pytest.mark.parametrize(
        "state, expected",
        [
            (JobState.QUEUED, False),
            (JobState.UPLOADING, False),
            (JobState.PROCESSING, False),
            (JobState.DOWNLOADING, False),
            (JobState.ASSEMBLING, False),
            (JobState.COMPLETED, True),
            (JobState.FAILED, True),
            (JobState.CANCELLED, True),
        ],
    )
    def test_is_terminal(self, state: JobState, expected: bool, tmp_path: Path):
        job = ProcessingJob(
            backend_name="x",
            raw_path=tmp_path / "a.mp4",
            final_path=tmp_path / "a.mp4",
            relative_dir=Path(),
            state=state,
        )
        assert job.is_terminal is expected

    def test_touch_updates_updated_at(self, tmp_path: Path):
        job = ProcessingJob(
            backend_name="x",
            raw_path=tmp_path / "a.mp4",
            final_path=tmp_path / "a.mp4",
            relative_dir=Path(),
        )
        first = job.updated_at
        # Sleep briefly to guarantee a distinct timestamp on fast systems.
        time.sleep(0.001)
        job.touch()
        assert job.updated_at > first

    def test_model_dump_and_validate_round_trip(self, tmp_path: Path):
        raw = tmp_path / "lecture--RAW.mp4"
        final = tmp_path / "final" / "lecture.mp4"
        cut_list = tmp_path / "final" / "lecture.edl"

        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw,
            final_path=final,
            relative_dir=Path("py/week01"),
            state=JobState.PROCESSING,
            progress=0.4,
            message="Processing on Auphonic",
            artifacts={"cut_list": cut_list},
            backend_ref="abc-123",
        )

        dumped = job.model_dump(mode="json")
        restored = ProcessingJob.model_validate(dumped)

        assert restored.id == job.id
        assert restored.backend_name == "auphonic"
        assert restored.raw_path == raw
        assert restored.final_path == final
        assert restored.state == JobState.PROCESSING
        assert restored.artifacts == {"cut_list": cut_list}
        assert restored.backend_ref == "abc-123"
