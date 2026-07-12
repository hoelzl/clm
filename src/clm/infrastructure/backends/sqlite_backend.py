"""SQLite-based backend for job queue orchestration.

This backend submits jobs to a SQLite database and waits for workers to complete them.
It's a simpler alternative to the RabbitMQ-based FastStreamBackend.
"""

import asyncio
import json
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from attrs import define, field

from clm.core.output_write_registry import (
    WriteOutcome,
    is_image_path,
)
from clm.infrastructure.backend import JobsPendingTimeoutError
from clm.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clm.infrastructure.build_profiling import now as profiler_now
from clm.infrastructure.build_profiling import profiler
from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.operation import Operation
from clm.infrastructure.utils.path_utils import atomic_write_bytes
from clm.infrastructure.workers.progress_tracker import ProgressTracker, get_progress_tracker_config

if TYPE_CHECKING:
    from clm.cli.build_data_classes import BuildWarning
    from clm.cli.build_reporter import BuildReporter
    from clm.infrastructure.database.execution_telemetry import ExecutionTelemetryStore
    from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData
    from clm.infrastructure.utils.copy_file_data import CopyFileData

logger = logging.getLogger(__name__)


# Max concurrent ``execute_operation`` calls (see ``_ensure_submission_semaphore``).
# Small on purpose: the submit thread is single, and a tighter cap keeps any
# on-loop cache-hit-replay burst short so the progress bar stays responsive.
SUBMISSION_CONCURRENCY = 8


