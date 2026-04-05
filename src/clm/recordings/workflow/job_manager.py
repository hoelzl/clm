"""Central job coordinator for the recordings workflow.

The :class:`JobManager` is the only thing that mutates
:class:`ProcessingJob` instances. Triggers (CLI, watcher, web) call
:meth:`submit`; backends carry out the work and return updated jobs;
the manager persists each transition and publishes a ``job`` event on
the :class:`EventBus`.

For asynchronous backends (Auphonic), a background poller thread calls
``backend.poll(job, ctx=…)`` on a fixed cadence until the job reaches a
terminal state. The poller is started lazily when a backend declares
``capabilities.is_synchronous == False``.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from loguru import logger

from clm.recordings.workflow.backends.base import JobContext, ProcessingBackend
from clm.recordings.workflow.directories import (
    final_dir,
    to_process_dir,
)
from clm.recordings.workflow.event_bus import EventBus
from clm.recordings.workflow.job_store import JobStore
from clm.recordings.workflow.jobs import (
    JobState,
    ProcessingJob,
    ProcessingOptions,
)
from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX, parse_raw_stem

#: Topic name used for all job-lifecycle events on the bus.
JOB_EVENT_TOPIC = "job"

#: Default poller cadence for asynchronous backends (seconds).
#: Individual backends may override via ``JobManager(poll_interval=…)``.
DEFAULT_POLL_INTERVAL_SECONDS = 30.0


class _DefaultJobContext:
    """Default :class:`JobContext` implementation wired to a manager and bus."""

    def __init__(
        self,
        *,
        manager: JobManager,
        bus: EventBus,
        work_dir: Path,
    ) -> None:
        self._manager = manager
        self._bus = bus
        self._work_dir = work_dir

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    def report(self, job: ProcessingJob) -> None:
        """Persist and publish progress for *job*.

        Backends call this whenever they want the UI to see a transition.
        The manager touches the timestamp, saves, then publishes — in
        that order — so subscribers always see on-disk state.
        """
        job.touch()
        self._manager._store_job(job)
        self._bus.publish(JOB_EVENT_TOPIC, job)


class JobManager:
    """Coordinates triggers and a single backend.

    One manager owns one backend. Swapping backends at runtime is not
    supported; callers construct a new manager when the config changes.

    Args:
        backend: The backend this manager delegates to.
        root_dir: Recordings root (the directory containing
            ``to-process/``, ``final/``, and ``archive/``).
        store: Persistence layer for jobs.
        bus: Event bus for publishing job lifecycle events.
        poll_interval: Seconds between poller iterations for async
            backends. Ignored for synchronous backends.
        raw_suffix: Filename suffix identifying raw recordings (passed
            through to :func:`parse_raw_stem` when deriving final paths).
        work_dir: Optional scratch directory for backends. Defaults to a
            subdirectory of the OS temp dir (not auto-cleaned so jobs
            can inspect intermediates on failure).
    """

    def __init__(
        self,
        *,
        backend: ProcessingBackend,
        root_dir: Path,
        store: JobStore,
        bus: EventBus,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        raw_suffix: str = DEFAULT_RAW_SUFFIX,
        work_dir: Path | None = None,
    ) -> None:
        self._backend = backend
        self._root = root_dir
        self._store = store
        self._bus = bus
        self._poll_interval = poll_interval
        self._raw_suffix = raw_suffix
        self._work_dir = work_dir or Path(tempfile.gettempdir()) / "clm-recordings-jobs"
        self._work_dir.mkdir(parents=True, exist_ok=True)

        self._jobs: dict[str, ProcessingJob] = {}
        self._lock = threading.RLock()
        self._poller: threading.Thread | None = None
        self._stop = threading.Event()

        # Rehydrate in-flight jobs from disk. PROCESSING jobs will be
        # re-polled on the next poller tick; UPLOADING jobs are stale
        # (the upload endpoint isn't resumable), so we fail them with
        # a clear message. COMPLETED/FAILED/CANCELLED jobs are loaded
        # into memory too so list_jobs() can surface recent history.
        for job in store.load_all():
            if job.state == JobState.UPLOADING:
                job.state = JobState.FAILED
                job.error = (
                    "Upload was interrupted by a process restart. Please re-submit the recording."
                )
                job.touch()
                self._store.save(job)
            self._jobs[job.id] = job

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def backend(self) -> ProcessingBackend:
        return self._backend

    def submit(
        self,
        raw_path: Path,
        *,
        options: ProcessingOptions | None = None,
    ) -> ProcessingJob:
        """Start a new job for *raw_path*.

        Blocks for synchronous backends (the job is terminal on return).
        Returns early for asynchronous backends; the poller loop carries
        the job to completion.
        """
        options = options or ProcessingOptions()
        final_path = self._derive_final_path(raw_path)
        ctx = self._make_context()

        job = self._backend.submit(
            raw_path,
            final_path,
            options=options,
            ctx=ctx,
        )
        self._store_job(job)
        self._bus.publish(JOB_EVENT_TOPIC, job)

        # Lazily start the poller the first time we accept a non-terminal
        # job from an async backend. Synchronous backends never need it.
        if (
            not self._backend.capabilities.is_synchronous
            and not job.is_terminal
            and self._poller is None
        ):
            self._start_poller()

        return job

    def list_jobs(self) -> list[ProcessingJob]:
        """Return all known jobs, newest first."""
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def get(self, job_id: str) -> ProcessingJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> ProcessingJob | None:
        """Cancel *job_id* and return the updated job (or None if unknown)."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.is_terminal:
            return job

        ctx = self._make_context()
        try:
            self._backend.cancel(job, ctx=ctx)
        except Exception as exc:
            logger.warning("Backend cancel raised for {}: {}", job.id, exc)

        job.state = JobState.CANCELLED
        job.message = "Cancelled"
        job.touch()
        self._store_job(job)
        self._bus.publish(JOB_EVENT_TOPIC, job)
        return job

    def shutdown(self, *, timeout: float | None = 5.0) -> None:
        """Stop the poller thread and wait for it to exit.

        Safe to call multiple times and from any thread.
        """
        self._stop.set()
        poller = self._poller
        if poller is not None and poller.is_alive():
            poller.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _store_job(self, job: ProcessingJob) -> None:
        """Insert or update *job* in memory and on disk."""
        with self._lock:
            self._jobs[job.id] = job
        self._store.save(job)

    def _make_context(self) -> JobContext:
        return _DefaultJobContext(
            manager=self,
            bus=self._bus,
            work_dir=self._work_dir,
        )

    def _derive_final_path(self, raw_path: Path) -> Path:
        """Compute the planned ``final/`` output path for *raw_path*.

        Strips the raw suffix from the filename and places the result
        under ``final/<relative_dir>/``. If *raw_path* is outside the
        ``to-process/`` tree, the final file is placed at the root of
        ``final/``.
        """
        base_name, _ = parse_raw_stem(raw_path.stem, self._raw_suffix)
        tp = to_process_dir(self._root)
        try:
            relative_dir = raw_path.parent.relative_to(tp)
        except ValueError:
            relative_dir = Path()
        return final_dir(self._root) / relative_dir / f"{base_name}.mp4"

    # ------------------------------------------------------------------
    # Async poller
    # ------------------------------------------------------------------

    def _start_poller(self) -> None:
        if self._poller is not None:
            return
        self._stop.clear()
        self._poller = threading.Thread(
            target=self._poller_loop,
            name="clm-job-poller",
            daemon=True,
        )
        self._poller.start()
        logger.debug("JobManager poller started (interval={}s)", self._poll_interval)

    def _poller_loop(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        with self._lock:
            in_flight = [
                job
                for job in self._jobs.values()
                if job.state in (JobState.PROCESSING, JobState.UPLOADING, JobState.DOWNLOADING)
            ]

        if not in_flight:
            return

        ctx = self._make_context()
        for job in in_flight:
            try:
                updated = self._backend.poll(job, ctx=ctx)
            except Exception as exc:
                logger.exception("Poll failed for {}: {}", job.id, exc)
                job.state = JobState.FAILED
                job.error = str(exc)
                job.touch()
                self._store_job(job)
                self._bus.publish(JOB_EVENT_TOPIC, job)
                continue

            self._store_job(updated)
            self._bus.publish(JOB_EVENT_TOPIC, updated)
