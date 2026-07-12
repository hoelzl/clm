"""Tests for :mod:`clm.slides.doc_apply` (#520 Phase 3, design §6.2/§8).

Each test follows the dogfood loop the executor exists for: write a bundle,
``record`` it into the ledger, apply ONE authoring action, diff against the
ledger, apply — then assert the twin landed byte-correctly AND a fresh diff
against the updated ledger is clean (the convergence contract). Decision
handling asserts the re-homed accept guards: per-item rejection with reasons,
multi-cell smuggling refused, stale handles refused, valid answers land.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attrs import evolve

from clm.slides import doc_apply, doc_ledger, sync_diff
from clm.slides.bilingual_doc import SideCell
from clm.slides.doc_lenses import LoadedBundle, load_bundle
from clm.slides.sync_diff import DeckDiff, DiffItem, diff_outcome

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _shared_code(name: str, value: int = 1, tags: str = "keep") -> str:
    return f'# %% tags=["{tags}"]\n{name} = {value}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


DE_PARTS = (
    HEADER_DE,
    _slide("s0", "de", "Titel"),
    _shared_code("x"),
    _shared_code("y", 2),
    _localized("s0-m", "de", "DE Text"),
)
EN_PARTS = (
    HEADER_EN,
    _slide("s0", "en", "Title"),
    _shared_code("x"),
    _shared_code("y", 2),
    _localized("s0-m", "en", "EN text"),
)


class _Deck:
    """One on-disk bundle plus its recorded ledger — the loop harness."""

    def __init__(self, tmp_path: Path, de: str, en: str) -> None:
        self.de_path = tmp_path / "slides_t.de.py"
        self.en_path = tmp_path / "slides_t.en.py"
        self.de_path.write_text(de, encoding="utf-8")
        self.en_path.write_text(en, encoding="utf-8")

    def record(self) -> None:
        bundle = self.load()
        assert bundle.outcome.deck is not None
        ledger_path = doc_ledger.ledger_path_for(self.de_path)
        ledger = doc_ledger.load(ledger_path)
        doc_ledger.record_deck_snapshot(
            ledger,
            doc_ledger.deck_key_for(self.de_path),
            bundle.outcome.deck,
            provenance="record",
        )
        doc_ledger.save(ledger, ledger_path)

    def load(self) -> LoadedBundle:
        return load_bundle(self.de_path, self.en_path)

    def diff(self) -> tuple[LoadedBundle, DeckDiff]:
        bundle = self.load()
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(self.de_path))
        deck_ledger = ledger.decks.get(doc_ledger.deck_key_for(self.de_path))
        base = doc_ledger.baseline_from_ledger(deck_ledger) if deck_ledger else None
        return bundle, diff_outcome(bundle.outcome, base)

    def apply(
        self,
        decisions: dict[str, doc_apply.Decision] | None = None,
        *,
        dry_run: bool = False,
        only_members: set[str] | None = None,
    ) -> doc_apply.ApplyOutcome:
        bundle, diff = self.diff()
        assert bundle.outcome.deck is not None
        ledger_path = doc_ledger.ledger_path_for(self.de_path)
        ledger = doc_ledger.load(ledger_path)
        outcome = doc_apply.apply_deck(
            bundle,
            bundle.outcome.deck,
            diff,
            ledger,
            doc_ledger.deck_key_for(self.de_path),
            decisions=decisions,
            only_members=only_members,
            dry_run=dry_run,
        )
        if outcome.error is None and not dry_run and outcome.ledger_changed:
            doc_ledger.save(ledger, ledger_path)
        return outcome

    def edit_de(self, old: str, new: str) -> None:
        text = self.de_path.read_text(encoding="utf-8")
        assert old in text
        self.de_path.write_text(text.replace(old, new), encoding="utf-8")

    def edit_en(self, old: str, new: str) -> None:
        text = self.en_path.read_text(encoding="utf-8")
        assert old in text
        self.en_path.write_text(text.replace(old, new), encoding="utf-8")

    def write_de(self, *parts: str) -> None:
        self.de_path.write_text(_build(*parts), encoding="utf-8")

    def write_en(self, *parts: str) -> None:
        self.en_path.write_text(_build(*parts), encoding="utf-8")

    def assert_converged(self) -> None:
        """The post-apply contract: a fresh diff vs the updated ledger is clean."""
        _, diff = self.diff()
        assert diff.is_clean, [(i.outcome, i.action, i.key, i.detail) for i in diff.items]


def _deck(tmp_path: Path) -> _Deck:
    deck = _Deck(tmp_path, _build(*DE_PARTS), _build(*EN_PARTS))
    deck.record()
    return deck


def _statuses(outcome: doc_apply.ApplyOutcome) -> dict[str, str]:
    return {r.key: r.status for r in outcome.results}


# ---------------------------------------------------------------------------
# Mechanical rows
# ---------------------------------------------------------------------------


class TestMechanicalRows:
    def test_propagate_shared_edit_copies_verbatim(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        assert "x = 42" in deck.en_path.read_text(encoding="utf-8")
        deck.assert_converged()

    def test_record_symmetric_edit_writes_no_files(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")
        deck.edit_en("x = 1", "x = 42")
        before = deck.de_path.read_text(encoding="utf-8")
        outcome = deck.apply()
        assert outcome.all_applied
        assert not outcome.wrote
        assert deck.de_path.read_text(encoding="utf-8") == before
        deck.assert_converged()

    def test_two_sided_brand_new_member_is_cold_in_ledger_mode(self, tmp_path: Path):
        # Design §5: a TWO-sided member with no ledger entry is UNVERIFIED, never
        # a mechanical copy — both sides are present, so the agent confirms (or
        # records) it explicitly rather than the engine trusting it silently.
        deck = _deck(tmp_path)
        new = _shared_code("z", 9)  # new, between x and y — added on BOTH sides
        deck.write_de(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            new,
            _shared_code("y", 2),
            _localized("s0-m", "de", "DE Text"),
        )
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            new,
            _shared_code("y", 2),
            _localized("s0-m", "en", "EN text"),
        )
        _, diff = deck.diff()
        assert [(i.action) for i in diff.items] == ["verify_cold"]
        outcome = deck.apply()
        assert not outcome.wrote
        assert outcome.count("pending") == 1

    def test_one_sided_new_idd_shared_cell_grows_the_twin_in_ledger_mode(self, tmp_path: Path):
        # issue #566: a ONE-sided new *id-keyed* shared cell in a ledgered deck
        # is copy_new_shared (mechanical verbatim copy to the twin), NOT a
        # verify_cold dead end — verify_cold's only answer, `confirm`, is
        # rejected for a one-sided member, so it could never be resolved. The
        # slide_id lets the executor place the twin (positional inserts alias).
        deck = _deck(tmp_path)
        idd = '# %% tags=["keep"] slide_id="z-cell"\nz = 9\n\n'
        deck.write_de(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            idd,  # new on DE only, between x and y
            _shared_code("y", 2),
            _localized("s0-m", "de", "DE Text"),
        )
        _, diff = deck.diff()
        assert [i.action for i in diff.items] == ["copy_new_shared"]
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        en = deck.en_path.read_text(encoding="utf-8")
        assert en.index("x = 1") < en.index("z = 9") < en.index("y = 2")
        assert 'slide_id="z-cell"' in en
        deck.assert_converged()

    def test_one_sided_new_localized_slide_translates_into_the_twin(self, tmp_path: Path):
        # issue #566 headline path: add a slide in one language, answer
        # translate_new with the target-language body, and the engine mints the
        # twin (with the shared slide_id) — no hand-authoring of both halves.
        deck = _deck(tmp_path)
        deck.write_de(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            _shared_code("y", 2),
            _localized("s0-n", "de", "Neu"),  # new localized slide, DE only
            _localized("s0-m", "de", "DE Text"),
        )
        _, diff = deck.diff()
        assert [(i.action, i.direction) for i in diff.items] == [("translate_new", "de_to_en")]
        item = diff.items[0]
        outcome = deck.apply(decisions={item.key: doc_apply.Decision(item.key, body="# New")})
        assert outcome.all_applied, outcome.to_payload()
        en = deck.en_path.read_text(encoding="utf-8")
        assert 'slide_id="s0-n"' in en
        assert "# New" in en
        assert en.index("# New") < en.index("# EN text")
        deck.assert_converged()

    def test_copy_new_shared_completes_a_recorded_pending_twin(self, tmp_path: Path):
        deck = _deck(tmp_path)
        # A recorded pending twin: the entry knows only the DE side of "y".
        ledger_path = doc_ledger.ledger_path_for(deck.de_path)
        ledger = doc_ledger.load(ledger_path)
        deck_ledger = ledger.decks["slides_t"]
        key = "pos:s0/code/1"
        lm = deck_ledger.members[key]
        deck_ledger.members[key] = evolve(
            lm,
            entry=evolve(lm.entry, en_fp=None, en_body_fp=None, en_tags=None, en_sig=None),
        )
        doc_ledger.save(ledger, ledger_path)
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            _localized("s0-m", "en", "EN text"),
        )
        _, diff = deck.diff()
        assert [i.action for i in diff.items] == ["copy_new_shared"]
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        en = deck.en_path.read_text(encoding="utf-8")
        assert en.index("x = 1") < en.index("y = 2") < en.index("# EN text")
        deck.assert_converged()

    def test_mirror_remove_deletes_the_twin(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.write_de(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            _localized("s0-m", "de", "DE Text"),
        )
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        assert "y = 2" not in deck.en_path.read_text(encoding="utf-8")
        deck.assert_converged()

    def test_mirror_tags_rewrites_only_the_header(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de(
            '# %% [markdown] lang="de" slide_id="s0-m"',
            '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-m"',
        )
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        en = deck.en_path.read_text(encoding="utf-8")
        assert 'tags=["notes"]' in en
        assert "# EN text" in en  # the body was untouched
        deck.assert_converged()

    def test_stamp_twin_id_completes_the_443_shape(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de('# %% tags=["keep"]\nx = 1', '# %% tags=["keep"] slide_id="x-cell"\nx = 1')
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        assert 'slide_id="x-cell"' in deck.en_path.read_text(encoding="utf-8")
        deck.assert_converged()

    def test_mirror_order_reorders_the_twin_pool(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.write_de(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("y", 2),  # swapped with x
            _shared_code("x"),
            _localized("s0-m", "de", "DE Text"),
        )
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        en = deck.en_path.read_text(encoding="utf-8")
        assert en.index("y = 2") < en.index("x = 1")
        deck.assert_converged()

    def test_propagate_preamble(self, tmp_path: Path):
        deck = _Deck(
            tmp_path,
            "# preamble v1\n" + _build(*DE_PARTS),
            "# preamble v1\n" + _build(*EN_PARTS),
        )
        deck.record()
        deck.edit_de("# preamble v1", "# preamble v2")
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        assert deck.en_path.read_text(encoding="utf-8").startswith("# preamble v2")
        deck.assert_converged()

    def test_group_rename_is_recorded_without_touching_files(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de('slide_id="s0"', 'slide_id="s0-neu"')
        deck.edit_en('slide_id="s0"', 'slide_id="s0-neu"')
        before = deck.de_path.read_text(encoding="utf-8")
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        assert not outcome.wrote
        assert deck.de_path.read_text(encoding="utf-8") == before
        deck.assert_converged()


# ---------------------------------------------------------------------------
# Separated voiceover companions (issue #570)
# ---------------------------------------------------------------------------


def _vo_cell(slug: str, for_slide: str, lang: str, text: str) -> str:
    return (
        f'# %% [markdown] lang="{lang}" tags=["voiceover"] '
        f'slide_id="{slug}" for_slide="{for_slide}"\n#\n# - {text}\n\n'
    )


class TestSeparatedVoiceoverCompanion:
    """The harvest → sync handoff: ``clm harvest accept`` writes a one-sided
    (DE-only) separated voiceover companion and records it, deferring the EN
    twin. The ordinary sync loop must then be able to *create* the missing EN
    companion — ``report`` frames each DE-only cell ``translate_new`` and
    ``apply`` with a target-language body mints the companion file. Before the
    fix the executor inverted the direction and rejected every member with
    ``the en source cell … is missing``.
    """

    def _companion_deck(self, tmp_path: Path) -> _Deck:
        """A split deck whose only voiceover lives in a DE-only companion in the
        ``voiceover/`` subdir — the state left by ``harvest accept`` DE-only."""
        de = _build(HEADER_DE, _slide("s0", "de", "Titel"))
        en = _build(HEADER_EN, _slide("s0", "en", "Title"))
        deck = _Deck(tmp_path, de, en)
        vo_dir = tmp_path / "voiceover"
        vo_dir.mkdir()
        (vo_dir / "voiceover_t.de.py").write_text(
            _build(_vo_cell("s0-vo", "s0", "de", "Hallo Welt")), encoding="utf-8"
        )
        deck.record()  # baseline knows the standing one-sided companion cell
        return deck

    def test_report_frames_de_only_companion_translate_new(self, tmp_path: Path):
        deck = self._companion_deck(tmp_path)
        _, diff = deck.diff()
        assert [(i.action, i.direction, i.key) for i in diff.items] == [
            ("translate_new", "de_to_en", "id:s0-vo")
        ]
        # ``side`` names the side that exists (the translate source), uniformly
        # across the translate_new emitters (the normalized convention).
        assert diff.items[0].side == "de"

    def test_apply_mints_the_missing_en_companion(self, tmp_path: Path):
        deck = self._companion_deck(tmp_path)
        _, diff = deck.diff()
        item = diff.items[0]
        outcome = deck.apply(
            decisions={item.key: doc_apply.Decision(item.key, body="# - Hello World")}
        )
        assert outcome.all_applied, outcome.to_payload()

        en_comp = tmp_path / "voiceover" / "voiceover_t.en.py"
        assert en_comp.exists(), "the EN companion file was not created"
        en_text = en_comp.read_text(encoding="utf-8")
        assert 'lang="en"' in en_text
        assert 'slide_id="s0-vo"' in en_text
        assert 'for_slide="s0"' in en_text
        assert "Hello World" in en_text
        # The EN *deck* is untouched — narration stays in the companion.
        assert "voiceover" not in deck.en_path.read_text(encoding="utf-8")
        deck.assert_converged()


# ---------------------------------------------------------------------------
# Per-item independence and safety
# ---------------------------------------------------------------------------


class TestPerItem:
    def test_framed_items_stay_pending_while_mechanical_ones_land(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")  # mechanical
        deck.edit_de("DE Text", "DE Text NEU")  # framed translate_edit
        outcome = deck.apply()
        statuses = _statuses(outcome)
        assert statuses["pos:s0/code/0"] == "applied"
        assert statuses["id:s0-m"] == "pending"
        assert not outcome.all_applied
        assert "x = 42" in deck.en_path.read_text(encoding="utf-8")
        # The framed item survives the partial apply.
        _, diff = deck.diff()
        assert [(i.key, i.action) for i in diff.items] == [("id:s0-m", "translate_edit")]

    def test_member_filter_skips_everything_else(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")
        deck.edit_de("y = 2", "y = 99")
        outcome = deck.apply(only_members={"pos:s0/code/0"})
        assert {r.status for r in outcome.results} == {"applied", "skipped"}
        en = deck.en_path.read_text(encoding="utf-8")
        assert "x = 42" in en
        assert "y = 99" not in en

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")
        before_en = deck.en_path.read_text(encoding="utf-8")
        ledger_before = doc_ledger.ledger_path_for(deck.de_path).read_text(encoding="utf-8")
        outcome = deck.apply(dry_run=True)
        assert outcome.dry_run and not outcome.wrote
        assert deck.en_path.read_text(encoding="utf-8") == before_en
        assert doc_ledger.ledger_path_for(deck.de_path).read_text(encoding="utf-8") == ledger_before

    def test_conflict_is_never_resolved_without_a_decision(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 111")
        deck.edit_en("x = 1", "x = 222")
        outcome = deck.apply()
        assert _statuses(outcome) == {"pos:s0/code/0": "pending"}
        assert "x = 111" in deck.de_path.read_text(encoding="utf-8")
        assert "x = 222" in deck.en_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def _decision(
    key: str,
    *,
    choice: str | None = None,
    body: str | None = None,
    side: str | None = None,
):
    return {key: doc_apply.Decision(key=key, choice=choice, body=body, side=side)}


class TestDecisions:
    def test_translate_edit_body_lands_on_the_twin(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("DE Text", "DE Text NEU")
        outcome = deck.apply(_decision("id:s0-m", body="# EN text NEW"))
        assert outcome.all_applied, outcome.to_payload()
        assert "# EN text NEW" in deck.en_path.read_text(encoding="utf-8")
        deck.assert_converged()

    def test_translate_edit_keep_twin_records_without_retyping(self, tmp_path: Path):
        # issue #566 (minor #1): a one-sided prose edit whose twin is still a
        # faithful rendering is accepted with keep_twin — the new baseline is
        # recorded and the twin kept verbatim, with no unchanged body re-typed.
        deck = _deck(tmp_path)
        deck.edit_de("DE Text", "DE Text (verfeinert)")
        _, diff = deck.diff()
        assert [(i.key, i.action) for i in diff.items] == [("id:s0-m", "translate_edit")]
        en_before = deck.en_path.read_text(encoding="utf-8")
        outcome = deck.apply(_decision("id:s0-m", choice="keep_twin"))
        assert outcome.all_applied, outcome.to_payload()
        assert not outcome.wrote  # the twin is untouched; only the ledger moves
        assert deck.en_path.read_text(encoding="utf-8") == en_before
        deck.assert_converged()

    def test_conflict_choice_propagates_the_chosen_side(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 111")
        deck.edit_en("x = 1", "x = 222")
        outcome = deck.apply(_decision("pos:s0/code/0", choice="de"))
        assert outcome.all_applied, outcome.to_payload()
        assert "x = 111" in deck.en_path.read_text(encoding="utf-8")
        assert "x = 222" not in deck.en_path.read_text(encoding="utf-8")
        deck.assert_converged()

    def test_smuggled_cell_boundary_is_rejected_byte_unchanged(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("DE Text", "DE Text NEU")
        before = deck.en_path.read_text(encoding="utf-8")
        outcome = deck.apply(
            _decision("id:s0-m", body='# fine\n\n# %% [markdown] slide_id="evil"\n# smuggled')
        )
        statuses = _statuses(outcome)
        assert statuses["id:s0-m"] == "rejected"
        result = next(r for r in outcome.results if r.key == "id:s0-m")
        assert "delimiter" in result.reason
        assert deck.en_path.read_text(encoding="utf-8") == before

    def test_stale_handle_is_rejected(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")
        outcome = deck.apply(_decision("id:no-such-member", choice="de"))
        statuses = _statuses(outcome)
        assert statuses["id:no-such-member"] == "rejected"
        assert statuses["pos:s0/code/0"] == "applied"  # the valid work still lands

    def test_wrong_choice_for_the_action_is_rejected(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("DE Text", "DE Text NEU")
        outcome = deck.apply(_decision("id:s0-m", choice="remove"))
        result = next(r for r in outcome.results if r.key == "id:s0-m")
        assert result.status == "rejected"
        assert "not valid" in result.reason or "does not accept" in result.reason

    def test_verify_cold_confirm_records_the_member(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.write_de(*DE_PARTS, _localized("s0-new", "de", "Neu"))
        deck.write_en(*EN_PARTS, _localized("s0-new", "en", "New"))
        _, diff = deck.diff()
        assert [(i.key, i.action) for i in diff.items] == [("id:s0-new", "verify_cold")]
        outcome = deck.apply(_decision("id:s0-new", choice="confirm"))
        assert outcome.all_applied, outcome.to_payload()
        assert not outcome.wrote  # confirmation is a pure ledger record
        deck.assert_converged()

    def test_confirm_on_a_one_sided_member_is_rejected(self, tmp_path: Path):
        # issue #566: a one-sided new member is framed translate_new (grow the
        # twin), not verify_cold — so `confirm` is not a valid answer for it and
        # is rejected at the vocabulary gate. The agent supplies a `body`
        # instead; there is no confirm-a-one-sided-member dead end anymore.
        deck = _deck(tmp_path)
        deck.write_de(*DE_PARTS, _localized("s0-new", "de", "Neu"))
        _, diff = deck.diff()
        assert [(i.key, i.action) for i in diff.items] == [("id:s0-new", "translate_new")]
        outcome = deck.apply(_decision("id:s0-new", choice="confirm"))
        result = next(r for r in outcome.results if r.key == "id:s0-new")
        assert result.status == "rejected"
        assert "translate_new" in result.reason

    def test_remove_vs_edit_keep_readds_the_survivor(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.write_de(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            _shared_code("y", 99),  # edited...
            _localized("s0-m", "de", "DE Text"),
        )
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            # ...while removed on EN
            _localized("s0-m", "en", "EN text"),
        )
        _, diff = deck.diff()
        (item,) = diff.items
        assert item.action == "remove_vs_edit"
        outcome = deck.apply(_decision(item.key, choice="keep"))
        assert outcome.all_applied, outcome.to_payload()
        assert "y = 99" in deck.en_path.read_text(encoding="utf-8")
        deck.assert_converged()

    def test_remove_vs_edit_remove_deletes_the_survivor(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.write_de(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            _shared_code("y", 99),
            _localized("s0-m", "de", "DE Text"),
        )
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            _localized("s0-m", "en", "EN text"),
        )
        _, diff = deck.diff()
        (item,) = diff.items
        outcome = deck.apply(_decision(item.key, choice="remove"))
        assert outcome.all_applied, outcome.to_payload()
        assert "y = 99" not in deck.de_path.read_text(encoding="utf-8")
        deck.assert_converged()


class TestColdBodyRecovery:
    """Issue #572: a `body` answer fixes a stale twin on a two-sided
    `verify_cold` member in one pass, instead of `confirm` banking it stale."""

    def _cold_member_with_stale_de(self, tmp_path: Path) -> _Deck:
        # Record the base, then add a NEW id member whose DE twin is a stale
        # placeholder while EN carries the real content — the shape the issue
        # describes (a renamed/edited cell that fell cold with a stale twin).
        deck = _deck(tmp_path)
        deck.write_de(*DE_PARTS, _localized("s0-new", "de", "*(placeholder)*"))
        deck.write_en(*EN_PARTS, _localized("s0-new", "en", "Real EN content"))
        _, diff = deck.diff()
        assert [(i.key, i.action) for i in diff.items] == [("id:s0-new", "verify_cold")]
        return deck

    def test_body_overwrites_the_named_stale_twin(self, tmp_path: Path):
        deck = self._cold_member_with_stale_de(tmp_path)
        outcome = deck.apply(_decision("id:s0-new", body="# Echte DE-Übersetzung", side="de"))
        assert outcome.all_applied, outcome.to_payload()
        assert outcome.wrote
        de_text = deck.de_path.read_text(encoding="utf-8")
        assert "# Echte DE-Übersetzung" in de_text
        assert "*(placeholder)*" not in de_text
        # The other half is untouched, and the fixed pair records clean.
        assert "Real EN content" in deck.en_path.read_text(encoding="utf-8")
        deck.assert_converged()

    def test_body_answer_advertised_only_for_id_keyed_cold(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.write_de(*DE_PARTS, _localized("s0-new", "de", "Neu"))
        deck.write_en(*EN_PARTS, _localized("s0-new", "en", "New"))
        _, diff = deck.diff()
        (item,) = diff.items
        assert doc_apply.item_answers(item) == ("confirm", "body")

    def test_body_without_side_is_rejected(self, tmp_path: Path):
        deck = self._cold_member_with_stale_de(tmp_path)
        before = deck.de_path.read_text(encoding="utf-8")
        outcome = deck.apply(_decision("id:s0-new", body="# irgendwas"))
        result = next(r for r in outcome.results if r.key == "id:s0-new")
        assert result.status == "rejected"
        assert "side" in result.reason
        assert deck.de_path.read_text(encoding="utf-8") == before

    def test_confirm_still_records_the_pair_as_is(self, tmp_path: Path):
        # `confirm` remains valid — it banks both sides verbatim (the caller
        # judged them in sync). This is the pre-#572 behavior, still available.
        deck = self._cold_member_with_stale_de(tmp_path)
        outcome = deck.apply(_decision("id:s0-new", choice="confirm"))
        assert outcome.all_applied, outcome.to_payload()
        assert not outcome.wrote
        deck.assert_converged()

    def test_body_on_a_positional_cold_member_is_rejected(self, tmp_path: Path):
        # A new un-id'd shared code cell inserted among existing cells falls
        # cold positionally (its ordinal aliases a neighbor); it has no
        # addressable id, so a body is refused (mint a slide_id instead).
        deck = _deck(tmp_path)
        new = _shared_code("z", 9)  # new, between x and y — added on BOTH sides
        deck.write_de(
            HEADER_DE,
            _slide("s0", "de", "Titel"),
            _shared_code("x"),
            new,
            _shared_code("y", 2),
            _localized("s0-m", "de", "DE Text"),
        )
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            new,
            _shared_code("y", 2),
            _localized("s0-m", "en", "EN text"),
        )
        _, diff = deck.diff()
        cold = [i for i in diff.items if i.action == "verify_cold" and i.key.startswith("pos:")]
        assert cold, [(i.key, i.action) for i in diff.items]
        item = cold[0]
        assert doc_apply.item_answers(item) == ("confirm",)
        outcome = deck.apply(_decision(item.key, body="z = 9", side="de"))
        result = next(r for r in outcome.results if r.key == item.key)
        assert result.status == "rejected"
        assert "positional" in result.reason

    def test_side_on_a_translate_edit_body_is_rejected(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("DE Text", "DE Text NEU")
        _, diff = deck.diff()
        assert [(i.key, i.action) for i in diff.items] == [("id:s0-m", "translate_edit")]
        outcome = deck.apply(_decision("id:s0-m", body="# EN new", side="en"))
        result = next(r for r in outcome.results if r.key == "id:s0-m")
        assert result.status == "rejected"
        assert "side" in result.reason


class TestStampVsNew:
    """Issue #600: replacing an un-id'd positional cell with new id'd cells on
    one side frames every affected row ``stamp_vs_new`` with a ``treat_as_new``
    answer — the loop resolves through one decision document instead of
    dead-ending on an empty vocabulary."""

    def _replace_pos_cell_on_en(self, tmp_path: Path) -> _Deck:
        deck = _deck(tmp_path)
        deck.edit_en(
            '# %% tags=["keep"]\ny = 2\n',
            '# %% tags=["keep"] slide_id="y-assign"\ny = 3\n\n# %% slide_id="y-check"\ny\n',
        )
        return deck

    def test_treat_as_new_grows_twins_and_mirrors_the_removal(self, tmp_path: Path):
        deck = self._replace_pos_cell_on_en(tmp_path)
        _, diff = deck.diff()
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:y-assign", "stamp_vs_new"),
            ("id:y-check", "stamp_vs_new"),
            ("pos:s0/code/1", "stamp_vs_new"),
        }, [(i.key, i.action, i.detail) for i in diff.items]
        # The report advertises exactly what the executor accepts — never [].
        assert all(doc_apply.item_answers(i) == ("treat_as_new",) for i in diff.items)
        decisions = {
            i.key: doc_apply.Decision(key=i.key, choice="treat_as_new") for i in diff.items
        }
        outcome = deck.apply(decisions)
        assert outcome.all_applied, outcome.to_payload()
        de = deck.de_path.read_text(encoding="utf-8")
        assert '# %% tags=["keep"] slide_id="y-assign"\ny = 3\n' in de
        assert '# %% slide_id="y-check"\ny\n' in de
        assert "y = 2" not in de  # the superseded positional cell is gone
        deck.assert_converged()

    def test_partial_answering_still_converges(self, tmp_path: Path):
        # Answering only the id-view rows grows the twins; the vanished
        # positional cell then resolves mechanically on the next pass.
        deck = self._replace_pos_cell_on_en(tmp_path)
        outcome = deck.apply(
            {
                key: doc_apply.Decision(key=key, choice="treat_as_new")
                for key in ("id:y-assign", "id:y-check")
            }
        )
        assert _statuses(outcome)["pos:s0/code/1"] == "pending"
        _, diff = deck.diff()
        assert [(i.key, i.action) for i in diff.items] == [("pos:s0/code/1", "mirror_remove")]
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        assert "y = 2" not in deck.de_path.read_text(encoding="utf-8")
        deck.assert_converged()

    def test_edited_survivor_frames_remove_vs_edit_instead_of_a_dead_end(self, tmp_path: Path):
        # #602 adversarial review: the surviving positional twin carries an
        # edit, so a mirrored removal is deterministically rejected — the
        # report must not advertise treat_as_new as the row's only answer.
        # The shape frames remove_vs_edit instead (remove/keep both land),
        # with the stamp suspicion spelled out in the detail.
        deck = self._replace_pos_cell_on_en(tmp_path)
        deck.edit_de("y = 2", "y = 99")
        _, diff = deck.diff()
        pos_item = next(i for i in diff.items if i.key == "pos:s0/code/1")
        assert pos_item.action == "remove_vs_edit", (pos_item.action, pos_item.detail)
        assert "unmatched id'd cell" in pos_item.detail
        assert doc_apply.item_answers(pos_item) == ("remove", "keep")
        decisions = {
            "id:y-assign": doc_apply.Decision(key="id:y-assign", choice="treat_as_new"),
            "id:y-check": doc_apply.Decision(key="id:y-check", choice="treat_as_new"),
            "pos:s0/code/1": doc_apply.Decision(key="pos:s0/code/1", choice="remove"),
        }
        outcome = deck.apply(decisions)
        assert outcome.all_applied, outcome.to_payload()
        de = deck.de_path.read_text(encoding="utf-8")
        assert "y = 99" not in de  # the deliberate removal landed
        assert '# %% tags=["keep"] slide_id="y-assign"\ny = 3\n' in de
        deck.assert_converged()

    def test_partial_pool_answer_keeps_the_sibling_suspicion(self, tmp_path: Path):
        # #602 adversarial review: landing treat_as_new on ONE pool slot must
        # not re-record the pool wholesale — that would erase the two-sided
        # base entries evidencing the OTHER slot's suspicion, silently
        # downgrading it to mechanical duplication on the next report.
        deck = _deck(tmp_path)
        deck.edit_en(
            '# %% tags=["keep"]\nx = 1\n',
            '# %% tags=["keep"] slide_id="x-new"\nx = 10\n',
        )
        deck.edit_en(
            '# %% tags=["keep"]\ny = 2\n',
            '# %% tags=["keep"] slide_id="y-new"\ny = 20\n',
        )
        _, diff = deck.diff()
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:x-new", "stamp_vs_new"),
            ("id:y-new", "stamp_vs_new"),
            ("pos:s0/code/0", "stamp_vs_new"),
            ("pos:s0/code/1", "stamp_vs_new"),
        }, [(i.key, i.action, i.detail) for i in diff.items]
        # Answer only the x rows; the y suspicion is left pending.
        outcome = deck.apply(
            {
                key: doc_apply.Decision(key=key, choice="treat_as_new")
                for key in ("id:x-new", "pos:s0/code/0")
            }
        )
        statuses = _statuses(outcome)
        assert statuses["id:x-new"] == "applied"
        assert statuses["pos:s0/code/0"] == "applied"
        assert statuses["pos:s0/code/1"] == "pending"
        # The next report must still frame y as a suspected stamp (never as a
        # mechanical copy plus a resurrected "new" cell).
        _, diff = deck.diff()
        actions = {i.key: i.action for i in diff.items}
        assert actions["id:y-new"] == "stamp_vs_new"
        assert actions["pos:s0/code/1"] == "stamp_vs_new"
        # x's landed slot re-frames as a mechanical removal record (the frozen
        # pool deferred its ledger update) and everything converges once the
        # remaining suspicion is answered.
        assert actions.get("pos:s0/code/0") == "record_remove"
        outcome = deck.apply(
            {
                key: doc_apply.Decision(key=key, choice="treat_as_new")
                for key in ("id:y-new", "pos:s0/code/1")
            }
        )
        assert outcome.all_applied, outcome.to_payload()
        de = deck.de_path.read_text(encoding="utf-8")
        assert de.count("x = 10") == 1 and de.count("y = 20") == 1
        assert "x = 1\n" not in de and "y = 2\n" not in de
        deck.assert_converged()


class TestTitleMacroBody:
    """Issue #609: a body answer on the ``id:title`` header-macro member.

    The title cell is a single j2 line — simultaneously the cell boundary and
    the whole content — so the generic delimiter guard rejected EVERY valid
    answer (dead end; ``keep_twin`` was the only accepted answer) and the
    generic writer would have appended the text as a raw line instead of
    replacing the macro line.
    """

    def _edited(self, tmp_path: Path) -> _Deck:
        deck = _deck(tmp_path)
        deck.edit_en('header_en("Title EN")', 'header_en("Weather Agent: Real Data")')
        _, diff = deck.diff()
        assert ("id:title", "translate_edit") in [(i.key, i.action) for i in diff.items]
        return deck

    def test_full_macro_line_replaces_the_twin_title(self, tmp_path: Path):
        deck = self._edited(tmp_path)
        outcome = deck.apply(
            _decision("id:title", body='# {{ header_de("Wetter-Agent: Echte Daten") }}\n')
        )
        assert outcome.all_applied, outcome.to_payload()
        de_text = deck.de_path.read_text(encoding="utf-8")
        assert '# {{ header_de("Wetter-Agent: Echte Daten") }}\n' in de_text
        assert "Titel DE" not in de_text  # replaced, not appended alongside
        deck.assert_converged()

    def test_bare_title_text_is_spliced_into_the_macro(self, tmp_path: Path):
        deck = self._edited(tmp_path)
        outcome = deck.apply(_decision("id:title", body="Wetter-Agent: Echte Daten"))
        assert outcome.all_applied, outcome.to_payload()
        de_text = deck.de_path.read_text(encoding="utf-8")
        assert '# {{ header_de("Wetter-Agent: Echte Daten") }}' in de_text
        assert "Titel DE" not in de_text
        deck.assert_converged()

    def test_multi_line_body_is_rejected_byte_unchanged(self, tmp_path: Path):
        deck = self._edited(tmp_path)
        before = deck.de_path.read_text(encoding="utf-8")
        outcome = deck.apply(_decision("id:title", body="Neuer Titel\n# und noch eine Zeile"))
        result = next(r for r in outcome.results if r.key == "id:title")
        assert result.status == "rejected"
        assert "single-line j2 macro" in result.reason
        assert deck.de_path.read_text(encoding="utf-8") == before

    def test_percent_delimiter_is_still_rejected(self, tmp_path: Path):
        deck = self._edited(tmp_path)
        before = deck.de_path.read_text(encoding="utf-8")
        outcome = deck.apply(_decision("id:title", body='# %% [markdown] slide_id="evil"'))
        result = next(r for r in outcome.results if r.key == "id:title")
        assert result.status == "rejected"
        assert deck.de_path.read_text(encoding="utf-8") == before

    def test_bare_text_with_quotes_is_rejected(self, tmp_path: Path):
        deck = self._edited(tmp_path)
        outcome = deck.apply(_decision("id:title", body='Wetter "Agent"'))
        result = next(r for r in outcome.results if r.key == "id:title")
        assert result.status == "rejected"
        assert "full j2 line" in result.reason

    def test_bare_text_with_backslash_is_rejected(self, tmp_path: Path):
        """Regression test for #629 (F2): '\\' can escape out of the j2
        string literal, so bare text carrying one must be rejected."""
        deck = self._edited(tmp_path)
        before = deck.de_path.read_text(encoding="utf-8")
        outcome = deck.apply(_decision("id:title", body="Wetter\\Agent"))
        result = next(r for r in outcome.results if r.key == "id:title")
        assert result.status == "rejected"
        assert "full j2 line" in result.reason
        assert deck.de_path.read_text(encoding="utf-8") == before

    def test_keep_twin_still_works_on_the_title(self, tmp_path: Path):
        deck = self._edited(tmp_path)
        de_before = deck.de_path.read_text(encoding="utf-8")
        outcome = deck.apply(_decision("id:title", choice="keep_twin"))
        assert outcome.all_applied, outcome.to_payload()
        assert deck.de_path.read_text(encoding="utf-8") == de_before
        deck.assert_converged()


class TestMacroHeaderFromBody:
    """Regression tests for #629: the bare-text splice must target exactly
    one quoted macro argument (F1) and reject characters that could escape
    the j2 string literal (F2).

    The bilingual two-argument form ``header("De", "En")`` is not reachable
    through the two-sided decision pipeline today (a byte-identical shared
    title carries no divergence), so these pin the writer directly.
    """

    def _cell(self, header: str) -> SideCell:
        return SideCell(
            lines=(header, ""),
            index=0,
            line_number=1,
            part="deck",
            lang_attr=None,
            tags=(),
            slide_id=None,
            for_slide=None,
            vo_anchor=None,
            cell_type="j2",
        )

    def _splice(self, header: str, body: str) -> str:
        return doc_apply._macro_header_from_body(self._cell(header), body, "#", bare_ok=True)

    def test_single_argument_macro_splices_bare_text(self):
        header = self._splice('# {{ header_de("Titel DE") }}', "Neuer Titel")
        assert header == '# {{ header_de("Neuer Titel") }}'

    def test_two_argument_macro_rejects_bare_text(self):
        with pytest.raises(doc_apply._ItemError, match="multiple quoted arguments"):
            self._splice('# {{ header("Titel DE", "Title EN") }}', "Neuer Titel")

    def test_two_argument_macro_accepts_the_full_j2_line(self):
        line = '# {{ header("Neu DE", "New EN") }}'
        assert self._splice('# {{ header("Titel DE", "Title EN") }}', line) == line

    def test_zero_argument_macro_rejects_bare_text(self):
        with pytest.raises(doc_apply._ItemError, match="no quoted argument"):
            self._splice("# {{ clear_page() }}", "Neuer Titel")

    def test_bare_text_with_backslash_is_rejected(self):
        with pytest.raises(doc_apply._ItemError, match="full j2 line"):
            self._splice('# {{ header_de("Titel DE") }}', "Neuer\\Titel")


class TestGroupSplitGuard:
    """Issue #610: an id-keyed slide inserted before a run of un-id'd
    positional shared cells moves them into the new slide's group on that
    half only. The old pool's ``mirror_remove`` rows (which a mechanical
    apply would execute, DELETING the twin's untouched cells) must be
    reframed as ``remove_vs_split`` decisions (#630) until the insert is
    mirrored — or the removal is explicitly answered ``remove``.
    """

    CODE = (_shared_code("x"), _shared_code("y", 2), _shared_code("z", 3))

    def _split_deck(self, tmp_path: Path) -> _Deck:
        deck = _Deck(
            tmp_path,
            _build(HEADER_DE, _slide("s0", "de", "Titel"), *self.CODE),
            _build(HEADER_EN, _slide("s0", "en", "Title"), *self.CODE),
        )
        deck.record()
        # EN inserts a new id-keyed slide BEFORE the run of positional cells:
        # on EN they now belong to group s1, on DE they stay in group s0.
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _slide("s1", "en", "Setup"),
            *self.CODE,
        )
        return deck

    def test_group_split_is_never_framed_as_mechanical_removal(self, tmp_path: Path):
        deck = self._split_deck(tmp_path)
        _, diff = deck.diff()
        actions = {(i.key, i.action) for i in diff.items}
        assert ("id:s1", "translate_new") in actions
        assert not any(a == "mirror_remove" for _, a in actions), actions
        reframed = [i for i in diff.items if i.action == "remove_vs_split"]
        assert len(reframed) == 3
        assert all("group split" in i.detail for i in reframed)
        assert all(doc_apply.item_answers(i) == ("remove",) for i in reframed)
        assert any(o.kind == "suspected_group_split" for o in diff.observations)

    def test_mechanical_apply_deletes_nothing(self, tmp_path: Path):
        deck = self._split_deck(tmp_path)
        de_before = deck.de_path.read_text(encoding="utf-8")
        outcome = deck.apply()
        assert outcome.error is None
        assert deck.de_path.read_text(encoding="utf-8") == de_before
        for name in ("x = 1", "y = 2", "z = 3"):
            assert name in deck.de_path.read_text(encoding="utf-8")

    def test_mirroring_the_insert_then_confirming_converges(self, tmp_path: Path):
        # The issue's documented workaround, now driven through decisions:
        # grow the twin slide (translate_new body), re-report — the framing
        # flips to record_remove + two-sided verify_cold — confirm the pool.
        deck = self._split_deck(tmp_path)
        outcome = deck.apply(_decision("id:s1", body="#\n# # Einrichtung"))
        assert _statuses(outcome)["id:s1"] == "applied", outcome.to_payload()
        _, diff = deck.diff()
        actions = {(i.key, i.action) for i in diff.items}
        assert not any(a in ("mirror_remove", "remove_vs_split") for _, a in actions), actions
        cold = [i.key for i in diff.items if i.action == "verify_cold"]
        assert sorted(cold) == ["pos:s1/code/0", "pos:s1/code/1", "pos:s1/code/2"]
        confirms = {key: doc_apply.Decision(key=key, choice="confirm") for key in cold}
        outcome = deck.apply(confirms)
        assert outcome.all_applied, outcome.to_payload()
        deck.assert_converged()
        for name in ("x = 1", "y = 2", "z = 3"):
            assert name in deck.de_path.read_text(encoding="utf-8")

    def test_partial_split_keeps_the_base_evidence_frozen(self, tmp_path: Path):
        # A landing sibling row (propagate) must not re-record the old pool
        # wholesale while the reframed removal is unresolved — the two-sided
        # base entry is the only record the gone side ever existed.
        deck = _Deck(
            tmp_path,
            _build(HEADER_DE, _slide("s0", "de", "Titel"), *self.CODE),
            _build(HEADER_EN, _slide("s0", "en", "Title"), *self.CODE),
        )
        deck.record()
        # EN inserts s1 before z only; DE edits x (a propagate that lands).
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            _shared_code("y", 2),
            _slide("s1", "en", "Setup"),
            _shared_code("z", 3),
        )
        deck.edit_de("x = 1", "x = 111")
        _, diff = deck.diff()
        reframed = [i for i in diff.items if i.action == "remove_vs_split"]
        assert [i.key for i in reframed] == ["pos:s0/code/2"]
        outcome = deck.apply()
        assert outcome.error is None
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(deck.de_path))
        entries = ledger.decks["slides_t"].members
        z_entry = entries.get("pos:s0/code/2")
        assert z_entry is not None, sorted(entries)
        assert z_entry.entry.de_fp is not None and z_entry.entry.en_fp is not None
        # z itself is untouched on DE.
        assert "z = 3" in deck.de_path.read_text(encoding="utf-8")


class TestRemoveVsSplit:
    """Issue #630: the #610 guard is fingerprint-only, so a genuine removal
    that coincides with a byte-identical one-sided add elsewhere is blocked
    too. The reframed row must be answerable in-tool (F1), the pool freeze
    must not re-gate unrelated ``ambiguous_alignment`` shapes (F2), an
    edited split must at least warn (F3), and every rival group must be
    named (F4).
    """

    BOILER = _shared_code("b", 9)

    def _coincidence_deck(self, tmp_path: Path) -> _Deck:
        """A genuine removal + a coincidental identical one-sided add.

        EN deletes the boilerplate cell from slide s0 and independently adds
        the same boilerplate to the pre-existing slide s1 (EN half first) —
        no slide was inserted, nothing moved groups; DE is untouched.
        """
        deck = _Deck(
            tmp_path,
            _build(
                HEADER_DE,
                _slide("s0", "de", "Titel"),
                self.BOILER,
                _shared_code("x"),
                _slide("s1", "de", "Zweite"),
                _shared_code("q", 7),
            ),
            _build(
                HEADER_EN,
                _slide("s0", "en", "Title"),
                self.BOILER,
                _shared_code("x"),
                _slide("s1", "en", "Second"),
                _shared_code("q", 7),
            ),
        )
        deck.record()
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            _slide("s1", "en", "Second"),
            _shared_code("q", 7),
            self.BOILER,
        )
        return deck

    def test_coincidental_duplicate_row_advertises_a_remove_answer(self, tmp_path: Path):
        # Regression test for #630 F1: the reframed removal advertised no
        # answers at all — a dead end the decision document could not resolve.
        deck = self._coincidence_deck(tmp_path)
        _, diff = deck.diff()
        rows = [i for i in diff.items if i.action == "remove_vs_split"]
        assert len(rows) == 1, [(i.key, i.action) for i in diff.items]
        assert doc_apply.item_answers(rows[0]) == ("remove",)
        assert "remove" in rows[0].detail

    def test_answering_remove_executes_the_blocked_removal(self, tmp_path: Path):
        # Regression test for #630 F1: the in-tool escape — answer `remove`,
        # the genuine deletion lands, the coincidental cell is untouched.
        deck = self._coincidence_deck(tmp_path)
        _, diff = deck.diff()
        row = next(i for i in diff.items if i.action == "remove_vs_split")
        outcome = deck.apply(_decision(row.key, choice="remove"))
        assert _statuses(outcome)[row.key] == "applied", outcome.to_payload()
        assert "b = 9" not in deck.de_path.read_text(encoding="utf-8")
        assert "b = 9" in deck.en_path.read_text(encoding="utf-8")
        _, diff = deck.diff()
        leftover = {(i.key, i.action) for i in diff.items}
        assert all(a == "verify_cold" for _, a in leftover), leftover

    def test_every_rival_group_is_named(self, tmp_path: Path):
        # Regression test for #630 F4: only the lexicographically-first
        # rival group was named, which can point the user at the wrong slide.
        deck = _Deck(
            tmp_path,
            _build(
                HEADER_DE,
                _slide("s0", "de", "Titel"),
                self.BOILER,
                _slide("s1", "de", "Zweite"),
                _shared_code("q", 7),
                _slide("s2", "de", "Dritte"),
                _shared_code("r", 8),
            ),
            _build(
                HEADER_EN,
                _slide("s0", "en", "Title"),
                self.BOILER,
                _slide("s1", "en", "Second"),
                _shared_code("q", 7),
                _slide("s2", "en", "Third"),
                _shared_code("r", 8),
            ),
        )
        deck.record()
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _slide("s1", "en", "Second"),
            _shared_code("q", 7),
            self.BOILER,
            _slide("s2", "en", "Third"),
            _shared_code("r", 8),
            self.BOILER,
        )
        _, diff = deck.diff()
        row = next(i for i in diff.items if i.action == "remove_vs_split")
        assert "'s1'" in row.detail and "'s2'" in row.detail, row.detail

    def test_pool_freezing_is_gated_to_the_split_action(self):
        # Regression test for #630 F2: pre-existing `ambiguous_alignment`
        # emitters (rival id stamps, both-sides-added pools) must keep their
        # pre-#625 recording behavior — only the group-split reframe freezes
        # its pool.
        row = DiffItem(
            key="pos:s0/code/0",
            outcome="conflict",
            action="ambiguous_alignment",
            direction="both",
            detail="",
        )
        assert doc_apply._frozen_pools([row]) == set()
        assert doc_apply._frozen_pools([evolve(row, action="remove_vs_split")]) == {("s0", "code")}

    SETUP = '# %% tags=["keep"]\nresult = compute(1, 2)\nprint(result)\n\n'
    EDITED = '# %% tags=["keep"]\nresult = compute(1, 3)\nprint(result)\n\n'

    def test_edited_split_warns_with_a_similar_bodies_observation(self, tmp_path: Path):
        # Regression test for #630 F3 (warn-only): a split whose moved cell
        # was ALSO edited escapes the byte-identity guard — the removal stays
        # mechanical, but the report must flag the near-match for review.
        deck = _Deck(
            tmp_path,
            _build(HEADER_DE, _slide("s0", "de", "Titel"), self.SETUP),
            _build(HEADER_EN, _slide("s0", "en", "Title"), self.SETUP),
        )
        deck.record()
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _slide("s1", "en", "Setup"),
            self.EDITED,
        )
        _, diff = deck.diff()
        actions = {(i.key, i.action) for i in diff.items}
        assert ("pos:s0/code/0", "mirror_remove") in actions, actions
        obs = [o for o in diff.observations if o.kind == "suspected_group_split"]
        assert len(obs) == 1, [(o.kind, o.detail) for o in diff.observations]
        assert "similar" in obs[0].detail
        assert "'s1'" in obs[0].detail

    def test_attr_only_change_on_moved_cells_still_reframes(self, tmp_path: Path):
        # #630 adversarial review: content_fingerprint covers header attrs,
        # so a moved cell that merely gained a tag dodged the exact guard
        # and apply deleted the twin's cell. The gone side's recorded BODY
        # fingerprint is still exact evidence and must reframe.
        deck = _Deck(
            tmp_path,
            _build(HEADER_DE, _slide("s0", "de", "Titel"), _shared_code("marker", 5)),
            _build(HEADER_EN, _slide("s0", "en", "Title"), _shared_code("marker", 5)),
        )
        deck.record()
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _slide("s1", "en", "Setup"),
            _shared_code("marker", 5, tags="changed"),
        )
        _, diff = deck.diff()
        row = next(i for i in diff.items if i.key == "pos:s0/code/0")
        assert row.action == "remove_vs_split", (row.action, row.detail)
        assert "'s1'" in row.detail

    def test_diverged_base_has_no_similarity_proxy(self, tmp_path: Path):
        # #630 adversarial review: when the shared base recorded different
        # bytes per side, the surviving twin's body says nothing about what
        # vanished — the similar-bodies scan must stay silent instead of
        # comparing wrong-side text.
        de_cell = '# %% tags=["keep"]\nshared_setup = 111\nprint(shared_setup)\n\n'
        en_cell = '# %% tags=["keep"]\nshared_setup = 222\nprint(shared_setup)\n\n'
        deck = _Deck(
            tmp_path,
            _build(HEADER_DE, _slide("s0", "de", "Titel"), de_cell),
            _build(HEADER_EN, _slide("s0", "en", "Title"), en_cell),
        )
        deck.record()
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _slide("s1", "en", "Setup"),
            '# %% tags=["keep"]\nshared_setup = 223\nprint(shared_setup)\n\n',
        )
        _, diff = deck.diff()
        row = next(i for i in diff.items if i.key == "pos:s0/code/0")
        assert row.action == "mirror_remove", (row.action, row.detail)
        assert not [o for o in diff.observations if o.kind == "suspected_group_split"]

    def test_exact_match_does_not_hide_the_similar_split_target(self, tmp_path: Path):
        # #630 adversarial review: a coincidental byte-match in one group
        # must not suppress the similar-bodies evidence naming the real
        # (edited) split target — the agent would inspect only the named
        # group, answer remove, and delete the split-away cells.
        big = '# %% tags=["keep"]\nsetup_block = compute(1, 2)\nprint(setup_block)\n\n'
        edited = '# %% tags=["keep"]\nsetup_block = compute(1, 3)\nprint(setup_block)\n\n'
        deck = _Deck(
            tmp_path,
            _build(
                HEADER_DE,
                _slide("s0", "de", "Titel"),
                big,
                _slide("sB", "de", "Zweite"),
                _shared_code("q", 7),
            ),
            _build(
                HEADER_EN,
                _slide("s0", "en", "Title"),
                big,
                _slide("sB", "en", "Second"),
                _shared_code("q", 7),
            ),
        )
        deck.record()
        # EN: s0's cell is split into the new slide sN with an edit, while
        # the unrelated group sB independently gains a byte-identical copy.
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _slide("sN", "en", "Neu"),
            edited,
            _slide("sB", "en", "Second"),
            _shared_code("q", 7),
            big,
        )
        _, diff = deck.diff()
        row = next(i for i in diff.items if i.key == "pos:s0/code/0")
        assert row.action == "remove_vs_split"
        assert "'sB'" in row.detail
        similar = [
            o
            for o in diff.observations
            if o.kind == "suspected_group_split" and "similar" in o.detail
        ]
        assert similar, [(o.kind, o.detail) for o in diff.observations]
        assert "'sN'" in similar[0].detail

    def test_body_similarity_is_budgeted_and_memoized(self):
        # #630 adversarial review: ratio() is quadratic and reorganized
        # decks compare many pairs — the scan caches verdicts and stops
        # matching once the full-ratio budget is spent (warn-only feature,
        # best-effort by design).
        sim = sync_diff._BodySimilarity()
        base = "line one\nline two\nline three\nline four\nline five"
        close = base.replace("five", "5ive")
        assert sim.similar(base, base) is True  # equality costs no budget
        sim._budget = 1
        assert sim.similar(close, base) is True  # spends the last budget
        assert sim._budget == 0
        assert sim.similar(close, base) is True  # cached, no budget needed
        other = base.replace("two", "2wo")
        assert sim.similar(other, base) is False  # budget exhausted
        assert sim.similar("x" * 3000, "x" * 3000) is False  # oversized skipped


class TestGroupSplitInterleave:
    """Regression tests for #646: when ≥2 id'd slides are interleaved into a
    single run of id-less shared cells, mirroring the inserts on the twin
    must keep each shared cell under the slide it moved into. The buggy
    writer anchored each insert after the nearest mirrored predecessor
    only, so the second insert landed directly after the first and the
    twin's shared cells clumped after all the inserted slides.
    """

    def _interleaved_deck(self, tmp_path: Path) -> _Deck:
        code = (_shared_code("x"), _shared_code("y", 2), _shared_code("z", 3))
        deck = _Deck(
            tmp_path,
            _build(HEADER_DE, _slide("s0", "de", "Titel"), *code),
            _build(HEADER_EN, _slide("s0", "en", "Title"), *code),
        )
        deck.record()
        # EN interleaves TWO new id'd slides into the run: m1 before y
        # (splitting y into group m1), m2 before z (splitting z into m2).
        deck.write_en(
            HEADER_EN,
            _slide("s0", "en", "Title"),
            _shared_code("x"),
            _slide("m1", "en", "Middle 1"),
            _shared_code("y", 2),
            _slide("m2", "en", "Middle 2"),
            _shared_code("z", 3),
        )
        return deck

    def test_twin_keeps_the_shared_cells_interleaved(self, tmp_path: Path):
        deck = self._interleaved_deck(tmp_path)
        outcome = deck.apply(
            _decision("id:m1", body="#\n# # Mitte 1") | _decision("id:m2", body="#\n# # Mitte 2")
        )
        statuses = _statuses(outcome)
        assert statuses["id:m1"] == "applied", outcome.to_payload()
        assert statuses["id:m2"] == "applied", outcome.to_payload()
        de = deck.de_path.read_text(encoding="utf-8")
        markers = ("x = 1", "# Mitte 1", "y = 2", "# Mitte 2", "z = 3")
        order = [de.index(m) for m in markers]
        assert order == sorted(order), de

    def test_interleaved_split_converges_after_confirming(self, tmp_path: Path):
        # The full #630 resolution loop, with two inserts: mirror both
        # slides, re-report (framing flips to verify_cold under the new
        # groups), confirm — and the deck converges with the DE cells in
        # the EN order.
        deck = self._interleaved_deck(tmp_path)
        deck.apply(
            _decision("id:m1", body="#\n# # Mitte 1") | _decision("id:m2", body="#\n# # Mitte 2")
        )
        _, diff = deck.diff()
        actions = {(i.key, i.action) for i in diff.items}
        assert not any(a in ("mirror_remove", "remove_vs_split") for _, a in actions), actions
        cold = [i.key for i in diff.items if i.action == "verify_cold"]
        assert sorted(cold) == ["pos:m1/code/0", "pos:m2/code/0"]
        confirms = {key: doc_apply.Decision(key=key, choice="confirm") for key in cold}
        outcome = deck.apply(confirms)
        assert outcome.all_applied, outcome.to_payload()
        deck.assert_converged()


class TestDecisionParsing:
    def test_wrong_top_level_shape_error_teaches_the_schema(self):
        # The first error an agent sees must show the whole document shape —
        # agents provably guessed field names one rejection at a time.
        decisions, errors = doc_apply.parse_decisions({"id:x": "confirm"})
        assert not decisions
        assert len(errors) == 1
        assert '{"key": "id:intro", "choice": "confirm"}' in errors[0]
        assert "clm info sync-agents" in errors[0]
        assert "'# %%'" in errors[0]  # the body-format trap, stated up front

    def test_side_must_be_de_or_en(self):
        decisions, errors = doc_apply.parse_decisions(
            {"decisions": [{"key": "id:x", "body": "b", "side": "left"}]}
        )
        assert not decisions
        assert any("'side' must be 'de' or 'en'" in e for e in errors)

    def test_side_requires_a_body(self):
        decisions, errors = doc_apply.parse_decisions(
            {"decisions": [{"key": "id:x", "choice": "confirm", "side": "de"}]}
        )
        assert not decisions
        assert any("only accompanies a 'body'" in e for e in errors)

    def test_valid_side_body_parses(self):
        decisions, errors = doc_apply.parse_decisions(
            {"decisions": [{"key": "id:x", "body": "b", "side": "de"}]}
        )
        assert not errors
        assert decisions["id:x"].side == "de"


# ---------------------------------------------------------------------------
# The v3 write-path mutation oracle (design §11 Phase 3 exit gate)
# ---------------------------------------------------------------------------


class TestWritePathOracle:
    """Propagate-or-frame, never silent — through the v3 write path.

    The #269 cardinal invariant restated for v3: after ANY scripted one-sided
    mutation, either apply propagates it (and the loop converges) or the
    report frames it as agent work; a run must never read clean while a
    change was dropped.
    """

    MUTATIONS = (
        ("shared body edit", "x = 1", "x = 42"),
        ("shared tag edit", '# %% tags=["keep"]\nx = 1', '# %% tags=["keep", "alt"]\nx = 1'),
        ("localized body edit", "DE Text", "DE Text NEU"),
        ("slide title edit", "# # Titel", "# # Titel NEU"),
        ("header edit", 'header_de("Titel DE")', 'header_de("Titel DE Neu")'),
    )

    def test_every_one_sided_mutation_propagates_or_frames(self, tmp_path: Path):
        for i, (label, old, new) in enumerate(self.MUTATIONS):
            subdir = tmp_path / f"m{i}"
            subdir.mkdir()
            deck = _deck(subdir)
            deck.edit_de(old, new)
            _, diff = deck.diff()
            assert not diff.is_clean, f"{label}: silently clean"
            outcome = deck.apply()
            if outcome.all_applied:
                # Propagated: the loop converges and the twin carries the change.
                deck.assert_converged()
                assert outcome.wrote or outcome.count("recorded")
            else:
                # Framed: the residue is visible, nothing was dropped.
                assert outcome.count("pending") > 0, f"{label}: {outcome.to_payload()}"

    def test_both_sided_divergent_edit_is_a_framed_conflict(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 111")
        deck.edit_en("x = 1", "x = 222")
        _, diff = deck.diff()
        assert not diff.is_clean
        outcome = deck.apply()
        assert outcome.count("pending") == 1
        assert not outcome.wrote

    def test_apply_then_report_is_stable(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")
        deck.edit_de("y = 2", "y = 3")
        assert deck.apply().all_applied
        deck.assert_converged()
        # A second apply is a no-op: nothing to do, nothing rewritten.
        again = deck.apply()
        assert again.results == []
        assert not again.wrote


class TestReviewRegressions:
    """Pins for the Phase-3 adversarial review findings (never regress)."""

    def test_pool_rerecord_never_blesses_a_pending_sibling(self, tmp_path: Path):
        # One pool: cell x edited on DE only (mechanical), cell y edited
        # differently on BOTH sides (framed conflict). Applying the
        # mechanical item re-records the pool wholesale — but the pending
        # conflict's slot must come out COLD, never trusted at its diverged
        # state (which would silently drop the required reconciliation).
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")
        deck.edit_de("y = 2", "y = 111")
        deck.edit_en("y = 2", "y = 222")
        outcome = deck.apply()
        statuses = _statuses(outcome)
        assert statuses["pos:s0/code/0"] == "applied"
        assert statuses["pos:s0/code/1"] == "pending"
        _, diff = deck.diff()
        assert not diff.is_clean, "the pending conflict was silently blessed"
        assert any(i.outcome in ("conflict", "unverified") for i in diff.items)

    def test_member_filter_does_not_bless_the_skipped_sibling(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.edit_de("x = 1", "x = 42")
        deck.edit_de("y = 2", "y = 99")
        deck.apply(only_members={"pos:s0/code/0"})
        _, diff = deck.diff()
        assert not diff.is_clean, "the skipped edit was silently blessed"

    def test_confirm_only_apply_marks_the_ledger_changed(self, tmp_path: Path):
        deck = _deck(tmp_path)
        deck.write_de(*DE_PARTS, _localized("s0-new", "de", "Neu"))
        deck.write_en(*EN_PARTS, _localized("s0-new", "en", "New"))
        outcome = deck.apply(_decision("id:s0-new", choice="confirm"))
        assert outcome.all_applied
        assert not outcome.wrote
        assert outcome.ledger_changed  # the CLI save gate keys on this
        deck.assert_converged()

    def test_duplicate_content_confirm_survives_the_migration_sweep(self, tmp_path: Path):
        # A pos-keyed cell byte-identical (modulo slide_id) to an id'd cell:
        # its recorded trust must survive an unrelated apply — the migration
        # sweep is targeted, never a blanket fingerprint sweep.
        idd_dup = '# %% [markdown] tags=["keep"] slide_id="s0-dup"\n# same text\n\n'
        pos_dup = '# %% [markdown] tags=["keep"]\n# same text\n\n'
        deck = _Deck(
            tmp_path,
            _build(HEADER_DE, _slide("s0", "de", "Titel"), idd_dup, pos_dup, _shared_code("x")),
            _build(HEADER_EN, _slide("s0", "en", "Title"), idd_dup, pos_dup, _shared_code("x")),
        )
        deck.record()
        deck.edit_de("x = 1", "x = 42")  # unrelated mechanical edit
        assert deck.apply().all_applied
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(deck.de_path))
        pos_keys = [k for k in ledger.decks["slides_t"].members if k.startswith("pos:s0/markdown")]
        assert pos_keys, "the duplicate-content pos entry was swept by an unrelated apply"
        deck.assert_converged()

    # The verifier's repro shape: two per-language header cells BEFORE the
    # title macro form the ~header localized pool; deleting the DE half of
    # slot A while editing the EN half of slot B makes the parse pair slot
    # B's DE cell with slot A's EN cell (shifted cross-side pairing).
    HA_DE = "# j2 from 'macros.j2' import a_mac\n# {{ a_mac(\"A de\") }}\n\n"
    HA_EN = "# j2 from 'macros.j2' import a_mac\n# {{ a_mac(\"A en\") }}\n\n"
    HB_DE = "# j2 from 'macros.j2' import b_mac\n# {{ b_mac(\"B de\") }}\n\n"
    HB_EN = "# j2 from 'macros.j2' import b_mac\n# {{ b_mac(\"B en\") }}\n\n"

    def _shifted_deck(self, tmp_path: Path) -> _Deck:
        deck = _Deck(
            tmp_path,
            _build(
                self.HA_DE, self.HB_DE, HEADER_DE, _slide("s0", "de", "Titel"), _shared_code("x")
            ),
            _build(
                self.HA_EN, self.HB_EN, HEADER_EN, _slide("s0", "en", "Title"), _shared_code("x")
            ),
        )
        deck.record()
        de_text = deck.de_path.read_text(encoding="utf-8")
        deck.de_path.write_text(de_text.replace(self.HA_DE, ""), encoding="utf-8")
        deck.edit_en('b_mac("B en")', 'b_mac("B en!")')
        return deck

    def test_shifted_pairing_carries_the_member_twin_convention(self, tmp_path: Path):
        deck = self._shifted_deck(tmp_path)
        _, diff = deck.diff()
        edits = [i for i in diff.items if i.action == "translate_edit"]
        assert len(edits) == 1, [(i.action, i.key) for i in diff.items]
        item = edits[0]
        # The pairing shifted: member carries the slot's DE cell, twin its
        # EN cell — the executor's holder rule depends on exactly this.
        assert item.twin is not None
        assert item.member is not None and item.member.de is not None
        assert "b_mac" in item.member.de.header
        assert item.twin.en is not None and 'b_mac("B en!")' in item.twin.en.header
        # ...and the report excerpts render each side from its carrier.
        payload = item.payload()
        assert 'b_mac("B de")' in payload["de"]
        assert 'b_mac("B en!")' in payload["en"]
        # A body answer on the shifted slot must land on the slot's OWN DE
        # cell (the holder rule) — never a neighboring slot's. Since #609 the
        # macro-cell writer replaces the j2 line in place.
        outcome = deck.apply(_decision(item.key, body='# {{ b_mac("B de!") }}'))
        result = next(r for r in outcome.results if r.key == item.key)
        assert result.status == "applied", outcome.to_payload()
        de_text = deck.de_path.read_text(encoding="utf-8")
        assert '# {{ b_mac("B de!") }}' in de_text
        assert 'a_mac("B de!")' not in de_text  # the neighbor slot is untouched
        assert 'header_de("Titel DE")' in de_text

    def test_shifted_pairing_remove_decision_touches_only_the_survivor(self, tmp_path: Path):
        deck = self._shifted_deck(tmp_path)
        _, diff = deck.diff()
        removals = [i for i in diff.items if i.action == "remove_localized_side"]
        assert removals, [(i.action, i.key) for i in diff.items]
        decisions = {i.key: doc_apply.Decision(key=i.key, choice="remove") for i in removals}
        outcome = deck.apply(decisions)
        assert all(r.status == "applied" for r in outcome.results if r.key in decisions), (
            outcome.to_payload()
        )
        en = deck.en_path.read_text(encoding="utf-8")
        de = deck.de_path.read_text(encoding="utf-8")
        assert "a_mac" not in en  # the surviving EN half of slot A was removed
        assert 'b_mac("B de")' in de  # slot B's DE cell was NEVER touched
        assert 'b_mac("B en!")' in en  # slot B's edited EN cell survived


# ---------------------------------------------------------------------------
# Cross-side tag parity (issue #615)
# ---------------------------------------------------------------------------


def _ledger_entry(deck: _Deck, key: str):
    ledger = doc_ledger.load(doc_ledger.ledger_path_for(deck.de_path))
    return ledger.decks[doc_ledger.deck_key_for(deck.de_path)].members[key].entry


class TestTagParity:
    """Issue #615: cross-side tag parity is a first-class diff aspect.

    Tags are language-independent and mirror across the twins, but the
    localized path used to compare each side only against its own recorded
    fingerprint — a one-sided tag edit coinciding with body drift was
    silently banked by ``confirm``, leaving report clean while ``validate``
    flagged the pair forever. These are the apply-level round trips for the
    F1 (``conflict_tags`` / ``mirror_tags`` aspect rows) + F2 (recording
    guards) fixes.
    """

    DE_PLAIN = '# %% [markdown] lang="de" slide_id="s0-m"'
    DE_TAGGED = '# %% [markdown] lang="de" tags=["voiceover"] slide_id="s0-m"'

    def _divergent_baseline_deck(self, tmp_path: Path) -> _Deck:
        """A recorded baseline that ITSELF carries the cross-side divergence —
        the damaged end state #615 used to leave behind."""
        deck = _Deck(
            tmp_path,
            _build(
                HEADER_DE,
                _slide("s0", "de", "Titel"),
                _shared_code("x"),
                '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-m"\n# DE Text\n\n',
            ),
            _build(
                HEADER_EN,
                _slide("s0", "en", "Title"),
                _shared_code("x"),
                '# %% [markdown] lang="en" tags=["voiceover"] slide_id="s0-m"\n# EN text\n\n',
            ),
        )
        deck.record()
        return deck

    def test_615_confirm_lands_the_mirrored_tags_in_one_pass(self, tmp_path: Path):
        # The issue's exact shape: both bodies drifted off base AND the DE
        # side carries a one-sided tag edit. The tag aspect is its own
        # mechanical row; `confirm` answers the body row in the SAME pass
        # (the guard sees the in-pass mirror) and the banked entry is in
        # tag parity — never the silently-divergent #615 end state.
        deck = _deck(tmp_path)
        deck.edit_de(self.DE_PLAIN, self.DE_TAGGED)
        deck.edit_de("DE Text", "DE Text NEU")
        deck.edit_en("EN text", "EN text NEW")
        _, diff = deck.diff()
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:s0-m", "mirror_tags"),
            ("id:s0-m", "verify_translation"),
        }, [(i.key, i.action, i.detail) for i in diff.items]
        outcome = deck.apply(_decision("id:s0-m", choice="confirm"))
        assert outcome.all_applied, outcome.to_payload()
        en = deck.en_path.read_text(encoding="utf-8")
        assert 'tags=["voiceover"]' in en  # the tag line mirrored...
        assert "EN text NEW" in en  # ...and the EN body was untouched
        entry = _ledger_entry(deck, "id:s0-m")
        assert entry.de_tags == entry.en_tags == ("voiceover",)
        deck.assert_converged()

    def test_confirm_on_a_cold_member_with_divergent_tags_is_rejected(self, tmp_path: Path):
        # S4: a cold two-sided member has no baseline, so no tag row can be
        # framed for it — the confirm guard is the only thing standing
        # between the agent and banking a report-silent divergence.
        deck = _deck(tmp_path)
        deck.write_de(
            *DE_PARTS,
            '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-new"\n# Neu\n\n',
        )
        deck.write_en(*EN_PARTS, _localized("s0-new", "en", "New"))
        _, diff = deck.diff()
        assert [(i.key, i.action) for i in diff.items] == [("id:s0-new", "verify_cold")]
        outcome = deck.apply(_decision("id:s0-new", choice="confirm"))
        result = next(r for r in outcome.results if r.key == "id:s0-new")
        assert result.status == "rejected"
        assert "tag sets diverge cross-side" in result.reason
        _, diff = deck.diff()
        assert not diff.is_clean  # nothing was banked

    def test_baseline_carried_divergence_frames_conflict_tags_and_converges(self, tmp_path: Path):
        deck = self._divergent_baseline_deck(tmp_path)
        _, diff = deck.diff()
        assert [(i.key, i.action, i.direction) for i in diff.items] == [
            ("id:s0-m", "conflict_tags", "none")
        ], [(i.key, i.action, i.detail) for i in diff.items]
        assert "the recorded baseline itself carries" in diff.items[0].detail
        entry_before = _ledger_entry(deck, "id:s0-m")
        outcome = deck.apply(_decision("id:s0-m", choice="de"))
        assert outcome.all_applied, outcome.to_payload()
        assert outcome.wrote  # the EN tag line was mirrored...
        assert 'tags=["notes"]' in deck.en_path.read_text(encoding="utf-8")
        # ...but NOTHING was recorded this pass — the old baseline stands.
        assert _ledger_entry(deck, "id:s0-m") == entry_before
        assert entry_before.en_tags == ("voiceover",)
        # Second pass: the move is now attributable (EN off base, DE at base)
        # — an idempotent mechanical mirror that lands and records.
        _, diff = deck.diff()
        assert [(i.key, i.action, i.direction) for i in diff.items] == [
            ("id:s0-m", "mirror_tags", "en_to_de")
        ], [(i.key, i.action, i.detail) for i in diff.items]
        assert deck.apply().all_applied
        deck.assert_converged()

    def test_landed_mirror_tags_never_blesses_the_pending_body_row(self, tmp_path: Path):
        # S3: the mechanical tag mirror lands while the framed body row is
        # unanswered — recording is deferred, the ledger entry stays at its
        # old baseline, and the member re-frames next pass.
        deck = _deck(tmp_path)
        deck.edit_de(self.DE_PLAIN, self.DE_TAGGED)
        deck.edit_de("DE Text", "DE Text NEU")
        entry_before = _ledger_entry(deck, "id:s0-m")
        _, diff = deck.diff()
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:s0-m", "mirror_tags"),
            ("id:s0-m", "translate_edit"),
        }, [(i.key, i.action, i.detail) for i in diff.items]
        outcome = deck.apply()  # no decision: the body row stays pending
        results = [r for r in outcome.results if r.key == "id:s0-m"]
        assert sorted(r.status for r in results) == ["applied", "pending"]
        landed = next(r for r in results if r.status == "applied")
        assert "recording deferred" in landed.reason
        assert 'tags=["voiceover"]' in deck.en_path.read_text(encoding="utf-8")
        assert _ledger_entry(deck, "id:s0-m") == entry_before
        # Next pass: the mirrored twin's fingerprint is off base too, so the
        # pair frames as one verify_translation whose confirm converges.
        _, diff = deck.diff()
        assert [(i.key, i.action, i.direction) for i in diff.items] == [
            ("id:s0-m", "verify_translation", "both")
        ], [(i.key, i.action, i.detail) for i in diff.items]
        outcome = deck.apply(_decision("id:s0-m", choice="confirm"))
        assert outcome.all_applied, outcome.to_payload()
        deck.assert_converged()

    FORK_CELL = '# %% [markdown] tags=["keep"] slide_id="s0-f"\n# gemeinsam\n\n'

    def _forking_deck(self, tmp_path: Path, *, en_tags: str = "voiceover") -> _Deck:
        deck = _Deck(
            tmp_path,
            _build(HEADER_DE, _slide("s0", "de", "Titel"), self.FORK_CELL, _shared_code("x")),
            _build(HEADER_EN, _slide("s0", "en", "Title"), self.FORK_CELL, _shared_code("x")),
        )
        deck.record()
        deck.edit_de(
            '# %% [markdown] tags=["keep"] slide_id="s0-f"\n# gemeinsam',
            '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-f"\n# DE Gabel',
        )
        deck.edit_en(
            '# %% [markdown] tags=["keep"] slide_id="s0-f"\n# gemeinsam',
            f'# %% [markdown] lang="en" tags=["{en_tags}"] slide_id="s0-f"\n# EN fork',
        )
        return deck

    def test_fork_with_divergent_tag_moves_banks_nothing_until_answered(self, tmp_path: Path):
        # F1 fork-time tag check: record_fork is the one row that could
        # legitimize divergent tags as a trusted per-language baseline, so
        # both halves moving their tags differently co-emits a framed
        # conflict_tags — and F2's unresolved-key guard defers the upsert.
        deck = self._forking_deck(tmp_path)
        _, diff = deck.diff()
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:s0-f", "conflict_tags"),
            ("id:s0-f", "record_fork"),
        }, [(i.key, i.action, i.detail) for i in diff.items]
        tag_item = next(i for i in diff.items if i.action == "conflict_tags")
        assert tag_item.direction == "both"
        assert "the forking halves carry divergent tag sets" in tag_item.detail
        entry_before = _ledger_entry(deck, "id:s0-f")
        outcome = deck.apply()  # conflict unanswered
        landed = next(r for r in outcome.results if r.status != "pending")
        assert "recording deferred" in landed.reason
        assert _ledger_entry(deck, "id:s0-f") == entry_before  # nothing banked
        # Answering the conflict mirrors the tag line, but an ANSWERED
        # conflict_tags still defers same-key recordings (the review's
        # critical finding: a co-landed row must never bank state a framed
        # tag row's co-emission rule may have suppressed) — record_fork
        # reports "deferred" and the tag-consistent fork banks on the NEXT
        # pass, exactly as the design's fork test plan words it.
        outcome = deck.apply(_decision("id:s0-f", choice="de"))
        fork_result = next(r for r in outcome.results if r.action == "record_fork")
        assert fork_result.status == "deferred", outcome.to_payload()
        assert "recording deferred" in fork_result.reason
        en = deck.en_path.read_text(encoding="utf-8")
        assert 'tags=["notes"]' in en  # the DE tag set mirrored...
        assert "EN fork" in en  # ...and the forked EN body survived
        assert _ledger_entry(deck, "id:s0-f") == entry_before  # not yet banked
        outcome = deck.apply()  # second pass: the clean fork records
        assert outcome.all_applied, outcome.to_payload()
        entry = _ledger_entry(deck, "id:s0-f")
        assert entry.langness == "localized"
        assert entry.de_tags == entry.en_tags == ("notes",)
        deck.assert_converged()

    def test_conflict_tags_answer_mirrors_only_the_tag_line(self, tmp_path: Path):
        # S2 executor regression: the de/en answer must mirror the TAG SET
        # only — never a whole-cell propagate that would overwrite the
        # twin's translated body with the other language.
        deck = self._divergent_baseline_deck(tmp_path)
        outcome = deck.apply(_decision("id:s0-m", choice="en"))
        assert outcome.all_applied, outcome.to_payload()
        de = deck.de_path.read_text(encoding="utf-8")
        assert 'tags=["voiceover"]' in de  # the EN tag set landed on DE
        assert "# DE Text" in de  # the translated body was untouched
        assert "EN text" not in de  # no whole-cell copy
        en = deck.en_path.read_text(encoding="utf-8")
        assert "# EN text" in en
        assert "DE Text" not in en

    def test_body_answer_lands_on_a_twin_carrying_the_mirrored_tags(self, tmp_path: Path):
        # S1: a one-sided DE tag+body edit resolves in ONE pass — the
        # mechanical mirror_tags executes before the translate_edit body
        # answer (same ordered loop), so the EN cell comes out with BOTH
        # the mirrored tags and the new body, and the banked entry is in
        # tag parity — never the silently-divergent #615 end state.
        deck = _deck(tmp_path)
        deck.edit_de(self.DE_PLAIN, self.DE_TAGGED)
        deck.edit_de("DE Text", "DE Text NEU")
        _, diff = deck.diff()
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:s0-m", "mirror_tags"),
            ("id:s0-m", "translate_edit"),
        }, [(i.key, i.action, i.detail) for i in diff.items]
        outcome = deck.apply(_decision("id:s0-m", body="# EN text NEW"))
        assert outcome.all_applied, outcome.to_payload()
        en = deck.en_path.read_text(encoding="utf-8")
        header_line = next(line for line in en.splitlines() if 'slide_id="s0-m"' in line)
        assert 'tags=["voiceover"]' in header_line  # the mirrored tags...
        assert "# EN text NEW" in en  # ...AND the answered body, one cell
        entry = _ledger_entry(deck, "id:s0-m")
        assert entry.de_tags == entry.en_tags == ("voiceover",)
        deck.assert_converged()

    def test_fork_with_one_sided_tag_move_lands_in_one_pass(self, tmp_path: Path):
        # F1 fork-time tag check, mechanical half: only the DE half changed
        # its tags off the shared base, so the move is still attributable —
        # mirror_tags co-emits with record_fork and BOTH land in one pass
        # (two mechanical rows on one key have no decision-keying collision;
        # no deferral).
        deck = self._forking_deck(tmp_path, en_tags="keep")
        _, diff = deck.diff()
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:s0-f", "mirror_tags"),
            ("id:s0-f", "record_fork"),
        }, [(i.key, i.action, i.detail) for i in diff.items]
        outcome = deck.apply()
        assert outcome.all_applied, outcome.to_payload()
        fork_result = next(r for r in outcome.results if r.action == "record_fork")
        assert fork_result.status == "recorded", outcome.to_payload()
        en = deck.en_path.read_text(encoding="utf-8")
        assert 'tags=["notes"]' in en  # the DE tag set mirrored...
        assert "EN fork" in en  # ...and the forked EN body survived
        entry = _ledger_entry(deck, "id:s0-f")
        assert entry.langness == "localized"
        assert entry.de_tags == entry.en_tags == ("notes",)
        deck.assert_converged()

    def test_conflict_tags_suppresses_conflict_owner_until_answered(self, tmp_path: Path):
        # Adversarial review of #615: a framed conflict_owner shares
        # conflict_tags' exact de/en vocabulary on the same handle — one
        # decision would silently execute BOTH mirrors. The framed tag row
        # must suppress the owner AND body rows this pass; the answer
        # mirrors only the tag line; the suppressed aspects re-frame next.
        de = _build(HEADER_DE, _slide("s0", "de", "Titel"), _slide("s1", "de", "Zwei"))
        en = _build(HEADER_EN, _slide("s0", "en", "Title"), _slide("s1", "en", "Two"))
        deck = _Deck(tmp_path, de, en)
        vo_dir = tmp_path / "voiceover"
        vo_dir.mkdir()
        de_comp = vo_dir / "voiceover_t.de.py"
        en_comp = vo_dir / "voiceover_t.en.py"
        de_comp.write_text(
            _build(
                '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-vo" '
                'for_slide="s0"\n#\n# - DE Notiz\n\n'
            ),
            encoding="utf-8",
        )
        en_comp.write_text(
            _build(
                '# %% [markdown] lang="en" tags=["notes"] slide_id="s0-vo" '
                'for_slide="s0"\n#\n# - EN note\n\n'
            ),
            encoding="utf-8",
        )
        deck.record()
        # DE: owner AND tags AND body all move; EN: tags and body move
        # differently — every aspect of the member diverges cross-side.
        de_comp.write_text(
            de_comp.read_text(encoding="utf-8")
            .replace('tags=["notes"]', 'tags=["voiceover"]')
            .replace('for_slide="s0"', 'for_slide="s1"')
            .replace("- DE Notiz", "- DE Notiz v2"),
            encoding="utf-8",
        )
        en_comp.write_text(
            en_comp.read_text(encoding="utf-8")
            .replace('tags=["notes"]', 'tags=["alt"]')
            .replace("- EN note", "- EN note v2"),
            encoding="utf-8",
        )
        _, diff = deck.diff()
        assert [(i.key, i.action, i.direction) for i in diff.items] == [
            ("id:s0-vo", "conflict_tags", "both")
        ], [(i.key, i.action, i.detail) for i in diff.items]
        entry_before = _ledger_entry(deck, "id:s0-vo")
        outcome = deck.apply(_decision("id:s0-vo", choice="de"))
        assert outcome.all_applied, outcome.to_payload()
        en_after = en_comp.read_text(encoding="utf-8")
        assert 'tags=["voiceover"]' in en_after  # ONLY the tag line mirrored
        assert 'for_slide="s0"' in en_after  # the owner was NOT mirrored
        assert "- EN note v2" in en_after  # the body was untouched
        assert _ledger_entry(deck, "id:s0-vo") == entry_before  # nothing recorded
        _, diff = deck.diff()  # next pass: the suppressed aspects re-frame
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:s0-vo", "conflict_owner"),
            ("id:s0-vo", "verify_translation"),
        }, [(i.key, i.action, i.detail) for i in diff.items]

    HZ_A = '# %% [markdown] tags=["alt"]\n# HZ A\n\n'
    HZ_B = '# %% [markdown] tags=["alt"]\n# HZ B\n\n'

    def test_pool_conflict_tags_answer_touches_only_the_target_slot(self, tmp_path: Path):
        # The pool path end-to-end: answering the pos:-keyed conflict_tags
        # mirrors ONE slot's tag line (the neighboring slot byte-identical),
        # records nothing that pass, and the NEXT pass converges through the
        # pool residue branch (record_tags — the pool twin of
        # _tags_only_change) instead of mis-framing translate_edit.
        deck = _Deck(
            tmp_path,
            _build(self.HZ_A, self.HZ_B, HEADER_DE, _slide("s0", "de", "Titel")),
            _build(self.HZ_A, self.HZ_B, HEADER_EN, _slide("s0", "en", "Title")),
        )
        deck.record()
        deck.edit_de('tags=["alt"]\n# HZ A', 'tags=["beta"]\n# HZ A')
        deck.edit_en('tags=["alt"]\n# HZ A', 'tags=["gamma"]\n# HZ A')
        _, diff = deck.diff()
        assert [(i.key, i.action, i.direction) for i in diff.items] == [
            ("pos:~header/markdown/0", "conflict_tags", "both")
        ], [(i.key, i.action, i.detail) for i in diff.items]
        slot_a_before = _ledger_entry(deck, "pos:~header/markdown/0")
        slot_b_before = _ledger_entry(deck, "pos:~header/markdown/1")
        de_before = deck.de_path.read_text(encoding="utf-8")
        en_before = deck.en_path.read_text(encoding="utf-8")
        outcome = deck.apply(_decision("pos:~header/markdown/0", choice="de"))
        assert outcome.all_applied, outcome.to_payload()
        # ONLY the target slot's EN tag line changed — byte-precise.
        assert deck.de_path.read_text(encoding="utf-8") == de_before
        assert deck.en_path.read_text(encoding="utf-8") == en_before.replace(
            'tags=["gamma"]', 'tags=["beta"]'
        )
        # ...and NOTHING was recorded this pass.
        assert _ledger_entry(deck, "pos:~header/markdown/0") == slot_a_before
        assert _ledger_entry(deck, "pos:~header/markdown/1") == slot_b_before
        # Next pass: the slot's tags-only movement is mechanical (the pool
        # residue branch), never a translate_edit of an untouched body.
        _, diff = deck.diff()
        assert [(i.key, i.action, i.direction) for i in diff.items] == [
            ("pos:~header/markdown/0", "record_tags", "both")
        ], [(i.key, i.action, i.detail) for i in diff.items]
        assert deck.apply().all_applied
        deck.assert_converged()

    def test_deferred_stamp_keeps_the_divergent_pos_baseline(self, tmp_path: Path):
        # _sweep_migrated_pos regression (adversarial review of #615): a
        # shared positional cell RECORDED with a base-carried cross-side
        # divergence gets a slide_id stamped on the DE side only. The stamp
        # is mechanical and lands, but the pending_divergence row on the
        # same member stays pending — recording defers, and the migration
        # sweep must NOT destroy the pos: entry: it IS the surviving old
        # baseline evidencing the divergence.
        deck = _Deck(
            tmp_path,
            _build(
                HEADER_DE,
                _slide("s0", "de", "Titel"),
                '# %% tags=["keep", "alt"]\nx = 1\n\n',  # header (tags) diverge...
                _localized("s0-m", "de", "DE Text"),
            ),
            _build(
                HEADER_EN,
                _slide("s0", "en", "Title"),
                '# %% tags=["keep"]\nx = 1\n\n',  # ...the bodies are identical
                _localized("s0-m", "en", "EN text"),
            ),
        )
        deck.record()  # the baseline banks the in-flight divergence
        deck.edit_de(
            '# %% tags=["keep", "alt"]\nx = 1',
            '# %% tags=["keep", "alt"] slide_id="x-cell"\nx = 1',
        )
        _, diff = deck.diff()
        assert {(i.key, i.action) for i in diff.items} == {
            ("id:x-cell", "stamp_twin_id"),
            ("id:x-cell", "pending_divergence"),
        }, [(i.key, i.action, i.detail) for i in diff.items]
        outcome = deck.apply()  # no decisions: the divergence stays pending
        results = {r.action: r for r in outcome.results}
        assert results["stamp_twin_id"].status == "applied"
        assert "recording deferred" in results["stamp_twin_id"].reason
        assert results["pending_divergence"].status == "pending"
        assert 'slide_id="x-cell"' in deck.en_path.read_text(encoding="utf-8")
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(deck.de_path))
        members = ledger.decks["slides_t"].members
        assert "pos:s0/code/0" in members, sorted(members)  # the baseline SURVIVED
        entry = members["pos:s0/code/0"].entry
        assert entry.de_fp != entry.en_fp  # ...still carrying the divergence
        assert "id:x-cell" not in members  # the stamp banked nothing yet
        _, diff = deck.diff()  # the divergence is still framed, never silent
        assert not diff.is_clean
        assert any(i.action == "pending_divergence" for i in diff.items), [
            (i.key, i.action, i.detail) for i in diff.items
        ]
