"""The sequence core satisfies every sync law (``clm.slides.doc_core``).

The switch-over of ``docs/claude/design/sync-sequence-core.md`` rests on a
claim: the core satisfies the oracle-free laws of ``tests/slides/sync_laws.py``
on every small deck, where the current engine does not. This is the test that
executes that claim, over the same cases the engine's ratchet runs
(``tests/slides/test_sync_laws.py``), plus the laws across passes on their
smallest scope and the adapter from parsed decks and the recorded sequence.

The wider checks run by hand before any change to the core's rules (the
module docstring lists them): ``scripts/sync_model_probe.py``,
``scripts/sync_model_laws.py [--tags]``,
``scripts/sync_model_passes.py --base 3 --half 3`` and
``scripts/sync_shadow.py`` on both course repositories.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from clm.slides import doc_core
from clm.slides.doc_lenses import _read_source, parse_bundle
from clm.slides.doc_sequence import deck_sequence, sequence_fingerprint
from tests.slides.sync_laws import HOLDS, Case, cases, check

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_laws_script = _load("sync_laws")


def test_reference_model_satisfies_every_law() -> None:
    violations = {
        case.id: outcome
        for case in cases("ci")
        if (outcome := check(case, sync_laws_script.model_sync)) != HOLDS
    }
    assert not violations, dict(list(violations.items())[:10])


def test_reference_model_asks_about_identical_cells_each_half_edited() -> None:
    """Decision D3: two byte-identical recorded cells, each half edited one.
    Whether they edited the same cell cannot be read from the files, so the
    model asks instead of merging both edits."""
    asked = sync_laws_script.model_sync(("x", "x"), ("y", "x"), ("x", "z"))
    assert (asked.status, asked.detail) == ("asks", "conflict_copies")
    # one half alone editing a copy needs no question (the one-sided law)
    case = Case("one-sided", ("x", "x"), ("y", "x"), ("x", "x"))
    assert check(case, sync_laws_script.model_sync) == HOLDS


# ---------------------------------------------------------------------------
# Several passes, with questions left open (scripts/sync_model_passes.py)
# ---------------------------------------------------------------------------


passes = _load("sync_model_passes")


def _word(text: str) -> tuple[str, ...]:
    return () if text == "-" else tuple(text.split())


#: ``recorded | German | English``. Each broke an earlier design of the pass
#: that goes on around open questions: a later pass changed what the first had
#: written, asked something new, or ended somewhere else.
PARTIAL_PASS_CASES = [
    "- | A B | B A",  # recording a cell both halves added settled an open order
    "x | - | y A y",  # an identity answer was lost while its cell was held
    "x x | - | A x",  # one identical copy was settled, the question about the other open
    "A x | A y | y y",  # a held cell's place in the record moved between passes
    "x | A y y | z y",  # the next pass matched the wrong one of two identical cells
    "A y | B x x | y A",  # removing a cell made an identity question disappear
    "A B | x y A | y x B",  # two held cells swapped places in the record
    "- | x y A | y x B",  # additions of the two halves side by side
    "x x | A B x | x",  # each half removed one of two identical cells
]


@pytest.mark.parametrize("case", PARTIAL_PASS_CASES)
def test_pass_with_open_questions_is_safe(case: str) -> None:
    base, de, en = (_word(part.strip()) for part in case.split("|"))
    violations, _notes = passes.check_case(base, de, en)
    assert not violations, violations


def test_pass_goes_on_around_a_question_about_an_id_cell() -> None:
    """German adds a localized cell (it needs a translation), English adds a
    shared one. With the translation left open, the shared cell still lands
    on the German half and in the record; the localized cell waits."""
    base, de, en = passes.start(_word("x"), _word("x A"), _word("x y"))
    out = doc_core.merge(base, de, en, lambda _q: None)
    assert [q.kind for q in out.opened] == ["translate_new"]
    assert passes.labels(out.halves["de"]) == ("body:x", "id:A", "body:y")
    assert passes.labels(out.halves["en"]) == ("body:x", "body:y")
    assert [m.sid or m.de for m in out.record] == ["x", "y"]


def test_any_other_open_question_holds_the_whole_part() -> None:
    """Both halves edited one cell differently and English added another.
    While the conflict is open nothing is written: whether a question about
    un-id'd cells is asked depends on the cells around it."""
    base, de, en = passes.start(_word("x"), _word("y"), _word("z z"))
    out = doc_core.merge(base, de, en, lambda _q: None)
    assert out.opened and out.opened[0].kind not in doc_core.LOCAL_QUESTIONS
    assert (out.halves["de"], out.halves["en"], out.record) == (de, en, base)


