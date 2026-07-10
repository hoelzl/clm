"""Golden law suite for :mod:`clm.slides.sync_diff` (#520 Phase 2).

One shape per test, following the ``test_doc_lenses.py`` template: build a
tiny bundle, snapshot it as the baseline, apply one authoring action, and
assert the diff reports **exactly one correctly-classified item** (the
noise-floor contract that motivates the whole v3 core — design §1 goal 1).

The §7.4 transition-matrix walk and the §6.3 field-coverage test live in
``test_sync_diff_matrix.py``; this file pins each row's shape and direction
individually so a classification regression names the broken row.
"""

from __future__ import annotations

import pytest

from clm.slides.bilingual_doc import BilingualDeck
from clm.slides.doc_lenses import parse_bundle
from clm.slides.sync_diff import (
    DeckBaseline,
    DeckDiff,
    baseline_from_deck,
    diff_deck,
    diff_outcome,
)

# ---------------------------------------------------------------------------
# Builders (the test_doc_lenses.py conventions)
# ---------------------------------------------------------------------------

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _shared_code(name: str, value: int = 1) -> str:
    return f'# %% tags=["keep"]\n{name} = {value}\n\n'


def _companion_cell(slug: str, lang: str, owner: str, text: str, tag: str = "notes") -> str:
    return (
        f'# %% [markdown] lang="{lang}" tags=["{tag}"] for_slide="{owner}" '
        f'slide_id="{slug}"\n#\n# - {text}\n\n'
    )


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


def _parse(de: str, en: str, de_c: str | None = None, en_c: str | None = None) -> BilingualDeck:
    outcome = parse_bundle(de, en, de_c, en_c)
    assert outcome.deck is not None, outcome.refusal.render() if outcome.refusal else "parse failed"
    return outcome.deck


def _snapshot(de: str, en: str, de_c: str | None = None, en_c: str | None = None) -> DeckBaseline:
    return baseline_from_deck(_parse(de, en, de_c, en_c))


def _diff(
    base: DeckBaseline,
    de: str,
    en: str,
    de_c: str | None = None,
    en_c: str | None = None,
) -> DeckDiff:
    return diff_outcome(parse_bundle(de, en, de_c, en_c), base)


def _only_item(diff: DeckDiff):
    assert len(diff.items) == 1, [(i.outcome, i.action, i.key, i.detail) for i in diff.items]
    return diff.items[0]


# The canonical two-group deck every shared/localized test mutates.
DE0 = _build(
    HEADER_DE,
    _slide("s0", "de", "Titel"),
    _shared_code("x"),
    _shared_code("y", 2),
    _localized("s0-m", "de", "DE Text"),
)
EN0 = _build(
    HEADER_EN,
    _slide("s0", "en", "Title"),
    _shared_code("x"),
    _shared_code("y", 2),
    _localized("s0-m", "en", "EN text"),
)


