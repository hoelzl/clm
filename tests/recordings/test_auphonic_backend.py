"""Tests for :class:`AuphonicBackend` using a fake HTTP client.

The fake client plays back scripted Auphonic responses and records the
requests the backend makes. The tests drive a job through all lifecycle
transitions (QUEUED → UPLOADING → PROCESSING → DOWNLOADING → COMPLETED)
and the failure paths (submit error, poll error, timeout, cancel).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from clm.recordings.workflow.backends.auphonic import (
    AUPHONIC_POLL_TIMEOUT_MINUTES,
    DEFAULT_INLINE_ALGORITHMS,
    AuphonicBackend,
)
from clm.recordings.workflow.backends.auphonic_client import (
    AuphonicError,
    AuphonicOutputFile,
    AuphonicPreset,
    AuphonicProduction,
    AuphonicStatus,
)
from clm.recordings.workflow.backends.base import JobContext
from clm.recordings.workflow.directories import ensure_root, to_process_dir
from clm.recordings.workflow.jobs import (
    JobState,
    ProcessingJob,
    ProcessingOptions,
)

# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


class _RecordingContext:
    """JobContext stub that records every call to :meth:`report`."""

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir
        self.reports: list[ProcessingJob] = []

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    def report(self, job: ProcessingJob) -> None:
        # Record a snapshot so downstream assertions see each transition,
        # not just the final state of the shared mutable job object.
        self.reports.append(job.model_copy(deep=True))


class _FakeAuphonicClient:
    """Scripted Auphonic HTTP client used by backend tests.

    Tracks every method call on ``self.calls`` and cycles through a
    scripted list of responses for ``get_production``. Methods raise if
    the test asks for behaviour that wasn't wired up, so missing stubs
    fail loudly instead of silently returning None.
    """

    def __init__(
        self,
        *,
        production_uuid: str = "prod-1",
        get_responses: list[AuphonicProduction] | None = None,
        download_fn: Callable[[str, Path], None] | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._production_uuid = production_uuid
        self._get_responses = list(get_responses or [])
        self._download_fn = download_fn or (lambda url, dest: dest.write_bytes(b"fake-video"))
        # Track state for optional multi-call scripts.
        self.deleted = False

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.calls.append((name, args, kwargs))

    def create_production(self, **kwargs) -> AuphonicProduction:
        self._record("create_production", (), kwargs)
        return AuphonicProduction(uuid=self._production_uuid, status=AuphonicStatus.INCOMPLETE_FORM)

    def upload_input(self, uuid, file_path, *, on_progress=None):
        self._record("upload_input", (uuid, file_path), {})
        if on_progress is not None:
            on_progress(0.25)
            on_progress(0.75)
            on_progress(1.0)
        return AuphonicProduction(uuid=uuid, status=AuphonicStatus.FILE_UPLOAD)

    def start_production(self, uuid):
        self._record("start_production", (uuid,), {})
        return AuphonicProduction(uuid=uuid, status=AuphonicStatus.AUDIO_PROCESSING)

    def get_production(self, uuid) -> AuphonicProduction:
        self._record("get_production", (uuid,), {})
        if not self._get_responses:
            raise AssertionError("No scripted get_production responses remain")
        return self._get_responses.pop(0)

    def download(self, url, dest, *, on_progress=None):
        self._record("download", (url, dest), {})
        self._download_fn(url, dest)
        if on_progress is not None:
            on_progress(1.0)

    def delete_production(self, uuid):
        self._record("delete_production", (uuid,), {})
        self.deleted = True

    def list_presets(self):
        self._record("list_presets", (), {})
        return []

    def create_preset(self, **kwargs):
        self._record("create_preset", (), kwargs)
        return AuphonicPreset(uuid="new", preset_name=kwargs["preset_data"]["preset_name"])

    def update_preset(self, uuid, **kwargs):
        self._record("update_preset", (uuid,), kwargs)
        return AuphonicPreset(uuid=uuid, preset_name=kwargs["preset_data"]["preset_name"])


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """Initialize the to-process/final/archive tree for a test."""
    root_dir = tmp_path / "recordings"
    ensure_root(root_dir)
    return root_dir


@pytest.fixture()
def raw_file(root: Path) -> Path:
    """Place a pretend raw recording in to-process/."""
    tp = to_process_dir(root) / "python-basics" / "week-01"
    tp.mkdir(parents=True, exist_ok=True)
    video = tp / "topic-one--RAW.mp4"
    video.write_bytes(b"pretend video bytes")
    return video


@pytest.fixture()
def ctx(tmp_path: Path) -> _RecordingContext:
    return _RecordingContext(work_dir=tmp_path / "work")


def _final_path_for(root: Path, raw: Path) -> Path:
    tp = to_process_dir(root)
    rel = raw.parent.relative_to(tp)
    return root / "final" / rel / "topic-one.mp4"


def _make_backend(
    client: _FakeAuphonicClient,
    root: Path,
    **overrides: Any,
) -> AuphonicBackend:
    return AuphonicBackend(
        client=client,  # type: ignore[arg-type]
        root_dir=root,
        **overrides,
    )


# ---------------------------------------------------------------------
# accepts_file / capabilities
# ---------------------------------------------------------------------


class TestAcceptsFile:
    def test_accepts_raw_video(self, root: Path) -> None:
        backend = _make_backend(_FakeAuphonicClient(), root)
        assert backend.accepts_file(Path("topic--RAW.mp4")) is True
        assert backend.accepts_file(Path("topic--RAW.mkv")) is True

    def test_rejects_non_video_or_non_raw(self, root: Path) -> None:
        backend = _make_backend(_FakeAuphonicClient(), root)
        assert backend.accepts_file(Path("topic--RAW.wav")) is False
        assert backend.accepts_file(Path("topic.mp4")) is False
        assert backend.accepts_file(Path("notes.txt")) is False

    def test_honours_custom_raw_suffix(self, root: Path) -> None:
        backend = _make_backend(_FakeAuphonicClient(), root, raw_suffix="--SRC")
        assert backend.accepts_file(Path("topic--SRC.mp4")) is True
        assert backend.accepts_file(Path("topic--RAW.mp4")) is False

    def test_capabilities_declare_async_cloud(self, root: Path) -> None:
        backend = _make_backend(_FakeAuphonicClient(), root)
        caps = backend.capabilities
        assert caps.name == "auphonic"
        assert caps.is_synchronous is False
        assert caps.video_in_video_out is True
        assert caps.requires_internet is True
        assert caps.requires_api_key is True
        assert caps.supports_cut_lists is True


# ---------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------


class TestSubmit:
    def test_happy_path_transitions_to_processing(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root)
        final = _final_path_for(root, raw_file)

        job = backend.submit(raw_file, final, options=ProcessingOptions(), ctx=ctx)

        assert job.state == JobState.PROCESSING
        assert job.backend_ref == "prod-1"
        assert job.backend_name == "auphonic"
        assert job.started_at is not None
        assert job.progress == pytest.approx(0.4)
        # Raw file should still exist — it's archived only on finalize.
        assert raw_file.exists()

        # The method should have called create / upload / start in order.
        call_names = [name for name, _, _ in client.calls]
        assert call_names == ["create_production", "upload_input", "start_production"]

        # Inline algorithms should be sent when no preset is configured.
        create_kwargs = client.calls[0][2]
        assert create_kwargs["algorithms"] == DEFAULT_INLINE_ALGORITHMS
        assert create_kwargs["preset"] is None
        assert create_kwargs["metadata"] == {"title": "topic-one"}
        # One video output, no cut list by default.
        assert len(create_kwargs["output_files"]) == 1

    def test_preset_mode_skips_inline_algorithms(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root, preset="CLM Lecture Recording")
        final = _final_path_for(root, raw_file)

        backend.submit(raw_file, final, options=ProcessingOptions(), ctx=ctx)

        create_kwargs = client.calls[0][2]
        assert create_kwargs["preset"] == "CLM Lecture Recording"
        assert create_kwargs["algorithms"] is None

    def test_cut_list_option_adds_output(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root)
        final = _final_path_for(root, raw_file)

        backend.submit(
            raw_file,
            final,
            options=ProcessingOptions(request_cut_list=True),
            ctx=ctx,
        )

        outputs = client.calls[0][2]["output_files"]
        formats = [o["format"] for o in outputs]
        assert "video" in formats
        assert "cut-list" in formats

    def test_backend_level_cut_list_default(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        """When backend is constructed with request_cut_list_default=True."""
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root, request_cut_list_default=True)
        final = _final_path_for(root, raw_file)

        backend.submit(raw_file, final, options=ProcessingOptions(), ctx=ctx)

        formats = [o["format"] for o in client.calls[0][2]["output_files"]]
        assert "cut-list" in formats

    def test_submit_failure_marks_failed_and_cleans_up(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        class _FlakyClient(_FakeAuphonicClient):
            def upload_input(self, uuid, file_path, *, on_progress=None):
                self._record("upload_input", (uuid, file_path), {})
                raise AuphonicError("upload dropped the connection")

        client = _FlakyClient()
        backend = _make_backend(client, root)
        final = _final_path_for(root, raw_file)

        job = backend.submit(raw_file, final, options=ProcessingOptions(), ctx=ctx)

        assert job.state == JobState.FAILED
        assert "upload" in (job.error or "")
        # We should have attempted to delete the orphan production.
        assert client.deleted

    def test_submit_reports_upload_progress(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root)
        final = _final_path_for(root, raw_file)

        backend.submit(raw_file, final, options=ProcessingOptions(), ctx=ctx)

        # Upload occupies the 0.0 → 0.4 slice. Collect reported progress
        # values from the ctx transcript and verify it reaches 0.4 and
        # never exceeds it during upload.
        uploading = [j.progress for j in ctx.reports if j.state == JobState.UPLOADING]
        assert any(p > 0 for p in uploading)
        assert all(p <= 0.4 + 1e-9 for p in uploading)


# ---------------------------------------------------------------------
# poll
# ---------------------------------------------------------------------


class TestPoll:
    def _make_in_flight_job(self, root: Path, raw: Path) -> ProcessingJob:
        return ProcessingJob(
            backend_name="auphonic",
            raw_path=raw,
            final_path=_final_path_for(root, raw),
            relative_dir=raw.parent.relative_to(to_process_dir(root)),
            state=JobState.PROCESSING,
            progress=0.4,
            backend_ref="prod-1",
            started_at=datetime.now(timezone.utc),
        )

    def test_in_progress_updates_message_and_progress(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        client = _FakeAuphonicClient(
            get_responses=[
                AuphonicProduction(
                    uuid="prod-1",
                    status=AuphonicStatus.AUDIO_PROCESSING,
                    status_string="Audio Processing",
                )
            ]
        )
        backend = _make_backend(client, root)
        job = self._make_in_flight_job(root, raw_file)

        updated = backend.poll(job, ctx=ctx)

        assert updated.state == JobState.PROCESSING
        assert "Audio Processing" in updated.message
        assert updated.progress >= 0.4

    def test_done_downloads_and_completes(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        done = AuphonicProduction(
            uuid="prod-1",
            status=AuphonicStatus.DONE,
            output_files=[
                AuphonicOutputFile(
                    format="video",
                    ending="mp4",
                    download_url="https://cdn/prod-1.mp4",
                )
            ],
        )
        client = _FakeAuphonicClient(get_responses=[done])
        backend = _make_backend(client, root)
        job = self._make_in_flight_job(root, raw_file)

        updated = backend.poll(job, ctx=ctx)

        assert updated.state == JobState.COMPLETED
        assert updated.progress == pytest.approx(1.0)
        assert updated.final_path.exists(), "Final file should be downloaded"
        assert updated.final_path.read_bytes() == b"fake-video"
        # Raw file is archived off the to-process tree.
        assert not raw_file.exists()
        archive_file = (
            root / "archive" / raw_file.parent.relative_to(to_process_dir(root)) / raw_file.name
        )
        assert archive_file.exists()

    def test_done_with_cut_list_downloads_both(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        def _write(url: str, dest: Path) -> None:
            dest.write_bytes(b"video" if dest.suffix == ".mp4" else b"cuts")

        done = AuphonicProduction(
            uuid="prod-1",
            status=AuphonicStatus.DONE,
            output_files=[
                AuphonicOutputFile(format="video", ending="mp4", download_url="https://cdn/v.mp4"),
                AuphonicOutputFile(
                    format="cut-list",
                    ending="DaVinciResolve.edl",
                    download_url="https://cdn/v.edl",
                ),
            ],
        )
        client = _FakeAuphonicClient(get_responses=[done], download_fn=_write)
        backend = _make_backend(client, root)
        job = self._make_in_flight_job(root, raw_file)

        updated = backend.poll(job, ctx=ctx)

        assert updated.state == JobState.COMPLETED
        assert "cut_list" in updated.artifacts
        assert updated.artifacts["cut_list"].suffix == ".edl"
        assert updated.artifacts["cut_list"].read_bytes() == b"cuts"

    def test_error_status_marks_failed(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        err = AuphonicProduction(
            uuid="prod-1",
            status=AuphonicStatus.ERROR,
            error_message="Input file corrupt",
        )
        client = _FakeAuphonicClient(get_responses=[err])
        backend = _make_backend(client, root)
        job = self._make_in_flight_job(root, raw_file)

        updated = backend.poll(job, ctx=ctx)

        assert updated.state == JobState.FAILED
        assert "corrupt" in (updated.error or "")

    def test_poll_error_does_not_fail_job(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        class _TransientClient(_FakeAuphonicClient):
            def get_production(self, uuid):
                self._record("get_production", (uuid,), {})
                raise AuphonicError("temporary network blip")

        client = _TransientClient()
        backend = _make_backend(client, root)
        job = self._make_in_flight_job(root, raw_file)

        updated = backend.poll(job, ctx=ctx)

        # Transient errors should not fail the job — the next poll will
        # retry. The job stays in its current state with a diagnostic
        # message.
        assert updated.state == JobState.PROCESSING
        assert "network blip" in updated.message

    def test_timeout_fails_job(self, root: Path, raw_file: Path, ctx: _RecordingContext) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root, poll_timeout_minutes=1)
        job = self._make_in_flight_job(root, raw_file)
        # Pretend the job started 2 minutes ago.
        job.started_at = datetime.now(timezone.utc) - timedelta(minutes=2)

        updated = backend.poll(job, ctx=ctx)

        assert updated.state == JobState.FAILED
        assert "timed out" in (updated.error or "")
        # Poll must short-circuit before talking to Auphonic on timeout.
        assert not any(name == "get_production" for name, _, _ in client.calls)

    def test_missing_backend_ref_fails_immediately(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root)
        job = self._make_in_flight_job(root, raw_file)
        job.backend_ref = None

        updated = backend.poll(job, ctx=ctx)

        assert updated.state == JobState.FAILED
        assert "production reference" in (updated.error or "")

    def test_terminal_job_is_returned_untouched(
        self, root: Path, raw_file: Path, ctx: _RecordingContext
    ) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root)
        job = self._make_in_flight_job(root, raw_file)
        job.state = JobState.COMPLETED

        updated = backend.poll(job, ctx=ctx)

        assert updated is job
        # Terminal jobs are never polled on the server.
        assert not client.calls


# ---------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------


class TestCancel:
    def test_cancel_deletes_production(self, root: Path, raw_file: Path, ctx) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root)
        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw_file,
            final_path=_final_path_for(root, raw_file),
            relative_dir=Path(),
            backend_ref="prod-9",
        )

        backend.cancel(job, ctx=ctx)

        assert client.deleted

    def test_cancel_without_backend_ref_is_noop(self, root: Path, raw_file: Path, ctx) -> None:
        client = _FakeAuphonicClient()
        backend = _make_backend(client, root)
        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw_file,
            final_path=_final_path_for(root, raw_file),
            relative_dir=Path(),
        )

        backend.cancel(job, ctx=ctx)

        assert not client.deleted
        assert not client.calls
