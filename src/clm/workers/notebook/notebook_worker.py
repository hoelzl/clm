"""SQLite-based notebook worker implementation.

This module provides a worker that polls the SQLite job queue for notebook
processing jobs instead of using RabbitMQ.

Workers can operate in two modes:
1. Direct SQLite mode (default): Workers communicate with the database directly
2. REST API mode: Workers communicate via HTTP API (for Docker containers)

The mode is determined by the presence of CLM_API_URL environment variable.
"""

import logging
import os
from pathlib import Path

from clm.infrastructure.api.api_executed_notebook_cache import ApiExecutedNotebookCache
from clm.infrastructure.api.client import WorkerApiClient
from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
from clm.infrastructure.database.job_queue import Job
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.database.worker_heartbeats import WorkerHeartbeatStore
from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.infrastructure.workers.worker_base import Worker
from clm.workers.notebook.notebook_processor import NotebookProcessor
from clm.workers.notebook.output_spec import create_output_spec

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DB_PATH = Path(os.environ.get("DB_PATH", "/db/jobs.db"))
CACHE_DB_PATH = Path(os.environ.get("CACHE_DB_PATH", "clm_cache.db"))
API_URL = os.environ.get("CLM_API_URL")  # If set, use REST API mode

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
                (direct mode only — ignored in API mode)
            api_url: URL of the Worker API (for Docker mode); cache requests
                go over this URL via :class:`ApiExecutedNotebookCache`.
        """
        super().__init__(worker_id, "notebook", db_path=db_path, api_url=api_url)
        self.cache_db_path = cache_db_path
        self.api_url = api_url
        self._cache: ExecutedNotebookCache | ApiExecutedNotebookCache | None = None
        self._api_cache_client: WorkerApiClient | None = None
        # Per-cell heartbeat publisher. Only available in direct SQLite mode
        # (API/Docker workers can't reach the jobs DB directly — for those,
        # the per-cell visibility would need a separate API endpoint, which
        # is a follow-up). Clear any stale row left over from a previous
        # worker that recycled this worker_id.
        self._heartbeat_store: WorkerHeartbeatStore | None = None
        if db_path is not None and api_url is None:
            self._heartbeat_store = WorkerHeartbeatStore(db_path, worker_id)
            try:
                self._heartbeat_store.clear()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(f"Worker {worker_id}: could not clear stale heartbeat row: {exc}")
        mode = "API" if api_url else "SQLite"
        logger.info(f"NotebookWorker {worker_id} initialized in {mode} mode")

    def _ensure_cache_initialized(
        self,
    ) -> "ExecutedNotebookCache | ApiExecutedNotebookCache | None":
        """Ensure the executed notebook cache is initialized.

        In API mode, returns an :class:`ApiExecutedNotebookCache` that
        proxies reads/writes through the Worker REST API to the host's
        ``clm_cache.db``. In direct mode, opens a local SQLite connection.

        Returns:
            The cache instance, or None if no cache backend can be opened
            (direct mode without a ``cache_db_path``).
        """
        if self._cache is not None:
            return self._cache

        if self.api_url is not None:
            self._api_cache_client = WorkerApiClient(self.api_url)
            self._cache = ApiExecutedNotebookCache(self._api_cache_client)
            logger.info(f"Initialized API-backed executed notebook cache (via {self.api_url})")
            return self._cache

        if self.cache_db_path is None:
            return None

        sqlite_cache = ExecutedNotebookCache(self.cache_db_path)
        sqlite_cache.__enter__()
        self._cache = sqlite_cache
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
            # If CLM_HOST_DATA_DIR is set, input paths live under /source
            host_data_dir = os.environ.get("CLM_HOST_DATA_DIR")

            if host_data_dir:
                from clm.infrastructure.workers.worker_base import convert_input_path_to_container

                input_path = convert_input_path_to_container(job.input_file, host_data_dir)
            else:
                input_path = Path(job.input_file)

            # The payload's ``data`` is the canonical notebook source in BOTH
            # modes: the host builds it as slide text plus the merged voiceover
            # companion (``ProcessNotebookOperation.payload``). Docker mode
            # used to re-read the raw slide file from the mount instead, which
            # silently dropped the merged narration from the output and made
            # the worker's ``execution_cache_hash`` disagree with the host's,
            # permanently defeating the Stage-4 replay gate for voiceover
            # decks (issue #324). The filesystem read is only a fallback for
            # jobs whose payload carries no data.
            notebook_text: str = payload_data.get("data", "")
            if not notebook_text:
                if input_path.exists():
                    logger.debug(f"Empty payload data: reading from {input_path}")
                    notebook_text = input_path.read_text(encoding="utf-8")
                else:
                    raise FileNotFoundError(f"Input file not found: {input_path}")

            logger.debug(f"Processing notebook: {input_path.name}")

            # Check if job was cancelled after reading input
            if self.job_queue.is_job_cancelled(job.id):
                logger.info(f"Job {job.id} was cancelled after reading input, aborting")
                return

            # Reconstruct the full typed payload from the serialized job data.
            # ``from_job_payload`` deserializes the whole dict so no field can
            # be silently dropped at the worker boundary (issue #17); see its
            # docstring for the file-bound override rationale.
            payload = NotebookPayload.from_job_payload(
                payload_data,
                content=notebook_text,
                input_file=str(input_path),
                output_file=job.output_file,
                fallback_correlation_id=f"job-{job.id}",
            )

            # Create output spec from the reconstructed (typed) payload, so the
            # descriptor values come from a single validated source rather than
            # re-reading the raw dict with ad-hoc defaults.
            output_spec = create_output_spec(
                kind=payload.kind,
                prog_lang=payload.prog_lang,
                language=payload.language,
                format=payload.format,
            )

            # Determine source directory for supporting files (Docker mode with source mount)
            source_dir: Path | None = None
            if host_data_dir and payload.source_topic_dir:
                # Convert host topic directory to container path
                from clm.infrastructure.workers.worker_base import convert_input_path_to_container

                source_dir = convert_input_path_to_container(
                    payload.source_topic_dir, host_data_dir
                )
                logger.debug(
                    f"Docker mode: using source directory {source_dir} for supporting files"
                )

            # Get cache and process notebook
            cache = self._ensure_cache_initialized()
            logger.debug(f"Processing notebook with NotebookProcessor for {input_path.name}")
            processor = NotebookProcessor(
                output_spec,
                cache=cache,
                heartbeat_store=self._heartbeat_store,
                heartbeat_job_id=job.id,
            )
            try:
                result = await processor.process_notebook(payload, source_dir=source_dir)
            finally:
                # Always clear per-job heartbeat fields so the monitor doesn't
                # show a stale "executing cell X" line after the job ends.
                if self._heartbeat_store is not None:
                    try:
                        self._heartbeat_store.finish_job()
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug(
                            f"Worker {self.worker_id}: failed to clear "
                            f"per-job heartbeat fields: {exc}"
                        )
            logger.debug(f"Notebook processing complete for {input_path.name}")

            # Collect warnings from the processor
            warnings = processor.get_warnings()
            if warnings:
                logger.debug(f"Notebook processing generated {len(warnings)} warning(s)")
                self.set_job_warnings(warnings)

            # Write output file
            # In Docker mode, convert host path to container path
            host_workspace = os.environ.get("CLM_HOST_WORKSPACE")
            if host_workspace:
                from clm.infrastructure.workers.worker_base import convert_host_path_to_container

                output_path = convert_host_path_to_container(job.output_file, host_workspace)
                logger.debug(f"Converted output path: {job.output_file} -> {output_path}")
            else:
                output_path = Path(job.output_file)

            output_path.parent.mkdir(parents=True, exist_ok=True)

            # newline="\n" is required: without it, Python's text-mode
            # universal-newline translation rewrites every "\n" to os.linesep
            # (CRLF) on Windows Direct workers, while Docker/Linux workers emit
            # LF. That platform split produces spurious CRLF in built output
            # (noisy diffs, "CRLF will be replaced by LF" warnings). Pinning
            # LF keeps output byte-identical across platforms.
            with open(output_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(result)

            logger.info(f"Notebook written to {output_path}")

            # Add to cache (works for both SQLite and API modes)
            self.job_queue.add_to_cache(
                job.output_file,
                job.content_hash,
                {
                    "format": payload.format,
                    "kind": payload.kind,
                    "prog_lang": payload.prog_lang,
                    "language": payload.language,
                },
            )
            logger.debug(f"Added result to cache for {job.output_file}")

        except Exception as e:
            logger.error(f"Error processing notebook job {job.id}: {e}", exc_info=True)
            raise

    def cleanup(self):
        """Clean up resources including the executed notebook cache."""
        # Close the SQLite cache if it was initialized (API cache has no
        # connection to close — it's stateless aside from the httpx client).
        if isinstance(self._cache, ExecutedNotebookCache):
            try:
                self._cache.__exit__(None, None, None)
                logger.info("Closed executed notebook cache")
            except Exception as e:
                logger.warning(f"Error closing cache: {e}")
        self._cache = None

        if self._api_cache_client is not None:
            try:
                self._api_cache_client.close()
            except Exception as e:
                logger.warning(f"Error closing API cache client: {e}")
            self._api_cache_client = None

        # Clear and release the per-worker heartbeat row so the monitor does
        # not keep showing this worker as "currently executing cell X" after
        # the process exits.
        if self._heartbeat_store is not None:
            try:
                self._heartbeat_store.clear()
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Error clearing heartbeat row: {e}")
            try:
                self._heartbeat_store.close()
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Error closing heartbeat store: {e}")
            self._heartbeat_store = None

        # Call parent cleanup
        super().cleanup()


def main():
    """Main entry point for notebook worker."""
    # Determine mode based on environment
    if API_URL:
        logger.info(f"Starting notebook worker in API mode (URL: {API_URL})")

        # Get pre-assigned worker ID or register via API
        # This handles both pre-registration (CLM_WORKER_ID set) and legacy registration
        worker_id = Worker.get_or_register_worker(
            db_path=None, api_url=API_URL, worker_type="notebook"
        )

        # Create worker in API mode. The executed_notebooks cache is reached
        # via the Worker API (``ApiExecutedNotebookCache``) — the worker
        # never opens ``clm_cache.db`` directly in this mode, so
        # ``cache_db_path`` is intentionally left unset.
        worker = NotebookWorker(worker_id, api_url=API_URL)
    else:
        logger.info("Starting notebook worker in SQLite mode")

        # Ensure database exists
        if not DB_PATH.exists():
            logger.info(f"Initializing database at {DB_PATH}")
            init_database(DB_PATH)

        # Get pre-assigned worker ID or register with retry logic
        # This handles both pre-registration (CLM_WORKER_ID set) and legacy registration
        worker_id = Worker.get_or_register_worker(
            db_path=DB_PATH, api_url=None, worker_type="notebook"
        )

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
