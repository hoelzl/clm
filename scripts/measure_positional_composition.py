"""What is positional identity actually made of, and what does cold cost?

Companion to ``measure_sync_change_points.py``. That script counts how many
structural change points each *keying rule* exposes; this one asks what the
keyed members **are**, and how many of the verification questions a cold deck
produces are questions the engine could answer itself.

Three measurements, each re-runnable against any slides root:

1. **Composition** — positional members by kind / langness / sidedness.
2. **Blast radius** — the honest churn metric. A pool is the set of id-less
   members sharing one ``pos:<group>/<kind>`` prefix; inserting or deleting one
   member of a pool of size ``n`` re-keys about half its siblings, so the pool
   costs ``n*(n-1)/2``. "Member sits in a pool > 1" scores a slide with two code
   cells the same as a 170-cell anchor-less deck, which is why it misleads.
3. **Cold cost** — of the ``verify_cold`` items a fully cold diff emits, how
   many are ``shared`` + byte-identical, i.e. both halves are the same bytes and
   there is no translation divergence to verify.

Usage::

    python scripts/measure_positional_composition.py <slides-root>
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from clm.slides.doc_lenses import load_bundle
from clm.slides.sync_diff import diff_deck

#: Kinds that carry no natural-language content, so a ``shared`` declaration on
#: them is verifiable by inspection rather than trusted (design §6.2's
#: ``record_neutral`` predicate).
NEUTRAL_KINDS = frozenset({"code", "j2"})


def _body(cell: object | None) -> str | None:
    if cell is None:
        return None
    return "\n".join(cell.lines[1:]).rstrip()  # type: ignore[attr-defined]


def _bucket(n: int) -> str:
    if n == 1:
        return "1     (behaves like an id)"
    if n <= 3:
        return "2-3   (a slide's code steps)"
    if n <= 9:
        return "4-9   (dense slide)"
    return "10+   (anchor-poor deck)"


_ORDER = [
    "1     (behaves like an id)",
    "2-3   (a slide's code steps)",
    "4-9   (dense slide)",
    "10+   (anchor-poor deck)",
]


def _decks(root: Path) -> list[Path]:
    skip = {"_archive", "voiceover", "notes"}
    return sorted(
        p
        for p in root.rglob("*.de.py")
        if not (skip & set(p.parts)) and not p.name.startswith(("voiceover_", "notes_"))
    )


def main(root: Path) -> int:
    pools: dict[tuple[Path, str, str], int] = Counter()
    anchors: dict[Path, int] = {}
    kind_langness: Counter = Counter()
    sidedness: Counter = Counter()
    cold_class: Counter = Counter()
    id_members = pos_members = cold_total = 0

    for de_path in _decks(root):
        try:
            bundle = load_bundle(de_path)
        except Exception:  # noqa: BLE001
            continue
        deck = bundle.outcome.deck
        if deck is None:
            continue
        anchors[de_path] = sum(1 for g in deck.groups if g.anchor is not None)

        by_key = {}
        for m in deck.members():
            by_key[m.key.render()] = m
            if m.key.scheme == "id":
                id_members += 1
                continue
            pos_members += 1
            kind_langness[(m.kind, m.langness)] += 1
            sidedness["one side only" if m.is_one_sided else "both sides"] += 1
            group_tok, kind, _ord = m.key.value.rsplit("/", 2)
            pools[(de_path, group_tok, kind)] += 1

        for item in diff_deck(deck, None).items:  # base=None => fully cold
            if item.outcome != "unverified":
                continue
            cold_total += 1
            m = by_key.get(item.key)
            if m is None:
                cold_class["<unmatched>"] += 1
            elif m.is_one_sided:
                cold_class["one-sided — real question"] += 1
            elif m.langness != "shared":
                cold_class["localized — real question"] += 1
            elif _body(m.de) != _body(m.en):
                cold_class["shared but diverged — real question"] += 1
            elif m.kind in NEUTRAL_KINDS:
                cold_class[f"shared + identical, {m.kind} — ENGINE-DECIDABLE"] += 1
            else:
                cold_class[f"shared + identical, {m.kind} — excluded (prose)"] += 1

    print(f"decks parsed        : {len(anchors)}")
    print(f"id'd members        : {id_members}")
    print(f"positional members  : {pos_members}")
    print(f"pools               : {len(pools)}")
    print()

    print("=== 1. composition of positional identity ===")
    for (kind, langness), n in kind_langness.most_common():
        print(f"  {kind:<10} {langness:<10} {n:>6} ({100 * n / max(pos_members, 1):>5.1f}%)")
    for k, n in sidedness.most_common():
        print(f"  {k:<21} {n:>6} ({100 * n / max(pos_members, 1):>5.1f}%)")
    print()

    print("=== 2. blast radius by pool size ===")
    mem: Counter = Counter()
    blast: Counter = Counter()
    for n in pools.values():
        mem[_bucket(n)] += n
        blast[_bucket(n)] += n * (n - 1) / 2
    total_blast = sum(blast.values())
    print(f"  {'pool size':<28} {'members':>8} {'% members':>10} {'% churn':>9}")
    for b in _ORDER:
        print(
            f"  {b:<28} {mem[b]:>8} {100 * mem[b] / max(pos_members, 1):>9.1f}%"
            f" {100 * blast[b] / max(total_blast, 1):>8.1f}%"
        )
    per_deck: Counter = Counter()
    for (p, _g, _k), n in pools.items():
        per_deck[p] += n * (n - 1) / 2
    ranked = sorted(per_deck.items(), key=lambda kv: -kv[1])
    for cutoff in (5, 20):
        share = sum(b for _p, b in ranked[:cutoff]) / max(total_blast, 1)
        print(f"  top {cutoff:>2} decks carry {100 * share:>5.1f}% of all positional churn")
    print("  worst decks (note the anchor counts):")
    for p, b in ranked[:5]:
        print(f"    blast={b:>8.0f}  anchors={anchors[p]:>4}  {p.name}")
    print()

    print("=== 3. cold cost: what a fully cold diff asks ===")
    print(f"  total cold questions : {cold_total}")
    decidable = sum(v for k, v in cold_class.items() if "ENGINE-DECIDABLE" in k)
    for k, v in cold_class.most_common():
        print(f"    {k:<48} {v:>7} ({100 * v / max(cold_total, 1):>5.1f}%)")
    print()
    print(
        f"  `record_neutral` would resolve {decidable} of {cold_total} "
        f"({100 * decidable / max(cold_total, 1):.1f}%) without asking."
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
