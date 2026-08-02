"""`record_neutral` — the engine does not ask what it can observe (#764, §6.2.1).

A cold member is framed as a question only when the relationship between its
halves is genuinely unknown. When the member is two-sided, declared `shared`, of
a kind that carries no natural language, and its halves agree on every field the
differ compares, there is no translation divergence to verify — the question has
one possible answer, and asking it is ceremony. Measured on the 730-deck
PythonCourses corpus: **45.4% of the 28,791 cold-start items** are that class.

The predicate is four clauses (§6.2.1 numbers them 2–5; clause 1, "no ledger
entry", is the branch being replaced). Each is pinned here as *independently
necessary* where it is: clauses 3, 4 and 5 each reject a member the others
accept. Clause 2 is defence in depth — clause 5 already fails when a side is
absent — and is kept because it states the intent at the point of the risk.
Clause 4 is the load-bearing one — `markdown` is excluded because `shared` +
byte-identical cannot be told apart from German prose duplicated onto the EN
side, and auto-blessing that banks an untranslated cell as in-sync.

What this row must never do: write a file byte, claim trust it did not observe,
or overwrite an attestation a human made.
"""

from __future__ import annotations

import logging

import pytest

from clm.slides import doc_apply, sync_diff
from clm.slides import doc_ledger as doc_ledger_module
from clm.slides.bilingual_doc import BilingualDeck
from clm.slides.doc_lenses import parse_bundle
from clm.slides.sync_diff import (
    COMPARED_SIDECELL_FIELDS,
    NEUTRAL_KINDS,
    DeckBaseline,
    DeckDiff,
    diff_outcome,
    is_neutral_pair,
)

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


def _deck(de_cell: str, en_cell: str) -> BilingualDeck:
    """A one-slide deck whose only pool member is the cell under test."""
    outcome = parse_bundle(
        _build(HEADER_DE, _slide("s0", "de", "Titel"), de_cell),
        _build(HEADER_EN, _slide("s0", "en", "Title"), en_cell),
    )
    assert outcome.deck is not None, outcome.refusal.render() if outcome.refusal else "parse failed"
    return outcome.deck


def _cold_diff(de_cell: str, en_cell: str) -> DeckDiff:
    """Diff against an empty *ledger* baseline — the production cold shape."""
    return diff_outcome(
        parse_bundle(
            _build(HEADER_DE, _slide("s0", "de", "Titel"), de_cell),
            _build(HEADER_EN, _slide("s0", "en", "Title"), en_cell),
        ),
        DeckBaseline(complete=False),
    )


def _action_for(diff: DeckDiff, prefix: str) -> str:
    rows = [i for i in diff.items if i.key.startswith(prefix)]
    assert len(rows) == 1, [(i.key, i.action) for i in diff.items]
    return rows[0].action


#: The cell that satisfies every clause — the control for each mutation below.
NEUTRAL_CODE = '# %% tags=["keep"]\nx = 1\n\n'


