"""Tests for the worker_heartbeats schema and store helper.

Covers:

* The v8 schema migration creates the ``worker_heartbeats`` table with the
  correct columns and index.
* :class:`WorkerHeartbeatStore` UPSERTs the expected row on ``begin_cell`` /
  ``record_output`` / ``finish_job`` / ``clear``.
* ANSI codes and multi-line stream output are reduced to the last meaningful
  line before being stored.
* Write failures are best-effort: a poisoned DB does not propagate errors
  back to the caller, and the store disables further writes until reset.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from clm.infrastructure.database.schema import DATABASE_VERSION, init_database
from clm.infrastructure.database.worker_heartbeats import (
    MAX_EXCERPT_LENGTH,
    WorkerHeartbeatStore,
    truncate_excerpt,
)

# -- helpers -----------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Initialise a fresh jobs DB and return its path."""
    path = tmp_path / "jobs.db"
    init_database(path)
    return path


def _register_worker(db_path: Path, worker_id_hint: int | None = None) -> int:
    """Insert a row in ``workers`` so the FK constraint is satisfied.

    Returns the assigned worker id. If ``worker_id_hint`` is supplied the
    test wants a deterministic id; we INSERT with an explicit id.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        if worker_id_hint is None:
            cur = conn.execute(
                "INSERT INTO workers (worker_type, container_id, status) "
                "VALUES ('notebook', 'nb-test-1', 'busy')"
            )
            conn.commit()
            assert cur.lastrowid is not None
            return cur.lastrowid
        conn.execute(
            "INSERT INTO workers (id, worker_type, container_id, status) "
            "VALUES (?, 'notebook', ?, 'busy')",
            (worker_id_hint, f"nb-test-{worker_id_hint}"),
        )
        conn.commit()
        return worker_id_hint
    finally:
        conn.close()


def _read_heartbeat(db_path: Path, worker_id: int) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM worker_heartbeats WHERE worker_id = ?",
            (worker_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# -- schema ------------------------------------------------------------------


class TestHeartbeatSchema:
    def test_schema_version_is_v8(self, db_path: Path) -> None:
        """After init the schema is at the documented latest version."""
        assert DATABASE_VERSION == 8

    def test_table_exists_with_expected_columns(self, db_path: Path) -> None:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute("PRAGMA table_info(worker_heartbeats)")
            columns = {row[1]: row[2] for row in cur.fetchall()}
        finally:
            conn.close()

        assert columns == {
            "worker_id": "INTEGER",
            "job_id": "INTEGER",
            "current_cell_index": "INTEGER",
            "total_cells": "INTEGER",
            "current_cell_started_at": "TIMESTAMP",
            "last_output_excerpt": "TEXT",
            "last_output_at": "TIMESTAMP",
            "heartbeat_at": "TIMESTAMP",
        }

    def test_index_on_job_id_present(self, db_path: Path) -> None:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = {row[0] for row in cur.fetchall()}
        finally:
            conn.close()

        assert "idx_worker_heartbeats_job" in indexes


# -- excerpt normalisation ---------------------------------------------------


class TestTruncateExcerpt:
    def test_strips_ansi_color_codes(self) -> None:
        text = "\x1b[31mhello\x1b[0m"
        assert truncate_excerpt(text) == "hello"

    def test_keeps_last_non_empty_line_only(self) -> None:
        text = "first line\nsecond line\n\nthird line\n"
        assert truncate_excerpt(text) == "third line"

    def test_handles_carriage_returns(self) -> None:
        # Progress bars often use '\r' to overwrite — we treat them as newlines.
        text = "loading...\rdone!"
        assert truncate_excerpt(text) == "done!"

    def test_truncates_with_ellipsis(self) -> None:
        long_text = "x" * (MAX_EXCERPT_LENGTH + 50)
        result = truncate_excerpt(long_text)
        assert len(result) == MAX_EXCERPT_LENGTH
        assert result.endswith("...")

    def test_empty_input_returns_empty(self) -> None:
        assert truncate_excerpt("") == ""
        assert truncate_excerpt("\n\n  \n") == ""


# -- store write path --------------------------------------------------------


class TestHeartbeatStoreWritePath:
    def test_begin_cell_writes_expected_row(self, db_path: Path) -> None:
        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        store.begin_cell(job_id=42, cell_index=3, total_cells=10)

        row = _read_heartbeat(db_path, worker_id)
        assert row is not None
        assert row["worker_id"] == worker_id
        assert row["job_id"] == 42
        assert row["current_cell_index"] == 3
        assert row["total_cells"] == 10
        assert row["current_cell_started_at"] is not None
        assert row["heartbeat_at"] is not None
        assert row["last_output_excerpt"] is None
        assert row["last_output_at"] is None

    def test_begin_cell_upserts_on_repeated_calls(self, db_path: Path) -> None:
        """Two begin_cell calls produce one row, not two."""
        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        store.begin_cell(job_id=42, cell_index=1, total_cells=5)
        store.begin_cell(job_id=42, cell_index=2, total_cells=5)

        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM worker_heartbeats WHERE worker_id = ?",
                (worker_id,),
            )
            assert cur.fetchone()[0] == 1
        finally:
            conn.close()

        row = _read_heartbeat(db_path, worker_id)
        assert row is not None
        assert row["current_cell_index"] == 2

    def test_record_output_keeps_only_last_meaningful_line(self, db_path: Path) -> None:
        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        store.begin_cell(job_id=42, cell_index=0, total_cells=3)
        store.record_output("first\nsecond chunk\n")

        row = _read_heartbeat(db_path, worker_id)
        assert row is not None
        assert row["last_output_excerpt"] == "second chunk"
        assert row["last_output_at"] is not None

    def test_record_output_strips_ansi(self, db_path: Path) -> None:
        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        store.begin_cell(job_id=42, cell_index=0, total_cells=3)
        store.record_output("\x1b[1;32mGreen text\x1b[0m\n")

        row = _read_heartbeat(db_path, worker_id)
        assert row is not None
        assert row["last_output_excerpt"] == "Green text"

    def test_record_output_ignores_empty_chunks(self, db_path: Path) -> None:
        """A whitespace-only message must not blank out a previous excerpt."""
        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        store.begin_cell(job_id=42, cell_index=0, total_cells=3)
        store.record_output("real output\n")
        before = _read_heartbeat(db_path, worker_id)

        # Whitespace-only chunks should NOT clobber the last excerpt.
        store.record_output("\n\n  \n")
        after = _read_heartbeat(db_path, worker_id)

        assert before is not None and after is not None
        assert after["last_output_excerpt"] == "real output"

    def test_finish_job_nulls_per_job_fields(self, db_path: Path) -> None:
        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        store.begin_cell(job_id=42, cell_index=5, total_cells=10)
        store.record_output("hello\n")
        store.finish_job()

        row = _read_heartbeat(db_path, worker_id)
        assert row is not None
        assert row["job_id"] is None
        assert row["current_cell_index"] is None
        assert row["total_cells"] is None
        assert row["last_output_excerpt"] is None

    def test_clear_removes_row(self, db_path: Path) -> None:
        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        store.begin_cell(job_id=1, cell_index=0, total_cells=1)
        assert _read_heartbeat(db_path, worker_id) is not None

        store.clear()
        assert _read_heartbeat(db_path, worker_id) is None

    def test_multiple_workers_dont_collide(self, db_path: Path) -> None:
        wid_a = _register_worker(db_path, worker_id_hint=10)
        wid_b = _register_worker(db_path, worker_id_hint=20)
        store_a = WorkerHeartbeatStore(db_path, wid_a)
        store_b = WorkerHeartbeatStore(db_path, wid_b)

        store_a.begin_cell(job_id=100, cell_index=1, total_cells=5)
        store_b.begin_cell(job_id=200, cell_index=4, total_cells=8)

        row_a = _read_heartbeat(db_path, wid_a)
        row_b = _read_heartbeat(db_path, wid_b)
        assert row_a is not None and row_b is not None
        assert row_a["current_cell_index"] == 1
        assert row_a["job_id"] == 100
        assert row_b["current_cell_index"] == 4
        assert row_b["job_id"] == 200


# -- failure handling --------------------------------------------------------


class TestHeartbeatStoreFailureHandling:
    def test_write_failure_does_not_raise(self, db_path: Path) -> None:
        """A poisoned connection must not break the caller."""
        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        # Force the first write to succeed so we have a connection, then
        # break the table out from under it.
        store.begin_cell(job_id=1, cell_index=0, total_cells=1)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("DROP TABLE worker_heartbeats")
            conn.commit()
        finally:
            conn.close()

        # This must not raise and must mark the store as disabled.
        store.begin_cell(job_id=1, cell_index=1, total_cells=1)
        store.record_output("never written\n")  # silently no-op while disabled

    def test_slow_write_disables_further_writes(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a write blows the 5ms threshold, subsequent updates pause.

        Simulated by lowering the threshold to 0 — every real write past
        the first (connection priming) one is then "too slow" and the
        store flips itself off.
        """
        from clm.infrastructure.database import worker_heartbeats as wh

        monkeypatch.setattr(wh, "SLOW_WRITE_THRESHOLD_SECONDS", 0.0)

        worker_id = _register_worker(db_path)
        store = WorkerHeartbeatStore(db_path, worker_id)

        # First write primes the connection — slow-write check is skipped.
        store.begin_cell(job_id=1, cell_index=0, total_cells=5)
        # Second write triggers the slow-write check (and disables the
        # store under the threshold-of-0 above).
        store.record_output("first visible\n")

        # Now the store is disabled — further writes are silently dropped.
        store.record_output("should be ignored\n")
        row = _read_heartbeat(db_path, worker_id)
        assert row is not None
        # The first record_output landed before disable kicked in:
        assert row["last_output_excerpt"] == "first visible"

        # Re-enable and confirm writes flow again.
        store.reset_disabled()
        store.record_output("now visible\n")
        row = _read_heartbeat(db_path, worker_id)
        assert row is not None
        assert row["last_output_excerpt"] == "now visible"


