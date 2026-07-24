"""Tests for the journal-mode policy.

WAL is unsafe over a network share: SQLite's WAL index lives in a
memory-mapped ``-shm`` file that is not coherent between machines, so two hosts
can each believe they claimed the same job, and interleaved checkpoints can
corrupt the file. CLM supports a shared jobs database, so these tests pin the
fallback that keeps that configuration safe.
"""

import sqlite3
from pathlib import Path

import pytest

from clm.infrastructure.database.journal_mode import (
    NetworkJournalModeError,
    configure_connection,
    is_network_path,
)


@pytest.fixture
def db(tmp_path: Path):
    """An open connection to a real on-disk database and its path."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    yield conn, path
    conn.close()


def _journal_mode(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).upper()


class TestLocalDatabases:
    def test_local_database_uses_wal(self, db):
        conn, path = db
        assert configure_connection(conn, path) == "WAL"
        assert _journal_mode(conn) == "WAL"

    def test_synchronous_override_is_honoured_locally(self, db):
        conn, path = db
        configure_connection(conn, path, synchronous="NORMAL")
        # 1 == NORMAL. The job queue relies on this: the default FULL would
        # fsync on every queue write, which is pure latency on its hot path.
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1

    def test_busy_timeout_is_set(self, db):
        conn, path = db
        configure_connection(conn, path)
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000


class TestNetworkDatabases:
    """A database on a share must never be left in WAL mode."""

    @pytest.fixture
    def force_network(self, monkeypatch):
        monkeypatch.setattr(
            "clm.infrastructure.database.journal_mode.is_network_path",
            lambda _path: True,
        )

    def test_network_database_uses_delete_journaling(self, db, force_network):
        conn, path = db
        assert configure_connection(conn, path) == "DELETE"
        assert _journal_mode(conn) == "DELETE"

    def test_existing_wal_database_is_converted(self, db, force_network):
        """The realistic upgrade case: the share already holds a WAL database."""
        conn, path = db
        conn.execute("PRAGMA journal_mode=WAL")
        assert _journal_mode(conn) == "WAL"

        configure_connection(conn, path)

        assert _journal_mode(conn) == "DELETE"

    def test_network_database_forces_full_synchronous(self, db, force_network):
        """The local hot-path relaxation must not apply over a share."""
        conn, path = db
        configure_connection(conn, path, synchronous="NORMAL")
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL

    def test_longer_busy_timeout_over_the_wire(self, db, force_network):
        conn, path = db
        configure_connection(conn, path)
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 60_000

    def test_refuses_to_continue_if_wal_cannot_be_dropped(self, db, monkeypatch):
        """Failing to leave WAL means another connection holds the database.

        That is precisely the dangerous case — another machine is using it — so
        we must fail loudly rather than continue in an unsafe mode.
        """
        conn, path = db
        monkeypatch.setattr(
            "clm.infrastructure.database.journal_mode.is_network_path",
            lambda _path: True,
        )

        with pytest.raises(NetworkJournalModeError) as excinfo:
            configure_connection(_StuckInWal(conn), path)

        message = str(excinfo.value)
        assert "network share" in message
        # The message has to tell the user what to actually do about it.
        assert "CLM_JOBS_DB_PATH" in message


class TestNetworkDetection:
    def test_local_temp_path_is_not_network(self, tmp_path: Path):
        assert is_network_path(tmp_path / "clm_jobs.db") is False

    @pytest.mark.parametrize(
        "unc",
        [
            r"\\fileserver\share\clm_jobs.db",
            "//fileserver/share/clm_jobs.db",
        ],
    )
    def test_unc_paths_are_network(self, unc):
        assert is_network_path(unc) is True

    def test_detection_failure_is_not_fatal(self, monkeypatch, tmp_path: Path):
        """Detection is best-effort; a failure must not break the build.

        It fails open (assumes local), which preserves existing behaviour rather
        than introducing a new failure mode.
        """
        monkeypatch.setattr(
            "clm.infrastructure.database.journal_mode.Path",
            _ExplodingPath,
        )
        assert is_network_path(tmp_path / "clm_jobs.db") is False


class _StuckInWal:
    """A connection that reports WAL no matter what journal mode is requested.

    Stands in for the real hazard: another connection — typically on another
    machine — holds the database, so SQLite refuses the mode change and returns
    the current mode instead. ``sqlite3.Connection.execute`` is read-only, so
    this proxies rather than patches.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql: str, *args):
        if sql.startswith("PRAGMA journal_mode="):
            return self._conn.execute("SELECT 'wal'")
        return self._conn.execute(sql, *args)


class _ExplodingPath:
    def __init__(self, *_args, **_kwargs):
        raise OSError("simulated filesystem failure")
