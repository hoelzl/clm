"""Exhaustive small-scope check of the sync reference model across passes.

``scripts/sync_model.py`` makes progress in a pass whose questions are not
all answered: it applies and records what no open question touches and holds
the rest. This script checks that such a pass is safe, on every small input
and without an oracle. (``scripts/sync_model_laws.py`` checks single passes.)

A judge answers every question by a fixed rule and remembers its answers per
question, so the same question gets the same answer in a later pass. The
*direct* run answers everything in one pass. Every other run leaves a subset
of those questions open in its first pass, and answers the rest afterwards.

The laws:

1. complete     with every question answered, one pass ends it: the halves
                agree, and a further pass asks nothing and changes nothing
2. fixpoint     after a pass with open questions, a pass that answers nothing
                changes nothing: not a half, not the record. (The engine's
                counterpart is reading its own write as an author edit.)
3. held         what an open question is about stays as it was: the recorded
                entry, and the cells an identity question offers
4. confluent    leaving questions open and answering them later gives the
                same two halves as answering everything at once: the same
                cells, and the recorded ones in the same order. Where cells
                the two halves added end up side by side, their order may
                differ (the single-pass check has the same exception)
5. no new ask   a later pass asks nothing the direct run was not asked

Decks are words over two id'd localized cells (``A``, ``B``) and un-id'd
shared cells with contents ``x``, ``y``, ``z``, as in the single-pass check.
Here a half may also drop or add an id'd cell, so every kind of question
arises. Both halves change.

    python scripts/sync_model_passes.py [--base 2] [--half 3] [--workers N]

Exits 1 on a violation.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_model import BaseMember, Cell, Question, merge  # noqa: E402

IDS = ("A", "B")
BODIES = ("x", "y", "z")
Word = tuple[str, ...]
State = tuple[list[BaseMember], list[Cell], list[Cell]]

#: One fixed answer per kind of question; ``identify`` names a candidate.
STYLES = {
    "first": {
        "order": "de",
        "conflict_shared": "de",
        "conflict_copies": "distinct",
        "remove_vs_edit": "remove",
        "remove_localized_side": "remove",
        "translate_new": "body",
        "identify": 0,
    },
    "last": {
        "order": "en",
        "conflict_shared": "en",
        "conflict_copies": "de",
        "remove_vs_edit": "keep",
        "remove_localized_side": "remove",
        "translate_new": "body",
        "identify": -1,
    },
    "removed": {
        "order": "de",
        "conflict_shared": "en",
        "conflict_copies": "en",
        "remove_vs_edit": "keep",
        "remove_localized_side": "remove",
        "translate_new": "body",
        "identify": "removed",
    },
}
MAX_PASSES = 6
#: with more questions than this, the subsets left open are chosen by kind
ALL_SUBSETS_UP_TO = 4


def words(max_len: int, ids: Word, bodies: Word = BODIES):
    for n in range(len(ids), max_len + 1):
        for places in itertools.permutations(range(n), len(ids)):
            for fill in itertools.product(bodies, repeat=n - len(ids)):
                word: list[str | None] = [None] * n
                for t, p in zip(ids, places, strict=True):
                    word[p] = t
                rest = iter(fill)
                yield tuple(w if w is not None else next(rest) for w in word)


def all_words(max_len: int, bodies: Word = BODIES) -> list[Word]:
    """Every word of up to ``max_len`` cells, with any of the id'd cells."""
    out: set[Word] = set()
    for k in range(len(IDS) + 1):
        for ids in itertools.combinations(IDS, k):
            out.update(words(max_len, ids, bodies))
    return sorted(out)


def start(base: Word, de: Word, en: Word) -> State:
    def cells(word: Word, lang: str) -> list[Cell]:
        return [
            Cell(t, "vo", f"{t} {lang}", True, (lang, j))
            if t in IDS
            else Cell(None, "code", t, False, (lang, j))
            for j, t in enumerate(word)
        ]

    recorded = [
        BaseMember(t, "vo", True, f"{t} de", f"{t} en", ("base", i))
        if t in IDS
        else BaseMember(None, "code", False, t, t, ("base", i))
        for i, t in enumerate(base)
    ]
    return recorded, cells(de, "de"), cells(en, "en")


