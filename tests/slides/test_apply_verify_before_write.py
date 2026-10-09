"""Regression tests for #1051: ``sync apply`` settles a pass before it writes.

The filed failure: ``apply`` executed a mechanical removal beside a framed
row it then rejected, wrote a DE half that had lost a cell, and only the
post-write structural verify noticed — the bad bytes stayed on disk
(``cannot align DE/EN cells``), unrecorded. Two rules now hold:

* **C** — a mechanical row whose handle also carries a framed row the same
  pass leaves unresolved (unanswered, rejected) is deferred, and its reason
  names that handle.
* **B2** — the structural verify runs over the projected finals BEFORE the
  write. A violation the pass introduces on a slide withholds that slide
  group's *mutations*; one naming no slide (or an unplaceable id) withholds
  everything, so nothing is written. The pass is recomputed until clean.

The contract: ``apply`` never writes a structural violation the pair did not
already carry. :class:`TestDiskNeverGainsAViolation` pins it over a seeded
sweep of random edits and random answers (master introduced a violation in
~5% of those passes).
"""

from __future__ import annotations

import random
import re
from collections import Counter
from pathlib import Path

import pytest

from clm.slides import doc_apply, doc_ledger
from clm.slides.doc_lenses import load_bundle
from clm.slides.doc_write import DeckEmitter
from clm.slides.sync_diff import FRAMED_ACTIONS, DeckDiff, diff_outcome
from clm.slides.sync_verify import apply_verify_gate, gate_projected_pair


class _Pair:
    """One recorded split pair on disk — the report → apply loop harness."""

    def __init__(self, tmp_path: Path, de: str, en: str, *, ext: str = "py") -> None:
        topic = tmp_path / "topic_101_dup"
        topic.mkdir(exist_ok=True)
        self.de_path = topic / f"slides_dup.de.{ext}"
        self.en_path = topic / f"slides_dup.en.{ext}"
        self.de_path.write_text(de, encoding="utf-8")
        self.en_path.write_text(en, encoding="utf-8")
        bundle = load_bundle(self.de_path, self.en_path)
        assert bundle.outcome.deck is not None
        ledger = doc_ledger.load(self.ledger_path)
        doc_ledger.record_deck_snapshot(
            ledger, self.deck_key, bundle.outcome.deck, provenance="record"
        )
        doc_ledger.save(ledger, self.ledger_path)

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

    def gate(self):
        return apply_verify_gate(self.de_path, self.en_path)

    def apply(
        self, rows: list[doc_apply.Decision] | None = None, *, gate=None, dry_run: bool = False
    ) -> doc_apply.ApplyOutcome:
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
            decision_rows=rows or [],
            dry_run=dry_run,
            verify_gate=gate if gate is not None else self.gate(),
        )
        if outcome.error is None and not dry_run and outcome.ledger_changed:
            doc_ledger.save(ledger, self.ledger_path)
        return outcome

    def snapshot(self) -> tuple[bytes, bytes, bytes]:
        return (
            self.de_path.read_bytes(),
            self.en_path.read_bytes(),
            self.ledger_path.read_bytes(),
        )

    def violations(self) -> list:
        return gate_projected_pair(self.de_path, self.en_path)


def _result(outcome: doc_apply.ApplyOutcome, key: str, action: str) -> doc_apply.ItemResult:
    return next(r for r in outcome.results if r.key == key and r.action == action)


# ---------------------------------------------------------------------------
# The filed pair, in C++ (the issue's minimal `topic_101_dup` shape)
# ---------------------------------------------------------------------------


def _md(lang: str, tags: str, slide_id: str, text: str) -> str:
    return (
        f'// %% [markdown] lang="{lang}" tags=["{tags}"] slide_id="{slide_id}"\n//\n// - {text}\n\n'
    )


def _code(body: str) -> str:
    return f"// %%\n{body}\n\n"


