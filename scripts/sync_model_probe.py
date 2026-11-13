"""Score the sync reference model on the repo's sync probe, and compare runs.

``scripts/sync_model.py`` is a prototype structural core for
``clm slides sync`` (see ``docs/claude/sync-structural-core-investigation.md``).
This script runs it over the cases of ``scripts/sync_probe.py`` with the
probe's own answer policies and its own oracle (the label-sequence
comparison), on an abstract view of the two halves instead of on files:

    python scripts/sync_model_probe.py run MODEL.json [--no-identify]
    python scripts/sync_probe.py run ENGINE.json --workers 12
    python scripts/sync_model_probe.py compare ENGINE.json MODEL.json

``--no-identify`` switches off the model's one new question ("which of these
cells is X?"), which leaves it guessing where the engine guesses today.

The probe's policies answer as a careful agent would. For ``identify`` that
agent reads the cells and names the right one; here the harness knows it from
the cell names the case was built from.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.slides import sync_probe as sp  # noqa: E402
from tests.slides import sync_probe_cross as spc  # noqa: E402
from tests.slides import sync_probe_ext as spx  # noqa: E402
from tests.slides.sync_probe import SILENT  # noqa: E402

from clm.slides.doc_core import BaseMember, Cell, Question, merge  # noqa: E402

FAMILIES = {**sp.FAMILIES, **spx.FAMILIES, **spc.FAMILIES}
PREFIX = {"slide": "s-", "subslide": "u-", "vo": "vo-"}
#: The answers the engine advertises for the questions the model shares with it.
VOCAB = {
    "order": ("de", "en"),
    "conflict_shared": ("de", "en", "body"),
    "remove_vs_edit": ("remove", "keep"),
    "remove_localized_side": ("remove", "body"),
    "translate_new": ("body",),
}
REMOVE_Q = {"remove_vs_edit", "remove_localized_side"}


def cell(name: str, lang: str, bodies: dict[str, str]) -> Cell:
    k = sp.kind(name)
    if k in PREFIX:
        return Cell(PREFIX[k] + name, k, f"{name} {lang}", True)
    return Cell(None, k, bodies[name], False)


def half(order, lang: str, bodies: dict[str, str]) -> list[Cell]:
    return [cell(n, lang, bodies) for n in order]


def base_from_names(order, bodies: dict[str, str]) -> list[BaseMember]:
    out = []
    for n in order:
        k = sp.kind(n)
        if k in PREFIX:
            out.append(BaseMember(PREFIX[k] + n, k, True, f"{n} de", f"{n} en"))
        else:
            out.append(BaseMember(None, k, False, bodies[n], bodies[n]))
    return out


def base_from_sequence(sequence) -> list[BaseMember]:
    """What a converged round records: the merged sequence."""
    return [
        BaseMember(m.sid, m.kind, True, "de", "en")
        if m.localized
        else BaseMember(m.sid, m.kind, False, m.content, m.content)
        for m in sequence
    ]


def lab(name: str, bodies: dict[str, str]) -> str:
    k = sp.kind(name)
    return f"id:{PREFIX[k]}{name}" if k in PREFIX else "body:" + bodies[name]


def check(got, rnd, final: dict[str, str], asked: bool) -> str:
    """The probe's oracle (``_check`` / ``_check2`` / ``_check3``) on labels."""
    labs = [[lab(n, final) for n in o] for o in (rnd.expected, *rnd.accept)]
    skip = {lab(n, final) for n in rnd.unordered}
    seq = [x for x in got if x not in skip]
    if isinstance(rnd, spc.R3):  # the alternatives may differ in membership
        for o in labs:
            if sorted(got) == sorted(o) and seq == [x for x in o if x not in skip]:
                return "converged"
        if not any(sorted(got) == sorted(o) for o in labs):
            return "lost_or_duplicated"
    else:
        if sorted(got) != sorted(labs[0]):
            return "lost_or_duplicated"
        if any(seq == [x for x in o if x not in skip] for o in labs):
            return "converged"
    if rnd.alt is not None and seq == [lab(n, final) for n in rnd.alt]:
        return "misplaced_to_other" if asked else "silent_pick"
    return "misplaced"


