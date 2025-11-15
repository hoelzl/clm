"""Progress tracking and logging for job execution.

This module provides centralized progress tracking and periodic logging
for monitoring job execution during e2e tests and production workloads.
"""

import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class JobInfo:
    """Information about a tracked job."""

    job_id: int
    job_type: str
    input_file: str
    correlation_id: Optional[str] = None
    worker_id: Optional[str] = None
    started_at: Optional[datetime] = None
    submitted_at: datetime = field(default_factory=datetime.now)


class ProgressTracker:
    """Centralized progress tracking for job execution.

    This class tracks job lifecycle events and provides periodic progress
    logging to help users understand what's happening during long-running
    operations like e2e tests.

    Features:
    - Track submitted/completed/failed/pending jobs
    - Monitor active workers and their current jobs
    - Periodic progress logging (configurable interval)
    - Warnings for long-running jobs
    - Final summary statistics
    """

    def __init__(
        self,
        progress_interval: float = 5.0,
        long_job_threshold: float = 30.0,
        show_worker_details: bool = True,
    ):
        """Initialize the progress tracker.

        Args:
            progress_interval: Seconds between progress log updates
            long_job_threshold: Seconds before warning about long-running jobs
            show_worker_details: Whether to show per-worker activity in logs
        """
        self.progress_interval = progress_interval
        self.long_job_threshold = long_job_threshold
        self.show_worker_details = show_worker_details

        # Job tracking
        self._jobs: Dict[int, JobInfo] = {}
        self._completed_jobs: Set[int] = set()
        self._failed_jobs: Set[int] = set()
        self._lock = threading.RLock()

        # Statistics
        self._job_type_counts: Dict[str, int] = defaultdict(int)
        self._start_time = datetime.now()

        # Progress logging thread
        self._progress_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_warned_jobs: Set[int] = set()

    def job_submitted(
        self,
        job_id: int,
        job_type: str,
        input_file: str,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Record that a job was submitted.

        Args:
            job_id: Job identifier
            job_type: Type of job (e.g., 'notebook', 'drawio')
            input_file: Path to input file being processed
            correlation_id: Optional correlation ID for tracing
        """
        with self._lock:
            self._jobs[job_id] = JobInfo(
                job_id=job_id,
                job_type=job_type,
                input_file=input_file,
                correlation_id=correlation_id,
            )
            self._job_type_counts[job_type] += 1

        logger.debug(
            f"Job #{job_id} submitted: {job_type} for {input_file}"
            + (f" [correlation_id: {correlation_id}]" if correlation_id else "")
        )

    def job_started(self, job_id: int, worker_id: str) -> None:
        """Record that a job started processing.

        Args:
            job_id: Job identifier
            worker_id: Worker identifier that picked up the job
        """
        with self._lock:
            if job_id in self._jobs:
                job = self._jobs[job_id]
                job.worker_id = worker_id
                job.started_at = datetime.now()
                logger.info(
                    f"Worker {worker_id} picked up Job #{job_id} [{job.job_type}] "
                    f"for {job.input_file}"
                )
            else:
                logger.warning(
                    f"Job #{job_id} started but not found in tracked jobs"
                )

    def job_completed(self, job_id: int, duration: Optional[float] = None) -> None:
        """Record that a job completed successfully.

        Args:
            job_id: Job identifier
            duration: Optional processing duration in seconds
        """
        with self._lock:
            if job_id in self._jobs:
                job = self._jobs[job_id]
                self._completed_jobs.add(job_id)

                # Calculate duration if not provided
                if duration is None and job.started_at:
                    duration = (datetime.now() - job.started_at).total_seconds()

                duration_str = f" in {duration:.2f}s" if duration else ""
                logger.info(
                    f"Job #{job_id} completed{duration_str} "
                    f"[worker: {job.worker_id}, file: {job.input_file}]"
                )
            else:
                logger.warning(
                    f"Job #{job_id} completed but not found in tracked jobs"
                )

    def job_failed(self, job_id: int, error: str) -> None:
        """Record that a job failed.

        Args:
            job_id: Job identifier
            error: Error message or description
        """
        with self._lock:
            if job_id in self._jobs:
                job = self._jobs[job_id]
                self._failed_jobs.add(job_id)
                logger.error(
                    f"Job #{job_id} FAILED: {error} "
                    f"[worker: {job.worker_id}, file: {job.input_file}]"
                )
            else:
                logger.warning(f"Job #{job_id} failed but not found in tracked jobs")

    def start_progress_logging(self) -> None:
        """Start periodic progress logging in a background thread."""
        if self._progress_thread is not None:
            logger.warning("Progress logging already started")
            return

        self._stop_event.clear()
        self._progress_thread = threading.Thread(
            target=self._progress_loop, daemon=True, name="ProgressTracker"
        )
        self._progress_thread.start()
        logger.debug(
            f"Started progress logging (interval: {self.progress_interval}s, "
            f"long job threshold: {self.long_job_threshold}s)"
        )

    def stop_progress_logging(self) -> None:
        """Stop periodic progress logging."""
        if self._progress_thread is None:
            return

        self._stop_event.set()
        self._progress_thread.join(timeout=5.0)
        self._progress_thread = None
        logger.debug("Stopped progress logging")

    def get_summary(self) -> Dict:
        """Get summary statistics.

        Returns:
            Dictionary with summary information
        """
        with self._lock:
            total = len(self._jobs)
            completed = len(self._completed_jobs)
            failed = len(self._failed_jobs)
            active = total - completed - failed
            elapsed = (datetime.now() - self._start_time).total_seconds()

            return {
                "total": total,
                "completed": completed,
                "failed": failed,
                "active": active,
                "elapsed_seconds": elapsed,
                "job_type_counts": dict(self._job_type_counts),
            }

    def log_summary(self) -> None:
        """Log a final summary of job execution."""
        summary = self.get_summary()
        total = summary["total"]
        completed = summary["completed"]
        failed = summary["failed"]
        active = summary["active"]
        elapsed = summary["elapsed_seconds"]

        if total == 0:
            logger.info("No jobs were processed")
            return

        job_types_str = ", ".join(
            f"{count} {jtype}" for jtype, count in summary["job_type_counts"].items()
        )

        if failed > 0:
            logger.warning(
                f"Job execution completed: {completed}/{total} succeeded, "
                f"{failed} failed, {active} still pending in {elapsed:.1f}s ({job_types_str})"
            )
        elif completed == total:
            logger.info(
                f"✓ All {total} jobs completed successfully in {elapsed:.1f}s "
                f"({job_types_str})"
            )
        else:
            logger.info(
                f"Job execution summary: {completed}/{total} completed, "
                f"{active} still pending in {elapsed:.1f}s ({job_types_str})"
            )

    def _progress_loop(self) -> None:
        """Background thread loop for periodic progress logging."""
        while not self._stop_event.wait(self.progress_interval):
            self._log_progress()

    def _log_progress(self) -> None:
        """Log current progress and check for long-running jobs."""
        with self._lock:
            summary = self.get_summary()
            total = summary["total"]
            completed = summary["completed"]
            failed = summary["failed"]
            active = summary["active"]

            if total == 0:
                return  # Nothing to report yet

            # Calculate percentage
            percentage = int((completed + failed) / total * 100) if total > 0 else 0

            # Build progress message
            msg = (
                f"Progress: {completed}/{total} jobs completed | "
                f"{active} active | {failed} failed ({percentage}%)"
            )

            logger.info(msg)

            # Show per-worker details if enabled
            if self.show_worker_details and active > 0:
                self._log_worker_details()

            # Check for long-running jobs
            self._check_long_running_jobs()

    def _log_worker_details(self) -> None:
        """Log details about active workers and their jobs."""
        now = datetime.now()
        active_jobs = []

        for job_id, job in self._jobs.items():
            if job_id not in self._completed_jobs and job_id not in self._failed_jobs:
                if job.started_at and job.worker_id:
                    elapsed = (now - job.started_at).total_seconds()
                    active_jobs.append((job.worker_id, job_id, job.job_type, job.input_file, elapsed))

        if active_jobs:
            for worker_id, job_id, job_type, input_file, elapsed in sorted(active_jobs):
                logger.info(
                    f"  └─ {worker_id}: Processing {job_type} job #{job_id} "
                    f"({elapsed:.1f}s elapsed) [{input_file}]"
                )

    def _check_long_running_jobs(self) -> None:
        """Check for jobs running longer than threshold and emit warnings."""
        now = datetime.now()

        for job_id, job in self._jobs.items():
            # Skip completed/failed jobs
            if job_id in self._completed_jobs or job_id in self._failed_jobs:
                continue

            # Skip jobs that haven't started
            if not job.started_at:
                continue

            elapsed = (now - job.started_at).total_seconds()

            # Warn if job is running longer than threshold
            if elapsed >= self.long_job_threshold:
                # Only warn once per threshold interval
                if job_id not in self._last_warned_jobs or elapsed >= (
                    self.long_job_threshold * 2
                ):
                    logger.warning(
                        f"Job #{job_id} has been processing for {elapsed:.0f}s "
                        f"[worker: {job.worker_id}, type: {job.job_type}, "
                        f"file: {job.input_file}]"
                    )
                    self._last_warned_jobs.add(job_id)


def get_progress_tracker_config() -> Dict:
    """Get progress tracker configuration from environment variables.

    Returns:
        Dictionary with configuration values
    """
    return {
        "progress_interval": float(
            os.environ.get("CLX_E2E_PROGRESS_INTERVAL", "5.0")
        ),
        "long_job_threshold": float(
            os.environ.get("CLX_E2E_LONG_JOB_THRESHOLD", "30.0")
        ),
        "show_worker_details": os.environ.get("CLX_E2E_SHOW_WORKER_DETAILS", "true").lower()
        in ("true", "1", "yes"),
    }