def _cpp_half(lang: str, *, cells: str = "base", next_text: str = "") -> str:
    intro = _md(lang, "slide", "intro", "Text intro")
    vo = _md(lang, "voiceover", "vo-assign", "Text narration for the assignment")
    nxt = _md(lang, "slide", "next", f"Text next slide{next_text}")
    decl, show, assign = _code("int x = 0;"), _code("SHOW(x);"), _code("SHOW(x = 10);")
    layouts = {
        "base": [intro, decl, show, vo, assign, show, nxt],
        # The second `SHOW(x);` moved past the next slide's heading.
        "moved_out": [intro, decl, show, vo, assign, nxt, show],
        # A duplicate `SHOW(x);` inserted, and the assignment edited.
        "dup_edit": [intro, decl, show, show, vo, _code("SHOW(x = 11);"), show, nxt],
        # moved_out, plus a new one-sided cell: the pair already fails `unify`.
        "moved_out_plus_new": [intro, decl, show, vo, assign, nxt, show, _code("SHOW(x * 2);")],
    }
    return "".join(layouts[cells]).rstrip("\n") + "\n"


def _cpp_pair(tmp_path: Path) -> _Pair:
    return _Pair(tmp_path, _cpp_half("de"), _cpp_half("en"), ext="cpp")


