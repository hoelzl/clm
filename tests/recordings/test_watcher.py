"""Tests for the recordings filesystem watcher.

The watcher is backend-agnostic after Phase B: it asks the active
backend whether a file is interesting, waits for size stability, and
delegates to a :class:`JobManager`. Tests here use a fake backend that
records calls and returns canned jobs.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from clm.recordings.workflow.backends.base import ProcessingBackend
from clm.recordings.workflow.directories import ensure_root
from clm.recordings.workflow.event_bus import EventBus
from clm.recordings.workflow.job_manager import JobManager
from clm.recordings.workflow.jobs import (
    BackendCapabilities,
    JobState,
    ProcessingJob,
    ProcessingOptions,
)
from clm.recordings.workflow.watcher import (
    RecordingsWatcher,
    WatcherState,
    _WatchHandler,
)

# ------------------------------------------------------------------
# Test doubles
# ------------------------------------------------------------------


class _FakeBackend:
    """In-memory backend that records submit calls and returns canned jobs."""

    capabilities = BackendCapabilities(
        name="fake",
        display_name="Fake",
        is_synchronous=True,
    )

    def __init__(
        self,
        *,
        accepted_suffix: str = ".wav",
        fail: bool = False,
    ) -> None:
        self._accepted_suffix = accepted_suffix
        self._fail = fail
        self.submit_calls: list[Path] = []
        self.cancel_calls: list[str] = []

    def accepts_file(self, path: Path) -> bool:
        return path.suffix.lower() == self._accepted_suffix

    def submit(self, raw_path, final_path, *, options, ctx):
        self.submit_calls.append(raw_path)
        state = JobState.FAILED if self._fail else JobState.COMPLETED
        job = ProcessingJob(
            backend_name="fake",
            raw_path=raw_path,
            final_path=final_path,
            relative_dir=Path(),
            state=state,
            progress=1.0,
            message="Done" if not self._fail else "Fake failure",
            error="Fake failure" if self._fail else None,
        )
        ctx.report(job)
        return job

    def poll(self, job, *, ctx):
        return job

    def cancel(self, job, *, ctx):
        self.cancel_calls.append(job.id)


class _InMemoryJobStore:
    """JobStore protocol implementation backed by an in-memory dict."""

    def __init__(self) -> None:
        self._jobs: dict[str, ProcessingJob] = {}

    def load_all(self) -> list[ProcessingJob]:
        return list(self._jobs.values())

    def save(self, job: ProcessingJob) -> None:
        self._jobs[job.id] = job

    def delete(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)


def _make_manager(
    root: Path,
    backend: ProcessingBackend,
) -> JobManager:
    return JobManager(
        backend=backend,
        root_dir=root,
        store=_InMemoryJobStore(),
        bus=EventBus(),
    )


# ------------------------------------------------------------------
# WatcherState
# ------------------------------------------------------------------


class TestWatcherState:
    def test_try_claim_returns_true_first_time(self):
        state = WatcherState()
        assert state.try_claim(Path("/a.wav")) is True

    def test_try_claim_returns_false_if_already_claimed(self):
        state = WatcherState()
        state.try_claim(Path("/a.wav"))
        assert state.try_claim(Path("/a.wav")) is False

    def test_release_allows_reclaim(self):
        state = WatcherState()
        p = Path("/a.wav")
        state.try_claim(p)
        state.release(p)
        assert state.try_claim(p) is True

    def test_try_claim_rejects_submitted(self):
        state = WatcherState()
        p = Path("/a.wav")
        state.mark_submitted(p)
        assert state.try_claim(p) is False

    def test_mark_submitted_is_idempotent(self):
        state = WatcherState()
        p = Path("/a.wav")
        state.mark_submitted(p)
        state.mark_submitted(p)  # should not raise

    def test_release_does_not_clear_submitted(self):
        state = WatcherState()
        p = Path("/a.wav")
        state.try_claim(p)
        state.mark_submitted(p)
        state.release(p)
        # Still rejected because it's in the submitted set
        assert state.try_claim(p) is False

    def test_release_unclaimed_path_is_safe(self):
        state = WatcherState()
        state.release(Path("/nonexistent"))  # should not raise

    def test_concurrent_claims(self):
        """Two threads racing to claim the same path — only one wins."""
        state = WatcherState()
        p = Path("/race.wav")
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait()
            results.append(state.try_claim(p))

        t1 = threading.Thread(target=claim)
        t2 = threading.Thread(target=claim)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sorted(results) == [False, True]


# ------------------------------------------------------------------
# RecordingsWatcher — construction
# ------------------------------------------------------------------


class TestWatcherInit:
    def test_exposes_backend_name(self, tmp_path: Path):
        backend = _FakeBackend()
        manager = _make_manager(tmp_path, backend)
        watcher = RecordingsWatcher(tmp_path, manager, backend)
        assert watcher.backend_name == "fake"

    def test_not_running_initially(self, tmp_path: Path):
        backend = _FakeBackend()
        manager = _make_manager(tmp_path, backend)
        watcher = RecordingsWatcher(tmp_path, manager, backend)
        assert watcher.running is False


# ------------------------------------------------------------------
# RecordingsWatcher — start / stop
# ------------------------------------------------------------------


class TestWatcherStartStop:
    def _make(self, tmp_path: Path) -> RecordingsWatcher:
        backend = _FakeBackend()
        manager = _make_manager(tmp_path, backend)
        return RecordingsWatcher(tmp_path, manager, backend)

    def test_start_creates_to_process_dir(self, tmp_path: Path):
        watcher = self._make(tmp_path)
        watcher.start()
        try:
            assert (tmp_path / "to-process").is_dir()
            assert watcher.running is True
        finally:
            watcher.stop()

    def test_stop_marks_not_running(self, tmp_path: Path):
        watcher = self._make(tmp_path)
        watcher.start()
        watcher.stop()
        assert watcher.running is False

    def test_start_is_idempotent(self, tmp_path: Path):
        watcher = self._make(tmp_path)
        watcher.start()
        observer1 = watcher._observer
        watcher.start()  # should not create a second observer
        assert watcher._observer is observer1
        watcher.stop()

    def test_stop_when_not_started_is_safe(self, tmp_path: Path):
        watcher = self._make(tmp_path)
        watcher.stop()  # should not raise


# ------------------------------------------------------------------
# RecordingsWatcher — stability detection
# ------------------------------------------------------------------


class TestStabilityDetection:
    def _make(
        self, tmp_path: Path, *, interval: float = 0.01, checks: int = 2
    ) -> RecordingsWatcher:
        backend = _FakeBackend()
        manager = _make_manager(tmp_path, backend)
        return RecordingsWatcher(
            tmp_path,
            manager,
            backend,
            stability_interval=interval,
            stability_checks=checks,
        )

    def test_stable_file_passes(self, tmp_path: Path):
        f = tmp_path / "test.wav"
        f.write_bytes(b"data")

        watcher = self._make(tmp_path)
        watcher._wait_for_stable(f)  # should not raise

    def test_missing_file_raises(self, tmp_path: Path):
        import pytest

        watcher = self._make(tmp_path)
        with pytest.raises(FileNotFoundError, match="disappeared"):
            watcher._wait_for_stable(tmp_path / "missing.wav")

    def test_empty_file_waits(self, tmp_path: Path):
        """Empty files (size 0) are never considered stable."""
        f = tmp_path / "empty.wav"
        f.write_bytes(b"")

        watcher = self._make(tmp_path)

        def grow():
            time.sleep(0.05)
            f.write_bytes(b"real data")

        t = threading.Thread(target=grow, daemon=True)
        t.start()
        watcher._wait_for_stable(f)
        t.join()
        assert f.stat().st_size > 0


# ------------------------------------------------------------------
# RecordingsWatcher — event dispatch
# ------------------------------------------------------------------


class TestEventDispatch:
    def _make_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "recordings"
        ensure_root(root)
        return root

    def test_ignores_unaccepted_file(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)
        watcher = RecordingsWatcher(
            root, manager, backend, stability_interval=0.01, stability_checks=1
        )

        # mp4 file when backend only accepts .wav
        mp4 = root / "to-process" / "topic--RAW.mp4"
        mp4.write_bytes(b"video data")

        watcher._on_file_event(mp4)
        # Give any stray thread a chance to run before asserting.
        time.sleep(0.05)
        assert backend.submit_calls == []

    def test_dispatches_accepted_file_to_job_manager(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)

        submitted: list[ProcessingJob] = []
        watcher = RecordingsWatcher(
            root,
            manager,
            backend,
            stability_interval=0.01,
            stability_checks=1,
            on_submitted=submitted.append,
        )

        wav = root / "to-process" / "topic--RAW.wav"
        wav.write_bytes(b"audio data")

        watcher._dispatch(wav)

        assert backend.submit_calls == [wav]
        assert len(submitted) == 1
        assert submitted[0].state == JobState.COMPLETED

    def test_double_claim_rejected(self, tmp_path: Path):
        """Two events for the same path must only produce one submission."""
        root = self._make_root(tmp_path)
        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)
        watcher = RecordingsWatcher(
            root, manager, backend, stability_interval=0.01, stability_checks=1
        )

        wav = root / "to-process" / "topic--RAW.wav"
        wav.write_bytes(b"audio")

        # First claim succeeds; simulate a concurrent-event re-entry
        # by firing a second _on_file_event while the first is still held.
        assert watcher._state.try_claim(wav) is True
        try:
            watcher._on_file_event(wav)
            time.sleep(0.05)
            # The second event was rejected because the claim is held.
            assert backend.submit_calls == []
        finally:
            watcher._state.release(wav)

    def test_error_callback_on_missing_file(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)
        errors: list[tuple[Path, str]] = []

        watcher = RecordingsWatcher(
            root,
            manager,
            backend,
            stability_interval=0.01,
            stability_checks=1,
            on_error=lambda path, err: errors.append((path, err)),
        )

        # Nonexistent file → FileNotFoundError in stability check
        watcher._dispatch(root / "to-process" / "missing--RAW.wav")

        assert len(errors) == 1
        assert "disappeared" in errors[0][1]
        assert backend.submit_calls == []

    def test_error_callback_on_backend_failure(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        backend = _FakeBackend(accepted_suffix=".wav", fail=True)
        manager = _make_manager(root, backend)

        watcher = RecordingsWatcher(
            root, manager, backend, stability_interval=0.01, stability_checks=1
        )

        wav = root / "to-process" / "topic--RAW.wav"
        wav.write_bytes(b"audio")

        watcher._dispatch(wav)

        # Backend was called; the job is FAILED but that is not a watcher
        # error — the watcher only fires on_error for its own failures
        # (stability / dispatch). Job failures surface through the bus.
        assert backend.submit_calls == [wav]


# ------------------------------------------------------------------
# _WatchHandler
# ------------------------------------------------------------------


class TestWatchHandler:
    def _make(self, tmp_path: Path) -> RecordingsWatcher:
        backend = _FakeBackend()
        manager = _make_manager(tmp_path, backend)
        return RecordingsWatcher(tmp_path, manager, backend)

    def test_on_created_delegates_to_watcher(self, tmp_path: Path):
        watcher = self._make(tmp_path)
        watcher._on_file_event = MagicMock()  # type: ignore[method-assign]

        handler = _WatchHandler(watcher)
        event = MagicMock(is_directory=False, src_path=str(tmp_path / "test.wav"))
        handler.on_created(event)

        watcher._on_file_event.assert_called_once_with(tmp_path / "test.wav")

    def test_on_created_ignores_directories(self, tmp_path: Path):
        watcher = self._make(tmp_path)
        watcher._on_file_event = MagicMock()  # type: ignore[method-assign]

        handler = _WatchHandler(watcher)
        event = MagicMock(is_directory=True, src_path=str(tmp_path / "subdir"))
        handler.on_created(event)

        watcher._on_file_event.assert_not_called()

    def test_on_moved_uses_dest_path(self, tmp_path: Path):
        watcher = self._make(tmp_path)
        watcher._on_file_event = MagicMock()  # type: ignore[method-assign]

        handler = _WatchHandler(watcher)
        event = MagicMock(
            is_directory=False,
            src_path=str(tmp_path / "old.wav"),
            dest_path=str(tmp_path / "new--RAW.wav"),
        )
        handler.on_moved(event)

        watcher._on_file_event.assert_called_once_with(tmp_path / "new--RAW.wav")


# ------------------------------------------------------------------
# Integration: live watcher with real filesystem events
# ------------------------------------------------------------------


class TestWatcherLiveEvents:
    """Tests that exercise the real watchdog Observer with short timeouts."""

    def test_detects_new_accepted_file(self, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = root / "to-process"

        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)
        submitted_event = threading.Event()

        watcher = RecordingsWatcher(
            root,
            manager,
            backend,
            stability_interval=0.05,
            stability_checks=2,
            on_submitted=lambda job: submitted_event.set(),
        )
        watcher.start()

        try:
            wav = tp / "lecture--RAW.wav"
            wav.write_bytes(b"processed audio content")

            assert submitted_event.wait(timeout=5.0), "Watcher did not submit"
            assert len(backend.submit_calls) == 1
        finally:
            watcher.stop()

    def test_ignores_file_backend_rejects(self, tmp_path: Path):
        """A watched file that accepts_file rejects never reaches the manager."""
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = root / "to-process"

        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)
        watcher = RecordingsWatcher(
            root,
            manager,
            backend,
            stability_interval=0.05,
            stability_checks=2,
        )
        watcher.start()

        try:
            # Create a file the backend doesn't accept.
            irrelevant = tp / "lecture--RAW.mp4"
            irrelevant.write_bytes(b"video content")

            # Give the watcher a moment to see the event and decide.
            time.sleep(0.5)
            assert backend.submit_calls == []
        finally:
            watcher.stop()


# ------------------------------------------------------------------
# Scan existing files on start
# ------------------------------------------------------------------


class TestScanExisting:
    def test_scan_finds_pre_existing_files(self, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = root / "to-process"

        # Create a file BEFORE starting the watcher
        wav = tp / "lecture--RAW.wav"
        wav.write_bytes(b"audio data")

        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)
        submitted_event = threading.Event()

        watcher = RecordingsWatcher(
            root,
            manager,
            backend,
            stability_interval=0.05,
            stability_checks=2,
            on_submitted=lambda job: submitted_event.set(),
        )
        watcher.start()

        try:
            assert submitted_event.wait(timeout=5.0), "Pre-existing file not picked up"
            assert len(backend.submit_calls) == 1
            assert backend.submit_calls[0] == wav
        finally:
            watcher.stop()

    def test_scan_ignores_rejected_files(self, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = root / "to-process"

        # Only .wav accepted, but we put .mp4
        (tp / "lecture--RAW.mp4").write_bytes(b"video data")

        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)
        watcher = RecordingsWatcher(
            root, manager, backend, stability_interval=0.05, stability_checks=2
        )
        watcher.start()

        try:
            time.sleep(0.5)
            assert backend.submit_calls == []
        finally:
            watcher.stop()

    def test_submitted_file_not_resubmitted(self, tmp_path: Path):
        """After dispatch completes, a stop+start cycle should not re-submit."""
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = root / "to-process"

        wav = tp / "lecture--RAW.wav"
        wav.write_bytes(b"audio data")

        backend = _FakeBackend(accepted_suffix=".wav")
        manager = _make_manager(root, backend)
        submitted_event = threading.Event()

        watcher = RecordingsWatcher(
            root,
            manager,
            backend,
            stability_interval=0.05,
            stability_checks=2,
            on_submitted=lambda job: submitted_event.set(),
        )
        watcher.start()

        try:
            assert submitted_event.wait(timeout=5.0)
            assert len(backend.submit_calls) == 1
        finally:
            watcher.stop()

        # Stop and restart — the file should NOT be re-submitted
        submitted_event.clear()
        watcher.start()
        try:
            time.sleep(0.5)
            assert len(backend.submit_calls) == 1  # still just the one
        finally:
            watcher.stop()
