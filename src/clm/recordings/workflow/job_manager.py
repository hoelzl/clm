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

#: HTTP status codes that make a poll error permanent (won't recover
#: on retry). Everything else — including 5xx, 408, 429, network
#: errors, and schema drift — is treated as transient so the next
#: poll cycle can retry.
#:
#: * 401 Unauthorized — API key is wrong or revoked.
#: * 403 Forbidden — account lacks permission for this action.
#: * 404 Not Found — the backend production has been deleted.
#: * 410 Gone — same as 404 but upstream is explicit about it.
_PERMANENT_POLL_HTTP_STATUSES = frozenset({401, 403, 404, 410})


def _is_permanent_poll_error(exc: BaseException) -> bool:
    """Return ``True`` if *exc* will not be fixed by retrying the poll.

    Used by :meth:`JobManager._poll_once` to decide whether a poll
    exception should drive the job to :attr:`JobState.FAILED`
    immediately or just be recorded on
    :attr:`~clm.recordings.workflow.jobs.ProcessingJob.last_poll_error`
    so the next tick can retry. The rationale for the specific
    classification lives on :data:`_PERMANENT_POLL_HTTP_STATUSES`.

    We deliberately treat **unknown exceptions** as transient: losing
    a completed Auphonic production to an unhandled blip is worse
    than leaving a stuck job in the list for a user to notice. The
    ``last_poll_error`` field makes the stuck state visible.
    """
    # Lazy import to avoid a hard dependency on the auphonic backend
    # from the manager module (other backends may not be installed).
    try:
        from clm.recordings.workflow.backends.auphonic_client import AuphonicHTTPError
    except ImportError:  # pragma: no cover — defensive
        return False

    if isinstance(exc, AuphonicHTTPError):
        return exc.status_code in _PERMANENT_POLL_HTTP_STATUSES
    return False


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

    def request_poll_soon(self) -> None:
        """Wake the manager's poller loop — see :meth:`JobManager.request_poll_soon`."""
        self._manager.request_poll_soon()


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
        # Fires when a backend calls ``request_poll_soon`` — or on
        # shutdown, so the poller wakes promptly instead of sleeping
        # out the remainder of its interval.
        self._wake = threading.Event()

        # Rehydrate in-flight jobs from disk. PROCESSING jobs will be
        # re-polled on the next poller tick; UPLOADING jobs need to be
        # classified by whether a production already exists upstream.
        # COMPLETED/FAILED/CANCELLED jobs are loaded into memory too so
        # ``list_jobs()`` can surface recent history.
        for job in store.load_all():
            if job.state == JobState.UPLOADING:
                if job.backend_ref:
                    # A production was created upstream before the crash.
                    # Move to PROCESSING so the next poll (or a user-
                    # triggered Verify) can pick the state back up from
                    # the backend rather than assuming the work is lost.
                    job.state = JobState.PROCESSING
                    job.message = "Resumed after restart — checking upstream"
                    job.last_poll_error = None
                else:
                    # No upstream handle: the upload never made it past
                    # step 1. The raw is still on disk, so this is a
                    # genuine "please retry" case.
                    job.state = JobState.FAILED
                    job.error = (
                        "Upload was interrupted before the production was created. "
                        "Please re-submit the recording."
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

    def reconcile(self, job_id: str) -> ProcessingJob | None:
        """Run the backend's reconcile hook for *job_id*.

        Returns the updated job, or ``None`` if the id is unknown.
        Works on any state (including terminal) so a stuck ``FAILED``
        job whose work actually completed upstream can be resurrected.
        Any exception from the backend is classified like a poll:
        permanent errors drive the job to ``FAILED``; transient ones
        are recorded on ``last_poll_error`` and the state is left alone.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None

        ctx = self._make_context()
        try:
            updated = self._backend.reconcile(job, ctx=ctx)
        except Exception as exc:
            if _is_permanent_poll_error(exc):
                logger.error(
                    "Permanent reconcile error for {}, marking FAILED: {}",
                    job.id,
                    exc,
                )
                job.state = JobState.FAILED
                job.error = str(exc)
                job.last_poll_error = None
            else:
                logger.warning(
                    "Transient reconcile error for {} (caller can retry): {}",
                    job.id,
                    exc,
                )
                job.last_poll_error = str(exc)
            job.touch()
            self._store_job(job)
            self._bus.publish(JOB_EVENT_TOPIC, job)
            return job

        self._store_job(updated)
        self._bus.publish(JOB_EVENT_TOPIC, updated)
        return updated

    def mark_failed(self, job_id: str, *, reason: str) -> ProcessingJob | None:
        """Manually transition *job_id* to :attr:`JobState.FAILED`.

        Unlike :meth:`cancel`, this does **not** call the backend's
        ``cancel`` hook — the remote production (e.g. an Auphonic
        production) is left untouched so the user can still download
        it manually or inspect it upstream. Intended for rescuing
        stuck jobs whose backend work is fine but whose local poll
        loop is wedged (e.g. repeated transient errors that aren't
        going to clear on their own).

        Refuses already-terminal jobs so users can't accidentally
        overwrite a genuine COMPLETED/CANCELLED state with a manual
        FAILED.

        Args:
            job_id: The job to transition.
            reason: Stored on ``job.error`` so it shows up in
                ``jobs list``. Required — no silent defaults.

        Returns:
            The updated :class:`ProcessingJob`, or ``None`` if the id
            is unknown or the job is already terminal.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.is_terminal:
                logger.warning(
                    "Refusing to mark already-terminal job {} as failed (current state={})",
                    job.id,
                    job.state.value,
                )
                return None
            job.state = JobState.FAILED
            job.error = reason
            job.last_poll_error = None
            job.touch()
        self._store_job(job)
        self._bus.publish(JOB_EVENT_TOPIC, job)
        return job

    def delete_job(self, job_id: str) -> bool:
        """Remove *job_id* from memory and the on-disk store.

        Refuses to delete in-flight jobs (queued/uploading/processing/
        downloading) — callers should cancel them first. Safe to call
        for unknown ids; returns ``False`` in that case.

        Returns:
            ``True`` if a job was actually removed, ``False`` if the
            id was unknown or the job was in-flight (not deleted).
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if not job.is_terminal:
                logger.warning(
                    "Refusing to delete in-flight job {} (state={})",
                    job.id,
                    job.state.value,
                )
                return False
            del self._jobs[job_id]
        self._store.delete(job_id)
        return True

    def shutdown(self, *, timeout: float | None = 5.0) -> None:
        """Stop the poller thread and wait for it to exit.

        Safe to call multiple times and from any thread.
        """
        self._stop.set()
        # Also wake the poller so it doesn't sit out the rest of its
        # sleep interval before seeing ``_stop``.
        self._wake.set()
        poller = self._poller
        if poller is not None and poller.is_alive():
            poller.join(timeout=timeout)

    def request_poll_soon(self) -> None:
        """Ask the poller to run again on the next scheduler tick.

        Called by async backends (via :class:`JobContext`) after an
        in-band state transition so the dashboard sees the new state
        without waiting out the full ``poll_interval``. Safe to call
        before the poller has started — the wake flag stays set until
        the first loop iteration consumes it. No-op when the backend
        is synchronous.
        """
        if self._backend.capabilities.is_synchronous:
            return
        self._wake.set()

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
        self._wake.clear()
        self._poller = threading.Thread(
            target=self._poller_loop,
            name="clm-job-poller",
            daemon=True,
        )
        self._poller.start()
        logger.debug("JobManager poller started (interval={}s)", self._poll_interval)

    def _poller_loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            # Wait for the normal interval, an explicit wake-up, or
            # shutdown — whichever comes first. ``_wake`` is shared by
            # ``request_poll_soon`` and ``shutdown``; inspecting
            # ``_stop`` at the top of the loop distinguishes them.
            self._wake.wait(self._poll_interval)
            self._wake.clear()

    def poll_once(self, *, job_id: str | None = None) -> list[ProcessingJob]:
        """Run a single poll cycle and return the jobs that were polled.

        By default polls every in-flight job (``PROCESSING``,
        ``UPLOADING``, ``DOWNLOADING``). Passing *job_id* narrows to a
        single job — useful for the ``clm recordings jobs poll`` CLI
        which lets the user drive one specific job without running the
        dashboard. Unknown or terminal ids are silently skipped (no
        error) so a prefix-match caller doesn't have to re-check.

        On exception from the backend, the error is classified:

        * Permanent errors (see :func:`_is_permanent_poll_error`) drive
          the job to :attr:`JobState.FAILED` with ``error`` set.
        * Transient errors (network blips, HTTP 5xx, schema drift,
          unknown exceptions) are recorded on
          :attr:`ProcessingJob.last_poll_error` but the job's
          ``state`` is left unchanged — the next tick will retry.

        Both permanent and transient error paths persist the job and
        publish a ``job`` event so the UI stays in sync.

        Returns:
            The list of ``ProcessingJob`` instances that were polled,
            in their post-tick state. Empty if there were no in-flight
            jobs (or if *job_id* didn't match a pollable job).
        """
        with self._lock:
            if job_id is not None:
                candidate = self._jobs.get(job_id)
                in_flight = (
                    [candidate]
                    if candidate is not None
                    and candidate.state
                    in (JobState.PROCESSING, JobState.UPLOADING, JobState.DOWNLOADING)
                    else []
                )
            else:
                in_flight = [
                    job
                    for job in self._jobs.values()
                    if job.state in (JobState.PROCESSING, JobState.UPLOADING, JobState.DOWNLOADING)
                ]

        if not in_flight:
            return []

        polled: list[ProcessingJob] = []
        ctx = self._make_context()
        for job in in_flight:
            try:
                updated = self._backend.poll(job, ctx=ctx)
            except Exception as exc:
                if _is_permanent_poll_error(exc):
                    logger.error(
                        "Permanent poll error for {}, marking FAILED: {}",
                        job.id,
                        exc,
                    )
                    job.state = JobState.FAILED
                    job.error = str(exc)
                    job.last_poll_error = None
                else:
                    logger.warning(
                        "Transient poll error for {} (will retry next tick): {}",
                        job.id,
                        exc,
                    )
                    job.last_poll_error = str(exc)
                job.touch()
                self._store_job(job)
                self._bus.publish(JOB_EVENT_TOPIC, job)
                polled.append(job)
                continue

            # Successful poll: clear any lingering transient-error
            # marker so the user sees a clean state next time they
            # run `clm recordings jobs list`.
            updated.last_poll_error = None
            self._store_job(updated)
            self._bus.publish(JOB_EVENT_TOPIC, updated)
            polled.append(updated)
        return polled

    # Kept for backwards compatibility with any external caller that
    # was reaching into the manager. New code should call ``poll_once``.
    def _poll_once(self) -> None:
        self.poll_once()