class TestFiledShapeNeverDropsACell:
    """The #1051 repro, adapted to what still reaches apply after #1070.

    EN moves the duplicate ``SHOW(x);`` across the next slide's heading. The
    report frames the gone cell ``remove_vs_split`` (its bytes sit
    un-ledgered under ``next``) and the moved cell a one-sided
    ``verify_cold`` that cannot be confirmed — ``remove`` is the only answer
    either row allows. Before the fix the removal landed: DE was written
    without the cell and failed ``cannot align DE/EN cells — DE end``, the
    issue's exact symptom.
    """

    def _moved(self, tmp_path: Path, *, next_edit: bool) -> _Pair:
        pair = _cpp_pair(tmp_path)
        pair.en_path.write_text(
            _cpp_half("en", cells="moved_out", next_text=" NEW" if next_edit else ""),
            encoding="utf-8",
        )
        assert pair.violations() == []  # the hand edit itself passes the verify
        return pair

    def test_the_only_allowed_answer_writes_nothing(self, tmp_path: Path):
        pair = self._moved(tmp_path, next_edit=False)
        assert pair.rows() == {
            ("pos:intro/code/3", "remove_vs_split"),
            ("pos:next/code/0", "verify_cold"),
        }
        before = pair.snapshot()
        outcome = pair.apply([doc_apply.Decision(key="pos:intro/code/3", choice="remove")])

        assert outcome.error is None
        assert outcome.wrote is False
        assert pair.snapshot() == before  # DE keeps its cell; the ledger is untouched
        removal = _result(outcome, "pos:intro/code/3", "remove_vs_split")
        assert removal.status == "deferred"
        assert "fail deck-wide" in removal.reason and "cannot align" in removal.reason
        assert outcome.verify_withheld == ["pos:intro/code/3"]
        assert [v.kind for v in outcome.verify_violations] == ["unify"]
        assert outcome.ledger_changed is False
        # Withheld work re-frames: the next report asks the same questions.
        assert pair.rows() == {
            ("pos:intro/code/3", "remove_vs_split"),
            ("pos:next/code/0", "verify_cold"),
        }

    def test_a_deck_wide_fault_withholds_the_unrelated_answer_too(self, tmp_path: Path):
        # Rule B2's deck-wide arm: a `unify` failure names no slide, so the
        # engine cannot tell which change caused it — nothing is written,
        # including a well-formed body on another slide.
        pair = self._moved(tmp_path, next_edit=True)
        assert ("id:next", "translate_edit") in pair.rows()
        before = pair.snapshot()
        outcome = pair.apply(
            [
                doc_apply.Decision(key="pos:intro/code/3", choice="remove"),
                doc_apply.Decision(key="id:next", body="//\n// - Text next slide NEU"),
            ]
        )
        assert outcome.wrote is False
        assert pair.snapshot() == before
        assert sorted(outcome.verify_withheld) == ["id:next", "pos:intro/code/3"]
        for key, action in (("id:next", "translate_edit"), ("pos:intro/code/3", "remove_vs_split")):
            result = _result(outcome, key, action)
            assert result.status == "deferred" and "fail deck-wide" in result.reason
        assert {r["key"] for r in outcome.to_payload()["left_undone"]} == {
            "id:next",
            "pos:intro/code/3",
        }

    def test_a_pre_existing_unify_fault_does_not_mask_a_new_one(self, tmp_path: Path):
        # Review finding: the pair already fails `unify` (EN's new one-sided
        # cell at the end), and the removal would add a second misalignment
        # in front of it. Keyed on line-number-blanked messages the two read
        # alike ("DE end; EN line #: '// %%'") and the write went through;
        # keyed on the offending cells' bytes (the violation's locus) the new
        # fault is new.
        pair = _cpp_pair(tmp_path)
        pair.en_path.write_text(_cpp_half("en", cells="moved_out_plus_new"), encoding="utf-8")
        assert [v.kind for v in pair.violations()] == ["unify"]
        before = pair.snapshot()
        outcome = pair.apply([doc_apply.Decision(key="pos:intro/code/3", choice="remove")])
        assert outcome.wrote is False
        assert pair.snapshot() == before
        removal = _result(outcome, "pos:intro/code/3", "remove_vs_split")
        assert removal.status == "deferred" and "fail deck-wide" in removal.reason

    def test_the_callers_parse_is_never_mutated(self, tmp_path: Path):
        # Review finding: round 1 used to execute on the caller's deck, so
        # after a withholding re-round it held mutations that were never
        # written. Every round now runs on its own copy.
        pair = self._moved(tmp_path, next_edit=False)
        bundle = load_bundle(pair.de_path, pair.en_path)
        assert bundle.outcome.deck is not None
        emitted = DeckEmitter(deck=bundle.outcome.deck).emit_all()
        ledger = doc_ledger.load(pair.ledger_path)
        base = doc_ledger.baseline_from_ledger(ledger.decks[pair.deck_key])
        doc_apply.apply_deck(
            bundle,
            bundle.outcome.deck,
            diff_outcome(bundle.outcome, base),
            ledger,
            pair.deck_key,
            decision_rows=[doc_apply.Decision(key="pos:intro/code/3", choice="remove")],
            verify_gate=pair.gate(),
        )
        assert DeckEmitter(deck=bundle.outcome.deck).emit_all() == emitted

    @pytest.mark.parametrize("shape", ["zero-argument", "texts-required"])
    def test_a_gate_of_the_wrong_shape_is_refused_before_anything_runs(
        self, tmp_path: Path, shape: str
    ):
        # Review findings: the pre-#1051 gate took no argument (it would crash
        # on the pre-write call), and one that REQUIRES the texts would crash
        # on the post-write call — after the files were written. Both are
        # refused up front.
        pair = self._moved(tmp_path, next_edit=False)
        before = pair.snapshot()
        gate = (
            (lambda: gate_projected_pair(pair.de_path, pair.en_path))
            if shape == "zero-argument"
            else (lambda texts: pair.gate()(texts))
        )
        with pytest.raises(TypeError, match="file texts"):
            pair.apply([doc_apply.Decision(key="pos:intro/code/3", choice="remove")], gate=gate)
        assert pair.snapshot() == before

    def test_the_dry_run_reports_the_same_withholding(self, tmp_path: Path):
        # The pre-write verify runs in memory, so `--dry-run` now shows what
        # a real run would hold back (it used to skip the gate entirely).
        pair = self._moved(tmp_path, next_edit=False)
        outcome = pair.apply(
            [doc_apply.Decision(key="pos:intro/code/3", choice="remove")], dry_run=True
        )
        assert _result(outcome, "pos:intro/code/3", "remove_vs_split").status == "deferred"
        assert [v.kind for v in outcome.verify_violations] == ["unify"]


def _s1_deck(lang: str, *, drop_s1: bool = False, edit_s0: bool = False) -> str:
    """s0 / s1 (two shared code cells) / s2 — the second review round's shape."""

    def slide(slug: str) -> str:
        return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {slug}\n\n'

    def text(slug: str, body: str) -> str:
        return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {body}\n\n'

    zero = f"{lang} zero" + (" NEW" if edit_s0 else "")
    out = [HEADER_DE if lang == "de" else HEADER_EN, slide("s0"), text("s0-m", zero)]
    if not drop_s1:
        y = "y = 2 + 1" if lang == "en" else "y = 2"
        out += [slide("s1"), "# %%\nx = 1\n\n", f"# %%\n{y}\n\n", text("s1-m", f"{lang} one")]
    out += [slide("s2"), text("s2-m", f"{lang} two")]
    return "".join(out).rstrip("\n") + "\n"


