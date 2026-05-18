"""Tests for :class:`clm.infrastructure.llm.cache.CoverageCache`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import CoverageCache


@pytest.fixture
def cache(tmp_path: Path):
    c = CoverageCache(tmp_path / "clm-llm.sqlite")
    try:
        yield c
    finally:
        c.close()


class TestCoverageCache:
    def test_miss(self, cache):
        assert cache.get("slide-hash", "vo-hash", "v1", "en") is None

    def test_round_trip(self, cache):
        cache.put("sh", "vh", "v1", "en", "covered", '{"bullets":[]}')
        assert cache.get("sh", "vh", "v1", "en") == ("covered", '{"bullets":[]}')

    def test_overwrite_same_key(self, cache):
        cache.put("sh", "vh", "v1", "en", "covered", None)
        cache.put("sh", "vh", "v1", "en", "gaps", '{"bullets":[1]}')
        assert cache.get("sh", "vh", "v1", "en") == ("gaps", '{"bullets":[1]}')

    def test_lang_in_key(self, cache):
        cache.put("sh", "vh", "v1", "en", "covered", None)
        cache.put("sh", "vh", "v1", "de", "gaps", None)
        assert cache.get("sh", "vh", "v1", "en") == ("covered", None)
        assert cache.get("sh", "vh", "v1", "de") == ("gaps", None)

    def test_slide_hash_in_key(self, cache):
        cache.put("sh1", "vh", "v1", "en", "covered", None)
        cache.put("sh2", "vh", "v1", "en", "gaps", None)
        assert cache.get("sh1", "vh", "v1", "en") == ("covered", None)
        assert cache.get("sh2", "vh", "v1", "en") == ("gaps", None)

    def test_voiceover_hash_in_key(self, cache):
        cache.put("sh", "vh1", "v1", "en", "covered", None)
        cache.put("sh", "vh2", "v1", "en", "gaps", None)
        assert cache.get("sh", "vh1", "v1", "en") == ("covered", None)
        assert cache.get("sh", "vh2", "v1", "en") == ("gaps", None)

    def test_prompt_version_in_key(self, cache):
        cache.put("sh", "vh", "v1", "en", "covered", None)
        cache.put("sh", "vh", "v2", "en", "gaps", None)
        assert cache.get("sh", "vh", "v1", "en") == ("covered", None)
        assert cache.get("sh", "vh", "v2", "en") == ("gaps", None)

    def test_invalidate_prompt_version(self, cache):
        cache.put("sh1", "vh", "v1", "en", "covered", None)
        cache.put("sh2", "vh", "v1", "en", "gaps", None)
        cache.put("sh3", "vh", "v2", "en", "covered", None)
        removed = cache.invalidate_prompt_version("v2")
        assert removed == 2
        assert cache.get("sh1", "vh", "v1", "en") is None
        assert cache.get("sh3", "vh", "v2", "en") == ("covered", None)

    def test_iter_entries_empty(self, cache):
        assert cache.iter_entries() == []

    def test_iter_entries_returns_all_rows(self, cache):
        cache.put("sh1", "vh1", "v1", "en", "covered", '{"bullets":[]}')
        cache.put("sh2", "vh2", "v1", "de", "gaps", '{"bullets":[{"text":"x"}]}')

        rows = cache.iter_entries()
        assert len(rows) == 2
        keys = {(row[0], row[3]) for row in rows}
        assert keys == {("sh1", "en"), ("sh2", "de")}

    def test_survives_close_and_reopen(self, tmp_path: Path):
        path = tmp_path / "clm-llm.sqlite"
        first = CoverageCache(path)
        first.put("sh", "vh", "v1", "en", "covered", None)
        first.close()

        second = CoverageCache(path)
        try:
            assert second.get("sh", "vh", "v1", "en") == ("covered", None)
        finally:
            second.close()

    def test_coexists_with_title_suggestion_cache(self, tmp_path: Path):
        from clm.infrastructure.llm.cache import TitleSuggestionCache

        path = tmp_path / "clm-llm.sqlite"
        cov = CoverageCache(path)
        tit = TitleSuggestionCache(path)
        try:
            cov.put("sh", "vh", "v1", "en", "covered", None)
            tit.put("ch", "v1", "RAG Architecture Overview", "en")
            assert cov.get("sh", "vh", "v1", "en") == ("covered", None)
            assert tit.get("ch", "v1", "en") == "RAG Architecture Overview"
        finally:
            tit.close()
            cov.close()