@pytest.mark.slow
def test_passes_with_open_questions_are_safe_on_every_tiny_deck() -> None:
    """The exhaustive check of ``scripts/sync_model_passes.py`` on its
    smallest scope (the wider ones are run by hand)."""
    for base in passes.all_words(1, bodies=("x", "y")):
        stats, bad = passes.check_base(base, half_len=2)
        assert not bad, bad[:5]
        assert stats["cases"] > 0


# ---------------------------------------------------------------------------
# Tags, slide-hood and a slide_id gained on one half: the open issues' shapes
# ---------------------------------------------------------------------------


def _half(lang: str, *specs: tuple) -> list:
    """Cells from ``(id or None, body, *tags)``: an id'd ``S*`` / ``vo-*``
    cell is localized (its body is per language), anything else is shared
    code."""
    out = []
    for sid, body, *tags in specs:
        localized = sid is not None and sid.startswith(("S", "vo-"))
        text = f"{body} {lang}" if localized else body
        kind = "slide" if localized and sid.startswith("S") else "vo" if localized else "code"
        out.append(doc_core.Cell(sid, kind, text, localized, tags=frozenset(tags)))
    return out


def _sync(recorded: list[tuple], de: list[tuple], en: list[tuple]):
    """Record ``recorded`` on both halves, then merge ``de`` / ``en`` with
    every question refused."""
    base = [
        doc_core.BaseMember(d.sid, d.kind, d.localized, d.body, e.body, tags=d.tags)
        for d, e in zip(_half("de", *recorded), _half("en", *recorded), strict=True)
    ]
    return doc_core.merge(base, _half("de", *de), _half("en", *en), lambda _q: None)


def _shape(cells: list) -> list[tuple]:
    """A half without its language: ``(id or body, tags)`` per cell."""
    return [(c.sid or c.body, tuple(sorted(c.tags))) for c in cells]


S0, S1 = ("S0", "Intro"), ("S1", "Next")


def test_shared_cell_promoted_to_subslide_without_an_id() -> None:
    """#1216: English adds ``subslide`` to a shared code cell and no id. The
    engine stops on ``idless_anchor``. Here it is a cell whose tags changed."""
    recorded = [S0, (None, "#include <string>", "keep"), (None, "int x{};")]
    english = [S0, (None, "#include <string>", "keep", "subslide"), (None, "int x{};")]
    out = _sync(recorded, recorded, english)
    assert not out.opened and not out.asked
    assert _shape(out.halves["de"]) == _shape(out.halves["en"]) == _shape(_half("en", *english))


def test_shared_cell_promoted_to_subslide_with_an_id() -> None:
    """#1092, shape 1: the same promotion with a ``slide_id``. The engine
    frames it as a new cell plus a removal; here the German cell gains the tag
    and the id, and nothing is duplicated."""
    recorded = [S0, (None, "values_in{};", "keep"), (None, "sum(values_in);")]
    english = [
        S0,
        ("sum-while-demo", "values_in{};", "keep", "subslide"),
        (None, "sum(values_in);"),
    ]
    out = _sync(recorded, recorded, english)
    assert not out.opened and not out.asked
    assert _shape(out.halves["de"]) == _shape(out.halves["en"]) == _shape(_half("en", *english))


