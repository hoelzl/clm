"""The in-build dead-worker sweep also recovers jobs whose worker row is gone (#799 C2/C7).

Another process (an older clm, ``clm workers cleanup``) can delete a worker's
row. No monitor then marks it dead, and the sweep's old INNER JOIN on
``workers`` couldn't see the job, so the build waited for the stall detector.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from clm.infrastructure.database.schema import init_database
from clm.infrastructure.database.worker_heartbeats import touch_job_beacon
from tests.infrastructure.backends.test_sqlite_backend_resilience import _backend


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    init_database(path)
    return path


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def _orphan_job(db: Path, session_id: str, *, beacon: bool = False) -> int:
    """A processing job whose worker (id 41) has no row."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        job_id = conn.execute(
            """
            INSERT INTO jobs (job_type, status, input_file, output_file, content_hash,
                              payload, worker_id, started_at, attempts, session_id)
            VALUES ('notebook', 'processing', 'f.py', 'f.html', 'h', '{}', 41,
                    CURRENT_TIMESTAMP, 1, ?)
            RETURNING id
            """,
            (session_id,),
        ).fetchone()[0]
        if beacon:
            touch_job_beacon(conn, 41, job_id)
        conn.commit()
        return job_id
    finally:
        conn.close()


def _status(db: Path, job_id: int) -> str:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_id", "beacon", "recovered"),
    [("A", False, True), ("A", True, False), ("B", False, False)],
    ids=["own-silent", "own-beaconing", "other-build"],
)
async def test_a_job_whose_worker_row_is_gone(
    temp_db: Path, temp_workspace: Path, session_id: str, beacon: bool, recovered: bool
) -> None:
    job_id = _orphan_job(temp_db, session_id, beacon=beacon)
    backend = _backend(temp_db, temp_workspace)
    backend.worker_session_id = "A"
    try:
        assert backend._cleanup_dead_worker_jobs() == int(recovered)

        assert _status(temp_db, job_id) == ("pending" if recovered else "processing")
    finally:
        await backend.shutdown()
