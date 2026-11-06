"""Regression tests for #1137: one order authority per slide group, not per deck.

The filed failure: a placement question in group ``h`` deferred the mechanical
``mirror_order`` of the unrelated group ``g`` (#885 / #906 scoped the rule to
the whole deck). The missing mirror left the id'd order divergent, the
post-write verify failed deck-wide, the agent's ``body`` answers were written
but not recorded, and the next report framed them ``verify_cold``. 20
re-confirmations in the real deck.

Two rules now hold:

* a ``mirror_order`` defers only for an order question that can touch its own
  group. A row that can reach more than one group (``~groups``, an ``id:``
  cross-group move, ``mirror_pool_move``), or a group whose cells do not all
  sit inside its own bracket, keeps the deck-wide rule;
* when the only deck-wide finding is an ``order-parity`` divergence that the
  mirrors the pass deferred under that rule account for, answered bodies on
  id'd cells still record. Nothing else does (#992).
"""

from __future__ import annotations

from pathlib import Path

from clm.slides import doc_apply, doc_ledger
from clm.slides.doc_lenses import load_bundle
from clm.slides.sync_diff import DeckDiff, diff_outcome
from clm.slides.sync_verify import VerifyViolation, apply_verify_gate


def _vo(lang: str, slug: str) -> str:
    return (
        f'// %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-{slug}"\n'
        f"//\n// - {slug.upper()} {lang}\n"
    )


def _cell(lang: str, token: str) -> str:
    fixed = {
        "s": f'// %% [markdown] lang="{lang}" tags=["slide"] slide_id="s"\n//\n// ## S {lang}\n',
        "t": f'// %% [markdown] lang="{lang}" tags=["slide"] slide_id="t"\n//\n// ## T {lang}\n',
        "g": '// %% tags=["keep", "subslide"] slide_id="g"\nint g{0};\n',
        "h": '// %% tags=["keep", "subslide"] slide_id="h"\nint h{0};\n',
        "n": '// %% tags=["keep", "subslide"] slide_id="n"\nint n{0};\n',
        "xte": (
            f'// %% [markdown] lang="{lang}" tags=["voiceover", "alt"] slide_id="vo-x"\n'
            f"//\n// - X {lang} edited\n"
        ),
        "sa": '// %% tags=["keep"] slide_id="sa"\nint z{9};\n',
        "sb": '// %% tags=["keep"] slide_id="sb"\nint w{9};\n',
        "c0": '// %% tags=["keep"]\nint a{0};\n',
        "c1": '// %% tags=["keep"]\nint b{1};\n',
        "c1e": '// %% tags=["keep"]\nint b{7};\n',
        "c2": '// %% tags=["keep"]\nint c{2};\n',
        "d0": '// %% tags=["keep"]\nint x{0};\n',
        "d1": '// %% tags=["keep"]\nint y{1};\n',
        "d2": '// %% tags=["keep"]\nint v{2};\n',
    }
    return fixed.get(token) or _vo(lang, token)


def _text(lang: str, spec: str) -> str:
    header = (
        f"// -*- coding: utf-8 -*-\n// j2 from 'macros.j2' import header_{lang}\n"
        f'// {{{{ header_{lang}("Mini") }}}}\n'
    )
    return header + "\n" + "\n".join(_cell(lang, token) for token in spec.split())


