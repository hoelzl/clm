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


class TitleSuggestionCache:
    """Cache LLM-suggested slide titles keyed by ``(content_hash, prompt_version, lang)``.

    Used by ``clm slides assign-ids --llm-suggest`` to avoid re-querying
    the local LLM for cells whose content has not changed. Shares the
    same SQLite file as :class:`SummaryCache` (the consuming repo's
    ``clm-llm.sqlite`` cache; see §2.5 of the slide-format-redesign
    handover) but lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(title_suggestions)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE title_suggestions (
                    content_hash    TEXT NOT NULL,
                    prompt_version  TEXT NOT NULL,
                    lang            TEXT NOT NULL,
                    suggested_title TEXT NOT NULL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (content_hash, prompt_version, lang)
                )"""
            )
            self._conn.commit()

    def get(self, content_hash: str, prompt_version: str, lang: str = "en") -> str | None:
        row = self._conn.execute(
            "SELECT suggested_title FROM title_suggestions "
            "WHERE content_hash=? AND prompt_version=? AND lang=?",
            (content_hash, prompt_version, lang),
        ).fetchone()
        return row[0] if row else None

    def put(
        self,
        content_hash: str,
        prompt_version: str,
        suggested_title: str,
        lang: str = "en",
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO title_suggestions
               (content_hash, prompt_version, lang, suggested_title)
               VALUES (?, ?, ?, ?)""",
            (content_hash, prompt_version, lang, suggested_title),
        )
        self._conn.commit()

    def invalidate_prompt_version(self, prompt_version: str) -> int:
        """Delete entries whose prompt version no longer matches."""
        cursor = self._conn.execute(
            "DELETE FROM title_suggestions WHERE prompt_version!=?",
            (prompt_version,),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()


class CoverageCache:
    """Cache LLM voiceover-coverage verdicts.

    Keyed by ``(slide_hash, voiceover_hash, prompt_version, lang)`` per
    §2.5 of the slide-format-redesign handover. The verdict is a short
    string (``"covered"`` or ``"gaps"``) and ``gap_details`` is a JSON
    blob produced by the judge listing the per-bullet results.

    Shares the same SQLite file as :class:`SummaryCache` and
    :class:`TitleSuggestionCache` (the consuming repo's
    ``clm-llm.sqlite``) but lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(coverage)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE coverage (
                    slide_hash      TEXT NOT NULL,
                    voiceover_hash  TEXT NOT NULL,
                    prompt_version  TEXT NOT NULL,
                    lang            TEXT NOT NULL,
                    verdict         TEXT NOT NULL,
                    gap_details     TEXT,
                    checked_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (slide_hash, voiceover_hash, prompt_version, lang)
                )"""
            )
            self._conn.commit()

    def get(
        self,
        slide_hash: str,
        voiceover_hash: str,
        prompt_version: str,
        lang: str,
    ) -> tuple[str, str | None] | None:
        """Return ``(verdict, gap_details_json)`` or ``None`` on a miss."""
        row = self._conn.execute(
            "SELECT verdict, gap_details FROM coverage "
            "WHERE slide_hash=? AND voiceover_hash=? AND prompt_version=? AND lang=?",
            (slide_hash, voiceover_hash, prompt_version, lang),
        ).fetchone()
        if row is None:
            return None
        return (row[0], row[1])

    def put(
        self,
        slide_hash: str,
        voiceover_hash: str,
        prompt_version: str,
        lang: str,
        verdict: str,
        gap_details: str | None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO coverage
               (slide_hash, voiceover_hash, prompt_version, lang, verdict, gap_details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (slide_hash, voiceover_hash, prompt_version, lang, verdict, gap_details),
        )
        self._conn.commit()

    def invalidate_prompt_version(self, prompt_version: str) -> int:
        """Delete entries whose prompt version no longer matches."""
        cursor = self._conn.execute(
            "DELETE FROM coverage WHERE prompt_version!=?",
            (prompt_version,),
        )
        self._conn.commit()
        return cursor.rowcount

    def iter_entries(self) -> list[tuple[str, str, str, str, str, str | None, str]]:
        """Return every cached entry for ``coverage --dump``.

        Tuples are ``(slide_hash, voiceover_hash, prompt_version, lang,
        verdict, gap_details, checked_at)`` ordered by check time so the
        most recent verdicts surface first.
        """
        rows = self._conn.execute(
            "SELECT slide_hash, voiceover_hash, prompt_version, lang, "
            "verdict, gap_details, checked_at "
            "FROM coverage ORDER BY checked_at DESC, slide_hash"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]

    def close(self) -> None:
        self._conn.close()


def resolve_cache_dir(
    *,
    cli_override: Path | None = None,
    repo_root: Path | None = None,
) -> Path:
    """Resolve the LLM cache directory per §2.5 of the redesign handover.

    Lookup order:

    1. ``cli_override`` (the ``--cache-dir`` flag value)
    2. ``CLM_CACHE_DIR`` environment variable
    3. ``tool.clm.cache_dir`` in ``<repo_root>/pyproject.toml``
    4. ``<repo_root>/.clm-cache/`` (default, gitignored)

    The returned path is created if it does not exist. ``repo_root``
    defaults to the current working directory.
    """
    import os

    if cli_override is not None:
        return _ensure_dir(Path(cli_override))

    env = os.environ.get("CLM_CACHE_DIR")
    if env:
        return _ensure_dir(Path(env))

    root = repo_root or Path.cwd()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        configured = _read_pyproject_cache_dir(pyproject)
        if configured:
            path = Path(configured)
            if not path.is_absolute():
                path = root / path
            return _ensure_dir(path)

    return _ensure_dir(root / ".clm-cache")


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_pyproject_cache_dir(pyproject: Path) -> str | None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python <3.11 not supported
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tool = data.get("tool", {})
    clm = tool.get("clm", {})
    value = clm.get("cache_dir")
    if isinstance(value, str) and value:
        return value
    return None
