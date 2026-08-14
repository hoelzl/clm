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

import attrs
import pytest

from clm.slides.bilingual_doc import BilingualDeck
from clm.slides.doc_lenses import parse_bundle
from clm.slides.sync_diff import (
    MECHANICAL_ACTIONS,
    DeckBaseline,
    DeckDiff,
    baseline_from_deck,
    diff_deck,
    diff_outcome,
)
from clm.slides.sync_wire import WIRE_SCHEMA

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

    def test_no_baseline_means_every_member_is_cold_or_observably_neutral(self):
        """No baseline: every member is accounted for, none is silently trusted.

        Since #764 the snapshot-cold path splits the same way the ledger-cold one
        does — a `shared` code/j2 member whose halves are byte-identical resolves
        as `record_neutral` (§6.2.1) instead of asking a question with one
        possible answer. Everything else is still `verify_cold`.
        """
        deck = _parse(DE0, EN0)
        diff = diff_deck(deck, None)
        assert diff.items
        # Every member is framed — nothing falls through to "in sync".
        assert len(diff.items) == len(list(deck.members()))
        assert {i.outcome for i in diff.items} == {"unverified", "mechanical"}
        assert {i.action for i in diff.items} == {"verify_cold", "record_neutral"}
        neutral = {i.key for i in diff.items if i.action == "record_neutral"}
        assert neutral == {"pos:s0/code/0", "pos:s0/code/1"}

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


def _order_blind(base: DeckBaseline) -> DeckBaseline:
    """The confirm-seeded ledger's view (issue #654, review C3): member
    entries exist, but no order scope was ever seeded."""
    base.group_order = []
    base.group_order_by_side = {}
    base.member_order = {}
    base.complete = False
    return base


class TestOrderFirstClass:
    """Issue #654 (adversarial review C3/M1): order is a pair invariant.

    Cross-side divergence of the *current* sequences must frame even when
    no base order trust exists — a confirm-seeded ledger used to be
    permanently order-blind while reporting ``is_clean``."""

    DE3 = _build(
        HEADER_DE,
        _slide("s0", "de", "Eins"),
        _localized("m", "de", "DE"),
        _slide("s1", "de", "Zwei"),
        _localized("n", "de", "DE2"),
        _slide("s2", "de", "Drei"),
    )
    EN3 = _build(
        HEADER_EN,
        _slide("s0", "en", "One"),
        _localized("m", "en", "EN"),
        _slide("s1", "en", "Two"),
        _localized("n", "en", "EN2"),
        _slide("s2", "en", "Three"),
    )
    # s2 moved between s0 and s1 on the EN side only — the #654 shape.
    EN3_MOVED = _build(
        HEADER_EN,
        _slide("s0", "en", "One"),
        _localized("m", "en", "EN"),
        _slide("s2", "en", "Three"),
        _slide("s1", "en", "Two"),
        _localized("n", "en", "EN2"),
    )

    def test_order_blind_one_sided_slide_move_frames_order_decision(self):
        """The #654 regression: an EN-side slide move on an order-blind
        ledger framed nothing and reported clean."""
        base = _order_blind(_snapshot(self.DE3, self.EN3))
        diff = _diff(base, self.DE3, self.EN3_MOVED)
        assert [(i.outcome, i.action, i.key, i.direction) for i in diff.items] == [
            ("order", "order_decision", "pos:~groups/order.deck/0", "none")
        ]
        assert "recorded order trust does not cover" in diff.items[0].detail
        assert not diff.is_clean

    def test_order_blind_agreeing_sides_stay_clean(self):
        base = _order_blind(_snapshot(self.DE3, self.EN3))
        assert _diff(base, self.DE3, self.EN3).is_clean

    def test_order_blind_within_group_reorder_frames(self):
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "T"),
            _localized("m1", "de", "eins"),
            _localized("m2", "de", "zwei"),
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "T"),
            _localized("m1", "en", "one"),
            _localized("m2", "en", "two"),
        )
        en_swapped = _build(
            HEADER_EN,
            _slide("s0", "en", "T"),
            _localized("m2", "en", "two"),
            _localized("m1", "en", "one"),
        )
        base = _order_blind(_snapshot(de, en))
        diff = _diff(base, de, en_swapped)
        assert [(i.outcome, i.action, i.key) for i in diff.items] == [
            ("order", "order_decision", "pos:s0/order.deck/0")
        ]

    def test_cold_deck_with_divergent_order_frames_order_item(self):
        """A wholly-cold divergent deck must not hide the divergence behind
        its verify_cold items — the write gate (#719) would only reject it
        after the agent had confirmed every member."""
        diff = diff_deck(_parse(self.DE3, self.EN3_MOVED), None)
        order_items = [i for i in diff.items if i.outcome == "order"]
        assert [(i.action, i.key) for i in order_items] == [
            ("order_decision", "pos:~groups/order.deck/0")
        ]
        assert {i.action for i in diff.items} == {"verify_cold", "order_decision"}

    def test_cold_deck_with_agreeing_order_frames_no_order_item(self):
        diff = diff_deck(_parse(self.DE3, self.EN3), None)
        assert {i.action for i in diff.items} == {"verify_cold"}

    def test_renamed_and_edited_anchor_move_still_frames(self):
        """Review M1: a same-pass rename+edit of a group anchor fails rename
        detection and used to destroy the order evidence — the move
        vanished. The pair check on current sequences must still frame."""
        base = _snapshot(self.DE3, self.EN3)
        de = self.DE3.replace('slide_id="s2"', 'slide_id="s2x"').replace("Drei", "Drei NEU")
        en_moved = _build(
            HEADER_EN,
            _slide("s0", "en", "One"),
            _localized("m", "en", "EN"),
            _slide("s2x", "en", "Three NEW"),
            _slide("s1", "en", "Two"),
            _localized("n", "en", "EN2"),
        )
        diff = _diff(base, de, en_moved)
        order_scope_items = [i for i in diff.items if i.key == "pos:~groups/order.deck/0"]
        assert [(i.action, i.direction) for i in order_scope_items] == [
            ("order_decision", "none")
        ], [(i.outcome, i.action, i.key, i.detail) for i in diff.items]

    def test_seeded_base_keeps_the_directed_mirror(self):
        """Base order trust still refines the same move into a mechanical
        directed row — the decision is framed only when direction is
        unknowable."""
        base = _snapshot(self.DE3, self.EN3)
        diff = _diff(base, self.DE3, self.EN3_MOVED)
        assert [(i.outcome, i.action, i.direction) for i in diff.items] == [
            ("order", "mirror_order", "en_to_de")
        ]

    def test_group_order_divergence_observation_suppresses_is_clean(self):
        from clm.slides.bilingual_doc import Observation

        diff = DeckDiff(
            items=[],
            observations=[Observation(kind="group_order_divergence", detail="probe")],
        )
        assert not diff.is_clean


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


