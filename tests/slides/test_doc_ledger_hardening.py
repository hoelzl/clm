"""Ledger hardening — the M8 lost update and the M13 provenance churn (review Q7).

A topic ledger is **one file holding independent per-deck sections**, and every
sync verb reads the whole file, mutates one section, and writes the whole file
back. Two things followed from that, both field-reported:

* **M8 — silent lost update.** Two runs on *different* decks of the same topic
  (the normal shape of a parallel sweep) each hold a whole-file copy. The second
  to save writes its pre-run view of the first's deck, reverting it. No error on
  either side; the trust store just loses an entry, and the next report frames
  the reverted members cold.
* **M13 — churn drowns review.** ``preserve_unchanged_member`` compared
  ``provenance``, and the normal loop alternates ``record`` and ``apply``, so
  every touched member rewrote on every pass with nothing about it changed.
  883-line ledger diffs for 60 changed cells.

The review's Q7 also asked for UUID temp names — ``atomic_write_bytes`` already
had them (``path_utils.py``), so there was nothing to do there; that is asserted
here so the claim is checked rather than assumed.

What is deliberately *not* claimed: merging is not locking. It shrinks the
lost-update window from the whole verb to the gap between the re-read and
``os.replace``. Same-deck concurrency is still last-writer-wins, and says so.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from clm.slides import doc_ledger
from clm.slides.bilingual_doc import BilingualDeck
from clm.slides.doc_lenses import parse_bundle

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


def _deck(slug: str, de_text: str = "DE Text", en_text: str = "EN text") -> BilingualDeck:
    de = _build(HEADER_DE, _slide(slug, "de", "Titel"), _localized(f"{slug}-m", "de", de_text))
    en = _build(HEADER_EN, _slide(slug, "en", "Title"), _localized(f"{slug}-m", "en", en_text))
    outcome = parse_bundle(de, en)
    assert outcome.deck is not None
    return outcome.deck


def _record(
    ledger: doc_ledger.TopicLedger,
    deck_key: str,
    deck: BilingualDeck,
    *,
    provenance: str = "record",
    commit: str | None = None,
    deliberate: bool = False,
) -> None:
    doc_ledger.record_deck_snapshot(
        ledger,
        deck_key,
        deck,
        provenance=provenance,
        commit=commit,
        deliberate_provenance=deliberate,
    )


def _seed(path: Path) -> None:
    """A committed ledger holding two deck sections."""
    ledger = doc_ledger.TopicLedger()
    _record(ledger, "slides_a", _deck("a"), commit="c0")
    _record(ledger, "slides_b", _deck("b"), commit="c0")
    doc_ledger.save(ledger, path)


def _body(path: Path, deck_key: str, member: str) -> str:
    """The recorded DE fingerprint of one member — a proxy for 'whose version won'."""
    return doc_ledger.load(path).decks[deck_key].members[member].entry.de_fp or ""


class TestConcurrentSiblingDecks:
    """M8: a parallel sweep must not silently revert a sibling deck's section."""

    def test_a_section_written_during_our_run_survives_our_save(self, tmp_path: Path):
        """The exact lost-update shape. Fails without merge-on-save.

        Run 1 loads, then run 2 loads, records a *new* deck and saves. Run 1 then
        saves its own work. Before the merge, run 1's whole-file write had no
        ``slides_c`` in it at all and simply deleted run 2's section.
        """
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)

        run1 = doc_ledger.load(path)
        run2 = doc_ledger.load(path)

        _record(run2, "slides_c", _deck("c"), commit="c2")
        assert doc_ledger.save(run2, path) is True

        _record(run1, "slides_a", _deck("a", de_text="DE Text NEU"), commit="c3")
        assert doc_ledger.save(run1, path) is True

        final = doc_ledger.load(path)
        assert set(final.decks) == {"slides_a", "slides_b", "slides_c"}, (
            "run 2's new section was reverted by run 1's whole-file write"
        )

    def test_a_siblings_edit_to_an_existing_section_survives(self, tmp_path: Path):
        """Sharper: both runs hold ``slides_b``, and only run 2 changed it.

        Run 1's in-memory ``slides_b`` is not empty — it is *stale*. A merge that
        simply unioned the deck keys would still write the stale copy over run 2's
        edit, so this pins that "untouched by us" is decided by comparison against
        what we loaded, not by key presence.
        """
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)

        run1 = doc_ledger.load(path)
        run2 = doc_ledger.load(path)

        _record(run2, "slides_b", _deck("b", de_text="DE B NEU"), commit="c2")
        doc_ledger.save(run2, path)
        b_after_run2 = _body(path, "slides_b", "id:b-m")

        _record(run1, "slides_a", _deck("a", de_text="DE A NEU"), commit="c3")
        doc_ledger.save(run1, path)

        assert _body(path, "slides_b", "id:b-m") == b_after_run2, (
            "run 1 wrote its stale copy of slides_b over run 2's edit"
        )
        # ...and run 1's own work landed rather than being dropped by the merge.
        assert doc_ledger.load(path).decks["slides_a"].members["id:a-m"].confirmed_commit == "c3"

    def test_our_own_edit_wins_over_the_disk_copy(self, tmp_path: Path):
        """The merge must not become "disk always wins" — that loses our work instead."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)
        run1 = doc_ledger.load(path)
        _record(run1, "slides_a", _deck("a", de_text="DE Text NEU"), commit="c3")
        doc_ledger.save(run1, path)

        members = doc_ledger.load(path).decks["slides_a"].members
        assert members["id:a-m"].confirmed_commit == "c3"

    def test_same_deck_concurrency_warns_and_takes_the_later_writer(self, tmp_path: Path, caplog):
        """Not solvable by merging — but it must not be silent."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)

        run1 = doc_ledger.load(path)
        run2 = doc_ledger.load(path)
        _record(run2, "slides_a", _deck("a", de_text="RUN TWO"), commit="c2")
        doc_ledger.save(run2, path)

        _record(run1, "slides_a", _deck("a", de_text="RUN ONE"), commit="c3")
        with caplog.at_level(logging.WARNING, logger="clm.slides.doc_ledger"):
            doc_ledger.save(run1, path)

        assert any("changed by this run AND by another writer" in r.message for r in caplog.records)
        assert doc_ledger.load(path).decks["slides_a"].members["id:a-m"].confirmed_commit == "c3"

    def test_a_never_loaded_ledger_writes_all_of_its_sections(self, tmp_path: Path):
        """No load snapshot means every section is this run's work — not "unchanged"."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        ledger = doc_ledger.TopicLedger()
        _record(ledger, "slides_a", _deck("a"), commit="c1")
        assert doc_ledger.save(ledger, path) is True
        assert set(doc_ledger.load(path).decks) == {"slides_a"}

    def test_a_byte_identical_save_is_still_skipped(self, tmp_path: Path):
        """#555 must survive the merge: a clean sweep leaves ``git status`` clean."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)
        reloaded = doc_ledger.load(path)
        assert doc_ledger.save(reloaded, path) is False

    def test_merging_does_not_resurrect_a_members_removal(self, tmp_path: Path):
        """Removing a member from OUR deck must still persist through the merge."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)
        run1 = doc_ledger.load(path)
        # Re-record slides_a from a deck that no longer has the localized member.
        de = _build(HEADER_DE, _slide("a", "de", "Titel"))
        en = _build(HEADER_EN, _slide("a", "en", "Title"))
        outcome = parse_bundle(de, en)
        assert outcome.deck is not None
        _record(run1, "slides_a", outcome.deck, commit="c3")
        doc_ledger.save(run1, path)

        assert "id:a-m" not in doc_ledger.load(path).decks["slides_a"].members


class TestUnreadableDiskIsFailSafe:
    """The merge's most load-bearing clause: an unparseable file must not empty the store.

    ``_from_bytes`` degrades anything it cannot read to an *empty* ledger, so the
    "we did not change this section, keep disk" branch is guarded by
    ``key in on_disk.decks``. Without that guard a run which modified **nothing**
    would write an empty ledger over a healthy one — silent total loss of the
    trust store, from a no-op.
    """

    def test_a_corrupt_file_does_not_wipe_sections_we_hold(self, tmp_path: Path):
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)
        ledger = doc_ledger.load(path)
        assert set(ledger.decks) == {"slides_a", "slides_b"}

        path.write_text("{ this is not json", encoding="utf-8")
        doc_ledger.save(ledger, path)

        assert set(doc_ledger.load(path).decks) == {"slides_a", "slides_b"}

    def test_a_truncated_file_does_not_wipe_sections_we_hold(self, tmp_path: Path):
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)
        ledger = doc_ledger.load(path)

        path.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])
        doc_ledger.save(ledger, path)

        assert set(doc_ledger.load(path).decks) == {"slides_a", "slides_b"}

    def test_a_newer_schema_on_disk_does_not_wipe_sections_we_hold(self, tmp_path: Path):
        """The realistic trigger: an older clm in a worktree beside a newer one.

        A future ``SCHEMA_VERSION`` reads as unknown, so ``_from_bytes`` returns
        empty — exactly the shape that turns a merge into a wipe.
        """
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)
        ledger = doc_ledger.load(path)

        path.write_text(
            json.dumps({"schema": doc_ledger.SCHEMA_VERSION + 99, "decks": {}}),
            encoding="utf-8",
        )
        doc_ledger.save(ledger, path)

        assert set(doc_ledger.load(path).decks) == {"slides_a", "slides_b"}


class TestRepeatedSaves:
    """``save`` must leave the file matching the ledger, however many times it runs."""

    def test_a_revert_saved_after_an_edit_actually_lands(self, tmp_path: Path):
        """Without refreshing the load snapshot, the second save adopts our own
        earlier write and reports ``False`` while the file keeps the stale value."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)
        ledger = doc_ledger.load(path)

        _record(ledger, "slides_a", _deck("a", de_text="EDITED"), commit="c1")
        assert doc_ledger.save(ledger, path) is True
        edited = _body(path, "slides_a", "id:a-m")

        _record(ledger, "slides_a", _deck("a"), commit="c2")
        assert doc_ledger.save(ledger, path) is True, "the revert was reported as a no-op"
        assert _body(path, "slides_a", "id:a-m") != edited, "the revert never reached disk"

    def test_a_second_save_does_not_warn_about_our_own_first_write(self, tmp_path: Path, caplog):
        """A false concurrency warning tells operators to re-plan their parallelism."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        _seed(path)
        ledger = doc_ledger.load(path)

        _record(ledger, "slides_a", _deck("a", de_text="ONE"), commit="c1")
        doc_ledger.save(ledger, path)
        _record(ledger, "slides_a", _deck("a", de_text="TWO"), commit="c2")
        with caplog.at_level(logging.WARNING, logger="clm.slides.doc_ledger"):
            doc_ledger.save(ledger, path)

        assert not [r for r in caplog.records if "another writer" in r.message]


class TestProvenanceChurn:
    """M13: swapping between automatic provenances says nothing and must not rewrite."""

    def test_record_then_apply_leaves_an_unchanged_member_alone(self, tmp_path: Path):
        """The loop that produced 883-line diffs for 60 changed cells."""
        ledger = doc_ledger.TopicLedger()
        _record(ledger, "slides_a", _deck("a"), provenance="record", commit="c1")
        before = dict(ledger.decks["slides_a"].members)

        _record(ledger, "slides_a", _deck("a"), provenance="apply", commit="c2")
        after = ledger.decks["slides_a"].members

        assert after == before
        assert {lm.provenance for lm in after.values()} == {"record"}
        assert {lm.confirmed_commit for lm in after.values()} == {"c1"}

    def test_the_loop_is_write_free_on_disk_too(self, tmp_path: Path):
        """The churn that mattered was in `git diff`, so pin it at the file level."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        ledger = doc_ledger.TopicLedger()
        _record(ledger, "slides_a", _deck("a"), provenance="record", commit="c1")
        assert doc_ledger.save(ledger, path) is True

        reloaded = doc_ledger.load(path)
        _record(reloaded, "slides_a", _deck("a"), provenance="apply", commit="c2")
        assert doc_ledger.save(reloaded, path) is False, "an apply pass rewrote an unchanged deck"

    def test_any_undeclared_stamp_is_automatic_whatever_the_string(self):
        """Intent comes from the caller, never from the value.

        An earlier draft enumerated "automatic" provenance strings. That was wrong
        twice: it named a value the engine does not write, and it could not tell a
        defaulted ``record`` from a typed one. Nothing about the string decides.
        """
        for stamp in ("apply", "harvest:abc123", "some-future-verb"):
            ledger = doc_ledger.TopicLedger()
            _record(ledger, "slides_a", _deck("a"), provenance="record", commit="c1")
            _record(ledger, "slides_a", _deck("a"), provenance=stamp, commit="c2")
            members = ledger.decks["slides_a"].members
            assert {lm.provenance for lm in members.values()} == {"record"}, stamp

    def test_an_explicit_agent_stamp_still_records_fresh(self):
        """`--provenance agent` is asked for, so it must land."""
        ledger = doc_ledger.TopicLedger()
        _record(ledger, "slides_a", _deck("a"), provenance="record", commit="c1")
        _record(ledger, "slides_a", _deck("a"), provenance="agent", commit="c2", deliberate=True)
        members = ledger.decks["slides_a"].members
        assert {lm.provenance for lm in members.values()} == {"agent"}
        assert {lm.confirmed_commit for lm in members.values()} == {"c2"}

    def test_a_semantic_stamp_records_fresh(self):
        ledger = doc_ledger.TopicLedger()
        _record(ledger, "slides_a", _deck("a"), provenance="record", commit="c1")
        _record(
            ledger, "slides_a", _deck("a"), provenance="semantic:gpt", commit="c2", deliberate=True
        )
        assert {lm.provenance for lm in ledger.decks["slides_a"].members.values()} == {
            "semantic:gpt"
        }

    def test_an_automatic_pass_does_not_demote_a_deliberate_stamp(self):
        """The other half of the ping-pong.

        With the content identical there is nothing for ``apply`` to re-establish,
        so the more informative label is the one worth keeping. Keying the rule on
        the *incoming* stamp rather than on both is what makes this hold.
        """
        ledger = doc_ledger.TopicLedger()
        _record(ledger, "slides_a", _deck("a"), provenance="agent", commit="c1", deliberate=True)
        _record(ledger, "slides_a", _deck("a"), provenance="apply", commit="c2")
        members = ledger.decks["slides_a"].members
        assert {lm.provenance for lm in members.values()} == {"agent"}
        assert {lm.confirmed_commit for lm in members.values()} == {"c1"}

    def test_a_content_change_still_records_fresh_under_any_provenance(self):
        """Provenance-insensitivity must not leak into content-insensitivity."""
        ledger = doc_ledger.TopicLedger()
        _record(ledger, "slides_a", _deck("a"), provenance="record", commit="c1")
        _record(
            ledger,
            "slides_a",
            _deck("a", de_text="DE Text NEU"),
            provenance="apply",
            commit="c2",
        )
        members = ledger.decks["slides_a"].members
        assert members["id:a-m"].provenance == "apply"
        assert members["id:a-m"].confirmed_commit == "c2"
        # The untouched sibling keeps everything.
        assert members["id:a"].provenance == "record"
        assert members["id:a"].confirmed_commit == "c1"


