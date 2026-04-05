"""Persistence for :class:`ProcessingJob` instances.

Jobs outlive the process — an Auphonic production can take 30 minutes,
and a crash in the middle must not lose the job reference so the poller
can pick it up on restart. This module provides a :class:`JobStore`
Protocol plus a :class:`JsonFileJobStore` implementation that writes all
jobs to a single JSON file under ``<recordings-root>/.clm/jobs.json``.

Atomic writes: each save serializes the full job list to a temp file in
the same directory and then renames it over the target. This guarantees
that readers never see a half-written file even on abrupt shutdown.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Protocol

from loguru import logger

from clm.recordings.workflow.jobs import ProcessingJob

#: Default location of the job store, relative to the recordings root.
DEFAULT_JOBS_FILE = Path(".clm/jobs.json")


class JobStore(Protocol):
    """Persistence interface for jobs.

    Implementations must be thread-safe: the :class:`JobManager` may call
    :meth:`save` from its submit path and from its poller thread.
    """

    def load_all(self) -> list[ProcessingJob]:
        """Return every job currently in the store, in no particular order."""
        ...

    def save(self, job: ProcessingJob) -> None:
        """Persist *job* (inserting or replacing by id)."""
        ...

    def delete(self, job_id: str) -> None:
        """Remove the job with the given id, if present."""
        ...


class JsonFileJobStore:
    """Store all jobs in a single JSON file under the recordings tree.

    The file is read on construction and held in memory thereafter; each
    :meth:`save` rewrites the whole file atomically. This is fine for
    hundreds of jobs (lectures over a whole semester) and avoids the
    operational complexity of SQLite for a mostly-append workload.

    Args:
        path: Path to the JSON file. Parent directory is created if
            missing. If the file does not exist, the store starts empty.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._jobs: dict[str, ProcessingJob] = {}
        self._load_from_disk()

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    def load_all(self) -> list[ProcessingJob]:
        with self._lock:
            return list(self._jobs.values())

    def save(self, job: ProcessingJob) -> None:
        with self._lock:
            self._jobs[job.id] = job
            self._flush_to_disk()

    def delete(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                self._flush_to_disk()

    # ------------------------------------------------------------------
    # Disk IO (caller must hold self._lock)
    # ------------------------------------------------------------------

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Could not read job store at {}: {}. Starting empty.",
                self._path,
                exc,
            )
            return

        jobs = raw.get("jobs", []) if isinstance(raw, dict) else []
        for entry in jobs:
            try:
                job = ProcessingJob.model_validate(entry)
            except Exception as exc:
                logger.warning("Skipping invalid job entry in {}: {}", self._path, exc)
                continue
            self._jobs[job.id] = job

    def _flush_to_disk(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": 1,
            "jobs": [job.model_dump(mode="json") for job in self._jobs.values()],
        }
        serialized = json.dumps(payload, indent=2, sort_keys=False)

        # Atomic write: tmp file in the same directory, then rename.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp.write_text(serialized, encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