class TestPropagatePreambleCarriedDivergence:
    """Y6 (review 2026-07-24): the one-side-moved preamble branch carries the
    same divergence guard the cell path does — a baseline whose recorded
    preamble fingerprints differ per side has no safe verbatim source, so a
    one-sided preamble edit must FRAME, never mechanically propagate over the
    twin. Reachable for pre-existing or escape-hatch baselines even with the
    Y2 strict ``record`` gate (a trivial DE kernel-metadata edit replaced the
    entire EN preamble before this guard).
    """

    def test_de_edit_on_diverged_base_frames_pending_divergence(self):
        # The old row was a mechanical propagate_preamble; the new row is the
        # frame. _only_item pins both sides of that exchange at once.
        base = _snapshot("# pre-de\n" + DE0, "# pre-en\n" + EN0)
        diff = _diff(base, "# pre-de v2\n" + DE0, "# pre-en\n" + EN0)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("conflict", "pending_divergence")
        assert item.direction == "none"
        assert item.key == "pos:~preamble/deck/0"

    def test_en_edit_on_diverged_base_frames_pending_divergence(self):
        base = _snapshot("# pre-de\n" + DE0, "# pre-en\n" + EN0)
        diff = _diff(base, "# pre-de\n" + DE0, "# pre-en v2\n" + EN0)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("conflict", "pending_divergence")
        assert item.direction == "none"

    def test_preamble_added_on_one_side_of_diverged_base_frames(self):
        # Base carried an empty DE preamble against recorded EN content: the
        # new DE preamble must not mechanically overwrite the EN preamble.
        base = _snapshot(DE0, "# pre-en\n" + EN0)
        diff = _diff(base, "# pre-de new\n" + DE0, "# pre-en\n" + EN0)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("conflict", "pending_divergence")

    def test_preamble_added_on_aligned_empty_base_stays_mechanical(self):
        # The common flow — no preambles at base, one side adds one — keeps
        # its mechanical propagate (preserved-mechanical pin).
        base = _snapshot(DE0, EN0)
        diff = _diff(base, "# new preamble\n" + DE0, EN0)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("mechanical", "propagate_preamble")
        assert item.direction == "de_to_en"

    def test_diverged_companion_preamble_edit_frames(self):
        # Same guard on the companion part, not just the deck part.
        de_c = _build("# c-de\n", _companion_cell("s0-vo", "de", "s0", "DE Notiz"))
        en_c = _build("# c-en\n", _companion_cell("s0-vo", "en", "s0", "EN note"))
        base = _snapshot(DE0, EN0, de_c, en_c)
        diff = _diff(base, DE0, EN0, de_c.replace("# c-de", "# c-de v2"), en_c)
        item = _only_item(diff)
        assert (item.outcome, item.action) == ("conflict", "pending_divergence")
        assert item.key == "pos:~preamble/companion/0"


class TestEnvelope:
    def test_payload_announces_the_wire_schema_with_stable_booleans(self):
        base = _snapshot(DE0, EN0)
        payload = _diff(base, DE0.replace("# DE Text", "# DE v2"), EN0).to_payload()
        # One version for the report envelope AND the decision documents it is
        # answered with — they are one contract (clm.slides.sync_wire).
        assert payload["schema"] == WIRE_SCHEMA
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


