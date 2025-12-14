"""SQLite-based notebook worker implementation.

This module provides a worker that polls the SQLite job queue for notebook
processing jobs instead of using RabbitMQ.

Workers can operate in two modes:
1. Direct SQLite mode (default): Workers communicate with the database directly
2. REST API mode: Workers communicate via HTTP API (for Docker containers)

The mode is determined by the presence of CLX_API_URL environment variable.
"""

import logging
import os
from pathlib import Path

from clx.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
from clx.infrastructure.database.job_queue import Job
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.messaging.notebook_classes import NotebookPayload
from clx.infrastructure.workers.worker_base import Worker
from clx.workers.notebook.notebook_processor import NotebookProcessor
from clx.workers.notebook.output_spec import create_output_spec

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DB_PATH = Path(os.environ.get("DB_PATH", "/db/jobs.db"))
CACHE_DB_PATH = Path(os.environ.get("CACHE_DB_PATH", "clx_cache.db"))
API_URL = os.environ.get("CLX_API_URL")  # If set, use REST API mode

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - notebook-worker - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class NotebookWorker(Worker):
    """Worker that processes notebook jobs from SQLite queue or REST API."""

    def __init__(
        self,
        worker_id: int,
        db_path: Path | None = None,
        cache_db_path: Path | None = None,
        api_url: str | None = None,
    ):
        """Initialize notebook worker.

        Args:
            worker_id: Worker ID from database
            db_path: Path to SQLite database (required for direct mode)
            cache_db_path: Path to executed notebook cache database
            api_url: URL of the Worker API (for Docker mode)
        """
        super().__init__(worker_id, "notebook", db_path=db_path, api_url=api_url)
        self.cache_db_path = cache_db_path
        self._cache: ExecutedNotebookCache | None = None
        mode = "API" if api_url else "SQLite"
        logger.info(f"NotebookWorker {worker_id} initialized in {mode} mode")

    def _ensure_cache_initialized(self) -> ExecutedNotebookCache | None:
        """Ensure the executed notebook cache is initialized.

        Returns:
            The cache instance, or None if no cache path configured.
        """
        if self.cache_db_path is None:
            return None

        if self._cache is None:
            self._cache = ExecutedNotebookCache(self.cache_db_path)
            self._cache.__enter__()
            logger.info(f"Initialized executed notebook cache at {self.cache_db_path}")

        return self._cache

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

            # Determine if we're in Docker mode with source mount
            # If CLX_HOST_DATA_DIR is set, we can read input files from /source
            host_data_dir = os.environ.get("CLX_HOST_DATA_DIR")

            notebook_text: str
            if host_data_dir:
                # Docker mode with source mount: read from filesystem
                from clx.infrastructure.workers.worker_base import convert_input_path_to_container

                input_path = convert_input_path_to_container(job.input_file, host_data_dir)
                logger.debug(f"Docker mode: reading from {input_path}")
                notebook_text = input_path.read_text(encoding="utf-8")
            else:
                # Direct mode or legacy Docker mode: use payload data
                notebook_text = payload_data.get("data", "")
                if not notebook_text:
                    # Fallback: try reading from filesystem (direct mode)
                    input_path = Path(job.input_file)
                    if input_path.exists():
                        notebook_text = input_path.read_text(encoding="utf-8")
                    else:
                        raise FileNotFoundError(f"Input file not found: {input_path}")
                input_path = Path(job.input_file)  # Keep for logging/error messages

            logger.debug(f"Processing notebook: {input_path.name}")

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
                fallback_execute=payload_data.get("fallback_execute", False),
                img_path_prefix=payload_data.get("img_path_prefix", "img/"),
                source_topic_dir=payload_data.get("source_topic_dir", ""),
            )

            # Determine source directory for supporting files (Docker mode with source mount)
            source_dir: Path | None = None
            if host_data_dir and payload_data.get("source_topic_dir"):
                # Convert host topic directory to container path
                from clx.infrastructure.workers.worker_base import convert_input_path_to_container

                source_dir = convert_input_path_to_container(
                    payload_data["source_topic_dir"], host_data_dir
                )
                logger.debug(
                    f"Docker mode: using source directory {source_dir} for supporting files"
                )

            # Get cache and process notebook
            cache = self._ensure_cache_initialized()
            logger.debug(f"Processing notebook with NotebookProcessor for {input_path.name}")
            processor = NotebookProcessor(output_spec, cache=cache)
            result = await processor.process_notebook(payload, source_dir=source_dir)
            logger.debug(f"Notebook processing complete for {input_path.name}")

            # Collect warnings from the processor
            warnings = processor.get_warnings()
            if warnings:
                logger.debug(f"Notebook processing generated {len(warnings)} warning(s)")
                self.set_job_warnings(warnings)

            # Write output file
            # In Docker mode, convert host path to container path
            host_workspace = os.environ.get("CLX_HOST_WORKSPACE")
            if host_workspace:
                from clx.infrastructure.workers.worker_base import convert_host_path_to_container

                output_path = convert_host_path_to_container(job.output_file, host_workspace)
                logger.debug(f"Converted output path: {job.output_file} -> {output_path}")
            else:
                output_path = Path(job.output_file)

            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result)

            logger.info(f"Notebook written to {output_path}")

            # Add to cache (works for both SQLite and API modes)
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

    def cleanup(self):
        """Clean up resources including the executed notebook cache."""
        # Close the cache if it was initialized
        if self._cache is not None:
            try:
                self._cache.__exit__(None, None, None)
                logger.info("Closed executed notebook cache")
            except Exception as e:
                logger.warning(f"Error closing cache: {e}")
            self._cache = None

        # Call parent cleanup
        super().cleanup()


def main():
    """Main entry point for notebook worker."""
    # Determine mode based on environment
    if API_URL:
        logger.info(f"Starting notebook worker in API mode (URL: {API_URL})")

        # Register worker via API with retry logic
        worker_id = Worker.register_worker_via_api(API_URL, "notebook")

        # Create worker in API mode (no database access)
        # Note: cache_db_path is not used in API mode as cache is handled by host
        worker = NotebookWorker(worker_id, api_url=API_URL)
    else:
        logger.info("Starting notebook worker in SQLite mode")

        # Ensure database exists
        if not DB_PATH.exists():
            logger.info(f"Initializing database at {DB_PATH}")
            init_database(DB_PATH)

        # Register worker with retry logic
        worker_id = Worker.register_worker_with_retry(DB_PATH, "notebook")

        # Create and run worker with cache support
        worker = NotebookWorker(worker_id, db_path=DB_PATH, cache_db_path=CACHE_DB_PATH)

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
