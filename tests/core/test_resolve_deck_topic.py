"""Tests for ``Course.resolve_deck_topic`` (issue #208 follow-up).

The recordings dashboard lists decks by ``section.name[lang]`` /
``notebook.file_name(lang, "")`` and records actions back keyed on those
display names. ``resolve_deck_topic`` is the inverse used to recover
``(section_id, topic_id)`` for recording provenance. These tests round-trip
that mapping against the shared ``course_1`` fixture so they stay correct as
naming evolves.
"""

import pytest


def _first_section_with_notebooks(course):
    for section in course.sections:
        if section.notebooks:
            return section
    pytest.skip("course fixture has no notebook decks")


def test_resolve_deck_topic_round_trips(course_1):
    section = _first_section_with_notebooks(course_1)
    nb = section.notebooks[0]
    section_name = section.name["en"]
    deck_name = nb.file_name("en", "")

    section_id, topic_id = course_1.resolve_deck_topic(section_name, deck_name, "en")

    assert topic_id == nb.topic.id
    assert section_id == section.id
    # The topic actually owns this notebook.
    assert nb.topic.id in {t.id for t in section.topics}


def test_resolve_deck_topic_unknown_section_is_none(course_1):
    assert course_1.resolve_deck_topic("No Such Section", "00 Whatever", "en") == (None, None)


def test_resolve_deck_topic_unknown_deck_is_none(course_1):
    section = _first_section_with_notebooks(course_1)
    section_name = section.name["en"]
    assert course_1.resolve_deck_topic(section_name, "99 Not A Real Deck", "en") == (None, None)


def test_resolve_deck_topic_wrong_language_section_name(course_1):
    """A German section name must not match when resolving in English."""
    section = _first_section_with_notebooks(course_1)
    nb = section.notebooks[0]
    de_section_name = section.name["de"]
    en_deck_name = nb.file_name("en", "")
    # Looking up the German section name under lang="en" should miss.
    if section.name["de"] != section.name["en"]:
        assert course_1.resolve_deck_topic(de_section_name, en_deck_name, "en") == (None, None)


def test_resolve_deck_file_returns_the_matching_source_path(course_1):
    """The recordings ledger keys on the deck's source file (#1004)."""
    section = _first_section_with_notebooks(course_1)
    nb = section.notebooks[0]

    path = course_1.resolve_deck_file(section.name["en"], nb.file_name("en", ""), "en")

    assert path == nb.source_path
    assert path is not None and path.is_file()


def test_resolve_deck_file_unknown_deck_is_none(course_1):
    section = _first_section_with_notebooks(course_1)
    assert course_1.resolve_deck_file(section.name["en"], "99 Not A Real Deck", "en") is None


def test_resolve_deck_location_skips_the_other_language_half(course_1):
    """A split deck's halves share a slot; the half whose intrinsic language
    is not *lang* must not win the lookup (the dashboard lists it that way)."""
    section = _first_section_with_notebooks(course_1)
    nb = section.notebooks[0]
    section_name, deck_name = section.name["en"], nb.file_name("en", "")
    assert course_1.resolve_deck_location(section_name, deck_name, "en") == (
        section.id,
        nb.topic.id,
        nb.source_path,
    )

    nb.output_language_filter = "de"  # pretend it is the DE half of a split deck
    try:
        assert course_1.resolve_deck_location(section_name, deck_name, "en") == (None, None, None)
        assert course_1.resolve_deck_file(section_name, deck_name, "en") is None
    finally:
        nb.output_language_filter = None
