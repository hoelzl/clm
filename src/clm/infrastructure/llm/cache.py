"""SQLite-based cache for LLM summaries."""

import functools
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Filename of the shared LLM SQLite cache (summaries, titles, translations,
# sync watermarks, …) inside the resolved cache directory. The CLI commands
# under ``clm slides`` each keep a local copy of this literal; this is the
# canonical home.
CACHE_DB_NAME = "clm-llm.sqlite"


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


class TranslationCache:
    """Cache translated cell bodies for ``clm slides translate`` (Issue #232).

    Keyed by ``(content_hash, prompt_version, source_lang, target_lang, role)``:
    the source body's hash plus everything that changes the *output* — the
    translator's prompt version (model-folded by the caller, so two models never
    share an entry), the direction, and the role (markdown vs the
    identifier-preserving code prompt). Bootstrapping a whole deck is the same
    per-cell translation a later sync would do, so a shared cache makes re-runs
    and tests cheap. Only **successful** translations are stored.

    Shares the consuming repo's ``clm-llm.sqlite`` cache file with the other LLM
    caches but lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(translations)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE translations (
                    content_hash   TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    source_lang    TEXT NOT NULL,
                    target_lang    TEXT NOT NULL,
                    role           TEXT NOT NULL,
                    translation    TEXT NOT NULL,
                    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (content_hash, prompt_version, source_lang, target_lang, role)
                )"""
            )
            self._conn.commit()

    def get(
        self,
        content_hash: str,
        prompt_version: str,
        source_lang: str,
        target_lang: str,
        role: str,
    ) -> str | None:
        row = self._conn.execute(
            "SELECT translation FROM translations WHERE content_hash=? AND prompt_version=? "
            "AND source_lang=? AND target_lang=? AND role=?",
            (content_hash, prompt_version, source_lang, target_lang, role),
        ).fetchone()
        return row[0] if row else None

    def put(
        self,
        content_hash: str,
        prompt_version: str,
        source_lang: str,
        target_lang: str,
        role: str,
        translation: str,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO translations
               (content_hash, prompt_version, source_lang, target_lang, role, translation)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (content_hash, prompt_version, source_lang, target_lang, role, translation),
        )
        self._conn.commit()

    def invalidate_prompt_version(self, prompt_version: str) -> int:
        """Delete entries whose prompt version no longer matches."""
        cursor = self._conn.execute(
            "DELETE FROM translations WHERE prompt_version!=?",
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


class SyncCache:
    """Cache LLM-proposed cross-language sync edits.

    Keyed by ``(de_hash, en_hash, prompt_version)`` per Phase 7 of the
    slide-format-redesign. A row represents *"the LLM was asked to
    propose an edit for this specific pair of DE/EN cell content, and
    here is what it said"*. Re-runs against the same pair short-circuit
    to the cached proposal so re-invocations of ``clm slides sync`` do
    not respend on the LLM.

    ``direction`` is ``"de->en"`` when the proposal updates the EN cell
    from the DE cell's content, or ``"en->de"`` for the mirror. Stored
    in the value (not the key) because the algorithm decides direction
    once per pair before firing the LLM.

    ``proposal`` is the LLM's verbatim suggestion (typically the full
    replacement body of the target cell). For the *in-sync* case
    (algorithm determined no edit needed without firing the LLM), the
    cache is not consulted — there is nothing to memoize.

    Shares the same SQLite file as :class:`SummaryCache`,
    :class:`TitleSuggestionCache`, and :class:`CoverageCache` (the
    consuming repo's ``clm-llm.sqlite``) but lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(sync_proposals)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE sync_proposals (
                    de_hash         TEXT NOT NULL,
                    en_hash         TEXT NOT NULL,
                    prompt_version  TEXT NOT NULL,
                    direction       TEXT NOT NULL,
                    proposal        TEXT NOT NULL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (de_hash, en_hash, prompt_version)
                )"""
            )
            self._conn.commit()

    def get(
        self,
        de_hash: str,
        en_hash: str,
        prompt_version: str,
    ) -> tuple[str, str] | None:
        """Return ``(direction, proposal)`` or ``None`` on a miss."""
        row = self._conn.execute(
            "SELECT direction, proposal FROM sync_proposals "
            "WHERE de_hash=? AND en_hash=? AND prompt_version=?",
            (de_hash, en_hash, prompt_version),
        ).fetchone()
        if row is None:
            return None
        return (row[0], row[1])

    def put(
        self,
        de_hash: str,
        en_hash: str,
        prompt_version: str,
        direction: str,
        proposal: str,
    ) -> None:
        if direction not in ("de->en", "en->de"):
            raise ValueError(f"direction must be 'de->en' or 'en->de', got {direction!r}")
        self._conn.execute(
            """INSERT OR REPLACE INTO sync_proposals
               (de_hash, en_hash, prompt_version, direction, proposal)
               VALUES (?, ?, ?, ?, ?)""",
            (de_hash, en_hash, prompt_version, direction, proposal),
        )
        self._conn.commit()

    def invalidate_prompt_version(self, prompt_version: str) -> int:
        """Delete entries whose prompt version no longer matches."""
        cursor = self._conn.execute(
            "DELETE FROM sync_proposals WHERE prompt_version!=?",
            (prompt_version,),
        )
        self._conn.commit()
        return cursor.rowcount

    def iter_entries(self) -> list[tuple[str, str, str, str, str, str]]:
        """Return every cached entry for a future ``sync --dump``.

        Tuples are ``(de_hash, en_hash, prompt_version, direction,
        proposal, created_at)`` ordered by creation time so the most
        recent proposals surface first.
        """
        rows = self._conn.execute(
            "SELECT de_hash, en_hash, prompt_version, direction, "
            "proposal, created_at "
            "FROM sync_proposals ORDER BY created_at DESC, de_hash"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]

    def close(self) -> None:
        self._conn.close()


