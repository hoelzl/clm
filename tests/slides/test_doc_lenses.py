"""Round-trip law suite for :mod:`clm.slides.doc_lenses` (#520 Phase 1).

The non-negotiable laws (design §4)::

    project(parse_bundle(...).deck, lang, part) == input text   # byte-identity
    parse_bundle(*(project(deck, ...) for each file)) == deck   # identity

Structure mirrors ``test_split.py`` — the proven template for lens law
suites: tiny string builders + one-shape-per-test unit cases for diagnosable
failures, Hypothesis properties over generated bundles, and negative tests
pinning the framed-refusal contract (§3.4: refusals are typed and
enumerated, never exceptions, never heuristic parses).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from clm.slides.bilingual_doc import MemberKey, ParseOutcome
from clm.slides.doc_lenses import parse_bundle, project

# ---------------------------------------------------------------------------
# Builders — DE and EN halves constructed in lockstep
# ---------------------------------------------------------------------------

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _shared_code(name: str, value: int = 1) -> str:
    return f'# %% tags=["keep"]\n{name} = {value}\n\n'


def _inline_vo(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{slug}"\n# {text}\n\n'


def _companion_cell(slug: str, lang: str, owner: str, text: str, tag: str = "notes") -> str:
    return (
        f'# %% [markdown] lang="{lang}" tags=["{tag}"] for_slide="{owner}" '
        f'vo_anchor="id:{owner}#0" slide_id="{slug}"\n#\n# - {text}\n\n'
    )


def _strip_final_blank(text: str) -> str:
    """Builders end blocks with a blank separator; files end with one newline."""
    return text[:-1] if text.endswith("\n\n") else text


def _assert_round_trip(
    de: str,
    en: str,
    de_comp: str | None = None,
    en_comp: str | None = None,
    comment_token: str = "#",
) -> ParseOutcome:
    outcome = parse_bundle(de, en, de_comp, en_comp, comment_token=comment_token)
    assert outcome.ok, outcome.refusal.render() if outcome.refusal else "?"
    deck = outcome.deck
    assert deck is not None
    assert project(deck, "de", "deck") == de
    assert project(deck, "en", "deck") == en
    assert project(deck, "de", "companion") == de_comp
    assert project(deck, "en", "companion") == en_comp
    # The other direction: parsing the projections yields the same document.
    again = parse_bundle(
        project(deck, "de", "deck") or "",
        project(deck, "en", "deck") or "",
        project(deck, "de", "companion"),
        project(deck, "en", "companion"),
        comment_token=comment_token,
    )
    assert again.ok
    assert again.deck == deck
    return outcome


# ---------------------------------------------------------------------------
# Direct unit cases — one shape per test
# ---------------------------------------------------------------------------


class TestParseNormalizedDeck:
    def _pair(self) -> tuple[str, str]:
        de = _strip_final_blank(
            HEADER_DE
            + _slide("intro", "de", "Einführung")
            + _localized("intro-note", "de", "Hinweis")
            + _shared_code("x")
            + _slide("details", "de", "Details")
        )
        en = _strip_final_blank(
            HEADER_EN
            + _slide("intro", "en", "Introduction")
            + _localized("intro-note", "en", "Note")
            + _shared_code("x")
            + _slide("details", "en", "Details")
        )
        return de, en

    def test_round_trips_byte_identically(self):
        de, en = self._pair()
        _assert_round_trip(de, en)

    def test_structure_and_keys(self):
        de, en = self._pair()
        deck = parse_bundle(de, en).deck
        assert deck is not None
        assert deck.observations == []
        # Header zone: the j2 import line; the macro anchors the title group.
        assert [m.key.render() for m in deck.header] == ["pos:~header/j2/0"]
        assert [g.anchor_id for g in deck.groups] == ["title", "intro", "details"]
        title = deck.groups[0]
        assert title.anchor is not None
        assert title.anchor.key == MemberKey.for_id("title")
        assert title.anchor.role == "header"
        intro = deck.groups[1]
        member_keys = [m.key.render() for m in intro.members]
        assert member_keys == ["id:intro-note", "pos:intro/code/0"]
        note = intro.members[0]
        assert note.langness == "localized"
        assert note.role == "aux"
        assert note.owner == MemberKey.for_id("intro")
        shared = intro.members[1]
        assert shared.langness == "shared"
        assert shared.role == "code"

    def test_localized_code_cell_gets_code_role(self):
        de = _strip_final_blank(
            HEADER_DE
            + _slide("a", "de", "A")
            + '# %% lang="de" slide_id="a-code"\nprint("Hallo")\n\n'
        )
        en = _strip_final_blank(
            HEADER_EN
            + _slide("a", "en", "A")
            + '# %% lang="en" slide_id="a-code"\nprint("Hello")\n\n'
        )
        outcome = _assert_round_trip(de, en)
        assert outcome.deck is not None
        member = outcome.deck.member_by_key(MemberKey.for_id("a-code"))
        assert member is not None
        assert member.role == "code"
        assert member.langness == "localized"

    def test_preserve_marker_id_keys_bare(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + _localized("!kept-id", "de", "T")
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + _localized("!kept-id", "en", "T")
        )
        outcome = _assert_round_trip(de, en)
        assert outcome.deck is not None
        member = outcome.deck.member_by_key(MemberKey.for_id("kept-id"))
        assert member is not None
        assert member.de is not None
        assert member.de.slide_id == "!kept-id"  # bytes stay verbatim


class TestCompanions:
    def test_companion_members_join_their_owner_group(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        de_c = _strip_final_blank(
            _companion_cell("a-greet", "de", "title", "Willkommen!")
            + _companion_cell("a-vo", "de", "a", "Hinweis.")
        )
        en_c = _strip_final_blank(
            _companion_cell("a-greet", "en", "title", "Welcome!")
            + _companion_cell("a-vo", "en", "a", "Note.")
        )
        outcome = _assert_round_trip(de, en, de_c, en_c)
        deck = outcome.deck
        assert deck is not None
        assert deck.observations == []
        assert deck.orphans == []
        title, group_a = deck.groups
        assert [m.key.render() for m in title.members] == ["id:a-greet"]
        assert title.members[0].layout == "companion"
        assert title.members[0].owner == MemberKey.for_id("title")
        assert [m.key.render() for m in group_a.members] == ["id:a-vo"]
        assert group_a.members[0].role == "notes"

    def test_orphan_companion_cell_is_kept_and_observed(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        de_c = _strip_final_blank(_companion_cell("lost", "de", "no-such-slide", "Text"))
        en_c = _strip_final_blank(_companion_cell("lost", "en", "no-such-slide", "Text"))
        outcome = _assert_round_trip(de, en, de_c, en_c)
        deck = outcome.deck
        assert deck is not None
        assert [m.key.render() for m in deck.orphans] == ["id:lost"]
        assert [o.kind for o in deck.observations] == ["owner_missing"]

    def test_missing_companion_projects_to_none(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        deck = parse_bundle(de, en).deck
        assert deck is not None
        assert project(deck, "de", "companion") is None
        assert project(deck, "en", "companion") is None

    def test_empty_companion_string_round_trips_as_empty(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        _assert_round_trip(de, en, "", "")

    def test_solo_companion_is_cross_language_layout(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        de_c = _strip_final_blank(_companion_cell("a-vo", "de", "a", "Nur DE."))
        outcome = _assert_round_trip(de, en, de_c, None)
        deck = outcome.deck
        assert deck is not None
        kinds = [o.kind for o in deck.observations]
        assert "layout_cross_language" in kinds
        assert "one_sided_member" in kinds

    def test_inline_voiceover_plus_companion_is_layout_mixed(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + _inline_vo("a-inline", "de", "Inline.")
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + _inline_vo("a-inline", "en", "Inline.")
        )
        de_c = _strip_final_blank(_companion_cell("a-vo", "de", "a", "Companion."))
        en_c = _strip_final_blank(_companion_cell("a-vo", "en", "a", "Companion."))
        outcome = _assert_round_trip(de, en, de_c, en_c)
        deck = outcome.deck
        assert deck is not None
        assert {o.kind for o in deck.observations} == {"layout_mixed"}

    def test_mid_relayout_member_pairs_across_parts(self):
        # The same id inline on the DE half but in the EN companion: one
        # member, mid-relayout (design §7.3 / P2) — never two members.
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + _inline_vo("a-vo", "de", "Inline DE.")
        )
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        en_c = _strip_final_blank(
            _companion_cell("a-vo", "en", "a", "Companion EN.", tag="voiceover")
        )
        outcome = _assert_round_trip(de, en, None, en_c)
        deck = outcome.deck
        assert deck is not None
        member = deck.member_by_key(MemberKey.for_id("a-vo"))
        assert member is not None
        assert member.de is not None and member.de.part == "deck"
        assert member.en is not None and member.en.part == "companion"
        assert "layout_cross_language" in {o.kind for o in deck.observations}


class TestObservations:
    def test_shared_divergence_is_observed_and_bytes_kept(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A") + _shared_code("x", 1))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A") + _shared_code("x", 2))
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        assert [o.kind for o in deck.observations] == ["shared_divergence"]

    def test_one_sided_member_is_observed(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + _localized("en-only", "en", "Extra")
        )
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        obs = [o for o in deck.observations if o.kind == "one_sided_member"]
        assert len(obs) == 1
        assert obs[0].member == MemberKey.for_id("en-only")
        assert obs[0].side == "en"

    def test_443_shape_is_a_transition_not_a_refusal(self):
        # Id'd on the DE half, id-less twin on the EN half: one member under
        # the id'd side's key with an id-stamp-pending observation (§3.3).
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A") + _localized("noted", "de", "T"))
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + '# %% [markdown] lang="en"\n# T\n\n'
        )
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        member = deck.member_by_key(MemberKey.for_id("noted"))
        assert member is not None
        assert member.de is not None and member.en is not None
        assert [o.kind for o in deck.observations] == ["id_stamp_pending_twin"]

    def test_one_sided_group_is_observed(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A") + _slide("b", "de", "B"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        kinds = [o.kind for o in deck.observations]
        assert "one_sided_group" in kinds

    def test_group_order_divergence_is_observed(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A") + _slide("b", "de", "B"))
        en = _strip_final_blank(HEADER_EN + _slide("b", "en", "B") + _slide("a", "en", "A"))
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        assert "group_order_divergence" in {o.kind for o in deck.observations}

    def test_wrong_language_cell_is_observed(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + _localized("odd", "en", "englisch im DE-File")
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + _localized("odd2", "en", "fine")
        )
        outcome = parse_bundle(de, en)
        assert outcome.ok
        assert outcome.deck is not None
        assert "wrong_language_cell" in {o.kind for o in outcome.deck.observations}

    def test_preamble_divergence_is_observed(self):
        de = "# leading DE comment\n" + _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        outcome = _assert_round_trip(de, en)
        assert outcome.deck is not None
        assert "preamble_divergence" in {o.kind for o in outcome.deck.observations}


class TestRefusals:
    def test_legacy_bundled_corpus_pair_refuses(self):
        # The v2-era bundled corpus deck carries the inherited-id voiceover
        # and id-less localized shapes — under the §3.4 precondition it must
        # refuse with the duplicate enumerated on both sides.
        data = Path(__file__).parent.parent / "data" / "sync_corpus"
        de = (data / "deck_features.de.py").read_text(encoding="utf-8")
        en = (data / "deck_features.en.py").read_text(encoding="utf-8")
        outcome = parse_bundle(de, en)
        assert not outcome.ok
        assert outcome.refusal is not None
        codes = [r.code for r in outcome.refusal.reasons]
        assert codes.count("duplicate_id") == 2  # "intro" on each side
        assert "normalize" in outcome.refusal.render()

    def test_duplicate_id_within_one_side_refuses(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + _localized("a", "de", "shadows the anchor")
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + _localized("a", "en", "shadows the anchor")
        )
        outcome = parse_bundle(de, en)
        assert not outcome.ok
        assert outcome.refusal is not None
        assert {r.code for r in outcome.refusal.reasons} == {"duplicate_id"}

    def test_companion_inherit_shape_refuses(self):
        # Legacy companions whose narrative slide_id equals for_slide (the
        # owner's id) duplicate the deck id inside the ≤4-file namespace.
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        de_c = _strip_final_blank(_companion_cell("a", "de", "a", "geerbt"))
        en_c = _strip_final_blank(_companion_cell("a", "en", "a", "inherited"))
        outcome = parse_bundle(de, en, de_c, en_c)
        assert not outcome.ok
        assert outcome.refusal is not None
        assert {r.code for r in outcome.refusal.reasons} == {"duplicate_id"}

    def test_idless_anchor_refuses(self):
        de = _strip_final_blank(HEADER_DE + '# %% [markdown] lang="de" tags=["slide"]\n# # A\n\n')
        en = _strip_final_blank(HEADER_EN + '# %% [markdown] lang="en" tags=["slide"]\n# # A\n\n')
        outcome = parse_bundle(de, en)
        assert not outcome.ok
        assert outcome.refusal is not None
        codes = [r.code for r in outcome.refusal.reasons]
        assert codes == ["idless_anchor", "idless_anchor"]  # enumerated per side

    def test_idless_localized_on_both_halves_refuses(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + '# %% [markdown] lang="de"\n# ohne Id\n\n'
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + '# %% [markdown] lang="en"\n# no id\n\n'
        )
        outcome = parse_bundle(de, en)
        assert not outcome.ok
        assert outcome.refusal is not None
        assert {r.code for r in outcome.refusal.reasons} == {"idless_localized"}

    def test_idless_inline_narrative_refuses(self):
        de = _strip_final_blank(
            HEADER_DE
            + _slide("a", "de", "A")
            + '# %% [markdown] lang="de" tags=["voiceover"]\n# ohne Id\n\n'
        )
        en = _strip_final_blank(
            HEADER_EN
            + _slide("a", "en", "A")
            + '# %% [markdown] lang="en" tags=["voiceover"]\n# no id\n\n'
        )
        outcome = parse_bundle(de, en)
        assert not outcome.ok
        assert outcome.refusal is not None
        assert {r.code for r in outcome.refusal.reasons} == {"idless_narrative"}

    def test_refusal_never_raises(self):
        # Degenerate input: not a deck at all — still a typed outcome.
        outcome = parse_bundle("just some text\n", "other text\n")
        assert outcome.ok  # no cells, no ids: an empty but valid document
        deck = outcome.deck
        assert deck is not None
        assert project(deck, "de", "deck") == "just some text\n"
        assert project(deck, "en", "deck") == "other text\n"

    def test_empty_slide_id_anchor_refuses_as_idless(self):
        # slide_id="" (and "!") carries no identity — it must hit the
        # idless_anchor refusal, not mint an empty or line-number key.
        de = _strip_final_blank(
            HEADER_DE + '# %% [markdown] lang="de" tags=["slide"] slide_id=""\n# # A\n\n'
        )
        en = _strip_final_blank(
            HEADER_EN + '# %% [markdown] lang="en" tags=["slide"] slide_id="!"\n# # A\n\n'
        )
        outcome = parse_bundle(de, en)
        assert not outcome.ok
        assert outcome.refusal is not None
        assert {r.code for r in outcome.refusal.reasons} == {"idless_anchor"}


class TestPairingAlignment:
    """The #443 adoption must be slot-aware (review finding: interleaved
    id'd cells previously stole the wrong twin via tail-residue pairing)."""

    def test_443_interleaved_shared_cells_adopt_the_matching_twin(self):
        # Three shared cells A/X/B; the author stamped an id on DE's X only.
        # X must adopt EN's byte-equal x — not shift B onto the wrong twin.
        cells = [_shared_code("cell_a", 1), _shared_code("cell_x", 2), _shared_code("cell_b", 3)]
        de = _strip_final_blank(
            HEADER_DE
            + _slide("g", "de", "G")
            + cells[0]
            + cells[1].replace('tags=["keep"]', 'tags=["keep"] slide_id="x-cell"')
            + cells[2]
        )
        en = _strip_final_blank(HEADER_EN + _slide("g", "en", "G") + "".join(cells))
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        member = deck.member_by_key(MemberKey.for_id("x-cell"))
        assert member is not None
        assert member.de is not None and member.en is not None
        assert member.de.lines[1:] == member.en.lines[1:]  # the true twin
        kinds = [o.kind for o in deck.observations]
        assert kinds == ["id_stamp_pending_twin"]  # no false shared_divergence

    def test_new_one_sided_idd_shared_cell_does_not_steal_a_twin(self):
        # A genuinely new id'd shared cell on DE only (different body): it
        # must stay one-sided and leave the id-less alignment untouched.
        de = _strip_final_blank(
            HEADER_DE
            + _slide("g", "de", "G")
            + '# %% tags=["keep"] slide_id="new-cell"\nbrand_new = 0\n\n'
            + _shared_code("cell_a", 1)
            + _shared_code("cell_b", 2)
        )
        en = _strip_final_blank(
            HEADER_EN
            + _slide("g", "en", "G")
            + _shared_code("cell_a", 1)
            + _shared_code("cell_b", 2)
        )
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        member = deck.member_by_key(MemberKey.for_id("new-cell"))
        assert member is not None
        assert member.en is None  # one-sided, nothing adopted
        kinds = [o.kind for o in deck.observations]
        assert kinds == ["one_sided_member"]  # and no divergence noise

    def test_reverse_443_direction_en_idd_de_idless(self):
        # The mirror of test_443_shape...: id'd on the EN half, id-less DE
        # twin — one member under the EN id, observation side="de".
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + '# %% [markdown] lang="de"\n# T\n\n'
        )
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A") + _localized("noted", "en", "T"))
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        member = deck.member_by_key(MemberKey.for_id("noted"))
        assert member is not None
        assert member.de is not None and member.en is not None
        obs = [o for o in deck.observations if o.kind == "id_stamp_pending_twin"]
        assert len(obs) == 1
        assert obs[0].side == "de"
        assert obs[0].member == MemberKey.for_id("noted")

    def test_reverse_443_in_companion_files(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        de_c = _strip_final_blank(
            '# %% [markdown] lang="de" tags=["notes"] for_slide="a"\n#\n# - Text\n\n'
        )
        en_c = _strip_final_blank(_companion_cell("a-vo", "en", "a", "Text"))
        outcome = _assert_round_trip(de, en, de_c, en_c)
        deck = outcome.deck
        assert deck is not None
        member = deck.member_by_key(MemberKey.for_id("a-vo"))
        assert member is not None
        assert member.de is not None and member.en is not None
        obs = [o for o in deck.observations if o.kind == "id_stamp_pending_twin"]
        assert len(obs) == 1 and obs[0].side == "de"


class TestObservationKeys:
    """Observations must carry the member's FINAL key (P1: identity computed
    once and carried unchanged) — never the pre-ordinal sentinel."""

    def test_one_sided_idless_member_observation_resolves(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + "# %%\n# de-only shared extra\n\n"
        )
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        obs = [o for o in deck.observations if o.kind == "one_sided_member"]
        assert len(obs) == 1
        assert obs[0].member is not None
        assert obs[0].member.render() == "pos:a/code/0"
        assert deck.member_by_key(obs[0].member) is not None

    def test_two_diverged_shared_cells_get_distinct_observation_keys(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + _shared_code("x", 1) + _shared_code("y", 1)
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + _shared_code("x", 2) + _shared_code("y", 2)
        )
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        obs = [o for o in deck.observations if o.kind == "shared_divergence"]
        assert len(obs) == 2
        keys = {o.member.render() for o in obs if o.member is not None}
        assert keys == {"pos:a/code/0", "pos:a/code/1"}
        for o in obs:
            assert o.member is not None
            assert deck.member_by_key(o.member) is not None


class TestObservationKinds:
    """Each documented observation kind fires from a realistic input."""

    def test_member_kind_mismatch(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + '# %% [markdown] lang="de" slide_id="odd"\n# T\n\n'
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + '# %% lang="en" slide_id="odd"\nprint("t")\n\n'
        )
        outcome = _assert_round_trip(de, en)
        assert outcome.deck is not None
        obs = {o.kind for o in outcome.deck.observations}
        assert "member_kind_mismatch" in obs

    def test_lang_attr_mismatch(self):
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + '# %% [markdown] lang="de" slide_id="odd"\n# T\n\n'
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + '# %% [markdown] slide_id="odd"\n# T\n\n'
        )
        outcome = _assert_round_trip(de, en)
        assert outcome.deck is not None
        obs = [o for o in outcome.deck.observations if o.kind == "lang_attr_mismatch"]
        assert len(obs) == 1
        assert obs[0].member == MemberKey.for_id("odd")

    def test_owner_mismatch(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A") + _slide("b", "de", "B"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A") + _slide("b", "en", "B"))
        de_c = _strip_final_blank(_companion_cell("vo", "de", "a", "Text"))
        en_c = _strip_final_blank(_companion_cell("vo", "en", "b", "Text"))
        outcome = _assert_round_trip(de, en, de_c, en_c)
        assert outcome.deck is not None
        obs = [o for o in outcome.deck.observations if o.kind == "owner_mismatch"]
        assert len(obs) == 1
        assert obs[0].member == MemberKey.for_id("vo")

    def test_unexpected_companion_cell(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        de_c = _strip_final_blank('# %% lang="de" slide_id="odd-code"\nprint("?")\n\n')
        en_c = _strip_final_blank('# %% lang="en" slide_id="odd-code"\nprint("?")\n\n')
        outcome = _assert_round_trip(de, en, de_c, en_c)
        assert outcome.deck is not None
        assert "unexpected_companion_cell" in {o.kind for o in outcome.deck.observations}

    def test_legacy_title_companion_refuses_with_the_actual_fix(self):
        # Pre-#242 shape: slide_id="title", no for_slide. The "title" id is an
        # owner reference, not the member's identity — keying it would collide
        # with the title anchor. Like `--stamp-ids`, the parse refuses the
        # unowned shape rather than guessing, and the detail names the real
        # fix instead of a misleading duplicate_id.
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        de_c = _strip_final_blank(
            '# %% [markdown] lang="de" tags=["notes"] slide_id="title"\n#\n# - Gruß\n\n'
        )
        en_c = _strip_final_blank(
            '# %% [markdown] lang="en" tags=["notes"] slide_id="title"\n#\n# - Greeting\n\n'
        )
        outcome = parse_bundle(de, en, de_c, en_c)
        assert not outcome.ok
        assert outcome.refusal is not None
        codes = {r.code for r in outcome.refusal.reasons}
        assert "legacy_title_companion" in codes
        assert "duplicate_id" not in codes  # never the misleading collision
        legacy = next(r for r in outcome.refusal.reasons if r.code == "legacy_title_companion")
        assert 'for_slide="title"' in legacy.detail

    def test_legacy_title_companion_refuses_without_title_macro_too(self):
        de = _strip_final_blank(_slide("a", "de", "A"))
        en = _strip_final_blank(_slide("a", "en", "A"))
        de_c = _strip_final_blank(
            '# %% [markdown] lang="de" tags=["notes"] slide_id="title"\n#\n# - Gruß\n\n'
        )
        en_c = _strip_final_blank(
            '# %% [markdown] lang="en" tags=["notes"] slide_id="title"\n#\n# - Greeting\n\n'
        )
        outcome = parse_bundle(de, en, de_c, en_c)
        assert not outcome.ok
        assert outcome.refusal is not None
        assert "legacy_title_companion" in {r.code for r in outcome.refusal.reasons}


class TestGroupStructure:
    def test_subslide_anchors_its_own_group(self):
        sub = '# %% [markdown] lang="{lang}" tags=["subslide"] slide_id="a-sub"\n#\n# ## Sub\n\n'
        de = _strip_final_blank(
            HEADER_DE + _slide("a", "de", "A") + sub.format(lang="de") + _shared_code("x")
        )
        en = _strip_final_blank(
            HEADER_EN + _slide("a", "en", "A") + sub.format(lang="en") + _shared_code("x")
        )
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        assert [g.anchor_id for g in deck.groups] == ["title", "a", "a-sub"]
        sub_group = deck.groups[2]
        assert sub_group.anchor is not None
        assert sub_group.anchor.role == "subslide"
        # The shared cell after the subslide belongs to the SUBSLIDE's group.
        assert [m.key.render() for m in sub_group.members] == ["pos:a-sub/code/0"]

    def test_preface_group_without_title_macro(self):
        de = _strip_final_blank("# %%\nimport math\n\n" + _slide("a", "de", "A"))
        en = _strip_final_blank("# %%\nimport math\n\n" + _slide("a", "en", "A"))
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        assert deck.header == []
        assert [g.anchor_id for g in deck.groups] == ["~preface", "a"]
        preface = deck.groups[0]
        assert preface.anchor is None
        assert [m.key.render() for m in preface.members] == ["pos:~preface/code/0"]


class TestByteEdgeCases:
    def test_leading_blank_line_survives(self):
        # raw_cells.reconstruct drops an empty preamble; the lens must not.
        de = "\n" + _strip_final_blank(HEADER_DE + _slide("a", "de", "A"))
        en = "\n" + _strip_final_blank(HEADER_EN + _slide("a", "en", "A"))
        _assert_round_trip(de, en)

    def test_no_trailing_newline_survives(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A")).rstrip("\n")
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A")).rstrip("\n")
        _assert_round_trip(de, en)

    def test_extra_blank_runs_survive(self):
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A")) + "\n\n\n"
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A")) + "\n\n\n"
        _assert_round_trip(de, en)

    def test_cpp_comment_token_round_trips(self):
        de = (
            "// j2 from 'macros.j2' import header_de\n"
            '// {{ header_de("Titel") }}\n\n'
            '// %% [markdown] lang="de" tags=["slide"] slide_id="cpp-a"\n// # A\n\n'
            '// %% tags=["keep"]\nint x = 1;\n'
        )
        en = (
            "// j2 from 'macros.j2' import header_en\n"
            '// {{ header_en("Title") }}\n\n'
            '// %% [markdown] lang="en" tags=["slide"] slide_id="cpp-a"\n// # A\n\n'
            '// %% tags=["keep"]\nint x = 1;\n'
        )
        _assert_round_trip(de, en, comment_token="//")

    def test_j2_widget_cell_mid_group_is_a_shared_member(self):
        widget = '# {{ img("diagram.png") }}\n\n'
        de = _strip_final_blank(HEADER_DE + _slide("a", "de", "A") + widget)
        en = _strip_final_blank(HEADER_EN + _slide("a", "en", "A") + widget)
        outcome = _assert_round_trip(de, en)
        deck = outcome.deck
        assert deck is not None
        assert deck.observations == []
        member = deck.member_by_key(MemberKey.parse("pos:a/j2/0"))
        assert member is not None
        assert member.langness == "shared"
        assert member.role == "aux"


# ---------------------------------------------------------------------------
# Hypothesis properties
# ---------------------------------------------------------------------------


@st.composite
def _normalized_bundle(draw) -> tuple[str, str, str | None, str | None]:
    """Generate a canonical normalized bundle (§3.4 steady state).

    Every localized/narrative cell is id'd, DE/EN twins share ids, shared
    cells are byte-identical, companions mirror. Non-canonical shapes are
    covered by the mutation property below and by the unit tests.
    """
    n_groups = draw(st.integers(min_value=1, max_value=4))
    de_parts, en_parts = [HEADER_DE], [HEADER_EN]
    comp_de: list[str] = []
    comp_en: list[str] = []
    # Wholly-inline-or-wholly-companion (the #501 invariant): a canonical
    # deck never mixes inline voiceover with a companion file.
    with_companion = draw(st.booleans())
    member_kinds = ["localized", "shared", "code"] + ([] if with_companion else ["inline_vo"])
    for g in range(n_groups):
        slug = f"s{g}"
        de_parts.append(_slide(slug, "de", f"Titel {g}"))
        en_parts.append(_slide(slug, "en", f"Title {g}"))
        n_members = draw(st.integers(min_value=0, max_value=3))
        for m in range(n_members):
            kind = draw(st.sampled_from(member_kinds))
            mslug = f"{slug}-m{m}"
            if kind == "localized":
                de_parts.append(_localized(mslug, "de", f"DE {mslug}"))
                en_parts.append(_localized(mslug, "en", f"EN {mslug}"))
            elif kind == "shared":
                de_parts.append(_shared_code(f"var_{g}_{m}"))
                en_parts.append(_shared_code(f"var_{g}_{m}"))
            elif kind == "inline_vo":
                de_parts.append(_inline_vo(mslug, "de", f"VO DE {mslug}"))
                en_parts.append(_inline_vo(mslug, "en", f"VO EN {mslug}"))
            else:
                de_parts.append(f'# %% lang="de" slide_id="{mslug}"\nprint("de")\n\n')
                en_parts.append(f'# %% lang="en" slide_id="{mslug}"\nprint("en")\n\n')
        if with_companion and draw(st.booleans()):
            comp_de.append(_companion_cell(f"{slug}-vo", "de", slug, f"Text {slug}"))
            comp_en.append(_companion_cell(f"{slug}-vo", "en", slug, f"Text {slug}"))
    de = _strip_final_blank("".join(de_parts))
    en = _strip_final_blank("".join(en_parts))
    de_c = _strip_final_blank("".join(comp_de)) if comp_de else None
    en_c = _strip_final_blank("".join(comp_en)) if comp_en else None
    return de, en, de_c, en_c


@given(bundle=_normalized_bundle())
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=80)
def test_round_trip_property(bundle: tuple[str, str, str | None, str | None]) -> None:
    de, en, de_c, en_c = bundle
    outcome = _assert_round_trip(de, en, de_c, en_c)
    deck = outcome.deck
    assert deck is not None
    # Canonical bundles parse without noise: no observations, and every
    # localized member is id-keyed (the §3.4 steady state).
    assert deck.observations == []
    for member in deck.members():
        if member.langness == "localized" and member.role != "header":
            assert member.key.scheme == "id"


@given(bundle=_normalized_bundle(), data=st.data())
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=100)
def test_round_trip_survives_one_sided_mutations(
    bundle: tuple[str, str, str | None, str | None], data: st.DataObject
) -> None:
    """Byte-identity and reparse-identity are unconditional on parseable
    input: whatever one-sided edit an author makes — on either half, in the
    deck or in a companion — either the parse refuses (framed) or the round
    trip holds."""
    de, en, de_c, en_c = bundle
    side = data.draw(st.sampled_from(["de", "en"]))
    menu = ["edit_shared", "drop_cell", "add_cell", "strip_id", "none"]
    if (de_c if side == "de" else en_c) is not None:
        menu += ["drop_companion_cell", "strip_companion_id"]
    mutation = data.draw(st.sampled_from(menu))

    def mutate(text: str) -> str:
        if mutation == "edit_shared" and " = 1" in text:
            return text.replace(" = 1", " = 2", 1)
        if mutation == "drop_cell":
            cells = text.split("\n# %%")
            if len(cells) > 1:
                idx = data.draw(st.integers(min_value=1, max_value=len(cells) - 1))
                cells.pop(idx)
                return "\n# %%".join(cells)
        if mutation == "add_cell":
            return text + "\n" + _strip_final_blank(_localized(f"extra-{side}", side, "added"))
        if mutation == "strip_id":
            # Remove the first localized member's slide_id (the #443 shape).
            return re.sub(r'(\[markdown\] lang="[a-z]+") slide_id="[^"]*"', r"\1", text, count=1)
        return text

    def mutate_companion(text: str) -> str:
        if mutation == "drop_companion_cell":
            cells = text.split("\n# %%")
            if len(cells) > 1:
                cells.pop(len(cells) - 1)
                return "\n# %%".join(cells)
        if mutation == "strip_companion_id":
            return re.sub(r' slide_id="[^"]*"', "", text, count=1)
        return text

    if mutation in ("drop_companion_cell", "strip_companion_id"):
        if side == "de" and de_c is not None:
            de_c = mutate_companion(de_c)
        elif en_c is not None:
            en_c = mutate_companion(en_c)
    elif side == "de":
        de = mutate(de)
    else:
        en = mutate(en)

    outcome = parse_bundle(de, en, de_c, en_c)
    if not outcome.ok:
        assert outcome.refusal is not None
        assert outcome.refusal.reasons  # framed, enumerated
        return
    deck = outcome.deck
    assert deck is not None
    assert project(deck, "de", "deck") == de
    assert project(deck, "en", "deck") == en
    assert project(deck, "de", "companion") == de_c
    assert project(deck, "en", "companion") == en_c
    again = parse_bundle(de, en, de_c, en_c)
    assert again.deck == deck
