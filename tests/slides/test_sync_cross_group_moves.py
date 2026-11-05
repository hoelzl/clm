"""A shared un-id'd cell moved into another slide group (#1142).

Pools pair positional cells per slide group, so a code cell one half moves
into another group used to read as a removal in one pool and a cold
one-sided cell in the other. Neither could execute (the removal would delete
the cell, the conservation check withholds it), and sync stalled on an
answerless ``verify_cold`` -- even for a byte-unchanged cell whose
fingerprint proves where it went.

Sync now recognises the move and mirrors it mechanically
(``mirror_pool_move``) when it is proven without a pairing guess:
- the moving half holds the slot's recorded content once, in another group;
- the other half holds the slot in its recorded group, unedited or edited in
  place (the row then carries that edit to the moved copy);
- or, by elimination, the moving half also edited it: its only missing slot
  and its only unrecorded cell of the kind, where "moved and edited" and
  "deleted plus added there" land the same bytes in the same place.

Each case drives report -> apply through the real pre-write verify (the
probe's careful-agent loop) and checks convergence, every edit kept, and
the intended final order on both halves.
"""

from __future__ import annotations

import pytest

from tests.slides import sync_probe as sp

BASE = ("S0", "a", "c1", "b", "S1", "e", "c2", "c3", "S2")
SUB = ("S0", "a", "c1", "U1", "c2", "b", "S1")


def _case(base, moved_order, *, mover="en", edits=(), edit_side=None):
    other = "de" if mover == "en" else "en"
    de, en = (moved_order, base) if mover == "de" else (base, moved_order)
    rnd = sp.Round(de, en, tuple(edits), edit_side or other, mover, moved_order)
    return sp.Case("cross_group", base, (rnd,))


def _first_pass_actions(result: sp.Result) -> set[str]:
    return {action for _, action in result.passes[0]}


@pytest.mark.parametrize("mover", ["en", "de"])
@pytest.mark.parametrize(
    "order",
    [
        pytest.param(("S0", "a", "c1", "b", "c3", "S1", "e", "c2", "S2"), id="to-end-of-S0"),
        pytest.param(("S0", "a", "b", "S1", "e", "c2", "c1", "c3", "S2"), id="into-middle-of-S1"),
        pytest.param(("S0", "c2", "a", "b", "S1", "e", "c1", "c3", "S2"), id="swap-issue-example"),
    ],
)
def test_an_unedited_move_across_groups_is_mirrored_in_one_pass(order, mover):
    result = sp.run_case(_case(BASE, order, mover=mover))
    assert result.status == "converged", (result.status, result.detail, result.passes)
    assert "mirror_pool_move" in _first_pass_actions(result), result.passes
    assert len(result.passes) == 1, result.passes


@pytest.mark.parametrize("mover", ["en", "de"])
@pytest.mark.parametrize(
    ("order", "edits"),
    [
        pytest.param(
            ("S0", "a", "b", "S1", "e", "c2", "c1", "c3", "S2"), ("c1",), id="moved-cell-edited"
        ),
        pytest.param(
            ("S0", "c2", "a", "b", "S1", "e", "c1", "c3", "S2"), ("c1", "c2"), id="both-edited"
        ),
    ],
)
def test_the_other_half_editing_in_place_rides_with_the_move(order, edits, mover):
    """The half that did not move the cells edited them where they were:
    the move row carries that edit to the moved copy."""
    other = "de" if mover == "en" else "en"
    result = sp.run_case(_case(BASE, order, mover=mover, edits=edits, edit_side=other))
    assert result.status == "converged", (result.status, result.detail, result.passes)
    assert "mirror_pool_move" in _first_pass_actions(result), result.passes


@pytest.mark.parametrize("mover", ["en", "de"])
def test_a_move_across_a_subslide_with_both_cells_edited_on_the_other_half(mover):
    """Both cells cross the id'd subslide on one half; the other half edits
    both in place. Before #1142 the S0 placement landed and a
    ``conflict_shared`` on a mis-paired slot silently undid the move."""
    order = ("S0", "b", "c2", "a", "U1", "c1", "S1")
    other = "de" if mover == "en" else "en"
    result = sp.run_case(_case(SUB, order, mover=mover, edits=("c1", "c2"), edit_side=other))
    assert result.status == "converged", (result.status, result.detail, result.passes)


@pytest.mark.parametrize("mover", ["en", "de"])
def test_a_cell_moved_and_edited_on_one_half_is_paired_by_elimination(mover):
    """``c1`` moves and is edited on the moving half (no fingerprint proof),
    next to ``c2``'s proven move the other way."""
    order = ("S0", "c2", "a", "b", "S1", "e", "c1", "c3", "S2")
    result = sp.run_case(_case(BASE, order, mover=mover, edits=("c1",), edit_side=mover))
    assert result.status == "converged", (result.status, result.detail, result.passes)
    assert "mirror_pool_move" in _first_pass_actions(result), result.passes


