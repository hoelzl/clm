"""Base worker class for processing jobs from SQLite queue.

This module provides the abstract Worker class that handles job polling,
heartbeat updates, and graceful shutdown.

Workers can operate in two modes:
1. Direct SQLite mode (default): Workers communicate with the database directly
2. REST API mode: Workers communicate via HTTP API (for Docker containers)

The mode is determined by the presence of CLX_API_URL environment variable
or the api_url parameter.
"""

import asyncio
import json
import logging
import os
import signal
import sqlite3
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from clx.infrastructure.database.job_queue import Job, JobQueue
from clx.infrastructure.messaging.base_classes import ProcessingWarning

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Docker container mount points
CONTAINER_WORKSPACE = "/workspace"  # Output directory (read-write)
CONTAINER_SOURCE = "/source"  # Source data directory (read-only)


def _convert_path_to_container(host_path: str, host_base: str, container_base: str) -> Path:
    """Convert a host file path to a container path.

    Args:
        host_path: Absolute path on the host
        host_base: The host base path that is mounted
        container_base: The container mount point

    Returns:
        Path object for the container path

    Raises:
        ValueError: If host_path is not under host_base
    """
    # Normalize paths for comparison
    # Handle both Windows and Unix paths from the host
    host_path_obj: PureWindowsPath | PurePosixPath
    base_obj: PureWindowsPath | PurePosixPath
    if "\\" in host_path or (len(host_path) > 1 and host_path[1] == ":"):
        # Windows-style path
        host_path_obj = PureWindowsPath(host_path)
        base_obj = PureWindowsPath(host_base)
    else:
        # Unix-style path
        host_path_obj = PurePosixPath(host_path)
        base_obj = PurePosixPath(host_base)

    # Check if host_path is under host_base
    try:
        relative = host_path_obj.relative_to(base_obj)
    except ValueError as err:
        raise ValueError(f"Path '{host_path}' is not under '{host_base}'") from err

    # Convert to container path (always POSIX in container)
    # Use forward slashes for the relative path
    relative_posix = str(relative).replace("\\", "/")
    container_path = Path(container_base) / relative_posix

    return container_path


def convert_host_path_to_container(host_path: str, host_workspace: str) -> Path:
    """Convert a host output path to a container path.

    When running in Docker, output files are mounted at /workspace. This function
    converts absolute host paths to the corresponding container paths.

    Args:
        host_path: Absolute path on the host (e.g., C:\\Users\\...\\output\\file.txt
                   or /home/user/.../output/file.txt)
        host_workspace: The host workspace path that is mounted at /workspace

    Returns:
        Path object for the container path (e.g., /workspace/file.txt)

    Raises:
        ValueError: If host_path is not under host_workspace
    """
    return _convert_path_to_container(host_path, host_workspace, CONTAINER_WORKSPACE)


def convert_input_path_to_container(host_path: str, host_data_dir: str) -> Path:
    """Convert a host input path to a container path.

    When running in Docker, source files are mounted at /source. This function
    converts absolute host paths to the corresponding container paths.

    Args:
        host_path: Absolute path on the host (e.g., C:\\Users\\...\\slides\\file.cpp
                   or /home/user/.../slides/file.cpp)
        host_data_dir: The host data directory that is mounted at /source

    Returns:
        Path object for the container path (e.g., /source/slides/file.cpp)

    Raises:
        ValueError: If host_path is not under host_data_dir
    """
    return _convert_path_to_container(host_path, host_data_dir, CONTAINER_SOURCE)


