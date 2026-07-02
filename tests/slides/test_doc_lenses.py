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
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=80)
def test_round_trip_survives_one_sided_mutations(
    bundle: tuple[str, str, str | None, str | None], data: st.DataObject
) -> None:
    """Byte-identity and reparse-identity are unconditional on parseable
    input: whatever one-sided edit an author makes, either the parse refuses
    (framed) or the round trip holds."""
    de, en, de_c, en_c = bundle
    mutation = data.draw(st.sampled_from(["edit_shared", "drop_en_cell", "add_en_cell", "none"]))
    if mutation == "edit_shared":
        en = en.replace(" = 1", " = 2")
    elif mutation == "drop_en_cell":
        cells = en.split("\n# %%")
        if len(cells) > 1:
            idx = data.draw(st.integers(min_value=1, max_value=len(cells) - 1))
            cells.pop(idx)
            en = "\n# %%".join(cells)
    elif mutation == "add_en_cell":
        en = en + "\n" + _strip_final_blank(_localized("extra-en", "en", "added"))
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