class TestRelocatedUnifyFaultIsScopedToItsSlide:
    """Second review round: DE deleted slide s1 by hand while EN edited one of
    its shared cells, so the pair already fails ``unify``. The mechanical
    ``mirror_remove`` of s1's other cell (the framed removal questions are
    unanswered) moves that fault to another cell — a new locus. Read as a new
    deck-wide fault it withheld everything, the unrelated s0 body included;
    the finding now names the slides of its cells, and withholding s1's
    changes restores the fault the pair already had."""

    def test_the_unrelated_answer_is_written(self, tmp_path: Path):
        pair = _Pair(tmp_path, _s1_deck("de"), _s1_deck("en", edit_s0=False))
        pair.de_path.write_text(_s1_deck("de", drop_s1=True), encoding="utf-8")
        en = _s1_deck("en", edit_s0=True)
        pair.en_path.write_text(en, encoding="utf-8")
        before = _key_counts(pair.violations())
        assert ("pos:s1/code/0", "mirror_remove") in pair.rows()
        outcome = pair.apply([doc_apply.Decision(key="id:s0-m", body="# de zero NEU")])

        body = _result(outcome, "id:s0-m", "translate_edit")
        assert body.status == "applied", outcome.to_payload()
        assert "# de zero NEU" in pair.de_path.read_text(encoding="utf-8")
        mirror = _result(outcome, "pos:s1/code/0", "mirror_remove")
        assert mirror.status == "deferred" and "on this member's slide" in mirror.reason
        assert not _key_counts(pair.violations()) - before  # nothing new on disk


class TestDependentMechanicalRowsDefer:
    """Rule C: a mechanical row on a handle whose framed row stays unresolved.

    EN duplicates ``SHOW(x);`` and edits the assignment. The pool frames a
    mechanical ``propagate_shared_edit`` and a one-sided ``verify_cold`` on
    the same handle — half of one question. Before the fix the propagate
    executed alone; now it defers, names the framed handle, and re-derives
    next pass, while an unrelated answer on another slide lands and records.
    """

    @pytest.mark.parametrize("answer", [None, "confirm"])
    def test_the_dependent_row_defers_and_the_rest_lands(self, tmp_path: Path, answer):
        pair = _cpp_pair(tmp_path)
        pair.en_path.write_text(
            _cpp_half("en", cells="dup_edit", next_text=" NEW"), encoding="utf-8"
        )
        assert pair.rows() == {
            ("pos:intro/code/2", "propagate_shared_edit"),
            ("pos:intro/code/2", "verify_cold"),
            ("id:next", "translate_edit"),
        }
        rows = [doc_apply.Decision(key="id:next", body="//\n// - Text next slide NEU")]
        if answer is not None:  # the issue's shape: the cold row's answer is rejected
            rows.append(doc_apply.Decision(key="pos:intro/code/2", choice=answer))
        outcome = pair.apply(rows)

        propagate = _result(outcome, "pos:intro/code/2", "propagate_shared_edit")
        assert propagate.status == "deferred", outcome.to_payload()
        assert "verify_cold row pos:intro/code/2" in propagate.reason
        cold = _result(outcome, "pos:intro/code/2", "verify_cold")
        assert cold.status == ("pending" if answer is None else "rejected")
        assert f"which is {cold.status} this pass" in propagate.reason
        assert any(r["key"] == "pos:intro/code/2" for r in outcome.to_payload()["left_undone"])
        # The DE assignment is untouched; the unrelated body was written. (It
        # is not recorded: the hand-inserted duplicate leaves the pair failing
        # `unify` deck-wide — a fault it already had, so #992's post-write
        # gate keeps the whole pass out of the ledger, as before.)
        de = pair.de_path.read_text(encoding="utf-8")
        assert "SHOW(x = 10);" in de and "SHOW(x = 11);" not in de
        assert "Text next slide NEU" in de
        assert _result(outcome, "id:next", "translate_edit").status == "applied"
        # The deferred row re-derives beside its framed sibling.
        assert {
            ("pos:intro/code/2", "propagate_shared_edit"),
            ("pos:intro/code/2", "verify_cold"),
        } <= pair.rows()


