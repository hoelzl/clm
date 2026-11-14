"""Brute-force reorder x edit sweep of one sync group (#1130).

One slide group holds two id'd voiceovers and shared un-id'd code cells.
Each case:
- records the bilingual pair;
- has EN reorder the group's cells;
- edits a subset of the code cells, on EN (the reordering half) or on DE (the
  other half).

It then drives ``report -> apply`` through the real pre-write verify gate
(``apply_verify_gate``) until the diff is clean or ``MAX_PASSES`` runs out.
It answers every question a careful agent would answer, so a failure is the
engine's, not the harness's:
- placement / order questions: ``en`` (EN is the half that moved);
- ``conflict_shared``: the half holding the case's edit.

Any other answerable row is reported as ``unanswered_question``, so a new
question kind can never pass for a livelock.

A case **converges** when:
- the diff is clean;
- no apply errored;
- every final code body (base bodies with the case's edits applied) appears
  exactly once on each half, so no edit was lost and no cell was duplicated;
- both halves end in EN's new order, so no move was undone.

Anything else is a failure kind:
- ``livelock_withheld``: the last pass's pre-write verify withheld rows, so
  the gate rejects the same rows every pass (#1130);
- ``livelock_stalled``: the last pass withheld nothing, yet the diff never
  turns clean;
- ``misplaced``: the diff turns clean with every edit kept, but the cells
  are not in EN's order. The engine reports success while it silently undid
  a move (#1130);
- ``unanswered_question``, ``apply_error`` or ``lost_or_duplicated``.

``sync_sweep_baseline.json`` records today's failures
as a ratchet; regenerate it with ``python scripts/update_sync_sweep_baseline.py``.
"""

from __future__ import annotations

import itertools
import json
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from clm.slides import doc_apply, doc_ledger
from clm.slides.doc_lenses import load_bundle
from clm.slides.sync_diff import diff_outcome
from clm.slides.sync_verify import apply_verify_gate
from tests.slides.sync_engines import POSITIONAL, Engine, answer_identify

BASELINE_PATH = Path(__file__).with_name("sync_sweep_baseline.json")
MAX_PASSES = 6

HEADER = {
    "de": "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n",
    "en": "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n",
}

#: Domain name -> (base order, base code bodies). ``a`` / ``b`` are id'd
#: voiceovers; every other name is an un-id'd shared code cell.
DOMAINS: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "four": (("a", "c1", "b", "c2"), {"c1": "c1 = 1", "c2": "c2 = 2"}),
    "five": (("x", "a", "c1", "b", "c2"), {"x": "x = 0", "c1": "c1 = 1", "c2": "c2 = 2"}),
}


@dataclass(frozen=True)
class Case:
    domain: str
    edit_side: str  # "en" (the reordering half) or "de" (the other half)
    order: tuple[str, ...]  # EN's new order
    edits: tuple[str, ...]  # code cells edited on ``edit_side``

    @property
    def id(self) -> str:
        return f"{self.domain}|{self.edit_side}|{' '.join(self.order)}|{','.join(self.edits)}"


def cases(domain: str) -> Iterator[Case]:
    base, code = DOMAINS[domain]
    names = sorted(code)
    for side in ("en", "de"):
        for order in itertools.permutations(base):
            for r in range(len(names) + 1):
                for edits in itertools.combinations(names, r):
                    yield Case(domain, side, order, edits)


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _text(lang: str, order: tuple[str, ...], code: dict[str, str]) -> str:
    cells = {
        "a": f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-a"\n#\n# - A {lang}\n\n',
        "b": f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-b"\n#\n# - B {lang}\n\n',
        **{name: f"# %%\n{body}\n\n" for name, body in code.items()},
    }
    parts = [
        HEADER[lang],
        _slide("intro", lang, "T"),
        *(cells[n] for n in order),
        _slide("next", lang, "N"),
    ]
    return "".join(parts).rstrip("\n") + "\n"


def _edited(code: dict[str, str], edits: tuple[str, ...]) -> dict[str, str]:
    return {name: (f"{body}0" if name in edits else body) for name, body in code.items()}


def _diff(de_path: Path, en_path: Path):
    bundle = load_bundle(de_path, en_path)
    ledger = doc_ledger.load(doc_ledger.ledger_path_for(de_path))
    deck_ledger = ledger.decks.get(doc_ledger.deck_key_for(de_path))
    base = doc_ledger.baseline_from_ledger(deck_ledger) if deck_ledger else None
    return bundle, diff_outcome(bundle.outcome, base)


def _record(de_path: Path, en_path: Path) -> None:
    bundle = load_bundle(de_path, en_path)
    assert bundle.outcome.deck is not None
    ledger_path = doc_ledger.ledger_path_for(de_path)
    ledger = doc_ledger.load(ledger_path)
    doc_ledger.record_deck_snapshot(
        ledger, doc_ledger.deck_key_for(de_path), bundle.outcome.deck, provenance="record"
    )
    doc_ledger.save(ledger, ledger_path)