def test_cells_inserted_before_a_cell_promoted_to_subslide() -> None:
    """#1092, shape 2: English inserts three cells in the middle of a slide
    and promotes the cell after them. The engine reads the insertion as edits
    of the following cells and withholds the pass."""
    recorded = [S0, (None, "SHOW(first);"), (None, "text_out;"), (None, "SHOW(text_out);"), S1]
    english = [
        S0,
        (None, "SHOW(first);"),
        (None, "int second{};"),
        (None, "numbers_in >> second;"),
        (None, "SHOW(second);"),
        ("ostringstream-demo", "text_out;", "subslide"),
        (None, "SHOW(text_out);"),
        S1,
    ]
    out = _sync(recorded, recorded, english)
    assert not out.opened and not out.asked
    assert _shape(out.halves["de"]) == _shape(out.halves["en"]) == _shape(_half("en", *english))


def test_new_subslide_anchor_followed_by_new_cells() -> None:
    """#1092, shape 3: a new id'd shared anchor with new un-id'd cells under
    it. The engine asks for hand-made ids for the cells under the anchor."""
    recorded = [S0, (None, "first();"), S1]
    english = [
        S0,
        (None, "first();"),
        ("sum-for-demo", "scoped_in{};", "subslide"),
        (None, "for (;;) {}"),
        (None, "SHOW(sum);"),
        S1,
    ]
    out = _sync(recorded, recorded, english)
    assert not out.opened and not out.asked
    assert _shape(out.halves["de"]) == _shape(out.halves["en"]) == _shape(_half("en", *english))


def test_move_past_a_cell_that_is_retagged_in_the_same_edit() -> None:
    """#1187: English moves a shared cell above an id'd cell and tags that
    cell ``subslide``. The engine mirrors the tag, which re-keys the cells
    under it and strands the pending move. Here one half changed, so nothing
    is asked and both halves end as that half."""
    recorded = [S0, ("vo-m1", "M1"), (None, "b = 2"), (None, "c = 3"), S1]
    english = [S0, (None, "b = 2"), ("vo-m1", "M1", "subslide"), (None, "c = 3"), S1]
    out = _sync(recorded, recorded, english)
    assert not out.opened and not out.asked
    assert _shape(out.halves["de"]) == _shape(out.halves["en"]) == _shape(_half("en", *english))


def test_promotion_on_one_half_and_an_edit_of_the_same_cell_on_the_other() -> None:
    """Tags and content are merged apart: German's edit and English's
    promotion (with an id) both land, without a question."""
    recorded = [S0, (None, "values_in{};", "keep"), (None, "sum(values_in);")]
    german = [S0, (None, "values_in{1, 2};", "keep"), (None, "sum(values_in);")]
    english = [S0, ("demo", "values_in{};", "keep", "subslide"), (None, "sum(values_in);")]
    out = _sync(recorded, german, english)
    assert not out.opened and not out.asked
    merged = [("S0", ()), ("demo", ("keep", "subslide")), ("sum(values_in);", ())]
    assert _shape(out.halves["de"]) == _shape(out.halves["en"]) == merged
    assert out.halves["de"][1].body == out.halves["en"][1].body == "values_in{1, 2};"


def test_both_halves_retag_a_cell_differently() -> None:
    """The one question tags add. It is about more than its own cell, since a
    tag can start a slide, so it holds the part."""
    recorded = [S0, (None, "x = 1")]
    out = _sync(recorded, [S0, (None, "x = 1", "keep")], [S0, (None, "x = 1", "subslide")])
    assert [q.kind for q in out.opened] == ["conflict_tags"]
    assert "conflict_tags" not in doc_core.LOCAL_QUESTIONS
    assert _shape(out.halves["de"]) == [("S0", ()), ("x = 1", ("keep",))]


# ---------------------------------------------------------------------------
# Fork and unify: a cell that changes between shared and per-language
# ---------------------------------------------------------------------------


def _cell(sid, body: str, localized: bool):
    return doc_core.Cell(sid, "code", body, localized)


def _fork_case(german_localized: bool, english_localized: bool, recorded_localized: bool = False):
    body = {"de": "plot()  # Achse", "en": "plot()  # axis"}
    shared = "plot()"
    base = [
        doc_core.BaseMember(
            "fit",
            "code",
            recorded_localized,
            body["de"] if recorded_localized else shared,
            body["en"] if recorded_localized else shared,
        )
    ]

    def half(lang: str, localized: bool):
        return [_cell("fit", body[lang] if localized else shared, localized)]

    return base, half("de", german_localized), half("en", english_localized)


