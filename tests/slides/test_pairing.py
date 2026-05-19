"""Tests for clm.slides.pairing — DE/EN group detection + title macro anchor.

The helpers are consumed by both :mod:`clm.slides.assign_ids` (Phase 2)
and :mod:`clm.slides.validator` (Phase 3). Phase 2 already covers the
algorithm indirectly via the slug-assignment tests; this file pins down
the pure structural behavior so future refactors don't drift.
"""

from __future__ import annotations

from clm.notebooks.slide_parser import parse_cells
from clm.slides.pairing import (
    HEADER_MACRO_RE,
    TITLE_SLIDE_ID,
    build_slide_groups,
    build_slide_pairs,
    is_title_macro_cell,
)


def _cells(text: str):
    return parse_cells(text)


class TestBuildSlideGroups:
    def test_solo_slide(self):
        cells = _cells('# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n')
        assert build_slide_groups(cells) == [(0,)]

    def test_paired_de_en_in_order(self):
        cells = _cells(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n'
        )
        assert build_slide_groups(cells) == [(0, 1)]

    def test_paired_en_de_in_order(self):
        # The helper preserves source order; EN-first is still a pair.
        cells = _cells(
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n'
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
        )
        assert build_slide_groups(cells) == [(0, 1)]

    def test_same_language_consecutive_not_paired(self):
        # Two DE slides in a row are two solo groups, not a pair.
        cells = _cells(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## A\n'
            '# %% [markdown] lang="de" tags=["slide"]\n# ## B\n'
        )
        assert build_slide_groups(cells) == [(0,), (1,)]

    def test_lang_less_slide_not_paired(self):
        # A slide without lang= shouldn't pair up with anything; pairing
        # requires both sides to carry distinct lang attributes.
        cells = _cells(
            '# %% [markdown] tags=["slide"]\n# ## Shared\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n'
        )
        assert build_slide_groups(cells) == [(0,), (1,)]

    def test_intervening_cells_dont_split_pair(self):
        # The slide-only index walk is what produces tuples — narrative
        # and code cells between two slide cells don't block pairing
        # (separate ordering check covers that anti-pattern).
        cells = _cells(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
            "# %% tags=[]\nx = 1\n"
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n'
        )
        assert build_slide_groups(cells) == [(0, 2)]

    def test_multiple_groups(self):
        cells = _cells(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## A1\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## A2\n'
            '# %% [markdown] lang="de" tags=["subslide"]\n# ## B1\n'
            '# %% [markdown] lang="en" tags=["subslide"]\n# ## B2\n'
        )
        assert build_slide_groups(cells) == [(0, 1), (2, 3)]


class TestBuildSlidePairs:
    def test_solo_maps_to_self(self):
        cells = _cells('# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n')
        assert build_slide_pairs(cells) == {0: 0}

    def test_pair_picks_en_index(self):
        # DE first, EN second: both map to the EN cell (index 1).
        cells = _cells(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n'
        )
        assert build_slide_pairs(cells) == {0: 1, 1: 1}

    def test_pair_picks_en_index_when_en_first(self):
        # EN first, DE second: both still map to the EN cell (index 0).
        cells = _cells(
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n'
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
        )
        assert build_slide_pairs(cells) == {0: 0, 1: 0}


class TestTitleMacro:
    def test_header_macro_matches(self):
        # Bilingual ``header(de, en)`` form — group 1 captures the EN title.
        match = HEADER_MACRO_RE.search('# {{ header("Einfuehrung", "Introduction") }}')
        assert match is not None
        assert match.group(1) == "Introduction"

    def test_header_de_macro_matches(self):
        # Phase 5 sibling ``header_de(de)`` form — group 2 captures the DE title.
        match = HEADER_MACRO_RE.search('# {{ header_de("Einfuehrung") }}')
        assert match is not None
        assert match.group(2) == "Einfuehrung"
        # The bilingual capture must be empty for the split form.
        assert match.group(1) is None

    def test_header_en_macro_matches(self):
        # Phase 5 sibling ``header_en(en)`` form — group 2 captures the EN title.
        match = HEADER_MACRO_RE.search('# {{ header_en("Introduction") }}')
        assert match is not None
        assert match.group(2) == "Introduction"
        assert match.group(1) is None

    def test_is_title_macro_cell_true(self):
        cells = _cells(
            '# j2 from \'macros.j2\' import header\n# {{ header("Einfuehrung", "Introduction") }}\n'
        )
        # The macro line is its own j2 cell after parsing.
        macro_cells = [c for c in cells if is_title_macro_cell(c)]
        assert len(macro_cells) == 1

    def test_is_title_macro_cell_true_for_header_de(self):
        cells = _cells(
            "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Einfuehrung\") }}\n"
        )
        macro_cells = [c for c in cells if is_title_macro_cell(c)]
        assert len(macro_cells) == 1

    def test_is_title_macro_cell_true_for_header_en(self):
        cells = _cells(
            "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Introduction\") }}\n"
        )
        macro_cells = [c for c in cells if is_title_macro_cell(c)]
        assert len(macro_cells) == 1

    def test_is_title_macro_cell_false_for_regular_cell(self):
        cells = _cells('# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n')
        assert not any(is_title_macro_cell(c) for c in cells)

    def test_is_title_macro_cell_false_for_unrelated_macro(self):
        # ``header`` is the substring of many things; the regex must not match
        # arbitrary macros that *contain* the word.
        cells = _cells("# j2 from 'macros.j2' import other\n# {{ something_else(\"x\") }}\n")
        assert not any(is_title_macro_cell(c) for c in cells)

    def test_title_slide_id_constant(self):
        # Pinned so accidental rename doesn't silently break the validator
        # and assign-ids consumers.
        assert TITLE_SLIDE_ID == "title"