class TestEveryClauseIsNecessary:
    def test_the_control_is_decidable(self):
        """Without this, a mutation "failing" proves nothing."""
        assert _action_for(_cold_diff(NEUTRAL_CODE, NEUTRAL_CODE), "pos:s0/") == "record_neutral"

    def test_clause_2_one_sided_stays_cold(self):
        """Only one half present: there is no second half to compare against."""
        diff = _cold_diff(NEUTRAL_CODE, "")
        assert _action_for(diff, "pos:s0/") == "verify_cold"

    def test_clause_3_a_localized_cell_copied_verbatim_into_the_twin_stays_cold(self):
        """The shape clause 3 is *alone* in rejecting — and the worst false positive.

        Both halves carry ``lang="de"`` and identical bytes: a German cell sitting
        untranslated in the English deck. Clause 5 holds (the halves really are
        the same bytes) and clause 4 holds (it is code), so if clause 3 were
        dropped the engine would bank an untranslated cell as verified.

        An earlier version of this test used ``lang="de"`` / ``lang="en"``, which
        made the halves differ — so clause 5 rejected them and the test passed
        with clause 3 deleted. The cell needs a slide_id either way: the lens
        refuses an id-LESS localized cell outright (``idless_localized``).
        """
        cell = '# %% lang="de" tags=["keep"] slide_id="c1"\n# Berechne die Summe\nx = 1 + 1\n\n'
        member = next(m for m in _deck(cell, cell).members() if m.key.render() == "id:c1")
        assert member.langness == "localized"
        assert member.kind in NEUTRAL_KINDS, "clause 4 must not be what rejects this"
        assert sync_diff._halves_observably_identical(member), "clause 5 must hold"
        assert not is_neutral_pair(member), "only clause 3 stands between this and a bank"

        assert _action_for(_cold_diff(cell, cell), "id:c1") == "verify_cold"

    def test_clause_4_markdown_stays_cold_even_when_identical(self):
        """The prose exclusion — the maintainer's explicit decision.

        `shared` + byte-identical markdown has two readings the engine cannot
        distinguish: a genuinely neutral cell (a fenced code block, an `<img>`),
        or German prose duplicated onto the EN side and mis-declared neutral.
        Auto-blessing the second banks an untranslated cell as in-sync.
        """
        cell = "# %% [markdown]\n#\n# Ein Absatz, der auf beiden Seiten steht.\n\n"
        diff = _cold_diff(cell, cell)
        assert _action_for(diff, "pos:s0/") == "verify_cold"

    def test_clause_5_diverged_bodies_stay_cold(self):
        diff = _cold_diff(NEUTRAL_CODE, '# %% tags=["keep"]\nx = 2\n\n')
        assert _action_for(diff, "pos:s0/") == "verify_cold"

    def test_clause_5_is_not_body_only__a_tags_divergence_must_not_be_swallowed(self):
        """Same body, different tags. Explicitly called out in #764's scope.

        A body-only comparison would call this pair identical and bank it — and
        the tag sets would stay divergent with the ledger asserting they are
        fine. This is also the shape that made the corpus estimate one member
        too high: a j2 cell whose *body* is empty on both halves but whose
        header line reads `header_de` on one side and `header_en` on the other.
        """
        diff = _cold_diff(NEUTRAL_CODE, '# %% tags=["keep", "extra"]\nx = 1\n\n')
        assert _action_for(diff, "pos:s0/") == "verify_cold"

    def test_clause_5_walks_the_field_set__not_a_hand_written_list(self):
        """The genericity requirement (P6), exercised rather than asserted in prose.

        Clause 5 is defined over ``COMPARED_SIDECELL_FIELDS`` so that a field
        added to the differ's comparison later tightens this predicate
        automatically. Extend the set with a field the halves happen to disagree
        on, and the same member must stop being decidable.
        """
        # Proved by NARROWING, which needs no field that happens to differ: take a
        # member clause 5 currently rejects, empty the set, and it must become
        # decidable. A hand-written comparison (`de.lines == en.lines and ...`)
        # would be unmoved by that — and would be equally unmoved by a field
        # ADDED to the differ later, which is the drift this guards against.
        deck = _deck(NEUTRAL_CODE, '# %% tags=["keep"]\nx = 2\n\n')
        member = next(m for m in deck.members() if m.key.render().startswith("pos:s0/"))
        assert not is_neutral_pair(member), "control: diverged halves are not decidable"

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sync_diff, "COMPARED_SIDECELL_FIELDS", frozenset())
            assert is_neutral_pair(member), (
                "clause 5 ignored the field set — it is hand-written, so a field "
                "added to the differ later will not tighten it"
            )