def test_a_fork_on_one_half_is_a_question_that_holds_the_part() -> None:
    """Found by the shadow on real history (the doc's §2.6): German turns a
    shared cell into its own language. Copying it would put German text in the
    English file; the English variant has to be written, which is a question."""
    base, de, en = _fork_case(True, False)
    out = doc_core.merge(base, de, en, lambda _q: None)
    assert [q.kind for q in out.opened] == ["fork"]
    assert out.halves == {"de": de, "en": en} and out.record == base
    answered = doc_core.merge(base, de, en, lambda q: "body" if q.kind == "fork" else None)
    assert not answered.opened
    assert answered.halves["de"] == de
    english = answered.halves["en"][0]
    assert english.localized and english.body != de[0].body and english.sid == "fit"


def test_a_fork_on_both_halves_needs_no_question() -> None:
    base, de, en = _fork_case(True, True)
    out = doc_core.merge(base, de, en, lambda _q: None)
    assert not out.opened and out.halves == {"de": de, "en": en}
    assert out.record[0].localized


def test_a_unify_on_one_half_is_a_question_and_takes_that_half() -> None:
    base, de, en = _fork_case(False, True, recorded_localized=True)
    out = doc_core.merge(base, de, en, lambda _q: None)
    assert [q.kind for q in out.opened] == ["unify"]
    answered = doc_core.merge(base, de, en, lambda q: "accept" if q.kind == "unify" else None)
    assert not answered.opened
    assert answered.halves["de"] == answered.halves["en"] == de
    assert not answered.record[0].localized


# ---------------------------------------------------------------------------
# The adapter: parsed cells and the recorded sequence
# ---------------------------------------------------------------------------

DE = """# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# # Einführung

# %%
x = 1

# %% tags=["keep"]
y = 2

# %% [markdown] lang="de" tags=["subslide"] slide_id="more"
# Mehr
"""
EN = (
    DE.replace('lang="de"', 'lang="en"')
    .replace("# # Einführung", "# # Introduction")
    .replace("# Mehr", "# More")
)


def _core_input(
    de: str, en: str
) -> tuple[list[doc_core.BaseMember], dict[str, list[doc_core.Cell]]]:
    """The recorded sequence of the in-sync pair ``DE``/``EN`` and the cells
    of ``de``/``en``, both with fingerprints as content: what a pass that
    reads the ledger compares."""
    deck = parse_bundle(DE, EN).deck
    sequence = deck_sequence(deck)
    assert not isinstance(sequence, str), sequence
    halves = {
        lang: doc_core.cells_of(_read_source(text, lang, "deck", "#").cells, sequence_fingerprint)
        for lang, text in (("de", de), ("en", en))
    }
    return doc_core.base_of(sequence), halves


def test_an_in_sync_deck_is_its_own_recorded_sequence() -> None:
    base, halves = _core_input(DE, EN)
    assert [(m.sid, m.kind, m.localized, sorted(m.tags)) for m in base] == [
        ("intro", "markdown", True, ["slide"]),
        (None, "code", False, []),
        (None, "code", False, ["keep"]),
        ("more", "markdown", True, ["subslide"]),
    ]
    out = doc_core.merge(base, halves["de"], halves["en"], lambda _q: None)
    assert out.status == "merged" and not out.asked
    assert out.halves == halves
    assert out.record == base


def test_a_one_sided_edit_of_an_unid_cell_reaches_the_other_half() -> None:
    """German edits an un-id'd shared cell. Matched against the recorded
    sequence by fingerprint, the edit is one member's new content, and the
    English half gets the German cell."""
    base, halves = _core_input(DE.replace("x = 1", "x = 10"), EN)
    out = doc_core.merge(base, halves["de"], halves["en"], lambda _q: None)
    assert out.status == "merged" and not out.asked
    assert out.halves["en"][1].body == halves["de"][1].body != base[1].de
    assert [c.body for c in out.halves["en"][2:]] == [c.body for c in halves["en"][2:]]
