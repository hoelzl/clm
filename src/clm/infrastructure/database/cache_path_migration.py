"""Rewrite the path-keyed rows in ``clm_cache.db`` when a course file or
directory is renamed/moved — so a rename preserves cached work instead of
cold-starting it.

Why this can exist at all
=========================

The build caches key their *payloads* on a **content hash that contains no
path** (``NotebookPayload.content_hash`` / ``execution_cache_hash``): the input
path, output path, and module/topic/slide names are absent, and sibling files
are folded in by their **topic-relative** name. The path appears only as a
**separate lookup column** (``processed_files.file_path`` /
``processing_issues.file_path`` / ``executed_notebooks.input_file``).

Therefore, after a pure rename/renumber the stored ``Result`` and executed
``NotebookNode`` bytes are *still exactly what the build would produce* — only
the lookup key points at the old path. Rewriting the key is observationally
identical to a fresh cache hit; **no re-execution is needed.** See
``docs/developer-guide/caching.md`` and
``docs/claude/design/course-restructure-move-rename.md``.

Scope
=====

This module owns the ``clm_cache.db`` half — the three **input-path**-keyed
tables, which is all a topic *renumber* needs (output paths, and hence
``clm_jobs.db``'s ``results_cache``, do not move when only a directory's ordinal
prefix changes). The ``results_cache.output_file`` rewrite in ``clm_jobs.db`` is
the analogous, deliberately-separate operation for moves that change output
locations; it is not implemented here.

Overlapping mappings and collision handling
===========================================

The rewrite is **two-phase and order-independent** (issue #587): every
mapping's rows are first parked on a unique sentinel path, then landed on
their destinations. A naive sequential ``UPDATE old → new`` would let a later
mapping re-migrate an earlier mapping's result whenever the set overlaps
(``new₁ == old₂`` — a chain, or two paths swapping names in a batch
restructure), silently piling rows onto the wrong path and, worse, letting the
UNIQUE-collision check delete rows that were themselves about to move away.
Parking first means each row moves exactly once and collisions are judged
against the *final* state.

Only ``executed_notebooks`` carries a ``UNIQUE(input_file, content_hash,
language, prog_lang)`` constraint. After parking, a landing collides only when
a row that was **never mapped away** already sits at ``new`` with the same
``content_hash`` — i.e. an equivalent executed notebook (same content ⇒ same
cached payload). Such a migrated duplicate is dropped (counted as
``collisions_dropped``); the surviving destination row serves the identical
hit. ``processed_files`` / ``processing_issues`` have no UNIQUE constraint, so
their landing is a plain ``UPDATE``; any transient duplicate versions are
trimmed by the ordinary newest-N build-end prune.

``processing_issues`` rows additionally get the ``file_path`` **embedded in
their ``issue_json`` payload** re-pointed (issue #588) — the column alone is
not enough, because cached errors/warnings are re-reported from that JSON and
would otherwise name the old, no-longer-existing path. The pickled ``Result``
in ``processed_files`` also embeds its original ``input_file``, but that copy
is inert: the replay path consumes only the freshly computed payload paths
plus ``result_bytes()``, so it is deliberately left untouched.

The whole rewrite runs in one transaction (``BEGIN IMMEDIATE`` … ``COMMIT``);
``dry_run`` performs the identical work and ``ROLLBACK``s, so the reported
counts always match what a real run would do. Run it while no build is active.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from attrs import frozen

logger = logging.getLogger(__name__)

__all__ = [
    "INPUT_PATH_COLUMNS",
    "CacheMigrationReport",
    "PathMapping",
    "TableMigration",
    "migrate_cache_paths",
    "migrate_dir_rename",
    "plan_dir_rename",
]

#: The ``clm_cache.db`` tables that key rows on an absolute **input** path, and
#: the column each stores it in.
INPUT_PATH_COLUMNS: dict[str, str] = {
    "processed_files": "file_path",
    "processing_issues": "file_path",
    "executed_notebooks": "input_file",
}

#: Content dimensions that, together with the path column, form a table's UNIQUE
#: constraint. Only ``executed_notebooks`` has one; a rewrite that would land two
#: rows on the same ``(path, *dims)`` drops the migrated duplicate (same
#: ``content_hash`` ⇒ interchangeable payload).
_UNIQUE_CONTENT_DIMS: dict[str, tuple[str, ...]] = {
    "executed_notebooks": ("content_hash", "language", "prog_lang"),
}


@frozen
class PathMapping:
    """One ``old`` absolute input path to rewrite to ``new``.

    ``old`` must be the path **exactly as stored** in the cache DB (that is what
    the ``WHERE`` clause matches). :func:`plan_dir_rename` produces these from
    the DB's own distinct values, so the match is always exact.
    """

    old: str
    new: str


@frozen
class TableMigration:
    """Per-table outcome of a migration."""

    table: str
    rows_rewritten: int
    collisions_dropped: int


@frozen
class CacheMigrationReport:
    """Aggregate outcome across every migrated table."""

    db_path: str
    dry_run: bool
    tables: tuple[TableMigration, ...]

    @property
    def rows_rewritten(self) -> int:
        return sum(t.rows_rewritten for t in self.tables)

    @property
    def collisions_dropped(self) -> int:
        return sum(t.collisions_dropped for t in self.tables)

    @property
    def changed(self) -> bool:
        return self.rows_rewritten > 0 or self.collisions_dropped > 0

    def summary(self) -> str:
        verb = "would rewrite" if self.dry_run else "rewrote"
        parts = [
            f"{t.table}: {t.rows_rewritten}"
            + (f" (-{t.collisions_dropped} dup)" if t.collisions_dropped else "")
            for t in self.tables
            if t.rows_rewritten or t.collisions_dropped
        ]
        detail = ", ".join(parts) if parts else "nothing to migrate"
        return f"cache path migration {verb} {self.rows_rewritten} row(s): {detail}"


# ---------------------------------------------------------------------------
# Path arithmetic (pure, filesystem-free — the old directory is already gone)
# ---------------------------------------------------------------------------


def _rewrite_under(stored: str, old_dir: str, new_dir: str) -> str | None:
    """Return ``stored`` re-rooted from ``old_dir`` to ``new_dir``, or ``None``
    if ``stored`` does not lie under ``old_dir``.

    Comparison is separator- and (on Windows) case-insensitive via
    :func:`os.path.normcase`/:func:`os.path.normpath`, but the returned path
    keeps the original remainder's casing and is emitted with native
    separators — exactly the form the next build will store.
    """
    stored_np = os.path.normpath(stored)
    old_np = os.path.normpath(old_dir)
    stored_cmp = os.path.normcase(stored_np)
    old_cmp = os.path.normcase(old_np)

    if stored_cmp == old_cmp:
        return str(Path(new_dir))
    prefix = old_cmp + os.sep
    if not stored_cmp.startswith(prefix):
        return None
    # Case differs only in the shared prefix, which has equal length in the
    # case-preserving normpath form, so this cut recovers the original-cased
    # remainder.
    remainder = stored_np[len(old_np) + 1 :]
    return str(Path(new_dir) / remainder)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _distinct_paths(conn: sqlite3.Connection, table: str, column: str) -> list[str]:
    # table/column come only from the module-internal INPUT_PATH_COLUMNS map,
    # never user input, so the interpolation is safe.
    rows = conn.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall()  # noqa: S608
    return [r[0] for r in rows if r[0] is not None]


def _connect(db_path: Path) -> sqlite3.Connection:
    # isolation_level=None → autocommit, so our explicit BEGIN/COMMIT/ROLLBACK
    # are the only transaction control (and dry-run can ROLLBACK cleanly).
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_dir_rename(
    cache_db_path: str | Path,
    old_dir: str | Path,
    new_dir: str | Path,
) -> list[PathMapping]:
    """Compute the ``old → new`` mappings for renaming directory ``old_dir`` to
    ``new_dir``, by scanning every input path stored in the cache DB.

    Mappings are drawn from the DB's own distinct values, so a later
    :func:`migrate_cache_paths` matches each row exactly. Paths not under
    ``old_dir`` are ignored. Returns ``[]`` if the DB does not exist.
    """
    cache_db_path = Path(cache_db_path)
    if not cache_db_path.exists():
        return []
    old_s, new_s = str(old_dir), str(new_dir)

    seen: dict[str, str] = {}
    conn = _connect(cache_db_path)
    try:
        for table, column in INPUT_PATH_COLUMNS.items():
            if not _table_exists(conn, table):
                continue
            for stored in _distinct_paths(conn, table, column):
                if stored in seen:
                    continue
                rewritten = _rewrite_under(stored, old_s, new_s)
                if rewritten is not None and rewritten != stored:
                    seen[stored] = rewritten
    finally:
        conn.close()

    return [PathMapping(old=old, new=new) for old, new in seen.items()]


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


#: Sentinel paths park rows between the two rewrite phases. NUL bytes cannot
#: appear in a real filesystem path, so a sentinel can never alias stored data.
_SENTINEL_PREFIX = "\x00clm-cache-path-migration:"


def _sentinel(index: int) -> str:
    return f"{_SENTINEL_PREFIX}{index}\x00"


def _rewrite_issue_json(issue_json: str, old: str, new: str) -> str | None:
    """Return ``issue_json`` with its embedded ``file_path`` re-pointed from
    ``old`` to ``new``, or ``None`` if there is nothing to rewrite.

    ``BuildError`` / ``BuildWarning`` are stored as ``json.dumps(asdict(...))``
    with a top-level ``file_path`` (issue #588). Free-text ``message`` content
    is left alone — rewriting paths inside prose is not reliably possible —
    and malformed JSON is left untouched rather than destroyed.
    """
    try:
        data = json.loads(issue_json)
    except ValueError:
        return None
    field = data.get("file_path")
    if not isinstance(field, str):
        return None
    rewritten = _rewrite_under(field, old, new)
    if rewritten is None or rewritten == field:
        return None
    data["file_path"] = rewritten
    return json.dumps(data)


def _rewrite_parked_issue_payloads(
    conn: sqlite3.Connection, sentinel: str, mapping: PathMapping
) -> None:
    """Re-point the ``file_path`` embedded in parked ``processing_issues``
    rows' ``issue_json`` — the column rewrite alone would leave cached
    errors/warnings reporting the old, no-longer-existing path on replay
    (issue #588)."""
    rows = conn.execute(
        "SELECT id, issue_json FROM processing_issues WHERE file_path = ?",
        (sentinel,),
    ).fetchall()
    for row_id, issue_json in rows:
        updated = _rewrite_issue_json(issue_json, mapping.old, mapping.new)
        if updated is not None:
            conn.execute(
                "UPDATE processing_issues SET issue_json = ? WHERE id = ?",
                (updated, row_id),
            )


def _migrate_table(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    mappings: Sequence[PathMapping],
) -> TableMigration:
    dims = _UNIQUE_CONTENT_DIMS.get(table)
    # Phase 1: park every mapping's rows on a unique sentinel so no later
    # UPDATE can observe (and re-migrate) an earlier mapping's result. This
    # makes the rewrite order-independent and correct for overlapping mapping
    # sets — a chain (a→b, b→c) or a swap (a→b, b→a) moves every row exactly
    # once (issue #587).
    parked: list[tuple[str, PathMapping]] = []
    for index, m in enumerate(mappings):
        sentinel = _sentinel(index)
        moved = conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {column} = ?",  # noqa: S608
            (sentinel, m.old),
        ).rowcount
        if moved:
            parked.append((sentinel, m))
    # Phase 2: land the parked rows. Rows now sitting at a destination path
    # are exactly the rows that were never mapped away, so the UNIQUE-collision
    # check judges against the final state instead of a mid-rewrite one.
    rewritten = 0
    collisions = 0
    for sentinel, m in parked:
        if dims:
            # Drop parked rows that would violate UNIQUE against a row already
            # sitting at the new path (same content_hash ⇒ equivalent payload).
            dim_match = " AND ".join(f"t2.{d} = {table}.{d}" for d in dims)
            del_sql = (
                f"DELETE FROM {table} WHERE {column} = ? AND EXISTS ("  # noqa: S608
                f"SELECT 1 FROM {table} AS t2 WHERE t2.{column} = ? AND {dim_match})"
            )
            collisions += conn.execute(del_sql, (sentinel, m.new)).rowcount
        if table == "processing_issues":
            _rewrite_parked_issue_payloads(conn, sentinel, m)
        upd = conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {column} = ?",  # noqa: S608
            (m.new, sentinel),
        )
        rewritten += upd.rowcount
    return TableMigration(table=table, rows_rewritten=rewritten, collisions_dropped=collisions)


