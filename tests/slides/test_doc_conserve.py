"""Content conservation in ``sync apply`` (#1130 step 2; ``doc_conserve``).

The check compares cell contents before and after a pass, independent of
the differ's pairing, and the pre-write verify withholds a pass that would
lose agreed content, drop an unrecorded edit, or duplicate a cell.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from clm.slides import doc_apply
from clm.slides.doc_conserve import check_conservation, content_key, discarded_by_answers
from clm.slides.doc_identity import content_fingerprint
from clm.slides.doc_lenses import parse_bundle
from tests.slides.sync_probe import _apply, _diff, _record, deck_text, parse_labels

NONE: frozenset[str] = frozenset()


def _members(de_order: tuple[str, ...], en_order: tuple[str, ...], edits=NONE, en_edits=None):
    parse = parse_bundle(
        deck_text("de", de_order, edits),
        deck_text("en", en_order, edits if en_edits is None else en_edits),
        None,
        None,
        comment_token="#",
    )
    assert parse.deck is not None
    return doc_apply._members_with_group(parse.deck)


def _fps(members) -> set[str]:
    return {
        content_fingerprint(cell)
        for _group, member in members
        for cell in (member.de, member.en)
        if cell is not None
    }


BASE = ("S0", "a", "c1", "b", "c2", "S1")


class TestCheckConservation:
    def test_a_reorder_conserves(self):
        before = _members(BASE, BASE)
        after = _members(("S0", "b", "c2", "a", "c1", "S1"), ("S0", "b", "c2", "a", "c1", "S1"))
        assert check_conservation(before, after, _fps(before), set()) == []

    def test_agreed_content_must_survive(self):
        before = _members(BASE, BASE)
        after = _members(("S0", "a", "c1", "b", "c1", "S1"), BASE)
        found = check_conservation(before, after, _fps(before), set())
        messages = [v.message for v in found]
        assert any("both halves agree on from DE: 'c2 = 1'" in m for m in messages), messages
        assert any("duplicate content on DE (2 copies, at most 1): 'c1 = 1'" in m for m in messages)
        assert {g for v in found for g in v.slide_groups} == {"s-S0"}

    def test_an_unrecorded_edit_must_survive_on_its_half(self):
        recorded = _fps(_members(BASE, BASE))
        before = _members(BASE, BASE, edits=frozenset({"c1"}), en_edits=NONE)
        after = _members(BASE, BASE)  # DE's edit replaced by the recorded body
        found = check_conservation(before, after, recorded, set())
        assert [v.locus.split(":")[0] for v in found] == ["de"]
        assert "unrecorded edit" in found[0].message
        # Without a ledger nothing tells an edit from recorded content.
        assert check_conservation(before, after, None, set()) == []

    def test_an_explicit_discard_exempts_exactly_its_cell(self):
        recorded = _fps(_members(BASE, BASE))
        before = _members(BASE, BASE, edits=frozenset({"c1"}), en_edits=NONE)
        after = _members(BASE, BASE)
        cell = next(m.de for _g, m in before if m.de is not None and "c1 = 10" in m.de.body)
        assert check_conservation(before, after, recorded, {("de", content_key(cell))}) == []

    def test_a_side_choice_discards_the_other_side(self):
        class Row:
            key = "pos:s-S0/code/0"
            action = "conflict_shared"
            twin = None

        before = _members(BASE, BASE, edits=frozenset({"c1"}), en_edits=NONE)
        Row.member = next(m for _g, m in before if m.de is not None and "c1" in m.de.body)
        assert discarded_by_answers([(Row, "agent")], {Row.key: ["en"]}) == {
            ("de", content_key(Row.member.de))
        }
        # A placement answer discards nothing.
        Row.action = "pool_placement_divergence"
        assert discarded_by_answers([(Row, "agent")], {Row.key: ["en"]}) == set()

    def test_an_id_makes_identical_bytes_a_distinct_cell(self):
        stamped = deck_text("en", BASE, NONE).replace(
            "# %%\nc1 = 1", '# %% slide_id="code-c1"\nc1 = 1'
        )
        before = _members(BASE, BASE)
        parse = parse_bundle(deck_text("de", BASE, NONE), stamped, None, None, comment_token="#")
        assert parse.deck is not None
        keys = {
            content_key(m.en)
            for _g, m in doc_apply._members_with_group(parse.deck)
            if m.en is not None and "c1 = 1" in m.en.body
        }
        assert keys == {"id=code-c1\n# %%\nc1 = 1"}
        assert before  # parsed


def test_a_mis_paired_cross_group_move_is_withheld_not_written():
    """The probe's ``two_groups`` shape: EN swaps c1 and c2 across slide
    groups. The pass master wrote here put ``c1`` on DE twice and dropped
    ``c2`` there; a later pass happened to repair it. Now nothing is written
    and the verify names the loss."""
    base = ("S0", "a", "c1", "b", "S1", "e", "c2", "c3", "S2")
    moved = ("S0", "c2", "a", "b", "S1", "e", "c1", "c3", "S2")
    with tempfile.TemporaryDirectory() as tmp:
        de, en = Path(tmp) / "slides_t.de.py", Path(tmp) / "slides_t.en.py"
        de.write_text(deck_text("de", base, NONE), encoding="utf-8")
        en.write_text(deck_text("en", base, NONE), encoding="utf-8")
        _record(de, en)
        en.write_text(deck_text("en", moved, NONE), encoding="utf-8")
        before = de.read_text(encoding="utf-8")
        _, diff = _diff(de, en)
        decisions = {
            i.key: doc_apply.Decision(key=i.key, choice="en")
            for i in diff.items
            if i.action == "pool_placement_divergence"
        }
        outcome = _apply(de, en, decisions)
        payload = outcome.to_payload()
        assert de.read_text(encoding="utf-8") == before
        assert payload["verify_withheld"], payload
        assert any(
            "would remove content both halves agree on" in m for m in payload["verify_violations"]
        )
        labels, _ = parse_labels(de.read_text(encoding="utf-8"), "de")
        assert labels.count("body:c1 = 1") == 1
