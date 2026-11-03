"""Brute-force reorder x edit sweep of one sync group (#1130).

One slide group holds two id'd voiceovers and shared un-id'd code cells.
Each case:
- records the bilingual pair;
- has EN reorder the group's cells;
- edits a subset of the code cells, on EN (the reordering half) or on DE (the
  other half).

It then drives ``report -> apply`` through the real pre-write verify gate
(``apply_verify_gate``), answering every placement / order question ``en``
(EN is the half that moved), until the diff is clean or ``MAX_PASSES`` runs out.

A case **converges** when:
- the diff is clean;
- no apply errored;
- every final code body (base bodies with the case's edits applied) appears
  exactly once on each half, so no edit was lost and no cell was duplicated.

Anything else is a failure kind: ``livelock``, ``apply_error`` or
``lost_or_duplicated``. ``sync_sweep_baseline.json`` records today's failures
as a ratchet; regenerate it with ``python scripts/update_sync_sweep_baseline.py``.
"""

from __future__ import annotations

import itertools
import json
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from clm.slides import doc_apply, doc_ledger
from clm.slides.doc_lenses import load_bundle
from clm.slides.sync_diff import diff_outcome
from clm.slides.sync_verify import apply_verify_gate

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


def run_case(case: Case) -> tuple[str, list[list[tuple[str, str]]]]:
    """``(status, passes)``: ``converged`` or a failure kind, plus each
    pass's framed ``(key, action)`` rows."""
    base, code = DOMAINS[case.domain]
    edited = _edited(code, case.edits)
    passes: list[list[tuple[str, str]]] = []
    with tempfile.TemporaryDirectory() as tmp:
        de_path = Path(tmp) / "slides_t.de.py"
        en_path = Path(tmp) / "slides_t.en.py"
        de_path.write_text(_text("de", base, code), encoding="utf-8")
        en_path.write_text(_text("en", base, code), encoding="utf-8")
        _record(de_path, en_path)
        en_code = edited if case.edit_side == "en" else code
        en_path.write_text(_text("en", case.order, en_code), encoding="utf-8")
        if case.edit_side == "de":
            de_path.write_text(_text("de", base, edited), encoding="utf-8")
        for _ in range(MAX_PASSES):
            _, diff = _diff(de_path, en_path)
            if diff.is_clean:
                for path in (de_path, en_path):
                    text = path.read_text(encoding="utf-8")
                    if any(text.count(f"\n{body}\n") != 1 for body in edited.values()):
                        return "lost_or_duplicated", passes
                return "converged", passes
            passes.append([(i.key, i.action) for i in diff.items])
            decisions = {
                i.key: doc_apply.Decision(key=i.key, choice="en")
                for i in diff.items
                if i.action in ("pool_placement_divergence", "order_decision")
            }
            if _apply(de_path, en_path, decisions).error is not None:
                return "apply_error", passes
    return "livelock", passes


def load_baseline() -> dict[str, str]:
    baseline: dict[str, str] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return baseline