@dataclass
class Policy:
    """One round's answers: the probe's "careful agent", question by question.

    The shared questions are answered exactly as the probe's runners answer
    the engine's (``run_case`` / ``run_case2`` / ``run_case3``). ``identify``
    is the model's own: the agent reads the candidate cells and names the one
    that is the recorded cell, which the harness knows from the cell names.
    """

    base_names: list[str]
    orders: dict[str, tuple[str, ...]]
    order_answer: str
    conflict_side: str
    answer_new: bool
    rve: str | None
    rm: str | None
    removing: bool
    asked_order: bool = False

    def __call__(self, q: Question):
        if q.kind == "identify":
            assert q.member is not None and q.side is not None
            want = self.base_names[q.member]
            return next((j for j in q.candidates if self.orders[q.side][j] == want), "removed")
        answers = VOCAB[q.kind]
        if q.kind == "order":
            self.asked_order = True
            return self.order_answer
        if q.kind == "conflict_shared":
            return self.conflict_side
        if q.kind == "translate_new":
            return "body" if self.answer_new else None
        if q.kind == "remove_vs_edit" and self.rve is not None:
            return self.rve if self.rve in answers else None
        if self.rm is not None:
            return self.rm if self.rm in answers else None
        return "remove" if self.removing else None


def run_case(case, *, identify: bool) -> dict:
    names = set(case.base)
    for r in case.rounds:
        names |= set(r.de_order) | set(r.en_order)
    cur = {n: sp.base_body(n) for n in names if sp.kind(n) in ("code", "md")}
    base = base_from_names(case.base, cur)
    base_names = list(case.base)
    asked_total: Counter = Counter()
    one_sided = True

    def result(status: str, detail: str = "") -> dict:
        return {
            "family": case.family,
            "status": status,
            "detail": detail,
            "questions": dict(asked_total),
            "one_sided": one_sided,
        }

    for ri, rnd in enumerate(case.rounds):
        prev = case.base if ri == 0 else case.rounds[ri - 1].expected
        rm = rve = None
        answer_new = False
        if isinstance(rnd, sp.Round):
            de_ed = rnd.edits if rnd.edit_side == "de" else ()
            en_ed = rnd.edits if rnd.edit_side == "en" else ()
            de_b = {**cur, **{n: sp.edited_body(n) for n in de_ed}}
            en_b = {**cur, **{n: sp.edited_body(n) for n in en_ed}}
            final = {**cur, **{n: sp.edited_body(n) for n in rnd.edits}}
            conflict_side, order_answer = rnd.edit_side, rnd.intent
        else:
            de_ed, en_ed = rnd.de_edits, rnd.en_edits
            de_b, en_b = spx._bodies(cur, de_ed, "de"), spx._bodies(cur, en_ed, "en")
            final = dict(cur)
            for n in set(de_ed) | set(en_ed):
                if n in de_ed and n in en_ed:
                    final[n] = (de_b if rnd.conflict_side == "de" else en_b)[n]
                else:
                    final[n] = (de_b if n in de_ed else en_b)[n]
            conflict_side, order_answer = rnd.conflict_side, rnd.intent
            if isinstance(rnd, spx.Round2):
                for n, w in rnd.winner:
                    final[n] = {"de": de_b, "en": en_b, "none": cur}[w][n]
                rve = rnd.rve
            else:
                order_answer, rm, answer_new = rnd.order_answer or rnd.intent, rnd.rm, True
        # one-sided: in every round one half is exactly what the last sync left
        if not (
            (tuple(rnd.de_order) == tuple(prev) and not de_ed)
            or (tuple(rnd.en_order) == tuple(prev) and not en_ed)
        ):
            one_sided = False
        removed = set(prev) - set(rnd.expected)
        regrouped = sp._group_of(prev) != sp._group_of(rnd.expected)
        answer = Policy(
            base_names=base_names,
            orders={"de": rnd.de_order, "en": rnd.en_order},
            order_answer=order_answer,
            conflict_side=conflict_side,
            answer_new=answer_new,
            rve=rve,
            rm=rm,
            removing=bool(removed or regrouped),
        )
        out = merge(
            base,
            half(rnd.de_order, "de", de_b),
            half(rnd.en_order, "en", en_b),
            answer,
            identify=identify,
        )
        for q in out.asked:
            asked_total[q.kind] += 1
        prefix = "" if ri == 0 else f"r{ri + 1}:"
        if out.open is not None:
            return result(prefix + "unanswered_question", out.open.kind)
        status = check(out.labels(), rnd, final, answer.asked_order)
        if status != "converged":
            return result(prefix + status, f"got={out.labels()}")
        base = base_from_sequence(out.sequence)
        base_names = [
            rnd.de_order[m.pos["de"]] if m.pos["de"] is not None else rnd.en_order[m.pos["en"]]
            for m in out.sequence
        ]
        cur = {n: final[n] for n in cur}
    return result("converged")