# ---------------------------------------------------------------------------
# B2 per slide group — a fault injected through a test-only gate
# ---------------------------------------------------------------------------

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title\") }}\n\n"


def _three_slides(lang: str) -> str:
    def slide(slug: str, title: str) -> str:
        return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'

    def text(slug: str, body: str) -> str:
        return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {body}\n\n'

    word = {"de": ("Eins", "Zwei", "Drei"), "en": ("One", "Two", "Three")}[lang]
    return (
        (HEADER_DE if lang == "de" else HEADER_EN)
        + slide("s0", word[0])
        + text("s0-m", f"{lang.upper()} {word[0].lower()}")
        + slide("s1", word[1])
        + '# %% tags=["keep"]\nx = 1\n\n'
        + text("s1-m", f"{lang.upper()} {word[1].lower()}")
        + slide("s2", word[2])
        + text("s2-m", f"{lang.upper()} {word[2].lower()}")
    ).rstrip("\n") + "\n"


class _Fault:
    """A stand-in for a verify violation the injected gate adds."""

    def __init__(self, kind: str, message: str, slide_id: str | None) -> None:
        self.kind = kind
        self.message = message
        self.slide_id = slide_id
        self.severity = "error"


def _faulty_gate(pair: _Pair, fault: _Fault, *, trigger: str = "DE zwei NEU"):
    """The real gate, plus ``fault`` whenever the pair it judges holds ``trigger``.

    With the default trigger the fault is caused by this pass's own change
    (the s1 body answer), so the pre-apply pair does not carry it — what an
    engine fault confined to one slide looks like to the gate.
    """
    real = pair.gate()

    def gate(texts=None):
        found = list(real(texts))
        if texts is None:
            halves = [p.read_text(encoding="utf-8") for p in (pair.de_path, pair.en_path)]
        else:
            halves = [texts[("de", "deck")] or "", texts[("en", "deck")] or ""]
        if any(trigger in half for half in halves):
            found.append(fault)
        return found

    return gate


