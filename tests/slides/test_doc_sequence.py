"""The recorded cell sequence (structural-core step 4).

``clm.slides.doc_sequence`` defines the sequence a deck in sync records;
``clm.slides.doc_ledger`` stores it beside the current entries. Nothing in the
engine reads it yet (``docs/claude/sync-structural-core-investigation.md``
§9, steps 4 and 5), so these tests pin what is written, when, in which bytes,
and how it survives the verbs that rewrite ids.
"""

from __future__ import annotations

import json
from pathlib import Path

from clm.slides import doc_ledger
from clm.slides.doc_lenses import parse_bundle
from clm.slides.doc_sequence import SequenceEntry, canonical, deck_sequence
from tests.slides.sync_probe import _apply, _diff, _record

TITLE = {"de": "Einführung", "en": "Introduction"}
NEXT = {"de": "Weiter", "en": "Next"}


def deck(lang: str, *code: str, tags: str = '["slide"]', intro: str = "intro") -> str:
    cells = [
        f'# %% [markdown] lang="{lang}" tags={tags} slide_id="{intro}"\n# # {TITLE[lang]}\n',
        *(f"# %%\n{line}\n" for line in code),
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="next"\n# # {NEXT[lang]}\n',
    ]
    return "\n".join(cells)


def companion(lang: str, text: str, owner: str = "intro") -> str:
    return (
        f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-0" '
        f'for_slide="{owner}"\n# {text}\n'
    )


def parsed(de: str, en: str, de_c: str | None = None, en_c: str | None = None):
    return parse_bundle(de, en, de_c, en_c).deck


# ---------------------------------------------------------------------------
# What a deck in sync records
# ---------------------------------------------------------------------------


def test_a_deck_in_sync_records_one_sequence_over_both_files() -> None:
    seq = deck_sequence(
        parsed(
            deck("de", "x = 1"),
            deck("en", "x = 1"),
            companion("de", "Eins"),
            companion("en", "One"),
        )
    )
    assert isinstance(seq, list), seq
    assert [(e.sid, e.kind, e.file, e.localized, e.tags) for e in seq] == [
        ("intro", "markdown", "deck", True, ("slide",)),
        (None, "code", "deck", False, ()),
        ("next", "markdown", "deck", True, ("slide",)),
        ("vo-0", "markdown", "companion", True, ("voiceover",)),
    ]
    shared = seq[1]
    assert shared.de == shared.en  # a shared cell: one content on both halves
    assert seq[0].de != seq[0].en  # a localized cell: one per half


def test_the_content_leaves_out_the_id_and_the_tags() -> None:
    plain = deck_sequence(parsed(deck("de", "x = 1"), deck("en", "x = 1")))
    retagged = deck_sequence(
        parsed(deck("de", "x = 1", tags='["subslide"]'), deck("en", "x = 1", tags='["subslide"]'))
    )
    assert isinstance(plain, list) and isinstance(retagged, list)
    assert plain[0].de == retagged[0].de and plain[0].tags != retagged[0].tags
    assert canonical(parsed(deck("de"), deck("en")).groups[0].anchor.de).startswith(  # type: ignore[union-attr]
        '# %% [markdown] lang="de"'
    )


def test_a_deck_out_of_sync_records_no_sequence() -> None:
    assert isinstance(deck_sequence(parsed(deck("de", "x = 1"), deck("en", "x = 2"))), str)
    assert isinstance(
        deck_sequence(parsed(deck("de", "x = 1", "y = 2"), deck("en", "y = 2", "x = 1"))), str
    )
    assert deck_sequence(None) == "refused"


# ---------------------------------------------------------------------------
# The ledger: written on a full record, kept otherwise, exact bytes
# ---------------------------------------------------------------------------


