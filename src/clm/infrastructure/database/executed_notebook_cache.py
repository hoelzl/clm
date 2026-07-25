"""Cache for executed notebooks to reduce redundant evaluation.

This module provides caching of executed Jupyter notebooks (NotebookNode objects
with execution outputs). When Speaker HTML notebooks are processed, the executed
notebook is cached so that Completed HTML can reuse the execution results by
simply filtering out the "notes" cells.

The cache is stored in the same database as processed_files (clm_cache.db) but
in a separate table (executed_notebooks).

Cache key: (input_file, content_hash, language, prog_lang)
- Excludes 'kind' because Speaker and Completed share the same execution
- content_hash ensures cache invalidation when source changes

Payloads are nbformat JSON (see
:mod:`clm.infrastructure.notebook_serialization`). They used to be pickles,
which made every read a deserialization sink for bytes the Worker API had
accepted from the network. Rows written by those older versions carry
``payload_format='pickle'`` and are deleted on first open — see
:meth:`ExecutedNotebookCache._init_table`.
"""

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from clm.infrastructure.database.journal_mode import configure_connection
from clm.infrastructure.notebook_serialization import (
    NotebookSerializationError,
    deserialize_notebook,
    serialize_notebook,
)

if TYPE_CHECKING:
    from nbformat import NotebookNode

logger = logging.getLogger(__name__)

#: Value of the ``payload_format`` column for entries this version writes.
#: Anything else is from a version that stored pickles and is unreadable here.
PAYLOAD_FORMAT = "nbformat-json"


