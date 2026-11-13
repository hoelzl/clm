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

With ``--tags`` a cell may also carry the ``subslide`` tag (written ``x^``,
``A^``), which makes it start a slide, and an un-id'd cell of a half may have
gained a ``slide_id`` (``x#``). A half may then retag any cell, promote an
un-id'd cell to a slide start, and stamp an id. The decks are shorter (3
recorded cells against a half of 4; 2 against 2 where both halves changed),
since there are several times as many of them.

The laws, with no question answered (the judge refuses every one):

1. unchanged      nothing changed: no question, nothing changes
2. one-sided      only one half changed: no question, and the result is that
                  half, exactly
3. symmetric      swapping the halves gives the same content. The ORDER may
                  differ: ties between unrelated additions are broken DE first
4. conserving     a merge that asked nothing never holds more copies of a cell
                  than a half holds, never fewer than both halves hold, and
                  never drops an addition. (Where the recorded deck holds
                  byte-identical cells and each half edited a different one,
                  the model asks: ``conflict_copies``.)

    python scripts/sync_model_laws.py            # about 4 minutes
    python scripts/sync_model_laws.py --tags

Exits 1 on a violation.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clm.slides.doc_core import BaseMember, Cell, merge  # noqa: E402

IDS = ("A", "B")
BODIES = ("x", "y", "z")
TAG = frozenset({"subslide"})
Word = tuple[str, ...]


def _tags(token: str) -> frozenset[str]:
    return TAG if "^" in token else frozenset()


def cells(word: Word, lang: str) -> list[Cell]:
    return [
        Cell(t[0], "vo", f"{t[0]} {lang}", True, tags=_tags(t))
        if t[0] in IDS
        else Cell(f"s-{t[0]}" if "#" in t else None, "code", t[0], False, tags=_tags(t))
        for t in word
    ]


def base(word: Word) -> list[BaseMember]:
    return [
        BaseMember(t[0], "vo", True, f"{t[0]} de", f"{t[0]} en", tags=_tags(t))
        if t[0] in IDS
        else BaseMember(None, "code", False, t[0], t[0], tags=_tags(t))
        for t in word
    ]


def labels(word: Word) -> list[str]:
    return [
        (f"id:{t[0]}" if t[0] in IDS else f"id:s-{t[0]}" if "#" in t else f"body:{t[0]}")
        + ("^subslide" if "^" in t else "")
        for t in word
    ]


def words(max_len: int, ids: Word, bodies: Word = BODIES, tags: bool = False):
    """Every word holding each of ``ids`` once, plus any un-id'd cells. With
    ``tags``, also every way of tagging its id'd cells."""
    taggings = itertools.product(("", "^"), repeat=len(ids)) if tags else [("",) * len(ids)]
    for marks in taggings:
        named = tuple(t + mark for t, mark in zip(ids, marks, strict=True))
        for n in range(len(ids), max_len + 1):
            for places in itertools.permutations(range(n), len(ids)):
                for fill in itertools.product(bodies, repeat=n - len(ids)):
                    stamped = [t for t in fill if "#" in t]
                    if len(stamped) != len(set(stamped)):
                        continue  # a slide_id names one cell
                    word: list[str | None] = [None] * n
                    for t, p in zip(named, places, strict=True):
                        word[p] = t
                    rest = iter(fill)
                    yield tuple(w if w is not None else next(rest) for w in word)


def refuse(_question):
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tags", action="store_true", help="also retag, promote and stamp cells")
    args = parser.parse_args()
    if args.tags:
        base_bodies: Word = ("x", "y", "x^")
        half_bodies: Word = ("x", "y", "z", "x^", "y^", "x#")
        one_base, one_half, two_base, two_half = 3, 4, 2, 2
    else:
        base_bodies, half_bodies = ("x", "y"), BODIES
        one_base, one_half, two_base, two_half = 4, 5, 3, 4

    def halves(max_len: int, ids: Word) -> list[Word]:
        return sorted(set(words(max_len, ids, half_bodies, args.tags)))

    bases: set[Word] = set()
    for k in range(len(IDS) + 1):
        for ids in itertools.combinations(IDS, k):
            bases.update(words(one_base, ids, base_bodies, args.tags))
    stats: Counter = Counter()
    bad: dict[str, list] = {"unchanged": [], "one-sided": [], "symmetric": [], "conserving": []}

    for b in sorted(bases):
        ids = tuple(t[0] for t in b if t[0] in IDS)
        out = merge(base(b), cells(b, "de"), cells(b, "en"), refuse)
        stats["unchanged"] += 1
        if out.open is not None or out.labels() != labels(b):
            bad["unchanged"].append(b)
        for d in halves(one_half, ids):
            for side in ("de", "en"):
                de, en = (d, b) if side == "de" else (b, d)
                out = merge(base(b), cells(de, "de"), cells(en, "en"), refuse)
                stats["one-sided"] += 1
                if out.open is not None or out.asked or out.labels() != labels(d):
                    bad["one-sided"].append((b, side, d, out.labels()))

    # both halves changed: a smaller space (it is quadratic in the halves)
    for b in sorted(w for w in bases if len(w) <= two_base):
        ids = tuple(t[0] for t in b if t[0] in IDS)
        both = halves(two_half, ids)
        for d in both:
            for e in both:
                stats["two-sided"] += 1
                out = merge(base(b), cells(d, "de"), cells(e, "en"), refuse)
                if out.open is not None:
                    stats["two-sided, asks"] += 1
                    continue
                stats["two-sided, no question"] += 1
                got = out.labels()
                # a cell is counted by what it is, whatever tags or id it carries
                cb, cd, ce = (Counter(t[0] for t in w) for w in (b, d, e))
                cr = Counter(m.sid if m.localized else m.content for m in out.sequence)
                for c in set(cb) | set(cd) | set(ce) | set(cr):
                    if cr[c] > max(cd[c], ce[c]):
                        bad["conserving"].append(("more copies than a half holds", b, d, e, got))
                    if max(cd[c], ce[c]) > cb[c] and cr[c] == 0:
                        bad["conserving"].append(("an addition vanished", b, d, e, got))
                    if cr[c] < min(cd[c], ce[c]):
                        bad["conserving"].append(("fewer than both halves", b, d, e, got))
                mirrored = merge(base(b), cells(e, "de"), cells(d, "en"), refuse)
                if mirrored.open is not None or Counter(mirrored.labels()) != Counter(got):
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