class TestTypedProvenanceReachesTheLedger:
    """``--provenance record`` typed by hand must reset a stale semantic stamp.

    The trap the string-enumeration design walked into: ``record`` is both the
    option's default *and* a value a human types to re-verify a member whose
    ``semantic:<model>`` attribution they no longer trust. Preserving on the value
    swallowed the reset while the verb still reported the member as recorded —
    defeating the stated purpose of the field (``LedgerMember``: "kept so a later
    run can selectively distrust a source"). Intent has to come from the CLI.
    """

    def _write_pair(self, folder: Path) -> Path:
        de = folder / "slides_t.de.py"
        en = folder / "slides_t.en.py"
        de.write_text(
            _build(HEADER_DE, _slide("t", "de", "Titel"), _localized("t-m", "de", "DE Text")),
            encoding="utf-8",
        )
        en.write_text(
            _build(HEADER_EN, _slide("t", "en", "Title"), _localized("t-m", "en", "EN text")),
            encoding="utf-8",
        )
        return de

    def _provenances(self, de: Path) -> set[str]:
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(de))
        return {lm.provenance for lm in ledger.decks["slides_t"].members.values()}

    def _run(self, de: Path, *args: str) -> None:
        from click.testing import CliRunner

        from clm.cli.commands.slides.sync import slides_sync_group

        try:
            runner = CliRunner(mix_stderr=False)
        except TypeError:  # Click 8.2+ dropped the parameter
            runner = CliRunner()
        result = runner.invoke(slides_sync_group, ["record", str(de), *args])
        assert result.exit_code == 0, result.output

    def test_a_typed_record_resets_a_semantic_stamp(self, tmp_path: Path):
        de = self._write_pair(tmp_path)
        self._run(de, "--provenance", "semantic:gpt-4")
        assert self._provenances(de) == {"semantic:gpt-4"}

        self._run(de, "--provenance", "record")
        assert self._provenances(de) == {"record"}, (
            "a hand-typed --provenance record was swallowed, so the human's "
            "re-verification never reached the ledger"
        )

    def test_the_default_does_not_reset_a_semantic_stamp(self, tmp_path: Path):
        """The other side of the same coin — this is what kills M13's churn."""
        de = self._write_pair(tmp_path)
        self._run(de, "--provenance", "semantic:gpt-4")
        self._run(de)
        assert self._provenances(de) == {"semantic:gpt-4"}


