"""Mock worker infrastructure for testing without real subprocesses.

This module provides mock worker implementations that can be used to test
worker lifecycle and coordination logic without starting real worker processes.
Mock workers process jobs in background threads and interact with the real
SQLite job queue, making them suitable for fast integration testing.

Path Validation:
    Mock workers can optionally validate that output paths would work in Docker
    mode. This catches path-related bugs early without requiring actual Docker
    execution. Enable by passing workspace_path to MockWorkerConfig.
"""

import json
import logging
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MockWorkerConfig:
    """Configuration for a mock worker.

    Attributes:
        worker_type: The type of worker (notebook, plantuml, drawio)
        processing_delay: Simulated processing time in seconds (default: 0.1)
        fail_rate: Probability of job failure, 0.0 to 1.0 (default: 0.0)
        poll_interval: Time between job queue polls in seconds (default: 0.05)
        workspace_path: Optional workspace path for path validation (default: None)
        validate_paths: Whether to validate paths for Docker compatibility (default: False)
        data_dir: Optional data directory path for input path validation (default: None)
    """

    worker_type: str
    processing_delay: float = 0.1
    fail_rate: float = 0.0
    poll_interval: float = 0.05
    workspace_path: Path | None = None
    validate_paths: bool = False
    data_dir: Path | None = None


@dataclass
class MockWorkerStats:
    """Statistics for a mock worker.

    Attributes:
        jobs_processed: Number of successfully processed jobs
        jobs_failed: Number of failed jobs
        total_processing_time: Total time spent processing jobs
    """

    jobs_processed: int = 0
    jobs_failed: int = 0
    total_processing_time: float = 0.0