class TestLensAdoptionGuard:
    """Issue #716: the lens's rule-2 adoption must not guess under an id'd-side
    pool surplus — the differ must never frame a mechanical removal of an
    authored cell whose twin the lens mis-adopted (C1), nor mis-marry a new
    localized cell to another slide's translation (C2)."""

    def test_new_idd_clone_before_pos_twin_frames_add_not_remove(self):
        de0 = _build(HEADER_DE, _slide("g", "de", "G"), _shared_code("data"))
        en0 = _build(HEADER_EN, _slide("g", "en", "G"), _shared_code("data"))
        base = _snapshot(de0, en0)
        en1 = _build(
            HEADER_EN,
            _slide("g", "en", "G"),
            '# %% tags=["keep"] slide_id="new-x"\ndata = 1\n\n',
            _shared_code("data"),
        )
        diff = _diff(base, de0, en1)
        actions = [(i.key, i.action) for i in diff.items]
        assert not any(a == "mirror_remove" for _, a in actions), actions
        # The new cell is a one-sided add; the authored positional cell is
        # untouched (in sync against its recorded fingerprints).
        assert ("id:new-x", "copy_new_shared") in actions, actions
        keys = [k for k, _ in actions]
        assert keys.count("id:new-x") == 1  # one handle, one item

    def test_new_localized_above_pending_stamp_keeps_the_true_marriage(self):
        # Warm base holds the #443 pending pair (old-pair id'd on DE, id-less
        # EN twin). Inserting brand-new above it must frame work for
        # brand-new only — old-pair stays in sync.
        de0 = _build(
            HEADER_DE,
            _slide("a", "de", "A"),
            _localized("old-pair", "de", "Alt"),
        )
        en0 = _build(
            HEADER_EN,
            _slide("a", "en", "A"),
            '# %% [markdown] lang="en"\n# Old translation\n\n',
        )
        base = _snapshot(de0, en0)
        de1 = _build(
            HEADER_DE,
            _slide("a", "de", "A"),
            _localized("brand-new", "de", "Neu"),
            _localized("old-pair", "de", "Alt"),
        )
        diff = _diff(base, de1, en0)
        # old-pair keeps its true EN marriage: its only item is the STANDING
        # mechanical #443 stamp (present in a no-op self-diff of this state
        # too) — never a translate_new for a "lost" twin, never a removal.
        old_actions = [i.action for i in diff.items if i.key == "id:old-pair"]
        assert old_actions == ["stamp_twin_id"], [(i.key, i.action, i.detail) for i in diff.items]
        assert any(i.key == "id:brand-new" for i in diff.items)
        assert not any(i.action == "mirror_remove" for i in diff.items)


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

    def test_ledger_mode_pool_members_are_never_a_mechanical_add(self):
        """MAJOR: with complete=False a pos member without an entry is never an add.

        The guard is about *adds*: a mechanical `record_symmetric_add` /
        `copy_new_shared` would treat an un-ledgered member as new and mirror it,
        which for a positional member the ordinals alias. Since #764 a two-sided
        `shared` code/j2 member resolves as `record_neutral` instead of asking —
        also mechanical, but ledger-only, so no cell is mirrored and no file byte
        is written. Everything else stays `verify_cold`.
        """
        base = _snapshot(DE0, EN0)
        for key in [k for k in base.members if k.startswith("pos:")]:
            del base.members[key]
        base.complete = False
        diff = _diff(base, DE0, EN0)
        assert diff.items
        assert {i.action for i in diff.items} <= {"verify_cold", "record_neutral"}
        assert not {i.action for i in diff.items} & {"record_symmetric_add", "copy_new_shared"}
        # The shared code cells are the neutral class; the j2 header and the
        # localized/markdown members are not.
        by_action = {
            i.action: [j.key for j in diff.items if j.action == i.action] for i in diff.items
        }
        assert sorted(by_action.get("record_neutral", [])) == ["pos:s0/code/0", "pos:s0/code/1"]

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

    def test_edited_survivor_with_stamp_suspicion_frames_remove_vs_edit(self):
        """#602 adversarial review: an edited survivor deterministically
        rejects a mirrored removal, so the pos-view row must not advertise
        ``treat_as_new`` as its only answer (an unbreakable report→reject
        loop). The shape frames ``remove_vs_edit`` — whose remove/keep answers
        both land — with the stamp suspicion spelled out in the detail."""
        base = _snapshot(DE0, EN0)
        base.complete = False
        en = EN0.replace(
            '# %% tags=["keep"]\ny = 2\n',
            '# %% tags=["keep"] slide_id="y-assign"\ny = 3\n\n# %% slide_id="y-check"\ny\n',
        )
        de = DE0.replace("y = 2", "y = 99")
        diff = _diff(base, de, en)
        by_key = {i.key: i for i in diff.items}
        pos = by_key["pos:s0/code/1"]
        assert pos.action == "remove_vs_edit", (pos.action, pos.detail)
        assert pos.side == "en"
        assert "unmatched id'd cell" in pos.detail
        # The id-view rows keep the stamp_vs_new framing — copying them to
        # the twin stays answerable regardless of the survivor's edit.
        assert by_key["id:y-assign"].action == "stamp_vs_new"
        assert by_key["id:y-check"].action == "stamp_vs_new"

    def test_644_new_id_cell_byte_identical_to_pos_cell_is_copy_new_shared(self):
        """Regression test for #644: a brand-new one-sided id-keyed cell whose
        body is byte-identical to a pos cell still present on BOTH sides must
        frame ``copy_new_shared`` — not steal that cell's base entry via the
        §7.3 key migration and conclude ``mirror_remove`` (which would delete
        the freshly-authored cell from its authoring side on apply)."""
        base = _snapshot(DE0, EN0)
        base.complete = False
        new = '# %% tags=["keep"] slide_id="y-bonus"\ny = 2\n\n'
        en = EN0.replace(_shared_code("y", 2), _shared_code("y", 2) + new)
        diff = _diff(base, DE0, en)
        by_key = {i.key: i for i in diff.items}
        item = by_key.get("id:y-bonus")
        assert item is not None, [(i.key, i.action) for i in diff.items]
        assert (item.outcome, item.action) == ("add", "copy_new_shared"), (
            item.outcome,
            item.action,
            item.detail,
        )
        assert item.direction == "en_to_de"
        # The untouched positional `y = 2` twin keeps its own entry — no churn.
        assert not any(i.key.startswith("pos:") for i in diff.items), [
            (i.key, i.action) for i in diff.items
        ]

    def test_644_de_side_authoring_mirror_case(self):
        """#644 neighbor: same shape authored on the DE side."""
        base = _snapshot(DE0, EN0)
        base.complete = False
        new = '# %% tags=["keep"] slide_id="x-bonus"\nx = 1\n\n'
        de = DE0.replace(_shared_code("x"), _shared_code("x") + new)
        diff = _diff(base, de, EN0)
        by_key = {i.key: i for i in diff.items}
        item = by_key.get("id:x-bonus")
        assert item is not None, [(i.key, i.action) for i in diff.items]
        assert (item.outcome, item.action) == ("add", "copy_new_shared")
        assert item.direction == "de_to_en"

    def test_644_true_id_stamp_still_migrates_when_pos_cell_left_the_pool(self):
        """#644 guard must not break the genuine §7.3 stamp: when the pos cell
        actually left the pool (stamped in place on both sides) the key still
        migrates."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        en = EN0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("transition", "record_key_migration")

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


class TestMirrorRemoveCarriedDivergence:
    """Y1 (adversarial review 2026-07-24): a two-sided shared base that
    itself carried a byte divergence must downgrade a one-sided removal to a
    FRAMED decision — the survivor sitting on its own fingerprint proves
    nothing about what the removed side held, so a mechanical
    ``mirror_remove`` could delete content that never existed on the removed
    side. The edit paths already refuse verbatim propagation on such a base
    (``pending_divergence``); the removal paths must follow the same rule.
    """

    def test_id_keyed_removal_on_diverged_base_frames_remove_vs_edit(self):
        """Y1 repro: base recorded the shared cell divergent (DE real
        content, EN placeholder); EN deletes its placeholder. The survivor
        (DE) sits on its own base fingerprint — but mirroring the removal
        would empty a DE cell whose content never existed on EN."""
        de = DE0.replace(
            '# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 98'
        )
        en = EN0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        base = _snapshot(de, en)  # diverged base: DE x=98, EN x=1
        en_removed = EN0.replace('# %% tags=["keep"]\nx = 1\n\n', "")
        item = _only_item(_diff(base, de, en_removed))
        assert (item.outcome, item.action) == ("conflict", "remove_vs_edit")
        assert item.side == "en"  # the gone side

    def test_pool_removal_on_diverged_base_frames_remove_vs_edit(self):
        """The positional-pool twin of the Y1 repro: same diverged base,
        same one-sided deletion, but the cell is un-id'd, so classification
        goes through the pool slot path."""
        de = DE0.replace("x = 1", "x = 98")
        base = _snapshot(de, EN0)  # diverged base, both cells positional
        en_removed = EN0.replace('# %% tags=["keep"]\nx = 1\n\n', "")
        item = _only_item(_diff(base, de, en_removed))
        assert (item.outcome, item.action) == ("conflict", "remove_vs_edit")
        assert item.side == "en"  # the gone side

    def test_removal_on_clean_base_still_mirrors_mechanically(self):
        """The guard must not declassify the ordinary case: a base whose
        sides agreed keeps its mechanical mirror_remove (pinned beside the
        new rows so the old behaviour cannot silently vanish)."""
        base = _snapshot(DE0, EN0)
        en_removed = EN0.replace('# %% tags=["keep"]\nx = 1\n\n', "")
        item = _only_item(_diff(base, DE0, en_removed))
        assert (item.outcome, item.action) == ("remove", "mirror_remove")

    def test_diverged_base_removal_with_suspected_stamp_frames_remove_vs_edit(self):
        """Review follow-up (PR #824, round 1): the stamp suspicion must not
        preempt the Y1 guard. A diverged base plus an unmatched id'd cell on
        the gone side still frames ``remove_vs_edit`` — ``stamp_vs_new``'s
        ``treat_as_new`` answer would mirror the removal, the same data loss
        one framed answer away. The detail keeps the stamped-edit hypothesis
        so the signal is not lost with the frame."""
        de = DE0.replace("x = 1", "x = 98")
        base = _snapshot(de, EN0)  # diverged base, positional
        en = EN0.replace(
            '# %% tags=["keep"]\nx = 1',
            '# %% tags=["keep"] slide_id="x-stamped"\nx = 1',
        )
        diff = _diff(base, de, en)
        by_key = {i.key: i for i in diff.items}
        row = by_key["pos:s0/code/0"]
        assert (row.outcome, row.action) == ("conflict", "remove_vs_edit")
        assert row.side == "en"  # the gone side
        assert "stamped" in row.detail  # the hypothesis survives the frame
        # The id view still frames stamp_vs_new — its treat_as_new answer only
        # copies (never deletes), so it is safe on a diverged base.
        assert by_key["id:x-stamped"].action == "stamp_vs_new"

    def test_diverged_base_removal_with_edited_survivor_keeps_both_clauses(self):
        """The Y1 guard's detail must still say when the survivor ALSO moved
        off base — the pre-existing 'edited on the {present} side' clause
        tells the agent the survivor isn't at base; dropping it thins the
        frame (PR #824 review round 2, minor)."""
        de = DE0.replace(
            '# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 98'
        )
        en = EN0.replace('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        base = _snapshot(de, en)  # diverged base: DE x=98, EN x=1
        en_removed = EN0.replace('# %% tags=["keep"]\nx = 1\n\n', "")
        item = _only_item(_diff(base, de.replace("x = 98", "x = 99"), en_removed))
        assert (item.outcome, item.action) == ("conflict", "remove_vs_edit")
        assert "diverged at base" in item.detail
        assert "edited" in item.detail  # the survivor moved too — say so


class TestRenameEditGuard:
    """Y7 (adversarial review 2026-07-24): a one-sided rename+edit defeats
    both rival checks (the content-matched `_find_rival_stamp` and the
    pos-only `_pool_side_deficit`) and used to execute mechanical
    ``copy_new_shared`` + ``mirror_remove`` — a decision-free apply deleted
    the twin's untouched cell and banked the loss invisibly (the next diff
    was clean). The removal side must frame ``remove_vs_edit`` when the
    gone side holds an estranged candidate (an unmatched one-sided id'd or
    positional cell of the pool), and the copy side must frame
    ``stamp_vs_new`` because the id-keyed base cell's half is unaccounted
    for there.
    """

    OLD = '# %% tags=["keep"] slide_id="old"\nx = 1\n\n'

    @staticmethod
    def _de(cell: str) -> str:
        return _build(HEADER_DE, _slide("s0", "de", "Titel"), cell)

    @staticmethod
    def _en(cell: str) -> str:
        return _build(HEADER_EN, _slide("s0", "en", "Title"), cell)

    def test_one_sided_rename_edit_frames_both_rows(self):
        base = _snapshot(self._de(self.OLD), self._en(self.OLD))
        renamed = '# %% tags=["keep"] slide_id="new"\nx = 2\n\n'
        diff = _diff(base, self._de(renamed), self._en(self.OLD))
        assert {(i.key, i.outcome, i.action) for i in diff.items} == {
            ("id:new", "conflict", "stamp_vs_new"),
            ("id:old", "conflict", "remove_vs_edit"),
        }, [(i.key, i.outcome, i.action) for i in diff.items]
        by_key = {i.key: i for i in diff.items}
        assert by_key["id:old"].side == "de"  # the gone side
        assert "renamed" in by_key["id:old"].detail  # the hypothesis named

    def test_one_sided_strip_edit_frames_the_removal(self):
        base = _snapshot(self._de(self.OLD), self._en(self.OLD))
        stripped = '# %% tags=["keep"]\nx = 2\n\n'
        diff = _diff(base, self._de(stripped), self._en(self.OLD))
        assert not any(i.action == "mirror_remove" for i in diff.items)
        by_key = {i.key: i for i in diff.items}
        assert (by_key["id:old"].outcome, by_key["id:old"].action) == (
            "conflict",
            "remove_vs_edit",
        )

    def test_pure_rename_also_frames_the_copy(self):
        # No edit: the rival-stamp check already framed id:old, but the
        # mechanical copy of the renamed cell fired beside the frame — the
        # unverified-mechanical-row leak class. The widened deficit frames
        # it too.
        base = _snapshot(self._de(self.OLD), self._en(self.OLD))
        renamed = '# %% tags=["keep"] slide_id="new"\nx = 1\n\n'
        diff = _diff(base, self._de(renamed), self._en(self.OLD))
        assert {(i.key, i.outcome, i.action) for i in diff.items} == {
            ("id:new", "conflict", "stamp_vs_new"),
            ("id:old", "conflict", "ambiguous_alignment"),
        }, [(i.key, i.outcome, i.action) for i in diff.items]

    def test_genuine_one_sided_removal_still_mirrors(self):
        # Preserved-mechanical pin: no estranged candidate on the gone side,
        # so the removal of an id-keyed cell stays a mechanical mirror.
        base = _snapshot(self._de(self.OLD), self._en(self.OLD))
        de = _build(HEADER_DE, _slide("s0", "de", "Titel"))
        item = _only_item(_diff(base, de, self._en(self.OLD)))
        assert (item.outcome, item.action) == ("remove", "mirror_remove")
        assert item.direction == "de_to_en"

    def test_genuine_new_idd_cell_still_copies(self):
        # Preserved-mechanical pin: old kept on both halves, a genuinely new
        # id'd cell added on one side — no unaccounted base half, no frame.
        base = _snapshot(self._de(self.OLD), self._en(self.OLD))
        new = '# %% tags=["keep"] slide_id="new"\ny = 5\n\n'
        item = _only_item(_diff(base, self._de(self.OLD + new), self._en(self.OLD)))
        assert (item.outcome, item.action) == ("add", "copy_new_shared")
        assert item.key == "id:new"

    def test_both_sides_rename_still_records_mechanically(self):
        # The deliberate both-halves rename keeps its quiet record path.
        base = _snapshot(self._de(self.OLD), self._en(self.OLD))
        renamed = '# %% tags=["keep"] slide_id="new"\nx = 1\n\n'
        diff = _diff(base, self._de(renamed), self._en(renamed))
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:new", "record_symmetric_add"),
            ("id:old", "record_remove"),
        }, [(i.key, i.action) for i in diff.items]

    def test_new_id_cell_identical_to_present_pos_cell_cannot_steal_its_entry(self):
        """#644 x Y7 (PR #831 review round 1, CRITICAL): remove id-cell A on
        one side and add id-cell N byte-identical to a STILL-PRESENT
        positional cell P. The id-keyed gap must not satisfy the pos→id
        migration precondition — a stamp takes a cell OUT of the pool, and
        A's gap says nothing about P's pool. Pre-fix N migrated P's base
        entry and emitted a mechanical mirror_remove that deleted N's
        authoring half, next diff clean."""
        pos = '# %% tags=["keep"]\np = 1\n\n'
        a = '# %% tags=["keep"] slide_id="a"\na = 1\n\n'
        n = '# %% tags=["keep"] slide_id="n"\np = 1\n\n'
        base = _snapshot(self._de(pos + a), self._en(pos + a))
        diff = _diff(base, self._de(pos + n), self._en(pos + a))
        assert {(i.key, i.outcome, i.action) for i in diff.items} == {
            ("id:n", "conflict", "stamp_vs_new"),
            ("id:a", "conflict", "remove_vs_edit"),
        }, [(i.key, i.outcome, i.action) for i in diff.items]

    def test_simultaneous_anchor_rename_still_frames(self):
        """PR #831 review round 1 (Important): renaming the slide anchor AND
        a cell on one half breaks group-token matching (the base owner names
        the old anchor id, the current group the new one), which defeated
        both guards. With one-sided-anchor evidence the suspicion scans fall
        back to group-unscoped — nothing destructive may execute
        mechanically."""
        base = _snapshot(self._de(self.OLD), self._en(self.OLD))
        renamed_cell = '# %% tags=["keep"] slide_id="new"\nx = 2\n\n'
        de = _build(HEADER_DE, _slide("sX", "de", "Titel"), renamed_cell)
        diff = _diff(base, de, self._en(self.OLD))
        assert not any(i.action in ("mirror_remove", "copy_new_shared") for i in diff.items), [
            (i.key, i.outcome, i.action) for i in diff.items
        ]
        by_key = {i.key: i for i in diff.items}
        assert (by_key["id:old"].outcome, by_key["id:old"].action) == (
            "conflict",
            "remove_vs_edit",
        )
        assert (by_key["id:new"].outcome, by_key["id:new"].action) == (
            "conflict",
            "stamp_vs_new",
        )

    def test_absorbed_fork_twin_does_not_hide_the_estranged_cell(self):
        """PR #831 review round 2 (CRITICAL): a mid-transition fork
        classified earlier in deck order claims the lone unpaired pos cell
        via _absorb_any_pos_twin — the claim suppresses the pool's own
        mechanical row, it must not hide the cell from the Y7 suspicion
        scan. Both classification orders must frame the removal; pre-fix,
        the fork-first order emitted a mechanical mirror_remove that deleted
        the untouched twin, next diff clean."""
        b = '# %% tags=["keep"] slide_id="b"\nb = 1\n\n'
        q = '# %% tags=["keep"]\nq = 1\n\n'
        b_stripped = '# %% tags=["keep"]\nb = 2\n\n'
        q_forked = '# %% tags=["keep"] lang="en" slide_id="q2"\nq = 1\n\n'
        base = _snapshot(
            _build(HEADER_DE, _slide("s0", "de", "Titel"), b, q),
            _build(HEADER_EN, _slide("s0", "en", "Title"), b, q),
        )
        cur_de = _build(HEADER_DE, _slide("s0", "de", "Titel"), b_stripped)
        for label, en in (
            ("fork first", _build(HEADER_EN, _slide("s0", "en", "Title"), q_forked, b)),
            ("victim first", _build(HEADER_EN, _slide("s0", "en", "Title"), b, q_forked)),
        ):
            diff = _diff(base, cur_de, en)
            assert not any(i.action == "mirror_remove" for i in diff.items), label
            by_key = {i.key: i for i in diff.items}
            assert (by_key["id:b"].outcome, by_key["id:b"].action) == (
                "conflict",
                "remove_vs_edit",
            ), label

    def test_one_sided_anchor_add_widens_suspicion_deck_wide(self):
        """Deliberate trade-off (PR #831 review round 2, Minor): a plain
        one-sided slide ADD also opens the group-unscoped fallback (an
        anchor renamed AND edited is fingerprint-indistinguishable from a
        remove+add), so a concurrent genuine removal frames instead of
        mirroring — safe direction, convergent dance, and the detail must
        SAY the match was deck-wide. Pinned so a future narrowing is a
        conscious decision, not a regression."""
        base = _snapshot(self._de(self.OLD), self._en(self.OLD))
        new_slide = _slide("s9", "de", "Neu") + '# %% tags=["keep"] slide_id="n"\ny = 5\n\n'
        de = _build(HEADER_DE, _slide("s0", "de", "Titel"), new_slide)  # id:old removed
        diff = _diff(base, de, self._en(self.OLD))
        assert not any(i.action in ("mirror_remove", "copy_new_shared") for i in diff.items), [
            (i.key, i.outcome, i.action) for i in diff.items
        ]
        by_key = {i.key: i for i in diff.items}
        assert by_key["id:old"].action == "remove_vs_edit"
        assert "could not be matched to this slide" in by_key["id:old"].detail
        assert by_key["id:n"].action == "stamp_vs_new"


class TestStampTwinIdTrustGate:
    """Y5 (adversarial review 2026-07-24): ``pair_positionally`` adopts an
    id-less twin by pool order, and P2 makes a stamped id the member's
    identity — so a mechanical ``stamp_twin_id`` on a pairing the ledger
    does not know is permanent identity corruption (the review's repro
    stamped ``apples`` onto the oranges text). The stamp may execute
    mechanically only when the pairing is ledger-known: recorded under the
    member's own key with the twin's current fingerprint, or content-matched
    via a pos→id key migration. Anything else stays framed — ``confirm``
    banks the pairing and the NEXT report stamps mechanically.
    """

    @staticmethod
    def _warm_ledger_base() -> DeckBaseline:
        base = _snapshot(
            _build(HEADER_DE, _slide("s0", "de", "Titel")),
            _build(HEADER_EN, _slide("s0", "en", "Title")),
        )
        base.complete = False  # ledger semantics: a missing entry is cold, never "new"
        return base

    # The Y5 repro state: two localized cells added per side; the EN twins
    # are id-less and SWAPPED, so pool order marries each id to the other
    # text. Equal cardinality — the #716 residue guard does not fire.
    _DE_SWAPPED = _build(
        HEADER_DE,
        _slide("s0", "de", "Titel"),
        _localized("apples", "de", "Aepfel"),
        _localized("oranges", "de", "Birnen"),
    )
    _EN_SWAPPED = _build(
        HEADER_EN,
        _slide("s0", "en", "Title"),
        '# %% [markdown] lang="en"\n# ORANGES text\n\n',
        '# %% [markdown] lang="en"\n# APPLES text\n\n',
    )

    def test_swapped_idless_twins_are_never_stamped_mechanically(self):
        """The Y5 repro: no ledger entry knows either pairing, so both members
        stay framed and NO stamp executes."""
        diff = _diff(self._warm_ledger_base(), self._DE_SWAPPED, self._EN_SWAPPED)
        stamps = [i for i in diff.items if i.action == "stamp_twin_id"]
        assert stamps == [], [(i.key, i.action, i.detail) for i in diff.items]
        framed = {i.key: i.action for i in diff.items}
        assert framed == {"id:apples": "verify_cold", "id:oranges": "verify_cold"}
        # The observation still surfaces, so the report names the pending stamp.
        pending = {o.member.value for o in diff.observations if o.kind == "id_stamp_pending_twin"}
        assert pending == {"apples", "oranges"}

    def test_confirmed_pairing_stamps_mechanically_on_the_next_pass(self):
        """The framed path is not a dead end: once the pairing is banked
        (``confirm`` / ``record``), the next report finds it ledger-known and
        the stamp is mechanical."""
        diff = _diff(self._warm_ledger_base(), self._DE_SWAPPED, self._EN_SWAPPED)
        assert not any(i.action == "stamp_twin_id" for i in diff.items)
        banked = _snapshot(self._DE_SWAPPED, self._EN_SWAPPED)
        banked.complete = False
        diff2 = _diff(banked, self._DE_SWAPPED, self._EN_SWAPPED)
        stamps = {i.key for i in diff2.items if i.action == "stamp_twin_id"}
        assert stamps == {"id:apples", "id:oranges"}, [
            (i.key, i.action, i.detail) for i in diff2.items
        ]

    def test_ledger_known_pairing_restores_a_stripped_id_mechanically(self):
        """Pin (must survive the gate): the id was recorded on BOTH halves and
        stripped from EN with the text untouched — the pairing is ledger-known,
        so re-stamping it stays mechanical."""
        de = _build(HEADER_DE, _slide("s0", "de", "Titel"), _localized("m1", "de", "DE Text"))
        en = _build(HEADER_EN, _slide("s0", "en", "Title"), _localized("m1", "en", "EN text"))
        base = _snapshot(de, en)
        base.complete = False
        item = _only_item(_diff(base, de, en.replace(' slide_id="m1"', "")))
        assert (item.outcome, item.action) == ("transition", "stamp_twin_id")
        assert item.side == "en"

    def test_one_sided_ledger_entry_does_not_trust_the_positional_twin(self):
        """A ledger entry that recorded only the DE half proves the ID, not the
        PAIRING — the newly authored id-less EN twin is still a pool-order
        guess, so no mechanical stamp."""
        de = _build(HEADER_DE, _slide("s0", "de", "Titel"), _localized("m1", "de", "DE Text"))
        base = _snapshot(de, _build(HEADER_EN, _slide("s0", "en", "Title")))
        base.complete = False
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            '# %% [markdown] lang="en"\n# EN text\n\n',
        )
        diff = _diff(base, de, en)
        assert not any(i.action == "stamp_twin_id" for i in diff.items), [
            (i.key, i.action, i.detail) for i in diff.items
        ]
        assert {i.action for i in diff.items if i.key == "id:m1"} == {"verify_translation"}

    # -- the fork route around the gate (PR #825 review round 1) ----------------
    #
    # fork_match migrates a positional base entry by a body match on EITHER
    # side, so a fork can establish the member's identity without establishing
    # the STAMPED side's pairing. Two recorded shared cells forked into
    # localized cells, DE halves id'd, EN twins id-less and swapped.
    _FORK_BASE_DE = _build(
        HEADER_DE,
        _slide("s0", "de", "Titel"),
        "# %% [markdown]\n# SHARED ONE\n\n",
        "# %% [markdown]\n# SHARED TWO\n\n",
    )
    _FORK_BASE_EN = _build(
        HEADER_EN,
        _slide("s0", "en", "Title"),
        "# %% [markdown]\n# SHARED ONE\n\n",
        "# %% [markdown]\n# SHARED TWO\n\n",
    )
    _FORK_DE = _build(
        HEADER_DE,
        _slide("s0", "de", "Titel"),
        '# %% [markdown] lang="de" slide_id="aa"\n# SHARED ONE\n\n',
        '# %% [markdown] lang="de" slide_id="bb"\n# SHARED TWO\n\n',
    )
    _FORK_EN = _build(
        HEADER_EN,
        _slide("s0", "en", "Title"),
        '# %% [markdown] lang="en"\n# SHARED TWO\n\n',
        '# %% [markdown] lang="en"\n# SHARED ONE\n\n',
    )

    def test_fork_adoption_with_swapped_twin_neither_stamps_nor_banks(self):
        """The fork Y5 route: suppressing the stamp is not enough — the same
        pass's mechanical ``record_fork`` would bank the guessed pairing and
        the next report would find it 'ledger-known'. Both must stay framed."""
        base = _snapshot(self._FORK_BASE_DE, self._FORK_BASE_EN)
        base.complete = False
        diff = _diff(base, self._FORK_DE, self._FORK_EN)
        assert not any(i.action == "stamp_twin_id" for i in diff.items), [
            (i.key, i.action, i.detail) for i in diff.items
        ]
        assert not any(i.action == "record_fork" for i in diff.items), [
            (i.key, i.action, i.detail) for i in diff.items
        ]
        framed = {i.key: i.action for i in diff.items}
        assert framed == {"id:aa": "verify_translation", "id:bb": "verify_translation"}

    def test_fork_with_true_twin_still_stamps_and_records_mechanically(self):
        """Pin: the legitimate fork — the EN twin's body IS the recorded shared
        body, so the pairing is content-established on both sides and the fork
        plus stamp stay mechanical."""
        base = _snapshot(
            _build(HEADER_DE, _slide("s0", "de", "Titel"), "# %% [markdown]\n# SHARED ONE\n\n"),
            _build(HEADER_EN, _slide("s0", "en", "Title"), "# %% [markdown]\n# SHARED ONE\n\n"),
        )
        base.complete = False
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            '# %% [markdown] lang="de" slide_id="aa"\n# SHARED ONE\n\n',
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            '# %% [markdown] lang="en"\n# SHARED ONE\n\n',
        )
        diff = _diff(base, de, en)
        actions = {i.action for i in diff.items if i.key == "id:aa"}
        assert actions == {"stamp_twin_id", "record_fork"}, [
            (i.key, i.action, i.detail) for i in diff.items
        ]

    # -- same-body/different-header collision (PR #825 review round 2) ---------
    #
    # Two recorded shared cells with IDENTICAL bodies but different tags,
    # forked with id'd DE halves and swapped id-less EN twins. A body-only
    # trust check cannot tell the cells apart — the header (tags) is exactly
    # what must stay load-bearing.
    _COLLIDE_BASE_DE = _build(
        HEADER_DE,
        _slide("s0", "de", "Titel"),
        '# %% [markdown] tags=["a"]\n# ---\n\n',
        '# %% [markdown] tags=["b"]\n# ---\n\n',
    )
    _COLLIDE_BASE_EN = _build(
        HEADER_EN,
        _slide("s0", "en", "Title"),
        '# %% [markdown] tags=["a"]\n# ---\n\n',
        '# %% [markdown] tags=["b"]\n# ---\n\n',
    )
    _COLLIDE_DE = _build(
        HEADER_DE,
        _slide("s0", "de", "Titel"),
        '# %% [markdown] lang="de" tags=["a"] slide_id="aa"\n# ---\n\n',
        '# %% [markdown] lang="de" tags=["b"] slide_id="bb"\n# ---\n\n',
    )
    _COLLIDE_EN = _build(
        HEADER_EN,
        _slide("s0", "en", "Title"),
        '# %% [markdown] lang="en" tags=["b"]\n# ---\n\n',
        '# %% [markdown] lang="en" tags=["a"]\n# ---\n\n',
    )

    def test_same_body_different_tags_fork_swap_is_not_ledger_known(self):
        """PR #825 review round 2 (Critical): the stamped side must match the
        recorded entry modulo EXACTLY the lang attribute — a body-only match
        confuses cells whose bodies collide."""
        base = _snapshot(self._COLLIDE_BASE_DE, self._COLLIDE_BASE_EN)
        base.complete = False
        diff = _diff(base, self._COLLIDE_DE, self._COLLIDE_EN)
        assert not any(
            i.action in ("stamp_twin_id", "record_fork", "mirror_tags") for i in diff.items
        ), [(i.key, i.action, i.detail) for i in diff.items]
        framed = {i.key: i.action for i in diff.items}
        assert framed == {"id:aa": "verify_translation", "id:bb": "verify_translation"}

    def test_unverified_pairing_never_leaks_an_order_mirror(self):
        """PR #825 review round 3 (Important): the adopted twin's position is
        part of the guess — ``_diff_order`` must not mirror it mechanically
        while the pairing frame is pending."""
        de0 = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _localized("aa", "de", "Apfel"),
            _localized("xx", "de", "Birne"),
        )
        en0 = _build(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _localized("aa", "en", "Apple"),
            _localized("xx", "en", "Pear"),
        )
        base = _snapshot(de0, en0)
        base.complete = False
        # EN twin of aa: edited off base, id-stripped, moved below xx — the
        # adoption pairs it positionally, and its slot feeds the order check.
        en1 = _build(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _localized("xx", "en", "Pear"),
            '# %% [markdown] lang="en"\n# Apple v2\n\n',
        )
        diff = _diff(base, de0, en1)
        assert not any(i.action == "mirror_order" for i in diff.items), [
            (i.key, i.action, i.detail) for i in diff.items
        ]
        assert not any(i.action == "stamp_twin_id" for i in diff.items)
        assert {i.action for i in diff.items if i.key == "id:aa"} == {"verify_translation"}

    def test_unverified_pairing_with_divergent_tags_co_frames_the_tags_row(self):
        """PR #825 review round 3 (Important): confirm on the pairing frame is
        refused while tag sets diverge cross-side (``_reject_divergent_tags``),
        so suppressing the tags row deadlocked the member — the refusal named a
        nonexistent item. The gate co-frames ``conflict_tags`` (never the
        mechanical ``mirror_tags``: attribution against an unverified pairing
        is meaningless), keeping the executor's mirror-then-confirm dance
        answerable."""
        base = _snapshot(
            _build(
                HEADER_DE,
                _slide("s0", "de", "Titel"),
                '# %% [markdown] tags=["a"]\n# SHARED ONE\n\n',
                '# %% [markdown] tags=["b"]\n# SHARED TWO\n\n',
            ),
            _build(
                HEADER_EN,
                _slide("s0", "en", "Title"),
                '# %% [markdown] tags=["a"]\n# SHARED ONE\n\n',
                '# %% [markdown] tags=["b"]\n# SHARED TWO\n\n',
            ),
        )
        base.complete = False
        de = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            '# %% [markdown] lang="de" tags=["a"] slide_id="aa"\n# SHARED ONE\n\n',
            '# %% [markdown] lang="de" tags=["b"] slide_id="bb"\n# SHARED TWO\n\n',
        )
        en = _build(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            '# %% [markdown] lang="en" tags=["b"]\n# SHARED TWO\n\n',
            '# %% [markdown] lang="en" tags=["a"]\n# SHARED ONE\n\n',
        )
        diff = _diff(base, de, en)
        assert not any(i.action in MECHANICAL_ACTIONS for i in diff.items), [
            (i.key, i.action, i.detail) for i in diff.items
        ]
        by_key: dict[str, set[str]] = {}
        for i in diff.items:
            by_key.setdefault(i.key, set()).add(i.action)
        assert by_key.get("id:aa") == {"verify_translation", "conflict_tags"}
        assert by_key.get("id:bb") == {"verify_translation", "conflict_tags"}

    def test_gated_stamp_surfaces_in_the_text_report(self):
        """The text report must not lose the pending-stamp signal when the gate
        suppresses the stamp row (PR #825 review round 1, minor): the JSON
        report carries the ``id_stamp_pending_twin`` observation; the text
        report must print it too, or a human reader sees only bare verify_cold
        rows with no hint that an identity decision is pending."""
        from pathlib import Path
        from types import SimpleNamespace

        from clm.cli.commands.slides import sync_v3

        diff = _diff(self._warm_ledger_base(), self._DE_SWAPPED, self._EN_SWAPPED)
        bundle = SimpleNamespace(de_path=Path("slides_t.de.py"))
        text = sync_v3._render_pair(bundle, diff)  # type: ignore[arg-type]
        lines = [ln for ln in text.splitlines() if "id_stamp_pending_twin" in ln]
        assert len(lines) == 2, text

    def test_trusted_stamp_does_not_double_print_the_observation(self):
        """A stamp row that WAS emitted carries the signal itself; printing the
        observation too would be a redundant line per trusted stamp."""
        from pathlib import Path
        from types import SimpleNamespace

        from clm.cli.commands.slides import sync_v3

        de = _build(HEADER_DE, _slide("s0", "de", "Titel"), _localized("m1", "de", "DE Text"))
        en = _build(HEADER_EN, _slide("s0", "en", "Title"), _localized("m1", "en", "EN text"))
        base = _snapshot(de, en)
        base.complete = False
        diff = _diff(base, de, en.replace(' slide_id="m1"', ""))
        bundle = SimpleNamespace(de_path=Path("slides_t.de.py"))
        text = sync_v3._render_pair(bundle, diff)  # type: ignore[arg-type]
        assert "stamp_twin_id" in text  # the row itself
        assert "id_stamp_pending_twin" not in text  # no redundant observation line

    def test_cold_sweep_hint_names_pending_id_stamp_pairings(self):
        """The wholesale-record hint fires on an all-verify_cold report —
        exactly the gated-stamp shape — and must say that `record` banks the
        positionally guessed pairings too (PR #825 review round 1, minor)."""
        from clm.slides.doc_report import cold_sweep_hint

        diff = _diff(self._warm_ledger_base(), self._DE_SWAPPED, self._EN_SWAPPED)
        hint = cold_sweep_hint(diff)
        assert hint is not None
        assert "id-stamp" in hint


