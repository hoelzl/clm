"""Tests for ``CourseSpec.resolve_section_selectors`` (phase 3 of section
filtering).

The resolver is a pure function over a ``CourseSpec`` that was parsed with
``keep_disabled=True`` — so ``self.sections`` already contains disabled
sections at their declared indices. Tests construct ``CourseSpec`` objects
directly so they do not depend on XML parsing or filesystem state.
"""

from __future__ import annotations

import pytest

from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    SectionSelection,
    SectionSpec,
)
from clm.core.utils.text_utils import Text


def _spec(sections: list[SectionSpec]) -> CourseSpec:
    """Construct a bare-bones ``CourseSpec`` wrapping the given sections."""
    return CourseSpec(
        name=Text(de="Test", en="Test"),
        prog_lang="python",
        description=Text(de="", en=""),
        certificate=Text(de="", en=""),
        sections=sections,
    )


def _section(
    de: str,
    en: str,
    *,
    id: str | None = None,
    enabled: bool = True,
) -> SectionSpec:
    return SectionSpec(name=Text(de=de, en=en), id=id, enabled=enabled)


class TestBareTokens:
    """Bare tokens try: id → 1-based index → substring (de/en)."""

    def test_bare_id_match(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
                _section("Woche 2", "Week 2", id="w02"),
                _section("Woche 3", "Week 3", id="w03"),
            ]
        )
        sel = spec.resolve_section_selectors(["w02"])
        assert sel.resolved_indices == [1]
        assert sel.skipped_disabled == []

    def test_bare_index_match(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1"),
                _section("Woche 2", "Week 2"),
                _section("Woche 3", "Week 3"),
            ]
        )
        sel = spec.resolve_section_selectors(["3"])
        assert sel.resolved_indices == [2]

    def test_bare_index_counts_disabled_sections(self):
        """Disabled sections occupy an index slot — the index of later
        sections does not shift."""
        spec = _spec(
            [
                _section("Woche 1", "Week 1"),
                _section("Woche 2", "Week 2", enabled=False),
                _section("Woche 3", "Week 3"),
            ]
        )
        # Index 3 still points to Woche 3 even with the disabled w02 in the middle.
        sel = spec.resolve_section_selectors(["3"])
        assert sel.resolved_indices == [2]

    def test_bare_substring_english(self):
        spec = _spec(
            [
                _section("Woche 1 Grundlagen", "Week 1 Basics"),
                _section("Woche 2 Fortgeschritten", "Week 2 Advanced"),
            ]
        )
        sel = spec.resolve_section_selectors(["Advanced"])
        assert sel.resolved_indices == [1]

    def test_bare_substring_german(self):
        spec = _spec(
            [
                _section("Woche 1 Grundlagen", "Week 1 Basics"),
                _section("Woche 2 Fortgeschritten", "Week 2 Advanced"),
            ]
        )
        sel = spec.resolve_section_selectors(["Grundlagen"])
        assert sel.resolved_indices == [0]

    def test_bare_substring_case_insensitive(self):
        spec = _spec(
            [
                _section("Einführung", "Introduction"),
            ]
        )
        sel = spec.resolve_section_selectors(["INTRODUCTION"])
        assert sel.resolved_indices == [0]
        sel = spec.resolve_section_selectors(["einführung"])
        assert sel.resolved_indices == [0]

    def test_multiple_tokens_combined(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
                _section("Woche 2", "Week 2", id="w02"),
                _section("Woche 3", "Week 3", id="w03"),
            ]
        )
        sel = spec.resolve_section_selectors(["w01", "w03"])
        assert sel.resolved_indices == [0, 2]

    def test_duplicate_tokens_deduplicated(self):
        spec = _spec([_section("Woche 1", "Week 1", id="w01")])
        sel = spec.resolve_section_selectors(["w01", "w01"])
        assert sel.resolved_indices == [0]


class TestPrefixedTokens:
    """Prefixed tokens (``id:``, ``idx:``, ``name:``) only try one strategy."""

    def test_id_prefix(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
                _section("Woche 2", "Week 2", id="w02"),
            ]
        )
        sel = spec.resolve_section_selectors(["id:w02"])
        assert sel.resolved_indices == [1]

    def test_idx_prefix(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1"),
                _section("Woche 2", "Week 2"),
                _section("Woche 3", "Week 3"),
            ]
        )
        sel = spec.resolve_section_selectors(["idx:2"])
        assert sel.resolved_indices == [1]

    def test_name_prefix(self):
        spec = _spec(
            [
                _section("Woche 1 Start", "Week 1 Start"),
                _section("Woche 2 Ende", "Week 2 End"),
            ]
        )
        sel = spec.resolve_section_selectors(["name:Woche 2"])
        assert sel.resolved_indices == [1]

    def test_prefix_is_case_insensitive(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
            ]
        )
        sel = spec.resolve_section_selectors(["ID:w01"])
        assert sel.resolved_indices == [0]

    def test_prefix_id_does_not_fall_back(self):
        """``id:`` never falls back to index/name; unmatched = error."""
        spec = _spec(
            [
                _section("Woche 1", "Week 1"),  # no id
            ]
        )
        with pytest.raises(CourseSpecError, match="id match only"):
            spec.resolve_section_selectors(["id:w01"])

    def test_prefix_disambiguates_id_vs_index(self):
        """Section with id='3' and a bare '3' token resolves to the ID;
        ``idx:3`` resolves to the third section by position."""
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="3"),
                _section("Woche 2", "Week 2"),
                _section("Woche 3", "Week 3"),
            ]
        )
        sel_bare = spec.resolve_section_selectors(["3"])
        assert sel_bare.resolved_indices == [0]  # matched by id

        sel_idx = spec.resolve_section_selectors(["idx:3"])
        assert sel_idx.resolved_indices == [2]  # matched by index