class TestPreWriteVerifyIsScopedPerGroup:
    """Rule B2: a fault the pass introduces on one slide withholds that slide's
    mutations — not just its recording, as #992 did after writing them —
    while every other group is written and recorded."""

    def _pair(self, tmp_path: Path) -> _Pair:
        pair = _Pair(tmp_path, _three_slides("de"), _three_slides("en"))
        en = pair.en_path.read_text(encoding="utf-8")
        en = en.replace("EN one", "EN one NEW").replace("EN two", "EN two NEW")
        pair.en_path.write_text(en, encoding="utf-8")
        assert pair.rows() == {("id:s0-m", "translate_edit"), ("id:s1-m", "translate_edit")}
        return pair

    BODIES = [
        doc_apply.Decision(key="id:s0-m", body="# DE eins NEU"),
        doc_apply.Decision(key="id:s1-m", body="# DE zwei NEU"),
    ]

    def test_only_the_faulted_groups_mutations_are_withheld(self, tmp_path: Path):
        pair = self._pair(tmp_path)
        members_before = doc_ledger.load(pair.ledger_path).decks[pair.deck_key].members
        fault = _Fault("id-asymmetry", "injected engine fault on s1", "s1-m")
        outcome = pair.apply(self.BODIES, gate=_faulty_gate(pair, fault))

        faulted = _result(outcome, "id:s1-m", "translate_edit")
        assert faulted.status == "deferred"
        assert "fail on this member's slide" in faulted.reason
        assert "injected engine fault on s1" in faulted.reason
        assert outcome.verify_withheld == ["id:s1-m"]
        # The faulted group's bytes were never written …
        de = pair.de_path.read_text(encoding="utf-8")
        assert "DE zwei NEU" not in de and "DE zwei" in de
        # … while the other group's answer was written AND recorded.
        body = _result(outcome, "id:s0-m", "translate_edit")
        assert body.status == "applied" and "deferred" not in body.reason
        assert "# DE eins NEU" in de
        assert outcome.ledger_changed is True
        assert pair.violations() == []  # the files on disk pass the verify
        members = doc_ledger.load(pair.ledger_path).decks[pair.deck_key].members
        assert members["id:s0-m"].entry != members_before["id:s0-m"].entry
        assert members["id:s1-m"].entry == members_before["id:s1-m"].entry
        # The withheld row re-frames next pass; the banked one does not.
        assert pair.rows() == {("id:s1-m", "translate_edit")}

    def test_a_fault_on_an_untouched_slide_withholds_everything(self, tmp_path: Path):
        # The pass's change on s1 faults slide s2, which has no rows:
        # withholding s2 cannot clear it, so the cause is elsewhere and
        # nothing is written (the #992 fail-safe, now on the write).
        pair = self._pair(tmp_path)
        before = pair.snapshot()
        fault = _Fault("id-asymmetry", "injected fault on s2", "s2-m")
        outcome = pair.apply(self.BODIES, gate=_faulty_gate(pair, fault))
        assert outcome.wrote is False and pair.snapshot() == before
        assert sorted(outcome.verify_withheld) == ["id:s0-m", "id:s1-m"]
        reason = _result(outcome, "id:s0-m", "translate_edit").reason
        assert "a change elsewhere caused it" in reason

    @pytest.mark.parametrize(
        "fault",
        [
            _Fault("order-parity", "injected deck-wide fault", None),
            _Fault("id-asymmetry", "injected fault on an unknown id", "ghost"),
        ],
        ids=["deck-wide", "unplaceable-id"],
    )
    def test_an_introduced_deck_wide_or_unplaceable_fault_writes_nothing(
        self, tmp_path: Path, fault: _Fault
    ):
        pair = self._pair(tmp_path)
        before = pair.snapshot()
        outcome = pair.apply(self.BODIES, gate=_faulty_gate(pair, fault))
        assert outcome.wrote is False
        assert pair.snapshot() == before
        assert outcome.ledger_changed is False
        for result in outcome.results:
            assert result.status == "deferred" and "fail deck-wide" in result.reason, result
        assert [v.message for v in outcome.verify_violations] == [fault.message]

    def test_a_fault_the_pair_already_carried_withholds_no_write(self, tmp_path: Path):
        # Only an INTRODUCED fault holds a write back. One the pair already
        # had keeps its slide out of the ledger (#992), but the slide's
        # changes still land — a fix that takes two passes (remove a narrated
        # slide, then its orphaned narration) must be able to take the first.
        pair = self._pair(tmp_path)
        fault = _Fault("id-asymmetry", "pre-existing fault on s1", "s1-m")
        gate = _faulty_gate(pair, fault, trigger='slide_id="s1-m"')
        outcome = pair.apply(self.BODIES, gate=gate)
        faulted = _result(outcome, "id:s1-m", "translate_edit")
        assert faulted.status == "applied"
        assert "recording deferred" in faulted.reason
        assert "DE zwei NEU" in pair.de_path.read_text(encoding="utf-8")
        assert outcome.verify_withheld == ["id:s1-m"]


# ---------------------------------------------------------------------------
# The contract, over a seeded sweep of random edits and random answers
# ---------------------------------------------------------------------------

_BODIES = ["SHOW(x);", "x = 1", "SHOW(x);", "y = 2", "SHOW(y);", "x = 1"]


def _render(cells: list[tuple[str, str]], lang: str) -> str:
    out = [HEADER_DE if lang == "de" else HEADER_EN]
    for kind, value in cells:
        if kind == "slide":
            out.append(
                f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{value}"\n'
                f"#\n# # {value} {lang}\n\n"
            )
        elif kind == "vo":
            out.append(
                f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{value}"\n'
                f"#\n# - n {lang}\n\n"
            )
        else:
            tags = ' tags=["keep"]' if kind == "codet" else ""
            out.append(f"# %%{tags}\n{value}\n\n")
    return "".join(out).rstrip("\n") + "\n"


def _random_deck(rng: random.Random) -> list[tuple[str, str]]:
    cells = [("slide", "intro")]
    for i in range(rng.randint(3, 6)):
        cells.append(("code", rng.choice(_BODIES)))
        if rng.random() < 0.3:
            cells.append(("vo", f"vo-{i}"))
    cells.append(("slide", "next"))
    for _ in range(rng.randint(0, 3)):
        cells.append(("code", rng.choice(_BODIES)))
    return cells