def labels(cells: list[Cell]) -> tuple[str, ...]:
    return tuple(f"id:{c.sid}" if c.sid else f"body:{c.body}" for c in cells)


def key_of(q: Question) -> tuple:
    """What makes two questions the same one. Byte-identical recorded cells
    share their questions: which of them is which is not a fact about the
    deck. Only ``identify`` tells them apart, by what it offers."""
    return (q.kind, q.side, q.mark if q.kind == "identify" else q.about)


class Judge:
    """Answers by a fixed rule, and the same question the same way again."""

    def __init__(self, style: str) -> None:
        self.rule = STYLES[style]
        self.memo: dict[tuple, object] = {}
        self.cells: dict[str, list[Cell]] = {}
        #: keys the judge may answer in this pass (``None``: all of them)
        self.allow: set[tuple] | None = None
        #: once set, a question that is not in ``memo`` is a new one
        self.replaying = False
        self.new_asks: list[tuple] = []
        self.lost_candidates: list[tuple] = []

    def __call__(self, q: Question):
        key = key_of(q)
        if self.allow is not None and key not in self.allow:
            return None
        if q.kind != "identify":
            if key not in self.memo:
                if self.replaying:
                    self.new_asks.append(key)
                self.memo[key] = self.rule[q.kind]
            return self.memo[key]
        assert q.side is not None
        marks = [self.cells[q.side][j].mark for j in q.candidates]
        if key not in self.memo:
            if self.replaying:
                self.new_asks.append(key)
            pick = self.rule["identify"]
            self.memo[key] = "removed" if pick == "removed" else marks[pick]
        want = self.memo[key]
        if want == "removed":
            return "removed"
        if want not in marks:  # the cell it named before is no longer offered
            self.lost_candidates.append(key)
            return "removed"
        return q.candidates[marks.index(want)]


def one_pass(state: State, judge) -> tuple[State, object]:
    base, de, en = state
    if isinstance(judge, Judge):
        judge.cells = {"de": de, "en": en}
    out = merge(base, de, en, judge)
    return (out.record, out.halves["de"], out.halves["en"]), out


def refuse(_question):
    return None


def recorded_order(record: list[BaseMember]) -> list[str]:
    """The cells that were recorded before the first pass, in record order."""
    return [
        m.sid or f"{m.de}/{m.en}"
        for m in record
        if isinstance(m.mark, tuple) and m.mark[0] == "base"
    ]


