"""Tests for :class:`clm.infrastructure.llm.cache.TitleSuggestionCache` and
the cache-directory resolver."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import TitleSuggestionCache, resolve_cache_dir


@pytest.fixture
def cache(tmp_path: Path):
    c = TitleSuggestionCache(tmp_path / "clm-llm.sqlite")
    try:
        yield c
    finally:
        c.close()


class TestTitleSuggestionCache:
    def test_round_trip(self, cache):
        cache.put("hash1", "v1", "RAG Architecture Overview", "en")
        assert cache.get("hash1", "v1", "en") == "RAG Architecture Overview"

    def test_miss(self, cache):
        assert cache.get("nope", "v1", "en") is None

    def test_overwrite_same_key(self, cache):
        cache.put("hash1", "v1", "First Title", "en")
        cache.put("hash1", "v1", "Updated Title", "en")
        assert cache.get("hash1", "v1", "en") == "Updated Title"

    def test_lang_in_key(self, cache):
        cache.put("hash1", "v1", "English Title", "en")
        cache.put("hash1", "v1", "Deutscher Titel", "de")
        assert cache.get("hash1", "v1", "en") == "English Title"
        assert cache.get("hash1", "v1", "de") == "Deutscher Titel"

    def test_prompt_version_in_key(self, cache):
        cache.put("hash1", "v1", "Title v1", "en")
        cache.put("hash1", "v2", "Title v2", "en")
        assert cache.get("hash1", "v1", "en") == "Title v1"
        assert cache.get("hash1", "v2", "en") == "Title v2"

    def test_invalidate_prompt_version(self, cache):
        cache.put("hash1", "v1", "Old", "en")
        cache.put("hash2", "v1", "Old", "en")
        cache.put("hash3", "v2", "New", "en")
        removed = cache.invalidate_prompt_version("v2")
        assert removed == 2
        assert cache.get("hash1", "v1", "en") is None
        assert cache.get("hash3", "v2", "en") == "New"

    def test_survives_close_and_reopen(self, tmp_path: Path):
        path = tmp_path / "clm-llm.sqlite"
        first = TitleSuggestionCache(path)
        first.put("hash1", "v1", "Persisted", "en")
        first.close()

        second = TitleSuggestionCache(path)
        try:
            assert second.get("hash1", "v1", "en") == "Persisted"
        finally:
            second.close()


class TestResolveCacheDir:
    def test_cli_override_wins(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CLM_CACHE_DIR", str(tmp_path / "env"))
        chosen = tmp_path / "explicit"
        assert resolve_cache_dir(cli_override=chosen, repo_root=tmp_path) == chosen
        assert chosen.is_dir()

    def test_env_var_used_when_no_cli(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "from-env"
        monkeypatch.setenv("CLM_CACHE_DIR", str(target))
        assert resolve_cache_dir(repo_root=tmp_path) == target
        assert target.is_dir()

    def test_pyproject_used_when_no_env(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.clm]\ncache_dir = "custom-cache"\n', encoding="utf-8"
        )
        chosen = resolve_cache_dir(repo_root=tmp_path)
        assert chosen == tmp_path / "custom-cache"
        assert chosen.is_dir()

    def test_pyproject_absolute_path(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        absolute = tmp_path / "absolute-cache"
        (tmp_path / "pyproject.toml").write_text(
            f'[tool.clm]\ncache_dir = "{absolute.as_posix()}"\n',
            encoding="utf-8",
        )
        assert resolve_cache_dir(repo_root=tmp_path) == absolute

    def test_default_fallback(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        chosen = resolve_cache_dir(repo_root=tmp_path)
        assert chosen == tmp_path / ".clm-cache"
        assert chosen.is_dir()