def run(out: Path, identify: bool) -> int:
    results = {}
    for name, family in FAMILIES.items():
        cases = list(dict.fromkeys(family()))
        print(f"{name}: {len(cases)} cases", flush=True)
        for case in cases:
            results[case.id] = run_case(case, identify=identify)
    out.write_text(json.dumps({"engine": "sync_model", "results": results}), encoding="utf-8")
    print(len(results), "cases:", dict(Counter(rank(v["status"]) for v in results.values())))
    return 0


def rank(status: str) -> str:
    kind = status.split(":")[-1]
    if kind == "converged":
        return "converged"
    return "silent" if kind in SILENT else "visible"


def compare(engine_path: Path, model_path: Path, show: int) -> int:
    eng = json.loads(engine_path.read_text(encoding="utf-8"))["results"]
    mod = json.loads(model_path.read_text(encoding="utf-8"))["results"]
    if set(eng) != set(mod):
        print("the two runs probed different cases")
        return 2
    one = [k for k in mod if mod[k]["one_sided"]]
    print("| cases | run | converged | visible stop | silently wrong |\n|---|---|---:|---:|---:|")
    for label, ids in (("all", list(mod)), ("one half untouched", one)):
        for name, res in (("engine", eng), ("model", mod)):
            c = Counter(rank(res[k]["status"]) for k in ids)
            print(
                f"| {label} ({len(ids)}) | {name} | {c['converged']} | {c['visible']} | {c['silent']} |"
            )
    print("\nengine -> model, per case:")
    moves = Counter((rank(eng[k]["status"]), rank(mod[k]["status"])) for k in mod)
    for (a, b), n in sorted(moves.items()):
        print(f"  {a:>9} -> {b:<9} {n}")
    shared = [k for k in mod if eng[k]["status"] == "converged" == mod[k]["status"]]
    print(f"\nquestions answered on the {len(shared)} cases both converge on:")
    for name, res in (("engine", eng), ("model", mod)):
        q: Counter = Counter()
        for k in shared:
            q.update(res[k]["questions"])
        print(f"  {name}: {sum(q.values())}  {dict(q.most_common())}")
    bad: dict[tuple[str, str], list[str]] = defaultdict(list)
    for k, v in mod.items():
        if v["status"] != "converged":
            bad[(v["family"], v["status"])].append(k)
    if bad:
        print("\nmodel, not converged (family, status, count, the engine on the same cases):")
    for (fam, status), ids in sorted(bad.items()):
        print(f"  {fam} {status} {len(ids)} {dict(Counter(rank(eng[k]['status']) for k in ids))}")
        for k in ids[:show]:
            print("    CASE", k)
            print("      model: ", mod[k]["detail"][:300])
            print("      engine:", eng[k]["status"], eng[k]["detail"][:200])
    failing = sorted(
        (k for k in one if eng[k]["status"] != "converged" and k.count("DE[") == 1), key=len
    )
    print(f"\nengine, one half untouched, single round, not converged: {len(failing)}")
    seen: set[tuple[str, str]] = set()
    for k in failing:
        key = (eng[k]["family"], eng[k]["status"])
        if key not in seen and len(seen) < show:
            seen.add(key)
            print(f"    [{eng[k]['status']}] {k}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("out", type=Path)
    p_run.add_argument("--no-identify", action="store_true")
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("engine", type=Path)
    p_cmp.add_argument("model", type=Path)
    p_cmp.add_argument("--show", type=int, default=3, help="example cases per group")
    args = parser.parse_args()
    if args.command == "run":
        return run(args.out, not args.no_identify)
    return compare(args.engine, args.model, args.show)


if __name__ == "__main__":
    raise SystemExit(main())
