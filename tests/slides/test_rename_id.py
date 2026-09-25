"""Unit tests for :mod:`clm.slides.rename_id` (issue #572).

Covers the two pure pieces: the per-half text rewrite (``slide_id`` + owner
``for_slide``, marker-preserving, byte-stable on a no-op) and the ledger
key migration (member re-key, owner references, member-order handles, and the
group-anchor positional cascade) — asserting throughout that fingerprints are
*carried*, never recomputed.
"""

from __future__ import annotations

from attrs import evolve

from clm.slides import doc_ledger
from clm.slides.bilingual_doc import BilingualDeck
from clm.slides.doc_lenses import parse_bundle
from clm.slides.rename_id import (
    is_valid_slide_id,
    ledger_knows,
    migrate_ledger_key,
    referenced_ids_in,
    rename_in_half,
    slide_ids_in,
)

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _shared_code(name: str, value: int = 1) -> str:
    return f'# %% tags=["keep"]\n{name} = {value}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


DE0 = _build(
    HEADER_DE, _slide("s0", "de", "Titel"), _shared_code("x"), _localized("s0-m", "de", "DE Text")
)
EN0 = _build(
    HEADER_EN, _slide("s0", "en", "Title"), _shared_code("x"), _localized("s0-m", "en", "EN text")
)


def _parse(de: str, en: str) -> BilingualDeck:
    outcome = parse_bundle(de, en)
    assert outcome.deck is not None
    return outcome.deck


def _deck_ledger(de: str = DE0, en: str = EN0) -> doc_ledger.DeckLedger:
    ledger = doc_ledger.TopicLedger()
    doc_ledger.record_deck_snapshot(ledger, "slides_x", _parse(de, en), provenance="record")
    return ledger.decks["slides_x"]


class TestValidId:
    def test_rejects_whitespace_and_quotes_and_empty(self):
        assert not is_valid_slide_id("")
        assert not is_valid_slide_id("a b")
        assert not is_valid_slide_id('a"b')
        assert is_valid_slide_id("foo-new")
        assert is_valid_slide_id("s0_m.2")


class TestSlideIdsIn:
    def test_collects_bare_ids(self):
        assert slide_ids_in(DE0, "#") == {"s0", "s0-m"}

    def test_strips_preserve_marker(self):
        text = _build(HEADER_DE, _slide("!kept", "de", "Titel"))
        assert slide_ids_in(text, "#") == {"kept"}


class TestRenameInHalf:
    def test_rewrites_slide_id(self):
        out, sid, fs, _ = rename_in_half(DE0, "#", "s0-m", "s0-x")
        assert (sid, fs) == (1, 0)
        assert 'slide_id="s0-x"' in out
        assert 'slide_id="s0-m"' not in out
        # the OTHER id and all bodies are untouched
        assert 'slide_id="s0"' in out and "DE Text" in out

    def test_noop_returns_byte_identical(self):
        out, sid, fs, _ = rename_in_half(DE0, "#", "absent", "whatever")
        assert (sid, fs) == (0, 0)
        assert out == DE0

    def test_preserves_preserve_marker(self):
        text = _build(HEADER_DE, _slide("!keep-me", "de", "Titel"))
        out, sid, _, _ = rename_in_half(text, "#", "keep-me", "renamed")
        assert sid == 1
        assert 'slide_id="!renamed"' in out

    def test_rewrites_for_slide_owner_reference(self):
        text = _build(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="s0"\n# VO\n\n',
        )
        out, sid, fs, _ = rename_in_half(text, "#", "s0", "s0-new")
        assert (sid, fs) == (1, 1)
        assert 'slide_id="s0-new"' in out
        assert 'for_slide="s0-new"' in out
        assert 'for_slide="s0"' not in out


class TestMigrateLedgerNonAnchor:
    def test_rekeys_member_preserving_fingerprints(self):
        deck = _deck_ledger()
        before = deck.members["id:s0-m"].entry
        assert migrate_ledger_key(deck, "s0-m", "s0-x") is True
        assert "id:s0-m" not in deck.members
        after = deck.members["id:s0-x"]
        # migrated, NOT re-fingerprinted: fps carried, key rewritten
        assert after.entry.key == "id:s0-x"
        assert (after.entry.de_fp, after.entry.en_fp) == (before.de_fp, before.en_fp)
        assert after.provenance == deck.members["id:s0-x"].provenance

    def test_swaps_member_order_handle(self):
        deck = _deck_ledger()
        migrate_ledger_key(deck, "s0-m", "s0-x")
        handles = [h for lst in deck.member_order.values() for h in lst]
        assert "id:s0-m" not in handles
        assert "id:s0-x" in handles

    def test_absent_id_is_noop(self):
        deck = _deck_ledger()
        assert migrate_ledger_key(deck, "not-here", "whatever") is False

    def test_migrates_owner_reference(self):
        deck = _deck_ledger()
        # Graft an explicit owner ref onto an existing entry (a companion the
        # renamed slide owns) and confirm the rename follows it.
        orig = deck.members["id:s0-m"]
        deck.members["id:s0-m"] = evolve(orig, entry=evolve(orig.entry, owner="id:s0"))
        migrate_ledger_key(deck, "s0", "s0-new")
        assert deck.members["id:s0-m"].entry.owner == "id:s0-new"


