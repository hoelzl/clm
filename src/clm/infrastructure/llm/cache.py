"""SQLite-based cache for LLM summaries."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


class SummaryCache:
    """Cache LLM summaries keyed by (content_hash, audience, model, language, style)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self):
        """Create or migrate the summaries table."""
        cursor = self._conn.execute("PRAGMA table_info(summaries)")
        columns = {row[1] for row in cursor.fetchall()}

        if not columns:
            # Fresh database
            self._create_current_table()
        elif "language" not in columns:
            # Very old table without language — rebuild with both language and style
            logger.info("Migrating summary cache to include language and style columns")
            self._conn.execute("ALTER TABLE summaries RENAME TO summaries_old")
            self._create_current_table()
            self._conn.execute(
                """INSERT OR IGNORE INTO summaries
                   (content_hash, audience, model, language, style, summary, created_at)
                   SELECT content_hash, audience, model, 'en', 'prose', summary, created_at
                   FROM summaries_old"""
            )
            self._conn.execute("DROP TABLE summaries_old")
            self._conn.commit()
        elif "style" not in columns:
            # Has language but no style — add style column
            logger.info("Migrating summary cache to include style column")
            self._conn.execute("ALTER TABLE summaries RENAME TO summaries_old")
            self._create_current_table()
            self._conn.execute(
                """INSERT OR IGNORE INTO summaries
                   (content_hash, audience, model, language, style, summary, created_at)
                   SELECT content_hash, audience, model, language, 'prose', summary, created_at
                   FROM summaries_old"""
            )
            self._conn.execute("DROP TABLE summaries_old")
            self._conn.commit()

    def _create_current_table(self):
        self._conn.execute(
            """CREATE TABLE summaries (
                content_hash TEXT NOT NULL,
                audience TEXT NOT NULL,
                model TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'en',
                style TEXT NOT NULL DEFAULT 'prose',
                summary TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (content_hash, audience, model, language, style)
            )"""
        )
        self._conn.commit()

    def get(
        self,
        content_hash: str,
        audience: str,
        model: str,
        language: str = "en",
        style: str = "prose",
    ) -> str | None:
        row = self._conn.execute(
            "SELECT summary FROM summaries "
            "WHERE content_hash=? AND audience=? AND model=? AND language=? AND style=?",
            (content_hash, audience, model, language, style),
        ).fetchone()
        return row[0] if row else None

    def put(
        self,
        content_hash: str,
        audience: str,
        model: str,
        summary: str,
        language: str = "en",
        style: str = "prose",
    ):
        self._conn.execute(
            """INSERT OR REPLACE INTO summaries
               (content_hash, audience, model, language, style, summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (content_hash, audience, model, language, style, summary),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