class TestWhatItRecords:
    def _apply(self, tmp_path, de_cell: str, en_cell: str):
        from click.testing import CliRunner

        from clm.cli.commands.slides.sync import slides_sync_group
        from clm.slides import doc_ledger

        de = tmp_path / "slides_t.de.py"
        en = tmp_path / "slides_t.en.py"
        de.write_text(_build(HEADER_DE, _slide("s0", "de", "Titel"), de_cell), encoding="utf-8")
        en.write_text(_build(HEADER_EN, _slide("s0", "en", "Title"), en_cell), encoding="utf-8")
        before = (de.read_text(encoding="utf-8"), en.read_text(encoding="utf-8"))
        try:
            runner = CliRunner(mix_stderr=False)
        except TypeError:  # Click 8.2+
            runner = CliRunner()
        runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        after = (de.read_text(encoding="utf-8"), en.read_text(encoding="utf-8"))
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(de))
        return before, after, ledger.decks.get("slides_t"), de, runner

    def test_it_writes_the_ledger_and_not_one_file_byte(self, tmp_path):
        before, after, deck_ledger, _de, _r = self._apply(tmp_path, NEUTRAL_CODE, NEUTRAL_CODE)
        assert after == before, "a ledger-only row must not touch the files"
        assert deck_ledger is not None
        pos = {k: lm for k, lm in deck_ledger.members.items() if k.startswith("pos:s0/")}
        assert pos, deck_ledger.members

    def test_the_stamp_is_structural_not_the_pass_provenance(self, tmp_path):
        """`apply` stamps `apply`; this row was established by the engine, not the verb."""
        _b, _a, deck_ledger, _de, _r = self._apply(tmp_path, NEUTRAL_CODE, NEUTRAL_CODE)
        assert deck_ledger is not None
        stamps = {lm.provenance for k, lm in deck_ledger.members.items() if k.startswith("pos:s0/")}
        assert stamps == {"structural"}

    def test_every_member_of_a_multi_member_pool_keeps_its_stamp(self, tmp_path):
        """A `pos:` record re-records its WHOLE pool, so per-item stamping is unsafe.

        With N neutral members in one pool, each item's `rerecord_pool` reset the
        stamp the previous one had just written: 65% of positional neutral
        members corpus-wide ended up with the pass provenance and a re-created
        dangling owner. Every fixture used a single-member pool, so it shipped
        green. Stamping happens once, after every record has landed.
        """
        cells = "".join(f'# %% tags=["keep"]\n{n} = 1\n\n' for n in "xyz")
        _b, _a, deck_ledger, _de, _r = self._apply(tmp_path, cells, cells)
        assert deck_ledger is not None
        pool = {k: lm for k, lm in deck_ledger.members.items() if k.startswith("pos:s0/")}
        assert len(pool) == 3, pool
        assert {lm.provenance for lm in pool.values()} == {"structural"}
        assert {lm.entry.owner for lm in pool.values()} == {None}

    def test_a_cold_apply_logs_no_dangling_reference_warning(self, tmp_path, caplog):
        """What the owner-drop actually exists for, asserted directly.

        The previous test read the ledger back from *disk*, where
        `prune_dangling_refs` had already degraded any dangling owner — so it
        passed with the owner-drop deleted. The point of dropping at write time
        is that #718's corruption detector must not fire on every cold apply.
        """
        cells = "".join(f'# %% tags=["keep"]\n{n} = 1\n\n' for n in "xyz")
        with caplog.at_level(logging.WARNING, logger="clm.slides.doc_ledger"):
            self._apply(tmp_path, cells, cells)
        assert not [r for r in caplog.records if "dangling reference" in r.message], [
            r.message for r in caplog.records
        ]

    def test_it_records_no_owner_it_cannot_back(self, tmp_path):
        """The anchor is still cold on a cold deck, so its id would dangle (#718).

        `save` prunes exactly that and warns that nothing should create it —
        creating it on every cold apply would turn a corruption detector into
        noise. Ownership records when the anchor itself lands.
        """
        _b, _a, deck_ledger, _de, _r = self._apply(tmp_path, NEUTRAL_CODE, NEUTRAL_CODE)
        assert deck_ledger is not None
        for key, lm in deck_ledger.members.items():
            assert lm.entry.owner in (None, *deck_ledger.members), key

    def test_the_deck_converges_and_ownership_settles_on_a_full_record(self, tmp_path):
        from clm.cli.commands.slides.sync import slides_sync_group
        from clm.slides import doc_ledger

        _b, _a, _dl, de, runner = self._apply(tmp_path, NEUTRAL_CODE, NEUTRAL_CODE)
        runner.invoke(slides_sync_group, ["record", str(de)])
        result = runner.invoke(slides_sync_group, ["report", str(de)])
        assert "clean" in result.output, result.output
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(de))
        members = ledger.decks["slides_t"].members
        pos = next(k for k in members if k.startswith("pos:s0/"))
        assert members[pos].entry.owner == "id:s0"


class TestEveryEmissionSiteFires:
    """Three code paths classify a member with no base entry. All three branch.

    Site 1 is `base is None` (snapshot-cold), site 3 is `_classify_pool_news`
    (positional, ledger mode) — both are exercised throughout this file. Site 2,
    the **id-keyed** ledger-mode path, had no coverage at all: disabling it
    passed the entire 9,399-test suite, while the corpus says ~16% of neutral
    members are `id:`-keyed.
    """

    def test_an_id_keyed_member_added_to_a_recorded_deck(self, tmp_path):
        from click.testing import CliRunner

        from clm.cli.commands.slides.sync import slides_sync_group
        from clm.slides import doc_ledger

        de = tmp_path / "slides_t.de.py"
        en = tmp_path / "slides_t.en.py"
        de.write_text(_build(HEADER_DE, _slide("s0", "de", "Titel")), encoding="utf-8")
        en.write_text(_build(HEADER_EN, _slide("s0", "en", "Title")), encoding="utf-8")
        try:
            runner = CliRunner(mix_stderr=False)
        except TypeError:  # Click 8.2+
            runner = CliRunner()
        assert runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0

        # An id'd, two-sided, byte-identical shared code cell — added on BOTH
        # halves of an already-recorded deck, so it reaches the id-keyed branch.
        idd = '# %% tags=["keep"] slide_id="c1"\nx = 1\n\n'
        de.write_text(_build(HEADER_DE, _slide("s0", "de", "Titel"), idd), encoding="utf-8")
        en.write_text(_build(HEADER_EN, _slide("s0", "en", "Title"), idd), encoding="utf-8")

        diff = diff_outcome(
            parse_bundle(de.read_text(encoding="utf-8"), en.read_text(encoding="utf-8")),
            doc_ledger.baseline_from_ledger(
                doc_ledger.load(doc_ledger.ledger_path_for(de)).decks["slides_t"]
            ),
        )
        assert _action_for(diff, "id:c1") == "record_neutral"