class TestMigrateLedgerAnchorCascade:
    def test_positional_keys_and_group_order_migrate(self):
        deck = _deck_ledger()
        pos_before = [k for k in deck.members if k.startswith("pos:s0/")]
        assert pos_before, "fixture should have a positional member under the s0 group"
        assert "s0" in deck.group_order

        assert migrate_ledger_key(deck, "s0", "s0-new") is True

        # anchor re-keyed; its group token cascaded into every pos: key
        assert "id:s0" not in deck.members and "id:s0-new" in deck.members
        assert not any(k.startswith("pos:s0/") for k in deck.members)
        migrated_pos = [k for k in deck.members if k.startswith("pos:s0-new/")]
        assert len(migrated_pos) == len(pos_before)
        assert "s0" not in deck.group_order and "s0-new" in deck.group_order
        # member-order scope keys re-grouped to the new anchor token
        assert not any(g == "s0" for (_lang, g, _part) in deck.member_order)

    def test_anchor_cascade_preserves_positional_fingerprints(self):
        deck = _deck_ledger()
        pos_key = next(k for k in deck.members if k.startswith("pos:s0/"))
        fp_before = (deck.members[pos_key].entry.de_fp, deck.members[pos_key].entry.en_fp)
        migrate_ledger_key(deck, "s0", "s0-new")
        new_key = pos_key.replace("pos:s0/", "pos:s0-new/", 1)
        assert (deck.members[new_key].entry.de_fp, deck.members[new_key].entry.en_fp) == fp_before


class TestRenameVoAnchor:
    """Regression tests for #990: ``vo_anchor`` tokens follow a renamed id."""

    VO = (
        '# %% [markdown] lang="de" tags=["voiceover"] for_slide="s0" vo_anchor="id:s0#1"\n'
        "# Erzählung\n\n"
        '# %% [markdown] lang="de" tags=["voiceover"] for_slide="other" vo_anchor="fp:abc#0"\n'
        "# Andere\n\n"
        '# %% [markdown] lang="de" tags=["voiceover"] for_slide="s0-x" vo_anchor="id:s0-x#0"\n'
        "# Nicht s0\n"
    )

    def test_rewrites_id_anchor_keeping_the_ordinal(self):
        out, sid, fs, va = rename_in_half(self.VO, "#", "s0", "s0-new")
        assert (sid, fs, va) == (0, 1, 1)
        assert 'for_slide="s0-new" vo_anchor="id:s0-new#1"' in out
        # A prefix-sharing id and a fp: anchor are untouched.
        assert 'for_slide="s0-x" vo_anchor="id:s0-x#0"' in out
        assert 'vo_anchor="fp:abc#0"' in out

    def test_legacy_anchor_without_ordinal(self):
        text = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="s0" vo_anchor="id:s0"\n# x\n'
        )
        out, _, _, va = rename_in_half(text, "#", "s0", "s0-new")
        assert va == 1
        assert 'vo_anchor="id:s0-new"' in out

    def test_no_anchor_match_is_byte_stable(self):
        out, sid, fs, va = rename_in_half(self.VO, "#", "absent", "x")
        assert out == self.VO
        assert (sid, fs, va) == (0, 0, 0)


class TestReferencedIdsIn:
    def test_collects_for_slide_and_id_anchor_targets(self):
        text = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="a" vo_anchor="id:b#1"\n# x\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="!c" vo_anchor="fp:abc#0"\n# y\n'
        )
        assert referenced_ids_in(text, "#") == {"a", "b", "c"}


class TestMigrateLedgerKeyReferencesOnly:
    """Repoint onto an id the ledger already records (#990 review finding 1)."""

    def test_keeps_recorded_new_and_moves_owner_refs(self):
        deck = _deck_ledger()
        # Fake the "ledger knows NEW" state: a recorded entry under the target key.
        deck.members["id:s0-x"] = evolve(
            deck.members["id:s0"], entry=evolve(deck.members["id:s0"].entry, key="id:s0-x")
        )
        recorded_new = deck.members["id:s0-x"]
        old_entry = deck.members["id:s0"]
        assert ledger_knows(deck, "s0-x")

        changed = migrate_ledger_key(deck, "s0", "s0-x", references_only=True)

        assert changed is True
        assert deck.members["id:s0-x"] is recorded_new  # not clobbered by OLD's baseline
        assert deck.members["id:s0"] is old_entry  # left for the mechanical record_remove
        assert all(m.entry.owner != "id:s0" for m in deck.members.values())
        for handles in deck.member_order.values():
            assert "id:s0" not in handles
            assert handles.count("id:s0-x") <= 1
