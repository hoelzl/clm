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
