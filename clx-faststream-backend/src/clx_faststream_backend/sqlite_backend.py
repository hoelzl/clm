"""SQLite-based backend for job queue orchestration.

This backend submits jobs to a SQLite database and waits for workers to complete them.
It's a simpler alternative to the RabbitMQ-based FastStreamBackend.
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict

from attrs import define, field
from clx_common.backends.local_ops_backend import LocalOpsBackend
from clx_common.database.job_queue import JobQueue
from clx_common.database.schema import init_database
from clx_common.database.db_operations import DatabaseManager
from clx_common.operation import Operation
from clx_common.messaging.base_classes import Payload

logger = logging.getLogger(__name__)


@define
class SqliteBackend(LocalOpsBackend):
    """SQLite-based backend for job queue orchestration.

    This backend submits jobs to a SQLite database and waits for
    workers to complete them. It's a simpler alternative to the
    RabbitMQ-based FastStreamBackend.
    """

    db_path: Path = Path('clx_jobs.db')
    workspace_path: Path = Path.cwd()
    job_queue: JobQueue | None = field(init=False, default=None)
    db_manager: DatabaseManager | None = None
    ignore_db: bool = False
    active_jobs: Dict[int, Dict] = field(factory=dict)  # job_id -> job info
    poll_interval: float = 0.5  # seconds
    max_wait_for_completion_duration: float = 1200.0  # 20 minutes

    def __attrs_post_init__(self):
        """Initialize SQLite database and job queue."""
        # Database should already be initialized, but ensure it exists
        init_database(self.db_path)
        self.job_queue = JobQueue(self.db_path)
        logger.info(f"Initialized SQLite backend with database: {self.db_path}")

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
        # Check database cache first
        if not self.ignore_db and self.db_manager:
            result = self.db_manager.get_result(
                payload.input_file,
                payload.content_hash(),
                payload.output_metadata()
            )
            if result:
                logger.debug(
                    f"Database cache hit for {payload.input_file} -> {payload.output_file}"
                )
                # Write cached result
                output_file = Path(payload.output_file)
                # Make path absolute relative to workspace if not already absolute
                if not output_file.is_absolute():
                    output_file = self.workspace_path / output_file
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_bytes(result.result_bytes())
                return

        # Check SQLite job cache
        if self.job_queue:
            cached = self.job_queue.check_cache(
                str(payload.output_file),
                payload.content_hash()
            )
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
                    logger.warning(
                        f"Cache indicated file exists but not found: {output_path}"
                    )

        # Map service to job type
        service_to_job_type = {
            "notebook-processor": "notebook",
            "drawio-converter": "drawio",
            "plantuml-converter": "plantuml"
        }

        service_name = operation.service_name
        if service_name not in service_to_job_type:
            raise ValueError(f"Unknown service: {service_name}")

        job_type = service_to_job_type[service_name]

        # Prepare payload dict using Pydantic's model_dump() with mode='json'
        # This ensures bytes are serialized to base64 strings for JSON compatibility
        payload_dict = payload.model_dump(mode='json')

        # Add job to queue
        job_id = self.job_queue.add_job(
            job_type=job_type,
            input_file=str(payload.input_file),
            output_file=str(payload.output_file),
            content_hash=payload.content_hash(),
            payload=payload_dict
        )

        # Track active job
        self.active_jobs[job_id] = {
            'job_type': job_type,
            'input_file': str(payload.input_file),
            'output_file': str(payload.output_file),
            'correlation_id': payload.correlation_id
        }

        logger.debug(
            f"Added job {job_id} ({job_type}): {payload.input_file} -> {payload.output_file}"
        )

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
        start_time = asyncio.get_event_loop().time()
        failed_jobs = []

        while self.active_jobs:
            # Check each active job
            completed_jobs = []

            for job_id, job_info in list(self.active_jobs.items()):
                # Query job status from database
                conn = self.job_queue._get_conn()
                cursor = conn.execute(
                    "SELECT status, error FROM jobs WHERE id = ?",
                    (job_id,)
                )
                row = cursor.fetchone()

                if not row:
                    logger.warning(f"Job {job_id} not found in database")
                    completed_jobs.append(job_id)
                    continue

                status = row[0]
                error = row[1]

                if status == 'completed':
                    logger.info(
                        f"Job {job_id} completed: {job_info['input_file']} -> {job_info['output_file']}"
                    )
                    completed_jobs.append(job_id)

                    # Add to database cache if applicable
                    if not self.ignore_db and self.db_manager:
                        output_path = Path(job_info['output_file'])
                        if output_path.exists():
                            # Read output file and store in database
                            try:
                                data = output_path.read_bytes()
                                # Store result in database cache for future runs
                                # The actual storage happens through result handlers
                                # This is a placeholder for future enhancement
                            except Exception as e:
                                logger.warning(
                                    f"Could not cache result for job {job_id}: {e}"
                                )

                elif status == 'failed':
                    logger.error(
                        f"Job {job_id} failed: {job_info['input_file']} -> {job_info['output_file']}\n"
                        f"Error: {error}"
                    )
                    completed_jobs.append(job_id)
                    failed_jobs.append({
                        'job_id': job_id,
                        'job_info': job_info,
                        'error': error
                    })

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
            logger.warning(
                f"Shutdown called with {len(self.active_jobs)} job(s) still pending"
            )
            try:
                await asyncio.wait_for(
                    self.wait_for_completion(),
                    timeout=5.0
                )
            except TimeoutError:
                logger.warning(
                    f"Shutdown timeout - {len(self.active_jobs)} job(s) still pending"
                )
