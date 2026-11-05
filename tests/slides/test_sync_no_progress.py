"""An apply that makes no progress says so (#1150).

When the pre-write verify (#1051 B2) holds a slide back, nothing on that
slide is written or recorded, so the next report frames the same rows. If
the whole pass changed no file and no ledger baseline, the next report and
apply are exactly this pass again: an agent answering the same rows makes no
progress, ever (#1092 in the field). Apply now flags such a pass
(``no_progress``) and names the held slides, so the agent loop stops and the
slide is reconciled by hand.

The shapes come from the sync probe's ``livelock_withheld`` cases, one per
cause: the engine's own mechanical rows trip the gate; every real answer to
the held rows trips it; the pair already carries a violation no row fixes,
so the recording gate keeps the slide out of the ledger forever.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from clm.slides import doc_apply
from tests.slides import sync_probe as sp


def _decisions(diff, intent: str, edit_side: str) -> dict[str, doc_apply.Decision]:
    """The probe's careful-agent answers (``sync_probe.run_case``)."""
    out: dict[str, doc_apply.Decision] = {}
    for item in diff.items:
        answers = doc_apply.item_answers(item)
        choice = None
        if item.action in ("order_decision", "pool_placement_divergence"):
            choice = intent
        elif item.action == "conflict_shared":
            choice = edit_side
        elif item.action in ("verify_cold", "verify_translation") and "confirm" in answers:
            choice = "confirm"
        if choice is not None:
            out[item.key] = doc_apply.Decision(key=item.key, choice=choice)
    return out


def _drive(base, de_order, en_order, *, edits, edit_side, intent, passes=4):
    """Report -> apply until clean or ``passes`` applies; returns each
    pass's ``(rows, outcome)`` and the rows of the report after the last."""
    with tempfile.TemporaryDirectory() as tmp:
        de, en = Path(tmp) / "slides_t.de.py", Path(tmp) / "slides_t.en.py"
        none: frozenset[str] = frozenset()
        de.write_text(sp.deck_text("de", base, none), encoding="utf-8")
        en.write_text(sp.deck_text("en", base, none), encoding="utf-8")
        sp._record(de, en)
        de_edits = frozenset(edits) if edit_side == "de" else none
        en_edits = frozenset(edits) if edit_side == "en" else none
        de.write_text(sp.deck_text("de", de_order, de_edits), encoding="utf-8")
        en.write_text(sp.deck_text("en", en_order, en_edits), encoding="utf-8")
        history = []
        for _ in range(passes):
            _, diff = sp._diff(de, en)
            if diff.is_clean:
                return history, []
            rows = [(i.key, i.action) for i in diff.items]
            outcome = sp._apply(de, en, _decisions(diff, intent, edit_side))
            history.append((rows, outcome))
        _, diff = sp._diff(de, en)
        return history, [(i.key, i.action) for i in diff.items]


STUCK = [
    pytest.param(
        ("S0", "a", "c1", "b", "c2", "S1"),
        ("S0", "a", "c1", "b", "c2", "S1"),
        ("S0", "b", "c1", "a", "c2", "S1"),
        ("c1", "c2"),
        "en",
        "en",
        (),
        id="mechanical-rows-trip-the-gate",
    ),
    pytest.param(
        ("S0", "a", "d1", "b", "d2", "S1"),
        ("S0", "a", "d1", "b", "d2", "S1"),
        ("S0", "d1", "b", "a", "d2", "S1"),
        ("d1",),
        "de",
        "en",
        ("pos:s-S0/code/1",),
        id="every-answer-trips-the-gate",
    ),
    pytest.param(
        ("S0", "a", "c1", "b", "d1", "d2", "S1"),
        ("S0", "a", "c1", "b", "d1", "d2", "S1"),
        ("S0", "a", "c1", "b", "d1", "d2", "S1"),
        ("d1",),
        "en",
        "en",
        (),
        id="recording-gate-on-a-violation-no-row-fixes",
    ),
]


