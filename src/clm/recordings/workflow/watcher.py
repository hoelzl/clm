"""Filesystem watcher for the recording workflow.

Monitors ``to-process/`` for new files and hands them to a
:class:`~clm.recordings.workflow.job_manager.JobManager` for
backend-specific processing. The watcher is backend-agnostic: it asks
the active backend ``accepts_file(path)`` on every filesystem event
and, for accepted files, waits for the file to become size-stable and
then calls ``job_manager.submit(path)`` on a background thread.

All the per-mode branching that existed before Phase B (separate
``_handle_external`` / ``_handle_onnx`` paths) is gone — the backend is
responsible for deciding which files trigger work.

The watcher runs on a background thread via ``watchdog``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger
from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from clm.recordings.workflow.backends.base import ProcessingBackend
from clm.recordings.workflow.directories import to_process_dir
from clm.recordings.workflow.job_manager import JobManager
from clm.recordings.workflow.jobs import ProcessingJob, ProcessingOptions


class WatcherState:
    """Thread-safe container for watcher runtime state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processing: set[Path] = set()
        self._submitted: set[Path] = set()

    def try_claim(self, path: Path) -> bool:
        """Claim a file for processing.  Returns False if already claimed or submitted."""
        with self._lock:
            if path in self._processing or path in self._submitted:
                return False
            self._processing.add(path)
            return True

    def mark_submitted(self, path: Path) -> None:
        """Record that a file has been submitted to the job manager."""
        with self._lock:
            self._submitted.add(path)

    def release(self, path: Path) -> None:
        with self._lock:
            self._processing.discard(path)


class RecordingsWatcher:
    """Filesystem watcher that monitors ``to-process/`` and submits jobs.

    Args:
        root_dir: Recordings root (``to-process/``, ``final/``, ``archive/``).
        job_manager: The manager that owns job lifecycle. Every file the
            active backend accepts is passed to ``job_manager.submit``.
        backend: The active processing backend. The watcher uses its
            :meth:`~ProcessingBackend.accepts_file` to decide which
            filesystem events trigger a submission. Must be the same
            backend the ``job_manager`` was built with.
        stability_interval: Seconds between file-size polls.
        stability_checks: Consecutive identical readings = stable.
        on_submitted: Callback invoked with the :class:`ProcessingJob`
            after a successful submission. Optional; used by the web
            app to nudge the SSE queue.
        on_error: Callback on watcher-side failure (stability check
            timeout, submission exception). Receives ``(path, message)``.
    """

    def __init__(
        self,
        root_dir: Path,
        job_manager: JobManager,
        backend: ProcessingBackend,
        *,
        stability_interval: float = 2.0,
        stability_checks: int = 3,
        on_submitted: Callable[[ProcessingJob], None] | None = None,
        on_error: Callable[[Path, str], None] | None = None,
    ) -> None:
        self._root = root_dir
        self._job_manager = job_manager
        self._backend = backend
        self._stability_interval = stability_interval
        self._stability_checks = stability_checks
        self._on_submitted = on_submitted
        self._on_error = on_error

        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._state = WatcherState()

    @property
    def running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    @property
    def backend_name(self) -> str:
        """Machine id of the active backend (``"onnx"``, ``"external"``, …)."""
        return self._backend.capabilities.name

    def start(self) -> None:
        """Start watching ``to-process/`` for new files."""
        if self.running:
            return

        watch_dir = to_process_dir(self._root)
        watch_dir.mkdir(parents=True, exist_ok=True)

        handler = _WatchHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(watch_dir), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        logger.info(
            "Watcher started (backend={}, dir={})",
            self.backend_name,
            watch_dir,
        )
        self._scan_existing()

    def stop(self) -> None:
        """Stop the watcher."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
            logger.info("Watcher stopped")

    # ------------------------------------------------------------------
    # Called by the event handler (on background threads)
    # ------------------------------------------------------------------

    def _on_file_event(self, path: Path) -> None:
        """Respond to a new or moved file in ``to-process/``.

        Asks the backend whether this file is interesting and, if so,
        claims it and spawns a background thread that waits for
        stability and then submits a job.
        """
        if not self._backend.accepts_file(path):
            return

        if not self._state.try_claim(path):
            return

        threading.Thread(
            target=self._dispatch,
            args=(path,),
            daemon=True,
            name=f"watcher-dispatch-{path.stem}",
        ).start()

    def _dispatch(self, path: Path) -> None:
        """Wait for stability, then submit to the job manager."""
        try:
            self._wait_for_stable(path)
            job = self._job_manager.submit(path, options=ProcessingOptions())
            self._state.mark_submitted(path)
            if self._on_submitted is not None:
                self._on_submitted(job)
        except Exception as exc:
            logger.error("Watcher dispatch failed for {}: {}", path.name, exc)
            if self._on_error is not None:
                self._on_error(path, str(exc))
        finally:
            self._state.release(path)

    def _scan_existing(self) -> None:
        """Walk ``to-process/`` and dispatch any files the backend accepts.

        Called once after the observer starts so that files created before
        the watcher was running are picked up.  The ``_submitted`` set
        prevents double-processing if the observer also sees the file.
        """
        watch_dir = to_process_dir(self._root)
        for path in sorted(watch_dir.rglob("*")):
            if path.is_file():
                self._on_file_event(path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _wait_for_stable(self, path: Path) -> None:
        """Poll file size until it stops changing.

        Raises:
            FileNotFoundError: If the file disappears during polling.
        """
        prev_size = -1
        stable_count = 0

        while stable_count < self._stability_checks:
            if not path.exists():
                raise FileNotFoundError(f"File disappeared during stability check: {path}")

            size = path.stat().st_size
            if size == prev_size and size > 0:
                stable_count += 1
            else:
                stable_count = 0
            prev_size = size

            if stable_count < self._stability_checks:
                time.sleep(self._stability_interval)


class _WatchHandler(FileSystemEventHandler):
    """Watchdog event handler that delegates to :class:`RecordingsWatcher`."""

    def __init__(self, watcher: RecordingsWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._watcher._on_file_event(Path(str(event.src_path)))

    def on_moved(self, event: FileMovedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        # Treat the destination as a new file
        self._watcher._on_file_event(Path(str(event.dest_path)))
