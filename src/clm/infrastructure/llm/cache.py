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
            # and mirror it across the split halves.
            if "construct" not in columns:
                self._conn.execute("ALTER TABLE sync_watermarks ADD COLUMN construct TEXT")
            if "tags" not in columns:
                self._conn.execute("ALTER TABLE sync_watermarks ADD COLUMN tags TEXT")
            self._conn.commit()

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
        """
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
        """
        if lang not in ("de", "en", "shared", "de-header", "en-header"):
            raise ValueError(
                f"lang must be 'de', 'en', 'shared', 'de-header', or 'en-header', got {lang!r}"
            )
        tag_for = tags or {}
        with self._conn:  # single transaction (BEGIN/COMMIT or ROLLBACK)
            self._conn.execute(
                "DELETE FROM sync_watermarks WHERE de_path=? AND en_path=? AND lang=?",
                (de_path, en_path, lang),
            )
            self._conn.executemany(
                "INSERT INTO sync_watermarks "
                "(de_path, en_path, lang, position, slide_id, role, content_hash, construct, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    )
                    for (position, slide_id, role, content_hash, construct) in cells
                ],
            )

    def get_deck_tags(self, de_path: str, en_path: str, lang: str) -> dict[int, frozenset[str]]:
        """Return ``{position: tag_set}`` for the rows that recorded a tag set.

        Only rows whose ``tags`` column is non-NULL appear, so the caller can
        distinguish a *known* empty tag set (present, ``frozenset()``) from an
        *undeterminable* one (absent — a pre-#198 watermark row). Issue #198.
        """
        rows = self._conn.execute(
            "SELECT position, tags FROM sync_watermarks "
            "WHERE de_path=? AND en_path=? AND lang=? AND tags IS NOT NULL ORDER BY position",
            (de_path, en_path, lang),
        ).fetchall()
        return {r[0]: _deserialize_tags(r[1]) for r in rows}

    def has_pair(self, de_path: str, en_path: str) -> bool:
        """Return True when any watermark row exists for the pair."""
        row = self._conn.execute(
            "SELECT 1 FROM sync_watermarks WHERE de_path=? AND en_path=? LIMIT 1",
            (de_path, en_path),
        ).fetchone()
        return row is not None

    def clear_pair(self, de_path: str, en_path: str) -> int:
        """Delete all watermark rows for the pair; return rows removed."""
        cursor = self._conn.execute(
            "DELETE FROM sync_watermarks WHERE de_path=? AND en_path=?",
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