class Worker(ABC):
    """Abstract base class for workers that process jobs from SQLite queue.

    Workers poll the queue for jobs of their type, process them, and update
    the job status. They also maintain heartbeat updates to allow health
    monitoring.

    Workers also monitor their parent process and will exit gracefully if
    the parent process dies (e.g., CLX crashes or is killed).
    """

    # Default interval for checking if parent process is still alive (seconds)
    DEFAULT_PARENT_CHECK_INTERVAL = 5.0

    def __init__(
        self,
        worker_id: int,
        worker_type: str,
        db_path: Path | None = None,
        poll_interval: float = 0.1,
        job_timeout: float | None = None,
        heartbeat_interval: float = 2.0,
        parent_check_interval: float | None = None,
        api_url: str | None = None,
    ):
        """Initialize worker.

        Args:
            worker_id: Unique worker ID (from workers table)
            worker_type: Type of jobs to process ('notebook', 'drawio', 'plantuml')
            db_path: Path to SQLite database (required for direct mode)
            poll_interval: Time to wait between polls when no jobs available (seconds)
            job_timeout: Maximum time a job can run before being considered hung (seconds, default: None = no timeout)
            heartbeat_interval: Minimum time between heartbeat updates (seconds, default: 2.0)
            parent_check_interval: Interval for checking if parent process is alive (seconds, default: 5.0)
            api_url: URL of the Worker REST API (for Docker mode)
        """
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.db_path = db_path
        self.api_url = api_url
        self.poll_interval = poll_interval
        self.job_timeout = job_timeout or float("inf")  # Default to infinity (no timeout)
        self.heartbeat_interval = heartbeat_interval
        self.parent_check_interval = parent_check_interval or self.DEFAULT_PARENT_CHECK_INTERVAL
        self.running = True
        self._last_heartbeat = datetime.now()
        self._last_parent_check = datetime.now()
        self._loop: asyncio.AbstractEventLoop | None = None  # Persistent event loop

        # Determine mode and create appropriate job queue
        self._api_mode = api_url is not None
        if self._api_mode:
            from clx.infrastructure.api.job_queue_adapter import ApiJobQueue

            assert api_url is not None  # Type narrowing
            self.job_queue: JobQueue | ApiJobQueue = ApiJobQueue(api_url, worker_id)
            logger.info(f"Worker {worker_id} using REST API mode: {api_url}")
        else:
            if db_path is None:
                raise ValueError("db_path is required when not using API mode")
            self.job_queue = JobQueue(db_path)

        # Per-job warnings collection
        self._current_job_warnings: list[ProcessingWarning] = []

        # Store parent process ID for orphan detection
        self.parent_pid = os.getppid()
        logger.debug(f"Worker {worker_id} initialized with parent PID: {self.parent_pid}")

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown signal."""
        logger.info(f"Worker {self.worker_id} ({self.worker_type}) received shutdown signal")

        # Log stopping event
        self._log_event(
            "worker_stopping",
            f"Worker {self.worker_id} received shutdown signal {signum}",
            {"signal": signum},
        )

        self.running = False

    def _update_heartbeat(self):
        """Update worker heartbeat in database or via API."""
        try:
            if self._api_mode:
                # Use API adapter's heartbeat method
                from clx.infrastructure.api.job_queue_adapter import ApiJobQueue

                assert isinstance(self.job_queue, ApiJobQueue)
                self.job_queue.update_heartbeat(self.worker_id)
            else:
                conn = self.job_queue._get_conn()
                conn.execute(
                    """
                    UPDATE workers
                    SET last_heartbeat = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (self.worker_id,),
                )
            self._last_heartbeat = datetime.now()
        except Exception as e:
            logger.error(f"Worker {self.worker_id} failed to update heartbeat: {e}")

    def _should_update_heartbeat(self) -> bool:
        """Check if enough time has passed to send another heartbeat.

        This throttles heartbeat updates to reduce database write overhead.
        With 8 idle workers at 100ms poll interval, this reduces heartbeat
        writes from 80/sec to 4/sec (8 workers / 2 second interval).

        Returns:
            True if heartbeat should be updated, False otherwise
        """
        elapsed = (datetime.now() - self._last_heartbeat).total_seconds()
        return elapsed >= self.heartbeat_interval

    def _is_parent_alive(self) -> bool:
        """Check if parent process is still running.

        This is used to detect when the parent CLX process has crashed or been
        killed, allowing the worker to exit gracefully instead of becoming
        an orphan process.

        Returns:
            True if parent process exists, False otherwise
        """
        try:
            # Signal 0 doesn't actually send a signal, just checks if process exists
            os.kill(self.parent_pid, 0)
            return True
        except OSError:
            # Process doesn't exist or we don't have permission to signal it
            return False
        except Exception as e:
            # Unexpected error - log and assume parent is alive to be safe
            logger.warning(f"Error checking parent process {self.parent_pid}: {e}")
            return True

    def _should_check_parent(self) -> bool:
        """Check if enough time has passed to check parent process status.

        This throttles parent checks to reduce overhead while still detecting
        orphan status reasonably quickly.

        Returns:
            True if parent should be checked, False otherwise
        """
        elapsed = (datetime.now() - self._last_parent_check).total_seconds()
        return elapsed >= self.parent_check_interval

    def _check_parent_and_exit_if_dead(self) -> bool:
        """Check if parent is alive and initiate shutdown if dead.

        Returns:
            True if worker should exit (parent is dead), False otherwise
        """
        if not self._should_check_parent():
            return False

        self._last_parent_check = datetime.now()

        if not self._is_parent_alive():
            logger.warning(
                f"Worker {self.worker_id} ({self.worker_type}): "
                f"Parent process {self.parent_pid} is no longer running. "
                f"Initiating graceful shutdown to avoid becoming orphaned."
            )

            # Log event for debugging/monitoring
            self._log_event(
                "parent_died",
                f"Worker {self.worker_id} detected parent process {self.parent_pid} death",
                {"parent_pid": self.parent_pid},
            )

            self.running = False
            return True

        return False

    def _update_status(self, status: str):
        """Update worker status in database.

        In API mode, status updates are handled by the API server when
        jobs are claimed/completed, so this is a no-op.

        Args:
            status: New status ('idle', 'busy', 'hung', 'dead')
        """
        if self._api_mode:
            # Status managed by API server
            return

        try:
            conn = self.job_queue._get_conn()
            conn.execute("UPDATE workers SET status = ? WHERE id = ?", (status, self.worker_id))
        except Exception as e:
            logger.error(f"Worker {self.worker_id} failed to update status: {e}")

    def _deregister(self):
        """Remove worker from database on graceful shutdown.

        This removes the worker row entirely from the database rather than
        marking it as 'dead', which keeps the workers table clean after
        a build completes.
        """
        try:
            if self._api_mode:
                # Use API client to unregister
                from clx.infrastructure.api.client import WorkerApiClient

                assert self.api_url is not None  # Type narrowing
                client = WorkerApiClient(self.api_url)
                try:
                    client.unregister(self.worker_id)
                    logger.debug(f"Worker {self.worker_id} unregistered via API")
                finally:
                    client.close()
            else:
                conn = self.job_queue._get_conn()
                conn.execute("DELETE FROM workers WHERE id = ?", (self.worker_id,))
                conn.commit()
                logger.debug(f"Worker {self.worker_id} deregistered from database")
        except Exception as e:
            logger.error(f"Worker {self.worker_id} failed to deregister: {e}")

    def _update_stats(self, success: bool, processing_time: float):
        """Update worker statistics after processing a job.

        In API mode, stats are tracked by the API server when jobs
        complete, so this is a no-op.

        Args:
            success: Whether the job completed successfully
            processing_time: Time taken to process the job (seconds)
        """
        if self._api_mode:
            # Stats managed by API server
            return

        try:
            conn = self.job_queue._get_conn()

            if success:
                # Update jobs_processed and average processing time
                conn.execute(
                    """
                    UPDATE workers
                    SET jobs_processed = jobs_processed + 1,
                        avg_processing_time = CASE
                            WHEN avg_processing_time IS NULL THEN ?
                            ELSE (avg_processing_time * jobs_processed + ?) / (jobs_processed + 1)
                        END
                    WHERE id = ?
                    """,
                    (processing_time, processing_time, self.worker_id),
                )
            else:
                # Update jobs_failed
                conn.execute(
                    """
                    UPDATE workers
                    SET jobs_failed = jobs_failed + 1
                    WHERE id = ?
                    """,
                    (self.worker_id,),
                )
        except Exception as e:
            logger.error(f"Worker {self.worker_id} failed to update stats: {e}")

    @abstractmethod
    def process_job(self, job: Job) -> None:
        """Process a job. Must be implemented by subclass.

        This method should:
        1. Read the input file specified in job.input_file
        2. Process it according to job.payload
        3. Write the result to job.output_file
        4. Optionally add result to cache using self.job_queue.add_to_cache()
        5. Optionally call set_job_warnings() to attach warnings to the job result

        Args:
            job: Job to process

        Raises:
            Exception: Any exception will be caught and the job marked as failed
        """
        pass

    def set_job_warnings(self, warnings: list[ProcessingWarning]) -> None:
        """Set warnings for the current job.

        This method should be called by process_job() implementations to
        attach warnings to the job result. The warnings will be stored
        in the database when the job completes.

        Args:
            warnings: List of processing warnings
        """
        self._current_job_warnings = warnings

    def _clear_job_warnings(self) -> None:
        """Clear warnings for the current job."""
        self._current_job_warnings = []

    def _get_job_result_json(self) -> str | None:
        """Get job result as JSON string for storing in database.

        Returns:
            JSON string with warnings, or None if no warnings
        """
        if not self._current_job_warnings:
            return None

        result_data = {
            "warnings": [w.model_dump() for w in self._current_job_warnings],
        }
        return json.dumps(result_data)

    def _log_event(self, event_type: str, message: str, metadata: dict | None = None):
        """Log a worker lifecycle event to the database.

        In API mode, event logging is skipped as it's not critical for
        worker operation and would require additional API endpoints.

        Args:
            event_type: Type of event (e.g., 'worker_starting', 'worker_stopping')
            message: Human-readable message
            metadata: Optional metadata dictionary
        """
        if self._api_mode:
            # Event logging skipped in API mode - just log locally
            logger.debug(f"Event {event_type}: {message}")
            return

        try:
            conn = self.job_queue._get_conn()
            conn.execute(
                """
                INSERT INTO worker_events
                (event_type, worker_id, worker_type, execution_mode, message, metadata, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    self.worker_id,
                    self.worker_type,
                    "direct",  # Assume direct; Docker workers can override if needed
                    message,
                    json.dumps(metadata) if metadata else None,
                    None,  # No session ID in base worker
                ),
            )
        except Exception as e:
            # Don't fail the worker if event logging fails
            logger.debug(f"Failed to log event {event_type}: {e}")

    def _get_or_create_loop(self):
        """Get or create the event loop for this worker.

        This ensures we reuse the same event loop across all job processing,
        avoiding the overhead and potential issues of creating a new loop
        for each job with asyncio.run().

        Returns:
            asyncio.AbstractEventLoop: The event loop for this worker
        """
        if self._loop is None or self._loop.is_closed():
            try:
                # Try to get the current running loop (if we're already in async context)
                self._loop = asyncio.get_running_loop()
                logger.debug(f"Worker {self.worker_id}: Using existing event loop")
            except RuntimeError:
                # No running loop, create a new one
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                logger.debug(f"Worker {self.worker_id}: Created new event loop")
        return self._loop

    def cleanup(self):
        """Clean up resources when worker stops.

        This closes the event loop, cancels pending tasks, and closes
        database connections to prevent resource leaks.
        """
        # Close event loop
        if self._loop is not None and not self._loop.is_closed():
            logger.debug(f"Worker {self.worker_id}: Closing event loop")
            try:
                # Cancel all pending tasks
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                # Run the loop one more time to handle cancellations
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception as e:
                logger.warning(f"Worker {self.worker_id}: Error during loop cleanup: {e}")
            finally:
                self._loop.close()
                self._loop = None

        # Close database connection
        if hasattr(self, "job_queue") and self.job_queue is not None:
            logger.debug(f"Worker {self.worker_id}: Closing job queue connection")
            self.job_queue.close()

    def run(self):
        """Main worker loop.

        Continuously polls for jobs, processes them, and updates status.
        Handles errors and maintains heartbeat.

        The worker will exit gracefully if it detects that its parent process
        has died (e.g., CLX crashed or was killed), preventing orphan processes.
        """
        logger.info(
            f"Worker {self.worker_id} ({self.worker_type}) starting (parent PID: {self.parent_pid})"
        )

        # Log worker ready event
        self._log_event(
            "worker_ready",
            f"Worker {self.worker_id} ({self.worker_type}) ready to process jobs",
            {"parent_pid": self.parent_pid},
        )

        self._update_status("idle")
        self._update_heartbeat()

        while self.running:
            try:
                # Check if parent process is still alive (throttled)
                # This prevents workers from becoming orphans when CLX crashes
                if self._check_parent_and_exit_if_dead():
                    break

                # Get next job
                job = self.job_queue.get_next_job(self.worker_type, self.worker_id)

                if job is None:
                    # No jobs available, update heartbeat (throttled) and wait
                    if self._should_update_heartbeat():
                        self._update_heartbeat()
                    time.sleep(self.poll_interval)
                    continue

                # Process job
                logger.info(
                    f"Worker {self.worker_id} processing job {job.id} "
                    f"({job.job_type}): {job.input_file} -> {job.output_file}"
                )
                self._update_status("busy")

                # Clear warnings from previous job
                self._clear_job_warnings()

                start_time = time.time()

                try:
                    # Process job with timeout enforcement
                    # We check elapsed time and fail if it exceeds the timeout
                    self.process_job(job)

                    processing_time = time.time() - start_time

                    # Check if job exceeded timeout
                    if processing_time > self.job_timeout:
                        raise TimeoutError(
                            f"Job processing exceeded timeout of {self.job_timeout}s "
                            f"(actual: {processing_time:.2f}s)"
                        )

                    # Mark job as completed (with warnings if any)
                    result_json = self._get_job_result_json()
                    self.job_queue.update_job_status(job.id, "completed", result=result_json)

                    if self._current_job_warnings:
                        logger.debug(
                            f"Worker {self.worker_id} job {job.id} completed "
                            f"with {len(self._current_job_warnings)} warning(s)"
                        )

                    logger.debug(
                        f"Worker {self.worker_id} finished processing job {job.id} "
                        f"for {job.input_file} in {processing_time:.2f}s"
                    )

                    # Update worker stats
                    self._update_stats(success=True, processing_time=processing_time)

                except Exception as e:
                    processing_time = time.time() - start_time

                    # Log with appropriate level based on error type
                    if isinstance(e, TimeoutError):
                        logger.error(
                            f"Worker {self.worker_id} TIMEOUT processing job {job.id} "
                            f"for {job.input_file} after {processing_time:.2f}s"
                        )
                    else:
                        logger.debug(
                            f"Worker {self.worker_id} encountered error processing job {job.id} "
                            f"for {job.input_file} after {processing_time:.2f}s",
                            exc_info=True,
                        )

                    # Create structured error message (JSON) for better error reporting
                    error_info = {
                        "error_message": str(e),
                        "error_class": type(e).__name__,
                        "traceback": traceback.format_exc(),
                        "processing_time": processing_time,
                        "worker_type": self.worker_type,
                    }
                    if isinstance(e, TimeoutError):
                        error_info["timeout"] = True

                    # Add error categorization for better monitoring integration
                    try:
                        from clx.cli.error_categorizer import ErrorCategorizer

                        categorized = ErrorCategorizer.categorize_job_error(
                            job_type=job.job_type,
                            input_file=job.input_file,
                            error_message=str(e),
                            job_payload=job.payload,
                        )

                        # Add categorization fields to error info
                        error_info.update(
                            {
                                "error_type": categorized.error_type,
                                "category": categorized.category,
                                "severity": categorized.severity,
                                "actionable_guidance": categorized.actionable_guidance,
                                "details": categorized.details,
                            }
                        )
                    except Exception as cat_error:
                        # If categorization fails, log but don't break error reporting
                        logger.debug(f"Failed to categorize error: {cat_error}")

                    error_msg = json.dumps(error_info)
                    self.job_queue.update_job_status(job.id, "failed", error_msg)

                    # Update worker stats
                    self._update_stats(success=False, processing_time=processing_time)

                finally:
                    # Always return to idle and update heartbeat
                    self._update_status("idle")
                    self._update_heartbeat()

            except Exception as e:
                # Unexpected error in main loop
                logger.error(
                    f"Worker {self.worker_id} encountered error in main loop: {e}", exc_info=True
                )

                # Log failure event
                self._log_event(
                    "worker_failed",
                    f"Worker {self.worker_id} encountered fatal error: {str(e)}",
                    {"error": str(e), "error_type": type(e).__name__},
                )

                time.sleep(1)  # Back off on errors

        logger.info(f"Worker {self.worker_id} ({self.worker_type}) stopped")

        # Log worker stopped event
        self._log_event(
            "worker_stopped", f"Worker {self.worker_id} ({self.worker_type}) shutdown completed"
        )

        # Deregister from database on graceful shutdown
        # This removes the worker row entirely rather than marking as 'dead'
        self._deregister()

        # Clean up resources (event loop, database connections)
        self.cleanup()

    def stop(self):
        """Stop the worker gracefully."""
        logger.info(f"Stopping worker {self.worker_id}")
        self.running = False

    @staticmethod
    def register_worker_with_retry(
        db_path: Path, worker_type: str, max_retries: int = 5, initial_delay: float = 0.5
    ) -> int:
        """Register a new worker in the database with retry logic.

        This is a helper method that can be called by worker main() functions
        to register the worker in the database with exponential backoff retry
        logic to handle transient database lock errors.

        The registration includes the parent process ID (PPID) which allows
        for orphan detection - workers can be identified and cleaned up if
        their parent process dies.

        Args:
            db_path: Path to SQLite database
            worker_type: Type of worker ('notebook', 'plantuml', 'drawio')
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay in seconds between retries (doubles each attempt)

        Returns:
            int: Worker ID from database

        Raises:
            sqlite3.OperationalError: If registration fails after all retries
        """
        # Get worker ID from environment
        # For direct execution: WORKER_ID is set explicitly
        # For Docker: HOSTNAME is the container ID
        worker_identifier = os.getenv("WORKER_ID") or os.getenv("HOSTNAME", "unknown")

        # Get parent process ID for orphan detection
        parent_pid = os.getppid()

        queue = JobQueue(db_path)
        retry_delay = initial_delay

        try:
            for attempt in range(max_retries):
                try:
                    conn = queue._get_conn()

                    cursor = conn.execute(
                        """
                        INSERT INTO workers (worker_type, container_id, status, parent_pid)
                        VALUES (?, ?, 'idle', ?)
                        """,
                        (worker_type, worker_identifier, parent_pid),
                    )
                    worker_id = cursor.lastrowid
                    assert worker_id is not None, "INSERT should always return a valid lastrowid"
                    # No commit() needed - connection is in autocommit mode

                    logger.info(
                        f"Registered {worker_type} worker {worker_id} "
                        f"(identifier: {worker_identifier}, parent_pid: {parent_pid})"
                    )
                    return worker_id

                except sqlite3.OperationalError as e:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Failed to register {worker_type} worker "
                            f"(attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {retry_delay}s..."
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        logger.error(
                            f"Failed to register {worker_type} worker after {max_retries} attempts: {e}"
                        )
                        raise

            # This should never be reached - loop either returns or raises
            raise RuntimeError(
                f"Failed to register {worker_type} worker after {max_retries} attempts"
            )
        finally:
            queue.close()

    @staticmethod
    def register_worker_via_api(
        api_url: str, worker_type: str, max_retries: int = 10, initial_delay: float = 1.0
    ) -> int:
        """Register a new worker via REST API with retry logic.

        This is used by Docker workers that communicate via the REST API
        instead of direct SQLite access. Uses longer retry times since
        the API server may not be ready immediately when the container starts.

        Args:
            api_url: URL of the Worker API (e.g., 'http://host.docker.internal:8765')
            worker_type: Type of worker ('notebook', 'plantuml', 'drawio')
            max_retries: Maximum number of retry attempts (default: 10)
            initial_delay: Initial delay in seconds between retries (default: 1.0)

        Returns:
            int: Worker ID from database

        Raises:
            WorkerApiError: If registration fails after all retries
        """
        from clx.infrastructure.api.client import WorkerApiClient, WorkerApiError

        # Get worker identifier from environment
        worker_identifier = os.getenv("HOSTNAME", "unknown")

        client = WorkerApiClient(api_url)
        retry_delay = initial_delay

        try:
            for attempt in range(max_retries):
                try:
                    worker_id = client.register(worker_type)
                    logger.info(
                        f"Registered {worker_type} worker {worker_id} via API "
                        f"(identifier: {worker_identifier})"
                    )
                    return worker_id

                except WorkerApiError as e:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Failed to register {worker_type} worker via API "
                            f"(attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {retry_delay}s..."
                        )
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 1.5, 10.0)  # Cap at 10 seconds
                    else:
                        logger.error(
                            f"Failed to register {worker_type} worker via API "
                            f"after {max_retries} attempts: {e}"
                        )
                        raise

                except Exception as e:
                    # Handle connection errors (API not ready yet)
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Connection error registering {worker_type} worker "
                            f"(attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {retry_delay}s..."
                        )
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 1.5, 10.0)
                    else:
                        logger.error(f"Failed to connect to API after {max_retries} attempts: {e}")
                        raise WorkerApiError(f"Failed to register: {e}") from e

            # This should never be reached
            raise RuntimeError(
                f"Failed to register {worker_type} worker via API after {max_retries} attempts"
            )
        finally:
            client.close()