@pytest.mark.parametrize(
    ("base", "de_order", "en_order", "edits", "edit_side", "intent", "answered"), STUCK
)
def test_a_pass_that_changes_nothing_is_flagged(
    base, de_order, en_order, edits, edit_side, intent, answered
):
    history, after = _drive(
        base, de_order, en_order, edits=edits, edit_side=edit_side, intent=intent
    )
    flagged = [i for i, (_, outcome) in enumerate(history) if outcome.no_progress is not None]
    assert flagged, [(rows, o.to_payload()["verify_withheld"]) for rows, o in history]
    first = flagged[0]
    rows, outcome = history[first]
    # The flag is exact: the next report frames this pass's rows again.
    next_rows = history[first + 1][0] if first + 1 < len(history) else after
    assert next_rows == rows
    stall = outcome.no_progress
    assert stall is not None
    assert [s.slide for s in stall.slides] == ["s-S0"]
    assert stall.reconcile == ["s-S0"]
    held = stall.slides[0]
    assert set(held.unwritten + held.unrecorded) <= {key for key, _ in rows}
    payload = outcome.to_payload()["no_progress"]
    assert payload["slides"][0]["slide"] == "s-S0"
    assert payload["reconcile"] == ["s-S0"]
    assert "by hand" in payload["advice"]
    # Held rows the agent answered are named: a different answer can get a
    # slide through, at the cost of one half's content (probe: 1,145 of
    # 6,284 livelocks). Only mechanical holds say no answer can help.
    assert stall.answered == list(answered)
    assert ("No answer can change this" in stall.advice) == (not answered)
    # Every pass before the flagged one made progress.
    assert all(o.no_progress is None for _, o in history[:first])


def test_unanswered_questions_are_not_a_stall():
    """A pass that writes nothing because the agent left a question open is
    the loop's normal state: no flag."""
    base = ("S0", "a", "c1", "S1")
    with tempfile.TemporaryDirectory() as tmp:
        de, en = Path(tmp) / "slides_t.de.py", Path(tmp) / "slides_t.en.py"
        none: frozenset[str] = frozenset()
        de.write_text(sp.deck_text("de", base, none), encoding="utf-8")
        en.write_text(sp.deck_text("en", base, none), encoding="utf-8")
        sp._record(de, en)
        en.write_text(sp.deck_text("en", ("S0", "c1", "a", "S1"), none), encoding="utf-8")
        de.write_text(sp.deck_text("de", ("S0", "a", "c1", "S1"), frozenset({"c1"})), "utf-8")
        _, diff = sp._diff(de, en)
        assert not diff.is_clean
        outcome = sp._apply(de, en, {})
        assert not outcome.wrote
        assert outcome.no_progress is None
        assert outcome.to_payload()["no_progress"] is None


def test_a_pass_that_writes_is_not_a_stall():
    """A held slide beside a slide whose changes land is progress: the next
    report differs."""
    base = ("S0", "a", "c1", "b", "c2", "S1", "e", "c3", "S2")
    en_order = ("S0", "b", "c1", "a", "c2", "S1", "e", "c3", "S2")
    history, _ = _drive(
        base,
        base,
        en_order,
        edits=("c1", "c2", "c3"),
        edit_side="en",
        intent="en",
        passes=1,
    )
    rows, outcome = history[0]
    assert outcome.wrote, rows
    assert outcome.verify_withheld, rows  # the gate did hold slides back
    assert outcome.no_progress is None


def test_a_pending_answerable_row_is_not_a_stall():
    """A held slide whose fix is a question the agent has not answered yet: an
    id-less shared cell forked on EN frames ``translate_new``. Without an answer
    the pass changes nothing, but the answer resolves it, so "no answer can
    change this" would be wrong (#787 check)."""
    fork_en = '# %% lang="en" slide_id="cc"\n# better\nx = 1\n\n'
    head = {
        lang: f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="s0"\n#\n# # s0 {lang}\n\n'
        for lang in ("de", "en")
    }
    with tempfile.TemporaryDirectory() as tmp:
        de, en = Path(tmp) / "slides_t.de.py", Path(tmp) / "slides_t.en.py"
        de.write_text(sp.HEADER["de"] + head["de"] + "# %%\n# prose\nx = 1\n", encoding="utf-8")
        en.write_text(sp.HEADER["en"] + head["en"] + "# %%\n# prose\nx = 1\n", encoding="utf-8")
        sp._record(de, en)
        en.write_text(sp.HEADER["en"] + head["en"] + fork_en, encoding="utf-8")

        _, diff = sp._diff(de, en)
        pos_key = next(i.key for i in diff.items if i.action == "stamp_vs_new")
        sp._apply(de, en, {pos_key: doc_apply.Decision(key=pos_key, choice="treat_as_new")})

        _, diff = sp._diff(de, en)
        assert ("id:cc", "translate_new") in [(i.key, i.action) for i in diff.items]
        outcome = sp._apply(de, en, {})
        assert outcome.verify_withheld, outcome.to_payload()  # the gate holds rows back
        assert not outcome.wrote
        assert outcome.no_progress is None, outcome.no_progress

        # And the pending answer does resolve it.
        answer = doc_apply.Decision(key="id:cc", body="# besser\nx = 1")
        sp._apply(de, en, {"id:cc": answer})
        _, diff = sp._diff(de, en)
        assert diff.is_clean, [(i.key, i.action) for i in diff.items]
