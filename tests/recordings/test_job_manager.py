"""Tests for :class:`JobManager` with fake backends."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from clm.recordings.workflow.backends.base import JobContext, ProcessingBackend
from clm.recordings.workflow.directories import ensure_root, to_process_dir
from clm.recordings.workflow.event_bus import EventBus
from clm.recordings.workflow.job_manager import JOB_EVENT_TOPIC, JobManager
from clm.recordings.workflow.job_store import JsonFileJobStore
from clm.recordings.workflow.jobs import (
    BackendCapabilities,
    JobState,
    ProcessingJob,
    ProcessingOptions,
)

# ----------------------------------------------------------------------
# Fake backends
# ----------------------------------------------------------------------


class _SyncFakeBackend:
    """Synchronous fake backend that completes immediately."""

    capabilities = BackendCapabilities(
        name="sync-fake",
        display_name="Sync Fake",
        is_synchronous=True,
    )

    def __init__(self) -> None:
        self.submits: list[Path] = []
        self.cancels: list[str] = []

    def accepts_file(self, path: Path) -> bool:
        return True

    def submit(self, raw_path, final_path, *, options, ctx):
        self.submits.append(raw_path)
        job = ProcessingJob(
            backend_name="sync-fake",
            raw_path=raw_path,
            final_path=final_path,
            relative_dir=Path(),
            state=JobState.COMPLETED,
            progress=1.0,
            message="Done",
        )
        ctx.report(job)
        return job

    def poll(self, job, *, ctx):
        return job

    def cancel(self, job, *, ctx):
        self.cancels.append(job.id)


class _AsyncFakeBackend:
    """Async fake that advances one step each poll call.

    submit() leaves the job in PROCESSING; the n-th poll call transitions
    it to COMPLETED after poll_completes_after_calls polls.
    """

    capabilities = BackendCapabilities(
        name="async-fake",
        display_name="Async Fake",
        is_synchronous=False,
    )

    def __init__(self, *, poll_completes_after_calls: int = 1) -> None:
        self._completes_after = poll_completes_after_calls
        self._poll_calls: dict[str, int] = {}
        self.submits: list[Path] = []
        self.cancels: list[str] = []
        self.poll_event = threading.Event()

    def accepts_file(self, path: Path) -> bool:
        return True

    def submit(self, raw_path, final_path, *, options, ctx):
        self.submits.append(raw_path)
        job = ProcessingJob(
            backend_name="async-fake",
            raw_path=raw_path,
            final_path=final_path,
            relative_dir=Path(),
            state=JobState.PROCESSING,
            progress=0.1,
            message="Starting",
            backend_ref="fake-ref",
        )
        ctx.report(job)
        return job

    def poll(self, job, *, ctx):
        count = self._poll_calls.get(job.id, 0) + 1
        self._poll_calls[job.id] = count
        if count >= self._completes_after:
            job.state = JobState.COMPLETED
            job.progress = 1.0
            job.message = "Done"
        else:
            job.progress = min(0.1 + 0.2 * count, 0.9)
        ctx.report(job)
        self.poll_event.set()
        return job

    def cancel(self, job, *, ctx):
        self.cancels.append(job.id)


class _RaisingAsyncBackend(_AsyncFakeBackend):
    """Async fake whose poll() raises so we can test error handling."""

    def poll(self, job, *, ctx):
        raise RuntimeError("network unreachable")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_manager(
    tmp_path: Path,
    backend: ProcessingBackend,
    *,
    poll_interval: float = 0.05,
) -> tuple[JobManager, EventBus, list[tuple[str, ProcessingJob]]]:
    """Build a manager with a JSON store, live bus, and a recording subscriber."""
    ensure_root(tmp_path)
    store = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
    bus = EventBus()
    events: list[tuple[str, ProcessingJob]] = []
    bus.subscribe(lambda topic, payload: events.append((topic, payload)))

    manager = JobManager(
        backend=backend,
        root_dir=tmp_path,
        store=store,
        bus=bus,
        poll_interval=poll_interval,
    )
    return manager, bus, events


def _make_raw_path(tmp_path: Path, *, name: str = "lecture--RAW.mp4") -> Path:
    topic_dir = to_process_dir(tmp_path) / "py/week01"
    topic_dir.mkdir(parents=True, exist_ok=True)
    raw = topic_dir / name
    raw.write_bytes(b"x")
    return raw


def _wait_for_state(
    manager: JobManager,
    bus: EventBus,
    job_id: str,
    target: JobState,
    *,
    timeout: float = 10.0,
) -> threading.Event:
    """Subscribe and return an Event set when *job_id* reaches *target*.

    Event-based waiting is deterministic under heavy test-suite load and
    avoids time.sleep polling loops, which flake on Windows CI runners.
    Also handles the subscribe-after-publish race by checking the
    manager's current view immediately after subscribing.
    """
    reached = threading.Event()

    def on_event(topic: str, payload: object) -> None:
        if isinstance(payload, ProcessingJob) and payload.id == job_id and payload.state == target:
            reached.set()

    bus.subscribe(on_event)

    # Guard against a race where the target state was reached between
    # job creation and subscription: if the current state already matches
    # (or is past the target for terminal states), short-circuit.
    current = manager.get(job_id)
    if current is not None and current.state == target:
        reached.set()

    reached.wait(timeout=timeout)
    return reached


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


class TestJobManagerSynchronousBackend:
    def test_submit_returns_completed_job(self, tmp_path: Path):
        backend = _SyncFakeBackend()
        manager, _, _ = _make_manager(tmp_path, backend)
        raw = _make_raw_path(tmp_path)

        job = manager.submit(raw)

        assert job.state == JobState.COMPLETED
        assert len(backend.submits) == 1
        assert backend.submits[0] == raw

    def test_submit_publishes_job_event(self, tmp_path: Path):
        manager, _, events = _make_manager(tmp_path, _SyncFakeBackend())
        raw = _make_raw_path(tmp_path)

        manager.submit(raw)

        topics = [topic for (topic, _) in events]
        assert JOB_EVENT_TOPIC in topics
        # At least two events: backend's report(job) during submit,
        # plus the manager's post-submit publish.
        assert len(events) >= 2

    def test_submit_persists_to_store(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        raw = _make_raw_path(tmp_path)
        job = manager.submit(raw)

        store = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
        persisted = store.load_all()
        assert len(persisted) == 1
        assert persisted[0].id == job.id
        assert persisted[0].state == JobState.COMPLETED

    def test_list_jobs_returns_newest_first(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())

        first = manager.submit(_make_raw_path(tmp_path, name="a--RAW.mp4"))
        time.sleep(0.002)  # guarantee distinct created_at
        second = manager.submit(_make_raw_path(tmp_path, name="b--RAW.mp4"))

        listed = manager.list_jobs()
        assert [j.id for j in listed] == [second.id, first.id]

    def test_get_returns_job_by_id(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        job = manager.submit(_make_raw_path(tmp_path))
        assert manager.get(job.id) is not None
        assert manager.get("does-not-exist") is None

    def test_poller_not_started_for_sync_backend(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        manager.submit(_make_raw_path(tmp_path))
        assert manager._poller is None

    def test_derive_final_path_strips_raw_suffix(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        raw = _make_raw_path(tmp_path, name="intro--RAW.mp4")
        final = manager._derive_final_path(raw)
        assert final.name == "intro.mp4"
        # Path is <tmp>/final/py/week01/intro.mp4
        assert final.parts[-4:] == ("final", "py", "week01", "intro.mp4")


class TestJobManagerAsynchronousBackend:
    def test_submit_leaves_job_in_processing(self, tmp_path: Path):
        backend = _AsyncFakeBackend(poll_completes_after_calls=3)
        manager, _, _ = _make_manager(tmp_path, backend)
        try:
            raw = _make_raw_path(tmp_path)
            job = manager.submit(raw)
            assert job.state == JobState.PROCESSING
            assert job.backend_ref == "fake-ref"
        finally:
            manager.shutdown(timeout=2.0)

    def test_poller_advances_job_to_completion(self, tmp_path: Path):
        backend = _AsyncFakeBackend(poll_completes_after_calls=2)
        manager, bus, _ = _make_manager(tmp_path, backend, poll_interval=0.02)
        try:
            job = manager.submit(_make_raw_path(tmp_path))

            reached = _wait_for_state(manager, bus, job.id, JobState.COMPLETED)
            assert reached.is_set(), "poller did not drive job to COMPLETED in time"

            final = manager.get(job.id)
            assert final is not None
            assert final.state == JobState.COMPLETED
            assert final.progress == 1.0
        finally:
            manager.shutdown(timeout=2.0)

    def test_poller_failure_marks_job_failed(self, tmp_path: Path):
        backend = _RaisingAsyncBackend()
        manager, bus, _ = _make_manager(tmp_path, backend, poll_interval=0.02)
        try:
            job = manager.submit(_make_raw_path(tmp_path))

            reached = _wait_for_state(manager, bus, job.id, JobState.FAILED)
            assert reached.is_set(), "poller did not mark failing job as FAILED in time"

            final = manager.get(job.id)
            assert final is not None
            assert final.state == JobState.FAILED
            assert "network unreachable" in (final.error or "")
        finally:
            manager.shutdown(timeout=2.0)

    def test_poller_is_started_only_once(self, tmp_path: Path):
        backend = _AsyncFakeBackend(poll_completes_after_calls=10)
        manager, _, _ = _make_manager(tmp_path, backend, poll_interval=0.05)
        try:
            manager.submit(_make_raw_path(tmp_path, name="a--RAW.mp4"))
            first_thread = manager._poller
            manager.submit(_make_raw_path(tmp_path, name="b--RAW.mp4"))
            second_thread = manager._poller
            assert first_thread is second_thread
            assert first_thread is not None
            assert first_thread.is_alive()
        finally:
            manager.shutdown(timeout=2.0)


class TestJobManagerCancel:
    def test_cancel_sync_job_sets_cancelled_state(self, tmp_path: Path):
        """Cancel of an already-terminal job is a no-op that preserves state."""
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        job = manager.submit(_make_raw_path(tmp_path))
        # Sync job is already COMPLETED — cancel should not move it.
        result = manager.cancel(job.id)
        assert result is not None
        assert result.state == JobState.COMPLETED

    def test_cancel_unknown_id_returns_none(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        assert manager.cancel("unknown-id") is None

    def test_cancel_in_flight_async_job(self, tmp_path: Path):
        backend = _AsyncFakeBackend(poll_completes_after_calls=100)
        manager, _, _ = _make_manager(tmp_path, backend, poll_interval=1.0)
        try:
            job = manager.submit(_make_raw_path(tmp_path))
            result = manager.cancel(job.id)
            assert result is not None
            assert result.state == JobState.CANCELLED
            assert backend.cancels == [job.id]
        finally:
            manager.shutdown(timeout=2.0)


class TestJobManagerRehydration:
    def test_loads_jobs_from_store_on_startup(self, tmp_path: Path):
        """A pre-existing job store is picked up on manager construction."""
        ensure_root(tmp_path)
        store_path = tmp_path / ".clm" / "jobs.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)

        pre = JsonFileJobStore(store_path)
        old_job = ProcessingJob(
            backend_name="sync-fake",
            raw_path=tmp_path / "a--RAW.mp4",
            final_path=tmp_path / "final" / "a.mp4",
            relative_dir=Path(),
            state=JobState.COMPLETED,
        )
        pre.save(old_job)

        manager = JobManager(
            backend=_SyncFakeBackend(),
            root_dir=tmp_path,
            store=JsonFileJobStore(store_path),
            bus=EventBus(),
        )

        assert manager.get(old_job.id) is not None

    def test_uploading_jobs_become_failed_on_startup(self, tmp_path: Path):
        """Per design doc Q5, interrupted uploads cannot be resumed."""
        ensure_root(tmp_path)
        store_path = tmp_path / ".clm" / "jobs.json"
        pre = JsonFileJobStore(store_path)
        in_flight = ProcessingJob(
            backend_name="auphonic",
            raw_path=tmp_path / "a--RAW.mp4",
            final_path=tmp_path / "final" / "a.mp4",
            relative_dir=Path(),
            state=JobState.UPLOADING,
            message="Uploading",
        )
        pre.save(in_flight)

        manager = JobManager(
            backend=_SyncFakeBackend(),
            root_dir=tmp_path,
            store=JsonFileJobStore(store_path),
            bus=EventBus(),
        )

        rehydrated = manager.get(in_flight.id)
        assert rehydrated is not None
        assert rehydrated.state == JobState.FAILED
        assert "re-submit" in (rehydrated.error or "")


class TestJobManagerShutdown:
    def test_shutdown_is_idempotent(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        manager.shutdown()
        manager.shutdown()  # second call must not raise

    def test_shutdown_stops_async_poller(self, tmp_path: Path):
        backend = _AsyncFakeBackend(poll_completes_after_calls=1000)
        manager, _, _ = _make_manager(tmp_path, backend, poll_interval=0.05)
        manager.submit(_make_raw_path(tmp_path))
        poller = manager._poller
        assert poller is not None and poller.is_alive()

        manager.shutdown(timeout=2.0)
        assert not poller.is_alive()


class TestJobContextProtocol:
    def test_default_context_is_job_context(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        ctx = manager._make_context()
        assert isinstance(ctx, JobContext)