class SyncAlignmentCache:
    """Cache bounded-LLM (Opus) *alignment* recoveries for the sync engine.

    Issue #190 §10 / Phase 5. When the deterministic id-migration (§9) cannot
    decide which ``slide_id`` belongs on which cell — a simultaneous function
    rename, a true N:1 split/merge, ambiguous ties (two ``def my_fun``, many bare
    imports) — the sync engine may escalate (only under ``--llm-recover``) to a
    **body-free, alignment-only** model call that returns an ``id ↔ cell`` *map*,
    never free-form edits. That map is validated and applied deterministically.

    A row memoizes *"the recoverer was asked to align this base region against
    this current region, and here is the (validated) map it returned"*, so a
    re-run over the same region short-circuits to the cached map and never
    re-spends on the LLM. Keyed by ``(base_region_hash, current_region_hash,
    prompt_version)`` — the two region fingerprints fully determine the question,
    and the prompt version invalidates the cache when the prompt/model contract
    changes (cf. :class:`SyncCache`, :class:`TitleSuggestionCache`).

    ``alignment`` is the recoverer's verbatim JSON map (a current-cell → base
    ``slide_id`` / ``"new"`` mapping). Only *successfully validated* maps are
    cached: a safe-aborted recovery records nothing, so a fixed prompt re-derives
    it on the next run.

    Shares the same SQLite file as the other LLM caches; lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(sync_alignments)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE sync_alignments (
                    base_region_hash    TEXT NOT NULL,
                    current_region_hash TEXT NOT NULL,
                    prompt_version      TEXT NOT NULL,
                    alignment           TEXT NOT NULL,
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (base_region_hash, current_region_hash, prompt_version)
                )"""
            )
            self._conn.commit()

    def get(
        self,
        base_region_hash: str,
        current_region_hash: str,
        prompt_version: str,
    ) -> str | None:
        """Return the cached alignment JSON for the region pair, or ``None``."""
        row = self._conn.execute(
            "SELECT alignment FROM sync_alignments "
            "WHERE base_region_hash=? AND current_region_hash=? AND prompt_version=?",
            (base_region_hash, current_region_hash, prompt_version),
        ).fetchone()
        return row[0] if row else None

    def put(
        self,
        base_region_hash: str,
        current_region_hash: str,
        prompt_version: str,
        alignment: str,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO sync_alignments
               (base_region_hash, current_region_hash, prompt_version, alignment)
               VALUES (?, ?, ?, ?)""",
            (base_region_hash, current_region_hash, prompt_version, alignment),
        )
        self._conn.commit()

    def invalidate_prompt_version(self, prompt_version: str) -> int:
        """Delete entries whose prompt version no longer matches."""
        cursor = self._conn.execute(
            "DELETE FROM sync_alignments WHERE prompt_version!=?",
            (prompt_version,),
        )
        self._conn.commit()
        return cursor.rowcount

    def iter_entries(self) -> list[tuple[str, str, str, str, str]]:
        """Return every cached entry, most-recent first.

        Tuples are ``(base_region_hash, current_region_hash, prompt_version,
        alignment, created_at)``.
        """
        rows = self._conn.execute(
            "SELECT base_region_hash, current_region_hash, prompt_version, "
            "alignment, created_at "
            "FROM sync_alignments ORDER BY created_at DESC, base_region_hash"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def close(self) -> None:
        self._conn.close()


class SyncCorrespondenceCache:
    """Cache cold-start *correspondence* verdicts for the sync engine.

    Issue #216 Phase 3 (design §12). When ``clm slides sync`` bootstraps a
    never-id'd split pair, it asks a cheap LLM whether the two structurally-aligned
    halves actually correspond (are translations) before minting a shared
    ``slide_id`` onto each pair. A row memoizes *"the verifier was asked about this
    set of aligned heading/snippet pairs, and here are the (validated) yes/no
    verdicts"*, so a re-run over the same deck short-circuits to the cached verdicts
    and never re-spends on the LLM.

    Keyed by ``(pairs_hash, prompt_version)`` — the pair fingerprint
    (:func:`clm.slides.sync_recover.correspondence_fingerprint`) fully determines the
    question, and the prompt version (which folds in the model) invalidates the
    cache when the prompt/model contract changes. Only *successfully validated*
    verdict maps are cached: a safe-aborted verification records nothing, so a fixed
    prompt re-derives it next run. Shares the SQLite file; lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(sync_correspondences)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE sync_correspondences (
                    pairs_hash      TEXT NOT NULL,
                    prompt_version  TEXT NOT NULL,
                    verdicts        TEXT NOT NULL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (pairs_hash, prompt_version)
                )"""
            )
            self._conn.commit()

    def get(self, pairs_hash: str, prompt_version: str) -> str | None:
        """Return the cached verdict JSON for the pair set, or ``None``."""
        row = self._conn.execute(
            "SELECT verdicts FROM sync_correspondences WHERE pairs_hash=? AND prompt_version=?",
            (pairs_hash, prompt_version),
        ).fetchone()
        return row[0] if row else None

    def put(self, pairs_hash: str, prompt_version: str, verdicts: str) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO sync_correspondences
               (pairs_hash, prompt_version, verdicts) VALUES (?, ?, ?)""",
            (pairs_hash, prompt_version, verdicts),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class SyncSnapshotCache:
    """Per-(file, slide_id, role) snapshot of the last accepted sync state.

    The :class:`SyncCache` memoizes the LLM's proposal for a given
    ``(de_hash, en_hash)`` pair — answering *"what would the LLM say
    about this pair?"*. Snapshots answer a different question:
    *"what state of this pair did the author last confirm as in sync?"*

    Written by the ``clm slides sync --interactive`` walker (Phase 7 v2)
    whenever the user applies or edits a proposal: the post-write
    ``(de_hash, en_hash)`` is the new "last-known-synced" tuple for
    that ``(de_path, en_path, slide_id, role)`` slot. A future
    direction-auto-detection pass (item #4 of the v2 follow-up list)
    can compare the on-disk hashes against this row to decide which
    side drifted.

    Shares the same SQLite file as the other LLM caches; lives in its
    own table so the proposal cache (content-addressed) and the
    snapshot store (location-addressed) stay semantically distinct.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(sync_snapshots)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE sync_snapshots (
                    de_path     TEXT NOT NULL,
                    en_path     TEXT NOT NULL,
                    slide_id    TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    de_hash     TEXT NOT NULL,
                    en_hash     TEXT NOT NULL,
                    direction   TEXT NOT NULL,
                    accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (de_path, en_path, slide_id, role)
                )"""
            )
            self._conn.commit()

    def get(
        self,
        de_path: str,
        en_path: str,
        slide_id: str,
        role: str,
    ) -> tuple[str, str, str] | None:
        """Return ``(de_hash, en_hash, direction)`` or ``None`` on miss."""
        row = self._conn.execute(
            "SELECT de_hash, en_hash, direction FROM sync_snapshots "
            "WHERE de_path=? AND en_path=? AND slide_id=? AND role=?",
            (de_path, en_path, slide_id, role),
        ).fetchone()
        if row is None:
            return None
        return (row[0], row[1], row[2])

    def put(
        self,
        *,
        de_path: str,
        en_path: str,
        slide_id: str,
        role: str,
        de_hash: str,
        en_hash: str,
        direction: str,
    ) -> None:
        if direction not in ("de->en", "en->de"):
            raise ValueError(f"direction must be 'de->en' or 'en->de', got {direction!r}")
        self._conn.execute(
            """INSERT OR REPLACE INTO sync_snapshots
               (de_path, en_path, slide_id, role, de_hash, en_hash, direction)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (de_path, en_path, slide_id, role, de_hash, en_hash, direction),
        )
        self._conn.commit()

    def iter_entries(self) -> list[tuple[str, str, str, str, str, str, str, str]]:
        """Return every snapshot row, most-recently-accepted first.

        Tuples are ``(de_path, en_path, slide_id, role, de_hash,
        en_hash, direction, accepted_at)``.
        """
        rows = self._conn.execute(
            "SELECT de_path, en_path, slide_id, role, de_hash, en_hash, "
            "direction, accepted_at "
            "FROM sync_snapshots ORDER BY accepted_at DESC, slide_id, role"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in rows]

    def close(self) -> None:
        self._conn.close()


def _serialize_tags(tags: frozenset[str]) -> str:
    """Canonical wire form of a cell's tag set for the watermark ``tags`` column.

    Sorted and comma-joined; tag names are identifiers so a comma never appears
    inside one. The empty set serializes to ``""`` — a *known* empty set, stored
    distinctly from a NULL (undeterminable) column. Issue #198.
    """
    return ",".join(sorted(tags))


def _deserialize_tags(raw: str) -> frozenset[str]:
    """Inverse of :func:`_serialize_tags`; ``""`` -> the empty set (not ``{""}``)."""
    return frozenset(raw.split(",")) if raw else frozenset()


# The canonicalization version of the stored ``content_hash`` values. Bump this
# whenever ``clm.slides.sync_writeback.cell_content_hash`` / ``normalize_for_hash``
# changes the canonical form it hashes — a stored snapshot at an older version is
# *unreadable* (its hashes can't be compared against current ones), so the cache
# treats a stale-version pair as absent (``has_pair``/``get_deck`` below). That
# self-heals the migration: the pair cold-starts off git HEAD and the next apply
# re-records it at the current version, with no manual ``watermark clear`` and no
# false "everything edited" drift. Issue #429 introduced reflow-insensitive
# markdown hashing → version 2.
WATERMARK_HASH_VERSION = 2


class SyncWatermarkCache:
    """Ordered, per-language structural watermark of the last synced deck state.

    This is the baseline against which the Issue #166 change classifier
    (:mod:`clm.slides.sync_plan`) detects adds / edits / moves / removes when
    one half of a split deck is edited. Where :class:`SyncSnapshotCache` stores
    a *per-cell* ``(de_hash, en_hash)`` pair keyed by ``slide_id`` (for
    direction inference), the watermark stores the **whole deck** as an ordered
    list of cells *per language*, so it can:

    - represent **id-less** cells (``slide_id`` is nullable), and
    - capture cell **order** (for move / reorder detection).

    A pair's two decks are written together, only on a successful sync apply, so
    the watermark advances with the agreed state and is immune to the author's
    git-commit cadence. Cold-start (no watermark) falls back to git HEAD.

    Shares the same SQLite file as the other LLM caches; lives in its own table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._migrate()

    def _migrate(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(sync_watermarks)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            self._conn.execute(
                """CREATE TABLE sync_watermarks (
                    de_path      TEXT NOT NULL,
                    en_path      TEXT NOT NULL,
                    lang         TEXT NOT NULL,
                    position     INTEGER NOT NULL,
                    slide_id     TEXT,
                    role         TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    construct    TEXT,
                    tags         TEXT,
                    anchor       TEXT,
                    synced_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (de_path, en_path, lang, position)
                )"""
            )
            self._conn.commit()
        else:
            # Additive migrations: nullable columns, so existing rows backfill to
            # NULL harmlessly. ``construct`` (Issue #190 §5) is the content-anchor
            # slug; ``tags`` (Issue #198) is the cell's tag set, recorded so a
            # later run can detect a tag-only edit (invisible to the content hash)
            # and mirror it across the split halves. ``anchor`` (Issue #403 Phase B)
            # is the positional voiceover anchor of a narrative row (``id:``/``fp:``/
            # ``tm:`` token), recorded only for narrative cells so a later run can
            # detect an *edit* to a multiple-per-slide narrative — sparse, NULL on
            # every non-narrative row.
            if "construct" not in columns:
                self._conn.execute("ALTER TABLE sync_watermarks ADD COLUMN construct TEXT")
            if "tags" not in columns:
                self._conn.execute("ALTER TABLE sync_watermarks ADD COLUMN tags TEXT")
            if "anchor" not in columns:
                self._conn.execute("ALTER TABLE sync_watermarks ADD COLUMN anchor TEXT")
            self._conn.commit()
        # Pair-level metadata, one row per (de_path, en_path): the repo HEAD commit
        # at the time the watermark was recorded. Lets a later run detect that the
        # watermark predates committed edits and name the exact `--baseline <ref>`
        # to diff against (the stale-watermark hint).
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS sync_watermark_meta (
                de_path       TEXT NOT NULL,
                en_path       TEXT NOT NULL,
                synced_commit TEXT,
                synced_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                hash_version  INTEGER,
                PRIMARY KEY (de_path, en_path)
            )"""
        )
        # ``hash_version`` (Issue #429): the canonicalization version of the stored
        # content hashes. Additive; pre-#429 rows backfill to NULL, which reads as
        # "stale version" so the pair cold-starts and re-records at the current
        # version. Existing meta tables get the column via ALTER.
        meta_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(sync_watermark_meta)")}
        if "hash_version" not in meta_cols:
            self._conn.execute("ALTER TABLE sync_watermark_meta ADD COLUMN hash_version INTEGER")
        self._conn.commit()

    def _key(self, de_path: str, en_path: str) -> tuple[str, str]:
        """Canonicalize the ``(de_path, en_path)`` watermark key (issue #435).

        Routes the absolute key paths through :func:`to_main_worktree_path` so a
        path resolved inside a linked git worktree is keyed by its main-checkout
        equivalent — every read and write therefore agrees on one key regardless
        of which worktree the command runs from. A no-op in the main checkout, and
        idempotent (re-keying an already-canonical key returns it unchanged), so
        internal methods that re-enter another keyed method stay correct.
        """
        return (
            str(to_main_worktree_path(Path(de_path))),
            str(to_main_worktree_path(Path(en_path))),
        )

    def get_deck(
        self,
        de_path: str,
        en_path: str,
        lang: str,
    ) -> list[tuple[int, str | None, str, str, str | None]]:
        """Return the watermark for one deck, ordered by position.

        Tuples are ``(position, slide_id, role, content_hash, construct)``;
        ``slide_id`` and ``construct`` are ``None`` for id-less / non-code rows.
        An empty list means the deck has no watermark (cold start for this pair).

        A pair recorded at a different ``hash_version`` reads as empty (Issue #429):
        its stored hashes use an older canonical form and cannot be compared, so the
        pair cold-starts and is re-recorded at the current version on the next apply.
        """
        de_path, en_path = self._key(de_path, en_path)
        if not self._version_current(de_path, en_path):
            return []
        rows = self._conn.execute(
            "SELECT position, slide_id, role, content_hash, construct FROM sync_watermarks "
            "WHERE de_path=? AND en_path=? AND lang=? ORDER BY position",
            (de_path, en_path, lang),
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def put_deck(
        self,
        *,
        de_path: str,
        en_path: str,
        lang: str,
        cells: list[tuple[int, str | None, str, str, str | None]],
        tags: dict[int, frozenset[str]] | None = None,
        anchors: dict[int, str] | None = None,
    ) -> None:
        """Replace the watermark for one deck atomically.

        ``cells`` is an ordered list of ``(position, slide_id, role,
        content_hash, construct)``. ``lang`` is ``de`` / ``en`` for localized
        decks or ``shared`` for the single-entity language-neutral partition
        (Issue #190 §5). The whole ``(de_path, en_path, lang)`` slice is deleted
        and rewritten in a single transaction so a deck's watermark is never
        observed half-updated.

        ``tags`` (Issue #198) optionally maps a cell's ``position`` to its tag
        set, stored in the additive ``tags`` column so a later run can detect a
        tag-only edit. A position absent from ``tags`` (or ``tags=None``) stores
        NULL — "tag set unknown" — which the classifier reads as "tag direction
        undeterminable" and skips, so a pre-#198 watermark degrades gracefully.

        ``de-header`` / ``en-header`` (Issue #269) record each half's j2 deck-header
        cells (excluded from every other partition) so a one-sided header edit —
        which sync never auto-translates — can be detected and surfaced rather than
        silently reported as "consistent".

        ``anchors`` (Issue #403 Phase B) optionally maps a *narrative* cell's
        ``position`` to its positional voiceover anchor token (``id:``/``fp:``/``tm:``),
        stored in the additive ``anchor`` column so a later run can key a narrative
        by ``(owning_slide_id, role, anchor)`` and detect an edit to one of several
        narratives under a single slide. Sparse — a position absent from ``anchors``
        (or ``anchors=None``) stores NULL ("not a narrative / unknown").
        """
        if lang not in ("de", "en", "shared", "de-header", "en-header"):
            raise ValueError(
                f"lang must be 'de', 'en', 'shared', 'de-header', or 'en-header', got {lang!r}"
            )
        de_path, en_path = self._key(de_path, en_path)
        tag_for = tags or {}
        anchor_for = anchors or {}
        with self._conn:  # single transaction (BEGIN/COMMIT or ROLLBACK)
            self._conn.execute(
                "DELETE FROM sync_watermarks WHERE de_path=? AND en_path=? AND lang=?",
                (de_path, en_path, lang),
            )
            self._conn.executemany(
                "INSERT INTO sync_watermarks "
                "(de_path, en_path, lang, position, slide_id, role, content_hash, "
                "construct, tags, anchor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        de_path,
                        en_path,
                        lang,
                        position,
                        slide_id,
                        role,
                        content_hash,
                        construct,
                        _serialize_tags(tag_for[position]) if position in tag_for else None,
                        anchor_for.get(position),
                    )
                    for (position, slide_id, role, content_hash, construct) in cells
                ],
            )
            # Stamp the canonicalization version of these hashes (Issue #429). Done
            # on every partition write so any record path (full or partial advance)
            # leaves the pair at the current version; a read at a different version
            # treats the pair as absent (see ``has_pair`` / ``get_deck``).
            self._conn.execute(
                "INSERT INTO sync_watermark_meta (de_path, en_path, hash_version) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(de_path, en_path) DO UPDATE SET hash_version=excluded.hash_version",
                (de_path, en_path, WATERMARK_HASH_VERSION),
            )

    def get_deck_tags(self, de_path: str, en_path: str, lang: str) -> dict[int, frozenset[str]]:
        """Return ``{position: tag_set}`` for the rows that recorded a tag set.

        Only rows whose ``tags`` column is non-NULL appear, so the caller can
        distinguish a *known* empty tag set (present, ``frozenset()``) from an
        *undeterminable* one (absent — a pre-#198 watermark row). Issue #198.
        """
        de_path, en_path = self._key(de_path, en_path)
        rows = self._conn.execute(
            "SELECT position, tags FROM sync_watermarks "
            "WHERE de_path=? AND en_path=? AND lang=? AND tags IS NOT NULL ORDER BY position",
            (de_path, en_path, lang),
        ).fetchall()
        return {r[0]: _deserialize_tags(r[1]) for r in rows}

    def get_deck_anchors(self, de_path: str, en_path: str, lang: str) -> dict[int, str]:
        """Return ``{position: anchor}`` for the narrative rows that recorded one.

        Sparse: only rows whose ``anchor`` column is non-NULL appear — the
        narrative cells (Issue #403 Phase B). A position absent from the map is
        either a non-narrative cell or a pre-Phase-B watermark row, which the
        narrative classifier reads as "anchor undeterminable" and skips, so an old
        watermark degrades gracefully (no false edit).
        """
        de_path, en_path = self._key(de_path, en_path)
        rows = self._conn.execute(
            "SELECT position, anchor FROM sync_watermarks "
            "WHERE de_path=? AND en_path=? AND lang=? AND anchor IS NOT NULL ORDER BY position",
            (de_path, en_path, lang),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def has_pair(self, de_path: str, en_path: str) -> bool:
        """Return True when a *current-version* watermark exists for the pair.

        A pair stored at a stale ``hash_version`` (or none) reads as absent
        (Issue #429) so the caller cold-starts off git HEAD rather than diffing
        against hashes in an incomparable canonical form.
        """
        de_path, en_path = self._key(de_path, en_path)
        if not self._version_current(de_path, en_path):
            return False
        row = self._conn.execute(
            "SELECT 1 FROM sync_watermarks WHERE de_path=? AND en_path=? LIMIT 1",
            (de_path, en_path),
        ).fetchone()
        return row is not None

    def get_hash_version(self, de_path: str, en_path: str) -> int | None:
        """The canonicalization version the pair's hashes were recorded at, or None."""
        de_path, en_path = self._key(de_path, en_path)
        row = self._conn.execute(
            "SELECT hash_version FROM sync_watermark_meta WHERE de_path=? AND en_path=?",
            (de_path, en_path),
        ).fetchone()
        return row[0] if row else None

    def _version_current(self, de_path: str, en_path: str) -> bool:
        """Whether the pair's stored hashes match the current canonicalization."""
        return self.get_hash_version(de_path, en_path) == WATERMARK_HASH_VERSION

    def set_synced_commit(self, de_path: str, en_path: str, commit: str | None) -> None:
        """Record the repo HEAD commit the pair was last synced at (pair-level)."""
        de_path, en_path = self._key(de_path, en_path)
        with self._conn:
            self._conn.execute(
                "INSERT INTO sync_watermark_meta (de_path, en_path, synced_commit) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(de_path, en_path) DO UPDATE SET "
                "synced_commit=excluded.synced_commit, synced_at=CURRENT_TIMESTAMP",
                (de_path, en_path, commit),
            )

    def get_synced_commit(self, de_path: str, en_path: str) -> str | None:
        """Return the commit recorded by :meth:`set_synced_commit`, or ``None``."""
        de_path, en_path = self._key(de_path, en_path)
        row = self._conn.execute(
            "SELECT synced_commit FROM sync_watermark_meta WHERE de_path=? AND en_path=?",
            (de_path, en_path),
        ).fetchone()
        return row[0] if row else None

    def clear_pair(self, de_path: str, en_path: str) -> int:
        """Delete all watermark rows + metadata for the pair; return rows removed."""
        de_path, en_path = self._key(de_path, en_path)
        cursor = self._conn.execute(
            "DELETE FROM sync_watermarks WHERE de_path=? AND en_path=?",
            (de_path, en_path),
        )
        self._conn.execute(
            "DELETE FROM sync_watermark_meta WHERE de_path=? AND en_path=?",
            (de_path, en_path),
        )
        self._conn.commit()
        return cursor.rowcount

    def iter_entries(
        self,
    ) -> list[tuple[str, str, str, int, str | None, str, str, str | None, str]]:
        """Return every watermark row for a future ``sync --dump``.

        Tuples are ``(de_path, en_path, lang, position, slide_id, role,
        content_hash, construct, synced_at)`` ordered by pair, language, and
        position.
        """
        rows = self._conn.execute(
            "SELECT de_path, en_path, lang, position, slide_id, role, "
            "content_hash, construct, synced_at "
            "FROM sync_watermarks ORDER BY de_path, en_path, lang, position"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows]

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class CacheDirResolution:
    """Where the LLM cache directory resolves to, and *why*.

    The provenance fields exist so a read-only diagnostic (``clm config
    locate``) can explain the resolution without re-deriving it — and so the
    git-worktree anchoring is observable. ``path`` is NOT created here; call
    :func:`resolve_cache_dir` (or ``_ensure_dir``) when you need the directory
    to exist.
    """

    path: Path
    source: str  # "cli" | "env" | "pyproject" | "default"
    configured_value: str | None = None  # raw [tool.clm] cache_dir value, if used
    pyproject_path: Path | None = None
    relative_anchor: Path | None = None  # dir a relative configured_value was joined to
    main_worktree_root: Path | None = None  # set iff resolved from a LINKED git worktree


def describe_cache_dir(
    *,
    cli_override: Path | None = None,
    repo_root: Path | None = None,
) -> CacheDirResolution:
    """Resolve the LLM cache directory and report its provenance (pure).

    Lookup order:

    1. ``cli_override`` (the ``--cache-dir`` flag value)
    2. ``CLM_CACHE_DIR`` environment variable
    3. ``tool.clm.cache_dir`` in ``<repo_root>/pyproject.toml``
    4. ``<repo_root>/.clm-cache/`` (default, gitignored)

    ``repo_root`` defaults to the **discovered project root** —
    :func:`clm.infrastructure.utils.path_utils.find_project_root` walks up from
    the current working directory to the nearest ``pyproject.toml`` /
    ``.clm/config.toml`` / ``.git`` (like ``git`` / ``uv`` / ``ruff``), so the
    cache resolves to the same place no matter which subdirectory the command
    was invoked from (issue #477). Without the walk-up, running from a topic
    subdir treated the subdir as the root: it missed ``[tool.clm] cache_dir``
    and created a stray ``<subdir>/.clm-cache``. This function has **no side
    effects** — it does not create the directory.

    Git-worktree anchoring: a *relative* ``[tool.clm] cache_dir`` (e.g.
    ``../shared-cache``) is normally joined to ``repo_root``. But in a git
    **worktree**, ``repo_root`` is the per-worktree checkout — so the relative
    value would resolve *under* the worktree instead of beside the main
    checkout, silently giving each worktree its own cache (the cause of
    sync watermarks "disappearing" in a worktree). When ``repo_root`` is not
    given explicitly (the real-CLI path, resolving from cwd) and cwd is inside a
    linked worktree, the relative value is anchored to the **main worktree
    root** instead, so every worktree shares the one cache. Passing an explicit
    ``repo_root`` opts out of this detection and anchors to that root verbatim.
    """
    import os

    from clm.infrastructure.utils.path_utils import find_project_root

    if cli_override is not None:
        return CacheDirResolution(path=Path(cli_override), source="cli")

    env = os.environ.get("CLM_CACHE_DIR")
    if env:
        return CacheDirResolution(path=Path(env), source="env")

    # Discover the project root by walking up (issue #477); an explicit
    # ``repo_root`` opts out and anchors to that root verbatim (library callers /
    # tests). The worktree re-anchoring of a relative value below then runs with
    # the correct root (the worktree checkout root, not a subdir).
    root = repo_root or find_project_root()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        configured = _read_pyproject_cache_dir(pyproject)
        if configured:
            path = Path(configured)
            if path.is_absolute():
                return CacheDirResolution(
                    path=path,
                    source="pyproject",
                    configured_value=configured,
                    pyproject_path=pyproject,
                )
            # Anchor a relative value to the main worktree root when resolving
            # from cwd inside a linked worktree (see docstring).
            main_root = _main_worktree_root(root) if repo_root is None else None
            anchor = main_root or root
            return CacheDirResolution(
                path=anchor / path,
                source="pyproject",
                configured_value=configured,
                pyproject_path=pyproject,
                relative_anchor=anchor,
                main_worktree_root=main_root,
            )

    return CacheDirResolution(path=root / ".clm-cache", source="default")


def resolve_cache_dir(
    *,
    cli_override: Path | None = None,
    repo_root: Path | None = None,
) -> Path:
    """Resolve the LLM cache directory and ensure it exists.

    Thin wrapper over :func:`describe_cache_dir` (which holds the resolution
    logic, including git-worktree anchoring of a relative ``[tool.clm]
    cache_dir``). The returned path is created if it does not exist.
    """
    return _ensure_dir(describe_cache_dir(cli_override=cli_override, repo_root=repo_root).path)


def _main_worktree_root(start: Path) -> Path | None:
    """The main worktree root if ``start`` is inside a LINKED git worktree.

    Returns ``None`` for the main worktree, outside any repo, or when git is
    unavailable — callers then fall back to ``start``. The main worktree's
    ``--git-common-dir`` is the repo's own ``.git`` (so the parent equals
    ``start``'s root and there is nothing to re-anchor); a linked worktree's
    common dir points at the *main* checkout's ``.git``, whose parent is the
    shared root we want.
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    out = completed.stdout.strip()
    if not out:
        return None
    common = Path(out)
    if not common.is_absolute():
        # In the MAIN worktree, --git-common-dir is the relative ".git"; its
        # parent is `start`'s root, so there is no separate main root to use.
        return None
    common = common.resolve()
    if common.name != ".git":
        return None
    return common.parent


def _git_show_toplevel(start: Path) -> Path | None:
    """The working-tree root of the (possibly linked) worktree containing ``start``.

    ``git rev-parse --show-toplevel`` run with ``cwd=start``. Returns ``None`` when
    git is unavailable or ``start`` is outside a repo. Used together with
    :func:`_main_worktree_root` to remap a worktree path to its main-checkout twin.
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    out = completed.stdout.strip()
    if not out:
        return None
    return Path(out).resolve()


@functools.cache
def _worktree_remap_for_dir(directory: str) -> tuple[str, str] | None:
    """``(worktree_toplevel, main_root)`` if ``directory`` is in a LINKED worktree.

    ``None`` for the main worktree, outside a repo, or when git is unavailable.
    Memoized: the answer is constant per worktree, so keying a whole batch of
    pairs makes at most one git invocation per unique directory.
    """
    start = Path(directory)
    main_root = _main_worktree_root(start)
    if main_root is None:
        return None
    top = _git_show_toplevel(start)
    if top is None:
        return None
    return (str(top), str(main_root))


def to_main_worktree_path(p: Path) -> Path:
    """Remap a path under a linked git worktree to its main-checkout equivalent.

    The sync watermark is keyed by the absolute ``(de_path, en_path)`` strings,
    but :meth:`Path.resolve` from a linked worktree yields the *worktree* path —
    which never matches the rows recorded from the main checkout, so every pair
    misses its watermark and silently cold-starts off git HEAD (issue #435).
    Canonicalizing the **key** to the main-checkout path lets the worktree and the
    main checkout share both the cache file (#374) and the keys inside it, and
    keeps writes on the single canonical key (no orphaned worktree-path rows).

    Returns ``p`` unchanged when it is not under a linked worktree, is outside a
    repo, or git is unavailable — so the main checkout and non-git callers are
    unaffected, and the function is idempotent (a main-checkout path remaps to
    itself). Only the watermark **key** is canonicalized; file reads and the
    content-keyed, worktree-portable sync ledger keep the real on-disk path.
    """
    resolved = p.resolve()
    directory = resolved if resolved.is_dir() else resolved.parent
    remap = _worktree_remap_for_dir(str(directory))
    if remap is None:
        return p
    wt_top, main_root = Path(remap[0]), Path(remap[1])
    try:
        rel = resolved.relative_to(wt_top)
    except ValueError:
        return p
    return main_root / rel


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
