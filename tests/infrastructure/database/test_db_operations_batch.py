"""``DatabaseManager.batch`` — one transaction for many writes (issue #917)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from clm.core.build_data_classes import BuildWarning
from clm.infrastructure.database.db_operations import DatabaseManager


def _warning(msg: str) -> BuildWarning:
    return BuildWarning(category="general", message=msg, severity="low", file_path="f.py")


def _count_issues(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM processing_issues").fetchone()[0]
    finally:
        conn.close()


def test_batch_defers_commits_until_exit(tmp_path):
    db_path = tmp_path / "cache.db"
    with DatabaseManager(db_path) as db:
        with db.batch():
            db.store_warning("f.py", "h", "m1", _warning("a"))
            db.store_warning("f.py", "h", "m2", _warning("b"))
            # Another connection sees nothing yet: no per-method commit ran.
            assert _count_issues(db_path) == 0
        assert _count_issues(db_path) == 2


def test_batch_rolls_back_everything_on_error(tmp_path):
    db_path = tmp_path / "cache.db"
    with DatabaseManager(db_path) as db:
        with pytest.raises(RuntimeError), db.batch():
            db.store_warning("f.py", "h", "m1", _warning("a"))
            raise RuntimeError("boom")
        assert _count_issues(db_path) == 0
        assert db.conn is not None and not db.conn.in_transaction
        # The connection is reusable afterwards.
        with db.batch():
            db.store_warning("f.py", "h", "m1", _warning("a"))
        assert _count_issues(db_path) == 1


def test_batch_recovers_when_commit_itself_raises(tmp_path):
    """A COMMIT that raises leaves SQLite's transaction open; the batch must
    roll it back or every later ``BEGIN IMMEDIATE`` on this connection fails
    with 'cannot start a transaction within a transaction' for good
    (adversarial review of the #917 fix)."""
    db_path = tmp_path / "cache.db"
    with DatabaseManager(db_path) as db:
        assert db.conn is not None
        real_commit = db.conn.commit
        state = {"fail": True}

        class _Conn:
            """Proxy that makes exactly one commit() raise like a held lock."""

            def __init__(self, inner):
                self._inner = inner

            def commit(self):
                if state["fail"]:
                    state["fail"] = False
                    raise sqlite3.OperationalError("database is locked")
                return real_commit()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        db.conn = _Conn(db.conn)  # type: ignore[assignment]
        with pytest.raises(sqlite3.OperationalError), db.batch():
            db.store_warning("f.py", "h", "m1", _warning("a"))
        assert not db.conn.in_transaction
        # Retry succeeds on a clean connection.
        with db.batch():
            db.store_warning("f.py", "h", "m1", _warning("a"))
        assert _count_issues(db_path) == 1


def test_nested_batch_is_flattened(tmp_path):
    db_path = tmp_path / "cache.db"
    with DatabaseManager(db_path) as db:
        with db.batch():
            db.store_warning("f.py", "h", "m1", _warning("a"))
            with db.batch():
                db.store_warning("f.py", "h", "m2", _warning("b"))
            assert _count_issues(db_path) == 0  # inner exit did not commit
        assert _count_issues(db_path) == 2