def _apply(de_path: Path, en_path: Path, decisions: dict[str, doc_apply.Decision]):
    bundle, diff = _diff(de_path, en_path)
    assert bundle.outcome.deck is not None
    ledger_path = doc_ledger.ledger_path_for(de_path)
    ledger = doc_ledger.load(ledger_path)
    outcome = doc_apply.apply_deck(
        bundle,
        bundle.outcome.deck,
        diff,
        ledger,
        doc_ledger.deck_key_for(de_path),
        decisions=decisions,
        verify_gate=apply_verify_gate(de_path, en_path, "#"),
    )
    if outcome.error is None and outcome.ledger_changed:
        doc_ledger.save(ledger, ledger_path)
    return outcome


#: Fixed answers by action; ``conflict_shared`` takes the edited half.
_ANSWERS = {"pool_placement_divergence": "en", "order_decision": "en"}


def _final_order(text: str, bodies: dict[str, str]) -> tuple[str, ...]:
    """The group's cells by name, in file order."""
    markers = {
        "a": 'slide_id="vo-a"',
        "b": 'slide_id="vo-b"',
        **{name: f"\n{body}\n" for name, body in bodies.items()},
    }
    return tuple(sorted(markers, key=lambda name: text.index(markers[name])))


#: ``trace(pass_index, rows, outcome_payload, de_text, en_text)``, called
#: after each pass's apply (``scripts/update_sync_sweep_baseline.py --trace``).
Tracer = Callable[[int, list[tuple[str, str]], dict, str, str], None]


def run_case(
    case: Case, trace: Tracer | None = None, engine: Engine = POSITIONAL
) -> tuple[str, list[list[tuple[str, str]]]]:
    """``(status, passes)``: ``converged`` or a failure kind, plus each
    pass's framed ``(key, action)`` rows. ``engine`` is the engine under
    test (``sync_engines``)."""
    base, code = DOMAINS[case.domain]
    edited = _edited(code, case.edits)
    passes: list[list[tuple[str, str]]] = []
    withheld = False
    with tempfile.TemporaryDirectory() as tmp:
        de_path = Path(tmp) / "slides_t.de.py"
        en_path = Path(tmp) / "slides_t.en.py"
        base_texts = {lang: _text(lang, base, code) for lang in ("de", "en")}
        de_path.write_text(base_texts["de"], encoding="utf-8")
        en_path.write_text(base_texts["en"], encoding="utf-8")
        _record(de_path, en_path)

        def now_of(body: str, side: str) -> str:
            """What ``side`` made of the recorded code body ``body``."""
            if side != case.edit_side:
                return body
            return next((edited[n] for n, b in code.items() if b == body), body)

        en_code = edited if case.edit_side == "en" else code
        en_path.write_text(_text("en", case.order, en_code), encoding="utf-8")
        if case.edit_side == "de":
            de_path.write_text(_text("de", base, edited), encoding="utf-8")
        carry: dict[str, doc_apply.Decision] = {}  # answers kept while nothing was written
        for _ in range(MAX_PASSES):
            report = engine.report(de_path, en_path, carry)
            if report.error is not None:
                return "apply_error", passes
            if report.clean:
                for path in (de_path, en_path):
                    text = path.read_text(encoding="utf-8")
                    if any(text.count(f"\n{body}\n") != 1 for body in edited.values()):
                        return "lost_or_duplicated", passes
                    if _final_order(text, edited) != case.order:
                        return "misplaced", passes
                return "converged", passes
            passes.append([(i.key, i.action) for i in report.items])
            decisions: dict[str, doc_apply.Decision] = dict(carry)
            for item in report.items:
                if item.answered:
                    continue
                choice = _ANSWERS.get(item.action, case.edit_side)
                if item.action in _ANSWERS or item.action == "conflict_shared":
                    decisions[item.key] = doc_apply.Decision(key=item.key, choice=choice)
                elif item.action == "identify":
                    decisions[f"{item.key}@{item.side}"] = answer_identify(
                        item, base_texts, {"de": de_path, "en": en_path}, now_of
                    )
                elif item.answers:
                    return "unanswered_question", passes
            applied = engine.apply(de_path, en_path, decisions)
            if applied.error is not None:
                return "apply_error", passes
            carry = {} if applied.wrote else decisions
            withheld = applied.withheld
            if trace is not None:
                trace(
                    len(passes) - 1,
                    passes[-1],
                    applied.payload,
                    de_path.read_text(encoding="utf-8"),
                    en_path.read_text(encoding="utf-8"),
                )
    return ("livelock_withheld" if withheld else "livelock_stalled"), passes


def final_order(text: str, case: Case) -> tuple[str, ...]:
    """The group's cells by name in ``text``; an edited body shows as ``name'``."""
    _, code = DOMAINS[case.domain]
    markers = {"a": 'slide_id="vo-a"', "b": 'slide_id="vo-b"'}
    for name, body in code.items():
        for variant, shown in ((body, name), (f"{body}0", f"{name}'")):
            if f"\n{variant}\n" in text:
                markers[shown] = f"\n{variant}\n"
    return tuple(sorted(markers, key=lambda name: text.index(markers[name])))


def load_baseline() -> dict[str, str]:
    baseline: dict[str, str] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return baseline