class _Pair:
    """One recorded pair whose EN half then took an edit."""

    def __init__(self, tmp_path: Path, base: str, en: str, de: str | None = None) -> None:
        topic = tmp_path / "topic_999_mini"
        topic.mkdir()
        self.de_path = topic / "slides_mini.de.cpp"
        self.en_path = topic / "slides_mini.en.cpp"
        self.de_path.write_text(_text("de", base), encoding="utf-8", newline="\n")
        self.en_path.write_text(_text("en", base), encoding="utf-8", newline="\n")
        bundle = load_bundle(self.de_path, self.en_path)
        assert bundle.outcome.deck is not None
        ledger = doc_ledger.load(self.ledger_path)
        doc_ledger.record_deck_snapshot(
            ledger, self.deck_key, bundle.outcome.deck, provenance="record"
        )
        doc_ledger.save(ledger, self.ledger_path)
        self.en_path.write_text(_text("en", en), encoding="utf-8", newline="\n")
        if de is not None:
            self.de_path.write_text(_text("de", de), encoding="utf-8", newline="\n")

    @property
    def ledger_path(self) -> Path:
        return doc_ledger.ledger_path_for(self.de_path)

    @property
    def deck_key(self) -> str:
        return doc_ledger.deck_key_for(self.de_path)

    def diff(self) -> DeckDiff:
        bundle = load_bundle(self.de_path, self.en_path)
        ledger = doc_ledger.load(self.ledger_path)
        deck_ledger = ledger.decks.get(self.deck_key)
        base = doc_ledger.baseline_from_ledger(deck_ledger) if deck_ledger else None
        return diff_outcome(bundle.outcome, base)

    def rows(self) -> set[tuple[str, str]]:
        return {(i.key, i.action) for i in self.diff().items}

    def apply(self, rows: list[doc_apply.Decision], *, gate=None) -> doc_apply.ApplyOutcome:
        bundle = load_bundle(self.de_path, self.en_path)
        assert bundle.outcome.deck is not None
        ledger = doc_ledger.load(self.ledger_path)
        deck_ledger = ledger.decks.get(self.deck_key)
        base = doc_ledger.baseline_from_ledger(deck_ledger) if deck_ledger else None
        outcome = doc_apply.apply_deck(
            bundle,
            bundle.outcome.deck,
            diff_outcome(bundle.outcome, base),
            ledger,
            self.deck_key,
            decision_rows=rows,
            verify_gate=gate or apply_verify_gate(self.de_path, self.en_path),
        )
        assert outcome.error is None, outcome.to_payload()
        if outcome.ledger_changed:
            doc_ledger.save(ledger, self.ledger_path)
        return outcome

    def answers(self, *, placements: bool = True) -> list[doc_apply.Decision]:
        """What a careful agent answers: ``en`` to a question, a body for a
        translation, ``confirm`` for a verify row."""
        rows: list[doc_apply.Decision] = []
        for item in self.diff().items:
            if item.action in ("pool_placement_divergence", "order_decision"):
                if placements:
                    rows.append(doc_apply.Decision(key=item.key, choice="en"))
            elif item.action.startswith("translate"):
                slug = item.key.removeprefix("id:vo-")
                rows.append(doc_apply.Decision(key=item.key, body=f"//\n// - {slug.upper()} de"))
            elif item.action.startswith("verify"):
                rows.append(doc_apply.Decision(key=item.key, choice="confirm"))
        return rows

    def apply_passes(self, *, limit: int = 6) -> list[set[tuple[str, str]]]:
        """Drive report -> apply until clean; the rows framed by each pass."""
        passes: list[set[tuple[str, str]]] = []
        for _ in range(limit):
            if self.diff().is_clean:
                return passes
            passes.append(self.rows())
            self.apply(self.answers())
        raise AssertionError(f"no convergence after {limit} passes: {passes}")


def _result(outcome: doc_apply.ApplyOutcome, key: str, action: str) -> doc_apply.ItemResult:
    return next(r for r in outcome.results if r.key == key and r.action == action)


#: The filed shape E: EN moves ``vo-u`` inside g and adds two voiceovers, and
#: moves ``vo-w`` across a positional cell in the unrelated subslide h.
SHAPE_E = ("s g c0 c1 c2 sa u h d0 w d1", "s g c0 u c1 c2 p sa q h d0 d1 w")


class TestDeferralIsPerGroup:
    def test_shape_e_converges_in_one_pass_with_no_reconfirmation(self, tmp_path: Path):
        pair = _Pair(tmp_path, *SHAPE_E)
        first = pair.rows()
        assert ("pos:g/order.deck/0", "mirror_order") in first
        assert ("pos:h/code/1", "pool_placement_divergence") in first

        outcome = pair.apply(pair.answers())

        assert _result(outcome, "pos:g/order.deck/0", "mirror_order").status == "applied"
        for key in ("id:vo-p", "id:vo-q"):
            result = _result(outcome, key, "translate_new")
            assert result.status == "applied"
            assert "recording deferred" not in result.reason
        assert outcome.verify_violations == []
        assert pair.diff().is_clean, pair.rows()

    def test_shape_e_writes_the_source_layout(self, tmp_path: Path):
        pair = _Pair(tmp_path, *SHAPE_E)
        assert len(pair.apply_passes()) == 1
        de = pair.de_path.read_text(encoding="utf-8")
        assert de == _text("de", SHAPE_E[1])

    def test_an_unanswered_question_elsewhere_holds_only_itself(self, tmp_path: Path):
        pair = _Pair(tmp_path, *SHAPE_E)
        outcome = pair.apply(pair.answers(placements=False))
        assert _result(outcome, "pos:g/order.deck/0", "mirror_order").status == "applied"
        assert _result(outcome, "pos:h/code/1", "pool_placement_divergence").status == "pending"
        # Only h's question is left.
        assert {action for _key, action in pair.rows()} == {"pool_placement_divergence"}
        assert not any(action == "verify_cold" for _key, action in pair.rows())

    def test_the_question_can_be_in_the_earlier_group(self, tmp_path: Path):
        # The mirror in h, the placement in g: execution order must not matter.
        pair = _Pair(
            tmp_path,
            "s g c0 u c1 h d0 d1 sa w",
            "s g c0 c1 u h d0 w d1 sa",
        )
        rows = pair.rows()
        assert ("pos:h/order.deck/0", "mirror_order") in rows, rows
        assert any(a == "pool_placement_divergence" and k.startswith("pos:g/") for k, a in rows)
        assert len(pair.apply_passes()) == 1
        assert pair.de_path.read_text(encoding="utf-8") == _text("de", "s g c0 c1 u h d0 w d1 sa")


