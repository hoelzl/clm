"""SQLite-based DrawIO converter worker implementation.

This module provides a worker that polls the SQLite job queue for DrawIO
conversion jobs instead of using RabbitMQ.

Workers can operate in two modes:
1. Direct SQLite mode (default): Workers communicate with the database directly
2. REST API mode: Workers communicate via HTTP API (for Docker containers)

The mode is determined by the presence of CLX_API_URL environment variable.
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
API_URL = os.environ.get("CLX_API_URL")  # If set, use REST API mode

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - drawio-worker - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class DrawioWorker(Worker):
    """Worker that processes DrawIO conversion jobs from SQLite queue or REST API."""

    def __init__(self, worker_id: int, db_path: Path | None = None, api_url: str | None = None):
        """Initialize DrawIO worker.

        Args:
            worker_id: Worker ID from database
            db_path: Path to SQLite database (required for direct mode)
            api_url: URL of the Worker API (for Docker mode)
        """
        super().__init__(worker_id, "drawio", db_path=db_path, api_url=api_url)
        mode = "API" if api_url else "SQLite"
        logger.info(f"DrawioWorker {worker_id} initialized in {mode} mode")

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
            payload_data = job.payload
            logger.debug(f"Processing DrawIO job {job.id}")

            # Determine if we're in Docker mode with source mount
            host_data_dir = os.environ.get("CLX_HOST_DATA_DIR")
            host_workspace = os.environ.get("CLX_HOST_WORKSPACE")

            # Log environment state for debugging Docker mode issues
            if host_data_dir and host_workspace:
                logger.debug(
                    f"Docker source mount mode: CLX_HOST_DATA_DIR={host_data_dir}, "
                    f"CLX_HOST_WORKSPACE={host_workspace}"
                )
            else:
                logger.debug(
                    f"Legacy/direct mode: CLX_HOST_DATA_DIR={'set' if host_data_dir else 'NOT SET'}, "
                    f"CLX_HOST_WORKSPACE={'set' if host_workspace else 'NOT SET'}"
                )

            drawio_content: str
            if host_data_dir and host_workspace:
                # Docker mode with source mount: read from filesystem
                from clx.infrastructure.workers.worker_base import (
                    convert_input_path_to_container,
                    convert_output_path_to_container,
                )

                input_path = convert_input_path_to_container(job.input_file, host_data_dir)
                logger.debug(f"Docker mode: reading from {input_path}")
                try:
                    drawio_content = input_path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    # Provide helpful error message for Docker mode
                    raise FileNotFoundError(
                        f"Input file not found in Docker container: {input_path} "
                        f"(host path: {job.input_file}). "
                        f"Verify the file exists and the Docker mount is configured correctly."
                    ) from None

                # Output path may be under workspace or data_dir (for generated images in source tree)
                output_path = convert_output_path_to_container(
                    job.output_file, host_workspace, host_data_dir
                )
                logger.debug(f"Docker mode: writing to {output_path}")
            else:
                # Direct mode or legacy Docker mode: use payload data
                drawio_content = payload_data.get("data", "")
                if not drawio_content:
                    # Fallback: try reading from filesystem (direct mode)
                    input_path = Path(job.input_file)
                    if input_path.exists():
                        drawio_content = input_path.read_text(encoding="utf-8")
                    else:
                        raise FileNotFoundError(
                            f"Input file not found: {input_path} "
                            f"(Job {job.id}: no DrawIO data in payload)"
                        )
                input_path = Path(job.input_file)

                if host_workspace or host_data_dir:
                    from clx.infrastructure.workers.worker_base import (
                        convert_output_path_to_container,
                    )

                    output_path = convert_output_path_to_container(
                        job.output_file, host_workspace, host_data_dir
                    )
                else:
                    output_path = Path(job.output_file)

            logger.debug(f"Processing DrawIO: {input_path.name}")
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

            # Add to cache (works for both SQLite and API modes)
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
    # Determine mode based on environment
    if API_URL:
        logger.info(f"Starting DrawIO worker in API mode (URL: {API_URL})")

        # Get pre-assigned worker ID or register via API
        # This handles both pre-registration (CLX_WORKER_ID set) and legacy registration
        worker_id = Worker.get_or_register_worker(
            db_path=None, api_url=API_URL, worker_type="drawio"
        )

        # Create worker in API mode (no database access)
        worker = DrawioWorker(worker_id, api_url=API_URL)
    else:
        logger.info("Starting DrawIO worker in SQLite mode")

        # Ensure database exists
        if not DB_PATH.exists():
            logger.info(f"Initializing database at {DB_PATH}")
            init_database(DB_PATH)

        # Get pre-assigned worker ID or register with retry logic
        # This handles both pre-registration (CLX_WORKER_ID set) and legacy registration
        worker_id = Worker.get_or_register_worker(
            db_path=DB_PATH, api_url=None, worker_type="drawio"
        )

        # Create and run worker
        worker = DrawioWorker(worker_id, db_path=DB_PATH)

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
