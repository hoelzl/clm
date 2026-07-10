"""Tests for :mod:`clm.slides.bilingual_doc` — the sync v3 document model.

The model itself is thin (parsing/projection live in ``doc_lenses`` and are
tested in ``test_doc_lenses.py``); what is pinned here is the identity
surface: :class:`MemberKey` render/parse totality and the model accessors
agents will navigate by (#520 Phase 1, design §3).
"""

from __future__ import annotations

import pytest

from clm.slides.bilingual_doc import (
    HEADER_GROUP,
    BilingualDeck,
    Member,
    MemberKey,
    NormalizeRefusal,
    RefusalReason,
    SideCell,
    SlideGroup,
)


class TestMemberKey:
    def test_id_key_renders_and_parses(self):
        key = MemberKey.for_id("intro-functions")
        assert key.render() == "id:intro-functions"
        assert MemberKey.parse("id:intro-functions") == key

    def test_positional_key_renders_and_parses(self):
        key = MemberKey.positional(HEADER_GROUP, "j2", 0)
        assert key.render() == "pos:~header/j2/0"
        assert MemberKey.parse("pos:~header/j2/0") == key

    def test_keys_are_hashable_values(self):
        assert MemberKey.for_id("a") == MemberKey.for_id("a")
        assert len({MemberKey.for_id("a"), MemberKey.for_id("a")}) == 1
        assert MemberKey.for_id("a") != MemberKey.positional("g", "markdown", 0)

    @pytest.mark.parametrize("bad", ["", "intro", "idx:intro", "id:", ":x"])
    def test_parse_rejects_non_keys(self, bad: str):
        with pytest.raises(ValueError):
            MemberKey.parse(bad)


def _member(key: MemberKey) -> Member:
    side = SideCell(
        lines=('# %% tags=["keep"]', "x = 1", ""),
        index=0,
        line_number=1,
        part="deck",
        lang_attr=None,
        tags=("keep",),
        slide_id=None,
        for_slide=None,
        vo_anchor=None,
        cell_type="code",
    )
    return Member(
        key=key,
        kind="code",
        role="code",
        langness="shared",
        layout="inline",
        owner=None,
        de=side,
        en=side,
    )


class TestBilingualDeck:
    def _deck(self) -> BilingualDeck:
        anchor = _member(MemberKey.for_id("intro"))
        group = SlideGroup(anchor_id="intro", anchor=anchor)
        group.members.append(_member(MemberKey.positional("intro", "code", 0)))
        return BilingualDeck(
            comment_token="#",
            de_deck_preamble=(),
            en_deck_preamble=(),
            de_companion_preamble=None,
            en_companion_preamble=("",),
            groups=[group],
        )

    def test_members_iterates_anchor_and_members(self):
        deck = self._deck()
        keys = [m.key.render() for m in deck.members()]
        assert keys == ["id:intro", "pos:intro/code/0"]

    def test_member_by_key(self):
        deck = self._deck()
        assert deck.member_by_key(MemberKey.for_id("intro")) is not None
        assert deck.member_by_key(MemberKey.for_id("absent")) is None

    def test_has_companion_distinguishes_absent_from_empty(self):
        deck = self._deck()
        assert not deck.has_companion("de")  # None = file absent
        assert deck.has_companion("en")  # empty tuple-ish = present file


class TestNormalizeRefusal:
    def test_render_enumerates_every_reason(self):
        refusal = NormalizeRefusal(
            reasons=[
                RefusalReason(code="duplicate_id", detail="id x twice"),
                RefusalReason(code="idless_localized", detail="line 7"),
            ]
        )
        text = refusal.render()
        assert "normalize" in text
        assert "duplicate_id" in text
        assert "idless_localized" in text

    def test_render_appends_remediation_hint_once_per_code(self):
        # duplicate_id names its fix (rename-id) — normalize cannot fix it —
        # and the hint appears once even for multiple reasons with the code.
        refusal = NormalizeRefusal(
            reasons=[
                RefusalReason(code="duplicate_id", detail="id x twice"),
                RefusalReason(code="duplicate_id", detail="id y twice"),
            ]
        )
        assert refusal.render().count("clm slides rename-id") == 1

    def test_render_no_hint_for_codes_normalize_fixes(self):
        refusal = NormalizeRefusal(
            reasons=[RefusalReason(code="idless_localized", detail="line 7")]
        )
        assert "hint:" not in refusal.render()
