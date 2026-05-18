"""Tests for :mod:`clm.slides.slug`."""

from __future__ import annotations

import pytest

from clm.slides.slug import (
    MAX_SLUG_LENGTH,
    is_preserved,
    is_valid_slug,
    resolve_collision,
    slugify,
    strip_preserve_marker,
)


class TestSlugify:
    def test_simple_title(self):
        assert slugify("RAG Architecture") == "rag-architecture"

    def test_lowercase(self):
        assert slugify("LANGCHAIN") == "langchain"

    def test_strips_markdown_bold(self):
        assert slugify("**LCEL** Pipelines") == "lcel-pipelines"

    def test_strips_markdown_code(self):
        assert slugify("Use the `header()` macro") == "use-the-header-macro"

    def test_strips_markdown_link(self):
        assert slugify("See [docs](http://example.com)") == "see-docs"

    def test_strips_html(self):
        assert slugify("Title<br/>Continued") == "titlecontinued"

    def test_german_umlauts(self):
        assert slugify("Wofür Brauchen Wir Das?") == "wofuer-brauchen-wir-das"

    def test_german_sharp_s(self):
        assert slugify("Großes Modell") == "grosses-modell"

    def test_strip_accents(self):
        assert slugify("Café Latté") == "cafe-latte"

    def test_punctuation_collapses(self):
        assert slugify("Hello, World! / Goodbye?") == "hello-world-goodbye"

    def test_numbers_preserved(self):
        assert slugify("RAG Step 1: Retrieval") == "rag-step-1-retrieval"

    def test_empty_when_no_word_chars(self):
        assert slugify("---!!!---") == ""

    def test_length_cap_drops_stopwords(self):
        # Without stop-word pruning this would be longer than the cap.
        slug = slugify("This Is A Very Long Title For The Slide Page")
        assert len(slug) <= MAX_SLUG_LENGTH
        assert slug.startswith("this")

    def test_length_cap_word_boundary(self):
        slug = slugify("aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii jjjj")
        assert len(slug) <= MAX_SLUG_LENGTH
        # Last segment is a whole word, not a fragment.
        assert "-" in slug
        last = slug.rsplit("-", 1)[-1]
        assert last in {"aaaa", "bbbb", "cccc", "dddd", "eeee", "ffff", "gggg"}

    def test_first_token_alone_exceeds_cap(self):
        slug = slugify("a" * 50)
        assert len(slug) <= MAX_SLUG_LENGTH

    def test_custom_max_length(self):
        slug = slugify("short title here", max_length=10)
        assert len(slug) <= 10


class TestPreserveMarker:
    def test_strip(self):
        assert strip_preserve_marker("!intro") == "intro"

    def test_strip_no_marker(self):
        assert strip_preserve_marker("intro") == "intro"

    def test_strip_only_marker(self):
        # The "!" alone strips to empty — invalid downstream, but the
        # function shouldn't crash.
        assert strip_preserve_marker("!") == ""

    def test_is_preserved_true(self):
        assert is_preserved("!intro") is True

    def test_is_preserved_false(self):
        assert is_preserved("intro") is False


class TestIsValidSlug:
    @pytest.mark.parametrize(
        "slug",
        ["intro", "rag-architecture", "wozu-eine-neue-bibliothek", "step-1", "title"],
    )
    def test_valid(self, slug):
        assert is_valid_slug(slug)

    def test_valid_preserved(self):
        assert is_valid_slug("!intro")

    @pytest.mark.parametrize(
        "slug",
        [
            "",
            "UPPER",
            "with space",
            "trailing-",
            "-leading",
            "double--dash",
            "ünicode",
            "!",
        ],
    )
    def test_invalid(self, slug):
        assert not is_valid_slug(slug)

    def test_over_length(self):
        assert not is_valid_slug("a" * (MAX_SLUG_LENGTH + 1))

    def test_preserve_marker_not_counted_in_length(self):
        body = "a" * MAX_SLUG_LENGTH
        assert is_valid_slug("!" + body)


class TestResolveCollision:
    def test_no_collision(self):
        assert resolve_collision("foo", []) == "foo"

    def test_simple_collision(self):
        assert resolve_collision("foo", ["foo"]) == "foo-2"

    def test_chain_of_collisions(self):
        assert resolve_collision("foo", ["foo", "foo-2", "foo-3"]) == "foo-4"

    def test_preserve_marker_collides(self):
        # !foo and foo are the same identifier — collision must trigger.
        assert resolve_collision("foo", ["!foo"]) == "foo-2"