@define
class SqliteBackend(LocalOpsBackend):
    """SQLite-based backend for job queue orchestration.

    This backend submits jobs to a SQLite database and waits for
    workers to complete them. It's a simpler alternative to the
    RabbitMQ-based FastStreamBackend.
    """

    db_path: Path = Path("clm_jobs.db")
    workspace_path: Path = Path.cwd()
    job_queue: JobQueue | None = field(init=False, default=None)
    db_manager: DatabaseManager | None = None
    ignore_db: bool = False
    active_jobs: dict[int, dict] = field(factory=dict)  # job_id -> job info
    poll_interval: float = 0.5  # seconds
    max_wait_for_completion_duration: float = 1200.0  # 20 minutes
    progress_tracker: ProgressTracker | None = field(init=False, default=None)
    enable_progress_tracking: bool = True
    skip_worker_check: bool = False  # Skip worker availability check (for unit tests only)
    build_reporter: Optional["BuildReporter"] = None  # Optional build reporter for improved output
    incremental: bool = False  # Incremental mode: skip writing cached results
    # When True (``clm build --explain-rebuilds`` / ``CLM_EXPLAIN_REBUILDS``),
    # a ``processed_files`` cache MISS runs one extra read-only probe to log
    # *why* the deck is being rebuilt (no cache entry / content-hash changed /
    # new output target). Off by default so a normal build pays nothing — the
    # miss path stays a single boolean check.
    explain_rebuilds: bool = False
    # Persistent per-deck execution telemetry (issue #330). When set, the
    # backend records retry/crash telemetry the notebook worker attached to
    # its results (completed jobs: ``execution_telemetry`` warnings; failed
    # jobs: the structured error JSON) and reports passed-only-after-retry
    # decks to the build summary's flake list.
    telemetry_store: Optional["ExecutionTelemetryStore"] = None
    # Per-job-type worker execution mode ('docker'/'direct') from this build's
    # resolved worker config. Jobs are tagged with it on submission so only
    # workers of that mode claim them — a Direct worker from a concurrent
    # build sharing the same jobs DB must never take a job that needs the
    # Docker image's toolchain (e.g. the xeus-cpp kernel; the cause of the
    # spurious "No such kernel named xcpp20" failures). An empty dict leaves
    # jobs untagged (legacy behaviour, used by tests).
    worker_execution_modes: dict[str, str] = field(factory=dict)
    # The owning WorkerLifecycleManager session (issue #597). Pre-registered
    # worker rows are stamped with their session_id (issue #594), and the
    # activation-timeout dead-marking must not condemn another session's
    # still-starting workers — only rows this session owns (or unowned legacy
    # rows) may be marked dead. None (tests, legacy callers) restricts the
    # dead-marking to the execution-mode filter only.
    worker_session_id: str | None = None

    # Background result-cache writer (lazy). Retiring a completed job by reading
    # its output back, pickling it, and committing the blob to the cache DB is
    # the slow part of the poll loop; doing it inline made the single consumer
    # fall behind the parallel workers, so the progress bar stalled mid-stage
    # and only caught up once the workers idled. These hold a queue + a single
    # daemon writer thread that drains that work off the poll loop. The queue is
    # joined at the end of each wait_for_completion (so the cache is fully
    # populated when it returns) and the thread is stopped in shutdown.
    _result_cache_queue: Any = field(init=False, default=None)
    _result_cache_thread: Any = field(init=False, default=None)

    # Bounded thread pool for the synchronous job-submission tail (job-cache
    # probe, worker-availability wait, payload JSON serialization, jobs-DB
    # INSERT). That work has no await and used to run inline on the event loop,
    # so a submission burst starved the completion poll loop and froze the
    # progress bar (the bar only advances from that poll loop). Offloading it
    # keeps the loop free to poll. Small + bounded because the jobs DB
    # serializes writers anyway, so more threads only add lock contention.
    _submit_executor: "ThreadPoolExecutor | None" = field(init=False, default=None)

    # Caps how many operations are processed concurrently, bounding the on-loop
    # work that runs per event-loop turn. The cache-HIT replay path still runs
    # on the loop (it reads the result cache and mutates the non-thread-safe
    # output registry), so without a cap a burst of cache hits blocks the poll
    # loop for the whole batch. The gate keeps any such burst small, and adds
    # backpressure so submission does not build the whole course's payloads
    # in memory at once. Created lazily on the event loop.
    _submission_semaphore: "asyncio.Semaphore | None" = field(init=False, default=None)

    def __attrs_post_init__(self):
        """Initialize SQLite database and job queue."""
        # Database should already be initialized, but ensure it exists
        init_database(self.db_path)
        self.job_queue = JobQueue(self.db_path)
        logger.info(f"Initialized SQLite backend with database: {self.db_path}")

        # Initialize progress tracker if enabled
        if self.enable_progress_tracking:
            config = get_progress_tracker_config()

            # Add progress callback if build_reporter exists
            if self.build_reporter:
                config["on_progress_update"] = self.build_reporter.on_progress_update

            self.progress_tracker = ProgressTracker(**config)
            logger.debug("Progress tracking enabled")

    async def __aenter__(self) -> "SqliteBackend":
        """Enter async context manager."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context manager."""
        await self.shutdown()
        return None

    async def start(self):
        """Start the backend and perform session-start cleanup if configured."""
        logger.debug("SQLite backend started")

        # Perform session-start cleanup if configured
        self._perform_session_start_cleanup()

    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        """Submit a job to the SQLite queue (timing wrapper).

        The real work lives in :meth:`_execute_operation_impl`. This thin
        wrapper only measures the call under ``CLM_PROFILE_BUILD`` — this is
        the awaitless submission body whose synchronous cost (payload
        serialization, cache lookup, INSERT, or cache-hit unpickle+write)
        starves the completion poll loop and freezes the progress bar.
        """
        async with self._ensure_submission_semaphore():
            # Time inside the gate so the (off-loop) semaphore wait is not
            # counted as on-loop blocking.
            if not profiler.enabled:
                await self._execute_operation_impl(operation, payload)
                return
            start = profiler_now()
            cache_hit = False
            try:
                cache_hit = await self._execute_operation_impl(operation, payload)
            finally:
                profiler.record_execute_op(profiler_now() - start, cache_hit)

    def _ensure_submission_semaphore(self) -> asyncio.Semaphore:
        """Lazily create the per-operation concurrency gate on the event loop.

        Bounds how many ``execute_operation`` calls run concurrently. The
        cache-HIT replay path runs on the loop (result-cache read + non-thread-
        safe registry mutation), so this cap limits how much of it can pile into
        a single event-loop turn and stall the completion poll loop, and it
        backpressures submission so the whole course's payloads are not built in
        memory at once. Misses spend their time awaiting the single submit
        thread, so a handful of permits keeps that thread saturated without
        bloating its queue.
        """
        sem = self._submission_semaphore
        if sem is None:
            sem = asyncio.Semaphore(SUBMISSION_CONCURRENCY)
            self._submission_semaphore = sem
        return sem

    async def _execute_operation_impl(self, operation: Operation, payload: Payload) -> bool:
        """Submit a job to the SQLite queue.

        Returns ``True`` when the operation was satisfied from a cache (DB
        result cache or SQLite job cache) without submitting a worker job,
        ``False`` when a new job was enqueued. The boolean is only consumed by
        the profiling wrapper.

        Args:
            operation: Operation to execute
            payload: Payload data for the job
        """
        # Map service to job type for cache hit reporting
        service_to_job_type = {
            "notebook-processor": "notebook",
            "drawio-converter": "drawio",
            "plantuml-converter": "plantuml",
            "jupyterlite-builder": "jupyterlite",
        }
        service_name = operation.service_name or "unknown"
        job_type = service_to_job_type.get(service_name, "unknown")

        # Check database cache first (processed_files table with full Result objects)
        #
        # ``force_execution`` carries the execution-cache warmup decision to the
        # SQLite job-cache probe in ``_submit_job_blocking``. When
        # ``_can_replay_from_cache`` returns False it is steering a
        # Recording/Speaker HTML payload with a cold ``executed_notebooks`` cache
        # off the replay path to force a worker run that repopulates that cache.
        # The job cache would otherwise short-circuit the very run with a
        # ``jobcache_hit`` and leave the execution cache cold (issue #579), so we
        # propagate the decision instead of letting it be silently defeated.
        force_execution = False
        if not self.ignore_db and self.db_manager:
            result = self.db_manager.get_result(
                payload.input_file, payload.content_hash(), payload.output_metadata()
            )
            can_replay = self._can_replay_from_cache(payload)
            if result and can_replay:
                # In incremental mode, skip writing cached results to disk
                # (they should already exist from a previous build)
                if self.incremental:
                    logger.info(
                        f"Database cache hit for {payload.input_file} -> {payload.output_file} "
                        f"(incremental mode: skipping write)"
                    )
                else:
                    logger.info(
                        f"Database cache hit for {payload.input_file} -> {payload.output_file} "
                        f"(skipping worker execution)"
                    )
                    # Write cached result from database
                    output_file = Path(payload.output_file)
                    # Make path absolute relative to workspace if not already absolute
                    if not output_file.is_absolute():
                        output_file = self.workspace_path / output_file
                    # Register the planned write so identical-content
                    # re-emissions (common when many output variants share
                    # an include-sourced file) collapse to one write, and
                    # path conflicts surface in the build summary. Image
                    # sources are still tracked here so a static
                    # ``img/X.png`` cached replay vs a ``pu/X.pu`` render
                    # to the same output path surfaces as a real content
                    # conflict instead of silent last-writer-wins.
                    content_bytes = result.result_bytes()
                    source_path = Path(payload.input_file)
                    skip_write = False
                    if is_image_path(source_path):
                        self.image_registry.record_output_write(output_file)
                    write_result = self.output_write_registry.record_write(
                        output_file,
                        content=content_bytes,
                        source=source_path,
                    )
                    if write_result.outcome == WriteOutcome.DEDUP:
                        logger.debug(
                            f"Output dedup: skipping cache-hit replay to "
                            f"{output_file} (identical content already written "
                            f"from {write_result.entry.first_writer_source})"
                        )
                        skip_write = True
                    elif write_result.outcome == WriteOutcome.CONFLICT:
                        logger.warning(
                            f"Output path conflict at {output_file}: prior "
                            f"writer {write_result.entry.first_writer_source}, "
                            f"new writer {source_path} (last writer wins)"
                        )
                    elif write_result.outcome == WriteOutcome.LARGE_FILE_COLLISION:
                        logger.debug(
                            f"Large-file collision at {output_file} from "
                            f"{source_path} (over hash limit; counted as "
                            f"collision)"
                        )
                    if not skip_write:
                        # Hash-aware skip: when the destination already
                        # holds byte-identical content from a prior build,
                        # avoid the write so mtime is preserved and git's
                        # stat-cache stays valid.
                        if self.output_write_registry.is_destination_identical(
                            output_file, content=content_bytes
                        ):
                            logger.debug(
                                f"Hash-aware skip: {output_file} already has identical content"
                            )
                        else:
                            # Atomic temp+rename with retry on transient OSError —
                            # plain write_bytes intermittently fails with EINVAL on
                            # Windows when AV/indexer/sync agents hold handles on
                            # files in the same directory.
                            atomic_write_bytes(output_file, content_bytes)
                            logger.debug(f"Wrote cached result to {output_file}")

                # Report any stored errors/warnings for this cached result
                self._report_cached_issues(
                    payload.input_file,
                    payload.content_hash(),
                    payload.output_metadata(),
                )

                # Report cache hit to build reporter for progress tracking
                if self.build_reporter:
                    self.build_reporter.report_cache_hit(
                        str(payload.input_file),
                        job_type,
                        detail="stored result replayed, no execution",
                    )

                return True

            # Replay was blocked to warm a cold ``executed_notebooks`` cache
            # (Recording/Speaker HTML producer). Suppress the downstream
            # job-cache probe too, so the forced worker run is not pre-empted by
            # a ``jobcache_hit`` and the execution cache actually gets
            # repopulated (issue #579).
            if not can_replay:
                force_execution = True

            # Cache MISS on the stored result. When rebuild explanation is
            # enabled, run one extra read-only probe to log why. Only for a
            # genuine ``result is None`` miss — the ``result`` truthy but
            # replay-gated case (Recording HTML producer) already logs its
            # own reason in ``_can_replay_from_cache``.
            if self.explain_rebuilds and result is None:
                self._explain_rebuild(payload, job_type)

        if service_name not in service_to_job_type:
            raise ValueError(f"Unknown service: {service_name}")

        # The remaining submission work — the SQLite job-cache probe, the
        # worker-availability wait (which can ``time.sleep`` for seconds while
        # workers activate), the payload JSON serialization (which re-encodes
        # the base64 sibling blobs), and the jobs-DB INSERT — is fully
        # synchronous. Run inline on the event loop it was the dominant cost
        # that starved the completion poll loop, so a submission burst froze
        # the progress bar (which only advances from that poll loop) while the
        # workers raced ahead. It touches only thread-safe resources (JobQueue
        # keeps a per-thread SQLite connection; model_dump is pure), so we run
        # it on a bounded thread pool and the event loop stays free to poll.
        #
        # Everything AFTER the offload — the registries, the build reporter,
        # the active_jobs dict, and the db_manager-backed cached-issue replay —
        # is confined to the event-loop thread because none of it is
        # thread-safe (the DB result-cache replay above, db_manager.get_result,
        # stays on the loop for the same reason).
        _offload_t0 = profiler_now() if profiler.enabled else 0.0
        outcome, job_id = await asyncio.get_running_loop().run_in_executor(
            self._ensure_submit_executor(),
            self._submit_job_blocking,
            payload,
            job_type,
            force_execution,
        )
        if profiler.enabled:
            profiler.record_submit_offload(profiler_now() - _offload_t0)

        if outcome == "jobcache_hit":
            # Stored output is already on disk (results_cache hit). Replay its
            # issues and report the hit here on the loop thread — db_manager
            # reads and the build reporter are loop-confined. Mirrors the
            # database-cache replay above (issue #321: a cache replay must be
            # observationally equivalent to execution).
            self._report_cached_issues(
                payload.input_file,
                payload.content_hash(),
                payload.output_metadata(),
            )
            if self.build_reporter:
                self.build_reporter.report_cache_hit(
                    str(payload.input_file),
                    job_type,
                    detail="output already on disk, no execution",
                )
            # Register the already-on-disk output so the end-of-build stray
            # sweep keeps it. The sweep deletes any output not in
            # output_write_registry; without this a job-cache hit leaves the
            # valid cached file unregistered and the sweep removes it (issue
            # #577 — recording/speaker HTML for unchanged topics vanished on
            # incremental rebuilds). Mirrors the executed-job registration and
            # the DB-cache path above: the "cache replay is observationally
            # equivalent to execution" invariant (issue #321) must include the
            # write registry, not just issue replay and hit reporting.
            registry_output_path = Path(payload.output_file)
            if not registry_output_path.is_absolute():
                registry_output_path = self.workspace_path / registry_output_path
            if registry_output_path.exists():
                registry_source = Path(payload.input_file)
                if is_image_path(registry_source):
                    self.image_registry.record_output_write(registry_output_path)
                try:
                    self.output_write_registry.record_write(
                        registry_output_path,
                        content_source=registry_output_path,
                        source=registry_source,
                    )
                except Exception as reg_exc:
                    logger.debug(
                        f"Could not register cached output {registry_output_path}: {reg_exc}"
                    )
            return True

        # outcome == "submitted": the job is in the jobs DB. Register it for the
        # poll loop and report it — all loop-confined state.
        assert job_id is not None
        correlation_id = getattr(payload, "correlation_id", None)
        self.active_jobs[job_id] = {
            "job_type": job_type,
            "input_file": str(payload.input_file),
            "output_file": str(payload.output_file),
            "correlation_id": correlation_id,
        }

        # Track in progress tracker
        if self.progress_tracker:
            self.progress_tracker.job_submitted(
                job_id=job_id,
                job_type=job_type,
                input_file=str(payload.input_file),
                correlation_id=correlation_id,
            )

        # Report file started to build reporter (for verbose mode output)
        if self.build_reporter:
            self.build_reporter.report_file_started(str(payload.input_file), job_type, job_id)

        logger.debug(
            f"Added job {job_id} ({job_type}): {payload.input_file} -> {payload.output_file}"
        )
        return False

    def _submit_job_blocking(
        self, payload: Payload, job_type: str, force_execution: bool = False
    ) -> tuple[str, int | None]:
        """Synchronous job-submission tail, run off the event loop.

        Performs the SQLite job-cache probe, the worker-availability wait, the
        payload JSON serialization, and the jobs-DB INSERT. Returns
        ``("jobcache_hit", None)`` when a stored output already satisfies the
        request, or ``("submitted", job_id)`` when a new job was enqueued.

        ``force_execution`` suppresses the job-cache probe so a worker always
        runs. The caller sets it when the execution-cache warmup guard has
        already decided this payload must execute to repopulate
        ``executed_notebooks`` (Recording/Speaker HTML with a cold execution
        cache); without it the job cache would short-circuit the very run the
        guard forced and leave the execution cache cold (issue #579).

        Runs on a :attr:`_submit_executor` thread, so it must touch ONLY
        thread-safe resources: ``JobQueue`` keeps a per-thread SQLite connection
        (so ``check_cache`` / ``_get_available_workers`` / ``add_job`` are safe
        here) and ``model_dump`` is pure. It must NOT touch ``db_manager``, the
        registries, or the build reporter — the caller does the cache-issue
        replay, reporting, and ``active_jobs`` bookkeeping on the event-loop
        thread after this returns.
        """
        assert self.job_queue is not None

        # SQLite job cache: a stored result whose output is already on disk
        # needs no worker run. Same --ignore-cache gate as the DB cache above,
        # plus the issue #579 warmup override that forces a run past the cache.
        if not self.ignore_db and not force_execution:
            cached = self.job_queue.check_cache(str(payload.output_file), payload.content_hash())
            if cached:
                logger.debug(f"SQLite cache hit for {payload.output_file}")
                output_path = Path(payload.output_file)
                if not output_path.is_absolute():
                    output_path = self.workspace_path / output_path
                if output_path.exists():
                    return ("jobcache_hit", None)
                logger.warning(f"Cache indicated file exists but not found: {output_path}")

        # Worker availability (may briefly block waiting for workers to activate).
        if not self.skip_worker_check:
            available_workers = self._get_available_workers(job_type)
            if available_workers == 0:
                raise RuntimeError(
                    f"No workers available to process '{job_type}' jobs. "
                    f"Please start {job_type} workers before submitting jobs. "
                    f"Workers should register in the database within 10 seconds of starting."
                )
            logger.debug(f"Found {available_workers} available worker(s) for job type '{job_type}'")

        # Prepare payload dict (model_dump mode='json' base64-encodes bytes) and
        # enqueue the job. Job cancellation for watch mode is handled elsewhere
        # (the file_event_handler), not here.
        payload_dict = payload.model_dump(mode="json")
        correlation_id = getattr(payload, "correlation_id", None)
        job_id = self.job_queue.add_job(
            job_type=job_type,
            input_file=str(payload.input_file),
            output_file=str(payload.output_file),
            content_hash=payload.content_hash(),
            payload=payload_dict,
            correlation_id=correlation_id,
            execution_mode=self.worker_execution_modes.get(job_type),
            # Stamp the owning build session so only this build's workers claim
            # the job (issue #620). Mirrors the worker-row session stamping the
            # same lifecycle manager applies (issue #594); None (tests, legacy
            # callers) leaves the job claimable by any worker.
            session_id=self.worker_session_id,
        )
        return ("submitted", job_id)

    def _ensure_submit_executor(self) -> ThreadPoolExecutor:
        """Lazily create the single-thread job-submission executor.

        Deliberately ONE worker. The goal is only to move the synchronous
        submission tail OFF the event loop, not to submit concurrently:
        ``check_cache`` (``BEGIN IMMEDIATE``) and ``add_job`` (INSERT) take the
        jobs-DB write lock, which SQLite serializes anyway, so multiple
        submission threads just pile onto that lock and starve the workers'
        ``get_next_job`` and the poll loop's own writes (measured: an 8-thread
        pool nearly doubled total build time). A single dedicated thread
        reproduces the original serial submission throughput with zero added
        lock contention, while the event loop stays free to poll.
        """
        executor = self._submit_executor
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clm-submit")
            self._submit_executor = executor
        return executor

    def _can_replay_from_cache(self, payload: Payload) -> bool:
        """Gate the ``processed_files`` short-circuit on the execution cache.

        Recording HTML is the producer for the ``executed_notebooks`` table —
        Stage 4 consumers (Completed/Trainer/Partial HTML) read from it to
        skip notebook execution. When Recording's ``processed_files`` entry
        hits but ``executed_notebooks`` is empty for the same content, the
        old short-circuit silently skipped Recording's worker run, leaving
        ``executed_notebooks`` cold and forcing every downstream consumer
        in Stage 4 to fall back to direct execution.

        This helper closes that gap: for Recording HTML payloads it peeks
        at ``executed_notebooks`` and returns ``False`` when the entry is
        missing, telling the caller to skip the cache replay and submit
        the worker job. Recording then executes normally and repopulates
        the execution cache so Stage 4 hits it.

        All other payload types (non-Recording, non-notebook, non-HTML)
        return ``True`` unconditionally — the existing fast path is
        preserved for them.
        """
        from clm.infrastructure.messaging.notebook_classes import NotebookPayload

        if not isinstance(payload, NotebookPayload):
            return True
        if payload.kind not in ("recording", "speaker") or payload.format != "html":
            return True
        if self.db_manager is None:
            return True

        from clm.infrastructure.database.executed_notebook_cache import (
            ExecutedNotebookCache,
        )

        with ExecutedNotebookCache(self.db_manager.db_path) as nb_cache:
            cached_nb = nb_cache.get(
                input_file=payload.input_file,
                content_hash=payload.execution_cache_hash(),
                language=payload.language,
                prog_lang=payload.prog_lang,
            )

        if cached_nb is None:
            logger.info(
                f"Recording HTML execution cache is cold for "
                f"'{payload.input_file}' (executed_notebooks has no entry for "
                f"this content); running worker to repopulate it so Stage 4 "
                f"consumers can reuse it instead of re-executing."
            )
            return False
        return True

    def _explain_rebuild(self, payload: Payload, job_type: str) -> None:
        """Log *why* a ``processed_files`` cache miss forces a rebuild.

        Only invoked when :attr:`explain_rebuilds` is set, so the extra
        read-only probe never runs on a normal build. Touches ``db_manager``,
        so — like the cache-hit replay path above — it must stay on the
        event-loop thread (``db_manager`` is not thread-safe). Purely
        informational: it never changes the rebuild decision, and a probe
        failure degrades to a debug line rather than disturbing the build.
        """
        if self.db_manager is None:
            return
        try:
            reason_code, detail = self.db_manager.diagnose_cache_miss(
                payload.input_file, payload.content_hash(), payload.output_metadata()
            )
        except Exception as e:  # never let a diagnostic break the build
            logger.debug(f"Could not diagnose rebuild reason for {payload.input_file}: {e}")
            return

        reason = self._format_rebuild_reason(reason_code, detail, payload)
        logger.info(f"Rebuilding {payload.input_file} -> {payload.output_file}: {reason}")
        if self.build_reporter:
            self.build_reporter.report_rebuild_reason(
                str(payload.input_file), job_type, reason, reason_code
            )

    @staticmethod
    def _format_rebuild_reason(reason_code: str, detail: str | None, payload: Payload) -> str:
        """Turn a ``diagnose_cache_miss`` verdict into a human-readable reason."""
        if reason_code == "no_entry":
            return "no cache entry for this file (never built with this cache, or cache cleared)"
        if reason_code == "hash_mismatch":
            stored = (detail or "")[:12]
            current = payload.content_hash()[:12]
            return (
                f"content hash changed — source text or a dependency differs "
                f"(cached {stored}…, now {current}…)"
            )
        if reason_code == "metadata_mismatch":
            return (
                f"no cache entry for this output target "
                f"({payload.output_metadata()}) — new kind/format/language"
            )
        return "cache miss"

    def _cleanup_dead_worker_jobs(self) -> int:
        """Check for jobs stuck in 'processing' with dead workers and reset them.

        Returns:
            Number of jobs reset
        """
        if not self.job_queue:
            return 0

        try:
            conn = self.job_queue._get_conn()

            # Use explicit transaction for read-then-write operation
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Find jobs in 'processing' state where the worker is dead
                cursor = conn.execute(
                    """
                    SELECT j.id, j.job_type, j.input_file, w.id as worker_id, w.status
                    FROM jobs j
                    INNER JOIN workers w ON j.worker_id = w.id
                    WHERE j.status = 'processing' AND w.status = 'dead'
                    """
                )
                stuck_jobs = cursor.fetchall()

                if not stuck_jobs:
                    conn.rollback()
                    return 0

                logger.warning(
                    f"Found {len(stuck_jobs)} job(s) stuck in 'processing' with dead workers, "
                    f"resetting to 'pending'"
                )

                # Reset these jobs to 'pending' so another worker can pick them up
                for job_row in stuck_jobs:
                    job_id, job_type, input_file, worker_id, worker_status = job_row
                    logger.info(
                        f"Resetting job {job_id} ({job_type}: {input_file}) - "
                        f"worker {worker_id} is {worker_status}"
                    )

                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'pending', worker_id = NULL, started_at = NULL
                        WHERE id = ?
                        """,
                        (job_id,),
                    )

                conn.commit()
                return len(stuck_jobs)

            except Exception:
                conn.rollback()
                raise

        except Exception as e:
            logger.error(f"Error cleaning up dead worker jobs: {e}", exc_info=True)
            return 0

    async def wait_for_completion(self, all_submitted: asyncio.Event | None = None) -> bool:
        """Wait for all submitted jobs to complete.

        Args:
            all_submitted: If provided, the method will continue polling
                even when active_jobs is momentarily empty, until this event
                is set. This allows polling to start concurrently with job
                submission so that progress updates are reported in real time.

        Returns:
            True if all jobs completed successfully

        Raises:
            TimeoutError: If jobs don't complete within timeout
        """
        if not self.active_jobs:
            if all_submitted is None or all_submitted.is_set():
                return True

        if self.active_jobs:
            logger.info(f"Waiting for {len(self.active_jobs)} job(s) to complete...")

        # Start progress tracking
        if self.progress_tracker:
            self.progress_tracker.start_progress_logging()

        start_time = asyncio.get_event_loop().time()
        failed_jobs: list[dict[str, Any]] = []
        last_cleanup_time = start_time

        # Profiling: the inter-iteration gap of this poll loop. A healthy gap is
        # ~poll_interval; a multi-second gap means the loop was starved of the
        # event loop (submission monopolizing it) and the progress bar was
        # frozen for that long. Recorded per cycle under CLM_PROFILE_BUILD.
        last_cycle_t = profiler_now() if profiler.enabled else 0.0
        cycle_gap = 0.0

        while True:
            if profiler.enabled:
                _cycle_now = profiler_now()
                cycle_gap = _cycle_now - last_cycle_t
                last_cycle_t = _cycle_now
            # If no active jobs, check whether we can exit
            if not self.active_jobs:
                if all_submitted is None or all_submitted.is_set():
                    break
                # Jobs are still being submitted; wait before checking again
                await asyncio.sleep(self.poll_interval)
                continue
            # Periodically check for and clean up jobs from dead workers
            current_time = asyncio.get_event_loop().time()
            if current_time - last_cleanup_time >= 5.0:  # Check every 5 seconds
                reset_count = self._cleanup_dead_worker_jobs()
                if reset_count > 0:
                    logger.info(f"Reset {reset_count} job(s) from dead workers")
                last_cleanup_time = current_time
            # Check each active job
            completed_jobs = []

            # Batch query all job statuses in a single database call
            # This reduces N queries to 1 query per poll cycle
            assert self.job_queue is not None
            job_statuses = self.job_queue.get_job_statuses_batch(list(self.active_jobs.keys()))

            for job_id, job_info in list(self.active_jobs.items()):
                # Get status from batch query result
                status_data = job_statuses.get(job_id)

                if not status_data:
                    logger.warning(f"Job {job_id} not found in database")
                    completed_jobs.append(job_id)
                    continue

                status, error = status_data

                if status == "completed":
                    logger.info(
                        f"Job {job_id} completed: {job_info['input_file']} -> {job_info['output_file']}"
                    )
                    completed_jobs.append(job_id)

                    # A successful run supersedes whatever issues earlier runs
                    # stored under the same (file, content, metadata) key.
                    # Without this, a transient failure's stored error is
                    # replayed on every later cache hit even though the
                    # content now builds cleanly — and re-runs of the same
                    # content (--ignore-cache) accumulate duplicate warnings.
                    # Must happen BEFORE the warning extraction below so the
                    # fresh run's warnings survive.
                    self._clear_stored_issues_for_job(job_id, job_info)

                    # Extract and report any warnings from the job result
                    self._extract_and_report_job_warnings(job_id, job_info)

                    # Report file completed to build reporter (for verbose mode output)
                    if self.build_reporter:
                        self.build_reporter.report_file_completed(
                            job_info["input_file"], job_info["job_type"], job_id, success=True
                        )

                    # Notify progress tracker
                    if self.progress_tracker:
                        self.progress_tracker.job_completed(job_id)

                    # Add to database cache if applicable
                    # Always store results in cache, even with --ignore-db
                    # (ignore_db only affects reading, not writing - like error storage below)
                    if self.db_manager:
                        output_path = Path(job_info["output_file"])
                        # Make path absolute relative to workspace if not already absolute
                        if not output_path.is_absolute():
                            output_path = self.workspace_path / output_path

                        if output_path.exists():
                            # Register the worker's output write with the
                            # registry. The file is already on disk (the
                            # worker subprocess wrote it), so dedup-skip
                            # is not meaningful here — but conflict
                            # detection across worker outputs in the same
                            # build still works. Image sources are kept
                            # in OutputWriteRegistry too, so that a
                            # generated PNG (PlantUML/DrawIO) and a
                            # static ``img/X.png`` writing to the same
                            # output path surface as a content conflict.
                            source_for_registry = Path(job_info["input_file"])
                            if is_image_path(source_for_registry):
                                self.image_registry.record_output_write(output_path)
                            try:
                                self.output_write_registry.record_write(
                                    output_path,
                                    content_source=output_path,
                                    source=source_for_registry,
                                )
                            except Exception as reg_exc:
                                logger.debug(
                                    f"Could not register worker output {output_path}: {reg_exc}"
                                )

                            # Hand the slow part — read the output back, pickle
                            # it, commit the blob with retention pruning — to the
                            # background writer so the poll loop can immediately
                            # detect and report the next completion. The bar has
                            # already advanced (job_completed above); the queue is
                            # drained before this wait returns, so the cache is
                            # fully populated for callers that read it afterwards.
                            self._enqueue_result_cache(job_id, dict(job_info), output_path)

                elif status == "failed":
                    # Get job payload for error categorization and storage
                    conn = self.job_queue._get_conn()
                    cursor = conn.execute(
                        "SELECT payload, content_hash FROM jobs WHERE id = ?", (job_id,)
                    )
                    payload_row = cursor.fetchone()
                    payload_dict = json.loads(payload_row[0]) if payload_row else {}
                    content_hash = payload_row[1] if payload_row else ""

                    # Persist execution telemetry the notebook worker attached
                    # to its structured error JSON (issue #330) — this is how
                    # deterministic kernel crashes become part of the per-deck
                    # crash history.
                    try:
                        error_info = json.loads(error) if error else {}
                    except (json.JSONDecodeError, TypeError):
                        error_info = {}
                    failure_telemetry = error_info.get("execution_telemetry")
                    if isinstance(failure_telemetry, dict):
                        self._persist_execution_telemetry(
                            job_info["input_file"], payload_dict, content_hash, failure_telemetry
                        )

                    # Import ErrorCategorizer
                    from clm.cli.error_categorizer import ErrorCategorizer

                    # Categorize the error
                    categorized_error = ErrorCategorizer.categorize_job_error(
                        job_type=job_info["job_type"],
                        input_file=job_info["input_file"],
                        error_message=error or "Unknown error",
                        job_payload=payload_dict,
                        job_id=job_id,
                        correlation_id=job_info.get("correlation_id"),
                    )

                    # Store error in database for future cache hits
                    # Only store user errors (e.g., bad notebooks) - NOT configuration errors
                    # Configuration errors (missing tools, bad env vars) should be retried
                    # since we can't know if the user fixed the configuration
                    if self.db_manager and categorized_error.error_type == "user":
                        try:
                            # Reconstruct output_metadata from payload
                            output_metadata = self._get_output_metadata(
                                job_info["job_type"], payload_dict
                            )
                            self.db_manager.store_error(
                                file_path=job_info["input_file"],
                                content_hash=content_hash,
                                output_metadata=output_metadata,
                                error=categorized_error,
                            )
                            logger.debug(f"Stored error for {job_info['input_file']} in database")
                        except Exception as e:
                            logger.warning(f"Could not store error for job {job_id}: {e}")
                    elif categorized_error.error_type == "configuration":
                        logger.debug(
                            f"Not caching configuration error for {job_info['input_file']} "
                            f"(will retry on next build)"
                        )

                    # Report file completed (failed) to build reporter (for verbose mode output)
                    if self.build_reporter:
                        self.build_reporter.report_file_completed(
                            job_info["input_file"], job_info["job_type"], job_id, success=False
                        )
                        # Also report the categorized error
                        self.build_reporter.report_error(categorized_error)
                    else:
                        # Fallback to logging if no build_reporter
                        logger.error(
                            f"Job {job_id} failed: {job_info['input_file']} -> {job_info['output_file']}\n"
                            f"Error: {error}"
                        )

                    completed_jobs.append(job_id)
                    failed_jobs.append({"job_id": job_id, "job_info": job_info, "error": error})

                    # Notify progress tracker
                    if self.progress_tracker:
                        self.progress_tracker.job_failed(job_id, error or "Unknown error")

            # Remove completed jobs
            for job_id in completed_jobs:
                del self.active_jobs[job_id]

            if profiler.enabled:
                profiler.record_poll_cycle(
                    cycle_gap, len(completed_jobs), len(self.active_jobs), self.poll_interval
                )

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self.max_wait_for_completion_duration:
                self._report_pending_jobs_timeout(elapsed)
                raise JobsPendingTimeoutError(
                    f"Jobs did not complete within "
                    f"{self.max_wait_for_completion_duration} seconds. "
                    f"{len(self.active_jobs)} job(s) still pending.",
                    pending_jobs=list(self.active_jobs.values()),
                )

            # Wait before polling again
            await asyncio.sleep(self.poll_interval)

        # Drain any background result-cache writes queued during this wait so
        # the cache DB is fully populated by the time we return — callers (and
        # tests) read processed_files right after wait_for_completion. The
        # writer thread stays alive for the next stage; it is stopped in
        # shutdown. This is where any backlog the writer could not keep up with
        # during the stage is absorbed; the live progress bar's spinner covers
        # the pause (a console notice here would render above the pinned bar).
        await self._drain_result_cache_writes()

        # Stop progress tracking and log summary
        if self.progress_tracker:
            self.progress_tracker.stop_progress_logging()
            self.progress_tracker.log_summary()

        if failed_jobs:
            logger.error(f"{len(failed_jobs)} job(s) failed")
            for failed in failed_jobs:
                failed_job_info: object | None = failed.get("job_info")
                input_file = (
                    failed_job_info.get("input_file", "unknown")
                    if isinstance(failed_job_info, dict)
                    else "unknown"
                )
                logger.error(
                    f"  - Job {failed.get('job_id')}: {input_file} ({failed.get('error')})"
                )
            return False

        logger.info("All jobs completed successfully")
        return True

    def _report_pending_jobs_timeout(self, elapsed: float) -> None:
        """Record one infrastructure error per still-pending job on timeout.

        Without this, a ``wait_for_completion`` timeout raised only a bare
        ``TimeoutError`` and never reached the build summary, so the build
        could report "completed successfully" and exit 0 despite stuck jobs
        (issue #143, sub-bug A). Recording the errors here means the timeout
        is visible in the summary and drives the non-zero exit policy.
        """
        if not self.build_reporter:
            return

        from clm.cli.build_data_classes import BuildError

        for job_info in self.active_jobs.values():
            input_file = str(job_info.get("input_file", "unknown"))
            job_type = job_info.get("job_type", "unknown")
            self.build_reporter.report_error(
                BuildError(
                    error_type="infrastructure",
                    category="job_timeout",
                    severity="error",
                    file_path=input_file,
                    message=(
                        f"{job_type} job did not complete within "
                        f"{self.max_wait_for_completion_duration:.0f}s "
                        f"(waited {elapsed:.0f}s); the worker appears to be "
                        f"stuck processing this file."
                    ),
                    actionable_guidance=(
                        "The worker hung on this file rather than failing. "
                        "Try running the notebook directly (e.g. via "
                        "'jupyter execute') to confirm it completes outside "
                        "CLM, then re-run the build. See issue #143."
                    ),
                    job_id=job_info.get("job_id"),
                    correlation_id=job_info.get("correlation_id"),
                )
            )

    async def shutdown(self):
        """Shutdown the backend and perform build-end cleanup if configured."""
        logger.debug("Shutting down SQLite backend")
        # Emit the build profiling summary (no-op unless CLM_PROFILE_BUILD is set).
        profiler.dump_summary()
        # Wait for remaining jobs with shorter timeout
        if self.active_jobs:
            logger.warning(f"Shutdown called with {len(self.active_jobs)} job(s) still pending")
            try:
                await asyncio.wait_for(self.wait_for_completion(), timeout=5.0)
            except TimeoutError:
                logger.warning(f"Shutdown timeout - {len(self.active_jobs)} job(s) still pending")

        # Stop the background result-cache writer before any cleanup/VACUUM so
        # all pending blob writes land first and nothing races the compaction.
        self._stop_result_cache_writer()

        # Shut down the job-submission thread pool (all submission is done by
        # the time we reach shutdown). wait=True so its jobs-DB connections are
        # closed before any end-of-build cleanup/VACUUM.
        if self._submit_executor is not None:
            self._submit_executor.shutdown(wait=True)
            self._submit_executor = None

        # Perform build-end cleanup if configured
        self._perform_build_end_cleanup()

        # Close job queue connection to avoid ResourceWarning about unclosed database
        if self.job_queue:
            self.job_queue.close()

    def _perform_session_start_cleanup(self) -> None:
        """Perform cleanup at session start if configured.

        This resets hung jobs and cleans up dead workers.
        """
        from clm.infrastructure.config import get_config

        retention_config = get_config().retention

        if not retention_config.auto_cleanup_on_session_start:
            return

        if not self.job_queue:
            return

        try:
            # Reset any hung jobs from previous sessions
            hung_reset = self.job_queue.reset_hung_jobs()
            if hung_reset > 0:
                logger.info(f"Session start: Reset {hung_reset} hung job(s) from previous session")

        except Exception as e:
            logger.warning(f"Session start cleanup failed: {e}")

    def _show_progress(self, message: str) -> None:
        """Surface a shutdown-phase progress message to the user.

        The build summary has already been printed when ``shutdown`` runs,
        and ``logger.info`` only reaches the log file by default — so
        without these messages the cleanup phase looks like a silent hang.
        """
        # getattr: tests drive shutdown with duck-typed stub reporters that
        # don't carry a formatter — progress display must never break them.
        formatter = getattr(self.build_reporter, "formatter", None)
        if formatter is not None:
            formatter.show_startup_message(message)

    def _perform_build_end_cleanup(self) -> None:
        """Perform cleanup at build end if configured.

        This removes old completed jobs, events, and cache entries.
        """
        from clm.infrastructure.config import get_config
        from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache

        retention_config = get_config().retention

        if not retention_config.auto_cleanup_on_build_end:
            return

        self._show_progress("Cleaning up job and cache databases...")
        try:
            # Clean up jobs database
            if self.job_queue:
                jobs_cleanup = self.job_queue.cleanup_all(
                    completed_days=retention_config.completed_jobs_retention_days,
                    failed_days=retention_config.failed_jobs_retention_days,
                    cancelled_days=retention_config.cancelled_jobs_retention_days,
                    events_days=retention_config.worker_events_retention_days,
                    cache_versions=retention_config.cache_versions_to_keep,
                )

                total_jobs_cleaned = sum(jobs_cleanup.values())
                if total_jobs_cleaned > 0:
                    logger.info(f"Build end: Cleaned up {total_jobs_cleaned} job database entries")

            # Clean up cache database
            if self.db_manager:
                cache_cleanup = self.db_manager.cleanup_all(
                    retain_versions=retention_config.cache_versions_to_keep,
                    issues_days=retention_config.failed_jobs_retention_days,  # Same as failed jobs
                )

                total_cache_cleaned = sum(cache_cleanup.values())
                if total_cache_cleaned > 0:
                    logger.info(
                        f"Build end: Cleaned up {total_cache_cleaned} cache database entries"
                    )

                # Clean up executed notebook cache (shares the cache DB file).
                # Use the path this backend actually opened — the same file
                # ``db_manager`` manages — not ``get_config().paths.cache_db_path``,
                # a separate, CLI-disconnected setting. That config field ignores
                # ``--cache-db-path`` / ``CLM_CACHE_DB_PATH`` and the project-root
                # anchoring, so it pointed at the wrong DB (or created a stray
                # ``clm_cache.db`` in the cwd) whenever the cache path was
                # overridden or the build ran from a subdirectory.
                try:
                    with ExecutedNotebookCache(self.db_manager.db_path) as nb_cache:
                        nb_cleaned = nb_cache.prune_stale_hashes()
                        if nb_cleaned > 0:
                            logger.info(
                                f"Build end: Cleaned up {nb_cleaned} stale executed "
                                f"notebook cache entries"
                            )
                except Exception as e:
                    logger.debug(f"Could not clean executed notebook cache: {e}")

            # Vacuum if configured (can be slow for large DBs)
            if retention_config.auto_vacuum_after_cleanup:
                self._show_progress("Compacting databases (VACUUM, may take a while)...")
                if self.job_queue:
                    try:
                        self.job_queue.vacuum()
                    except Exception as e:
                        logger.debug(f"Could not vacuum jobs database: {e}")

                if self.db_manager:
                    try:
                        self.db_manager.vacuum()
                    except Exception as e:
                        logger.debug(f"Could not vacuum cache database: {e}")

        except Exception as e:
            logger.warning(f"Build end cleanup failed: {e}")

    async def cancel_jobs_for_file(self, file_path: Path) -> int:
        """Cancel all pending jobs for a given input file.

        This is used in watch mode when a file is modified to cancel any
        pending jobs before submitting new ones with updated content.

        Args:
            file_path: Path to the input file

        Returns:
            Number of jobs cancelled
        """
        if not self.job_queue:
            return 0

        cancelled_ids = self.job_queue.cancel_jobs_for_file(
            str(file_path), cancelled_by="watch_mode"
        )

        if cancelled_ids:
            # Remove cancelled jobs from active_jobs tracking
            for job_id in cancelled_ids:
                if job_id in self.active_jobs:
                    del self.active_jobs[job_id]
            logger.info(f"Cancelled {len(cancelled_ids)} pending job(s) for {file_path.name}")

        return len(cancelled_ids)

    def _get_available_workers(self, job_type: str, wait_for_activation: bool = True) -> int:
        """Query database for available workers of a specific type.

        A worker is considered available if:
        - It matches the requested job_type
        - It matches this build's execution mode for the job type (when the
          backend knows one) — a Direct worker cannot service jobs tagged
          for Docker, so it must not count as available for them
        - Its status is 'idle' or 'busy' (not 'hung' or 'dead')
        - It has sent a heartbeat within the last 30 seconds

        If workers are pre-registered (status='created') but not yet activated,
        this method will wait for them to activate (up to 30 seconds).

        Args:
            job_type: Type of job (e.g., 'notebook', 'plantuml', 'drawio')
            wait_for_activation: If True, wait for pre-registered workers to activate

        Returns:
            Number of available workers for this job type
        """
        if not self.job_queue:
            return 0

        conn = self.job_queue._get_conn()

        # Same direct/docker discriminator WorkerDiscovery uses: Direct
        # executor IDs are 'direct-<type>-<uuid>'; anything else (Docker
        # container IDs, 'docker-<type>-<uuid>' pre-registrations) is Docker.
        required_mode = self.worker_execution_modes.get(job_type)
        if required_mode is None:
            mode_clause = ""
            mode_params: tuple[str, ...] = ()
        else:
            mode_clause = (
                "AND (CASE WHEN container_id LIKE 'direct-%' THEN 'direct' ELSE 'docker' END) = ?"
            )
            mode_params = (required_mode,)

        # First check for activated workers (idle or busy with recent heartbeat)
        cursor = conn.execute(
            f"""
            SELECT COUNT(*) FROM workers
            WHERE worker_type = ?
            AND status IN ('idle', 'busy')
            AND last_heartbeat > datetime('now', '-30 seconds')
            {mode_clause}
            """,
            (job_type, *mode_params),
        )
        row = cursor.fetchone()
        activated_count = row[0] if row else 0

        if activated_count > 0:
            return activated_count

        # Check if there are pre-registered workers waiting to activate
        if wait_for_activation:
            cursor = conn.execute(
                f"""
                SELECT COUNT(*) FROM workers
                WHERE worker_type = ?
                AND status = 'created'
                {mode_clause}
                """,
                (job_type, *mode_params),
            )
            row = cursor.fetchone()
            created_count = row[0] if row else 0

            if created_count > 0:
                logger.info(
                    f"Found {created_count} pre-registered {job_type} worker(s), "
                    f"waiting for activation..."
                )
                # Wait for workers to activate (up to 30 seconds)
                timeout = 30.0
                poll_interval = 0.5
                start_time = time.time()

                while (time.time() - start_time) < timeout:
                    cursor = conn.execute(
                        f"""
                        SELECT COUNT(*) FROM workers
                        WHERE worker_type = ?
                        AND status IN ('idle', 'busy')
                        AND last_heartbeat > datetime('now', '-30 seconds')
                        {mode_clause}
                        """,
                        (job_type, *mode_params),
                    )
                    row = cursor.fetchone()
                    activated_count = row[0] if row else 0

                    if activated_count > 0:
                        elapsed = time.time() - start_time
                        logger.info(
                            f"{activated_count} {job_type} worker(s) activated after {elapsed:.1f}s"
                        )
                        return activated_count

                    time.sleep(poll_interval)

                # Timeout waiting for activation. A pre-registered worker
                # activates within seconds of its subprocess starting; one
                # still 'created' after this wait (on top of its own age)
                # is a startup casualty — typically an import crash in the
                # worker subprocess (missing extras, broken env), visible
                # only in the worker's own log file (issue #348). Mark these
                # workers dead so every *subsequent* submission fails fast
                # instead of repeating this full wait per job, which is what
                # stalled builds for many minutes.
                logger.warning(
                    f"Timeout waiting for {job_type} workers to activate after {timeout}s"
                )
                # Scope the dead-marking like the availability queries above
                # (issue #597): without the mode filter, a Direct-mode build
                # timing out on its own workers condemned a concurrent
                # Docker-mode build's still-starting pre-registrations (>30s
                # old is plausible for a cold Docker image pull). Ownership
                # narrows it further — only this session's workers (or
                # unowned legacy rows) are ours to declare startup casualties.
                if self.worker_session_id is None:
                    ownership_clause = ""
                    ownership_params: tuple[str, ...] = ()
                else:
                    ownership_clause = "AND (session_id = ? OR session_id IS NULL)"
                    ownership_params = (self.worker_session_id,)
                cursor = conn.execute(
                    f"""
                    UPDATE workers SET status = 'dead'
                    WHERE worker_type = ?
                    AND status = 'created'
                    AND started_at < datetime('now', '-30 seconds')
                    {mode_clause}
                    {ownership_clause}
                    """,
                    (job_type, *mode_params, *ownership_params),
                )
                if cursor.rowcount:
                    conn.commit()
                    from clm.infrastructure.logging.log_paths import get_worker_log_path

                    log_dir = get_worker_log_path(job_type, 0).parent
                    logger.error(
                        f"Marked {cursor.rowcount} pre-registered {job_type} worker(s) "
                        f"as dead: the worker process(es) never activated and likely "
                        f"crashed at startup (e.g. missing worker dependencies — "
                        f"install clm[all-workers]). Check the worker logs in "
                        f"{log_dir} for the crash traceback."
                    )

        return 0

    def _get_output_metadata(self, job_type: str, payload_dict: dict) -> str:
        """Reconstruct output_metadata string from job payload.

        Args:
            job_type: Type of job (notebook, plantuml, drawio)
            payload_dict: Job payload dictionary

        Returns:
            Output metadata string matching the format used in payload.output_metadata()
        """
        if job_type == "notebook":
            # MUST match NotebookPayload.output_metadata() / NotebookResult
            # .output_metadata() exactly — the colon-joined form, NOT
            # ``str()`` of the tags tuple. Issues are *stored* under the key
            # this function returns but *looked up* (``_report_cached_issues``)
            # under ``payload.output_metadata()``; a previous ``str(tuple)``
            # here keyed every stored notebook error/warning under
            # ``"('completed', 'python', ...)"`` while lookups used
            # ``"completed:python:..."``, silently disabling cached-issue
            # replay for ALL notebook jobs (issue #321).
            from clm.infrastructure.messaging.notebook_classes import (
                notebook_metadata,
                notebook_metadata_tags_from_payload,
            )

            return notebook_metadata(*notebook_metadata_tags_from_payload(payload_dict))
        elif job_type in ("plantuml", "drawio"):
            # ImagePayload.output_metadata() returns output_format
            output_format = payload_dict.get("output_format", "png")
            return str(output_format)
        elif job_type == "jupyterlite":
            target_name = payload_dict.get("target_name", "")
            language = payload_dict.get("language", "")
            kinds = payload_dict.get("kinds", [])
            kernel = payload_dict.get("kernel", "")
            kinds_str = "+".join(sorted(kinds)) if kinds else ""
            return f"jupyterlite:{target_name}:{language}:{kinds_str}:{kernel}"
        else:
            return ""

    def _ensure_result_cache_writer(self) -> None:
        """Lazily start the background result-cache writer thread + queue."""
        if self._result_cache_thread is not None:
            return
        self._result_cache_queue = queue.Queue()
        self._result_cache_thread = threading.Thread(
            target=self._result_cache_writer_loop,
            name="ResultCacheWriter",
            daemon=True,
        )
        self._result_cache_thread.start()

    def _enqueue_result_cache(self, job_id: int, job_info: dict, output_path: Path) -> None:
        """Queue a completed job's result for background caching.

        Falls back to an inline write if the writer cannot be started, so a
        result is never silently dropped from the cache.
        """
        if self.db_manager is None:
            return
        try:
            self._ensure_result_cache_writer()
            assert self._result_cache_queue is not None
            self._result_cache_queue.put((job_id, job_info, output_path))
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Could not enqueue result cache for job {job_id}; writing inline: {e}")
            self._persist_result_to_cache(job_id, job_info, output_path, self.db_manager)

    def _result_cache_writer_loop(self) -> None:
        """Drain the result-cache queue on a dedicated DB connection.

        A SQLite connection is bound to the thread that opened it, so this
        thread uses its own DatabaseManager rather than ``self.db_manager``
        (which the main thread keeps using concurrently). Both are WAL
        connections to the same cache DB, so concurrent access is safe.
        """
        assert self.db_manager is not None
        assert self._result_cache_queue is not None

        # Open the writer's own connection. If this fails we still drain the
        # queue (calling task_done for every item) so a join() in
        # _drain_result_cache_writes / _stop_result_cache_writer can never hang;
        # results just go uncached this run.
        writer_db: DatabaseManager | None = None
        try:
            writer_db = DatabaseManager(self.db_manager.db_path).__enter__()
        except Exception:
            logger.exception(
                "Result-cache writer could not open its DB connection; "
                "results will not be cached this run"
            )
            writer_db = None

        try:
            while True:
                item = self._result_cache_queue.get()
                try:
                    if item is None:  # shutdown sentinel
                        return
                    if writer_db is not None:
                        job_id, job_info, output_path = item
                        self._persist_result_to_cache(job_id, job_info, output_path, writer_db)
                except Exception as e:
                    logger.warning(
                        f"Background result-cache write failed for job "
                        f"{item[0] if item else '?'}: {e}",
                        exc_info=True,
                    )
                finally:
                    self._result_cache_queue.task_done()
        finally:
            if writer_db is not None:
                try:
                    writer_db.__exit__(None, None, None)
                except Exception:  # pragma: no cover - best-effort close
                    logger.debug("Error closing result-cache writer DB", exc_info=True)

    async def _drain_result_cache_writes(self) -> None:
        """Wait until every queued result-cache write has been committed.

        Called at the end of each ``wait_for_completion`` so the cache DB is
        fully populated for callers that read it immediately afterwards. The
        writer thread keeps running for the next stage.

        The blocking ``queue.join()`` runs off the event loop (``to_thread``):
        if the background writer fell behind, joining inline here would freeze
        the loop — and therefore the progress bar — at the end of every stage
        (three times per stage with the default shared/trainer/speaker targets).

        No console notice here: the Rich live progress display is still active
        at this point, so a print would render above the pinned stage bar and
        interleave with the build output (same bug as the sweep notice, fixed
        in build.py). The bar's spinner covers the pause; the "Finishing N
        result-cache write(s)..." notice is shown by _stop_result_cache_writer
        instead, which runs in the shutdown phase after the summary.
        """
        q = self._result_cache_queue
        if q is None:
            return
        pending = q.qsize()
        if pending:
            logger.info(f"Draining {pending} queued result-cache write(s)")
        await asyncio.to_thread(q.join)

    def _stop_result_cache_writer(self) -> None:
        """Drain remaining writes and stop the writer thread (idempotent)."""
        thread = self._result_cache_thread
        q = self._result_cache_queue
        if thread is None or q is None:
            return
        pending = q.qsize()
        if pending:
            # Shutdown phase: the build summary is already on screen, so this
            # prints below it like the other cleanup notices.
            self._show_progress(f"Finishing {pending} result-cache write(s)...")
        q.join()
        q.put(None)  # sentinel: tells the loop to exit
        thread.join(timeout=30.0)
        self._result_cache_thread = None
        self._result_cache_queue = None

    def _persist_result_to_cache(
        self, job_id: int, job_info: dict, output_path: Path, db_manager: DatabaseManager
    ) -> None:
        """Reconstruct a completed job's Result and store it in the cache DB.

        Runs on the background writer thread (and inline as a fallback). Reads
        the job payload via the job queue's thread-local connection and writes
        through the supplied ``db_manager`` (the caller's connection).
        """
        if self.job_queue is None:
            return
        try:
            # job_queue uses a thread-local connection, so this is safe to call
            # from the writer thread.
            conn = self.job_queue._get_conn()
            cursor = conn.execute("SELECT payload, content_hash FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return

            from clm.infrastructure.messaging.base_classes import ImageResult, Result
            from clm.infrastructure.messaging.notebook_classes import (
                NotebookResult,
                notebook_metadata_tags_from_payload,
            )

            payload_dict = json.loads(row[0])
            content_hash = row[1]
            correlation_id = job_info.get("correlation_id", "")

            job_type = job_info["job_type"]
            result_obj: Result | None = None

            if job_type == "notebook":
                result_text = output_path.read_text(encoding="utf-8")
                result_obj = NotebookResult(
                    correlation_id=correlation_id,
                    output_file=str(job_info["output_file"]),
                    input_file=str(job_info["input_file"]),
                    content_hash=content_hash,
                    result=result_text,
                    output_metadata_tags=notebook_metadata_tags_from_payload(payload_dict),
                )
            elif job_type in ("plantuml", "drawio"):
                result_bytes = output_path.read_bytes()
                image_format = payload_dict.get("output_format", "png")
                result_obj = ImageResult(
                    correlation_id=correlation_id,
                    output_file=str(job_info["output_file"]),
                    input_file=str(job_info["input_file"]),
                    content_hash=content_hash,
                    result=result_bytes,
                    image_format=image_format,
                )
            elif job_type == "jupyterlite":
                # JupyterLite "output" is a directory tree; the worker already
                # wrote a queue cache entry, so we skip the single-blob layer.
                logger.debug(
                    "JupyterLite job %s: skipping DB result cache (queue cache is authoritative)",
                    job_id,
                )
            else:
                logger.warning(f"Unknown job type {job_type}, skipping cache storage")

            if result_obj is not None:
                from clm.infrastructure.config import get_config

                retain_count = get_config().retention.cache_versions_to_keep
                db_manager.store_latest_result(
                    file_path=job_info["input_file"],
                    content_hash=content_hash,
                    correlation_id=correlation_id,
                    result=result_obj,
                    retain_count=retain_count,
                )
                logger.debug(f"Stored result for {job_info['input_file']} in database cache")
        except Exception as e:
            logger.warning(f"Could not cache result for job {job_id}: {e}", exc_info=True)

    def _clear_stored_issues_for_job(self, job_id: int, job_info: dict) -> None:
        """Drop stored errors/warnings superseded by a successful run.

        ``processing_issues`` rows are keyed ``(file_path, content_hash,
        output_metadata)`` and are replayed by ``_report_cached_issues`` on
        every cache hit for that key. When a job for the exact same key
        completes successfully, those stored issues describe a run that the
        success just superseded (e.g. a transient kernel failure), so they
        must not haunt future cache hits. Called before
        ``_extract_and_report_job_warnings`` stores the fresh run's warnings.
        """
        if self.db_manager is None or self.job_queue is None:
            return
        try:
            conn = self.job_queue._get_conn()
            cursor = conn.execute("SELECT payload, content_hash FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return
            payload_dict = json.loads(row[0]) if row[0] else {}
            content_hash = row[1]
            output_metadata = self._get_output_metadata(job_info["job_type"], payload_dict)
            self.db_manager.clear_issues(
                file_path=job_info["input_file"],
                content_hash=content_hash,
                output_metadata=output_metadata,
            )
        except Exception as e:
            logger.warning(f"Could not clear stored issues for job {job_id}: {e}")

    def _persist_execution_telemetry(
        self,
        input_file: str,
        payload_dict: dict,
        content_hash: str,
        telemetry: dict,
    ) -> None:
        """Record one worker-reported execution-telemetry event (issue #330).

        No-op when no telemetry store is configured. Never raises — the
        store itself logs and swallows write failures.
        """
        if self.telemetry_store is None:
            return
        from clm.infrastructure.database.execution_telemetry import TelemetryEvent

        attempts_detail = telemetry.get("attempts_detail")
        self.telemetry_store.record_event(
            TelemetryEvent(
                input_file=input_file,
                outcome=str(telemetry.get("outcome", "") or ""),
                classification=str(telemetry.get("classification", "") or ""),
                attempts=int(telemetry.get("attempts", 0) or 0),
                failure_type=str(telemetry.get("failure_type", "") or ""),
                failing_cell_index=telemetry.get("failing_cell_index"),
                error_message=str(telemetry.get("error_message", "") or ""),
                prog_lang=str(payload_dict.get("prog_lang", "") or ""),
                language=str(payload_dict.get("language", "") or ""),
                content_hash=content_hash or "",
                worker_image_identity=str(payload_dict.get("worker_image_identity", "") or ""),
                attempts_detail=attempts_detail if isinstance(attempts_detail, list) else [],
            )
        )

    def _extract_and_report_job_warnings(self, job_id: int, job_info: dict) -> None:
        """Extract warnings from completed job and report/store them.

        Args:
            job_id: ID of the completed job
            job_info: Job info dict with input_file, output_file, job_type, etc.
        """
        if self.job_queue is None:
            return

        try:
            # Get the result column from the jobs table
            conn = self.job_queue._get_conn()
            cursor = conn.execute(
                "SELECT result, payload, content_hash FROM jobs WHERE id = ?", (job_id,)
            )
            row = cursor.fetchone()

            if not row or not row[0]:
                # No result data (or no warnings)
                return

            result_json = row[0]
            payload_json = row[1]
            content_hash = row[2]

            try:
                result_data = json.loads(result_json)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse result JSON for job {job_id}")
                return

            warnings_data = result_data.get("warnings", [])
            if not warnings_data:
                return

            logger.debug(f"Job {job_id} completed with {len(warnings_data)} warning(s)")

            # Import required classes
            from clm.cli.build_data_classes import BuildWarning

            # Parse payload for output_metadata
            payload_dict = json.loads(payload_json) if payload_json else {}
            output_metadata = self._get_output_metadata(job_info["job_type"], payload_dict)

            for warn_data in warnings_data:
                # Execution telemetry rides the warning channel but is not a
                # user-facing warning (issue #330): persist it and surface
                # flakes in the build summary's dedicated list instead. It is
                # also deliberately NOT stored via store_warning — a cache hit
                # replays no execution, so it must not replay telemetry.
                if warn_data.get("category") == "execution_telemetry":
                    details = warn_data.get("details") or {}
                    self._persist_execution_telemetry(
                        job_info["input_file"], payload_dict, content_hash, details
                    )
                    if self.build_reporter and details.get("outcome") == "passed_after_retry":
                        failure_types = [
                            a.get("failure_type", "other")
                            for a in details.get("attempts_detail") or []
                        ]
                        self.build_reporter.report_flaky_file(
                            file_path=job_info["input_file"],
                            attempts=int(details.get("attempts", 0) or 0),
                            failure_types=failure_types,
                            language=str(payload_dict.get("language", "") or ""),
                        )
                    continue

                # Create BuildWarning from ProcessingWarning data
                warning = BuildWarning(
                    category=warn_data.get("category", "general"),
                    message=warn_data.get("message", "Unknown warning"),
                    severity=warn_data.get("severity", "medium"),
                    file_path=warn_data.get("file_path") or job_info["input_file"],
                )

                # Report to build reporter if available
                if self.build_reporter:
                    self.build_reporter.report_warning(warning)

                # Store warning in database for future cache hits
                if self.db_manager:
                    try:
                        self.db_manager.store_warning(
                            file_path=job_info["input_file"],
                            content_hash=content_hash,
                            output_metadata=output_metadata,
                            warning=warning,
                        )
                    except Exception as e:
                        logger.warning(f"Could not store warning for job {job_id}: {e}")

        except Exception as e:
            logger.warning(f"Error extracting warnings for job {job_id}: {e}")

    def _report_cached_issues(
        self, file_path: str, content_hash: str, output_metadata: str
    ) -> None:
        """Report stored errors/warnings for a cached result.

        This method retrieves any stored errors and warnings for a file
        and reports them through the build_reporter.

        Args:
            file_path: Path to the source file
            content_hash: Hash of the file content
            output_metadata: Output metadata string
        """
        if not self.db_manager or not self.build_reporter:
            return

        try:
            errors, warnings = self.db_manager.get_issues(file_path, content_hash, output_metadata)

            for error in errors:
                # Mark this as a cached/historical error for display purposes
                if "from_cache" not in error.details:
                    error.details["from_cache"] = True
                self.build_reporter.report_error(error)

            for warning in warnings:
                self.build_reporter.report_warning(warning)

            if errors or warnings:
                logger.debug(
                    f"Reported {len(errors)} cached error(s) and {len(warnings)} "
                    f"cached warning(s) for {file_path}"
                )

        except Exception as e:
            logger.warning(f"Could not retrieve cached issues for {file_path}: {e}")

    async def copy_dir_group_to_output(self, copy_data: "CopyDirGroupData") -> list["BuildWarning"]:
        """Copy a directory group to the output directory and report any warnings.

        This override ensures warnings (like missing directories) are reported
        to the build reporter if one is available.

        Args:
            copy_data: Data for the copy operation.

        Returns:
            List of BuildWarning objects for any issues encountered.
        """
        from clm.cli.build_data_classes import BuildWarning

        warnings: list[BuildWarning] = await super().copy_dir_group_to_output(copy_data)

        # Report warnings to build reporter if available
        if self.build_reporter and warnings:
            for warning in warnings:
                self.build_reporter.report_warning(warning)

        return warnings

    async def copy_file_to_output(self, copy_data: "CopyFileData") -> None:
        """Copy a file to the output directory.

        In incremental mode, skips the copy if the destination file already exists.

        Args:
            copy_data: Data for the copy operation.
        """
        if self.incremental:
            # In incremental mode, skip copy if destination already exists
            if copy_data.output_path.exists():
                logger.debug(
                    f"Incremental mode: skipping copy of {copy_data.relative_input_path} "
                    f"(destination exists)"
                )
                return

        await super().copy_file_to_output(copy_data)