class TestFrozenPoolsAreHonoured:
    """#600/#630: a pool holding an unresolved conflict must not be re-recorded.

    `record_neutral` re-records its whole pool, so it inherits that hazard: the
    two-sided base entry is the only record the gone side ever existed, and a
    wholesale re-record from present state would erase it — silently downgrading
    a pending removal conflict to mechanical duplication on the next report.

    The guard was correct as written but unpinned: deleting it passed the whole
    suite. Exercised directly, since building a real frozen pool alongside a
    neutral member takes a five-step fixture that would obscure what is asserted.
    """

    def test_a_frozen_pool_records_nothing(self):
        from clm.slides.doc_ledger import DeckLedger, record_deck_snapshot

        deck = _deck(NEUTRAL_CODE, NEUTRAL_CODE)
        item = next(
            i for i in _cold_diff(NEUTRAL_CODE, NEUTRAL_CODE).items if i.action == "record_neutral"
        )
        pool = doc_apply._pool_scope(item)
        assert pool is not None, item.key

        fresh_ledger = doc_ledger_module.TopicLedger()
        record_deck_snapshot(fresh_ledger, "d", deck, provenance="apply")
        fresh = fresh_ledger.decks["d"]

        target = DeckLedger()
        assert (
            doc_apply._record_item(target, fresh, item, provenance="apply", frozen_pools={pool})
            == set()
        )
        assert target.members == {}, "a frozen pool must not be recorded"

        target2 = DeckLedger()
        assert doc_apply._record_item(
            target2, fresh, item, provenance="apply", frozen_pools=set()
        ) == {pool}
        assert target2.members, "control: an unfrozen pool records"


class TestItStaysInsideTheContract:
    def test_the_row_is_mechanical_and_answerless(self):
        item = next(
            i for i in _cold_diff(NEUTRAL_CODE, NEUTRAL_CODE).items if i.action == "record_neutral"
        )
        assert item.outcome == "mechanical"
        assert item.action in sync_diff.MECHANICAL_ACTIONS
        assert item.action not in sync_diff.FRAMED_ACTIONS
        assert doc_apply.item_answers(item) == ()
        assert doc_apply.item_resolution(item) == "mechanical"

    def test_it_is_a_ledger_only_row(self):
        assert "record_neutral" in doc_apply._RECORD_ONLY

    def test_the_all_cold_seeding_hint_survives(self):
        """Mechanical rows say nothing about the remaining questions.

        Keying the hint on *all* items would have silently withdrawn it from
        exactly the freshly-authored decks it exists for, since those now emit
        `record_neutral` rows alongside the cold ones.
        """
        from clm.slides.doc_report import cold_sweep_hint

        diff = _cold_diff(NEUTRAL_CODE, NEUTRAL_CODE)
        assert any(i.action == "record_neutral" for i in diff.items)
        assert any(i.action == "verify_cold" for i in diff.items)
        hint = cold_sweep_hint(diff)
        assert hint is not None and "sync record" in hint

    def test_a_structural_stamp_never_demotes_a_human_attestation(self):
        """`preserve_unchanged_member`'s automatic path, from this row's angle.

        An engine observation must not overwrite `semantic:<model>` on a member
        whose content has not moved — a later "distrust that model" sweep would
        then discard trust a human established.
        """
        from clm.slides.doc_identity import baseline_from_deck
        from clm.slides.doc_ledger import LedgerMember, preserve_unchanged_member

        entry = next(
            e
            for k, e in baseline_from_deck(_deck(NEUTRAL_CODE, NEUTRAL_CODE)).members.items()
            if k.startswith("pos:s0/")
        )
        human = LedgerMember(entry=entry, provenance="semantic:gpt-4", confirmed_commit="c1")
        engine = LedgerMember(entry=entry, provenance="structural", confirmed_commit="c2")
        assert preserve_unchanged_member(human, engine) is human


def test_neutral_kinds_excludes_markdown():
    """A one-line guard on the decision most likely to be "simplified" later."""
    assert NEUTRAL_KINDS == {"code", "j2"}
    assert "markdown" not in NEUTRAL_KINDS