def check_case(base: Word, de: Word, en: Word) -> tuple[list[tuple[str, str]], list[str]]:
    """Violations as ``(law, detail)``, empty when every law holds; and notes."""
    bad: list[tuple[str, str]] = []
    notes: list[str] = []
    s0 = start(base, de, en)
    for style in STYLES:
        ref = Judge(style)
        s1, out = one_pass(s0, ref)
        asked = [key_of(q) for q in out.asked]
        if out.opened or labels(s1[1]) != labels(s1[2]):
            bad.append(("complete", f"{style}: open or halves differ"))
            continue
        again, out2 = one_pass(s1, refuse)
        if out2.opened or again != s1:
            bad.append(("complete", f"{style}: a further pass asks or changes"))
        if not asked:
            break  # no question: every style gives the same run
        final = labels(s1[1])
        was_recorded = recorded_order(s1[0])
        keys = list(dict.fromkeys(asked))
        if len(keys) <= ALL_SUBSETS_UP_TO:
            subsets = [set(c) for r in range(len(keys)) for c in itertools.combinations(keys, r)]
        else:
            kinds = sorted({k[0] for k in keys})
            subsets = [
                {k for k in keys if k[0] in chosen}
                for r in range(len(kinds))
                for chosen in itertools.combinations(kinds, r)
            ]
        for allow in subsets:
            judge = Judge(style)
            judge.memo = dict(ref.memo)
            judge.replaying = True
            judge.allow = allow
            state, out = one_pass(s0, judge)
            tag = f"{style}, first answering {sorted(k[0] for k in allow) or 'nothing'}"
            # held: recorded entries and offered cells of open questions are untouched
            before = {m.mark: m for m in s0[0]}
            after = {m.mark: m for m in state[0]}
            for q in out.opened:
                if q.kind == "order":
                    if [m.mark for m in state[0] if m.mark in before] != [
                        m.mark for m in s0[0] if m.mark in after
                    ]:
                        bad.append(("held", f"{tag}: the recorded order changed"))
                    continue
                if q.mark in before and before[q.mark] != after.get(q.mark):
                    bad.append(("held", f"{tag}: the record of a {q.kind} member changed"))
                if q.kind == "identify":
                    side = 1 if q.side == "de" else 2
                    was = {c.mark: c for c in s0[side]}
                    now = {c.mark: c for c in state[side]}
                    if any(
                        was[s0[side][j].mark] != now.get(s0[side][j].mark) for j in q.candidates
                    ):
                        bad.append(("held", f"{tag}: an offered cell changed"))
            if out.opened:
                notes.append("passes that left a question open")
                if state != s0:
                    notes.append("passes that left a question open and still made progress")
                same, out_b = one_pass(state, refuse)
                if same != state or not out_b.opened:
                    bad.append(("fixpoint", tag))
            judge.allow = None
            for _ in range(MAX_PASSES):
                if not out.opened:
                    break
                state, out = one_pass(state, judge)
            if out.opened:
                bad.append(("confluent", f"{tag}: still open after {MAX_PASSES} passes"))
            elif labels(state[1]) != labels(state[2]) or sorted(labels(state[1])) != sorted(final):
                bad.append(
                    ("confluent", f"{tag}: {labels(state[1])} / {labels(state[2])} != {final}")
                )
            elif recorded_order(state[0]) != was_recorded:
                bad.append(
                    ("confluent", f"{tag}: recorded cells reordered: {labels(state[1])} != {final}")
                )
            elif labels(state[1]) != final:
                notes.append("confluent, but cells the halves added sit in another order")
            if judge.new_asks:
                bad.append(("no new ask", f"{tag}: {sorted({k[0] for k in judge.new_asks})}"))
            if judge.lost_candidates:
                bad.append(("no new ask", f"{tag}: a named cell is no longer offered"))
    return bad, notes


def check_base(
    base: Word, half_len: int
) -> tuple[Counter, list[tuple[str, str, Word, Word, Word]]]:
    stats: Counter = Counter()
    bad: list[tuple[str, str, Word, Word, Word]] = []
    halves = [w for w in all_words(half_len) if w != base]
    for de in halves:
        for en in halves:
            stats["cases"] += 1
            found, notes = check_case(base, de, en)
            stats.update(notes)
            if found:
                stats["cases with a violation"] += 1
            for law, detail in found:
                stats[f"violations: {law}"] += 1
                if len(bad) < 40:
                    bad.append((law, detail, base, de, en))
    return stats, bad


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", type=int, default=2, help="cells of a recorded deck, at most")
    parser.add_argument("--half", type=int, default=3, help="cells of a half, at most")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--show", type=int, default=12, help="violations to print per law")
    args = parser.parse_args()
    bases = all_words(args.base, bodies=("x", "y"))
    stats: Counter = Counter()
    bad: list[tuple[str, str, Word, Word, Word]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for part, found in pool.map(partial(check_base, half_len=args.half), bases):
            stats.update(part)
            bad.extend(found)
    for name, n in sorted(stats.items()):
        print(f"{n:9}  {name}")
    shown: Counter = Counter()
    for law, detail, base, de, en in sorted(
        bad, key=lambda b: (b[0], len(b[2]) + len(b[3]) + len(b[4]))
    ):
        shown[law] += 1
        if shown[law] <= args.show:
            words_ = " | ".join(" ".join(w) or "-" for w in (base, de, en))
            print(f"  {law}: {words_}: {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
