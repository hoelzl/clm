"""Tests for clm.slides.workshop_scope.find_workshop_start_index."""

from __future__ import annotations

from dataclasses import dataclass, field

from clm.slides.workshop_scope import find_workshop_start_index


@dataclass
class _Cell:
    cell_type: str
    tags: list[str] = field(default_factory=list)


def test_returns_none_when_no_workshop():
    cells = [
        _Cell("markdown", ["slide"]),
        _Cell("code", []),
        _Cell("markdown", ["subslide"]),
    ]
    assert find_workshop_start_index(cells) is None


def test_finds_first_workshop_heading():
    cells = [
        _Cell("markdown", ["slide"]),
        _Cell("code", []),
        _Cell("markdown", ["subslide", "workshop"]),
        _Cell("code", []),
    ]
    assert find_workshop_start_index(cells) == 2


def test_ignores_workshop_tag_on_code_cell():
    """The workshop tag only marks a heading when it sits on a markdown cell."""
    cells = [
        _Cell("code", ["workshop"]),
        _Cell("markdown", ["subslide", "workshop"]),
    ]
    assert find_workshop_start_index(cells) == 1


def test_returns_first_of_multiple_workshop_headings():
    cells = [
        _Cell("markdown", ["subslide", "workshop"]),
        _Cell("markdown", ["subslide", "workshop"]),
    ]
    assert find_workshop_start_index(cells) == 0


def test_empty_input():
    assert find_workshop_start_index([]) is None
