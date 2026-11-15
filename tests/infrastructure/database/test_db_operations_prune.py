"""The build-end retention prunes of the cache DB (issue #1090)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache


def _insert(conn: sqlite3.Connection, file_path: str, meta: str, created_at: str, tag: str) -> int:
    cur = conn.execute(
        "INSERT INTO processed_files "
        "(file_path, content_hash, correlation_id, result, output_metadata, created_at) "
        "VALUES (?, ?, 'c', ?, ?, ?)",
        (file_path, tag, b"x" * 64, meta, created_at),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _surviving_tags(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT content_hash FROM processed_files")}
    finally:
        conn.close()


def test_prune_keeps_newest_versions_per_file_and_metadata(tmp_path):
    db_path = tmp_path / "cache.db"
    with DatabaseManager(db_path) as db:
        assert db.conn is not None
        for minute in range(4):
            ts = f"2026-01-01 00:0{minute}:00"
            _insert(db.conn, "a.py", "de|html", ts, f"a-de-{minute}")
            _insert(db.conn, "a.py", "en|html", ts, f"a-en-{minute}")
            _insert(db.conn, "b.py", "de|html", ts, f"b-de-{minute}")
        # A group already within the limit is left alone.
        _insert(db.conn, "c.py", "de|html", "2026-01-01 00:00:00", "c-de-0")
        db.conn.commit()

        assert db.prune_old_versions(retain_count=2) == 6

    assert _surviving_tags(db_path) == {
        "a-de-2",
        "a-de-3",
        "a-en-2",
        "a-en-3",
        "b-de-2",
        "b-de-3",
        "c-de-0",
    }


def test_prune_breaks_created_at_ties_by_insert_order(tmp_path):
    """``created_at`` has second resolution, so a rebuild within the same
    second ties. The later insert (higher id) must survive."""
    db_path = tmp_path / "cache.db"
    with DatabaseManager(db_path) as db:
        assert db.conn is not None
        for i in range(3):
            _insert(db.conn, "a.py", "de|html", "2026-01-01 00:00:00", f"v{i}")
        db.conn.commit()

        assert db.prune_old_versions(retain_count=1) == 2

    assert _surviving_tags(db_path) == {"v2"}


def test_prune_scans_a_covering_index_not_the_blob_table(tmp_path):
    """Regression test for #1090.

    ``result`` (a large BLOB) is stored before ``output_metadata`` and
    ``created_at`` in each row, so a window over the table walks every row's
    overflow chain: it reads the whole cache file. On a 7 GB cache that took
    minutes with no output after an aborted build. The prune must read only
    a covering index. The plan is checked for the exact DELETE statement
    that ``prune_old_versions`` runs.
    """
    db_path = tmp_path / "cache.db"
    with DatabaseManager(db_path) as db:
        assert db.conn is not None
        statements: list[str] = []
        db.conn.set_trace_callback(statements.append)
        db.prune_old_versions(retain_count=5)
        db.conn.set_trace_callback(None)

        delete_sql = next(s for s in statements if "DELETE FROM processed_files" in s)
        plan = [row[3] for row in db.conn.execute(f"EXPLAIN QUERY PLAN {delete_sql}")]

    _assert_table_scanned_only_by_covering_index(plan, "processed_files")


def _assert_table_scanned_only_by_covering_index(plan: list[str], table: str) -> None:
    # SQLite < 3.36 prints "SCAN TABLE <t>", newer versions "SCAN <t>".
    scans = [step for step in plan if re.match(rf"SCAN (TABLE )?{table}\b", step)]
    assert scans, plan
    assert all("COVERING INDEX" in step for step in scans), plan


def test_executed_notebook_prune_scans_a_covering_index(tmp_path):
    """#1090, second table: ``executed_notebooks`` in the same cache file has
    the same layout (``executed_notebook`` BLOB before ``created_at``) and the
    same whole-table window in ``prune_stale_hashes``."""
    db_path = tmp_path / "cache.db"
    with ExecutedNotebookCache(db_path) as cache:
        assert cache.conn is not None
        statements: list[str] = []
        cache.conn.set_trace_callback(statements.append)
        cache.prune_stale_hashes()
        cache.conn.set_trace_callback(None)

        delete_sql = next(s for s in statements if "DELETE FROM executed_notebooks" in s)
        plan = [row[3] for row in cache.conn.execute(f"EXPLAIN QUERY PLAN {delete_sql}")]

    _assert_table_scanned_only_by_covering_index(plan, "executed_notebooks")


def test_executed_notebook_prune_keeps_newest_per_key(tmp_path):
    db_path = tmp_path / "cache.db"
    with ExecutedNotebookCache(db_path) as cache:
        assert cache.conn is not None
        rows = [
            ("a.py", "h0", "de", "python", "2026-01-01 00:00:00"),
            ("a.py", "h1", "de", "python", "2026-01-01 00:01:00"),
            ("a.py", "h2", "en", "python", "2026-01-01 00:00:00"),
            # created_at tie: the later insert wins.
            ("b.py", "t0", "de", "python", "2026-01-01 00:00:00"),
            ("b.py", "t1", "de", "python", "2026-01-01 00:00:00"),
        ]
        for input_file, content_hash, language, prog_lang, ts in rows:
            cache.conn.execute(
                "INSERT INTO executed_notebooks (input_file, content_hash, language, "
                "prog_lang, executed_notebook, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (input_file, content_hash, language, prog_lang, b"{}", ts),
            )
        cache.conn.commit()

        assert cache.prune_stale_hashes() == 2
        left = {r[0] for r in cache.conn.execute("SELECT content_hash FROM executed_notebooks")}

    assert left == {"h1", "h2", "t1"}


def test_prune_indexes_missing_until_both_prunes_ran(tmp_path):
    db_path = tmp_path / "cache.db"
    with ExecutedNotebookCache(db_path) as cache, DatabaseManager(db_path) as db:
        assert db.prune_indexes_missing()
        db.prune_old_versions(retain_count=1)
        # The executed-notebook table exists but its index does not yet.
        assert db.prune_indexes_missing()
        cache.prune_stale_hashes()
        assert not db.prune_indexes_missing()
