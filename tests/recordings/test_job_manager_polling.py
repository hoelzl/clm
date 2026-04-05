"""End-to-end polling tests: :class:`JobManager` + :class:`AuphonicBackend`.

These complement ``test_job_manager.py`` (which covers the manager with
minimal stub backends) by verifying the manager correctly drives a real
:class:`AuphonicBackend` instance through multiple PROCESSING polls until
completion or failure. A fake :class:`AuphonicClient` plays back scripted
API responses so no network activity occurs.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from clm.recordings.workflow.backends.auphonic import AuphonicBackend
from clm.recordings.workflow.backends.auphonic_client import (
    AuphonicOutputFile,
    AuphonicProduction,
    AuphonicStatus,
)
from clm.recordings.workflow.directories import ensure_root, to_process_dir
from clm.recordings.workflow.event_bus import EventBus
from clm.recordings.workflow.job_manager import JOB_EVENT_TOPIC, JobManager
from clm.recordings.workflow.job_store import JsonFileJobStore
from clm.recordings.workflow.jobs import JobState, ProcessingJob

# ---------------------------------------------------------------------
# Scripted Auphonic client
# ---------------------------------------------------------------------


class _ScriptedAuphonicClient:
    """AuphonicClient stand-in that cycles through a list of responses."""

    def __init__(
        self,
        *,
        get_responses: list[AuphonicProduction],
        download_fn: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._get_responses = list(get_responses)
        self._download_fn = download_fn or (lambda url, dest: dest.write_bytes(b"final"))
        self.calls: list[str] = []

    def create_production(self, **kwargs) -> AuphonicProduction:
        self.calls.append("create_production")
        return AuphonicProduction(uuid="prod-42", status=AuphonicStatus.INCOMPLETE_FORM)

    def upload_input(self, uuid, file_path, *, on_progress=None):
        self.calls.append("upload_input")
        if on_progress is not None:
            on_progress(0.5)
            on_progress(1.0)
        return AuphonicProduction(uuid=uuid, status=AuphonicStatus.FILE_UPLOAD)

    def start_production(self, uuid):
        self.calls.append("start_production")
        return AuphonicProduction(uuid=uuid, status=AuphonicStatus.AUDIO_PROCESSING)

    def get_production(self, uuid):
        self.calls.append("get_production")
        if not self._get_responses:
            # If we ran out of scripted responses, return the last known
            # state. Better than raising and silently hanging tests.
            return AuphonicProduction(uuid=uuid, status=AuphonicStatus.AUDIO_PROCESSING)
        return self._get_responses.pop(0)

    def download(self, url, dest, *, on_progress=None):
        self.calls.append("download")
        self._download_fn(url, dest)
        if on_progress is not None:
            on_progress(1.0)

    def delete_production(self, uuid):
        self.calls.append("delete_production")


# ---------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------


def _make_raw_file(root: Path) -> Path:
    tp = to_process_dir(root) / "course" / "week01"
    tp.mkdir(parents=True, exist_ok=True)
    raw = tp / "topic--RAW.mp4"
    raw.write_bytes(b"video bytes")
    return raw


def _wait_for_state(
    manager: JobManager,
    bus: EventBus,
    job_id: str,
    target: JobState,
    *,
    timeout: float = 10.0,
) -> threading.Event:
    """Wait for *job_id* to reach *target* via the bus.

    Mirrors the helper in ``test_job_manager.py`` — event-based waiting
    is deterministic on Windows CI runners where ``time.sleep`` polling
    loops are flaky.
    """
    reached = threading.Event()

    def on_event(topic: str, payload: object) -> None:
        if isinstance(payload, ProcessingJob) and payload.id == job_id and payload.state == target:
            reached.set()

    bus.subscribe(on_event)
    current = manager.get(job_id)
    if current is not None and current.state == target:
        reached.set()
    reached.wait(timeout=timeout)
    return reached


def _make_manager(
    root: Path,
    backend: AuphonicBackend,
) -> tuple[JobManager, EventBus, list[tuple[str, Any]]]:
    ensure_root(root)
    store = JsonFileJobStore(root / ".clm" / "jobs.json")
    bus = EventBus()
    events: list[tuple[str, Any]] = []
    bus.subscribe(lambda topic, payload: events.append((topic, payload)))
    manager = JobManager(
        backend=backend,
        root_dir=root,
        store=store,
        bus=bus,
        poll_interval=0.02,  # tiny interval for fast tests
    )
    return manager, bus, events


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


class TestAuphonicJobManagerIntegration:
    def test_poller_drives_job_to_completion(self, tmp_path: Path) -> None:
        """One intermediate PROCESSING poll then DONE → COMPLETED."""
        done = AuphonicProduction(
            uuid="prod-42",
            status=AuphonicStatus.DONE,
            output_files=[
                AuphonicOutputFile(
                    format="video",
                    ending="mp4",
                    download_url="https://cdn/final.mp4",
                )
            ],
        )
        client = _ScriptedAuphonicClient(
            get_responses=[
                AuphonicProduction(
                    uuid="prod-42",
                    status=AuphonicStatus.AUDIO_PROCESSING,
                    status_string="Audio Processing",
                ),
                AuphonicProduction(
                    uuid="prod-42",
                    status=AuphonicStatus.AUDIO_ENCODING,
                    status_string="Encoding",
                ),
                done,
            ]
        )
        root = tmp_path / "recordings"
        ensure_root(root)
        raw = _make_raw_file(root)

        backend = AuphonicBackend(client=client, root_dir=root)  # type: ignore[arg-type]
        manager, bus, events = _make_manager(root, backend)

        try:
            job = manager.submit(raw)
            assert job.state == JobState.PROCESSING

            reached = _wait_for_state(manager, bus, job.id, JobState.COMPLETED)
            assert reached.is_set(), "poller did not drive job to COMPLETED"

            final = manager.get(job.id)
            assert final is not None
            assert final.state == JobState.COMPLETED
            assert final.progress == pytest.approx(1.0)
            assert final.final_path.exists()
            # Raw archived off to-process tree.
            assert not raw.exists()
        finally:
            manager.shutdown(timeout=2.0)

        # The poller should have walked from submit through multiple
        # get_production calls and one download.
        assert "create_production" in client.calls
        assert "upload_input" in client.calls
        assert "start_production" in client.calls
        assert client.calls.count("get_production") >= 2
        assert "download" in client.calls

        # Subscribers saw multiple job events (submit + polls + finalize).
        job_events = [e for e in events if e[0] == JOB_EVENT_TOPIC]
        assert len(job_events) >= 3

    def test_poller_handles_error_status(self, tmp_path: Path) -> None:
        client = _ScriptedAuphonicClient(
            get_responses=[
                AuphonicProduction(
                    uuid="prod-42",
                    status=AuphonicStatus.ERROR,
                    error_message="input too short",
                )
            ]
        )
        root = tmp_path / "recordings"
        ensure_root(root)
        raw = _make_raw_file(root)

        backend = AuphonicBackend(client=client, root_dir=root)  # type: ignore[arg-type]
        manager, bus, _ = _make_manager(root, backend)

        try:
            job = manager.submit(raw)

            reached = _wait_for_state(manager, bus, job.id, JobState.FAILED)
            assert reached.is_set(), "poller did not mark job FAILED on Auphonic ERROR"

            final = manager.get(job.id)
            assert final is not None
            assert final.state == JobState.FAILED
            assert "input too short" in (final.error or "")
            # Raw file is NOT archived on failure.
            assert raw.exists()
        finally:
            manager.shutdown(timeout=2.0)

    def test_in_flight_job_is_persisted_between_polls(self, tmp_path: Path) -> None:
        """The JSON store should reflect intermediate PROCESSING updates."""
        client = _ScriptedAuphonicClient(
            get_responses=[
                AuphonicProduction(
                    uuid="prod-42",
                    status=AuphonicStatus.AUDIO_PROCESSING,
                    status_string="Audio Processing",
                ),
                AuphonicProduction(
                    uuid="prod-42",
                    status=AuphonicStatus.DONE,
                    output_files=[
                        AuphonicOutputFile(
                            format="video",
                            ending="mp4",
                            download_url="https://cdn/x.mp4",
                        )
                    ],
                ),
            ]
        )
        root = tmp_path / "recordings"
        ensure_root(root)
        raw = _make_raw_file(root)

        backend = AuphonicBackend(client=client, root_dir=root)  # type: ignore[arg-type]
        manager, bus, _ = _make_manager(root, backend)

        try:
            job = manager.submit(raw)
            reached = _wait_for_state(manager, bus, job.id, JobState.COMPLETED)
            assert reached.is_set()
        finally:
            manager.shutdown(timeout=2.0)

        # Re-open the store from disk and confirm the final state
        # survived. This emulates a process restart: a new JobStore
        # instance reading the same jobs.json.
        reopened = JsonFileJobStore(root / ".clm" / "jobs.json")
        persisted = {j.id: j for j in reopened.load_all()}
        assert job.id in persisted
        assert persisted[job.id].state == JobState.COMPLETED
        assert persisted[job.id].progress == pytest.approx(1.0)

    def test_cancel_stops_poller_work_for_that_job(self, tmp_path: Path) -> None:
        """Cancel should mark a job CANCELLED even while it's being polled."""
        # Return PROCESSING forever so the poller keeps iterating until
        # we cancel.
        forever = [
            AuphonicProduction(
                uuid="prod-42",
                status=AuphonicStatus.AUDIO_PROCESSING,
                status_string="Processing forever",
            )
        ] * 50
        client = _ScriptedAuphonicClient(get_responses=forever)
        root = tmp_path / "recordings"
        ensure_root(root)
        raw = _make_raw_file(root)

        backend = AuphonicBackend(client=client, root_dir=root)  # type: ignore[arg-type]
        manager, bus, _ = _make_manager(root, backend)

        try:
            job = manager.submit(raw)
            assert job.state == JobState.PROCESSING

            manager.cancel(job.id)

            cancelled = manager.get(job.id)
            assert cancelled is not None
            assert cancelled.state == JobState.CANCELLED
            # Backend should have been asked to delete the remote prod.
            assert "delete_production" in client.calls
        finally:
            manager.shutdown(timeout=2.0)
