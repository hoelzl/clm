"""SQLite-based DrawIO converter worker implementation.

This module provides a worker that polls the SQLite job queue for DrawIO
conversion jobs instead of using RabbitMQ.
"""

import os
import sys
import logging
import asyncio
import sqlite3
import time
from pathlib import Path
from base64 import b64decode, b64encode

# Add clx-common to path if running standalone
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "clx-common" / "src"))

from clx_common.workers.worker_base import Worker
from clx_common.database.job_queue import Job, JobQueue
from clx_common.database.schema import init_database

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DB_PATH = Path(os.environ.get("DB_PATH", "/db/jobs.db"))

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - drawio-worker - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class DrawioWorker(Worker):
    """Worker that processes DrawIO conversion jobs from SQLite queue."""

    def __init__(self, worker_id: int, db_path: Path):
        """Initialize DrawIO worker.

        Args:
            worker_id: Worker ID from database
            db_path: Path to SQLite database
        """
        super().__init__(worker_id, 'drawio', db_path)
        logger.info(f"DrawioWorker {worker_id} initialized")

    def process_job(self, job: Job):
        """Process a DrawIO conversion job.

        Args:
            job: Job to process
        """
        # Run async processing in event loop
        asyncio.run(self._process_job_async(job))

    async def _process_job_async(self, job: Job):
        """Async implementation of job processing.

        Args:
            job: Job to process
        """
        try:
            # Extract payload data
            payload_data = job.payload
            logger.debug(f"Processing DrawIO job {job.id}")

            # Read input file
            input_path = Path(job.input_file)
            if not input_path.exists():
                raise FileNotFoundError(f"Input file not found: {input_path}")

            with open(input_path, 'r', encoding='utf-8') as f:
                drawio_content = f.read()

            # Determine output format from file extension
            output_path = Path(job.output_file)
            output_format = output_path.suffix.lstrip('.')
            if not output_format:
                output_format = 'png'  # default

            logger.info(f"Converting {input_path} to {output_format}")

            # Import the conversion function
            from drawio_converter.drawio_converter import convert_drawio
            from tempfile import TemporaryDirectory
            import aiofiles

            # Process in temporary directory
            with TemporaryDirectory() as tmp_dir:
                tmp_input = Path(tmp_dir) / "input.drawio"
                tmp_output = Path(tmp_dir) / f"output.{output_format}"

                # Write input
                async with aiofiles.open(tmp_input, "w", encoding="utf-8") as f:
                    await f.write(drawio_content)

                # Create empty output file
                async with aiofiles.open(tmp_output, "wb") as f:
                    await f.write(b"")

                # Convert
                await convert_drawio(
                    tmp_input,
                    tmp_output,
                    output_format,
                    f"job-{job.id}"
                )

                # Read result
                async with aiofiles.open(tmp_output, "rb") as f:
                    result_bytes = await f.read()

            if len(result_bytes) == 0:
                raise ValueError("Conversion produced empty result")

            # Write output file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(result_bytes)

            logger.info(f"DrawIO image written to {output_path} ({len(result_bytes)} bytes)")

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
            logger.error(f"Error processing DrawIO job {job.id}: {e}", exc_info=True)
            raise


def register_worker(db_path: Path) -> int:
    """Register a new worker in the database with retry logic.

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

    # Retry logic with exponential backoff
    max_retries = 5
    retry_delay = 0.5  # Start with 500ms

    for attempt in range(max_retries):
        try:
            conn = queue._get_conn()

            cursor = conn.execute(
                """
                INSERT INTO workers (worker_type, container_id, status)
                VALUES (?, ?, 'idle')
                """,
                ('drawio', worker_identifier)
            )
            worker_id = cursor.lastrowid
            conn.commit()

            logger.info(f"Registered worker {worker_id} (identifier: {worker_identifier})")
            return worker_id

        except sqlite3.OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Failed to register worker (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.error(f"Failed to register worker after {max_retries} attempts: {e}")
                raise


def main():
    """Main entry point for DrawIO worker."""
    logger.info("Starting DrawIO worker in SQLite mode")

    # Ensure database exists
    if not DB_PATH.exists():
        logger.info(f"Initializing database at {DB_PATH}")
        init_database(DB_PATH)

    # Register worker
    worker_id = register_worker(DB_PATH)

    # Create and run worker
    worker = DrawioWorker(worker_id, DB_PATH)

    try:
        worker.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
        worker.stop()
    except Exception as e:
        logger.error(f"Worker crashed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
