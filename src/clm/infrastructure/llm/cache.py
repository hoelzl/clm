"""SQLite-based cache for LLM summaries."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


class SummaryCache:
    """Cache LLM summaries keyed by (content_hash, audience, model, language)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self):
        """Create or migrate the summaries table to include language."""
        # Check if old table exists without language column
        cursor = self._conn.execute("PRAGMA table_info(summaries)")
        columns = {row[1] for row in cursor.fetchall()}

        if not columns:
            # Fresh database — create table with language
            self._conn.execute(
                """CREATE TABLE summaries (
                    content_hash TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    model TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT 'en',
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (content_hash, audience, model, language)
                )"""
            )
            self._conn.commit()
        elif "language" not in columns:
            # Old table without language — migrate
            logger.info("Migrating summary cache to include language column")
            self._conn.execute("ALTER TABLE summaries RENAME TO summaries_old")
            self._conn.execute(
                """CREATE TABLE summaries (
                    content_hash TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    model TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT 'en',
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (content_hash, audience, model, language)
                )"""
            )
            # Copy old data assuming 'en' as the default language
            self._conn.execute(
                """INSERT OR IGNORE INTO summaries
                   (content_hash, audience, model, language, summary, created_at)
                   SELECT content_hash, audience, model, 'en', summary, created_at
                   FROM summaries_old"""
            )
            self._conn.execute("DROP TABLE summaries_old")
            self._conn.commit()

    def get(self, content_hash: str, audience: str, model: str, language: str = "en") -> str | None:
        row = self._conn.execute(
            "SELECT summary FROM summaries "
            "WHERE content_hash=? AND audience=? AND model=? AND language=?",
            (content_hash, audience, model, language),
        ).fetchone()
        return row[0] if row else None

    def put(self, content_hash: str, audience: str, model: str, summary: str, language: str = "en"):
        self._conn.execute(
            """INSERT OR REPLACE INTO summaries
               (content_hash, audience, model, language, summary)
               VALUES (?, ?, ?, ?, ?)""",
            (content_hash, audience, model, language, summary),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