class TestErrorHandling:
    def test_empty_list(self):
        spec = _spec([_section("Woche 1", "Week 1")])
        with pytest.raises(CourseSpecError, match="at least one selector token"):
            spec.resolve_section_selectors([])

    def test_empty_string_token(self):
        spec = _spec([_section("Woche 1", "Week 1")])
        with pytest.raises(CourseSpecError, match="empty selector token"):
            spec.resolve_section_selectors([""])

    def test_whitespace_only_token(self):
        spec = _spec([_section("Woche 1", "Week 1")])
        with pytest.raises(CourseSpecError, match="empty selector token"):
            spec.resolve_section_selectors(["   "])

    def test_zero_matches_lists_sections(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
                _section("Woche 2", "Week 2", id="w02"),
            ]
        )
        with pytest.raises(CourseSpecError) as excinfo:
            spec.resolve_section_selectors(["nonexistent"])
        msg = str(excinfo.value)
        assert "'nonexistent'" in msg
        assert "Available sections" in msg
        assert "w01" in msg
        assert "w02" in msg
        assert "Week 1" in msg
        assert "Woche 1" in msg

    def test_ambiguous_bare_substring_raises(self):
        spec = _spec(
            [
                _section("Einführung Python", "Introduction Python"),
                _section("Einführung ML", "Introduction ML"),
            ]
        )
        with pytest.raises(CourseSpecError, match="ambiguous substring"):
            spec.resolve_section_selectors(["Introduction"])

    def test_ambiguous_bare_error_lists_matches(self):
        spec = _spec(
            [
                _section("Einführung Python", "Introduction Python"),
                _section("Einführung ML", "Introduction ML"),
            ]
        )
        with pytest.raises(CourseSpecError) as excinfo:
            spec.resolve_section_selectors(["Introduction"])
        msg = str(excinfo.value)
        assert "Introduction Python" in msg
        assert "Introduction ML" in msg
        assert "Disambiguate" in msg

    def test_no_sections_raises(self):
        spec = _spec([])
        with pytest.raises(CourseSpecError, match="no <section>"):
            spec.resolve_section_selectors(["w01"])


class TestDisabledInteraction:
    def test_only_disabled_token_raises_entire_selection_disabled(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
                _section("Woche 2", "Week 2", id="w02", enabled=False),
            ]
        )
        with pytest.raises(CourseSpecError) as excinfo:
            spec.resolve_section_selectors(["w02"])
        assert "every selected section is disabled" in str(excinfo.value)
        assert "w02" in str(excinfo.value)

    def test_only_multiple_disabled_tokens_raises(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01", enabled=False),
                _section("Woche 2", "Week 2", id="w02", enabled=False),
            ]
        )
        with pytest.raises(CourseSpecError) as excinfo:
            spec.resolve_section_selectors(["w01", "w02"])
        assert "every selected section is disabled" in str(excinfo.value)

    def test_mixed_enabled_and_disabled_keeps_enabled(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
                _section("Woche 2", "Week 2", id="w02", enabled=False),
                _section("Woche 3", "Week 3", id="w03"),
            ]
        )
        sel = spec.resolve_section_selectors(["w01", "w02", "w03"])
        assert sel.resolved_indices == [0, 2]
        assert sel.skipped_disabled == ["w02"]

    def test_disabled_section_uses_id_in_warning_label(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
                _section(
                    "Woche 2 Fortgeschritten",
                    "Week 2 Advanced",
                    id="w02",
                    enabled=False,
                ),
            ]
        )
        sel = spec.resolve_section_selectors(["w01", "w02"])
        assert sel.skipped_disabled == ["w02"]

    def test_disabled_section_without_id_uses_name_label(self):
        spec = _spec(
            [
                _section("Woche 1", "Week 1"),
                _section("Woche 2", "Week 2", enabled=False),
            ]
        )
        sel = spec.resolve_section_selectors(["idx:1", "idx:2"])
        # Without an id, the label falls back to the English name.
        assert sel.skipped_disabled == ["Week 2"]


class TestResultType:
    def test_result_is_section_selection(self):
        spec = _spec([_section("Woche 1", "Week 1", id="w01")])
        sel = spec.resolve_section_selectors(["w01"])
        assert isinstance(sel, SectionSelection)

    def test_resolved_indices_preserve_token_order(self):
        """When multiple tokens match distinct sections, the returned
        indices are in token order, not section order."""
        spec = _spec(
            [
                _section("Woche 1", "Week 1", id="w01"),
                _section("Woche 2", "Week 2", id="w02"),
                _section("Woche 3", "Week 3", id="w03"),
            ]
        )
        sel = spec.resolve_section_selectors(["w03", "w01"])
        assert sel.resolved_indices == [2, 0]