def test_a_placement_never_carries_a_cell_into_another_group():
    """EN moves ``vo-a`` and ``vo-b`` into S1 around ``c2``. The open
    placement question defers the voiceovers' own moves (#885), so the
    placement must anchor ``c2`` inside S1 on DE, not after DE's ``vo-b``,
    still in S0. That intermediate layout used to read as a DE-side move."""
    order = ("S0", "c1", "S1", "b", "c2", "a", "e", "c3", "S2")
    result = sp.run_case(_case(BASE, order))
    assert result.status == "converged", (result.status, result.detail, result.passes)


def test_a_group_split_is_not_taken_for_a_move():
    """A slide inserted on one half re-homes the cells after it on that half
    only: the slide is what must be mirrored, so no cell move is framed
    (#610/#630)."""
    base = ("S0", "a", "c1", "c2", "S2")
    split = ("S0", "a", "c1", "S1", "c2", "S2")
    case = sp.Case("cross_group", base, (sp.Round(base, split, (), "en", "en", split),))
    result = sp.run_case(case)
    assert all(action != "mirror_pool_move" for rows in result.passes for _, action in rows), (
        result.passes
    )


def test_byte_identical_cells_are_not_taken_for_a_move():
    """Two cells with one body: neither fingerprint names a single slot."""
    base = ("S0", "a", "d1", "b", "S1", "e", "d2", "S2")
    moved = ("S0", "a", "b", "S1", "e", "d1", "d2", "S2")
    case = sp.Case(
        "cross_group",
        base,
        (sp.Round(base, moved, (), "en", "en", moved),),
    )
    result = sp.run_case(case)
    assert all(action != "mirror_pool_move" for rows in result.passes for _, action in rows), (
        result.passes
    )


# -- shapes the #1142 adversarial review found: no move may be inferred ------


def _first_diff_actions(base, de_order, en_order, *, de_edits=(), en_edits=()):
    """The actions of the first report after one round of changes."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        de, en = Path(tmp) / "slides_t.de.py", Path(tmp) / "slides_t.en.py"
        none: frozenset[str] = frozenset()
        de.write_text(sp.deck_text("de", base, none), encoding="utf-8")
        en.write_text(sp.deck_text("en", base, none), encoding="utf-8")
        sp._record(de, en)
        de.write_text(sp.deck_text("de", de_order, frozenset(de_edits)), encoding="utf-8")
        en.write_text(sp.deck_text("en", en_order, frozenset(en_edits)), encoding="utf-8")
        _, diff = sp._diff(de, en)
        return [(i.key, i.action) for i in diff.items]


@pytest.mark.parametrize(
    ("base", "de_order", "en_order", "de_edits", "en_edits"),
    [
        pytest.param(
            ("S0", "a", "c1", "S1", "b", "c2", "S2"),
            ("S0", "a", "S1", "b", "c2", "S2"),
            ("S0", "a", "c1", "S1", "b", "S2"),
            (),
            ("c1",),
            id="other-half-deleted-the-slot",  # F1: DE's deletion must stay a question
        ),
        pytest.param(
            ("S0", "a", "c1", "c2", "S1", "b", "c3", "S2"),
            ("S0", "a", "c1", "S1", "b", "c2", "c3", "S2"),
            ("S0", "c2", "a", "c1", "S1", "b", "c3", "S2"),
            (),
            (),
            id="both-halves-moved-the-cell",  # F3
        ),
        pytest.param(
            ("S0", "a", "c1", "c2", "S1", "b", "c3", "S2", "e", "c4", "S3"),
            ("S0", "a", "c1", "c4", "c2", "S1", "b", "c3", "S2", "e", "S3"),
            ("S0", "a", "c2", "S1", "b", "c3", "S2", "e", "c4", "S3"),
            (),
            ("c2",),
            id="destination-pool-has-its-own-changes",  # F5
        ),
        pytest.param(
            ("S0", "a", "c1", "c2", "S1", "b", "c3", "S2"),
            ("S0", "a", "S9", "c1", "c2", "S1", "b", "c3", "S2"),
            ("S0", "a", "c2", "S9", "c1", "S1", "b", "c3", "S2"),
            (),
            (),
            id="slide-inserted-at-different-points",  # probe split_both
        ),
    ],
)
def test_no_move_is_inferred_where_the_evidence_does_not_prove_it(
    base, de_order, en_order, de_edits, en_edits
):
    actions = _first_diff_actions(base, de_order, en_order, de_edits=de_edits, en_edits=en_edits)
    assert all(action != "mirror_pool_move" for _, action in actions), actions


def test_a_byte_identical_sibling_does_not_hide_a_lost_slot():
    """F4: DE edits ``d1`` (whose twin ``d2`` keeps the bytes) and deletes
    ``c2``; EN moves a voiceover. "c2 moved and edited" must not be read."""
    base = ("S0", "a", "c1", "d1", "S1", "b", "d2", "c2", "S2", "e", "S3")
    de_order = ("S0", "a", "c1", "d1", "S1", "b", "d2", "S2", "e", "S3")
    en_order = ("S0", "a", "c1", "d1", "e", "S1", "b", "d2", "c2", "S2", "S3")
    actions = _first_diff_actions(base, de_order, en_order, de_edits=("d1",))
    assert all(action != "mirror_pool_move" for _, action in actions), actions