def _write(tmp: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path, en_path = tmp / "slides_t.de.py", tmp / "slides_t.en.py"
    de_path.write_text(de, encoding="utf-8", newline="\n")
    en_path.write_text(en, encoding="utf-8", newline="\n")
    return de_path, en_path


def _section(de_path: Path) -> doc_ledger.DeckLedger:
    ledger = doc_ledger.load(doc_ledger.ledger_path_for(de_path))
    return ledger.decks[doc_ledger.deck_key_for(de_path)]


def test_a_full_record_writes_the_sequence_and_it_round_trips(tmp_path: Path) -> None:
    de_path, en_path = _write(tmp_path, deck("de", "x = 1"), deck("en", "x = 1"))
    _record(de_path, en_path)
    section = _section(de_path)
    assert section.sequence == deck_sequence(parsed(deck("de", "x = 1"), deck("en", "x = 1")))
    raw = json.loads(doc_ledger.ledger_path_for(de_path).read_text(encoding="utf-8"))
    recorded = raw["decks"]["slides_t"]["sequence"]
    assert recorded["version"] == doc_ledger.SEQUENCE_VERSION
    lines = recorded["cells"]  # one line per cell
    fp = section.sequence[1].de  # type: ignore[index]
    assert lines[1] == f"deck code S - {fp} -"
    assert lines[0].startswith("deck markdown L slide ") and lines[0].endswith(" intro")


def test_an_id_with_spaces_round_trips_and_an_unencodable_tag_records_nothing() -> None:
    entry = SequenceEntry("an id", "code", "deck", False, ("keep",), "f" * 64, "f" * 64)
    section = doc_ledger.DeckLedger(sequence=[entry])
    data = doc_ledger._deck_to_json(section)
    assert doc_ledger._deck_from_json(data).sequence == [entry]
    assert not doc_ledger._encodable(
        [SequenceEntry(None, "code", "deck", False, ("a b",), "f", "f")]
    )


def test_recording_the_same_deck_again_writes_nothing(tmp_path: Path) -> None:
    de_path, en_path = _write(tmp_path, deck("de", "x = 1"), deck("en", "x = 1"))
    _record(de_path, en_path)
    path = doc_ledger.ledger_path_for(de_path)
    ledger = doc_ledger.load(path)
    doc_ledger.record_deck_snapshot(
        ledger, "slides_t", parsed(deck("de", "x = 1"), deck("en", "x = 1")), provenance="record"
    )
    assert doc_ledger.save(ledger, path) is False  # #555: a clean sweep is write-free


def test_a_section_without_a_sequence_keeps_its_bytes(tmp_path: Path) -> None:
    de_path, en_path = _write(tmp_path, deck("de", "x = 1"), deck("en", "x = 1"))
    _record(de_path, en_path)
    path = doc_ledger.ledger_path_for(de_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["decks"]["slides_t"]["sequence"]  # a ledger written before step 4
    old_bytes = json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(old_bytes, encoding="utf-8", newline="\n")
    ledger = doc_ledger.load(path)
    assert ledger.decks["slides_t"].sequence is None
    assert doc_ledger.save(ledger, path) is False
    assert path.read_text(encoding="utf-8") == old_bytes


def test_a_sequence_of_another_version_loads_as_none(tmp_path: Path) -> None:
    de_path, en_path = _write(tmp_path, deck("de", "x = 1"), deck("en", "x = 1"))
    _record(de_path, en_path)
    path = doc_ledger.ledger_path_for(de_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["decks"]["slides_t"]["sequence"]["version"] = doc_ledger.SEQUENCE_VERSION + 1
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert doc_ledger.load(path).decks["slides_t"].sequence is None


def test_a_full_record_of_a_deck_out_of_sync_keeps_the_last_sequence(tmp_path: Path) -> None:
    de_path, en_path = _write(tmp_path, deck("de", "x = 1"), deck("en", "x = 1"))
    _record(de_path, en_path)
    before = _section(de_path).sequence
    _write(tmp_path, deck("de", "x = 2"), deck("en", "x = 1"))
    _record(de_path, en_path)  # record blesses the divergence; the sequence does not
    assert _section(de_path).sequence == before


# ---------------------------------------------------------------------------
# apply: a pass with nothing left open records it; anything else keeps it
# ---------------------------------------------------------------------------


def test_an_apply_pass_that_ends_in_sync_records_the_sequence(tmp_path: Path) -> None:
    de_path, en_path = _write(tmp_path, deck("de", "x = 1"), deck("en", "x = 1"))
    _record(de_path, en_path)
    _write(tmp_path, deck("de", "x = 2"), deck("en", "x = 1"))  # German edits shared code
    outcome = _apply(de_path, en_path, {})
    assert outcome.error is None
    assert en_path.read_text(encoding="utf-8") == deck("en", "x = 2")
    assert _section(de_path).sequence == deck_sequence(
        parsed(deck("de", "x = 2"), deck("en", "x = 2"))
    )


def test_an_apply_pass_with_a_question_left_open_keeps_the_sequence(tmp_path: Path) -> None:
    de_path, en_path = _write(tmp_path, deck("de", "x = 1"), deck("en", "x = 1"))
    _record(de_path, en_path)
    before = _section(de_path).sequence
    # German retags the first slide (a mechanical row that lands); both halves
    # edit the shared code differently (a question that stays open).
    _write(tmp_path, deck("de", "x = 2", tags='["subslide"]'), deck("en", "x = 3"))
    outcome = _apply(de_path, en_path, {})
    assert outcome.error is None
    assert 'tags=["subslide"]' in en_path.read_text(encoding="utf-8")  # the pass did land
    _, diff = _diff(de_path, en_path)
    assert "conflict_shared" in {i.action for i in diff.items}  # still open
    assert _section(de_path).sequence == before


# ---------------------------------------------------------------------------
# Renamed ids
# ---------------------------------------------------------------------------


def test_a_group_rename_renames_the_id_in_the_sequence() -> None:
    target = doc_ledger.DeckLedger(
        sequence=[SequenceEntry("intro", "markdown", "deck", True, ("slide",), "a", "b")]
    )
    doc_ledger.rename_group_scopes(target, "intro", "welcome")
    assert target.sequence is not None and target.sequence[0].sid == "welcome"


def test_rename_id_carries_the_sequence_across_the_rewritten_references() -> None:
    from clm.slides.rename_id import migrate_ledger_key, migrate_reference_fingerprints

    before = parsed(
        deck("de", "x = 1"), deck("en", "x = 1"), companion("de", "Eins"), companion("en", "One")
    )
    after = parsed(
        deck("de", "x = 1", intro="welcome"),
        deck("en", "x = 1", intro="welcome"),
        companion("de", "Eins", owner="welcome"),
        companion("en", "One", owner="welcome"),
    )
    assert before is not None and after is not None
    target = doc_ledger.snapshot_deck(before, provenance="record")
    migrate_ledger_key(target, "intro", "welcome")
    migrate_reference_fingerprints(target, before, after)
    assert target.sequence == deck_sequence(after)  # the for_slide bytes moved too


def test_rename_id_keeps_a_sequence_recorded_from_an_older_state() -> None:
    from clm.slides.rename_id import migrate_ledger_key, migrate_reference_fingerprints

    older = parsed(deck("de", "x = 0"), deck("en", "x = 0"))
    before = parsed(deck("de", "x = 1"), deck("en", "x = 1"))
    after = parsed(deck("de", "x = 1", intro="welcome"), deck("en", "x = 1", intro="welcome"))
    assert older is not None and before is not None and after is not None
    target = doc_ledger.snapshot_deck(older, provenance="record")
    migrate_ledger_key(target, "intro", "welcome")
    migrate_reference_fingerprints(target, before, after)
    assert target.sequence is not None
    assert target.sequence[0].sid == "welcome"  # the id moves
    assert target.sequence[1] == deck_sequence(older)[1]  # type: ignore[index]  # the edit is not re-fingerprinted


def test_halves_with_different_preambles_still_have_a_sequence() -> None:
    """A preamble is no cell: a translated README keeps all its text there,
    and the sequence core merges preambles on their own."""
    de = "# Eine Einleitung, die vor allen Zellen steht.\n\n" + deck("de", "x = 1")
    en = "# An introduction before every cell.\n\n" + deck("en", "x = 1")
    outcome = parsed(de, en)
    assert outcome is not None
    assert [o.kind for o in outcome.observations] == ["preamble_divergence"]
    assert isinstance(deck_sequence(outcome), list)
