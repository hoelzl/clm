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
"""

import logging
import pickle
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from nbformat import NotebookNode

logger = logging.getLogger(__name__)


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
        # Enable WAL mode for better concurrency
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._init_table()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _init_table(self) -> None:
        """Create the executed_notebooks table if it doesn't exist."""
        assert self.conn is not None, "Connection not initialized"
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS executed_notebooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_file TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                language TEXT NOT NULL,
                prog_lang TEXT NOT NULL,
                executed_notebook BLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(input_file, content_hash, language, prog_lang)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_executed_notebooks_lookup
            ON executed_notebooks(input_file, content_hash, language, prog_lang)
        """)
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
            The cached NotebookNode with execution outputs, or None if not found.
        """
        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return None

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT executed_notebook FROM executed_notebooks
            WHERE input_file = ? AND content_hash = ? AND language = ? AND prog_lang = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(input_file), content_hash, language, prog_lang),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(f"Cache hit for executed notebook: {input_file} ({language}, {prog_lang})")
            return cast("NotebookNode", pickle.loads(row[0]))
        else:
            logger.debug(
                f"Cache miss for executed notebook: {input_file} ({language}, {prog_lang})"
            )
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
        """
        if not self.conn:
            logger.warning("ExecutedNotebookCache not initialized (use with statement)")
            return

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO executed_notebooks
            (input_file, content_hash, language, prog_lang, executed_notebook)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(input_file),
                content_hash,
                language,
                prog_lang,
                pickle.dumps(executed_notebook),
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
