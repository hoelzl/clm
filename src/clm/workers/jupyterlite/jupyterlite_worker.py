"""SQLite-based JupyterLite site-builder worker.

Pulls ``jupyterlite`` jobs off the shared queue, reconstructs a
``BuildArgs`` bundle from the payload, and shells out to
``jupyter lite build``. The heavy ``jupyterlite-core`` dependency is
imported lazily (inside ``build_site``) so that the worker module
itself is safe to import even when the ``[jupyterlite]`` extra is
absent — that way discovery/registration code paths don't explode on
installs that never opt into the format.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from clm.infrastructure.database.job_queue import Job
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.worker_base import Worker

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DB_PATH = Path(os.environ.get("DB_PATH", "/db/jobs.db"))
API_URL = os.environ.get("CLM_API_URL")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - jupyterlite-worker - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class JupyterLiteWorker(Worker):
    """Worker that drives ``jupyter lite build`` from the SQLite queue."""

    def __init__(
        self,
        worker_id: int,
        db_path: Path | None = None,
        api_url: str | None = None,
    ):
        super().__init__(worker_id, "jupyterlite", db_path=db_path, api_url=api_url)
        mode = "API" if api_url else "SQLite"
        logger.info(f"JupyterLiteWorker {worker_id} initialized in {mode} mode")

    def process_job(self, job: Job) -> None:
        loop = self._get_or_create_loop()
        try:
            loop.run_until_complete(self._process_job_async(job))
        except Exception as e:
            logger.error(
                f"Worker {self.worker_id} error in event loop for job {job.id}: {e}",
                exc_info=True,
            )
            raise

    async def _process_job_async(self, job: Job) -> None:
        if self.job_queue.is_job_cancelled(job.id):
            logger.info(f"Job {job.id} was cancelled before processing, skipping")
            return

        payload = job.payload
        label = payload.get("input_file_name") or str(job.id)
        logger.info(f"Processing JupyterLite job {job.id} for {label}")

        from clm.workers.jupyterlite.builder import (
            BuildArgs,
            build_result_to_summary,
            build_site,
        )

        wheels = [Path(w) for w in payload.get("wheels", [])]
        environment_yml_raw = payload.get("environment_yml") or ""
        environment_yml = Path(environment_yml_raw) if environment_yml_raw else None

        raw_trees = payload.get("notebook_trees", {})
        notebook_trees = {k: Path(v) for k, v in raw_trees.items()}

        args = BuildArgs(
            notebook_trees=notebook_trees,
            output_dir=Path(payload["output_dir"]),
            kernel=payload["kernel"],
            wheels=wheels,
            environment_yml=environment_yml,
            app_archive=payload.get("app_archive", "offline"),
            launcher=payload.get("launcher", "python"),
            target_label=label,
            jupyterlite_core_version=payload.get("jupyterlite_core_version", ""),
            branding_theme=payload.get("branding_theme", ""),
            branding_logo=payload.get("branding_logo", ""),
            branding_site_name=payload.get("branding_site_name", ""),
        )

        result = build_site(args)
        logger.info(
            f"JupyterLite site ready at {result.site_dir} "
            f"({result.files_count} files, cache_key={result.cache_key[:12]}…)"
        )

        self.job_queue.add_to_cache(
            job.output_file,
            job.content_hash,
            {
                "cache_key": result.cache_key,
                "files_count": result.files_count,
                "summary": build_result_to_summary(result),
            },
        )


def main() -> None:
    """Entry point for ``python -m clm.workers.jupyterlite``."""
    if API_URL:
        logger.info(f"Starting JupyterLite worker in API mode (URL: {API_URL})")
        worker_id = Worker.get_or_register_worker(
            db_path=None, api_url=API_URL, worker_type="jupyterlite"
        )
        worker = JupyterLiteWorker(worker_id, api_url=API_URL)
    else:
        logger.info("Starting JupyterLite worker in SQLite mode")
        if not DB_PATH.exists():
            logger.info(f"Initializing database at {DB_PATH}")
            init_database(DB_PATH)
        worker_id = Worker.get_or_register_worker(
            db_path=DB_PATH, api_url=None, worker_type="jupyterlite"
        )
        worker = JupyterLiteWorker(worker_id, db_path=DB_PATH)

    try:
        worker.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
        worker.stop()
    except Exception as e:
        logger.error(f"Worker crashed: {e}", exc_info=True)
        raise
    finally:
        worker.cleanup()
        logger.info("Worker cleanup completed")


if __name__ == "__main__":
    main()