class TestNoopAndCold:
    def test_noop_is_clean(self):
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, EN0)
        assert diff.is_clean
        assert diff.items == []
        assert diff.in_sync_count == 6  # title, s0, x, y, s0-m, + header zone-free

    def test_diff_is_deterministic(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace("x = 1", "x = 2")
        first = _diff(base, de, EN0)
        second = _diff(base, de, EN0)
        assert [(i.key, i.action) for i in first.items] == [(i.key, i.action) for i in second.items]

    def test_no_baseline_means_every_member_is_cold(self):
        deck = _parse(DE0, EN0)
        diff = diff_deck(deck, None)
        assert diff.items
        assert {i.outcome for i in diff.items} == {"unverified"}
        assert {i.action for i in diff.items} == {"verify_cold"}

    def test_incomplete_baseline_reports_unknown_member_as_cold_not_add(self):
        base = _snapshot(DE0, EN0)
        base.complete = False
        de = DE0.replace(
            '# %% [markdown] lang="de" slide_id="s0-m"',
            _localized("s0-n", "de", "Neu").rstrip("\n") + "\n\n"
            '# %% [markdown] lang="de" slide_id="s0-m"',
        )
        en = EN0.replace(
            '# %% [markdown] lang="en" slide_id="s0-m"',
            _localized("s0-n", "en", "New").rstrip("\n") + "\n\n"
            '# %% [markdown] lang="en" slide_id="s0-m"',
        )
        diff = _diff(base, de, en)
        item = _only_item(diff)
        assert item.outcome == "unverified"
        assert item.action == "verify_cold"
        assert item.key == "id:s0-n"

    def test_refusal_becomes_framed_deck_outcome(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(' slide_id="s0-m"', "")
        en = EN0.replace(' slide_id="s0-m"', "")
        diff = _diff(base, de, en)
        assert diff.refusal is not None
        assert not diff.is_clean
        assert diff.needs_agent
        assert {r.code for r in diff.refusal.reasons} == {"idless_localized"}


class TestSharedRows:
    def test_one_sided_edit_propagates_de_to_en(self):
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0.replace("x = 1", "x = 99"), EN0)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("mechanical", "propagate_shared_edit")
        assert item.direction == "de_to_en"
        assert item.side == "de"

    def test_one_sided_edit_propagates_en_to_de(self):
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, EN0.replace("x = 1", "x = 99"))
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("mechanical", "propagate_shared_edit")
        assert item.direction == "en_to_de"

    def test_identical_edits_on_both_sides_record(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace("x = 1", "x = 99")
        en = EN0.replace("x = 1", "x = 99")
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("mechanical", "record_symmetric_edit")
        assert item.direction == "both"

    def test_diverging_edits_conflict(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace("x = 1", "x = 98")
        en = EN0.replace("x = 1", "x = 99")
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("conflict", "conflict_shared")
        assert item.direction == "both"

    def test_divergence_already_present_at_base_is_pending_not_silent(self):
        de = DE0.replace("x = 1", "x = 98")
        base = _snapshot(de, EN0)  # the baseline itself carries the divergence
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("conflict", "pending_divergence")
        assert item.direction == "none"

    def test_one_sided_insert_is_one_add_not_a_cascade(self):
        """The W10 noise shape: an insert shifts the twin pairing of every
        later sibling, but base alignment keeps it ONE item."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\nx = 1', '# %%\nnew = 0\n\n# %% tags=["keep"]\nx = 1')
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("add", "copy_new_shared")
        assert item.direction == "de_to_en"

    def test_identical_insert_on_both_sides_records(self):
        base = _snapshot(DE0, EN0)
        insertion = '# %%\nnew = 0\n\n# %% tags=["keep"]\nx = 1'
        de = DE0.replace('# %% tags=["keep"]\nx = 1', insertion)
        en = EN0.replace('# %% tags=["keep"]\nx = 1', insertion)
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("add", "record_symmetric_add")

    def test_different_inserts_on_both_sides_are_framed(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% tags=["keep"]\nx = 1', '# %%\nnew_de = 0\n\n# %% tags=["keep"]\nx = 1'
        )
        en = EN0.replace(
            '# %% tags=["keep"]\nx = 1', '# %%\nnew_en = 0\n\n# %% tags=["keep"]\nx = 1'
        )
        # The parse pairs the two inserts into ONE member (same slot):
        # exactly one framed row, mirroring the id-keyed analogue — never
        # two duplicate items for one divergence.
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("conflict", "conflict_shared")

    def test_one_sided_remove_mirrors_and_is_surfaced(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\ny = 2\n\n', "")
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("remove", "mirror_remove")
        assert item.direction == "de_to_en"

    def test_remove_on_both_sides_records(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\ny = 2\n\n', "")
        en = EN0.replace('# %% tags=["keep"]\ny = 2\n\n', "")
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("remove", "record_remove")

    def test_remove_vs_edit_is_framed(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\ny = 2\n\n', "")
        en = EN0.replace("y = 2", "y = 3")
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("conflict", "remove_vs_edit")

    def test_reorder_on_one_side_mirrors_order(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% tags=["keep"]\nx = 1\n\n# %% tags=["keep"]\ny = 2',
            '# %% tags=["keep"]\ny = 2\n\n# %% tags=["keep"]\nx = 1',
        )
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("order", "mirror_order")
        assert item.direction == "de_to_en"

    def test_tags_only_change_mirrors_tags(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep", "alt"]\nx = 1')
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("mechanical", "mirror_tags")
        assert item.direction == "de_to_en"


class TestLocalizedRows:
    def test_one_sided_edit_frames_translation(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace("# DE Text", "# DE Text v2")
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("edit", "translate_edit")
        assert item.direction == "de_to_en"
        assert item.side == "de"

    def test_both_sides_moved_frames_verification(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace("# DE Text", "# DE Text v2")
        en = EN0.replace("# EN text", "# EN text v2")
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("conflict", "verify_translation")

    def test_new_localized_member_frames_translation(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% [markdown] lang="de" slide_id="s0-m"',
            _localized("s0-n", "de", "Nur DE").rstrip("\n") + "\n\n"
            '# %% [markdown] lang="de" slide_id="s0-m"',
        )
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("add", "translate_new")
        assert item.direction == "de_to_en"

    def test_tags_only_change_on_localized_mirrors_tags(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% [markdown] lang="de" slide_id="s0-m"',
            '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-m"',
        )
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("mechanical", "mirror_tags")
        assert item.direction == "de_to_en"

    def test_identical_tag_change_on_both_sides_records(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% [markdown] lang="de" slide_id="s0-m"',
            '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-m"',
        )
        en = EN0.replace(
            '# %% [markdown] lang="en" slide_id="s0-m"',
            '# %% [markdown] lang="en" tags=["notes"] slide_id="s0-m"',
        )
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("mechanical", "record_tags")

    def test_deleted_variant_without_twin_is_framed(self):
        base = _snapshot(DE0, EN0)
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            _shared_code("y", 2),
        )
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("conflict", "remove_localized_side")
        assert item.side == "de"

    def test_header_edit_is_a_localized_edit_never_a_unify(self):
        """Headers are per-language BY DESIGN (§3.1) — a header edit must
        classify as a localized edit, not as a langness transition."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace('header_de("Titel DE")', 'header_de("Titel DE v2")')
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("edit", "translate_edit")
        assert item.key == "id:title"


class TestTransitions:
    DE_Y = DE0.replace('# %% tags=["keep"]\ny = 2', '# %% lang="de" slide_id="y-cell"\ny = 2')
    EN_Y = EN0.replace('# %% tags=["keep"]\ny = 2', '# %% lang="en" slide_id="y-cell"\ny = 2')

    def test_pure_fork_records(self):
        base = _snapshot(DE0, EN0)
        item = _only_item(_diff(base, self.DE_Y, self.EN_Y))
        assert (item.outcome, item.action) == ("transition", "record_fork")
        assert item.key == "id:y-cell"

    def test_fork_with_one_sided_edit_still_records(self):
        base = _snapshot(DE0, EN0)
        en = EN0.replace(
            '# %% tags=["keep"]\ny = 2', '# %% lang="en" slide_id="y-cell"\ny = 2  # EN'
        )
        item = _only_item(_diff(base, self.DE_Y, en))
        assert (item.outcome, item.action) == ("transition", "record_fork")

    def test_mid_fork_absorbs_the_unmarked_twin(self):
        """One half marked (lang + id), the twin untouched: exactly one
        framed transition — and never a ``copy_new_shared`` that would
        duplicate the twin cell on apply."""
        base = _snapshot(DE0, EN0)
        item = _only_item(_diff(base, self.DE_Y, EN0))
        assert (item.outcome, item.action) == ("transition", "fork_pending_twin")
        assert item.direction == "de_to_en"

    def test_mid_fork_on_paired_ids_frames_the_twin(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\ny = 2', '# %% lang="de" slide_id="y-cell"\ny = 2')
        en = EN0.replace('# %% tags=["keep"]\ny = 2', '# %% slide_id="y-cell"\ny = 2')
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("transition", "fork_pending_twin")
        assert item.side == "de"

    def test_unify_with_equal_bodies_records(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% [markdown] lang="de" slide_id="s0-m"\n# DE Text',
            '# %% [markdown] slide_id="s0-m"\n# same',
        )
        en = EN0.replace(
            '# %% [markdown] lang="en" slide_id="s0-m"\n# EN text',
            '# %% [markdown] slide_id="s0-m"\n# same',
        )
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("transition", "record_unify")
        assert item.key == "id:s0-m"

    def test_unify_with_diverging_bodies_frames_the_choice(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% [markdown] lang="de" slide_id="s0-m"', '# %% [markdown] slide_id="s0-m"'
        )
        en = EN0.replace(
            '# %% [markdown] lang="en" slide_id="s0-m"', '# %% [markdown] slide_id="s0-m"'
        )
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("transition", "unify_choose_body")

    def test_mid_unify_attr_dropped_on_one_side_frames_the_twin(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% [markdown] lang="de" slide_id="s0-m"', '# %% [markdown] slide_id="s0-m"'
        )
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("transition", "unify_pending_twin")
        assert item.direction == "de_to_en"

    def test_mid_unify_attr_and_id_dropped_absorbs_the_pos_twin(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% [markdown] lang="de" slide_id="s0-m"', "# %% [markdown]")
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("transition", "unify_pending_twin")

    def test_443_one_sided_id_strip_stamps_the_twin(self):
        base = _snapshot(DE0, EN0)
        en = EN0.replace('# %% [markdown] lang="en" slide_id="s0-m"', '# %% [markdown] lang="en"')
        item = _only_item(_diff(base, DE0, en))
        assert (item.outcome, item.action) == ("transition", "stamp_twin_id")
        assert item.side == "en"
        assert item.direction == "de_to_en"

    def test_id_stamped_on_both_sides_migrates_the_key(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        en = EN0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("transition", "record_key_migration")
        assert item.key == "id:x-cell"
        assert "pos:s0/code/0" in item.detail

    def test_group_rename_with_unchanged_anchor_records(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace('slide_id="s0"', 'slide_id="s0-renamed"')
        en = EN0.replace('slide_id="s0"', 'slide_id="s0-renamed"')
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("transition", "record_group_rename")
        assert item.key == "id:s0-renamed"


class TestCompanions:
    DE_C = _build(_companion_cell("s0-vo", "de", "s0", "DE Notiz"))
    EN_C = _build(_companion_cell("s0-vo", "en", "s0", "EN note"))

    def test_companion_noop_is_clean(self):
        base = _snapshot(DE0, EN0, self.DE_C, self.EN_C)
        diff = _diff(base, DE0, EN0, self.DE_C, self.EN_C)
        assert diff.is_clean, [(i.action, i.key) for i in diff.items]

    def test_companion_edit_frames_translation(self):
        base = _snapshot(DE0, EN0, self.DE_C, self.EN_C)
        de_c = self.DE_C.replace("DE Notiz", "DE Notiz v2")
        item = _only_item(_diff(base, DE0, EN0, de_c, self.EN_C))
        assert (item.outcome, item.action) == ("edit", "translate_edit")
        assert item.key == "id:s0-vo"
        assert item.direction == "de_to_en"

    DE_TWO_GROUPS = _build(HEADER_DE, _slide("s0", "de", "Eins"), _slide("s1", "de", "Zwei"))
    EN_TWO_GROUPS = _build(HEADER_EN, _slide("s0", "en", "One"), _slide("s1", "en", "Two"))

    def test_owner_change_on_both_sides_records(self):
        base = _snapshot(self.DE_TWO_GROUPS, self.EN_TWO_GROUPS, self.DE_C, self.EN_C)
        de_c = self.DE_C.replace('for_slide="s0"', 'for_slide="s1"')
        en_c = self.EN_C.replace('for_slide="s0"', 'for_slide="s1"')
        item = _only_item(_diff(base, self.DE_TWO_GROUPS, self.EN_TWO_GROUPS, de_c, en_c))
        assert (item.outcome, item.action) == ("mechanical", "record_owner")

    def test_owner_disagreement_is_framed(self):
        base = _snapshot(self.DE_TWO_GROUPS, self.EN_TWO_GROUPS, self.DE_C, self.EN_C)
        de_c = self.DE_C.replace('for_slide="s0"', 'for_slide="s1"')
        item = _only_item(_diff(base, self.DE_TWO_GROUPS, self.EN_TWO_GROUPS, de_c, self.EN_C))
        assert (item.outcome, item.action) == ("conflict", "conflict_owner")

    def test_broken_owner_is_framed(self):
        base = _snapshot(DE0, EN0, self.DE_C, self.EN_C)
        de_c = self.DE_C.replace('for_slide="s0"', 'for_slide="ghost"')
        en_c = self.EN_C.replace('for_slide="s0"', 'for_slide="ghost"')
        diff = _diff(base, DE0, EN0, de_c, en_c)
        assert {i.action for i in diff.items} == {"broken_owner"}

    def test_relayout_on_one_half_mirrors(self):
        """The same narrative id inline on DE and in the EN companion: one
        mechanical mirror item (the #501 shape as a §7.3 transition)."""
        de_inline = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-vo"\n#\n# - DE Notiz\n\n',
        )
        en_deck = _build(HEADER_EN, _slide("s0", "en", "Title"))
        de_base = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
        )
        base = _snapshot(de_base, en_deck, self.DE_C, self.EN_C)
        diff = _diff(base, de_inline, en_deck, "", self.EN_C)
        assert [i.action for i in diff.items] == ["mirror_layout"], [
            (i.outcome, i.action, i.key, i.detail) for i in diff.items
        ]
        item = diff.items[0]
        assert item.outcome == "transition"
        assert item.side == "de"


class TestOrderAndMoves:
    DE2 = _build(
        HEADER_DE,
        _slide("s0", "de", "Eins"),
        _localized("m", "de", "DE"),
        _slide("s1", "de", "Zwei"),
        _localized("n", "de", "DE2"),
    )
    EN2 = _build(
        HEADER_EN,
        _slide("s0", "en", "One"),
        _localized("m", "en", "EN"),
        _slide("s1", "en", "Two"),
        _localized("n", "en", "EN2"),
    )

    def test_cross_group_move_on_one_side_mirrors(self):
        base = _snapshot(self.DE2, self.EN2)
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "Eins"),
            _slide("s1", "de", "Zwei"),
            _localized("m", "de", "DE"),
            _localized("n", "de", "DE2"),
        )
        item = _only_item(_diff(base, de, self.EN2))
        assert (item.outcome, item.action) == ("order", "mirror_order")
        assert item.key == "id:m"
        assert item.direction == "de_to_en"

    def test_agreed_cross_group_move_is_clean(self):
        base = _snapshot(self.DE2, self.EN2)
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "Eins"),
            _slide("s1", "de", "Zwei"),
            _localized("m", "de", "DE"),
            _localized("n", "de", "DE2"),
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "One"),
            _slide("s1", "en", "Two"),
            _localized("m", "en", "EN"),
            _localized("n", "en", "EN2"),
        )
        assert _diff(base, de, en).is_clean

    def test_group_reorder_on_one_side_mirrors(self):
        base = _snapshot(self.DE2, self.EN2)
        de = _build(
            HEADER_DE,
            _slide("s1", "de", "Zwei"),
            _localized("n", "de", "DE2"),
            _slide("s0", "de", "Eins"),
            _localized("m", "de", "DE"),
        )
        diff = _diff(base, de, self.EN2)
        assert {(i.outcome, i.action) for i in diff.items} == {("order", "mirror_order")}
        assert all(i.direction == "de_to_en" for i in diff.items)


