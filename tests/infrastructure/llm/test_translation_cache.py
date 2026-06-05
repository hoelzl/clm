"""Tests for :class:`clm.infrastructure.llm.cache.TranslationCache` and the
:class:`clm.slides.sync_translate.CachingSlideTranslator` wrapper (Issue #232)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import TranslationCache
from clm.slides.sync_translate import CachingSlideTranslator, TranslationError


@pytest.fixture
def cache(tmp_path: Path):
    c = TranslationCache(tmp_path / "clm-llm.sqlite")
    try:
        yield c
    finally:
        c.close()


class TestTranslationCache:
    def test_round_trip(self, cache):
        cache.put("h1", "v1", "de", "en", "markdown", "Hello")
        assert cache.get("h1", "v1", "de", "en", "markdown") == "Hello"

    def test_miss(self, cache):
        assert cache.get("nope", "v1", "de", "en", "markdown") is None

    def test_overwrite_same_key(self, cache):
        cache.put("h1", "v1", "de", "en", "markdown", "First")
        cache.put("h1", "v1", "de", "en", "markdown", "Second")
        assert cache.get("h1", "v1", "de", "en", "markdown") == "Second"

    def test_direction_in_key(self, cache):
        cache.put("h1", "v1", "de", "en", "markdown", "to-en")
        cache.put("h1", "v1", "en", "de", "markdown", "to-de")
        assert cache.get("h1", "v1", "de", "en", "markdown") == "to-en"
        assert cache.get("h1", "v1", "en", "de", "markdown") == "to-de"

    def test_role_in_key(self, cache):
        cache.put("h1", "v1", "de", "en", "markdown", "prose")
        cache.put("h1", "v1", "de", "en", "code", "code")
        assert cache.get("h1", "v1", "de", "en", "markdown") == "prose"
        assert cache.get("h1", "v1", "de", "en", "code") == "code"

    def test_prompt_version_in_key(self, cache):
        cache.put("h1", "v1", "de", "en", "markdown", "old")
        cache.put("h1", "v2", "de", "en", "markdown", "new")
        assert cache.get("h1", "v1", "de", "en", "markdown") == "old"
        assert cache.get("h1", "v2", "de", "en", "markdown") == "new"

    def test_invalidate_prompt_version(self, cache):
        cache.put("h1", "v1", "de", "en", "markdown", "a")
        cache.put("h2", "v1", "de", "en", "markdown", "b")
        cache.put("h3", "v2", "de", "en", "markdown", "c")
        assert cache.invalidate_prompt_version("v2") == 2
        assert cache.get("h1", "v1", "de", "en", "markdown") is None
        assert cache.get("h3", "v2", "de", "en", "markdown") == "c"

    def test_survives_close_and_reopen(self, tmp_path: Path):
        path = tmp_path / "clm-llm.sqlite"
        first = TranslationCache(path)
        first.put("h1", "v1", "de", "en", "markdown", "Persisted")
        first.close()
        second = TranslationCache(path)
        try:
            assert second.get("h1", "v1", "de", "en", "markdown") == "Persisted"
        finally:
            second.close()


@dataclass
class _CountingTranslator:
    """A SlideTranslator that records every call and maps body -> result."""

    mapping: dict[str, str] = field(default_factory=dict)
    model: str = "test-model"
    prompt_version: str = "pv1"
    calls: list[str] = field(default_factory=list)

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        self.calls.append(source_body)
        if source_body in self.mapping:
            return self.mapping[source_body]
        raise TranslationError(f"no mapping for {source_body!r}")


class TestCachingSlideTranslator:
    def test_first_call_misses_then_caches(self, cache):
        inner = _CountingTranslator(mapping={"Hallo": "Hello"})
        wrapped = CachingSlideTranslator(inner=inner, cache=cache)
        out = wrapped.translate(
            source_body="Hallo", source_lang="de", target_lang="en", role="markdown"
        )
        assert out == "Hello"
        # Second call with the same inputs is served from the cache (inner not hit).
        out2 = wrapped.translate(
            source_body="Hallo", source_lang="de", target_lang="en", role="markdown"
        )
        assert out2 == "Hello"
        assert inner.calls == ["Hallo"]  # inner called exactly once

    def test_cache_persists_across_wrapper_instances(self, cache):
        inner1 = _CountingTranslator(mapping={"Hallo": "Hello"})
        CachingSlideTranslator(inner=inner1, cache=cache).translate(
            source_body="Hallo", source_lang="de", target_lang="en", role="markdown"
        )
        # A fresh wrapper over a fresh inner reuses the cached entry.
        inner2 = _CountingTranslator(mapping={})  # would raise if consulted
        out = CachingSlideTranslator(inner=inner2, cache=cache).translate(
            source_body="Hallo", source_lang="de", target_lang="en", role="markdown"
        )
        assert out == "Hello"
        assert inner2.calls == []

    def test_failure_is_not_cached(self, cache):
        inner = _CountingTranslator(mapping={})
        wrapped = CachingSlideTranslator(inner=inner, cache=cache)
        with pytest.raises(TranslationError):
            wrapped.translate(source_body="x", source_lang="de", target_lang="en", role="markdown")
        # Nothing stored: hash of "x" must not be present.
        h = hashlib.sha256(b"x").hexdigest()
        assert cache.get(h, wrapped.prompt_version, "de", "en", "markdown") is None

    def test_model_folded_into_version(self, cache):
        # Two models with the same base prompt_version must not share an entry.
        a = CachingSlideTranslator(
            inner=_CountingTranslator(mapping={"k": "A"}, model="m-a"), cache=cache
        )
        b = CachingSlideTranslator(
            inner=_CountingTranslator(mapping={"k": "B"}, model="m-b"), cache=cache
        )
        assert (
            a.translate(source_body="k", source_lang="de", target_lang="en", role="markdown") == "A"
        )
        assert (
            b.translate(source_body="k", source_lang="de", target_lang="en", role="markdown") == "B"
        )
        assert a.prompt_version != b.prompt_version

    def test_satisfies_protocol(self, cache):
        from clm.slides.sync_translate import SlideTranslator

        wrapped = CachingSlideTranslator(inner=_CountingTranslator(), cache=cache)
        assert isinstance(wrapped, SlideTranslator)