class TestAtomicWriteAlreadyUsesUniqueTempNames:
    """Q7 asked for UUID temp names; they were already there. Check, don't assume."""

    def test_the_temp_name_is_unique_per_write(self, tmp_path: Path, monkeypatch):
        from clm.infrastructure.utils import path_utils

        seen: list[str] = []
        real = Path.write_bytes

        def spy(self: Path, data: bytes) -> int:
            seen.append(self.name)
            return real(self, data)

        monkeypatch.setattr(Path, "write_bytes", spy)
        target = tmp_path / "ledger.json"
        path_utils.atomic_write_bytes(target, b"one")
        path_utils.atomic_write_bytes(target, b"two")

        temps = [n for n in seen if n.endswith(".tmp")]
        assert len(temps) == 2
        assert temps[0] != temps[1], "a fixed temp name collides between concurrent writers"
        assert all(n.startswith("ledger.json.") for n in temps)


def test_load_snapshot_does_not_affect_ledger_equality(tmp_path: Path):
    """``load_snapshot`` is bookkeeping about the read, not part of the recorded state.

    Two ledgers holding the same decks are the same ledger however each was
    obtained — otherwise every existing ``==`` comparison would start depending on
    whether an object came off disk.
    """
    path = tmp_path / ".clm" / "sync-ledger.json"
    _seed(path)
    from_disk = doc_ledger.load(path)

    rebuilt = doc_ledger.TopicLedger()
    _record(rebuilt, "slides_a", _deck("a"), commit="c0")
    _record(rebuilt, "slides_b", _deck("b"), commit="c0")

    assert from_disk.load_snapshot and not rebuilt.load_snapshot
    assert from_disk == rebuilt
    assert "load_snapshot" not in repr(from_disk)


def test_the_ledger_on_disk_stays_canonical_json(tmp_path: Path):
    """Merging must not disturb the sorted/indented form the diffs depend on."""
    path = tmp_path / ".clm" / "sync-ledger.json"
    _seed(path)
    run1 = doc_ledger.load(path)
    _record(run1, "slides_c", _deck("c"), commit="c3")
    doc_ledger.save(run1, path)

    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw == json.dumps(json.loads(raw), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
