"""SQLite-based backend for job queue orchestration.

This backend submits jobs to a SQLite database and waits for workers to complete them.
It's a simpler alternative to the RabbitMQ-based FastStreamBackend.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from attrs import define, field

from clx.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clx.infrastructure.database.db_operations import DatabaseManager
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.messaging.base_classes import Payload
from clx.infrastructure.operation import Operation
from clx.infrastructure.workers.progress_tracker import ProgressTracker, get_progress_tracker_config

if TYPE_CHECKING:
    from clx.cli.build_reporter import BuildReporter

logger = logging.getLogger(__name__)


@define
class SqliteBackend(LocalOpsBackend):
    """SQLite-based backend for job queue orchestration.

    This backend submits jobs to a SQLite database and waits for
    workers to complete them. It's a simpler alternative to the
    RabbitMQ-based FastStreamBackend.
    """

    db_path: Path = Path("clx_jobs.db")
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
        """Start the backend (no-op for SQLite, kept for compatibility)."""
        logger.debug("SQLite backend started")

    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        """Submit a job to the SQLite queue.

        Args:
            operation: Operation to execute
            payload: Payload data for the job
        """
        # Check database cache first (processed_files table with full Result objects)
        if not self.ignore_db and self.db_manager:
            result = self.db_manager.get_result(
                payload.input_file, payload.content_hash(), payload.output_metadata()
            )
            if result:
                logger.info(
                    f"Database cache hit for {payload.input_file} -> {payload.output_file} "
                    f"(skipping worker execution)"
                )
                # Write cached result from database
                output_file = Path(payload.output_file)
                # Make path absolute relative to workspace if not already absolute
                if not output_file.is_absolute():
                    output_file = self.workspace_path / output_file
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_bytes(result.result_bytes())
                logger.debug(f"Wrote cached result to {output_file}")

                # Report any stored errors/warnings for this cached result
                self._report_cached_issues(
                    payload.input_file,
                    payload.content_hash(),
                    payload.output_metadata(),
                )
                return

        # Check SQLite job cache
        if self.job_queue:
            cached = self.job_queue.check_cache(str(payload.output_file), payload.content_hash())
            if cached:
                logger.debug(f"SQLite cache hit for {payload.output_file}")
                # Output file should already exist from previous run
                output_path = Path(payload.output_file)
                # Make path absolute relative to workspace if not already absolute
                if not output_path.is_absolute():
                    output_path = self.workspace_path / output_path
                if output_path.exists():
                    return
                else:
                    logger.warning(f"Cache indicated file exists but not found: {output_path}")

        # Map service to job type
        service_to_job_type = {
            "notebook-processor": "notebook",
            "drawio-converter": "drawio",
            "plantuml-converter": "plantuml",
        }

        service_name = operation.service_name
        if service_name not in service_to_job_type:
            raise ValueError(f"Unknown service: {service_name}")

        job_type = service_to_job_type[service_name]

        # Check if workers are available for this job type (unless check is skipped for testing)
        if not self.skip_worker_check:
            available_workers = self._get_available_workers(job_type)
            if available_workers == 0:
                raise RuntimeError(
                    f"No workers available to process '{job_type}' jobs. "
                    f"Please start {job_type} workers before submitting jobs. "
                    f"Workers should register in the database within 10 seconds of starting."
                )

            logger.debug(f"Found {available_workers} available worker(s) for job type '{job_type}'")

        # Prepare payload dict using Pydantic's model_dump() with mode='json'
        # This ensures bytes are serialized to base64 strings for JSON compatibility
        payload_dict = payload.model_dump(mode="json")

        # Extract correlation_id from payload
        correlation_id = getattr(payload, "correlation_id", None)

        # Add job to queue (job_queue is always initialized in __attrs_post_init__)
        assert self.job_queue is not None

        # Note: Job cancellation for watch mode is handled by the file_event_handler
        # when a file change is detected, not here. The same source file can produce
        # multiple output files (HTML, .ipynb, .py in multiple languages), so we
        # cannot cancel by input file alone during normal operation submission.

        job_id = self.job_queue.add_job(
            job_type=job_type,
            input_file=str(payload.input_file),
            output_file=str(payload.output_file),
            content_hash=payload.content_hash(),
            payload=payload_dict,
            correlation_id=correlation_id,
        )

        # Track active job
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

    async def wait_for_completion(self) -> bool:
        """Wait for all submitted jobs to complete.

        Returns:
            True if all jobs completed successfully

        Raises:
            TimeoutError: If jobs don't complete within timeout
        """
        if not self.active_jobs:
            return True

        logger.info(f"Waiting for {len(self.active_jobs)} job(s) to complete...")

        # Start progress tracking
        if self.progress_tracker:
            self.progress_tracker.start_progress_logging()

        start_time = asyncio.get_event_loop().time()
        failed_jobs: list[dict[str, Any]] = []
        last_cleanup_time = start_time

        while self.active_jobs:
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

                    # Report file completed to build reporter (for verbose mode output)
                    if self.build_reporter:
                        self.build_reporter.report_file_completed(
                            job_info["input_file"], job_info["job_type"], job_id, success=True
                        )

                    # Notify progress tracker
                    if self.progress_tracker:
                        self.progress_tracker.job_completed(job_id)

                    # Add to database cache if applicable
                    if not self.ignore_db and self.db_manager:
                        output_path = Path(job_info["output_file"])
                        # Make path absolute relative to workspace if not already absolute
                        if not output_path.is_absolute():
                            output_path = self.workspace_path / output_path

                        if output_path.exists():
                            # Read output file and reconstruct Result object to store in database
                            try:
                                # Get the payload from the job to determine job type and metadata
                                # job_queue is guaranteed non-None here (checked at start of loop)
                                conn = self.job_queue._get_conn()
                                cursor = conn.execute(
                                    "SELECT payload, content_hash FROM jobs WHERE id = ?", (job_id,)
                                )
                                row = cursor.fetchone()
                                if row:
                                    from clx.infrastructure.messaging.base_classes import (
                                        ImageResult,
                                        Result,
                                    )
                                    from clx.infrastructure.messaging.notebook_classes import (
                                        NotebookResult,
                                    )

                                    payload_dict = json.loads(row[0])
                                    content_hash = row[1]
                                    correlation_id = job_info.get("correlation_id", "")

                                    # Reconstruct Result object based on job type
                                    job_type = job_info["job_type"]
                                    result_obj: Result | None = None

                                    if job_type == "notebook":
                                        # Read notebook output
                                        result_text = output_path.read_text(encoding="utf-8")
                                        result_obj = NotebookResult(
                                            correlation_id=correlation_id,
                                            output_file=str(job_info["output_file"]),
                                            input_file=str(job_info["input_file"]),
                                            content_hash=content_hash,
                                            result=result_text,
                                            output_metadata_tags=(
                                                payload_dict.get("kind", "participant"),
                                                payload_dict.get("prog_lang", "python"),
                                                payload_dict.get("language", "en"),
                                                payload_dict.get("format", "notebook"),
                                            ),
                                        )
                                    elif job_type in ("plantuml", "drawio"):
                                        # Read image output
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
                                    else:
                                        logger.warning(
                                            f"Unknown job type {job_type}, skipping cache storage"
                                        )

                                    # Store result in database cache
                                    if result_obj is not None:
                                        self.db_manager.store_result(
                                            file_path=job_info["input_file"],
                                            content_hash=content_hash,
                                            correlation_id=correlation_id,
                                            result=result_obj,
                                        )
                                        logger.debug(
                                            f"Stored result for {job_info['input_file']} in database cache"
                                        )
                            except Exception as e:
                                logger.warning(
                                    f"Could not cache result for job {job_id}: {e}", exc_info=True
                                )

                elif status == "failed":
                    # Get job payload for error categorization and storage
                    conn = self.job_queue._get_conn()
                    cursor = conn.execute(
                        "SELECT payload, content_hash FROM jobs WHERE id = ?", (job_id,)
                    )
                    payload_row = cursor.fetchone()
                    payload_dict = json.loads(payload_row[0]) if payload_row else {}
                    content_hash = payload_row[1] if payload_row else ""

                    # Import ErrorCategorizer
                    from clx.cli.error_categorizer import ErrorCategorizer

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
                    # Always store errors regardless of ignore_db - we want to report them in future runs
                    if self.db_manager:
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

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self.max_wait_for_completion_duration:
                raise TimeoutError(
                    f"Jobs did not complete within {self.max_wait_for_completion_duration} seconds. "
                    f"{len(self.active_jobs)} job(s) still pending."
                )

            # Wait before polling again
            if self.active_jobs:
                await asyncio.sleep(self.poll_interval)

        # Stop progress tracking and log summary
        if self.progress_tracker:
            self.progress_tracker.stop_progress_logging()
            self.progress_tracker.log_summary()

        if failed_jobs:
            logger.error(f"{len(failed_jobs)} job(s) failed")
            for failed in failed_jobs:
                logger.error(
                    f"  - Job {failed['job_id']}: {failed['job_info']['input_file']} "
                    f"({failed['error']})"
                )
            return False

        logger.info("All jobs completed successfully")
        return True

    async def shutdown(self):
        """Shutdown the backend."""
        logger.debug("Shutting down SQLite backend")
        # Wait for remaining jobs with shorter timeout
        if self.active_jobs:
            logger.warning(f"Shutdown called with {len(self.active_jobs)} job(s) still pending")
            try:
                await asyncio.wait_for(self.wait_for_completion(), timeout=5.0)
            except TimeoutError:
                logger.warning(f"Shutdown timeout - {len(self.active_jobs)} job(s) still pending")

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

    def _get_available_workers(self, job_type: str) -> int:
        """Query database for available workers of a specific type.

        A worker is considered available if:
        - It matches the requested job_type
        - Its status is 'idle' or 'busy' (not 'hung' or 'dead')
        - It has sent a heartbeat within the last 30 seconds

        Args:
            job_type: Type of job (e.g., 'notebook', 'plantuml', 'drawio')

        Returns:
            Number of available workers for this job type
        """
        if not self.job_queue:
            return 0

        conn = self.job_queue._get_conn()
        cursor = conn.execute(
            """
            SELECT COUNT(*) FROM workers
            WHERE worker_type = ?
            AND status IN ('idle', 'busy')
            AND last_heartbeat > datetime('now', '-30 seconds')
            """,
            (job_type,),
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    def _get_output_metadata(self, job_type: str, payload_dict: dict) -> str:
        """Reconstruct output_metadata string from job payload.

        Args:
            job_type: Type of job (notebook, plantuml, drawio)
            payload_dict: Job payload dictionary

        Returns:
            Output metadata string matching the format used in payload.output_metadata()
        """
        if job_type == "notebook":
            # NotebookPayload.output_metadata() returns tuple of (kind, prog_lang, language, format)
            kind = payload_dict.get("kind", "participant")
            prog_lang = payload_dict.get("prog_lang", "python")
            language = payload_dict.get("language", "en")
            format_val = payload_dict.get("format", "notebook")
            return str((kind, prog_lang, language, format_val))
        elif job_type in ("plantuml", "drawio"):
            # ImagePayload.output_metadata() returns output_format
            output_format = payload_dict.get("output_format", "png")
            return str(output_format)
        else:
            return ""

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
