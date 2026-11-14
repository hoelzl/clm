"""One pass of the sequence core over a deck on disk (``clm.slides.doc_pass``).

Phase C of ``docs/claude/design/sync-sequence-core.md``: what ``clm slides
sync report`` / ``apply`` do under ``CLM_SYNC_CORE=sequence``. The laws,
the sweep and the probe run the same pass over thousands of generated decks
(``sync_laws_sequence.py``, ``sync_engines.py``); these tests pin the rows,
the handles, the answers and the ledger on small ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.slides import doc_ledger, doc_pass
from clm.slides.doc_apply import Decision
from clm.slides.doc_lenses import load_bundle
from tests.slides.sync_engines import POSITIONAL, SEQUENCE, record

DE = """# j2 from 'macros.j2' import header_de
# {{ header_de("Titel") }}

# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
#
# # Einführung

# %%
x = 1

# %%
y = 2

# %% [markdown] lang="de" tags=["voiceover"] slide_id="vo-intro"
#
# - Hallo
"""
EN = (
    DE.replace('lang="de"', 'lang="en"')
    .replace("header_de", "header_en")
    .replace("Titel", "Title")
    .replace("# # Einführung", "# # Introduction")
    .replace("# - Hallo", "# - Hello")
)


class Deck:
    def __init__(self, root: Path) -> None:
        self.de = root / "slides_t.de.py"
        self.en = root / "slides_t.en.py"
        self.write(DE, EN)
        record(self.de, self.en)

    def write(self, de: str | None = None, en: str | None = None) -> None:
        for path, text in ((self.de, de), (self.en, en)):
            if text is not None:
                path.write_text(text, encoding="utf-8", newline="\n")

    def ledger(self) -> doc_ledger.TopicLedger:
        return doc_ledger.load(doc_ledger.ledger_path_for(self.de))

    def run(self, *decisions: Decision) -> doc_pass.Pass:
        bundle = load_bundle(self.de, self.en)
        section = self.ledger().decks.get("slides_t")
        return doc_pass.run_pass(bundle, section, list(decisions))

    def apply(self, *decisions: Decision) -> doc_pass.Pass:
        bundle = load_bundle(self.de, self.en)
        ledger = self.ledger()
        result = doc_pass.run_pass(bundle, ledger.decks.get("slides_t"), list(decisions))
        if not result.held and result.error is None and result.refusal is None:
            doc_pass.land(bundle, ledger, "slides_t", result)
            doc_ledger.save(ledger, doc_ledger.ledger_path_for(self.de))
        return result

    def text(self, lang: str) -> str:
        return (self.de if lang == "de" else self.en).read_text(encoding="utf-8")


@pytest.fixture
def deck(tmp_path: Path) -> Deck:
    return Deck(tmp_path)


def _rows(result: doc_pass.Pass) -> list[tuple[str, str, str | None]]:
    return [(r.action, r.key, r.side) for r in result.rows]


def test_an_unchanged_deck_is_clean(deck: Deck) -> None:
    result = deck.run()
    assert result.is_clean
    assert result.rendered == result.files


def test_a_shared_edit_is_a_change_not_a_question(deck: Deck) -> None:
    deck.write(de=DE.replace("x = 1", "x = 10"))
    result = deck.run()
    assert _rows(result) == [("mirror", "seq:3", "en")]
    assert not result.held
    deck.apply()
    assert deck.text("en") == EN.replace("x = 1", "x = 10")
    assert deck.run().is_clean
    assert POSITIONAL.report(deck.de, deck.en).clean


def test_a_translation_edit_asks_and_a_body_answers_it(deck: Deck) -> None:
    deck.write(de=DE.replace("# # Einführung", "# # Einleitung"))
    asked = deck.run()
    assert _rows(asked) == [("translate_edit", "id:intro", "de")]
    assert asked.held and asked.needs_model and not asked.needs_agent
    assert asked.rendered is None
    deck.apply(Decision(key="id:intro", body="#\n# # Opening"))
    assert deck.text("en") == EN.replace("# # Introduction", "# # Opening")
    assert deck.run().is_clean


def test_keep_twin_records_the_pair_as_it_is(deck: Deck) -> None:
    deck.write(de=DE.replace("# - Hallo", "# - Hallo!"))
    deck.apply(Decision(key="id:vo-intro", choice="keep_twin"))
    assert deck.text("en") == EN
    assert deck.run().is_clean


def test_a_localized_cell_new_on_one_half_needs_its_twin_s_text(deck: Deck) -> None:
    new = '# %% [markdown] lang="de" tags=["voiceover"] slide_id="vo-x"\n#\n# - Neu\n\n'
    deck.write(de=DE.replace("# %%\ny = 2", new + "# %%\ny = 2"))
    assert _rows(deck.run()) == [("translate_new", "id:vo-x", None)]
    deck.apply(Decision(key="id:vo-x", body="#\n# - New"))
    twin = new.replace('lang="de"', 'lang="en"').replace("Neu", "New")
    assert deck.text("en") == EN.replace("# %%\ny = 2", twin + "# %%\ny = 2")
    assert deck.run().is_clean


def test_the_title_macro_is_known_as_id_title(deck: Deck) -> None:
    deck.write(en=EN.replace('header_en("Title")', 'header_en("A title")'))
    assert _rows(deck.run()) == [("translate_edit", "id:title", "en")]


def test_an_answer_that_fits_no_row_is_rejected(deck: Deck) -> None:
    deck.write(de=DE.replace("# # Einführung", "# # Einleitung"))
    result = deck.run(
        Decision(key="id:intro", choice="confirm", action="translate_edit"),
        Decision(key="seq:9", choice="de"),
    )
    assert result.held
    assert [(r.key, r.reason.split(":")[0]) for r in result.rejected] == [
        ("id:intro", "translate_edit takes body, keep_twin (with `body`"),
        ("seq:9", "no row of this pass has this handle"),
    ]


def test_every_identify_question_is_listed_at_once(tmp_path: Path) -> None:
    """The core stops at the first open ``identify``; the report lists them
    all, so one decision document answers them (here: DE edited both code
    cells, EN reordered them)."""
    from tests.slides.sync_sweep import Case, run_case

    case = Case("four", "de", ("a", "c1", "c2", "b"), ("c1", "c2"))
    status, passes = run_case(case, engine=SEQUENCE)
    assert status == "converged"
    assert [action for _key, action in passes[0]] == ["identify", "identify"]


def test_a_deck_without_a_recorded_sequence_in_sync_is_its_own_base(deck: Deck) -> None:
    path = doc_ledger.ledger_path_for(deck.de)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["decks"]["slides_t"]["sequence"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert deck.run().is_clean  # in sync: the halves are the base
    deck.write(de=DE.replace("x = 1", "x = 10"))
    assert "records no cell sequence" in (deck.run().refusal or "")


def test_text_records_round_trip_through_the_ledger(deck: Deck) -> None:
    sequence = deck.ledger().decks["slides_t"].sequence
    assert sequence is not None
    texts = [e.texts for e in sequence]
    assert [t is not None for t in texts] == [True, True, True, False, False, True]
    intro = texts[2]
    assert intro is not None and intro[0].separator == 1 and intro[0].for_slide is None


def test_a_ledger_without_text_records_reads_the_fingerprints(deck: Deck) -> None:
    """A sequence recorded before phase C: an unchanged cell counts as
    recorded, a changed one as an edit of its text."""
    path = doc_ledger.ledger_path_for(deck.de)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["decks"]["slides_t"]["sequence"]["texts"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert deck.run().is_clean
    deck.write(en=EN.replace("# - Hello", "# - Hello there"))
    assert _rows(deck.run()) == [("translate_edit", "id:vo-intro", "en")]


def test_an_owner_attribute_changed_on_one_half_reaches_the_other(deck: Deck) -> None:
    deck.write(
        de=DE.replace('slide_id="vo-intro"', 'slide_id="vo-intro" for_slide="intro"'),
    )
    result = deck.run()
    assert _rows(result) == [("mirror_owner", "id:vo-intro", "en")]
    deck.apply()
    assert 'slide_id="vo-intro" for_slide="intro"' in deck.text("en")
    assert deck.run().is_clean


def _drop_sequence(deck: Deck) -> None:
    path = doc_ledger.ledger_path_for(deck.de)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["decks"]["slides_t"]["sequence"]
    path.write_text(json.dumps(data), encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_without_a_recorded_sequence_the_last_commit_is_the_base(tmp_path: Path) -> None:
    """A ledger section from before step 4: an edited deck starts from its
    committed state, when the positional ledger trusts that state."""
    deck = Deck(tmp_path)
    _drop_sequence(deck)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    deck.write(de=DE.replace("x = 1", "x = 10").replace("# - Hallo", "# - Hallo!"))
    assert _rows(deck.run()) == [
        ("translate_edit", "id:vo-intro", "de"),
        ("mirror", "seq:3", "en"),
    ]
    deck.apply(Decision(key="id:vo-intro", choice="keep_twin"))
    assert deck.text("en") == EN.replace("x = 1", "x = 10")
    assert deck.ledger().decks["slides_t"].sequence is not None
    assert deck.run().is_clean


def test_without_a_sequence_or_a_trusted_commit_the_deck_is_refused(tmp_path: Path) -> None:
    deck = Deck(tmp_path)
    _drop_sequence(deck)
    deck.write(de=DE.replace("x = 1", "x = 10"))
    assert "records no cell sequence" in (deck.run().refusal or "")


def test_without_a_sequence_a_pending_translation_is_still_asked(deck: Deck) -> None:
    """The halves are structurally in sync, so they are the base, but the
    positional ledger never verified the edited text: it is not blessed."""
    _drop_sequence(deck)
    deck.write(de=DE.replace("# - Hallo", "# - Hallo!"))
    assert _rows(deck.run()) == [("translate_edit", "id:vo-intro", "de")]


def test_a_deck_never_recorded_asks_verify_cold(tmp_path: Path) -> None:
    deck = Deck(tmp_path)
    doc_ledger.ledger_path_for(deck.de).unlink()
    rows = _rows(deck.run())
    assert sorted(rows) == [
        ("verify_cold", "id:intro", None),
        ("verify_cold", "id:title", None),
        ("verify_cold", "id:vo-intro", None),
    ]
    deck.apply(*(Decision(key=key, choice="confirm") for _a, key, _s in rows))
    assert deck.run().is_clean
