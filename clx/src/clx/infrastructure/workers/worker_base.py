"""Base worker class for processing jobs from SQLite queue.

This module provides the abstract Worker class that handles job polling,
heartbeat updates, and graceful shutdown.
"""

import time
import signal
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from datetime import datetime

from clx.infrastructure.database.job_queue import JobQueue, Job

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
        poll_interval: float = 0.1
    ):
        """Initialize worker.

        Args:
            worker_id: Unique worker ID (from workers table)
            worker_type: Type of jobs to process ('notebook', 'drawio', 'plantuml')
            db_path: Path to SQLite database
            poll_interval: Time to wait between polls when no jobs available (seconds)
        """
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.job_queue = JobQueue(db_path)
        self.running = True
        self._last_heartbeat = datetime.now()

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown signal."""
        logger.info(f"Worker {self.worker_id} ({self.worker_type}) received shutdown signal")
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
                (self.worker_id,)
            )
            conn.commit()
            self._last_heartbeat = datetime.now()
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
            conn.commit()
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

            conn.commit()
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

    def run(self):
        """Main worker loop.

        Continuously polls for jobs, processes them, and updates status.
        Handles errors and maintains heartbeat.
        """
        logger.info(f"Worker {self.worker_id} ({self.worker_type}) starting")
        self._update_status('idle')
        self._update_heartbeat()

        while self.running:
            try:
                # Get next job
                job = self.job_queue.get_next_job(self.worker_type, self.worker_id)

                if job is None:
                    # No jobs available, update heartbeat and wait
                    self._update_heartbeat()
                    time.sleep(self.poll_interval)
                    continue

                # Process job
                logger.info(
                    f"Worker {self.worker_id} processing job {job.id} "
                    f"({job.job_type}): {job.input_file} -> {job.output_file}"
                )
                self._update_status('busy')

                start_time = time.time()

                try:
                    # Call subclass implementation
                    self.process_job(job)

                    processing_time = time.time() - start_time

                    # Mark job as completed
                    self.job_queue.update_job_status(job.id, 'completed')

                    logger.debug(
                        f"Worker {self.worker_id} finished processing job {job.id} "
                        f"for {job.input_file} in {processing_time:.2f}s"
                    )

                    # Update worker stats
                    self._update_stats(success=True, processing_time=processing_time)

                except Exception as e:
                    processing_time = time.time() - start_time

                    logger.debug(
                        f"Worker {self.worker_id} encountered error processing job {job.id} "
                        f"for {job.input_file} after {processing_time:.2f}s",
                        exc_info=True
                    )

                    # Mark job as failed with error message
                    self.job_queue.update_job_status(job.id, 'failed', str(e))

                    # Update worker stats
                    self._update_stats(success=False, processing_time=processing_time)

                finally:
                    # Always return to idle and update heartbeat
                    self._update_status('idle')
                    self._update_heartbeat()

            except Exception as e:
                # Unexpected error in main loop
                logger.error(
                    f"Worker {self.worker_id} encountered error in main loop: {e}",
                    exc_info=True
                )
                time.sleep(1)  # Back off on errors

        logger.info(f"Worker {self.worker_id} ({self.worker_type}) stopped")

        # Mark as dead on shutdown
        self._update_status('dead')

    def stop(self):
        """Stop the worker gracefully."""
        logger.info(f"Stopping worker {self.worker_id}")
        self.running = False
