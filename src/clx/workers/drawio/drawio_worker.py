"""SQLite-based DrawIO converter worker implementation.

This module provides a worker that polls the SQLite job queue for DrawIO
conversion jobs instead of using RabbitMQ.
"""

import logging
import os
from pathlib import Path

from clx.infrastructure.database.job_queue import Job
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.worker_base import Worker

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
        super().__init__(worker_id, "drawio", db_path)
        logger.info(f"DrawioWorker {worker_id} initialized")

    def process_job(self, job: Job):
        """Process a DrawIO conversion job.

        Args:
            job: Job to process
        """
        # Use persistent event loop instead of asyncio.run()
        loop = self._get_or_create_loop()
        try:
            loop.run_until_complete(self._process_job_async(job))
        except Exception as e:
            logger.error(
                f"Worker {self.worker_id} error in event loop for job {job.id}: {e}", exc_info=True
            )
            raise

    async def _process_job_async(self, job: Job):
        """Async implementation of job processing.

        Args:
            job: Job to process
        """
        try:
            # Check if job was cancelled before starting
            if self.job_queue.is_job_cancelled(job.id):
                logger.info(f"Job {job.id} was cancelled before processing, skipping")
                return

            # Extract payload data
            logger.debug(f"Processing DrawIO job {job.id}")

            # Read input file
            input_path = Path(job.input_file)
            if not input_path.exists():
                raise FileNotFoundError(f"Input file not found: {input_path}")

            logger.debug(f"Reading DrawIO input file: {input_path}")
            with open(input_path, encoding="utf-8") as f:
                drawio_content = f.read()

            # Determine output format from file extension
            output_path = Path(job.output_file)
            output_format = output_path.suffix.lstrip(".")
            if not output_format:
                output_format = "png"  # default

            logger.info(f"Converting {input_path} to {output_format}")

            # Import the conversion function
            from tempfile import TemporaryDirectory

            import aiofiles  # type: ignore[import-untyped]

            from clx.workers.drawio.drawio_converter import convert_drawio

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
                logger.debug(f"Converting {input_path.name} to {output_format}")
                await convert_drawio(tmp_input, tmp_output, output_format, f"job-{job.id}")
                logger.debug(f"Conversion complete for {input_path.name}")

                # Read result
                async with aiofiles.open(tmp_output, "rb") as f:
                    result_bytes = await f.read()

            if len(result_bytes) == 0:
                raise ValueError("Conversion produced empty result")

            # Write output file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(result_bytes)

            logger.info(f"DrawIO image written to {output_path} ({len(result_bytes)} bytes)")

            # Add to cache
            self.job_queue.add_to_cache(
                job.output_file,
                job.content_hash,
                {"format": output_format, "size": len(result_bytes)},
            )

            logger.debug(f"Added result to cache for {job.output_file}")

        except Exception as e:
            logger.error(f"Error processing DrawIO job {job.id}: {e}", exc_info=True)
            raise


def main():
    """Main entry point for DrawIO worker."""
    logger.info("Starting DrawIO worker in SQLite mode")

    # Ensure database exists
    if not DB_PATH.exists():
        logger.info(f"Initializing database at {DB_PATH}")
        init_database(DB_PATH)

    # Register worker with retry logic
    worker_id = Worker.register_worker_with_retry(DB_PATH, "drawio")

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
    finally:
        # Clean up event loop and other resources
        worker.cleanup()
        logger.info("Worker cleanup completed")


if __name__ == "__main__":
    main()
