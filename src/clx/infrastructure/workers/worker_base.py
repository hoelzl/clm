"""Base worker class for processing jobs from SQLite queue.

This module provides the abstract Worker class that handles job polling,
heartbeat updates, and graceful shutdown.
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
from pathlib import Path

from clx.infrastructure.database.job_queue import Job, JobQueue

logger = logging.getLogger(__name__)


class Worker(ABC):
    """Abstract base class for workers that process jobs from SQLite queue.

    Workers poll the queue for jobs of their type, process them, and update
    the job status. They also maintain heartbeat updates to allow health
    monitoring.
    """

    def __init__(
        self,
        worker_id: int,
        worker_type: str,
        db_path: Path,
        poll_interval: float = 0.1,
        job_timeout: float | None = None,
        heartbeat_interval: float = 2.0,
    ):
        """Initialize worker.

        Args:
            worker_id: Unique worker ID (from workers table)
            worker_type: Type of jobs to process ('notebook', 'drawio', 'plantuml')
            db_path: Path to SQLite database
            poll_interval: Time to wait between polls when no jobs available (seconds)
            job_timeout: Maximum time a job can run before being considered hung (seconds, default: None = no timeout)
            heartbeat_interval: Minimum time between heartbeat updates (seconds, default: 2.0)
        """
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.job_timeout = job_timeout or float("inf")  # Default to infinity (no timeout)
        self.heartbeat_interval = heartbeat_interval
        self.job_queue = JobQueue(db_path)
        self.running = True
        self._last_heartbeat = datetime.now()
        self._loop: asyncio.AbstractEventLoop | None = None  # Persistent event loop

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
        """Update worker heartbeat in database."""
        try:
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

    def _update_status(self, status: str):
        """Update worker status in database.

        Args:
            status: New status ('idle', 'busy', 'hung', 'dead')
        """
        try:
            conn = self.job_queue._get_conn()
            conn.execute("UPDATE workers SET status = ? WHERE id = ?", (status, self.worker_id))
        except Exception as e:
            logger.error(f"Worker {self.worker_id} failed to update status: {e}")

    def _update_stats(self, success: bool, processing_time: float):
        """Update worker statistics after processing a job.

        Args:
            success: Whether the job completed successfully
            processing_time: Time taken to process the job (seconds)
        """
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

        Args:
            job: Job to process

        Raises:
            Exception: Any exception will be caught and the job marked as failed
        """
        pass

    def _log_event(self, event_type: str, message: str, metadata: dict | None = None):
        """Log a worker lifecycle event to the database.

        Args:
            event_type: Type of event (e.g., 'worker_starting', 'worker_stopping')
            message: Human-readable message
            metadata: Optional metadata dictionary
        """
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

        This closes the event loop and cancels pending tasks to prevent
        resource leaks.
        """
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

    def run(self):
        """Main worker loop.

        Continuously polls for jobs, processes them, and updates status.
        Handles errors and maintains heartbeat.
        """
        logger.info(f"Worker {self.worker_id} ({self.worker_type}) starting")

        # Log worker ready event
        self._log_event(
            "worker_ready", f"Worker {self.worker_id} ({self.worker_type}) ready to process jobs"
        )

        self._update_status("idle")
        self._update_heartbeat()

        while self.running:
            try:
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

                    # Mark job as completed
                    self.job_queue.update_job_status(job.id, "completed")

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

        # Mark as dead on shutdown
        self._update_status("dead")

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

        queue = JobQueue(db_path)
        retry_delay = initial_delay

        for attempt in range(max_retries):
            try:
                conn = queue._get_conn()

                cursor = conn.execute(
                    """
                    INSERT INTO workers (worker_type, container_id, status)
                    VALUES (?, ?, 'idle')
                    """,
                    (worker_type, worker_identifier),
                )
                worker_id = cursor.lastrowid
                assert worker_id is not None, "INSERT should always return a valid lastrowid"
                # No commit() needed - connection is in autocommit mode

                logger.info(
                    f"Registered {worker_type} worker {worker_id} (identifier: {worker_identifier})"
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
        raise RuntimeError(f"Failed to register {worker_type} worker after {max_retries} attempts")
