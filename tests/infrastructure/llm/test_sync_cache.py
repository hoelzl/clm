"""Tests for :class:`clm.infrastructure.llm.cache.SyncCache`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import (
    SyncAlignmentCache,
    SyncCache,
    SyncSnapshotCache,
    SyncWatermarkCache,
)


@pytest.fixture
def cache(tmp_path: Path):
    c = SyncCache(tmp_path / "clm-llm.sqlite")
    try:
        yield c
    finally:
        c.close()


class TestSyncCache:
    def test_miss(self, cache):
        assert cache.get("de-hash", "en-hash", "v1") is None

    def test_round_trip(self, cache):
        cache.put("dh", "eh", "v1", "de->en", "EN proposal text")
        assert cache.get("dh", "eh", "v1") == ("de->en", "EN proposal text")

    def test_overwrite_same_key(self, cache):
        cache.put("dh", "eh", "v1", "de->en", "first proposal")
        cache.put("dh", "eh", "v1", "en->de", "second proposal")
        assert cache.get("dh", "eh", "v1") == ("en->de", "second proposal")

    def test_de_hash_in_key(self, cache):
        cache.put("dh1", "eh", "v1", "de->en", "proposal-1")
        cache.put("dh2", "eh", "v1", "de->en", "proposal-2")
        assert cache.get("dh1", "eh", "v1") == ("de->en", "proposal-1")
        assert cache.get("dh2", "eh", "v1") == ("de->en", "proposal-2")

    def test_en_hash_in_key(self, cache):
        cache.put("dh", "eh1", "v1", "de->en", "proposal-1")
        cache.put("dh", "eh2", "v1", "de->en", "proposal-2")
        assert cache.get("dh", "eh1", "v1") == ("de->en", "proposal-1")
        assert cache.get("dh", "eh2", "v1") == ("de->en", "proposal-2")

    def test_prompt_version_in_key(self, cache):
        cache.put("dh", "eh", "v1", "de->en", "v1-proposal")
        cache.put("dh", "eh", "v2", "de->en", "v2-proposal")
        assert cache.get("dh", "eh", "v1") == ("de->en", "v1-proposal")
        assert cache.get("dh", "eh", "v2") == ("de->en", "v2-proposal")

    def test_invalid_direction_raises(self, cache):
        with pytest.raises(ValueError, match="direction must be"):
            cache.put("dh", "eh", "v1", "sideways", "proposal")

    def test_invalidate_prompt_version(self, cache):
        cache.put("dh1", "eh", "v1", "de->en", "p1")
        cache.put("dh2", "eh", "v1", "de->en", "p2")
        cache.put("dh3", "eh", "v2", "de->en", "p3")
        removed = cache.invalidate_prompt_version("v2")
        assert removed == 2
        assert cache.get("dh1", "eh", "v1") is None
        assert cache.get("dh3", "eh", "v2") == ("de->en", "p3")

    def test_iter_entries_empty(self, cache):
        assert cache.iter_entries() == []

    def test_iter_entries_returns_all_rows(self, cache):
        cache.put("dh1", "eh1", "v1", "de->en", "p1")
        cache.put("dh2", "eh2", "v1", "en->de", "p2")

        rows = cache.iter_entries()
        assert len(rows) == 2
        # Tuple shape: (de_hash, en_hash, prompt_version, direction,
        # proposal, created_at)
        keys = {(row[0], row[3]) for row in rows}
        assert keys == {("dh1", "de->en"), ("dh2", "en->de")}

    def test_survives_close_and_reopen(self, tmp_path: Path):
        path = tmp_path / "clm-llm.sqlite"
        first = SyncCache(path)
        first.put("dh", "eh", "v1", "de->en", "kept across reopen")
        first.close()

        second = SyncCache(path)
        try:
            assert second.get("dh", "eh", "v1") == ("de->en", "kept across reopen")
        finally:
            second.close()

    def test_coexists_with_other_caches(self, tmp_path: Path):
        from clm.infrastructure.llm.cache import CoverageCache, TitleSuggestionCache

        path = tmp_path / "clm-llm.sqlite"
        sync = SyncCache(path)
        cov = CoverageCache(path)
        tit = TitleSuggestionCache(path)
        try:
            sync.put("dh", "eh", "v1", "de->en", "sync-proposal")
            cov.put("sh", "vh", "v1", "en", "covered", None)
            tit.put("ch", "v1", "Some Title", "en")

            assert sync.get("dh", "eh", "v1") == ("de->en", "sync-proposal")
            assert cov.get("sh", "vh", "v1", "en") == ("covered", None)
            assert tit.get("ch", "v1", "en") == "Some Title"
        finally:
            tit.close()
            cov.close()
            sync.close()


# ---------------------------------------------------------------------------
# SyncSnapshotCache (Phase 7 v2)
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshots(tmp_path: Path):
    c = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
    try:
        yield c
    finally:
        c.close()


class TestSyncSnapshotCache:
    def test_miss(self, snapshots):
        assert snapshots.get("a.de.py", "a.en.py", "intro", "slide") is None

    def test_round_trip(self, snapshots):
        snapshots.put(
            de_path="a.de.py",
            en_path="a.en.py",
            slide_id="intro",
            role="slide",
            de_hash="dh",
            en_hash="eh",
            direction="de->en",
        )
        assert snapshots.get("a.de.py", "a.en.py", "intro", "slide") == (
            "dh",
            "eh",
            "de->en",
        )

    def test_overwrite_same_key(self, snapshots):
        snapshots.put(
            de_path="a.de.py",
            en_path="a.en.py",
            slide_id="intro",
            role="slide",
            de_hash="dh1",
            en_hash="eh1",
            direction="de->en",
        )
        snapshots.put(
            de_path="a.de.py",
            en_path="a.en.py",
            slide_id="intro",
            role="slide",
            de_hash="dh2",
            en_hash="eh2",
            direction="en->de",
        )
        assert snapshots.get("a.de.py", "a.en.py", "intro", "slide") == (
            "dh2",
            "eh2",
            "en->de",
        )

    def test_role_in_key(self, snapshots):
        snapshots.put(
            de_path="a.de.py",
            en_path="a.en.py",
            slide_id="intro",
            role="slide",
            de_hash="d_slide",
            en_hash="e_slide",
            direction="de->en",
        )
        snapshots.put(
            de_path="a.de.py",
            en_path="a.en.py",
            slide_id="intro",
            role="voiceover",
            de_hash="d_vo",
            en_hash="e_vo",
            direction="de->en",
        )
        assert snapshots.get("a.de.py", "a.en.py", "intro", "slide")[0] == "d_slide"
        assert snapshots.get("a.de.py", "a.en.py", "intro", "voiceover")[0] == "d_vo"

    def test_invalid_direction_raises(self, snapshots):
        with pytest.raises(ValueError, match="direction must be"):
            snapshots.put(
                de_path="a.de.py",
                en_path="a.en.py",
                slide_id="intro",
                role="slide",
                de_hash="dh",
                en_hash="eh",
                direction="sideways",
            )

    def test_iter_entries(self, snapshots):
        snapshots.put(
            de_path="a.de.py",
            en_path="a.en.py",
            slide_id="intro",
            role="slide",
            de_hash="dh",
            en_hash="eh",
            direction="de->en",
        )
        rows = snapshots.iter_entries()
        assert len(rows) == 1
        assert rows[0][:4] == ("a.de.py", "a.en.py", "intro", "slide")


# ---------------------------------------------------------------------------
# SyncWatermarkCache (Issue #166, Phase 1)
# ---------------------------------------------------------------------------


@pytest.fixture
def watermarks(tmp_path: Path):
    c = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        yield c
    finally:
        c.close()


class TestSyncWatermarkCache:
    def test_empty_deck_is_cold(self, watermarks):
        assert watermarks.get_deck("a.de.py", "a.en.py", "de") == []
        assert watermarks.has_pair("a.de.py", "a.en.py") is False

    def test_put_and_get_deck_ordered(self, watermarks):
        cells = [
            (0, "intro", "slide", "h0", None),
            (1, "intro", "voiceover", "h1", None),
            (2, "topic", "slide", "h2", None),
        ]
        watermarks.put_deck(de_path="a.de.py", en_path="a.en.py", lang="de", cells=cells)
        assert watermarks.get_deck("a.de.py", "a.en.py", "de") == cells
        assert watermarks.has_pair("a.de.py", "a.en.py") is True

    def test_nullable_slide_id(self, watermarks):
        cells = [(0, None, "slide", "h0", None), (1, "topic", "slide", "h1", None)]
        watermarks.put_deck(de_path="a.de.py", en_path="a.en.py", lang="en", cells=cells)
        got = watermarks.get_deck("a.de.py", "a.en.py", "en")
        assert got[0][1] is None
        assert got == cells

    def test_construct_roundtrips(self, watermarks):
        # The Issue #190 §5 anchor construct column: stored and returned as the
        # 5th tuple element; nullable for non-code rows.
        cells = [
            (0, None, "localized-code", "h0", "function-my-fun"),
            (1, "intro", "slide", "h1", None),
        ]
        watermarks.put_deck(de_path="a.de.py", en_path="a.en.py", lang="de", cells=cells)
        assert watermarks.get_deck("a.de.py", "a.en.py", "de") == cells

    def test_anchor_roundtrips_sparsely(self, watermarks):
        # Issue #403 Phase B: the ``anchor`` column records a narrative cell's
        # positional voiceover anchor. It is sparse — only narrative rows carry
        # one — and is read back via ``get_deck_anchors``, which filters NULLs.
        cells = [
            (0, "intro", "slide", "h0", None),
            (1, None, "voiceover", "h1", None),
            (2, None, "voiceover", "h2", None),
        ]
        watermarks.put_deck(
            de_path="a.de.py",
            en_path="a.en.py",
            lang="de",
            cells=cells,
            anchors={1: "id:intro#0", 2: "fp:abc123#1"},
        )
        # The cell rows round-trip unchanged (anchor is a sidecar map, not a row field).
        assert watermarks.get_deck("a.de.py", "a.en.py", "de") == cells
        # Only the narrative rows appear in the anchor map; the slide row (pos 0) does not.
        assert watermarks.get_deck_anchors("a.de.py", "a.en.py", "de") == {
            1: "id:intro#0",
            2: "fp:abc123#1",
        }

    def test_anchors_absent_yields_empty_map(self, watermarks):
        # A pre-Phase-B deck (no anchors passed) reads back an empty anchor map.
        cells = [(0, None, "voiceover", "h0", None)]
        watermarks.put_deck(de_path="a.de.py", en_path="a.en.py", lang="de", cells=cells)
        assert watermarks.get_deck_anchors("a.de.py", "a.en.py", "de") == {}

    def test_shared_partition_accepted(self, watermarks):
        # Language-neutral cells are tracked once under the "shared" partition.
        cells = [(0, None, "neutral-code", "h0", "import-time")]
        watermarks.put_deck(de_path="a.de.py", en_path="a.en.py", lang="shared", cells=cells)
        assert watermarks.get_deck("a.de.py", "a.en.py", "shared") == cells
        # "shared" is isolated from the localized partitions.
        assert watermarks.get_deck("a.de.py", "a.en.py", "de") == []

    def test_languages_are_isolated(self, watermarks):
        watermarks.put_deck(
            de_path="a.de.py", en_path="a.en.py", lang="de", cells=[(0, "x", "slide", "dh", None)]
        )
        watermarks.put_deck(
            de_path="a.de.py", en_path="a.en.py", lang="en", cells=[(0, "x", "slide", "eh", None)]
        )
        assert watermarks.get_deck("a.de.py", "a.en.py", "de") == [(0, "x", "slide", "dh", None)]
        assert watermarks.get_deck("a.de.py", "a.en.py", "en") == [(0, "x", "slide", "eh", None)]

    def test_put_deck_replaces_atomically(self, watermarks):
        watermarks.put_deck(
            de_path="a.de.py",
            en_path="a.en.py",
            lang="de",
            cells=[(0, "a", "slide", "h0", None), (1, "b", "slide", "h1", None)],
        )
        # Rewrite with a shorter, different deck — old rows must be gone.
        watermarks.put_deck(
            de_path="a.de.py", en_path="a.en.py", lang="de", cells=[(0, "c", "slide", "h2", None)]
        )
        assert watermarks.get_deck("a.de.py", "a.en.py", "de") == [(0, "c", "slide", "h2", None)]

    def test_invalid_lang_raises(self, watermarks):
        with pytest.raises(ValueError, match="lang must be"):
            watermarks.put_deck(
                de_path="a.de.py",
                en_path="a.en.py",
                lang="fr",
                cells=[(0, "x", "slide", "h", None)],
            )

    def test_pairs_are_isolated(self, watermarks):
        watermarks.put_deck(
            de_path="a.de.py", en_path="a.en.py", lang="de", cells=[(0, "x", "slide", "ah", None)]
        )
        watermarks.put_deck(
            de_path="b.de.py", en_path="b.en.py", lang="de", cells=[(0, "y", "slide", "bh", None)]
        )
        assert watermarks.get_deck("a.de.py", "a.en.py", "de") == [(0, "x", "slide", "ah", None)]
        assert watermarks.get_deck("b.de.py", "b.en.py", "de") == [(0, "y", "slide", "bh", None)]

    def test_clear_pair(self, watermarks):
        watermarks.put_deck(
            de_path="a.de.py", en_path="a.en.py", lang="de", cells=[(0, "x", "slide", "h", None)]
        )
        watermarks.put_deck(
            de_path="a.de.py", en_path="a.en.py", lang="en", cells=[(0, "x", "slide", "h", None)]
        )
        removed = watermarks.clear_pair("a.de.py", "a.en.py")
        assert removed == 2
        assert watermarks.has_pair("a.de.py", "a.en.py") is False

    def test_survives_close_and_reopen(self, tmp_path: Path):
        path = tmp_path / "clm-llm.sqlite"
        first = SyncWatermarkCache(path)
        first.put_deck(
            de_path="a.de.py", en_path="a.en.py", lang="de", cells=[(0, "x", "slide", "h", "c0")]
        )
        first.close()
        second = SyncWatermarkCache(path)
        try:
            assert second.get_deck("a.de.py", "a.en.py", "de") == [(0, "x", "slide", "h", "c0")]
        finally:
            second.close()

    def test_construct_column_migrates_onto_legacy_table(self, tmp_path: Path):
        # A pre-#190 table (no construct column) must gain it additively, with
        # existing rows backfilling to NULL — not a wipe.
        import sqlite3

        path = tmp_path / "clm-llm.sqlite"
        legacy = sqlite3.connect(str(path))
        legacy.execute(
            """CREATE TABLE sync_watermarks (
                de_path TEXT NOT NULL, en_path TEXT NOT NULL, lang TEXT NOT NULL,
                position INTEGER NOT NULL, slide_id TEXT, role TEXT NOT NULL,
                content_hash TEXT NOT NULL, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (de_path, en_path, lang, position)
            )"""
        )
        legacy.execute(
            "INSERT INTO sync_watermarks (de_path, en_path, lang, position, slide_id, role, "
            "content_hash) VALUES ('a.de.py', 'a.en.py', 'de', 0, 'x', 'slide', 'h')"
        )
        legacy.commit()
        legacy.close()

        migrated = SyncWatermarkCache(path)  # _migrate runs the ALTER
        try:
            # The ALTER is additive — the legacy row survives with construct=NULL
            # (a wipe would lose it). Verified against the raw table.
            raw = sqlite3.connect(str(path))
            try:
                row = raw.execute(
                    "SELECT position, slide_id, role, content_hash, construct "
                    "FROM sync_watermarks WHERE de_path='a.de.py'"
                ).fetchone()
            finally:
                raw.close()
            assert row == (0, "x", "slide", "h", None)
            # But a pre-#429 watermark has no recorded hash_version, so it reads as
            # stale (its hashes use the old canonical form) — get_deck cold-starts it
            # rather than diffing against incomparable hashes. Issue #429.
            assert migrated.get_deck("a.de.py", "a.en.py", "de") == []
            assert migrated.has_pair("a.de.py", "a.en.py") is False
        finally:
            migrated.close()

    def test_hash_version_stamped_and_gates_reads(self, tmp_path: Path):
        # Issue #429: every write stamps the current hash version; a pair stored at
        # a different version reads as absent so it cold-starts and re-records.
        from clm.infrastructure.llm.cache import WATERMARK_HASH_VERSION

        path = tmp_path / "clm-llm.sqlite"
        cache = SyncWatermarkCache(path)
        try:
            cache.put_deck(
                de_path="a.de.py",
                en_path="a.en.py",
                lang="de",
                cells=[(0, "x", "slide", "h", None)],
            )
            assert cache.get_hash_version("a.de.py", "a.en.py") == WATERMARK_HASH_VERSION
            assert cache.has_pair("a.de.py", "a.en.py") is True
            assert cache.get_deck("a.de.py", "a.en.py", "de") == [(0, "x", "slide", "h", None)]

            # Simulate a stored snapshot from a different (stale) canonicalization.
            cache._conn.execute(
                "UPDATE sync_watermark_meta SET hash_version=? WHERE de_path='a.de.py'",
                (WATERMARK_HASH_VERSION - 1,),
            )
            cache._conn.commit()
            assert cache.has_pair("a.de.py", "a.en.py") is False
            assert cache.get_deck("a.de.py", "a.en.py", "de") == []

            # A fresh write re-stamps the current version → readable again.
            cache.put_deck(
                de_path="a.de.py",
                en_path="a.en.py",
                lang="de",
                cells=[(0, "x", "slide", "h2", None)],
            )
            assert cache.has_pair("a.de.py", "a.en.py") is True
            assert cache.get_deck("a.de.py", "a.en.py", "de") == [(0, "x", "slide", "h2", None)]
        finally:
            cache.close()

    def test_coexists_with_other_caches(self, tmp_path: Path):
        path = tmp_path / "clm-llm.sqlite"
        wm = SyncWatermarkCache(path)
        snap = SyncSnapshotCache(path)
        try:
            wm.put_deck(
                de_path="a.de.py",
                en_path="a.en.py",
                lang="de",
                cells=[(0, "x", "slide", "h", None)],
            )
            snap.put(
                de_path="a.de.py",
                en_path="a.en.py",
                slide_id="x",
                role="slide",
                de_hash="dh",
                en_hash="eh",
                direction="de->en",
            )
            assert wm.get_deck("a.de.py", "a.en.py", "de") == [(0, "x", "slide", "h", None)]
            assert snap.get("a.de.py", "a.en.py", "x", "slide") == ("dh", "eh", "de->en")
        finally:
            snap.close()
            wm.close()

    def test_iter_entries(self, watermarks):
        watermarks.put_deck(
            de_path="a.de.py", en_path="a.en.py", lang="de", cells=[(0, "x", "slide", "h", "c0")]
        )
        rows = watermarks.iter_entries()
        assert len(rows) == 1
        # (de_path, en_path, lang, position, slide_id, role, content_hash, construct, synced_at)
        assert rows[0][:8] == ("a.de.py", "a.en.py", "de", 0, "x", "slide", "h", "c0")


# ---------------------------------------------------------------------------
# SyncAlignmentCache (Issue #190, Phase 5 — bounded LLM recovery)
# ---------------------------------------------------------------------------


@pytest.fixture
def alignments(tmp_path: Path):
    c = SyncAlignmentCache(tmp_path / "clm-llm.sqlite")
    try:
        yield c
    finally:
        c.close()


class TestSyncAlignmentCache:
    def test_miss(self, alignments):
        assert alignments.get("base", "current", "v1") is None

    def test_round_trip(self, alignments):
        alignments.put("base", "current", "v1", '{"0": "def-my-fun", "1": "new"}')
        assert alignments.get("base", "current", "v1") == '{"0": "def-my-fun", "1": "new"}'

    def test_overwrite_same_key(self, alignments):
        alignments.put("base", "current", "v1", '{"0": "a"}')
        alignments.put("base", "current", "v1", '{"0": "b"}')
        assert alignments.get("base", "current", "v1") == '{"0": "b"}'

    def test_base_region_hash_in_key(self, alignments):
        alignments.put("base1", "current", "v1", '{"0": "p1"}')
        alignments.put("base2", "current", "v1", '{"0": "p2"}')
        assert alignments.get("base1", "current", "v1") == '{"0": "p1"}'
        assert alignments.get("base2", "current", "v1") == '{"0": "p2"}'

    def test_current_region_hash_in_key(self, alignments):
        alignments.put("base", "current1", "v1", '{"0": "p1"}')
        alignments.put("base", "current2", "v1", '{"0": "p2"}')
        assert alignments.get("base", "current1", "v1") == '{"0": "p1"}'
        assert alignments.get("base", "current2", "v1") == '{"0": "p2"}'

    def test_prompt_version_in_key(self, alignments):
        alignments.put("base", "current", "v1", '{"0": "v1"}')
        alignments.put("base", "current", "v2", '{"0": "v2"}')
        assert alignments.get("base", "current", "v1") == '{"0": "v1"}'
        assert alignments.get("base", "current", "v2") == '{"0": "v2"}'

    def test_invalidate_prompt_version(self, alignments):
        alignments.put("b1", "c", "v1", '{"0": "p1"}')
        alignments.put("b2", "c", "v1", '{"0": "p2"}')
        alignments.put("b3", "c", "v2", '{"0": "p3"}')
        removed = alignments.invalidate_prompt_version("v2")
        assert removed == 2
        assert alignments.get("b1", "c", "v1") is None
        assert alignments.get("b3", "c", "v2") == '{"0": "p3"}'

    def test_iter_entries_empty(self, alignments):
        assert alignments.iter_entries() == []

    def test_iter_entries_returns_all_rows(self, alignments):
        alignments.put("b1", "c1", "v1", '{"0": "a"}')
        alignments.put("b2", "c2", "v1", '{"0": "b"}')
        rows = alignments.iter_entries()
        assert len(rows) == 2
        # (base_region_hash, current_region_hash, prompt_version, alignment, created_at)
        keys = {(row[0], row[1]) for row in rows}
        assert keys == {("b1", "c1"), ("b2", "c2")}

    def test_survives_close_and_reopen(self, tmp_path: Path):
        path = tmp_path / "clm-llm.sqlite"
        first = SyncAlignmentCache(path)
        first.put("base", "current", "v1", '{"0": "kept"}')
        first.close()
        second = SyncAlignmentCache(path)
        try:
            assert second.get("base", "current", "v1") == '{"0": "kept"}'
        finally:
            second.close()

    def test_coexists_with_other_caches(self, tmp_path: Path):
        path = tmp_path / "clm-llm.sqlite"
        align = SyncAlignmentCache(path)
        wm = SyncWatermarkCache(path)
        try:
            align.put("base", "current", "v1", '{"0": "align"}')
            wm.put_deck(
                de_path="a.de.py",
                en_path="a.en.py",
                lang="de",
                cells=[(0, "x", "slide", "h", None)],
            )
            assert align.get("base", "current", "v1") == '{"0": "align"}'
            assert wm.get_deck("a.de.py", "a.en.py", "de") == [(0, "x", "slide", "h", None)]
        finally:
            wm.close()
            align.close()


class TestSyncWatermarkSyncedCommit:
    """The pair-level ``synced_commit`` metadata (Fix D)."""

    def _wm(self, tmp_path: Path) -> SyncWatermarkCache:
        return SyncWatermarkCache(tmp_path / "clm-llm.sqlite")

    def test_get_is_none_when_unset(self, tmp_path: Path):
        wm = self._wm(tmp_path)
        try:
            assert wm.get_synced_commit("a.de.py", "a.en.py") is None
        finally:
            wm.close()

    def test_set_then_get_round_trip(self, tmp_path: Path):
        wm = self._wm(tmp_path)
        try:
            wm.set_synced_commit("a.de.py", "a.en.py", "abc123")
            assert wm.get_synced_commit("a.de.py", "a.en.py") == "abc123"
        finally:
            wm.close()

    def test_set_overwrites(self, tmp_path: Path):
        wm = self._wm(tmp_path)
        try:
            wm.set_synced_commit("a.de.py", "a.en.py", "first")
            wm.set_synced_commit("a.de.py", "a.en.py", "second")
            assert wm.get_synced_commit("a.de.py", "a.en.py") == "second"
        finally:
            wm.close()

    def test_clear_pair_removes_commit(self, tmp_path: Path):
        wm = self._wm(tmp_path)
        try:
            wm.put_deck(
                de_path="a.de.py",
                en_path="a.en.py",
                lang="de",
                cells=[(0, "x", "slide", "h", None)],
            )
            wm.set_synced_commit("a.de.py", "a.en.py", "abc123")
            wm.clear_pair("a.de.py", "a.en.py")
            assert wm.get_synced_commit("a.de.py", "a.en.py") is None
        finally:
            wm.close()

    def test_persists_across_reopen(self, tmp_path: Path):
        first = self._wm(tmp_path)
        first.set_synced_commit("a.de.py", "a.en.py", "deadbeef")
        first.close()
        second = self._wm(tmp_path)
        try:
            assert second.get_synced_commit("a.de.py", "a.en.py") == "deadbeef"
        finally:
            second.close()
