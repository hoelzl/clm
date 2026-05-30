"""Tests for the cross-reference extractor (Issue #17 scaffold).

Only the decision-agnostic extractor is implemented and therefore tested.
The resolver is a Protocol stub pending the open product decisions in
``docs/claude/design/cross-references.md``.
"""

from __future__ import annotations

from clm.core.cross_references import (
    SCHEME,
    extract_cross_references,
    has_cross_references,
)


def test_scheme_constant() -> None:
    assert SCHEME == "clm:"


def test_extracts_single_reference() -> None:
    text = "See the [Functions workshop](clm:functions_workshop) for exercises."
    assert extract_cross_references(text) == ["functions_workshop"]


def test_extracts_multiple_references_in_order() -> None:
    text = "First [intro](clm:introduction), then [the workshop](clm:functions_workshop)."
    assert extract_cross_references(text) == [
        "introduction",
        "functions_workshop",
    ]


def test_preserves_duplicate_references() -> None:
    text = "[a](clm:intro) and again [b](clm:intro)"
    assert extract_cross_references(text) == ["intro", "intro"]


def test_ignores_ordinary_links() -> None:
    text = "A [normal link](https://example.com) and a [relative](../foo/bar.html)."
    assert extract_cross_references(text) == []


def test_ignores_image_links() -> None:
    # Image links never use the clm: scheme; they must be left untouched.
    text = "![diagram](img/diagram.png)"
    assert extract_cross_references(text) == []


def test_captures_disambiguator_and_anchor_verbatim() -> None:
    # The extractor returns the raw reference; splitting "/stem" or
    # "#anchor" is the resolver's job (Decision 2/3), so it must be
    # preserved verbatim here.
    text = "[deck](clm:topic_id/slides_foo) and [section](clm:topic_id#heading)"
    assert extract_cross_references(text) == [
        "topic_id/slides_foo",
        "topic_id#heading",
    ]


def test_tolerates_internal_whitespace_in_href() -> None:
    text = "[x](clm: introduction )"
    assert extract_cross_references(text) == ["introduction"]


def test_has_cross_references() -> None:
    assert has_cross_references("[x](clm:intro)") is True
    assert has_cross_references("[x](https://example.com)") is False
    assert has_cross_references("plain text, no links") is False