class TestWhatKeepsTheDeckWideRule:
    def test_a_question_in_the_mirror_own_group_defers_it(self, tmp_path: Path):
        # g holds both the id'd reorder and a placement of its own.
        pair = _Pair(tmp_path, "s g c0 u c1 sa w c2 sb", "s g c0 c1 u sa c2 sb w")
        rows = pair.rows()
        placements = [k for k, a in rows if a == "pool_placement_divergence"]
        assert placements and all(k.startswith("pos:g/") for k in placements), rows
        assert ("pos:g/order.deck/0", "mirror_order") in rows, rows
        outcome = pair.apply([])
        row = _result(outcome, "pos:g/order.deck/0", "mirror_order")
        assert row.status == "deferred"
        assert placements[0] in row.reason

    def _assert_all_deferred(self, pair: _Pair, *keys: str) -> None:
        rows = pair.rows()
        assert ("pos:g/order.deck/0", "mirror_order") in rows, rows
        assert ("pos:h/code/1", "pool_placement_divergence") in rows, rows
        outcome = pair.apply([])
        for key in ("pos:g/order.deck/0", *keys):
            row = _result(outcome, key, "mirror_order")
            assert row.status == "deferred", row
            assert "pos:h/code/1" in row.reason, row

    def test_a_cross_group_member_move_keeps_the_rule_deck_wide(self, tmp_path: Path):
        # vo-q moves from s to g: an `id:` row reaches two groups.
        pair = _Pair(tmp_path, "s q g u p sa h d0 w d1", "s g p u sa q h d0 d1 w")
        assert ("id:vo-q", "mirror_order") in pair.rows(), pair.rows()
        self._assert_all_deferred(pair, "id:vo-q")

    def test_a_group_order_row_keeps_the_rule_deck_wide(self, tmp_path: Path):
        # s and t swap: a `~groups` row moves whole groups.
        pair = _Pair(tmp_path, "s c0 t g u p h d0 w d1", "t s c0 g p u h d0 d1 w")
        self._assert_all_deferred(pair, "pos:~groups/order.deck/0")

    def test_a_pool_move_keeps_the_rule_deck_wide(self, tmp_path: Path):
        # c0 moves from s to t: `mirror_pool_move` (#1142) reaches two groups.
        pair = _Pair(tmp_path, "s c0 t c1 g u p h d0 w d1", "s t c1 c0 g p u h d0 d1 w")
        assert any(a == "mirror_pool_move" for _k, a in pair.rows()), pair.rows()
        self._assert_all_deferred(pair)

    def test_a_cell_outside_its_bracket_keeps_the_rule_deck_wide(self, tmp_path: Path):
        # EN adds a slide t inside g, so g's `sa` sits under t on EN only. g's
        # handle says g, yet its permutation reaches into t's bracket.
        pair = _Pair(tmp_path, "s g u p sa h d0 w d1", "s g p u t sa h d0 d1 w")
        assert not any(k.startswith("id:") and a == "mirror_order" for k, a in pair.rows())
        self._assert_all_deferred(pair)


class TestAQuestionLeftOpenIsNotAStall:
    def test_no_stall_verdict_while_an_order_question_is_pending(self, tmp_path: Path):
        # g's mirror lands, h's order question stays unanswered, so the deck-wide
        # finding withholds g's recording. Pass 2 writes nothing, but the next
        # step is answering h, not reconciling g by hand.
        pair = _Pair(tmp_path, "s g u p h v y z", "s g p u h y v z", de="s g u p h v z y")
        assert ("pos:h/order.deck/0", "order_decision") in pair.rows(), pair.rows()
        first = pair.apply([])
        assert _result(first, "pos:g/order.deck/0", "mirror_order").status == "applied"
        second = pair.apply([])
        assert second.no_progress is None
        assert second.verify_withheld == ["pos:g/order.deck/0"]