def migrate_cache_paths(
    cache_db_path: str | Path,
    mappings: Iterable[PathMapping],
    *,
    dry_run: bool = False,
) -> CacheMigrationReport:
    """Rewrite ``old → new`` input paths across the three ``clm_cache.db`` tables.

    Overlapping mapping sets (one mapping's ``new`` equal to another's ``old`` —
    chains, swaps) are safe and order-independent: rows are parked on sentinel
    paths first, so each row moves exactly once (issue #587). Self-mappings
    (``old == new``) are ignored. If the DB does not exist or there is nothing
    to rewrite, returns an empty report without creating the file. ``dry_run``
    performs the identical work then rolls back, so its counts equal a real
    run's. The whole rewrite is a single transaction.
    """
    cache_db_path = Path(cache_db_path)
    mapping_list = [m for m in mappings if m.old != m.new]
    if not cache_db_path.exists() or not mapping_list:
        return CacheMigrationReport(db_path=str(cache_db_path), dry_run=dry_run, tables=())

    conn = _connect(cache_db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            tables = tuple(
                _migrate_table(conn, table, column, mapping_list)
                for table, column in INPUT_PATH_COLUMNS.items()
                if _table_exists(conn, table)
            )
            if dry_run:
                conn.execute("ROLLBACK")
            else:
                conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    report = CacheMigrationReport(db_path=str(cache_db_path), dry_run=dry_run, tables=tables)
    if report.changed:
        logger.info(report.summary())
    return report


def migrate_dir_rename(
    cache_db_path: str | Path,
    old_dir: str | Path,
    new_dir: str | Path,
    *,
    dry_run: bool = False,
) -> CacheMigrationReport:
    """Convenience: :func:`plan_dir_rename` then :func:`migrate_cache_paths`."""
    mappings = plan_dir_rename(cache_db_path, old_dir, new_dir)
    return migrate_cache_paths(cache_db_path, mappings, dry_run=dry_run)
