"""Tests for clm.slides.pairing — DE/EN group detection + title macro anchor.

The helpers are consumed by both :mod:`clm.slides.assign_ids` (Phase 2)
and :mod:`clm.slides.validator` (Phase 3). Phase 2 already covers the
algorithm indirectly via the slug-assignment tests; this file pins down
the pure structural behavior so future refactors don't drift.
"""

from __future__ import annotations

from pathlib import Path

from clm.notebooks.slide_parser import parse_cells
from clm.slides.pairing import (
    HEADER_MACRO_RE,
    TITLE_SLIDE_ID,
    build_slide_groups,
    build_slide_pairs,
    derive_split_pair,
    derive_split_pair_from_stem,
    derive_split_twin,
    is_title_macro_cell,
    order_split_pair,
    split_lang_tag,
    split_twin,
    split_twin_pair,
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


class TestSplitTwin:
    """``split_twin`` / ``split_twin_pair`` — prefix-gated, disk-existence-aware
    twin derivation (the consolidated home of what used to be copied in
    assign_ids and validator). These require the slides_/topic_/project_ prefix
    because they drive build-time routing.
    """

    def test_twin_found_when_sibling_exists(self, tmp_path: Path):
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        assert split_twin(de) == en
        assert split_twin(en) == de

    def test_twin_none_when_sibling_missing(self, tmp_path: Path):
        de = tmp_path / "slides_x.de.py"
        de.write_text("# de\n", encoding="utf-8")
        assert split_twin(de) is None

    def test_twin_none_for_non_split_half(self, tmp_path: Path):
        bilingual = tmp_path / "slides_x.py"
        bilingual.write_text("# both\n", encoding="utf-8")
        assert split_twin(bilingual) is None

    def test_twin_none_without_routing_prefix(self, tmp_path: Path):
        # split_twin is prefix-gated (build routing) — an un-prefixed name is
        # not a recognised slide file even if its sibling exists on disk.
        de = tmp_path / "apis.de.py"
        en = tmp_path / "apis.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        assert split_twin(de) is None

    def test_pair_orders_de_first_from_either_half(self, tmp_path: Path):
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        assert split_twin_pair(de) == (de, en)
        assert split_twin_pair(en) == (de, en)

    def test_pair_none_when_twin_missing(self, tmp_path: Path):
        en = tmp_path / "slides_x.en.py"
        en.write_text("# en\n", encoding="utf-8")
        assert split_twin_pair(en) is None


class TestOrderSplitPair:
    """``order_split_pair`` / ``split_lang_tag`` — the prefix-AGNOSTIC guard
    primitive (``clm slides sync`` reconciles any two halves, regardless of the
    build's routing prefix). No disk access — pure filename logic.
    """

    def test_valid_pair_in_order(self):
        assert order_split_pair(Path("slides_x.de.py"), Path("slides_x.en.py")) == (
            Path("slides_x.de.py"),
            Path("slides_x.en.py"),
        )

    def test_valid_pair_without_routing_prefix(self):
        # Prefix-agnostic: an un-prefixed deck name is still a valid sync pair.
        assert order_split_pair(Path("apis.de.py"), Path("apis.en.py")) == (
            Path("apis.de.py"),
            Path("apis.en.py"),
        )

    def test_swapped_order_is_corrected(self):
        assert order_split_pair(Path("apis.en.py"), Path("apis.de.py")) == (
            Path("apis.de.py"),
            Path("apis.en.py"),
        )

    def test_same_file_rejected(self):
        assert order_split_pair(Path("apis.de.py"), Path("apis.de.py")) is None

    def test_same_language_rejected(self):
        assert order_split_pair(Path("a.de.py"), Path("b.de.py")) is None

    def test_cross_deck_rejected(self):
        assert order_split_pair(Path("apis.de.py"), Path("other.en.py")) is None

    def test_cross_extension_rejected(self):
        # Family key includes the extension, so .de.py and .en.cpp differ.
        assert order_split_pair(Path("apis.de.py"), Path("apis.en.cpp")) is None

    def test_untagged_half_rejected(self):
        assert order_split_pair(Path("apis.py"), Path("apis.en.py")) is None

    def test_unknown_language_rejected(self):
        assert order_split_pair(Path("apis.de.py"), Path("apis.fr.py")) is None

    def test_lang_tag_prefix_agnostic(self):
        assert split_lang_tag(Path("apis.de.py")) == "de"
        assert split_lang_tag(Path("slides_x.en.py")) == "en"
        assert split_lang_tag(Path("apis.py")) is None
        assert split_lang_tag(Path("a.b.c.en.py")) == "en"


class TestDeriveSplitTwin:
    """``derive_split_twin`` / ``derive_split_pair`` — the PREFIX-AGNOSTIC,
    disk-aware twin derivation for the single-arg CLI surfaces (voiceover extract
    auto-pair, sync single-path). Unlike ``split_twin`` they work on prefix-less
    deck names too.
    """

    def test_twin_found_prefix_less(self, tmp_path: Path):
        de = tmp_path / "apis.de.py"
        en = tmp_path / "apis.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        # split_twin (prefix-gated) sees nothing; derive_split_twin does.
        assert split_twin(de) is None
        assert derive_split_twin(de) == en
        assert derive_split_twin(en) == de

    def test_twin_found_with_prefix(self, tmp_path: Path):
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        assert derive_split_twin(de) == en

    def test_twin_none_when_missing(self, tmp_path: Path):
        de = tmp_path / "apis.de.py"
        de.write_text("# de\n", encoding="utf-8")
        assert derive_split_twin(de) is None

    def test_twin_none_for_non_split(self, tmp_path: Path):
        bilingual = tmp_path / "apis.py"
        bilingual.write_text("# both\n", encoding="utf-8")
        assert derive_split_twin(bilingual) is None

    def test_pair_orders_de_first(self, tmp_path: Path):
        de = tmp_path / "apis.de.py"
        en = tmp_path / "apis.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        assert derive_split_pair(de) == (de, en)
        assert derive_split_pair(en) == (de, en)

    def test_pair_none_when_twin_missing(self, tmp_path: Path):
        en = tmp_path / "apis.en.py"
        en.write_text("# en\n", encoding="utf-8")
        assert derive_split_pair(en) is None

    def test_companion_is_not_a_deck_half(self, tmp_path: Path):
        # A voiceover companion carries a .de/.en tag but is the OUTPUT of an
        # extract, not a deck to auto-pair — deriving a twin must return None so
        # an extract pointed at a companion can't empty both companions.
        de = tmp_path / "voiceover_x.de.py"
        en = tmp_path / "voiceover_x.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        assert derive_split_twin(de) is None
        assert derive_split_pair(de) is None


class TestDeriveSplitPairFromStem:
    """``derive_split_pair_from_stem`` — the deck-stem form of the sync
    single-path contract (pass ``<deck>.py``, derive both halves)."""

    def test_stem_derives_both_halves(self, tmp_path: Path):
        de = tmp_path / "apis.de.py"
        en = tmp_path / "apis.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        stem = tmp_path / "apis.py"
        assert derive_split_pair_from_stem(stem) == (de, en)

    def test_none_when_a_half_missing(self, tmp_path: Path):
        (tmp_path / "apis.de.py").write_text("# de\n", encoding="utf-8")  # no en
        assert derive_split_pair_from_stem(tmp_path / "apis.py") is None

    def test_none_for_a_tagged_half(self, tmp_path: Path):
        # A path that is itself a .de/.en half is not a stem.
        assert derive_split_pair_from_stem(tmp_path / "apis.de.py") is None

    def test_none_for_companion_stem(self, tmp_path: Path):
        de = tmp_path / "voiceover_x.de.py"
        en = tmp_path / "voiceover_x.en.py"
        de.write_text("# de\n", encoding="utf-8")
        en.write_text("# en\n", encoding="utf-8")
        assert derive_split_pair_from_stem(tmp_path / "voiceover_x.py") is None

    def test_none_for_extensionless_path(self, tmp_path: Path):
        # An extensionless path has no program extension to build halves from;
        # it must not construct dotted ``deck.de``/``deck.en`` "halves" that the
        # pairing guard would then reject with a contradictory error.
        (tmp_path / "deck.de").write_text("# de\n", encoding="utf-8")
        (tmp_path / "deck.en").write_text("# en\n", encoding="utf-8")
        assert derive_split_pair_from_stem(tmp_path / "deck") is None