class TestBodiesRecordBesideADeferredMirror:
    """The order row is still deferred (same group), and the only finding is
    the divergence it leaves: the agent's bodies are not re-asked."""

    BASE = "s g u p sa w c0 c1"
    EN = "s g p u sa c0 w c1 r"

    def test_answered_bodies_record(self, tmp_path: Path):
        pair = _Pair(tmp_path, self.BASE, self.EN)
        rows = pair.rows()
        assert ("id:vo-r", "translate_new") in rows, rows
        outcome = pair.apply(pair.answers())
        assert _result(outcome, "pos:g/order.deck/0", "mirror_order").status == "deferred"
        assert {v.kind for v in outcome.verify_violations} == {"order-parity"}
        body = _result(outcome, "id:vo-r", "translate_new")
        assert "recording deferred" not in body.reason, body
        assert "id:vo-r" not in outcome.verify_withheld
        assert not any(action == "verify_cold" for _key, action in pair.rows()), pair.rows()

    def test_the_whole_shape_still_converges(self, tmp_path: Path):
        pair = _Pair(tmp_path, self.BASE, self.EN)
        passes = pair.apply_passes()
        assert not any(a == "verify_cold" for rows in passes for _k, a in rows), passes
        assert pair.de_path.read_text(encoding="utf-8") == _text("de", self.EN)

    def test_order_trust_is_not_recorded(self, tmp_path: Path):
        pair = _Pair(tmp_path, self.BASE, self.EN)
        before = doc_ledger.load(pair.ledger_path).decks[pair.deck_key].member_order
        pair.apply(pair.answers())
        after = doc_ledger.load(pair.ledger_path).decks[pair.deck_key].member_order
        # The recorded order is the deferred mirror's to change.
        assert after == before

    def test_a_mechanical_row_stays_out_of_the_ledger(self, tmp_path: Path):
        # EN also edits a shared cell: the propagation is written, but it is
        # not an answered body, so the deck-wide finding still withholds it.
        pair = _Pair(tmp_path, self.BASE, self.EN.replace("c1", "c1e"))
        mechanical = [k for k, a in pair.rows() if a == "propagate_shared_edit"]
        assert mechanical, pair.rows()
        outcome = pair.apply(pair.answers())
        assert {v.kind for v in outcome.verify_violations} == {"order-parity"}
        assert mechanical[0] in outcome.verify_withheld
        row = _result(outcome, mechanical[0], "propagate_shared_edit")
        assert "recording deferred" in row.reason

    def test_a_confirm_answer_stays_out_of_the_ledger(self, tmp_path: Path):
        # Only a body answer is exempt; a `confirm` beside it waits, as before.
        pair = _Pair(tmp_path, f"{self.BASE} x", f"{self.EN} x")
        ledger = doc_ledger.load(pair.ledger_path)
        ledger.decks[pair.deck_key].members.pop("id:vo-x")
        doc_ledger.save(ledger, pair.ledger_path)
        rows = pair.rows()
        assert ("id:vo-x", "verify_cold") in rows, rows
        outcome = pair.apply(pair.answers())
        assert {v.kind for v in outcome.verify_violations} == {"order-parity"}
        row = _result(outcome, "id:vo-x", "verify_cold")
        assert row.status == "applied" and "recording deferred" in row.reason, row
        assert "recording deferred" not in _result(outcome, "id:vo-r", "translate_new").reason

    def test_a_mechanical_row_beside_the_body_stays_out(self, tmp_path: Path):
        # vo-x takes a tag and an edit. Recording it banks its whole snapshot,
        # tags included, so the body may not carry `mirror_tags` past the gate.
        pair = _Pair(tmp_path, f"{self.BASE} x", self.EN.replace(" r", " xte"))
        rows = pair.rows()
        assert {("id:vo-x", "mirror_tags"), ("id:vo-x", "translate_edit")} <= rows, rows
        outcome = pair.apply(pair.answers())
        assert {v.kind for v in outcome.verify_violations} == {"order-parity"}
        for action in ("mirror_tags", "translate_edit"):
            assert "recording deferred" in _result(outcome, "id:vo-x", action).reason
        assert "id:vo-x" in outcome.verify_withheld
        # Unrecorded, as reported: the member re-frames.
        assert ("id:vo-x", "verify_translation") in pair.rows()

    def test_a_body_whose_owner_stays_unrecorded_waits(self, tmp_path: Path):
        # The new subslide n is a mechanical row, so it stays withheld. Its
        # voiceover must wait too: the save would prune the owner reference, and
        # nothing would re-frame it (#718).
        pair = _Pair(tmp_path, f"{self.BASE} h d0", "s g p u sa c0 w c1 h d0 n r")
        assert ("id:n", "copy_new_shared") in pair.rows(), pair.rows()
        outcome = pair.apply(pair.answers())
        assert "recording deferred" in _result(outcome, "id:vo-r", "translate_new").reason
        pair.apply_passes()
        recorded = doc_ledger.load(pair.ledger_path).decks[pair.deck_key].members["id:vo-r"]
        assert recorded.entry.owner == "id:n"

    def test_a_group_order_row_explains_nothing(self, tmp_path: Path):
        # s's scope row is deferred too, but leaving out s's cells would hide
        # the swap of s and t.
        pair = _Pair(tmp_path, "s u p t x y h d0 w d1", "t x y s p u r h d0 d1 w")
        rows = pair.rows()
        assert ("pos:~groups/order.deck/0", "mirror_order") in rows, rows
        assert ("id:vo-r", "translate_new") in rows, rows
        outcome = pair.apply(pair.answers())
        assert _result(outcome, "pos:~groups/order.deck/0", "mirror_order").status == "deferred"
        assert "recording deferred" in _result(outcome, "id:vo-r", "translate_new").reason

    def _divergent_gate(self, pair: _Pair, **fields):
        """The real gate, plus the given extra finding once the pass has been
        written (a pre-write finding would hold the pass back instead)."""
        real = apply_verify_gate(pair.de_path, pair.en_path)

        def gate(texts=None):
            found = real(texts)
            if texts is None:
                found = [*found, VerifyViolation(severity="error", **fields)]
            return found

        return gate

    def test_any_other_deck_wide_finding_withholds_the_bodies(self, tmp_path: Path):
        pair = _Pair(tmp_path, self.BASE, self.EN)
        outcome = pair.apply(
            pair.answers(), gate=self._divergent_gate(pair, kind="unify", message="injected")
        )
        assert "recording deferred" in _result(outcome, "id:vo-r", "translate_new").reason
        assert "id:vo-r" in outcome.verify_withheld

    def test_a_finding_on_the_body_slide_withholds_it(self, tmp_path: Path):
        pair = _Pair(tmp_path, self.BASE, self.EN)
        outcome = pair.apply(
            pair.answers(),
            gate=self._divergent_gate(pair, kind="tag-parity", message="injected", slide_id="vo-r"),
        )
        assert "recording deferred" in _result(outcome, "id:vo-r", "translate_new").reason

    def test_an_order_divergence_the_mirror_does_not_explain_withholds(self, tmp_path: Path):
        # Two divergences, one of them outside the deferred scope (here: the
        # anchors of two later slides swapped by hand, carried at base).
        pair = _Pair(tmp_path, self.BASE, self.EN)
        real = apply_verify_gate(pair.de_path, pair.en_path)

        def gate(texts=None):
            found = list(real(texts))
            if texts is None:
                found = [
                    VerifyViolation(
                        severity="error",
                        kind="order-parity",
                        message="injected",
                        order_ids=(("x", "y", "vo-u"), ("y", "x", "vo-u")),
                    )
                    if v.kind == "order-parity"
                    else v
                    for v in found
                ]
            return found

        outcome = pair.apply(pair.answers(), gate=gate)
        assert "recording deferred" in _result(outcome, "id:vo-r", "translate_new").reason

    def test_a_finding_with_no_order_evidence_withholds(self, tmp_path: Path):
        # A gate that does not say which ids diverge explains nothing.
        pair = _Pair(tmp_path, self.BASE, self.EN)
        real = apply_verify_gate(pair.de_path, pair.en_path)

        def gate(texts=None):
            found = list(real(texts))
            if texts is None:
                found = [
                    VerifyViolation(severity="error", kind="order-parity", message="injected")
                    if v.kind == "order-parity"
                    else v
                    for v in found
                ]
            return found

        outcome = pair.apply(pair.answers(), gate=gate)
        assert "recording deferred" in _result(outcome, "id:vo-r", "translate_new").reason