class TestTagParity:
    """Cross-side tag parity as an orthogonal aspect row (issue #615).

    Tags are language-independent and mirror across the twins (§3.1); the
    differ checks the pair invariant on localized members (and headers)
    regardless of body drift, instead of only in the narrow bodies-at-base
    states that let #615's one-sided tag edit vanish into the body row.
    """

    DE_M = '# %% [markdown] lang="de" slide_id="s0-m"'
    EN_M = '# %% [markdown] lang="en" slide_id="s0-m"'

    def test_615_tag_edit_plus_body_drift_coemits_mirror_and_verify(self):
        """The #615 shape: both bodies off base + a one-sided DE tag edit
        must frame BOTH aspects — never fold the tag delta into the body
        row where confirm would bank the divergence."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace(self.DE_M, self.DE_M.replace(" slide_id", ' tags=["notes"] slide_id'))
        de = de.replace("# DE Text", "# DE Text v2")
        en = EN0.replace("# EN text", "# EN text v2")
        diff = _diff(base, de, en)
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:s0-m", "mirror_tags"),
            ("id:s0-m", "verify_translation"),
        }, [(i.key, i.action) for i in diff.items]
        mirror = next(i for i in diff.items if i.action == "mirror_tags")
        assert (mirror.outcome, mirror.direction, mirror.side) == ("mechanical", "de_to_en", "de")

    def test_baseline_carried_tag_divergence_frames_conflict_tags(self):
        """The post-#615-damage state: the ledger itself carries
        de_tags != en_tags and nothing moved — never in_sync again."""
        de = DE0.replace(self.DE_M, self.DE_M.replace(" slide_id", ' tags=["notes"] slide_id'))
        base = _snapshot(de, EN0)  # baseline banks the divergence
        item = _only_item(_diff(base, de, EN0))  # unchanged input
        assert (item.outcome, item.action) == ("conflict", "conflict_tags")
        assert item.direction == "none"
        assert item.key == "id:s0-m"

    def test_both_sides_tags_moved_differently_is_conflict_tags_not_shared(self):
        """S2 regression: conflict_shared's propagate/body answers copy
        whole cells — body-destroying on a localized pair. The framed tag
        row must be conflict_tags."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace(self.DE_M, self.DE_M.replace(" slide_id", ' tags=["notes"] slide_id'))
        en = EN0.replace(self.EN_M, self.EN_M.replace(" slide_id", ' tags=["alt"] slide_id'))
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("conflict", "conflict_tags")
        assert item.direction == "both"

    def test_conflict_tags_suppresses_the_framed_body_row(self):
        """Two framed rows on one key cannot both be answered (decision
        documents are keyed by handle alone): the framed conflict_tags
        suppresses verify_translation this pass; the body row re-frames
        once the tags are reconciled."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace(self.DE_M, self.DE_M.replace(" slide_id", ' tags=["notes"] slide_id'))
        de = de.replace("# DE Text", "# DE Text v2")
        en = EN0.replace(self.EN_M, self.EN_M.replace(" slide_id", ' tags=["alt"] slide_id'))
        en = en.replace("# EN text", "# EN text v2")
        item = _only_item(_diff(base, de, en))
        assert (item.outcome, item.action) == ("conflict", "conflict_tags")
        assert item.direction == "both"

    def test_fork_with_one_sided_tag_move_coemits_mirror_tags(self):
        """record_fork legitimizes cross-side bytes as a trusted baseline —
        a one-sided tag move off the shared base is still attributable at
        fork time and mirrors mechanically alongside the fork record."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% tags=["keep"]\ny = 2', '# %% lang="de" tags=["alt"] slide_id="y-cell"\ny = 2'
        )
        en = EN0.replace(
            '# %% tags=["keep"]\ny = 2', '# %% lang="en" tags=["keep"] slide_id="y-cell"\ny = 2'
        )
        diff = _diff(base, de, en)
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:y-cell", "mirror_tags"),
            ("id:y-cell", "record_fork"),
        }, [(i.key, i.action) for i in diff.items]
        mirror = next(i for i in diff.items if i.action == "mirror_tags")
        assert (mirror.outcome, mirror.direction, mirror.side) == ("mechanical", "de_to_en", "de")

    def test_fork_with_divergent_tag_moves_coemits_conflict_tags(self):
        base = _snapshot(DE0, EN0)
        de = DE0.replace(
            '# %% tags=["keep"]\ny = 2', '# %% lang="de" tags=["alt"] slide_id="y-cell"\ny = 2'
        )
        en = EN0.replace(
            '# %% tags=["keep"]\ny = 2', '# %% lang="en" tags=["other"] slide_id="y-cell"\ny = 2'
        )
        diff = _diff(base, de, en)
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:y-cell", "conflict_tags"),
            ("id:y-cell", "record_fork"),
        }, [(i.key, i.action) for i in diff.items]
        conflict = next(i for i in diff.items if i.action == "conflict_tags")
        assert (conflict.outcome, conflict.direction) == ("conflict", "both")

    def test_none_recorded_tags_count_as_moved_and_frame_conflict_tags(self):
        """A ledger entry whose tag fields predate tag recording (None)
        must never be trusted as a baseline: with a cross-side divergence
        no direction is attributable — framed, not silently mechanical."""
        base = _snapshot(DE0, EN0)
        base.members["id:s0-m"] = attrs.evolve(base.members["id:s0-m"], de_tags=None, en_tags=None)
        de = DE0.replace(self.DE_M, self.DE_M.replace(" slide_id", ' tags=["notes"] slide_id'))
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("conflict", "conflict_tags")

    @pytest.mark.parametrize("none_side", ["de", "en"])
    def test_one_sided_none_tag_base_frames_instead_of_wiping_the_recorded_side(
        self, none_side: str
    ):
        """Adversarial-review regression: ONE side's recorded tag base is
        None (e.g. a pending twin that landed without tags) while the other
        side carries recorded tags. The None base counts as MOVED but must
        never become a mechanical mirror SOURCE — that mirror would wipe
        the recorded side's tags (notes/voiceover route audiences). No
        trusted source — framed, direction none."""
        de = (
            DE0.replace(self.DE_M, self.DE_M.replace(" slide_id", ' tags=["notes"] slide_id'))
            if none_side == "en"
            else DE0
        )
        en = (
            EN0.replace(self.EN_M, self.EN_M.replace(" slide_id", ' tags=["notes"] slide_id'))
            if none_side == "de"
            else EN0
        )
        assert 'tags=["notes"]' in de + en  # exactly one side carries recorded tags
        base = _snapshot(de, en)
        base.members["id:s0-m"] = attrs.evolve(
            base.members["id:s0-m"], **{f"{none_side}_tags": None}
        )
        item = _only_item(_diff(base, de, en))  # unchanged input
        assert (item.outcome, item.action) == ("conflict", "conflict_tags")
        assert item.direction == "none"
        assert "incomplete recorded tag baseline" in item.detail

    HZ = '# %% [markdown] tags=["alt"]\n# HZ\n\n'

    def test_pool_baseline_carried_tag_divergence_frames_conflict_tags(self):
        """The pool analogue of the damaged #615 end state: a warm id-less
        localized header slot whose RECORDED baseline itself carries
        de_tags != en_tags must frame conflict_tags — never in_sync."""
        de = _build(self.HZ, HEADER_DE, _slide("s0", "de", "Titel"))
        en = _build(
            self.HZ.replace('tags=["alt"]', 'tags=["beta"]'),
            HEADER_EN,
            _slide("s0", "en", "Title"),
        )
        base = _snapshot(de, en)  # the baseline banks the divergence
        item = _only_item(_diff(base, de, en))  # unchanged input
        assert (item.outcome, item.action) == ("conflict", "conflict_tags")
        assert item.direction == "none"
        assert item.key == "pos:~header/markdown/0"

    def test_pool_both_sides_tags_moved_differently_frames_conflict_tags(self):
        """Both sides of a warm id-less header slot moved their tags off
        the recorded base, differently: no safe mirror source (P8)."""
        de = _build(self.HZ, HEADER_DE, _slide("s0", "de", "Titel"))
        en = _build(self.HZ, HEADER_EN, _slide("s0", "en", "Title"))
        base = _snapshot(de, en)
        de2 = de.replace('tags=["alt"]', 'tags=["beta"]')
        en2 = en.replace('tags=["alt"]', 'tags=["gamma"]')
        item = _only_item(_diff(base, de2, en2))
        assert (item.outcome, item.action) == ("conflict", "conflict_tags")
        assert item.direction == "both"
        assert item.key == "pos:~header/markdown/0"

    def test_tuple_order_only_difference_normalizes_without_conflict(self):
        """Parity is judged on tag SETS: a one-sided reorder of the same
        tags stays on the bodies-at-base normalization path."""
        tagged_de = self.DE_M.replace(" slide_id", ' tags=["notes", "alt"] slide_id')
        tagged_en = self.EN_M.replace(" slide_id", ' tags=["notes", "alt"] slide_id')
        de = DE0.replace(self.DE_M, tagged_de)
        en = EN0.replace(self.EN_M, tagged_en)
        base = _snapshot(de, en)
        de2 = de.replace('tags=["notes", "alt"]', 'tags=["alt", "notes"]')
        item = _only_item(_diff(base, de2, en))
        assert (item.outcome, item.action) == ("mechanical", "mirror_tags")
        assert item.direction == "de_to_en"

    def test_contrast_case_bodies_at_base_one_sided_tag_edit_mirrors(self):
        """The issue's contrast case keeps its behavior: a tag edit with
        both bodies at base is one mechanical mirror_tags, nothing else."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace(self.DE_M, self.DE_M.replace(" slide_id", ' tags=["notes"] slide_id'))
        item = _only_item(_diff(base, de, EN0))
        assert (item.outcome, item.action) == ("mechanical", "mirror_tags")
        assert (item.direction, item.side) == ("de_to_en", "de")

    def test_pool_header_slot_tag_move_emits_pool_mirror_tags_row(self):
        """The pool path (id-less localized per-language header slot): a
        one-sided tag move splits the cross-side parse pairing, so the
        slot's sides live on different members — the row carries the
        DE-carrier as member and the EN-carrier as twin."""
        hz = '# %% [markdown] tags=["notes"]\n# HZ\n\n'
        de = _build(hz, HEADER_DE, _slide("s0", "de", "Titel"))
        en = _build(hz, HEADER_EN, _slide("s0", "en", "Title"))
        base = _snapshot(de, en)
        de2 = de.replace('tags=["notes"]\n# HZ', 'tags=["voiceover"]\n# HZ')
        item = _only_item(_diff(base, de2, en))
        assert (item.outcome, item.action) == ("mechanical", "mirror_tags")
        assert item.key == "pos:~header/markdown/0"
        assert (item.direction, item.side) == ("de_to_en", "de")
        assert item.member is not None and item.member.de is not None
        assert item.twin is not None and item.twin.en is not None  # pair_twin convention
        assert item.member.de.tags == ("voiceover",)
        assert item.twin.en.tags == ("notes",)


class TestCrossPlacedNoEvidence:
    """#654 review finding 1: a member sitting under different physical
    group brackets per side, with no recorded placement evidence, frames a
    PLACEMENT decision on the member — never a scope reorder (the merged
    owner token cannot express which side is displaced, and a scope-level
    answer would permute cells across group brackets)."""

    DE = _build(
        HEADER_DE,
        _slide("s0", "de", "Eins"),
        _localized("m", "de", "DE-m"),
        _localized("k", "de", "DE-k"),
        _slide("s1", "de", "Zwei"),
        _localized("n", "de", "DE-n"),
    )
    EN_ALIGNED = _build(
        HEADER_EN,
        _slide("s0", "en", "One"),
        _localized("m", "en", "EN-m"),
        _localized("k", "en", "EN-k"),
        _slide("s1", "en", "Two"),
        _localized("n", "en", "EN-n"),
    )
    # m sits under s0 on DE but under s1 on EN.
    EN_CROSS = _build(
        HEADER_EN,
        _slide("s0", "en", "One"),
        _localized("k", "en", "EN-k"),
        _slide("s1", "en", "Two"),
        _localized("m", "en", "EN-m"),
        _localized("n", "en", "EN-n"),
    )

    def test_cold_cross_placed_member_frames_placement_not_scope_reorder(self):
        diff = diff_deck(_parse(self.DE, self.EN_CROSS), None)
        order_items = [i for i in diff.items if i.outcome == "order"]
        assert [(i.action, i.key, i.direction) for i in order_items] == [
            ("order_decision", "id:m", "none")
        ], [(i.key, i.action, i.detail) for i in diff.items]
        assert "different groups" in order_items[0].detail
        # The placement decision suppresses the member's verify_cold row
        # (two framed rows on one key cannot both be answered); the member
        # re-frames cold once placed.
        assert not any(i.action == "verify_cold" and i.key == "id:m" for i in diff.items)

    def test_order_blind_cross_placed_member_frames_placement(self):
        base = _order_blind(_snapshot(self.DE, self.EN_ALIGNED))
        diff = _diff(base, self.DE, self.EN_CROSS)
        # The EN body edit is the move itself — content unchanged, so the
        # placement row is the only item.
        assert [(i.outcome, i.action, i.key) for i in diff.items] == [
            ("order", "order_decision", "id:m")
        ], [(i.key, i.action, i.detail) for i in diff.items]
