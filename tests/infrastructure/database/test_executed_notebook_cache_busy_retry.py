"""``ExecutedNotebookCache.store`` under lock contention (issue #945).

The host-side cache writes gained a bounded busy-retry in #918, but the
worker-side store of the executed notebook did a bare INSERT + commit. Under
8 workers all committing multi-hundred-KB payloads against the shared cache
DB, ``database is locked`` escaped as a job failure that the build reported
as a *user* error ("Check your notebook for errors") on a healthy notebook —
persisted it to the issue cache — and the executed notebook was not cached,
so the next build re-executed it.

Contract pinned here: the store retries transient lock errors (refreshing a
caller-supplied heartbeat between attempts), rolls back on every failure so
the long-lived connection never stays inside a half-open transaction, and
degrades a lock that outlasts the schedule to a warning + ``False`` rather
than an exception. Every other database error still propagates.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from nbformat.v4 import new_code_cell, new_notebook

from clm.infrastructure.database import busy_retry
from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache

_INSERT_MARKER = "INSERT OR REPLACE INTO executed_notebooks"
_KEY = ("/t/nb.py", "h1", "en", "python")


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(busy_retry, "DEFAULT_BUSY_RETRY_DELAYS", (0.0, 0.0, 0.0))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "clm_cache.db"


@pytest.fixture
def nb():
    notebook = new_notebook()
    notebook.cells = [new_code_cell("print('hello')")]
    notebook.cells[0]["outputs"] = [{"output_type": "stream", "name": "stdout", "text": "hello\n"}]
    return notebook


class _FlakyConn:
    """Proxy around a real connection whose INSERT / ``commit`` raise a
    configurable error a configurable number of times."""

    def __init__(
        self,
        inner: sqlite3.Connection,
        *,
        execute_failures: int = 0,
        commit_failures: int = 0,
        error: Exception | None = None,
    ):
        self._inner = inner
        self._error = error or sqlite3.OperationalError("database is locked")
        self.execute_left = execute_failures
        self.commit_left = commit_failures
        self.insert_attempts = 0
        self.commit_attempts = 0

    def execute(self, sql, *args, **kwargs):
        if _INSERT_MARKER in sql:
            self.insert_attempts += 1
            if self.execute_left > 0:
                self.execute_left -= 1
                raise self._error
        return self._inner.execute(sql, *args, **kwargs)

    def commit(self):
        self.commit_attempts += 1
        if self.commit_left > 0:
            self.commit_left -= 1
            raise self._error
        return self._inner.commit()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_store_retries_locked_insert(db_path, nb):
    with ExecutedNotebookCache(db_path) as cache:
        flaky = _FlakyConn(cache.conn, execute_failures=2)
        cache.conn = flaky  # type: ignore[assignment]

        assert cache.store(*_KEY, nb) is True

        assert flaky.insert_attempts == 3
        assert cache.get(*_KEY) is not None


def test_store_retries_locked_commit_and_rolls_back_between_attempts(db_path, nb):
    with ExecutedNotebookCache(db_path) as cache:
        flaky = _FlakyConn(cache.conn, commit_failures=2)
        cache.conn = flaky  # type: ignore[assignment]

        assert cache.store(*_KEY, nb) is True

        assert flaky.commit_attempts == 3
        assert not cache.conn.in_transaction
        assert cache.get(*_KEY) is not None


def test_store_calls_on_busy_retry_between_attempts(db_path, nb):
    """A worker passes its heartbeat refresh here; it must fire once per
    backoff so a store stuck behind the lock never looks like a dead process."""
    beats = [0]

    def heartbeat() -> None:
        beats[0] += 1

    with ExecutedNotebookCache(db_path, on_busy_retry=heartbeat) as cache:
        flaky = _FlakyConn(cache.conn, execute_failures=2)
        cache.conn = flaky  # type: ignore[assignment]

        assert cache.store(*_KEY, nb) is True

    assert beats[0] == 2


def test_store_degrades_to_warning_when_lock_outlasts_schedule(db_path, nb, caplog):
    """The give-up path, with a real transaction open: the INSERT succeeds
    each time and the COMMIT is what stays locked. The store must roll back,
    report False, warn, and leave neither a row nor an open transaction —
    and never raise into the job."""
    with ExecutedNotebookCache(db_path) as cache:
        flaky = _FlakyConn(cache.conn, commit_failures=99)
        cache.conn = flaky  # type: ignore[assignment]

        with caplog.at_level(logging.WARNING):
            result = cache.store(*_KEY, nb)

        assert result is False
        # 1 + len(DEFAULT_BUSY_RETRY_DELAYS) attempts, then degrade.
        assert flaky.commit_attempts == 4
        assert not cache.conn.in_transaction
        assert cache.get(*_KEY) is None

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("/t/nb.py" in m and "NOT cached" in m and "re-execute" in m for m in warnings), (
        warnings
    )


def test_store_does_not_retry_non_lock_errors_but_still_rolls_back(db_path, nb):
    """A non-transient failure propagates from the first attempt — and the
    rollback still runs, so the connection does not keep a RESERVED lock
    (and block every other worker) for the rest of the process."""
    with ExecutedNotebookCache(db_path) as cache:
        flaky = _FlakyConn(
            cache.conn,
            commit_failures=99,
            error=sqlite3.DatabaseError("database disk image is malformed"),
        )
        cache.conn = flaky  # type: ignore[assignment]

        with pytest.raises(sqlite3.DatabaseError, match="malformed"):
            cache.store(*_KEY, nb)

        assert flaky.commit_attempts == 1
        assert not cache.conn.in_transaction


def test_store_does_not_retry_non_lock_operational_errors(db_path, nb):
    with ExecutedNotebookCache(db_path) as cache:
        flaky = _FlakyConn(
            cache.conn,
            execute_failures=99,
            error=sqlite3.OperationalError("no such table: executed_notebooks"),
        )
        cache.conn = flaky  # type: ignore[assignment]

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            cache.store(*_KEY, nb)

        assert flaky.insert_attempts == 1


def test_store_survives_a_real_writer_holding_the_lock(db_path, nb, monkeypatch):
    """End-to-end shape of the #945 failure: another connection holds a
    write lock past the busy timeout; the store must retry until it is
    released rather than give up on the first ``database is locked``."""
    import clm.infrastructure.database.executed_notebook_cache as mod

    with ExecutedNotebookCache(db_path) as cache:
        # A short busy_timeout so the first attempt fails fast, as under
        # real contention past the 30 s local timeout.
        cache.conn.execute("PRAGMA busy_timeout=50")

        blocker = sqlite3.connect(str(db_path), isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute(
            "INSERT INTO executed_notebooks "
            "(input_file, content_hash, language, prog_lang, executed_notebook) "
            "VALUES ('/other', 'x', 'en', 'python', X'00')"
        )
        released = [False]

        def sleep(_delay: float) -> None:
            # Release the lock during the first backoff; the next attempt
            # must then succeed.
            if not released[0]:
                blocker.execute("COMMIT")
                released[0] = True

        real_retry = busy_retry.retry_on_busy
        monkeypatch.setattr(
            mod, "retry_on_busy", lambda op, **kw: real_retry(op, sleep=sleep, **kw)
        )
        try:
            assert cache.store(*_KEY, nb) is True
        finally:
            blocker.close()

        assert released[0], "store never hit the lock, so the test proved nothing"
        assert cache.get(*_KEY) is not None


def test_notebook_worker_wires_its_heartbeat_into_the_cache(tmp_path):
    """Direct-mode workers must pass their heartbeat refresh as
    ``on_busy_retry`` — the worst-case retry schedule brushes the 120 s
    heartbeat grace, so without it a worker stuck behind the cache lock is
    swept as dead by another build."""
    from clm.infrastructure.database.schema import init_database
    from clm.workers.notebook.notebook_worker import NotebookWorker

    jobs_db = tmp_path / "jobs.db"
    init_database(jobs_db)
    worker = NotebookWorker(1, jobs_db, cache_db_path=tmp_path / "clm_cache.db")
    try:
        cache = worker._ensure_cache_initialized()
        assert isinstance(cache, ExecutedNotebookCache)
        assert cache.on_busy_retry == worker._update_heartbeat
    finally:
        if worker._cache is not None:
            worker._cache.__exit__(None, None, None)  # type: ignore[union-attr]
        worker.job_queue.close()
