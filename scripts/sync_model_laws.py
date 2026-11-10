"""Exhaustive small-scope check of the sync reference model's laws.

``scripts/sync_model.py`` is a prototype structural core for
``clm slides sync`` (see ``docs/claude/sync-structural-core-investigation.md``).
Unlike the sync probe, these checks need no oracle and no notion of intent:
they hold for every input, so every small input is tried.

Decks are words over two id'd localized cells (``A``, ``B``) and un-id'd
shared cells with contents ``x``, ``y``, ``z``. Contents may repeat, so
byte-identical cells are covered. Every recorded deck of up to 4 cells is
tried against every half of up to 5 cells that keeps the deck's id'd cells
(adding or removing an id'd cell asks a question by design).

The laws, with no question answered (the judge refuses every one):

1. unchanged      nothing changed: no question, nothing changes
2. one-sided      only one half changed: no question, and the result is that
                  half, exactly
3. symmetric      swapping the halves gives the same content. The ORDER may
                  differ: ties between unrelated additions are broken DE first
4. conserving     a merge that asked nothing never holds more copies of a cell
                  than a half holds, and never drops an addition. It may hold
                  FEWER copies than both halves hold: only when the recorded
                  deck holds byte-identical cells and each half changed a
                  different one (reported, not counted as a violation)

    python scripts/sync_model_laws.py            # about 4 minutes

Exits 1 on a violation.
"""

from __future__ import annotations

import itertools
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_model import BaseMember, Cell, merge  # noqa: E402

IDS = ("A", "B")
BODIES = ("x", "y", "z")
Word = tuple[str, ...]


def cells(word: Word, lang: str) -> list[Cell]:
    return [
        Cell(t, "vo", f"{t} {lang}", True) if t in IDS else Cell(None, "code", t, False)
        for t in word
    ]


def base(word: Word) -> list[BaseMember]:
    return [
        BaseMember(t, "vo", True, f"{t} de", f"{t} en")
        if t in IDS
        else BaseMember(None, "code", False, t, t)
        for t in word
    ]


def labels(word: Word) -> list[str]:
    return [f"id:{t}" if t in IDS else f"body:{t}" for t in word]


def words(max_len: int, ids: Word, bodies: Word = BODIES):
    """Every word holding each of ``ids`` once, plus any un-id'd cells."""
    for n in range(len(ids), max_len + 1):
        for places in itertools.permutations(range(n), len(ids)):
            for fill in itertools.product(bodies, repeat=n - len(ids)):
                word: list[str | None] = [None] * n
                for t, p in zip(ids, places, strict=True):
                    word[p] = t
                rest = iter(fill)
                yield tuple(w if w is not None else next(rest) for w in word)


def refuse(_question):
    return None


def main() -> int:
    bases: set[Word] = set()
    for k in range(len(IDS) + 1):
        for ids in itertools.combinations(IDS, k):
            bases.update(words(4, ids, bodies=("x", "y")))
    stats: Counter = Counter()
    bad: dict[str, list] = {"unchanged": [], "one-sided": [], "symmetric": [], "conserving": []}

    for b in sorted(bases):
        ids = tuple(t for t in b if t in IDS)
        out = merge(base(b), cells(b, "de"), cells(b, "en"), refuse)
        stats["unchanged"] += 1
        if out.open is not None or out.labels() != labels(b):
            bad["unchanged"].append(b)
        for d in sorted(set(words(5, ids))):
            for side in ("de", "en"):
                de, en = (d, b) if side == "de" else (b, d)
                out = merge(base(b), cells(de, "de"), cells(en, "en"), refuse)
                stats["one-sided"] += 1
                if out.open is not None or out.asked or out.labels() != labels(d):
                    bad["one-sided"].append((b, side, d, out.labels()))

    # both halves changed: a smaller space (it is quadratic in the halves)
    for b in sorted(w for w in bases if len(w) <= 3):
        ids = tuple(t for t in b if t in IDS)
        halves = sorted(set(words(4, ids)))
        for d in halves:
            for e in halves:
                stats["two-sided"] += 1
                out = merge(base(b), cells(d, "de"), cells(e, "en"), refuse)
                if out.open is not None:
                    stats["two-sided, asks"] += 1
                    continue
                stats["two-sided, no question"] += 1
                got = out.labels()
                cb, cd, ce = (Counter(labels(w)) for w in (b, d, e))
                cr = Counter(got)
                for c in set(cb) | set(cd) | set(ce) | set(cr):
                    if cr[c] > max(cd[c], ce[c]):
                        bad["conserving"].append(("more copies than a half holds", b, d, e, got))
                    if max(cd[c], ce[c]) > cb[c] and cr[c] == 0:
                        bad["conserving"].append(("an addition vanished", b, d, e, got))
                    if cr[c] < min(cd[c], ce[c]):
                        if cb[c] >= 2:
                            stats["fewer copies than both halves (identical recorded cells)"] += 1
                        else:
                            bad["conserving"].append(("fewer than both halves", b, d, e, got))
                mirrored = merge(base(b), cells(e, "de"), cells(d, "en"), refuse)
                if mirrored.open is not None or Counter(mirrored.labels()) != cr:
                    bad["symmetric"].append((b, d, e, got, mirrored.labels()))
                elif mirrored.labels() != got:
                    stats["symmetric in content, order differs"] += 1

    for name, n in stats.items():
        print(f"{n:9}  {name}")
    for law, items in bad.items():
        print(f"{law}: {len(items)} violations")
        for item in items[:5]:
            print("   ", item)
    return 1 if any(bad.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
