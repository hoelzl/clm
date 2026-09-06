"""``JobQueue.add_job`` / ``check_cache`` retry transient lock errors (issue #917).

A single starved INSERT on the submit thread used to propagate through the
shielded submission and tear down the whole build's stage TaskGroup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from clm.infrastructure.database import busy_retry
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def queue(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    jq = JobQueue(db_path)
    yield jq
    jq.close()


def _flaky_execute(conn: sqlite3.Connection, failures: int, marker: str):
    """Make ``conn.execute`` raise 'database is locked' on the next N calls
    whose SQL contains *marker*."""
    real_execute = conn.execute
    left = [failures]

    def execute(sql, *args, **kwargs):
        if marker in sql and left[0] > 0:
            left[0] -= 1
            raise sqlite3.OperationalError("database is locked")
        return real_execute(sql, *args, **kwargs)

    return execute, left


def test_add_job_retries_locked_insert(queue: JobQueue, monkeypatch):
    monkeypatch.setattr(busy_retry, "DEFAULT_BUSY_RETRY_DELAYS", (0.0, 0.0, 0.0))
    conn = queue._get_conn()
    execute, left = _flaky_execute(conn, 2, "INSERT INTO jobs")
    monkeypatch.setattr(queue, "_get_conn", lambda: _Proxy(conn, execute))

    job_id = queue.add_job("notebook", "in.py", "out.ipynb", "h", {"data": 1})

    assert left[0] == 0
    assert queue.get_job(job_id) is not None


def test_check_cache_retries_locked_begin(queue: JobQueue, monkeypatch):
    monkeypatch.setattr(busy_retry, "DEFAULT_BUSY_RETRY_DELAYS", (0.0, 0.0, 0.0))
    queue.add_to_cache("out.ipynb", "h", {"ok": True})
    conn = queue._get_conn()
    execute, left = _flaky_execute(conn, 2, "BEGIN IMMEDIATE")
    monkeypatch.setattr(queue, "_get_conn", lambda: _Proxy(conn, execute))

    assert queue.check_cache("out.ipynb", "h") == {"ok": True}
    assert left[0] == 0
    assert not conn.in_transaction


def test_add_job_does_not_retry_other_errors(queue: JobQueue, monkeypatch):
    conn = queue._get_conn()
    real_execute = conn.execute

    def execute(sql, *args, **kwargs):
        if "INSERT INTO jobs" in sql:
            raise sqlite3.OperationalError("no such table: jobs")
        return real_execute(sql, *args, **kwargs)

    monkeypatch.setattr(queue, "_get_conn", lambda: _Proxy(conn, execute))
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        queue.add_job("notebook", "in.py", "out.ipynb", "h", {})


class _Proxy:
    def __init__(self, inner, execute):
        self._inner = inner
        self._execute = execute

    def execute(self, *args, **kwargs):
        return self._execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)
