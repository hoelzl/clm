"""Tests for clm.slides.workshop_scope."""

from __future__ import annotations

from dataclasses import dataclass, field

from clm.slides.workshop_scope import (
    find_workshop_ranges,
    find_workshop_start_index,
    is_in_workshop,
)


@dataclass
class _Cell:
    cell_type: str
    tags: list[str] = field(default_factory=list)
    slide_id: str | None = None


# ---------------------------------------------------------------------------
# find_workshop_start_index — kept for diagnostic call sites
# ---------------------------------------------------------------------------


class TestFindWorkshopStartIndex:
    def test_returns_none_when_no_workshop(self):
        cells = [
            _Cell("markdown", ["slide"]),
            _Cell("code", []),
            _Cell("markdown", ["subslide"]),
        ]
        assert find_workshop_start_index(cells) is None

    def test_finds_first_workshop_heading(self):
        cells = [
            _Cell("markdown", ["slide"]),
            _Cell("code", []),
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("code", []),
        ]
        assert find_workshop_start_index(cells) == 2

    def test_ignores_workshop_tag_on_code_cell(self):
        cells = [
            _Cell("code", ["workshop"]),
            _Cell("markdown", ["subslide", "workshop"]),
        ]
        assert find_workshop_start_index(cells) == 1

    def test_returns_first_of_multiple_workshop_headings(self):
        cells = [
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("markdown", ["subslide", "workshop"]),
        ]
        assert find_workshop_start_index(cells) == 0

    def test_empty_input(self):
        assert find_workshop_start_index([]) is None


# ---------------------------------------------------------------------------
# find_workshop_ranges — primary API
# ---------------------------------------------------------------------------


class TestFindWorkshopRanges:
    def test_no_workshop_returns_empty(self):
        cells = [_Cell("markdown", ["slide"]), _Cell("code", [])]
        assert find_workshop_ranges(cells) == []

    def test_single_workshop_runs_to_eof(self):
        """Backward compat: a workshop without an end-workshop tag extends
        to end-of-notebook, exactly like the legacy behaviour."""
        cells = [
            _Cell("markdown", ["slide"]),
            _Cell("code", []),
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("code", []),
            _Cell("code", []),
        ]
        assert find_workshop_ranges(cells) == [(2, 5)]

    def test_workshop_terminated_by_end_workshop(self):
        """``end-workshop`` is exclusive — the cell carrying the tag is
        outside the workshop."""
        cells = [
            _Cell("markdown", ["slide"]),
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("code", []),
            _Cell("markdown", ["subslide", "end-workshop"]),
            _Cell("code", []),
        ]
        assert find_workshop_ranges(cells) == [(1, 3)]

    def test_two_workshops_with_explicit_end(self):
        cells = [
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("code", []),
            _Cell("markdown", ["subslide", "end-workshop"]),
            _Cell("markdown", ["slide"]),
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("code", []),
        ]
        # Second workshop has no closing tag → runs to EOF.
        assert find_workshop_ranges(cells) == [(0, 2), (4, 6)]

    def test_consecutive_workshop_headings_close_previous(self):
        """A new ``workshop`` heading closes the open workshop."""
        cells = [
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("code", []),
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("code", []),
        ]
        assert find_workshop_ranges(cells) == [(0, 2), (2, 4)]

    def test_end_workshop_on_code_cell_ignored(self):
        """``end-workshop`` only matters on markdown cells."""
        cells = [
            _Cell("markdown", ["subslide", "workshop"]),
            _Cell("code", ["end-workshop"]),
            _Cell("code", []),
        ]
        assert find_workshop_ranges(cells) == [(0, 3)]

    def test_orphan_end_workshop_ignored(self):
        """``end-workshop`` with no preceding workshop has no effect on
        the partition (the validator emits a warning separately)."""
        cells = [
            _Cell("markdown", ["slide"]),
            _Cell("markdown", ["subslide", "end-workshop"]),
        ]
        assert find_workshop_ranges(cells) == []

    def test_empty_input(self):
        assert find_workshop_ranges([]) == []


class TestWorkshopSlideIdOpener:
    """The ``workshop-…`` slide_id convention opens a workshop range when
    used on a slide/subslide cell, equivalent to the ``workshop`` tag."""

    def test_slide_id_prefix_opens_range_to_eof(self):
        """Announcement slide carries the workshop scope to EOF when no
        ``end-workshop`` tag follows."""
        cells = [
            _Cell("markdown", ["slide"], slide_id="intro"),
            _Cell("code", []),
            _Cell("markdown", ["slide"], slide_id="workshop-persona-switcher"),
            _Cell("markdown", ["voiceover"], slide_id="workshop-persona-switcher"),
            _Cell("markdown", ["slide"], slide_id="task-1-persona-template"),
            _Cell("code", []),
            _Cell("markdown", ["slide"], slide_id="task-2-use-partial"),
        ]
        assert find_workshop_ranges(cells) == [(2, 7)]

    def test_slide_id_prefix_requires_slide_or_subslide_tag(self):
        """A voiceover/notes cell sharing the announcement slide_id must
        not open a new range — only the slide-start carries the boundary."""
        cells = [
            _Cell("markdown", ["voiceover"], slide_id="workshop-foo"),
            _Cell("markdown", ["notes"], slide_id="workshop-foo"),
            _Cell("markdown", ["slide"], slide_id="task-1"),
        ]
        assert find_workshop_ranges(cells) == []

    def test_subslide_with_workshop_slide_id_opens_range(self):
        cells = [
            _Cell("markdown", ["slide"], slide_id="lecture-intro"),
            _Cell("markdown", ["subslide"], slide_id="workshop-aufgabe-1"),
            _Cell("code", []),
        ]
        assert find_workshop_ranges(cells) == [(1, 3)]

    def test_end_workshop_closes_slide_id_opened_range(self):
        cells = [
            _Cell("markdown", ["slide"], slide_id="workshop-intro"),
            _Cell("code", []),
            _Cell("markdown", ["subslide", "end-workshop"]),
            _Cell("markdown", ["slide"], slide_id="post-workshop"),
        ]
        assert find_workshop_ranges(cells) == [(0, 2)]

    def test_legacy_tag_and_slide_id_opener_coexist(self):
        """A tag-opened workshop is closed when the next opener uses the
        slide_id form (and vice versa)."""
        cells = [
            _Cell("markdown", ["slide", "workshop"], slide_id="workshop-a"),
            _Cell("code", []),
            _Cell("markdown", ["slide"], slide_id="workshop-b"),
            _Cell("code", []),
        ]
        assert find_workshop_ranges(cells) == [(0, 2), (2, 4)]

    def test_slide_id_prefix_ignored_on_code_cell(self):
        """Code cells never open a workshop range, even with a matching
        slide_id (which would be unusual but should not derail detection)."""
        cells = [
            _Cell("code", ["keep"], slide_id="workshop-stub"),
            _Cell("markdown", ["slide"], slide_id="normal-slide"),
        ]
        assert find_workshop_ranges(cells) == []


class TestIsInWorkshop:
    def test_membership(self):
        ranges = [(2, 5), (8, 10)]
        assert is_in_workshop(0, ranges) is False
        assert is_in_workshop(2, ranges) is True
        assert is_in_workshop(4, ranges) is True
        assert is_in_workshop(5, ranges) is False  # exclusive end
        assert is_in_workshop(8, ranges) is True
        assert is_in_workshop(10, ranges) is False

    def test_empty_ranges(self):
        assert is_in_workshop(0, []) is False