class ExecutedNotebookCache:
    """Manages caching of executed notebooks for reuse across HTML variants.

    Speaker HTML notebooks are executed and cached. Completed HTML can then
    reuse the cached executed notebook by filtering out "notes" cells, avoiding
    redundant notebook execution.

    Usage:
        with ExecutedNotebookCache(db_path) as cache:
            # Check if cached execution exists
            cached_nb = cache.get(input_file, content_hash, language, prog_lang)
            if cached_nb:
                # Use cached execution
                ...
            else:
                # Execute notebook and cache result
                executed_nb = execute_notebook(nb)
                cache.store(input_file, content_hash, language, prog_lang, executed_nb)
    """

    def __init__(self, db_path: Path | str):
        """Initialize the cache manager.

        Args:
            db_path: Path to the SQLite database file (typically clm_cache.db)
        """
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ExecutedNotebookCache":
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        # WAL locally for concurrency; DELETE when the cache lives on a share.
        configure_connection(self.conn, self.db_path)
        self._init_table()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _init_table(self) -> None:
        """Create the executed_notebooks table if it doesn't exist.

        Also migrates a pre-existing table forward: adds the
        ``payload_format`` column (older rows default to ``'pickle'``) and
        deletes every row this version cannot read. The delete is deliberate
        and not recoverable — a pickle payload is exactly what we refuse to
        deserialize, and this is a cache: the entries regenerate on the next
        build.
        """
        assert self.conn is not None, "Connection not initialized"
        cursor = self.conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS executed_notebooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_file TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                language TEXT NOT NULL,
                prog_lang TEXT NOT NULL,
                executed_notebook BLOB NOT NULL,
                payload_format TEXT NOT NULL DEFAULT '{PAYLOAD_FORMAT}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(input_file, content_hash, language, prog_lang)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_executed_notebooks_lookup
            ON executed_notebooks(input_file, content_hash, language, prog_lang)
        """)

        columns = {row[1] for row in cursor.execute("PRAGMA table_info(executed_notebooks)")}
        if "payload_format" not in columns:
            # Pre-existing table from a pickle-era build. Every row in it is a
            # pickle, so the DEFAULT names what is actually there.
            cursor.execute(
                "ALTER TABLE executed_notebooks ADD COLUMN "
                "payload_format TEXT NOT NULL DEFAULT 'pickle'"
            )

        cursor.execute(
            "DELETE FROM executed_notebooks WHERE payload_format != ?",
            (PAYLOAD_FORMAT,),
        )
        if cursor.rowcount > 0:
            logger.info(
                f"Discarded {cursor.rowcount} executed-notebook cache entries in the "
                f"legacy pickle format; they will be re-executed and re-cached."
            )

        assert self.conn is not None  # Already checked above, but reassure mypy
        self.conn.commit()

    def get(
        self,
        input_file: str,
        content_hash: str,
        language: str,
        prog_lang: str,
    ) -> "NotebookNode | None":
        """Retrieve a cached executed notebook.

        Args:
            input_file: Path to the source notebook file
            content_hash: SHA hash of the notebook content
            language: Output language ("de" or "en")
            prog_lang: Programming language ("python", "cpp", etc.)

        Returns:
            The cached NotebookNode with execution outputs, or None if not found
            (or if the stored payload no longer parses, which is reported as a
            miss so a damaged entry cannot fail a build).
        """
        payload = self.get_raw(
            input_file=input_file,
            content_hash=content_hash,
            language=language,
            prog_lang=prog_lang,
        )
        if payload is None:
            logger.debug(
                f"Cache miss for executed notebook: {input_file} ({language}, {prog_lang})"
            )
            return None

        try:
            notebook = deserialize_notebook(payload)
        except NotebookSerializationError as e:
            logger.warning(
                f"Cached executed notebook for {input_file} ({language}, {prog_lang}) "
                f"could not be parsed; treating as a cache miss: {e}"
            )
            return None

        logger.debug(f"Cache hit for executed notebook: {input_file} ({language}, {prog_lang})")
        return notebook

    def get_raw(
        self,
        input_file: str,
        content_hash: str,
        language: str,
        prog_lang: str,
    ) -> bytes | None:
        """Retrieve the stored nbformat JSON bytes for a cached notebook.

        Unlike :meth:`get`, this does not parse the payload. Used by the
        Worker API to ship cache hits to remote workers without round-tripping
        the NotebookNode through nbformat twice.

        Args:
            input_file: Path to the source notebook file
            content_hash: SHA hash of the notebook content
            language: Output language ("de" or "en")
            prog_lang: Programming language ("python", "cpp", etc.)

        Returns:
            The stored nbformat JSON bytes (as written by :meth:`store`), or
            None if not found.
        """
        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return None

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT executed_notebook FROM executed_notebooks
            WHERE input_file = ? AND content_hash = ? AND language = ? AND prog_lang = ?
              AND payload_format = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(input_file), content_hash, language, prog_lang, PAYLOAD_FORMAT),
        )
        row = cursor.fetchone()
        if row:
            return bytes(row[0])
        return None

    def store(
        self,
        input_file: str,
        content_hash: str,
        language: str,
        prog_lang: str,
        executed_notebook: "NotebookNode",
    ) -> None:
        """Store an executed notebook in the cache.

        Uses INSERT OR REPLACE to handle updates atomically.

        Args:
            input_file: Path to the source notebook file
            content_hash: SHA hash of the notebook content
            language: Output language ("de" or "en")
            prog_lang: Programming language ("python", "cpp", etc.)
            executed_notebook: The NotebookNode with execution outputs

        A notebook that cannot be serialized is logged and skipped rather than
        raised: caching is best-effort, and the build has already produced its
        output by the time this is called.
        """
        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return

        try:
            payload = serialize_notebook(executed_notebook)
        except NotebookSerializationError as e:
            logger.warning(
                f"Not caching executed notebook for {input_file} ({language}, {prog_lang}): {e}"
            )
            return

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO executed_notebooks
            (input_file, content_hash, language, prog_lang, executed_notebook, payload_format)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(input_file),
                content_hash,
                language,
                prog_lang,
                payload,
                PAYLOAD_FORMAT,
            ),
        )
        self.conn.commit()
        logger.debug(f"Cached executed notebook: {input_file} ({language}, {prog_lang})")

    def clear(self, input_file: str | None = None) -> int:
        """Clear cached entries.

        Args:
            input_file: If specified, only clear entries for this file.
                       If None, clear all entries.

        Returns:
            Number of entries deleted.
        """
        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return 0

        cursor = self.conn.cursor()
        if input_file:
            cursor.execute(
                "DELETE FROM executed_notebooks WHERE input_file = ?",
                (str(input_file),),
            )
        else:
            cursor.execute("DELETE FROM executed_notebooks")
        deleted = cursor.rowcount
        self.conn.commit()
        logger.debug(f"Cleared {deleted} cached executed notebooks")
        return deleted

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary with cache statistics:
            - total_entries: Total number of cached entries
            - by_language: Count by language
            - by_prog_lang: Count by programming language
        """
        if not self.conn:
            return {"total_entries": 0, "by_language": {}, "by_prog_lang": {}}

        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM executed_notebooks")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT language, COUNT(*) FROM executed_notebooks GROUP BY language")
        by_language = dict(cursor.fetchall())

        cursor.execute("SELECT prog_lang, COUNT(*) FROM executed_notebooks GROUP BY prog_lang")
        by_prog_lang = dict(cursor.fetchall())

        return {
            "total_entries": total,
            "by_language": by_language,
            "by_prog_lang": by_prog_lang,
        }

    def prune_old_entries(self, days: int = 30) -> int:
        """Remove old cached executed notebooks.

        Args:
            days: Number of days to keep entries

        Returns:
            Number of entries deleted.
        """
        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return 0

        cursor = self.conn.cursor()
        cursor.execute(
            """
            DELETE FROM executed_notebooks
            WHERE created_at < datetime('now', '-' || ? || ' days')
            """,
            (days,),
        )
        deleted = cursor.rowcount
        self.conn.commit()

        if deleted > 0:
            logger.info(f"Pruned {deleted} old executed notebook cache entries")

        return deleted

    def prune_stale_hashes(self, valid_hashes: set[str] | None = None) -> int:
        """Remove cached entries whose content_hash no longer matches any current file.

        This is useful for cleaning up entries after source files have been modified.
        If valid_hashes is not provided, this method will keep only entries with
        the most recent content_hash per input_file.

        Args:
            valid_hashes: Set of valid content hashes to keep. If None, keeps only
                         the most recent entry per (input_file, language, prog_lang).

        Returns:
            Number of entries deleted.
        """
        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return 0

        cursor = self.conn.cursor()

        if valid_hashes is not None:
            # Delete entries not in the valid set
            if not valid_hashes:
                # No valid hashes means clear everything
                cursor.execute("DELETE FROM executed_notebooks")
            else:
                placeholders = ",".join("?" * len(valid_hashes))
                cursor.execute(
                    f"DELETE FROM executed_notebooks WHERE content_hash NOT IN ({placeholders})",
                    list(valid_hashes),
                )
        else:
            # Keep only the most recent entry per (input_file, language, prog_lang)
            cursor.execute(
                """
                DELETE FROM executed_notebooks
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY input_file, language, prog_lang
                            ORDER BY created_at DESC
                        ) as rn
                        FROM executed_notebooks
                    )
                    WHERE rn = 1
                )
                """
            )

        deleted = cursor.rowcount
        self.conn.commit()

        if deleted > 0:
            logger.info(f"Pruned {deleted} stale executed notebook cache entries")

        return deleted

    def remove_entries_for_missing_files(self, dry_run: bool = False) -> int:
        """Delete cached entries where the source input_file no longer exists on disk.

        Args:
            dry_run: If True, count what would be deleted without deleting.

        Returns:
            Number of entries deleted (or that would be deleted in dry_run mode).
        """
        import os

        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return 0

        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT input_file FROM executed_notebooks")
        all_paths = [row[0] for row in cursor.fetchall()]
        missing_paths = [p for p in all_paths if not os.path.exists(p)]

        if not missing_paths:
            return 0

        placeholders = ",".join("?" * len(missing_paths))

        if dry_run:
            cursor.execute(
                f"SELECT COUNT(*) FROM executed_notebooks WHERE input_file IN ({placeholders})",
                missing_paths,
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

        cursor.execute(
            f"DELETE FROM executed_notebooks WHERE input_file IN ({placeholders})",
            missing_paths,
        )
        deleted = cursor.rowcount
        self.conn.commit()
        if deleted > 0:
            logger.info(
                f"Deleted {deleted} executed notebook cache entries "
                f"for {len(missing_paths)} missing files"
            )
        return deleted

    def vacuum(self) -> None:
        """Compact the executed notebooks table.

        Note: This actually vacuums the entire database since the executed_notebooks
        table shares the clm_cache.db file with processed_files.
        """
        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return

        self.conn.execute("VACUUM")
        logger.debug("Vacuumed executed notebook cache")
