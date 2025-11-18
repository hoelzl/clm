"""SQLite-based PlantUML converter worker implementation.

This module provides a worker that polls the SQLite job queue for PlantUML
conversion jobs instead of using RabbitMQ.
"""

import os
import sys
import logging
import asyncio
from pathlib import Path
from base64 import b64decode, b64encode

# Add clx-common to path if running standalone
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "clx-common" / "src"))

from clx.infrastructure.workers.worker_base import Worker
from clx.infrastructure.database.job_queue import Job, JobQueue
from clx.infrastructure.database.schema import init_database

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DB_PATH = Path(os.environ.get("DB_PATH", "/db/jobs.db"))

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - plantuml-worker - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class PlantUmlWorker(Worker):
    """Worker that processes PlantUML conversion jobs from SQLite queue."""

    def __init__(self, worker_id: int, db_path: Path):
        """Initialize PlantUML worker.

        Args:
            worker_id: Worker ID from database
            db_path: Path to SQLite database
        """
        super().__init__(worker_id, 'plantuml', db_path)
        # Create persistent event loop for this worker
        self._loop = None
        logger.info(f"PlantUmlWorker {worker_id} initialized")

    def _get_or_create_loop(self):
        """Get or create the event loop for this worker.

        This ensures we reuse the same event loop across all job processing,
        avoiding the overhead and potential issues of creating a new loop
        for each job with asyncio.run().
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

    def process_job(self, job: Job):
        """Process a PlantUML conversion job.

        Args:
            job: Job to process
        """
        # Use persistent event loop instead of asyncio.run()
        loop = self._get_or_create_loop()
        try:
            loop.run_until_complete(self._process_job_async(job))
        except Exception as e:
            logger.error(
                f"Worker {self.worker_id} error in event loop for job {job.id}: {e}",
                exc_info=True
            )
            raise

    async def _process_job_async(self, job: Job):
        """Async implementation of job processing.

        Args:
            job: Job to process
        """
        try:
            # Extract payload data
            payload_data = job.payload
            logger.debug(f"Processing PlantUML job {job.id}")

            # Read input file
            input_path = Path(job.input_file)
            if not input_path.exists():
                raise FileNotFoundError(f"Input file not found: {input_path}")

            logger.debug(f"Reading PlantUML input file: {input_path}")
            with open(input_path, 'r', encoding='utf-8') as f:
                plantuml_content = f.read()

            # Determine output format from file extension
            output_path = Path(job.output_file)
            output_format = output_path.suffix.lstrip('.')
            if not output_format:
                output_format = 'png'  # default

            logger.info(f"Converting {input_path} to {output_format}")

            # Import the conversion function
            from plantuml_converter.plantuml_converter import (
                convert_plantuml,
                get_plantuml_output_name
            )
            from tempfile import TemporaryDirectory

            # Process in temporary directory
            with TemporaryDirectory() as tmp_dir:
                tmp_input = Path(tmp_dir) / "plantuml.pu"
                output_name = get_plantuml_output_name(plantuml_content, default="plantuml")
                tmp_output = (Path(tmp_dir) / output_name).with_suffix(f".{output_format}")

                # Write input
                tmp_input.write_text(plantuml_content, encoding="utf-8")

                # Convert
                logger.debug(f"Converting {input_path.name} to {output_format}")
                await convert_plantuml(tmp_input, f"job-{job.id}")
                logger.debug(f"Conversion complete for {input_path.name}")

                # Read result
                if not tmp_output.exists():
                    # List available files for debugging
                    available = list(Path(tmp_dir).iterdir())
                    logger.error(f"Expected output {tmp_output} not found. Available files: {available}")
                    raise FileNotFoundError(f"Conversion did not produce expected output: {tmp_output}")

                result_bytes = tmp_output.read_bytes()

            if len(result_bytes) == 0:
                raise ValueError("Conversion produced empty result")

            # Write output file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(result_bytes)

            logger.info(f"PlantUML image written to {output_path} ({len(result_bytes)} bytes)")

            # Add to cache
            self.job_queue.add_to_cache(
                job.output_file,
                job.content_hash,
                {
                    'format': output_format,
                    'size': len(result_bytes)
                }
            )

            logger.debug(f"Added result to cache for {job.output_file}")

        except Exception as e:
            logger.error(f"Error processing PlantUML job {job.id}: {e}", exc_info=True)
            raise

    def cleanup(self):
        """Clean up resources when worker stops.

        This closes the event loop to prevent resource leaks.
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


def register_worker(db_path: Path) -> int:
    """Register a new worker in the database.

    Args:
        db_path: Path to SQLite database

    Returns:
        Worker ID
    """
    # Get worker ID from environment
    # For direct execution: WORKER_ID is set explicitly
    # For Docker: HOSTNAME is the container ID
    worker_identifier = os.getenv('WORKER_ID') or os.getenv('HOSTNAME', 'unknown')

    queue = JobQueue(db_path)
    conn = queue._get_conn()

    cursor = conn.execute(
        """
        INSERT INTO workers (worker_type, container_id, status)
        VALUES (?, ?, 'idle')
        """,
        ('plantuml', worker_identifier)
    )
    worker_id = cursor.lastrowid
    # No commit() needed - connection is in autocommit mode

    logger.info(f"Registered worker {worker_id} (identifier: {worker_identifier})")
    return worker_id


def main():
    """Main entry point for PlantUML worker."""
    logger.info("Starting PlantUML worker in SQLite mode")

    # Ensure database exists
    if not DB_PATH.exists():
        logger.info(f"Initializing database at {DB_PATH}")
        init_database(DB_PATH)

    # Register worker
    worker_id = register_worker(DB_PATH)

    # Create and run worker
    worker = PlantUmlWorker(worker_id, DB_PATH)

    try:
        worker.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
        worker.stop()
    except Exception as e:
        logger.error(f"Worker crashed: {e}", exc_info=True)
        raise
    finally:
        # Clean up event loop and other resources
        worker.cleanup()
        logger.info("Worker cleanup completed")


if __name__ == "__main__":
    main()
