"""Tests for :class:`clm.infrastructure.llm.cache.SyncCache`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncCache


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