class MockWorker:
    """Mock worker that processes jobs in a background thread.

    This worker interacts with the real SQLite job queue but doesn't perform
    actual file processing. Instead, it simulates processing by waiting for
    a configurable delay and then creating placeholder output files.

    This allows testing of:
    - Worker lifecycle management (start, stop, health checks)
    - Job queue coordination
    - Worker pool management
    - Error handling and retries
    """

    def __init__(self, config: MockWorkerConfig, db_path: Path, worker_id: int):
        """Initialize mock worker.

        Args:
            config: Worker configuration
            db_path: Path to SQLite job queue database
            worker_id: Unique identifier for this worker
        """
        self.config = config
        self.db_path = db_path
        self.worker_id = worker_id
        self.container_id = f"mock-{config.worker_type}-{worker_id}"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._db_worker_id: int | None = None
        self.stats = MockWorkerStats()

    @property
    def db_worker_id(self) -> int | None:
        """Get the database worker ID."""
        return self._db_worker_id

    @property
    def is_running(self) -> bool:
        """Check if the worker thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the mock worker in a background thread."""
        if self._thread is not None:
            raise RuntimeError("Worker already started")

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=self.container_id)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the mock worker.

        Args:
            timeout: Maximum time to wait for the worker thread to stop
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    def _run(self) -> None:
        """Main worker loop - poll for jobs and process them."""
        # Register worker in database
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO workers (worker_type, container_id, status, execution_mode)
                   VALUES (?, ?, 'idle', 'mock')""",
                (self.config.worker_type, self.container_id),
            )
            self._db_worker_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()

        # Main loop
        while not self._stop_event.is_set():
            job = self._poll_job()
            if job:
                self._process_job(job)
            else:
                self._stop_event.wait(self.config.poll_interval)

        # Mark worker as dead
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE workers SET status = 'dead' WHERE container_id = ?",
                (self.container_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def _poll_job(self) -> dict | None:
        """Poll for a pending job from the queue.

        Returns:
            Job dictionary if a job was claimed, None otherwise
        """
        conn = self._get_conn()
        try:
            # Update worker status to idle
            conn.execute(
                "UPDATE workers SET status = 'idle' WHERE container_id = ?",
                (self.container_id,),
            )
            conn.commit()

            # Try to claim a job
            cursor = conn.execute(
                """UPDATE jobs
                   SET status = 'processing', worker_id = ?
                   WHERE id = (
                       SELECT id FROM jobs
                       WHERE job_type = ? AND status = 'pending'
                       ORDER BY created_at ASC
                       LIMIT 1
                   )
                   RETURNING *""",
                (self.container_id, self.config.worker_type),
            )
            row = cursor.fetchone()
            conn.commit()

            if row:
                # Update worker status to busy
                conn.execute(
                    "UPDATE workers SET status = 'busy' WHERE container_id = ?",
                    (self.container_id,),
                )
                conn.commit()
                return dict(row)
            return None
        finally:
            conn.close()

    def _validate_output_path(self, output_path: str) -> str | None:
        """Validate that output path would work in Docker mode.

        Args:
            output_path: The output path from the job

        Returns:
            Error message if validation fails, None if valid
        """
        if not self.config.validate_paths:
            return None

        path = Path(output_path)

        # Check for absolute paths that need workspace validation
        if path.is_absolute():
            if self.config.workspace_path is None:
                return (
                    f"Absolute output path '{output_path}' cannot be validated - "
                    "workspace_path not configured. In Docker mode, absolute host paths "
                    "must be under the mounted workspace directory."
                )

            # Check if path is under workspace
            try:
                path.relative_to(self.config.workspace_path)
            except ValueError:
                return (
                    f"Output path '{output_path}' is not under workspace "
                    f"'{self.config.workspace_path}'. This would fail in Docker mode "
                    "because the path would not be accessible inside the container."
                )

        return None

    def _validate_input_path(self, input_path: str) -> str | None:
        """Validate that input path would work in Docker mode.

        Args:
            input_path: The input path from the job

        Returns:
            Error message if validation fails, None if valid
        """
        if not self.config.validate_paths:
            return None

        path = Path(input_path)

        # Check for absolute paths that need data_dir validation
        if path.is_absolute():
            if self.config.data_dir is None:
                # Input paths need either data_dir mount or payload data
                logger.debug(
                    f"Input path '{input_path}' is absolute but data_dir not configured. "
                    "In Docker mode, this would require the file content in the payload."
                )
                return None  # Not an error, just a warning

            # Check if path is under data_dir
            try:
                path.relative_to(self.config.data_dir)
            except ValueError:
                return (
                    f"Input path '{input_path}' is not under data directory "
                    f"'{self.config.data_dir}'. This would fail in Docker mode "
                    "because the path would not be accessible at /source mount."
                )

        return None

    def _process_job(self, job: dict) -> None:
        """Process a single job.

        Args:
            job: Job dictionary from the database
        """
        start_time = time.time()

        # Validate paths for Docker compatibility (if enabled)
        output_error = self._validate_output_path(job["output_file"])
        if output_error:
            logger.warning(f"Path validation warning: {output_error}")
            # In strict mode, we could fail the job here
            # For now, just log the warning

        input_error = self._validate_input_path(job["input_file"])
        if input_error:
            logger.warning(f"Path validation warning: {input_error}")

        # Simulate processing delay
        time.sleep(self.config.processing_delay)

        # Determine if job should fail
        should_fail = random.random() < self.config.fail_rate

        conn = self._get_conn()
        try:
            if should_fail:
                conn.execute(
                    """UPDATE jobs SET status = 'failed', error = ?, completed_at = datetime('now')
                       WHERE id = ?""",
                    ("Mock failure (simulated)", job["id"]),
                )
                self.stats.jobs_failed += 1
            else:
                # Create mock output file
                output_file = Path(job["output_file"])
                output_file.parent.mkdir(parents=True, exist_ok=True)

                # Write mock content based on job type
                if self.config.worker_type == "notebook":
                    # Write a valid notebook structure
                    mock_notebook = {
                        "cells": [
                            {
                                "cell_type": "code",
                                "execution_count": 1,
                                "metadata": {},
                                "outputs": [
                                    {
                                        "name": "stdout",
                                        "output_type": "stream",
                                        "text": ["Mock output\n"],
                                    }
                                ],
                                "source": ["# Mock processed notebook\n", "print('Mock output')"],
                            }
                        ],
                        "metadata": {
                            "kernelspec": {
                                "display_name": "Python 3",
                                "language": "python",
                                "name": "python3",
                            }
                        },
                        "nbformat": 4,
                        "nbformat_minor": 4,
                    }
                    output_file.write_text(json.dumps(mock_notebook, indent=2))
                elif self.config.worker_type in ("plantuml", "drawio"):
                    # Write placeholder image content
                    output_file.write_bytes(b"MOCK_IMAGE_CONTENT")
                else:
                    output_file.write_text(f"Mock output for job {job['id']}")

                conn.execute(
                    """UPDATE jobs SET status = 'completed', completed_at = datetime('now')
                       WHERE id = ?""",
                    (job["id"],),
                )
                self.stats.jobs_processed += 1

            conn.commit()
        finally:
            conn.close()

        self.stats.total_processing_time += time.time() - start_time


class MockWorkerPool:
    """Pool of mock workers for testing.

    This class manages multiple mock workers and provides a similar interface
    to the real WorkerPoolManager, enabling testing of pool management logic.
    """

    def __init__(self, db_path: Path):
        """Initialize mock worker pool.

        Args:
            db_path: Path to SQLite job queue database
        """
        self.db_path = db_path
        self._workers: list[MockWorker] = []
        self._next_worker_id = 0

    @property
    def workers(self) -> list[MockWorker]:
        """Get list of all workers in the pool."""
        return list(self._workers)

    @property
    def running_workers(self) -> list[MockWorker]:
        """Get list of currently running workers."""
        return [w for w in self._workers if w.is_running]

    def start_workers(
        self,
        worker_type: str,
        count: int,
        processing_delay: float = 0.1,
        fail_rate: float = 0.0,
        poll_interval: float = 0.05,
        workspace_path: Path | None = None,
        data_dir: Path | None = None,
        validate_paths: bool = False,
    ) -> list[MockWorker]:
        """Start multiple mock workers.

        Args:
            worker_type: Type of workers to start (notebook, plantuml, drawio)
            count: Number of workers to start
            processing_delay: Simulated processing time per job
            fail_rate: Probability of job failure (0.0 to 1.0)
            poll_interval: Time between job queue polls
            workspace_path: Workspace path for Docker path validation
            data_dir: Data directory path for input path validation
            validate_paths: Whether to validate paths for Docker compatibility

        Returns:
            List of started MockWorker instances
        """
        config = MockWorkerConfig(
            worker_type=worker_type,
            processing_delay=processing_delay,
            fail_rate=fail_rate,
            poll_interval=poll_interval,
            workspace_path=workspace_path,
            data_dir=data_dir,
            validate_paths=validate_paths,
        )

        workers = []
        for _ in range(count):
            worker = MockWorker(config, self.db_path, self._next_worker_id)
            self._next_worker_id += 1
            worker.start()
            workers.append(worker)

        self._workers.extend(workers)
        return workers

    def stop_all(self, timeout: float = 5.0) -> None:
        """Stop all mock workers.

        Args:
            timeout: Maximum time to wait for each worker to stop
        """
        for worker in self._workers:
            worker.stop(timeout=timeout)
        self._workers.clear()

    def stop_workers(self, workers: list[MockWorker], timeout: float = 5.0) -> None:
        """Stop specific workers.

        Args:
            workers: List of workers to stop
            timeout: Maximum time to wait for each worker to stop
        """
        for worker in workers:
            worker.stop(timeout=timeout)
            if worker in self._workers:
                self._workers.remove(worker)

    def get_stats(self) -> dict:
        """Get aggregate statistics for all workers.

        Returns:
            Dictionary with aggregate statistics
        """
        total_processed = sum(w.stats.jobs_processed for w in self._workers)
        total_failed = sum(w.stats.jobs_failed for w in self._workers)
        total_time = sum(w.stats.total_processing_time for w in self._workers)

        return {
            "total_workers": len(self._workers),
            "running_workers": len(self.running_workers),
            "jobs_processed": total_processed,
            "jobs_failed": total_failed,
            "total_processing_time": total_time,
        }

    def wait_for_jobs(self, timeout: float = 30.0, poll_interval: float = 0.1) -> bool:
        """Wait for all jobs to be processed.

        Args:
            timeout: Maximum time to wait
            poll_interval: Time between checks

        Returns:
            True if all jobs completed, False if timeout occurred
        """
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        start_time = time.time()

        try:
            while time.time() - start_time < timeout:
                cursor = conn.execute(
                    "SELECT COUNT(*) as count FROM jobs WHERE status IN ('pending', 'processing')"
                )
                row = cursor.fetchone()
                if row["count"] == 0:
                    return True
                time.sleep(poll_interval)
            return False
        finally:
            conn.close()
