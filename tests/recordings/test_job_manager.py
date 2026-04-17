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

    def reconcile(self, job, *, ctx):
        return job


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

    def reconcile(self, job, *, ctx):
        self.reconciles = getattr(self, "reconciles", [])
        self.reconciles.append(job.id)
        return job


class _RaisingAsyncBackend(_AsyncFakeBackend):
    """Async fake whose poll() raises so we can test error handling.

    By default raises a generic ``RuntimeError`` (which the manager
    classifies as *transient* — the job should stay in PROCESSING and
    have ``last_poll_error`` populated). Tests that want to simulate
    a permanent failure construct :class:`_RaisingAsyncBackend` with
    a pre-built exception instead.
    """

    def __init__(
        self,
        *,
        poll_completes_after_calls: int = 3,
        exc: BaseException | None = None,
    ) -> None:
        super().__init__(poll_completes_after_calls=poll_completes_after_calls)
        self._exc = exc or RuntimeError("network unreachable")

    def poll(self, job, *, ctx):
        raise self._exc


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

    def test_delete_job_removes_terminal_job_from_store(self, tmp_path: Path):
        """delete_job on a completed job wipes it from memory and disk."""
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        job = manager.submit(_make_raw_path(tmp_path))
        assert job.state == JobState.COMPLETED

        assert manager.delete_job(job.id) is True
        assert manager.get(job.id) is None

        # A fresh store reading from disk should not see the job either.
        reopened = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
        assert reopened.load_all() == []

    def test_delete_job_refuses_in_flight_jobs(self, tmp_path: Path):
        """delete_job must NOT remove jobs that are still running.

        The CLI guards against this too, but belt-and-suspenders at
        the manager layer prevents a dashboard bug from losing work.

        Pre-seeds the manager directly (rather than using submit)
        so no background poller thread races the assertions.
        """
        manager, _, _ = _make_manager(tmp_path, _AsyncFakeBackend())
        raw = _make_raw_path(tmp_path)
        job = ProcessingJob(
            id="in-flight-job",
            backend_name="async-fake",
            raw_path=raw,
            final_path=tmp_path / "final" / "lecture.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
            progress=0.3,
            message="pre-seeded",
            backend_ref="fake-ref",
        )
        manager._store_job(job)

        assert manager.delete_job(job.id) is False
        # Job is still present in memory and the store.
        assert manager.get(job.id) is not None
        reopened = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
        assert [j.id for j in reopened.load_all()] == [job.id]
        assert manager._poller is None

    def test_delete_job_unknown_id_returns_false(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        assert manager.delete_job("does-not-exist") is False

    def test_mark_failed_transitions_in_flight_job(self, tmp_path: Path):
        """mark_failed on an in-flight job sets state=FAILED with the reason."""
        manager, _, _ = _make_manager(tmp_path, _AsyncFakeBackend())
        raw = _make_raw_path(tmp_path)
        job = ProcessingJob(
            id="stuck-job",
            backend_name="async-fake",
            raw_path=raw,
            final_path=tmp_path / "final" / "lecture.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
            progress=0.4,
            message="Processing on Auphonic",
            backend_ref="fake-ref",
            last_poll_error="connection timed out",
        )
        manager._store_job(job)

        updated = manager.mark_failed(job.id, reason="user gave up on retry")
        assert updated is not None
        assert updated.state == JobState.FAILED
        assert updated.error == "user gave up on retry"
        # Transient-error marker is cleared so the UI doesn't show both.
        assert updated.last_poll_error is None
        # Persisted through the store.
        reopened = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
        persisted = {j.id: j for j in reopened.load_all()}
        assert persisted[job.id].state == JobState.FAILED
        assert persisted[job.id].error == "user gave up on retry"

    def test_mark_failed_does_not_call_backend_cancel(self, tmp_path: Path):
        """mark_failed must NOT delete the remote production.

        The whole point of `jobs fail` vs `jobs cancel` is that the
        user wants to preserve the remote work — maybe to download
        it manually later. If mark_failed called backend.cancel we'd
        silently blow that away.
        """
        backend = _AsyncFakeBackend()
        manager, _, _ = _make_manager(tmp_path, backend)
        job = ProcessingJob(
            id="preserve-remote",
            backend_name="async-fake",
            raw_path=_make_raw_path(tmp_path),
            final_path=tmp_path / "final" / "lecture.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
            backend_ref="remote-uuid",
        )
        manager._store_job(job)

        manager.mark_failed(job.id, reason="poll wedged")
        assert backend.cancels == [], (
            f"mark_failed must not invoke backend.cancel — but got: {backend.cancels}"
        )

    def test_mark_failed_refuses_terminal_jobs(self, tmp_path: Path):
        """mark_failed returns None for COMPLETED/FAILED/CANCELLED jobs."""
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        completed = manager.submit(_make_raw_path(tmp_path))
        assert completed.state == JobState.COMPLETED

        result = manager.mark_failed(completed.id, reason="no")
        assert result is None
        # State is unchanged — the reason didn't overwrite it.
        same = manager.get(completed.id)
        assert same is not None
        assert same.state == JobState.COMPLETED
        assert same.error is None

    def test_mark_failed_unknown_id_returns_none(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        assert manager.mark_failed("does-not-exist", reason="n/a") is None


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

    def test_transient_poll_error_is_recorded_but_job_stays_processing(self, tmp_path: Path):
        """Generic exceptions are treated as transient.

        A ``RuntimeError`` from the backend (simulating a network blip
        or similar) must NOT drive the job to FAILED — losing a
        completed remote production to a momentary glitch is worse
        than leaving a stuck job visible. The error is recorded on
        ``last_poll_error`` so the user can see what's going wrong.
        """
        backend = _RaisingAsyncBackend()
        manager, bus, _ = _make_manager(tmp_path, backend, poll_interval=0.02)
        # Watch for poll events so we can assert the poll actually ran
        # at least once without racing on time.sleep.
        poll_event_seen = threading.Event()

        def on_event(topic: str, payload: object) -> None:
            if (
                topic == JOB_EVENT_TOPIC
                and isinstance(payload, ProcessingJob)
                and payload.last_poll_error is not None
            ):
                poll_event_seen.set()

        bus.subscribe(on_event)
        try:
            job = manager.submit(_make_raw_path(tmp_path))

            assert poll_event_seen.wait(timeout=5.0), (
                "poller did not record last_poll_error within timeout"
            )

            current = manager.get(job.id)
            assert current is not None
            # Job stays in-flight — transient errors never transition
            # to FAILED automatically.
            assert current.state == JobState.PROCESSING
            assert current.error is None
            assert current.last_poll_error is not None
            assert "network unreachable" in current.last_poll_error
        finally:
            manager.shutdown(timeout=2.0)

    def test_permanent_http_error_marks_job_failed(self, tmp_path: Path):
        """HTTP 401/403/404/410 drive the job to FAILED immediately.

        These statuses will not recover on retry (bad API key, deleted
        production, …) so the poller must surface them as terminal
        instead of leaving a zombie job that polls forever.
        """
        from clm.recordings.workflow.backends.auphonic_client import AuphonicHTTPError

        exc = AuphonicHTTPError(
            method="GET",
            url="https://auphonic.test/api/production/deleted.json",
            status_code=404,
            body='{"error": "not found"}',
        )
        backend = _RaisingAsyncBackend(exc=exc)
        manager, bus, _ = _make_manager(tmp_path, backend, poll_interval=0.02)
        try:
            job = manager.submit(_make_raw_path(tmp_path))

            reached = _wait_for_state(manager, bus, job.id, JobState.FAILED)
            assert reached.is_set(), "permanent error did not mark job FAILED in time"

            final = manager.get(job.id)
            assert final is not None
            assert final.state == JobState.FAILED
            assert final.error is not None
            assert "404" in final.error
            # last_poll_error is cleared on the terminal transition so
            # the UI shows `error`, not both.
            assert final.last_poll_error is None
        finally:
            manager.shutdown(timeout=2.0)

    def test_poll_once_targets_a_specific_job(self, tmp_path: Path):
        """poll_once(job_id=…) only ticks the named job.

        The ``jobs poll <id>`` CLI relies on this — polling one job
        manually must not accidentally advance every other in-flight
        job in the store.

        We pre-seed the manager with two PROCESSING jobs instead of
        calling submit, which would start the background poller and
        race the manual tick.
        """
        backend = _AsyncFakeBackend(poll_completes_after_calls=100)
        manager, _, _ = _make_manager(tmp_path, backend)

        raw_a = _make_raw_path(tmp_path, name="a--RAW.mp4")
        raw_b = _make_raw_path(tmp_path, name="b--RAW.mp4")
        a = ProcessingJob(
            id="aaaa-job",
            backend_name="async-fake",
            raw_path=raw_a,
            final_path=tmp_path / "final" / "a.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
            progress=0.1,
            message="pre-seeded",
            backend_ref="fake-ref-a",
        )
        b = ProcessingJob(
            id="bbbb-job",
            backend_name="async-fake",
            raw_path=raw_b,
            final_path=tmp_path / "final" / "b.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
            progress=0.1,
            message="pre-seeded",
            backend_ref="fake-ref-b",
        )
        manager._store_job(a)
        manager._store_job(b)

        polled = manager.poll_once(job_id=a.id)
        assert len(polled) == 1
        assert polled[0].id == a.id
        # Only job a advanced; b is untouched.
        a_after = manager.get(a.id)
        b_after = manager.get(b.id)
        assert a_after is not None and b_after is not None
        assert a_after.progress > 0.1
        assert b_after.progress == 0.1
        # And poll was only called for a, not b.
        assert backend._poll_calls.get(a.id, 0) == 1
        assert backend._poll_calls.get(b.id, 0) == 0
        # No poller thread was ever started, so no shutdown needed.
        assert manager._poller is None

    def test_poll_once_skips_terminal_or_unknown_jobs(self, tmp_path: Path):
        """poll_once with a terminal or unknown id returns an empty list.

        This is the shape the CLI relies on to print "nothing to poll"
        without raising.
        """
        backend = _SyncFakeBackend()
        manager, _, _ = _make_manager(tmp_path, backend)
        # Sync backend drives the job to COMPLETED on submit.
        job = manager.submit(_make_raw_path(tmp_path))
        assert job.state == JobState.COMPLETED

        # Terminal id → empty list, no exception.
        assert manager.poll_once(job_id=job.id) == []
        # Unknown id → empty list, no exception.
        assert manager.poll_once(job_id="does-not-exist") == []

    def test_successful_poll_clears_transient_error_marker(self, tmp_path: Path):
        """A good poll after a bad one wipes last_poll_error."""

        class _FlakyBackend(_AsyncFakeBackend):
            """Raises once, then behaves normally."""

            def __init__(self) -> None:
                super().__init__(poll_completes_after_calls=2)
                self._raised = False

            def poll(self, job, *, ctx):
                if not self._raised:
                    self._raised = True
                    raise RuntimeError("temporary blip")
                return super().poll(job, ctx=ctx)

        backend = _FlakyBackend()
        manager, bus, _ = _make_manager(tmp_path, backend, poll_interval=0.02)
        try:
            job = manager.submit(_make_raw_path(tmp_path))

            reached = _wait_for_state(manager, bus, job.id, JobState.COMPLETED)
            assert reached.is_set(), "flaky backend did not eventually complete"

            final = manager.get(job.id)
            assert final is not None
            assert final.state == JobState.COMPLETED
            assert final.last_poll_error is None, (
                "last_poll_error should be cleared after a successful poll"
            )
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


class TestRequestPollSoon:
    """Backends can nudge the poller loop without waiting out the interval."""

    def test_wake_runs_poll_sooner_than_interval(self, tmp_path: Path):
        """After a wake signal, poll_once is driven before the sleep elapses."""
        # Keep the job alive for many polls so we can observe repeat cycles.
        backend = _AsyncFakeBackend(poll_completes_after_calls=100)
        manager, _, _ = _make_manager(tmp_path, backend, poll_interval=30.0)
        raw = _make_raw_path(tmp_path)

        try:
            manager.submit(raw)

            # The first poller iteration runs poll_once immediately (no
            # wait yet), then blocks on _wake for poll_interval seconds.
            assert backend.poll_event.wait(timeout=2.0), "initial poll didn't fire"

            # Reset and wake. Without the signal the next poll would wait
            # the full 30s; with it, the poller reacts within a second.
            backend.poll_event.clear()
            manager.request_poll_soon()
            assert backend.poll_event.wait(timeout=2.0), (
                "poller did not react to request_poll_soon within 2s "
                "(poll_interval=30s so this would take 30s without the wake)"
            )
        finally:
            manager.shutdown(timeout=2.0)

    def test_wake_is_no_op_for_synchronous_backend(self, tmp_path: Path):
        """Synchronous backends don't have a poller — the call must not raise."""
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        # Explicitly safe to call even with no poller running.
        manager.request_poll_soon()

    def test_shutdown_wakes_poller_promptly(self, tmp_path: Path):
        """Shutdown must not sit out the remainder of the poll interval."""
        backend = _AsyncFakeBackend(poll_completes_after_calls=10)
        manager, bus, _ = _make_manager(tmp_path, backend, poll_interval=30.0)
        raw = _make_raw_path(tmp_path)

        job = manager.submit(raw)
        _wait_for_state(manager, bus, job.id, JobState.PROCESSING)

        started = time.monotonic()
        manager.shutdown(timeout=5.0)
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"shutdown took {elapsed:.1f}s — poller wasn't woken on stop"

    def test_context_request_poll_soon_delegates_to_manager(self, tmp_path: Path):
        """JobContext.request_poll_soon must reach the manager."""
        backend = _AsyncFakeBackend(poll_completes_after_calls=1)
        manager, _, _ = _make_manager(tmp_path, backend, poll_interval=30.0)
        # Build a context without going through submit so we can call it
        # directly. The manager isn't running a poller yet, so we just
        # verify the call doesn't raise and sets the wake flag.
        ctx = manager._make_context()
        ctx.request_poll_soon()
        assert manager._wake.is_set()


class TestReconcile:
    """``JobManager.reconcile`` routes to the backend and persists the result."""

    def test_reconcile_routes_to_backend(self, tmp_path: Path):
        backend = _AsyncFakeBackend()
        manager, _, _ = _make_manager(tmp_path, backend)
        raw = _make_raw_path(tmp_path)
        job = manager.submit(raw)

        try:
            updated = manager.reconcile(job.id)
            assert updated is not None
            # The fake's reconcile records the call.
            assert getattr(backend, "reconciles", []) == [job.id]
        finally:
            manager.shutdown(timeout=2.0)

    def test_reconcile_unknown_id_returns_none(self, tmp_path: Path):
        manager, _, _ = _make_manager(tmp_path, _SyncFakeBackend())
        assert manager.reconcile("no-such-job") is None

    def test_reconcile_persists_and_publishes(self, tmp_path: Path):
        backend = _SyncFakeBackend()
        manager, _, events = _make_manager(tmp_path, backend)
        raw = _make_raw_path(tmp_path)
        job = manager.submit(raw)

        # Clear events from submit so we only see the reconcile-generated ones.
        events.clear()
        updated = manager.reconcile(job.id)

        assert updated is not None
        # Reconcile must publish at least one job event on the bus.
        assert any(topic == JOB_EVENT_TOPIC for (topic, _) in events)

    def test_reconcile_classifies_permanent_backend_error(self, tmp_path: Path):
        """Permanent HTTP errors from reconcile mark the job FAILED."""
        from clm.recordings.workflow.backends.auphonic_client import AuphonicHTTPError

        class _PermanentFailBackend(_SyncFakeBackend):
            # Synchronous backend: no poller to race against.
            def reconcile(self, job, *, ctx):
                raise AuphonicHTTPError("GET", "https://x", 404, "Not Found")

        backend = _PermanentFailBackend()
        manager, _, _ = _make_manager(tmp_path, backend)
        raw = _make_raw_path(tmp_path)
        job = manager.submit(raw)

        updated = manager.reconcile(job.id)
        assert updated is not None
        assert updated.state == JobState.FAILED
        assert "404" in (updated.error or "") or "Not Found" in (updated.error or "")

    def test_reconcile_classifies_transient_backend_error(self, tmp_path: Path):
        """Transient errors don't change state; last_poll_error is set."""

        class _TransientBackend(_SyncFakeBackend):
            # Synchronous backend avoids the poller racing and clearing
            # ``last_poll_error`` via a background successful poll.
            def reconcile(self, job, *, ctx):
                raise RuntimeError("temporary network hiccup")

        backend = _TransientBackend()
        manager, _, _ = _make_manager(tmp_path, backend)
        raw = _make_raw_path(tmp_path)
        job = manager.submit(raw)

        pre_state = manager.get(job.id).state
        updated = manager.reconcile(job.id)
        assert updated is not None
        assert updated.state == pre_state
        assert "temporary" in (updated.last_poll_error or "")


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

    def test_uploading_without_backend_ref_fails_on_startup(self, tmp_path: Path):
        """When no production was ever created, the upload is genuinely lost."""
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
            backend_ref=None,
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

    def test_uploading_with_backend_ref_resumes_as_processing(self, tmp_path: Path):
        """When a production exists upstream, resume instead of failing.

        The crash might have happened mid-upload *after* the production
        was created — Auphonic likely has the file. Transition to
        PROCESSING so the next poll (or the user's Verify action) can
        settle the real state.
        """
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
            backend_ref="prod-uuid-7",
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
        assert rehydrated.state == JobState.PROCESSING
        assert rehydrated.error is None
        assert "Resumed" in rehydrated.message


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
