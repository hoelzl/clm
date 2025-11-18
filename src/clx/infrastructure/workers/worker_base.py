"""Base worker class for processing jobs from SQLite queue.

This module provides the abstract Worker class that handles job polling,
heartbeat updates, and graceful shutdown.
"""

import os
import time
import signal
import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from datetime import datetime

from clx.infrastructure.database.job_queue import JobQueue, Job

logger = logging.getLogger(__name__)

# Configuration from environment variables for performance tuning
DEFAULT_POLL_INTERVAL = float(os.getenv('CLX_WORKER_POLL_INTERVAL', '0.1'))
DEFAULT_HEARTBEAT_INTERVAL = float(os.getenv('CLX_WORKER_HEARTBEAT_INTERVAL', '5.0'))
DEFAULT_MAX_POLL_INTERVAL = float(os.getenv('CLX_WORKER_MAX_POLL_INTERVAL', '1.0'))


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
        poll_interval: Optional[float] = None,
        heartbeat_interval: Optional[float] = None,
        job_timeout: Optional[float] = None
    ):
        """Initialize worker.

        Args:
            worker_id: Unique worker ID (from workers table)
            worker_type: Type of jobs to process ('notebook', 'drawio', 'plantuml')
            db_path: Path to SQLite database
            poll_interval: Time to wait between polls when no jobs available (seconds, default: from env or 0.1)
            heartbeat_interval: Time between heartbeat updates (seconds, default: from env or 5.0)
            job_timeout: Maximum time a job can run before being considered hung (seconds, default: None = no timeout)
        """
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.db_path = db_path

        # Use environment variable defaults if not specified
        self.poll_interval = poll_interval if poll_interval is not None else DEFAULT_POLL_INTERVAL
        self.heartbeat_interval = heartbeat_interval if heartbeat_interval is not None else DEFAULT_HEARTBEAT_INTERVAL
        self.max_poll_interval = DEFAULT_MAX_POLL_INTERVAL

        self.job_timeout = job_timeout or float('inf')  # Default to infinity (no timeout)
        self.job_queue = JobQueue(db_path)
        self.running = True
        self._last_heartbeat = datetime.now()
        self._last_heartbeat_update = datetime.now()  # Track last DB update separately

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown signal."""
        logger.info(f"Worker {self.worker_id} ({self.worker_type}) received shutdown signal")

        # Log stopping event
        self._log_event(
            'worker_stopping',
            f"Worker {self.worker_id} received shutdown signal {signum}",
            {'signal': signum}
        )

        self.running = False

    def _update_heartbeat(self, force: bool = False):
        """Update worker heartbeat in database.

        Args:
            force: If True, update immediately regardless of interval
        """
        now = datetime.now()
        time_since_last_update = (now - self._last_heartbeat_update).total_seconds()

        # Only update if enough time has passed or forced
        if not force and time_since_last_update < self.heartbeat_interval:
            return

        try:
            conn = self.job_queue._get_conn()
            conn.execute(
                """
                UPDATE workers
                SET last_heartbeat = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self.worker_id,)
            )
            self._last_heartbeat = now
            self._last_heartbeat_update = now
        except Exception as e:
            logger.error(f"Worker {self.worker_id} failed to update heartbeat: {e}")

    def _update_status(self, status: str):
        """Update worker status in database.

        Args:
            status: New status ('idle', 'busy', 'hung', 'dead')
        """
        try:
            conn = self.job_queue._get_conn()
            conn.execute(
                "UPDATE workers SET status = ? WHERE id = ?",
                (status, self.worker_id)
            )
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
                    (processing_time, processing_time, self.worker_id)
                )
            else:
                # Update jobs_failed
                conn.execute(
                    """
                    UPDATE workers
                    SET jobs_failed = jobs_failed + 1
                    WHERE id = ?
                    """,
                    (self.worker_id,)
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

    def _log_event(self, event_type: str, message: str, metadata: Optional[dict] = None):
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
                    'direct',  # Assume direct; Docker workers can override if needed
                    message,
                    json.dumps(metadata) if metadata else None,
                    None  # No session ID in base worker
                )
            )
        except Exception as e:
            # Don't fail the worker if event logging fails
            logger.debug(f"Failed to log event {event_type}: {e}")

    def run(self):
        """Main worker loop.

        Continuously polls for jobs, processes them, and updates status.
        Handles errors and maintains heartbeat.
        Uses adaptive polling to reduce database load when idle.
        """
        logger.info(f"Worker {self.worker_id} ({self.worker_type}) starting")

        # Log worker ready event
        self._log_event(
            'worker_ready',
            f"Worker {self.worker_id} ({self.worker_type}) ready to process jobs"
        )

        self._update_status('idle')
        self._update_heartbeat(force=True)  # Initial heartbeat

        # Adaptive polling state
        current_poll_interval = self.poll_interval
        no_job_count = 0
        consecutive_empty_checks = 0  # Track truly consecutive empty checks

        while self.running:
            try:
                # Get next job
                job = self.job_queue.get_next_job(self.worker_type, self.worker_id)

                if job is None:
                    # No jobs available - use adaptive polling
                    no_job_count += 1
                    consecutive_empty_checks += 1

                    # More aggressive backoff threshold (50 instead of 10)
                    # This gives ~5 seconds of fast polling before backing off
                    # 50 polls * 0.1s = 5s
                    if consecutive_empty_checks >= 50:
                        current_poll_interval = min(self.max_poll_interval, current_poll_interval * 1.2)

                    # Update heartbeat (respects interval check)
                    self._update_heartbeat()

                    time.sleep(current_poll_interval)
                    continue

                # Job found - reset adaptive polling state
                no_job_count = 0
                consecutive_empty_checks = 0  # Reset consecutive counter
                current_poll_interval = self.poll_interval

                # Process job
                logger.info(
                    f"Worker {self.worker_id} processing job {job.id} "
                    f"({job.job_type}): {job.input_file} -> {job.output_file}"
                )
                self._update_status('busy')

                start_time = time.time()
                job_succeeded = False

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
                    self.job_queue.update_job_status(job.id, 'completed')

                    logger.debug(
                        f"Worker {self.worker_id} finished processing job {job.id} "
                        f"for {job.input_file} in {processing_time:.2f}s"
                    )

                    # Update worker stats
                    self._update_stats(success=True, processing_time=processing_time)
                    job_succeeded = True

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
                            exc_info=True
                        )

                    # Mark job as failed with error message
                    error_msg = str(e)
                    if isinstance(e, TimeoutError):
                        error_msg = f"Job timeout: {error_msg}"
                    self.job_queue.update_job_status(job.id, 'failed', error_msg)

                    # Update worker stats
                    self._update_stats(success=False, processing_time=processing_time)

                finally:
                    # Always return to idle and update heartbeat after job
                    self._update_status('idle')
                    self._update_heartbeat(force=True)  # Force update after job completion

            except Exception as e:
                # Unexpected error in main loop
                logger.error(
                    f"Worker {self.worker_id} encountered error in main loop: {e}",
                    exc_info=True
                )

                # Log failure event
                self._log_event(
                    'worker_failed',
                    f"Worker {self.worker_id} encountered fatal error: {str(e)}",
                    {'error': str(e), 'error_type': type(e).__name__}
                )

                time.sleep(1)  # Back off on errors

        logger.info(f"Worker {self.worker_id} ({self.worker_type}) stopped")

        # Log worker stopped event
        self._log_event(
            'worker_stopped',
            f"Worker {self.worker_id} ({self.worker_type}) shutdown completed"
        )

        # Mark as dead on shutdown
        self._update_status('dead')

    def stop(self):
        """Stop the worker gracefully."""
        logger.info(f"Stopping worker {self.worker_id}")
        self.running = False