class TestPreambles:
    def test_preamble_edit_on_one_side_propagates(self):
        base = _snapshot("# preamble\n" + DE0, "# preamble\n" + EN0)
        diff = _diff(base, "# preamble v2\n" + DE0, "# preamble\n" + EN0)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("mechanical", "propagate_preamble")
        assert item.direction == "de_to_en"

    def test_identical_preamble_edits_record(self):
        base = _snapshot("# preamble\n" + DE0, "# preamble\n" + EN0)
        diff = _diff(base, "# p2\n" + DE0, "# p2\n" + EN0)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("mechanical", "record_preamble")

    def test_diverging_preamble_edits_conflict(self):
        base = _snapshot("# preamble\n" + DE0, "# preamble\n" + EN0)
        diff = _diff(base, "# p-de\n" + DE0, "# p-en\n" + EN0)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("conflict", "conflict_preamble")


class TestEnvelope:
    def test_payload_is_schema_3_with_stable_booleans(self):
        base = _snapshot(DE0, EN0)
        payload = _diff(base, DE0.replace("# DE Text", "# DE v2"), EN0).to_payload()
        assert payload["schema"] == 3
        assert payload["engine"] == "v3"
        assert payload["is_clean"] is False
        assert payload["needs_model"] is True  # translate_edit is model-frameable
        assert payload["needs_agent"] is False
        assert payload["counts"] == {"edit": 1}
        (item,) = payload["items"]
        assert item["key"] == "id:s0-m"
        assert "# DE v2" in item["de"]  # excerpts are structurally free

    def test_mechanical_only_diff_needs_nobody(self):
        base = _snapshot(DE0, EN0)
        payload = _diff(base, DE0.replace("x = 1", "x = 9"), EN0).to_payload()
        assert payload["needs_model"] is False
        assert payload["needs_agent"] is False

    def test_conflict_needs_agent(self):
        base = _snapshot(DE0, EN0)
        payload = _diff(
            base, DE0.replace("x = 1", "x = 8"), EN0.replace("x = 1", "x = 9")
        ).to_payload()
        assert payload["needs_agent"] is True

    def test_refusal_payload_carries_reasons(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(' slide_id="s0-m"', "")
        en = EN0.replace(' slide_id="s0-m"', "")
        payload = _diff(base, de, en).to_payload()
        assert payload["is_clean"] is False
        assert payload["refusal"]["reasons"][0]["code"] == "idless_localized"


class TestNoiseFloor:
    """The design §1 goal-1 contract on a realistic multi-group deck."""

    DE = _build(
        HEADER_DE,
        _slide("a", "de", "A"),
        _shared_code("a1"),
        _localized("a-m", "de", "DE A"),
        _slide("b", "de", "B"),
        _shared_code("b1"),
        _shared_code("b2", 2),
        _localized("b-m", "de", "DE B"),
        _slide("c", "de", "C"),
        _localized("c-m", "de", "DE C"),
    )
    EN = _build(
        HEADER_EN,
        _slide("a", "en", "A"),
        _shared_code("a1"),
        _localized("a-m", "en", "EN A"),
        _slide("b", "en", "B"),
        _shared_code("b1"),
        _shared_code("b2", 2),
        _localized("b-m", "en", "EN B"),
        _slide("c", "en", "C"),
        _localized("c-m", "en", "EN C"),
    )

    def test_three_scattered_edits_yield_exactly_three_items(self):
        base = _snapshot(self.DE, self.EN)
        de = self.DE.replace("a1 = 1", "a1 = 2").replace("# DE B", "# DE B v2")
        en = self.EN.replace("# EN C", "# EN C v2")
        diff = _diff(base, de, en)
        assert len(diff.items) == 3
        assert {(i.key, i.action) for i in diff.items} == {
            ("pos:a/code/0", "propagate_shared_edit"),
            ("id:b-m", "translate_edit"),
            ("id:c-m", "translate_edit"),
        }

    def test_pipeline_survives_pool_shift_plus_edit(self):
        """Insert before an edited sibling in the same pool: both classified,
        nothing cascades."""
        base = _snapshot(self.DE, self.EN)
        de = self.DE.replace(
            '# %% tags=["keep"]\nb1 = 1', '# %%\nnew = 0\n\n# %% tags=["keep"]\nb1 = 1'
        ).replace("b2 = 2", "b2 = 3")
        diff = _diff(base, de, self.EN)
        assert {(i.action, i.outcome) for i in diff.items} == {
            ("copy_new_shared", "add"),
            ("propagate_shared_edit", "mechanical"),
        }


@pytest.mark.parametrize("side", ["de", "en"])
def test_every_direction_is_member_local(side: str):
    """Design §6.2: direction is per member — two opposite one-sided edits
    in one deck get opposite directions, no deck-level inference."""
    base = _snapshot(DE0, EN0)
    de = DE0.replace("x = 1", "x = 9")  # DE edit on x
    en = EN0.replace("y = 2", "y = 9")  # EN edit on y
    diff = _diff(base, de, en)
    directions = {i.key: i.direction for i in diff.items}
    assert directions == {
        "pos:s0/code/0": "de_to_en",
        "pos:s0/code/1": "en_to_de",
    }


class TestAdversarialReviewRegressions:
    """Shapes from the Phase 2 pre-merge adversarial review (30 raw → 25
    confirmed findings, every one with a verified repro). Each test pins one
    fixed defect class; the common theme is P8: a state the engine cannot
    resolve safely must FRAME, never emit a mechanical action that could
    lose or duplicate content on apply."""

    def test_base_carried_divergence_never_propagates_mechanically(self):
        """CRITICAL: an id-keyed shared member whose baseline already carried
        a byte divergence must not read the unchanged twin as 'edited'."""
        de = DE0.replace(
            '# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 98'
        )
        en = EN0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        base = _snapshot(de, en)
        item = _only_item(_diff(base, de, en))  # unchanged input
        assert (item.outcome, item.action) == ("conflict", "pending_divergence")
        assert item.direction == "none"

    def test_base_diverged_plus_one_sided_edit_stays_framed(self):
        de = DE0.replace(
            '# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 98'
        )
        en = EN0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        base = _snapshot(de, en)
        item = _only_item(_diff(base, de.replace("x = 98", "x = 99"), en))
        assert item.outcome == "conflict"
        assert item.action in ("pending_divergence", "conflict_shared")

    def test_carried_pending_twin_never_becomes_mirror_remove(self):
        """CRITICAL: an inline notes cell present on DE only, byte-identical
        to the companion cells — the phantom-slot steal shape. Unchanged
        input must never yield a destructive mechanical remove."""
        notes = '# %% [markdown] tags=["notes"] for_slide="s0"\n#\n# - Note text\n\n'
        de = _build(HEADER_DE, _slide("s0", "de", "Titel"), notes)
        en = _build(HEADER_EN, _slide("s0", "en", "Title"))
        comp = notes.rstrip("\n") + "\n"
        base = _snapshot(de, en, comp, comp)
        diff = _diff(base, de, en, comp, comp)
        assert not any(i.action == "mirror_remove" for i in diff.items)
        assert [(i.outcome, i.action) for i in diff.items] == [("add", "copy_new_shared")]

    def test_non_adjacent_reorder_is_one_order_item(self):
        """MAJOR: [x,y,z,w] → [y,x,w,z] on DE must be one mirror_order, not
        an edit+remove+add cascade of false content rows."""
        cells = "".join(_shared_code(n, i + 1) for i, n in enumerate("xyzw"))
        de = _build(HEADER_DE, _slide("s0", "de", "T"), cells)
        en = _build(HEADER_EN, _slide("s0", "en", "T"), cells)
        base = _snapshot(de, en)
        reordered = (
            _shared_code("y", 2)
            + _shared_code("x", 1)
            + _shared_code("w", 4)
            + _shared_code("z", 3)
        )
        de2 = _build(HEADER_DE, _slide("s0", "de", "T"), reordered)
        item = _only_item(_diff(base, de2, en))
        assert (item.outcome, item.action) == ("order", "mirror_order")
        assert item.direction == "de_to_en"

    def test_identical_pool_reorder_on_both_sides_records(self):
        cells = _shared_code("x", 1) + _shared_code("y", 2)
        de = _build(HEADER_DE, _slide("s0", "de", "T"), cells)
        en = _build(HEADER_EN, _slide("s0", "en", "T"), cells)
        base = _snapshot(de, en)
        swapped = _shared_code("y", 2) + _shared_code("x", 1)
        de2 = _build(HEADER_DE, _slide("s0", "de", "T"), swapped)
        en2 = _build(HEADER_EN, _slide("s0", "en", "T"), swapped)
        item = _only_item(_diff(base, de2, en2))
        assert (item.outcome, item.action) == ("order", "record_order")

    def test_pool_move_handles_are_kind_unique(self):
        """MINOR: markdown and code pools of one group moving on opposite
        sides must not collide on one handle with contradictory directions."""
        md = "# %% [markdown]\n# alpha\n\n# %% [markdown]\n# beta\n\n"
        code = _shared_code("x", 1) + _shared_code("y", 2)
        de = _build(HEADER_DE, _slide("s0", "de", "T"), md, code)
        en = _build(HEADER_EN, _slide("s0", "en", "T"), md, code)
        base = _snapshot(de, en)
        md_swapped = "# %% [markdown]\n# beta\n\n# %% [markdown]\n# alpha\n\n"
        code_swapped = _shared_code("y", 2) + _shared_code("x", 1)
        de2 = _build(HEADER_DE, _slide("s0", "de", "T"), md_swapped, code)
        en2 = _build(HEADER_EN, _slide("s0", "en", "T"), md, code_swapped)
        diff = _diff(base, de2, en2)
        keys = [i.key for i in diff.items]
        assert len(keys) == len(set(keys)), keys
        assert {i.action for i in diff.items} == {"mirror_order"}
        assert {i.direction for i in diff.items} == {"de_to_en", "en_to_de"}

    def test_insert_straddling_id_member_emits_no_false_order_row(self):
        """MAJOR (ordinal aliasing): a one-sided insert whose pool straddles
        an id'd member must not manufacture an order row."""
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "T"),
            _shared_code("a", 1),
            _localized("s0-m", "de", "DE"),
            _shared_code("b", 2),
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "T"),
            _shared_code("a", 1),
            _localized("s0-m", "en", "EN"),
            _shared_code("b", 2),
        )
        base = _snapshot(de, en)
        de2 = de.replace('# %% tags=["keep"]\na = 1', '# %%\nn = 0\n\n# %% tags=["keep"]\na = 1')
        item = _only_item(_diff(base, de2, en))
        assert (item.outcome, item.action) == ("add", "copy_new_shared")

    def test_carried_order_divergence_is_framed_not_mirrored(self):
        """MAJOR: sides that already disagreed about id-member order at base
        must not diff as a fresh EN reorder (the DE-biased merged order)."""
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "T"),
            _localized("m1", "de", "eins"),
            _localized("m2", "de", "zwei"),
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "T"),
            _localized("m2", "en", "two"),
            _localized("m1", "en", "one"),
        )
        base = _snapshot(de, en)
        diff = _diff(base, de, en)  # unchanged input
        assert [(i.outcome, i.action, i.direction) for i in diff.items] == [
            ("order", "order_decision", "none")
        ]

    def test_carried_one_sided_group_fires_no_cross_group_mirror(self):
        """MAJOR: a group existing on one half only (carried at base) must
        not read as a member move on the other half."""
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "T"),
            _localized("m0", "de", "null"),
            _slide("s1", "de", "T2"),
            _localized("x1", "de", "eins"),
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "T"),
            _localized("m0", "en", "zero"),
            _localized("x1", "en", "one"),
        )
        base = _snapshot(de, en)
        diff = _diff(base, de, en)  # unchanged input
        assert not any(i.action == "mirror_order" for i in diff.items)

    def test_conflicting_id_stamps_frame_instead_of_deleting(self):
        """MINOR: the halves stamping different ids onto the same cell must
        yield one framed decision, never mirror_remove + copy."""
        shared = "# %% [markdown]\n# Shared text\n\n"
        de = _build(HEADER_DE, _slide("s0", "de", "T"), shared)
        en = _build(HEADER_EN, _slide("s0", "en", "T"), shared)
        base = _snapshot(de, en)
        de2 = de.replace("# %% [markdown]\n# Shared", '# %% [markdown] slide_id="ida"\n# Shared')
        en2 = en.replace("# %% [markdown]\n# Shared", '# %% [markdown] slide_id="idb"\n# Shared')
        diff = _diff(base, de2, en2)
        assert all(i.action == "ambiguous_alignment" for i in diff.items), [
            (i.outcome, i.action, i.key) for i in diff.items
        ]
        assert not any(i.action in ("mirror_remove", "copy_new_shared") for i in diff.items)

    def test_mid_stamp_with_edited_twin_is_fully_framed(self):
        """MAJOR (#443 + edit): DE stamps an id while EN edits the same
        id-less cell — no mechanical row may revert the stamp or copy."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\ny = 2', '# %% tags=["keep"] slide_id="y-cell"\ny = 2')
        en = EN0.replace("y = 2", "y = 99")
        diff = _diff(base, de, en)
        assert diff.items and all(i.outcome == "conflict" for i in diff.items), [
            (i.outcome, i.action, i.key) for i in diff.items
        ]

    def test_clean_group_rename_rehomes_companions_quietly(self):
        """MAJOR: a consistent anchor-id rename (slide_id + every for_slide)
        must yield exactly the rename transition — no verify_translation
        noise on the companions."""
        comp_de = (
            '# %% [markdown] lang="de" tags=["notes"] for_slide="s0" slide_id="s0-n"\n#\n# - t\n'
        )
        comp_en = (
            '# %% [markdown] lang="en" tags=["notes"] for_slide="s0" slide_id="s0-n"\n#\n# - t\n'
        )
        de = _build(HEADER_DE, _slide("s0", "de", "T"))
        en = _build(HEADER_EN, _slide("s0", "en", "T"))
        base = _snapshot(de, en, comp_de, comp_en)
        diff = _diff(
            base,
            de.replace('slide_id="s0"', 'slide_id="s1"'),
            en.replace('slide_id="s0"', 'slide_id="s1"'),
            comp_de.replace('for_slide="s0"', 'for_slide="s1"'),
            comp_en.replace('for_slide="s0"', 'for_slide="s1"'),
        )
        assert [(i.outcome, i.action) for i in diff.items] == [
            ("transition", "record_group_rename")
        ]

    def test_owner_change_with_one_sided_anchor_drift_surfaces_both(self):
        """MAJOR: a both-sided owner change combined with a one-sided header
        drift (vo_anchor) must surface BOTH — never swallow the drift."""
        comp_de = (
            '# %% [markdown] lang="de" tags=["notes"] for_slide="s0" slide_id="n1"\n#\n# - t\n'
        )
        comp_en = (
            '# %% [markdown] lang="en" tags=["notes"] for_slide="s0" slide_id="n1"\n#\n# - t\n'
        )
        de = _build(HEADER_DE, _slide("s0", "de", "T"), _slide("s1", "de", "T2"))
        en = _build(HEADER_EN, _slide("s0", "en", "T"), _slide("s1", "en", "T2"))
        base = _snapshot(de, en, comp_de, comp_en)
        diff = _diff(
            base,
            de,
            en,
            comp_de.replace('for_slide="s0"', 'for_slide="s1" vo_anchor="tm:xyz#0"'),
            comp_en.replace('for_slide="s0"', 'for_slide="s1"'),
        )
        actions = {i.action for i in diff.items}
        assert "record_owner" in actions
        assert len(diff.items) >= 2  # the anchor drift is not swallowed

    def test_ledger_mode_pool_members_are_cold_not_added(self):
        """MAJOR: with complete=False a pos member without an entry is COLD
        (framed verification), never a mechanical add/copy."""
        base = _snapshot(DE0, EN0)
        removed = [k for k in base.members if k.startswith("pos:")]
        for key in removed:
            del base.members[key]
        base.complete = False
        diff = _diff(base, DE0, EN0)
        assert diff.items
        assert {i.action for i in diff.items} == {"verify_cold"}
        assert {i.outcome for i in diff.items} == {"unverified"}

    def test_ledger_mode_one_sided_localized_add_is_translate_new_not_cold(self):
        """issue #566: a NEW one-sided localized cell in a ledgered deck must be
        framed ``translate_new`` (grow the twin), NOT ``verify_cold`` — whose
        only answer, ``confirm``, apply rejects for a one-sided member, leaving
        no decision-document path to resolve it. Two-sided cold members stay
        ``verify_cold`` (both sides present to confirm)."""
        base = _snapshot(DE0, EN0)
        base.complete = False
        de = DE0.replace(
            '# %% [markdown] lang="de" slide_id="s0-m"',
            _localized("s0-n", "de", "Neu").rstrip("\n") + "\n\n"
            '# %% [markdown] lang="de" slide_id="s0-m"',
        )
        item = _only_item(_diff(base, de, EN0))  # EN unchanged → one-sided
        assert (item.outcome, item.action) == ("add", "translate_new")
        assert item.direction == "de_to_en"
        assert item.key == "id:s0-n"

    def test_ledger_mode_one_sided_idd_shared_add_is_copy_new_shared_not_cold(self):
        """issue #566: a NEW one-sided *id-keyed* shared code cell in a ledgered
        deck is ``copy_new_shared`` (verbatim to the twin), not a ``verify_cold``
        dead end. (An un-id'd positional insert stays cold — ordinal aliasing
        makes mechanical mirroring unsafe; mint a slide_id to resolve it.)"""
        base = _snapshot(DE0, EN0)
        base.complete = False
        new = '# %% tags=["keep"] slide_id="z-cell"\nz = 9\n\n'
        de = DE0.replace('# %% tags=["keep"]\nx = 1', new + '# %% tags=["keep"]\nx = 1')
        item = _only_item(_diff(base, de, EN0))  # EN unchanged → one-sided
        assert (item.outcome, item.action) == ("add", "copy_new_shared")
        assert item.direction == "de_to_en"
        assert item.key == "id:z-cell"

    def test_ledger_mode_one_sided_unidd_positional_add_stays_cold(self):
        """Un-id'd positional one-sided insert in a ledgered deck stays
        ``verify_cold``: it cannot be mechanically mirrored (ordinal aliasing),
        so the engine keeps it cold rather than emit an unappliable copy."""
        base = _snapshot(DE0, EN0)
        base.complete = False
        de = DE0.replace('# %% tags=["keep"]\nx = 1', '# %%\nnew = 0\n\n# %% tags=["keep"]\nx = 1')
        item = _only_item(_diff(base, de, EN0))
        assert item.action == "verify_cold"

    def test_replacing_positional_cell_with_idd_cells_frames_stamp_vs_new(self):
        """issue #600: replacing an un-id'd positional cell with new id'd cells
        on ONE side must frame every affected row ``stamp_vs_new`` (which
        carries the ``treat_as_new`` answer) — not ``ambiguous_alignment``,
        whose empty answer vocabulary dead-ends the decision-document loop."""
        base = _snapshot(DE0, EN0)
        base.complete = False
        en = EN0.replace(
            '# %% tags=["keep"]\ny = 2\n',
            '# %% tags=["keep"] slide_id="y-assign"\ny = 3\n\n# %% slide_id="y-check"\ny\n',
        )
        diff = _diff(base, DE0, en)
        assert {(i.key, i.outcome, i.action) for i in diff.items} == {
            ("id:y-assign", "conflict", "stamp_vs_new"),
            ("id:y-check", "conflict", "stamp_vs_new"),
            ("pos:s0/code/1", "conflict", "stamp_vs_new"),
        }, [(i.key, i.outcome, i.action) for i in diff.items]
        by_key = {i.key: i for i in diff.items}
        # The pos-view row names the gone side — the anchor a mirrored
        # removal needs; the id-view rows name their present (source) side.
        assert by_key["pos:s0/code/1"].side == "en"
        assert by_key["id:y-assign"].side == "en"

    def test_conflicting_stamp_shape_stays_ambiguous_alignment(self):
        """The rival-id shapes must NOT gain ``stamp_vs_new``'s treat_as_new
        answer: copying a cell that already claimed a base entry under a
        different id would duplicate content (#600 scope guard)."""
        shared = "# %% [markdown]\n# Shared text\n\n"
        de = _build(HEADER_DE, _slide("s0", "de", "T"), shared)
        en = _build(HEADER_EN, _slide("s0", "en", "T"), shared)
        base = _snapshot(de, en)
        de2 = de.replace("# %% [markdown]\n# Shared", '# %% [markdown] slide_id="ida"\n# Shared')
        en2 = en.replace("# %% [markdown]\n# Shared", '# %% [markdown] slide_id="idb"\n# Shared')
        diff = _diff(base, de2, en2)
        assert diff.items
        assert {i.action for i in diff.items} == {"ambiguous_alignment"}

    def test_slide_id_containing_slash_does_not_crash(self):
        """MAJOR: '/' is legal in slide ids; pos-key parsing must rsplit."""
        de = _build(
            HEADER_DE,
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro/setup"\n#\n# # T\n\n',
            _shared_code("x"),
        )
        en = _build(
            HEADER_EN,
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro/setup"\n#\n# # T\n\n',
            _shared_code("x"),
        )
        base = _snapshot(de, en)
        diff = _diff(base, de.replace("x = 1", "x = 2"), en)
        item = _only_item(diff)
        assert item.action == "propagate_shared_edit"
