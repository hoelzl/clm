"""The two sync engines behind one interface, for the brute-force harnesses.

Phase C of ``docs/claude/design/sync-sequence-core.md`` runs the probe
(``sync_probe.py``), the sweep (``sync_sweep.py``) and the laws
(``sync_laws.py``) against both engines. Each harness drives an engine the
way an agent drives ``clm slides sync``: record, ``report``, answer the
questions, ``apply``, until the report is clean. :class:`Engine` is that loop's
view of an engine:

- :data:`POSITIONAL` — today's engine (``sync_diff`` / ``doc_apply``, through
  the real pre-write verify gate);
- :data:`SEQUENCE` — the sequence core's pass (``doc_pass``), as
  ``CLM_SYNC_CORE=sequence`` runs it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from clm.slides import doc_apply, doc_ledger, doc_pass
from clm.slides.doc_lenses import load_bundle, read_file
from clm.slides.sync_diff import diff_outcome
from clm.slides.sync_verify import apply_verify_gate


@dataclass(frozen=True)
class Item:
    """One report row: its handle, action, and the answers it takes."""

    key: str
    action: str
    answers: tuple[str, ...]
    #: a question no answer resolves (the author edits the files)
    manual: bool = False
    #: the half an ``identify`` question is about
    side: str | None = None
    #: a question the answers passed to :meth:`Engine.report` already answer
    answered: bool = False


@dataclass(frozen=True)
class Report:
    clean: bool
    items: tuple[Item, ...]
    #: the engine refused the deck, or a renderer assertion failed
    error: str | None = None


@dataclass(frozen=True)
class Applied:
    error: str | None
    #: rows the positional engine's pre-write verify held back
    withheld: bool = False
    #: the positional engine's apply payload (``ApplyOutcome.to_payload``)
    payload: dict = field(default_factory=dict)
    #: ``False``: the sequence core held the deck and wrote nothing; the
    #: answers stay valid for the next pass, which asks what they led to
    wrote: bool = True


def record(de_path: Path, en_path: Path) -> None:
    """``clm slides sync record`` (the same verb for both engines)."""
    bundle = load_bundle(de_path, en_path)
    assert bundle.outcome.deck is not None
    ledger_path = doc_ledger.ledger_path_for(de_path)
    ledger = doc_ledger.load(ledger_path)
    doc_ledger.record_deck_snapshot(
        ledger, doc_ledger.deck_key_for(de_path), bundle.outcome.deck, provenance="record"
    )
    doc_ledger.save(ledger, ledger_path)


class Engine:
    name = ""

    def report(
        self,
        de_path: Path,
        en_path: Path,
        decisions: Mapping[str, doc_apply.Decision] | None = None,
    ) -> Report:
        """The report; with ``decisions``, the questions still open given
        those answers (the sequence core's ``apply --dry-run --decisions``;
        the positional engine ignores them)."""
        raise NotImplementedError

    def apply(
        self, de_path: Path, en_path: Path, decisions: Mapping[str, doc_apply.Decision]
    ) -> Applied:
        raise NotImplementedError


class _Positional(Engine):
    name = "positional"

    def _diff(self, de_path: Path, en_path: Path):
        bundle = load_bundle(de_path, en_path)
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(de_path))
        deck_ledger = ledger.decks.get(doc_ledger.deck_key_for(de_path))
        base = doc_ledger.baseline_from_ledger(deck_ledger) if deck_ledger else None
        return bundle, diff_outcome(bundle.outcome, base)

    def report(
        self,
        de_path: Path,
        en_path: Path,
        decisions: Mapping[str, doc_apply.Decision] | None = None,
    ) -> Report:
        _, diff = self._diff(de_path, en_path)
        items = tuple(
            Item(
                i.key,
                i.action,
                doc_apply.item_answers(i),
                manual=bool(doc_apply.decision_vocabulary(i.action))
                and not doc_apply.item_answers(i),
            )
            for i in diff.items
        )
        return Report(diff.is_clean, items)

    def apply(
        self, de_path: Path, en_path: Path, decisions: Mapping[str, doc_apply.Decision]
    ) -> Applied:
        bundle, diff = self._diff(de_path, en_path)
        assert bundle.outcome.deck is not None
        ledger_path = doc_ledger.ledger_path_for(de_path)
        ledger = doc_ledger.load(ledger_path)
        outcome = doc_apply.apply_deck(
            bundle,
            bundle.outcome.deck,
            diff,
            ledger,
            doc_ledger.deck_key_for(de_path),
            decisions=dict(decisions),
            verify_gate=apply_verify_gate(de_path, en_path, "#"),
        )
        if outcome.error is None and outcome.ledger_changed:
            doc_ledger.save(ledger, ledger_path)
        payload = outcome.to_payload()
        return Applied(outcome.error, bool(payload.get("verify_withheld")), payload)


class _Sequence(Engine):
    name = "sequence"

    def _pass(self, de_path: Path, en_path: Path, decisions=()):
        bundle = load_bundle(de_path, en_path)
        ledger_path = doc_ledger.ledger_path_for(de_path)
        ledger = doc_ledger.load(ledger_path)
        key = doc_ledger.deck_key_for(de_path)
        return bundle, ledger, key, doc_pass.run_pass(bundle, ledger.decks.get(key), decisions)

    def report(
        self,
        de_path: Path,
        en_path: Path,
        decisions: Mapping[str, doc_apply.Decision] | None = None,
    ) -> Report:
        given = list(decisions.values()) if decisions else []
        _, _, _, result = self._pass(de_path, en_path, given)
        error = result.refusal or result.error
        items = tuple(
            Item(r.key, r.action, r.answers, r.manual, r.side, r.answer is not None)
            for r in result.rows
        )
        return Report(result.is_clean and not given, items, error)

    def apply(
        self, de_path: Path, en_path: Path, decisions: Mapping[str, doc_apply.Decision]
    ) -> Applied:
        bundle, ledger, key, result = self._pass(de_path, en_path, list(decisions.values()))
        if result.refusal or result.error:
            return Applied(result.refusal or result.error)
        if result.held:
            return Applied(None, wrote=False)  # nothing written
        doc_pass.land(bundle, ledger, key, result)
        doc_ledger.save(ledger, doc_ledger.ledger_path_for(de_path))
        return Applied(None)


def _bodies(text: str, lang: str) -> list[str]:
    _, cells = read_file(text, lang, "deck", "#")  # type: ignore[arg-type]
    return [cell.body.strip("\n") for cell in cells]


def answer_identify(
    item: Item,
    base_texts: Mapping[str, str],
    paths: Mapping[str, Path],
    now_of: Callable[[str, str], str],
) -> doc_apply.Decision:
    """How a careful agent answers ``identify`` (``seq:<n>`` on a half):
    the candidate whose body is what that half made of the recorded cell's
    body (``now_of(body, side)``), else ``removed``. ``base_texts`` are the
    deck files as recorded (the harnesses' decks have no companion, so the
    n-th entry of the sequence is the file's n-th cell)."""
    assert item.side is not None and item.key.startswith("seq:")
    side = item.side
    recorded = _bodies(base_texts[side], side)[int(item.key[4:])]
    want = now_of(recorded, side)
    current = _bodies(paths[side].read_text(encoding="utf-8"), side)
    choice = "removed"
    for handle in item.answers:
        if handle.startswith("new:") and current[int(handle.rsplit(":", 1)[1])] == want:
            choice = handle
            break
    return doc_apply.Decision(key=item.key, choice=choice, side=side, action="identify")


POSITIONAL: Engine = _Positional()
SEQUENCE: Engine = _Sequence()
ENGINES = {e.name: e for e in (POSITIONAL, SEQUENCE)}
