"""SQLite-based notebook worker implementation.

This module provides a worker that polls the SQLite job queue for notebook
processing jobs instead of using RabbitMQ.
"""

import logging
import os
from pathlib import Path

from clx.infrastructure.database.job_queue import Job
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.messaging.notebook_classes import NotebookPayload
from clx.infrastructure.workers.worker_base import Worker
from clx.workers.notebook.notebook_processor import NotebookProcessor
from clx.workers.notebook.output_spec import create_output_spec

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DB_PATH = Path(os.environ.get("DB_PATH", "/db/jobs.db"))

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - notebook-worker - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class NotebookWorker(Worker):
    """Worker that processes notebook jobs from SQLite queue."""

    def __init__(self, worker_id: int, db_path: Path):
        """Initialize notebook worker.

        Args:
            worker_id: Worker ID from database
            db_path: Path to SQLite database
        """
        super().__init__(worker_id, "notebook", db_path)
        logger.info(f"NotebookWorker {worker_id} initialized")

    def process_job(self, job: Job):
        """Process a notebook job.

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
            payload_data = job.payload
            logger.debug(f"Processing job {job.id} with payload: {payload_data.keys()}")

            # Read input file
            input_path = Path(job.input_file)
            if not input_path.exists():
                raise FileNotFoundError(f"Input file not found: {input_path}")

            logger.debug(f"Reading input file: {input_path}")
            with open(input_path, encoding="utf-8") as f:
                notebook_text = f.read()

            # Check if job was cancelled after reading input
            if self.job_queue.is_job_cancelled(job.id):
                logger.info(f"Job {job.id} was cancelled after reading input, aborting")
                return

            # Create output spec
            output_spec = create_output_spec(
                kind=payload_data.get("kind", "completed"),
                prog_lang=payload_data.get("prog_lang", "python"),
                language=payload_data.get("language", "en"),
                format=payload_data.get("format", "notebook"),
            )

            # Create NotebookPayload for processing
            # Note: This is a simplified version that works with the new architecture
            payload = NotebookPayload(
                data=notebook_text,
                input_file=str(input_path),
                input_file_name=input_path.name,
                output_file=job.output_file,
                kind=payload_data.get("kind", "completed"),
                prog_lang=payload_data.get("prog_lang", "python"),
                language=payload_data.get("language", "en"),
                format=payload_data.get("format", "notebook"),
                template_dir=payload_data.get("template_dir", ""),
                other_files=payload_data.get("other_files", {}),
                correlation_id=payload_data.get("correlation_id", f"job-{job.id}"),
            )

            # Process notebook
            logger.debug(f"Processing notebook with NotebookProcessor for {input_path.name}")
            processor = NotebookProcessor(output_spec)
            result = await processor.process_notebook(payload)
            logger.debug(f"Notebook processing complete for {input_path.name}")

            # Write output file
            output_path = Path(job.output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result)

            logger.info(f"Notebook written to {output_path}")

            # Add to cache
            self.job_queue.add_to_cache(
                job.output_file,
                job.content_hash,
                {
                    "format": payload_data.get("format", "notebook"),
                    "kind": payload_data.get("kind", "participant"),
                    "prog_lang": payload_data.get("prog_lang", "python"),
                    "language": payload_data.get("language", "en"),
                },
            )

            logger.debug(f"Added result to cache for {job.output_file}")

        except Exception as e:
            logger.error(f"Error processing notebook job {job.id}: {e}", exc_info=True)
            raise


def main():
    """Main entry point for notebook worker."""
    logger.info("Starting notebook worker in SQLite mode")

    # Ensure database exists
    if not DB_PATH.exists():
        logger.info(f"Initializing database at {DB_PATH}")
        init_database(DB_PATH)

    # Register worker with retry logic
    worker_id = Worker.register_worker_with_retry(DB_PATH, "notebook")

    # Create and run worker
    worker = NotebookWorker(worker_id, DB_PATH)

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