# -- integration sanity ------------------------------------------------------


def test_heartbeat_round_trip_smoke(db_path: Path) -> None:
    """A short end-to-end-shaped write sequence produces sensible row state."""
    worker_id = _register_worker(db_path)
    store = WorkerHeartbeatStore(db_path, worker_id)

    store.begin_cell(job_id=7, cell_index=0, total_cells=2)
    store.record_output("import torch\n")
    time.sleep(0.01)
    store.record_output("Epoch 1/3 - loss: 0.123\n")
    store.begin_cell(job_id=7, cell_index=1, total_cells=2)
    store.record_output("done\n")

    row = _read_heartbeat(db_path, worker_id)
    assert row is not None
    assert row["current_cell_index"] == 1
    assert row["total_cells"] == 2
    assert row["job_id"] == 7
    assert row["last_output_excerpt"] == "done"


def test_heartbeat_timestamps_match_sqlite_now(db_path: Path) -> None:
    """Stored timestamps must be UTC so collector's julianday('now') diff is non-negative.

    Regression: heartbeat writes originally used ``datetime.now()`` (local
    time) while the collector computes elapsed seconds with SQLite's
    ``julianday('now')`` (always UTC). On a host in CEST that produced
    ``cell_elapsed`` values around -7200s, which the monitor rendered as
    ``00:-7913``. This test asserts the diff is sane.
    """
    worker_id = _register_worker(db_path)
    store = WorkerHeartbeatStore(db_path, worker_id)
    store.begin_cell(job_id=42, cell_index=0, total_cells=1)
    store.record_output("hello\n")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """
            SELECT
                CAST((julianday('now') - julianday(current_cell_started_at)) * 86400 AS INTEGER) AS cell_elapsed,
                CAST((julianday('now') - julianday(last_output_at)) * 86400 AS INTEGER) AS since_last_output,
                CAST((julianday('now') - julianday(heartbeat_at)) * 86400 AS INTEGER) AS since_heartbeat
            FROM worker_heartbeats WHERE worker_id = ?
            """,
            (worker_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    cell_elapsed, since_last_output, since_heartbeat = row
    # The values must be non-negative (UTC-vs-local mismatch produces a large
    # negative offset, e.g. -7200 on CEST). A small positive value is fine —
    # we don't pin an upper bound because CI machines vary, but anything > 60
    # for an operation we just performed would indicate something seriously
    # wrong.
    assert 0 <= cell_elapsed < 60, f"cell_elapsed={cell_elapsed} (UTC/local mismatch?)"
    assert 0 <= since_last_output < 60, (
        f"since_last_output={since_last_output} (UTC/local mismatch?)"
    )
    assert 0 <= since_heartbeat < 60, f"since_heartbeat={since_heartbeat} (UTC/local mismatch?)"
