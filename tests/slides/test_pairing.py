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
    find_split_slide_files_recursive,
    is_title_macro_cell,
    iter_split_pairs,
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


class TestFindSplitSlideFilesRecursive:
    """``find_split_slide_files_recursive`` — the prefix-agnostic enumerator
    behind ``clm slides sync DIR`` (§8a / B1)."""

    def _touch(self, *paths: Path) -> None:
        for p in paths:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# x\n", encoding="utf-8")

    def test_single_split_file_returns_itself_resolved(self, tmp_path: Path):
        f = tmp_path / "apis.de.py"
        self._touch(f)
        assert find_split_slide_files_recursive(f) == [f.resolve()]

    def test_single_non_split_file_returns_empty(self, tmp_path: Path):
        f = tmp_path / "apis.py"  # no .de/.en tag
        self._touch(f)
        assert find_split_slide_files_recursive(f) == []

    def test_finds_prefix_less_and_prefixed_halves(self, tmp_path: Path):
        a_de, a_en = tmp_path / "apis.de.py", tmp_path / "apis.en.py"
        s_de, s_en = tmp_path / "slides_x.de.py", tmp_path / "slides_x.en.py"
        self._touch(a_de, a_en, s_de, s_en)
        found = find_split_slide_files_recursive(tmp_path)
        assert set(found) == {a_de.resolve(), a_en.resolve(), s_de.resolve(), s_en.resolve()}

    def test_descends_full_subtree_no_early_exit(self, tmp_path: Path):
        # A stray top-level deck must NOT stop the walk from reaching nested dirs
        # (the silent-miss trap that the prefix-gated early-exit helper falls into).
        top_de, top_en = tmp_path / "top.de.py", tmp_path / "top.en.py"
        nested_de = tmp_path / "mod" / "topic" / "deep.de.py"
        nested_en = tmp_path / "mod" / "topic" / "deep.en.py"
        self._touch(top_de, top_en, nested_de, nested_en)
        found = set(find_split_slide_files_recursive(tmp_path))
        assert nested_de.resolve() in found and nested_en.resolve() in found
        assert top_de.resolve() in found

    def test_excludes_voiceover_companions(self, tmp_path: Path):
        self._touch(
            tmp_path / "apis.de.py",
            tmp_path / "apis.en.py",
            tmp_path / "voiceover_apis.de.py",
            tmp_path / "voiceover_apis.en.py",
        )
        found = find_split_slide_files_recursive(tmp_path)
        assert all("voiceover_" not in p.name for p in found)
        assert len(found) == 2

    def test_excludes_unsupported_extensions_and_bilingual(self, tmp_path: Path):
        self._touch(
            tmp_path / "apis.de.py",
            tmp_path / "apis.en.py",
            tmp_path / "notes.de.txt",  # unsupported extension
            tmp_path / "apis.py",  # bilingual stem (no tag)
        )
        found = find_split_slide_files_recursive(tmp_path)
        assert {p.name for p in found} == {"apis.de.py", "apis.en.py"}

    def test_other_languages_supported(self, tmp_path: Path):
        c_de, c_en = tmp_path / "demo.de.cpp", tmp_path / "demo.en.cpp"
        self._touch(c_de, c_en)
        assert set(find_split_slide_files_recursive(tmp_path)) == {c_de.resolve(), c_en.resolve()}

    def test_missing_path_returns_empty(self, tmp_path: Path):
        assert find_split_slide_files_recursive(tmp_path / "nope") == []

    def test_excludes_pairs_under_ignored_dirs(self, tmp_path: Path):
        # A vendored/archived deck under .git/.venv/build/__pycache__ must never be
        # enumerated (and thus never written on a directory apply).
        legit_de = tmp_path / "module_x" / "topic_a" / "apis.de.py"
        legit_en = tmp_path / "module_x" / "topic_a" / "apis.en.py"
        self._touch(legit_de, legit_en)
        for ignored in (".venv", ".git", "build", "__pycache__", "dist"):
            self._touch(tmp_path / ignored / "vend.de.py", tmp_path / ignored / "vend.en.py")
        found = find_split_slide_files_recursive(tmp_path)
        assert set(found) == {legit_de.resolve(), legit_en.resolve()}

    def test_root_under_ignored_component_still_enumerates(self, tmp_path: Path):
        # The ignored-dir test is applied RELATIVE to the root, so a root that
        # itself lives under a dir named like an ignored one (e.g. ``build/``)
        # must not falsely exclude its own decks.
        root = tmp_path / "build" / "decks"
        de, en = root / "apis.de.py", root / "apis.en.py"
        self._touch(de, en)
        found = find_split_slide_files_recursive(root)
        assert set(found) == {de.resolve(), en.resolve()}

    def test_named_ignored_file_still_honoured(self, tmp_path: Path):
        # The single-file branch is exempt from the ignored-dir prune: an
        # explicitly named half is always honoured, even under .venv.
        f = tmp_path / ".venv" / "apis.de.py"
        self._touch(f)
        assert find_split_slide_files_recursive(f) == [f.resolve()]


class TestIterSplitPairs:
    """``iter_split_pairs`` — partition split halves into ordered pairs + solos
    (§8a / B2)."""

    def _touch(self, *paths: Path) -> list[Path]:
        for p in paths:
            p.write_text("# x\n", encoding="utf-8")
        return list(paths)

    def test_pairs_ordered_de_first(self, tmp_path: Path):
        de, en = tmp_path / "apis.de.py", tmp_path / "apis.en.py"
        self._touch(de, en)
        pairs, solos = iter_split_pairs([en, de])  # en passed first
        assert pairs == [(de, en)]  # still (de, en)-ordered
        assert solos == []

    def test_solo_half_with_no_twin_is_isolated(self, tmp_path: Path):
        de, en = tmp_path / "apis.de.py", tmp_path / "apis.en.py"
        orphan = tmp_path / "orphan.de.py"
        self._touch(de, en, orphan)
        pairs, solos = iter_split_pairs([de, en, orphan])
        assert pairs == [(de, en)]
        assert solos == [orphan]

    def test_twin_on_disk_but_not_in_paths_is_solo(self, tmp_path: Path):
        # The twin exists on disk but was not handed in (caller passed a subset):
        # it must be reported solo, not silently pulled in from outside the set.
        de, en = tmp_path / "apis.de.py", tmp_path / "apis.en.py"
        self._touch(de, en)
        pairs, solos = iter_split_pairs([de])  # en omitted from the input set
        assert pairs == []
        assert solos == [de]

    def test_multiple_pairs_deterministic_order(self, tmp_path: Path):
        a_de, a_en = tmp_path / "apis.de.py", tmp_path / "apis.en.py"
        w_de, w_en = tmp_path / "web.de.py", tmp_path / "web.en.py"
        self._touch(a_de, a_en, w_de, w_en)
        pairs, solos = iter_split_pairs([w_en, a_de, w_de, a_en])
        assert pairs == [(a_de, a_en), (w_de, w_en)]  # sorted-input order
        assert solos == []

    def test_every_path_lands_in_exactly_one_list(self, tmp_path: Path):
        de, en = tmp_path / "apis.de.py", tmp_path / "apis.en.py"
        orphan = tmp_path / "orphan.en.py"
        self._touch(de, en, orphan)
        pairs, solos = iter_split_pairs([de, en, orphan])
        flattened = [p for pair in pairs for p in pair] + solos
        assert sorted(flattened) == sorted([de, en, orphan])