def _random_edit(cells: list[tuple[str, str]], rng: random.Random) -> list[tuple[str, str]]:
    cells = list(cells)
    for _ in range(rng.randint(1, 2)):
        op = rng.choice(["del", "move", "dup", "edit", "tag", "movevo"])
        candidates = [i for i, c in enumerate(cells) if c[0] != "slide"]
        if not candidates:
            break
        i = rng.choice(candidates)
        if op == "del":
            del cells[i]
        elif op in ("move", "movevo"):
            if op == "movevo":
                vos = [j for j, c in enumerate(cells) if c[0] == "vo"]
                if not vos:
                    continue
                i = rng.choice(vos)
            cell = cells.pop(i)
            cells.insert(rng.randint(1, len(cells)), cell)
        elif op == "dup":
            if cells[i][0] in ("code", "codet"):
                cells.insert(i + 1, cells[i])
        elif op == "edit":
            if cells[i][0] in ("code", "codet"):
                cells[i] = (cells[i][0], cells[i][1] + " // e")
        elif op == "tag" and cells[i][0] == "code":
            cells[i] = ("codet", cells[i][1])
    return cells


def _key_counts(violations) -> Counter:
    """Violations as a multiset of ``(kind, slide, message-modulo-line-numbers)``.

    Deliberately independent of the engine's own comparison key, so the
    contract is checked by a second reading, not by the code under test.
    """
    return Counter(
        (v.kind, v.slide_id, re.sub(r"\d+", "#", v.message) if v.slide_id is None else "")
        for v in violations
    )


#: Seeds whose pass made master WRITE a pair failing the verify (measured
#: over 1,200 seeds: 62 did), plus a plain range for breadth.
_MASTER_FAILING = [424, 432, 442, 488, 510, 565, 661, 717, 1010, 1193]


class TestDiskNeverGainsAViolation:
    """The agent contract: after ``apply`` the files on disk carry no
    structural violation the pair did not already carry — so a pair that
    passed the verify before still passes it after, whatever was answered."""

    @pytest.mark.parametrize("seed", _MASTER_FAILING + list(range(20)))
    def test_random_edit_and_answers(self, tmp_path: Path, seed: int):
        rng = random.Random(seed)
        cells = _random_deck(rng)
        pair = _Pair(tmp_path, _render(cells, "de"), _render(cells, "en"))
        edited = pair.en_path if rng.random() < 0.5 else pair.de_path
        lang = "en" if edited is pair.en_path else "de"
        edited.write_text(_render(_random_edit(cells, rng), lang), encoding="utf-8")
        before = _key_counts(pair.violations())
        diff = pair.diff()
        if diff.refusal is not None:
            pytest.skip("the edit leaves a pair the lens refuses")
        rows = []
        for item in diff.items:
            answers = [a for a in doc_apply.item_answers(item) if a != "body"]
            if item.action in FRAMED_ACTIONS and answers and rng.random() < 0.7:
                rows.append(doc_apply.Decision(key=item.key, choice=rng.choice(answers)))
        outcome = pair.apply(rows)
        assert outcome.error is None, outcome.to_payload()
        gained = _key_counts(pair.violations()) - before
        assert not gained, (gained, outcome.to_payload())


class TestUnifyLocus:
    """The ``unify`` finding's position-independent identity (review of #1051)."""

    @staticmethod
    def _locus(de: str, en: str) -> str | None:
        from clm.slides.sync_verify import structural_violations

        (found,) = [v for v in structural_violations(de, en, "#") if v.kind == "unify"]
        return found.locus

    def test_a_fault_that_moved_keeps_its_locus(self):
        de = _three_slides("de")
        en = _three_slides("en").replace("x = 1", "x = 2")  # shared cell diverges
        moved = de.replace("# DE eins", "# DE eins\n# more\n# lines")  # shifts DE lines
        assert self._locus(de, en) == self._locus(moved, en)
        assert "line" not in (self._locus(de, en) or "line")

    def test_a_fault_on_another_cell_gets_another_locus(self):
        de = _three_slides("de")
        en = _three_slides("en")
        x_fault = en.replace("x = 1", "x = 2")
        other = de.replace("x = 1", "x = 1  # DE only")
        assert self._locus(de, x_fault) != self._locus(other, en)
