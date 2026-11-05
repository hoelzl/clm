"""Probe families for shared cells moved across slide groups (#1142).

Written by the #1142 adversarial review; part of ``scripts/sync_probe.py``'s
gate since. Extends the #1130 probe with shapes the other families do not
reach: markdown and mixed cells crossing groups, slides inserted on one or
both halves, deletes and inserts beside a move, both halves moving one cell,
byte-identical cells, a second round, chains over three or four groups, and
answer-policy variants.
Runner ``run_case3``: per-half edits, translate_new answered with a body
(one-sided slide inserts), answer policies for order and removal questions,
and ``accept`` alternatives that may differ in MEMBERSHIP (ambiguous intent).
"""

from __future__ import annotations

import itertools
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tests.slides import sync_probe as sp
from tests.slides import sync_probe_ext as spx
from tests.slides.sync_probe import OTHER, Result, kind

SUFFIX = spx.SUFFIX


@dataclass(frozen=True)
class R3:
    de_order: tuple
    en_order: tuple
    de_edits: tuple = ()
    en_edits: tuple = ()
    conflict_side: str = "de"
    intent: str = "de"
    expected: tuple = ()
    unordered: tuple = ()
    accept: tuple = ()  # full alternatives (membership may differ)
    alt: tuple | None = None
    order_answer: str | None = None  # None -> intent
    rm: str | None = None  # None -> probe default; "keep"/"remove" override all removal Qs


@dataclass(frozen=True)
class C3:
    family: str
    base: tuple
    rounds: tuple
    tag: str = ""

    @property
    def id(self) -> str:
        parts = [self.family, self.tag, " ".join(self.base)]
        for r in self.rounds:
            parts.append(
                f"DE[{' '.join(r.de_order)}] EN[{' '.join(r.en_order)}] "
                f"ed@de:{','.join(r.de_edits) or '-'} ed@en:{','.join(r.en_edits) or '-'} "
                f"cf={r.conflict_side} intent={r.intent} oa={r.order_answer} rm={r.rm} "
                f"exp=[{' '.join(r.expected)}] acc={[' '.join(a) for a in r.accept]}"
            )
        return " | ".join(p for p in parts if p)


def _check3(de_path, en_path, rnd: R3, final, asked):
    got = {}
    for lang, path in (("de", de_path), ("en", en_path)):
        labels, anomalies = sp.parse_labels(path.read_text(encoding="utf-8"), lang)
        if anomalies:
            return "lang_or_kind_anomaly", f"{lang}: {anomalies}"
        got[lang] = labels
    skip = {spx._lab(n, final) for n in rnd.unordered}
    cands = (rnd.expected, *rnd.accept)
    for o in cands:
        lab = [spx._lab(n, final) for n in o]
        if all(sorted(got[x]) == sorted(lab) for x in ("de", "en")) and got["de"] == got["en"]:
            seq = [x for x in got["de"] if x not in skip]
            if seq == [x for x in lab if x not in skip]:
                return "converged", ""
    exp = [spx._lab(n, final) for n in rnd.expected]
    if not any(
        all(sorted(got[x]) == sorted(spx._lab(n, final) for n in o) for x in ("de", "en"))
        for o in cands
    ):
        return "lost_or_duplicated", f"de={got['de']} en={got['en']} exp={exp}"
    if got["de"] != got["en"]:
        return "halves_disagree", f"de={got['de']} en={got['en']}"
    seq = [x for x in got["de"] if x not in skip]
    if rnd.alt is not None and seq == [spx._lab(n, final) for n in rnd.alt]:
        return ("misplaced_to_other" if asked else "silent_pick"), f"got={seq}"
    return "misplaced", f"got={seq} exp={exp}"


def _slide_body(name, lang):
    k = kind(name)
    if k == "slide":
        return f"#\n# # {name} {lang}"
    if k == "subslide":
        return f"#\n# ## {name} {lang}"
    return f"#\n# - {name.upper()} {lang}"


def run_case3(case: C3) -> Result:
    res = Result(status="?")
    with tempfile.TemporaryDirectory() as tmp:
        de_path = Path(tmp) / "slides_t.de.py"
        en_path = Path(tmp) / "slides_t.en.py"
        names = set(case.base)
        for r in case.rounds:
            names |= set(r.de_order) | set(r.en_order)
        cur = {n: sp.base_body(n) for n in names if kind(n) in ("code", "md")}
        de_path.write_text(spx._deck("de", case.base, cur), encoding="utf-8")
        en_path.write_text(spx._deck("en", case.base, cur), encoding="utf-8")
        sp._record(de_path, en_path)
        for ri, rnd in enumerate(case.rounds):
            res.round = ri
            res.passes = []
            de_b = spx._bodies(cur, rnd.de_edits, "de")
            en_b = spx._bodies(cur, rnd.en_edits, "en")
            de_path.write_text(spx._deck("de", rnd.de_order, de_b), encoding="utf-8")
            en_path.write_text(spx._deck("en", rnd.en_order, en_b), encoding="utf-8")
            final = dict(cur)
            for n in set(rnd.de_edits) | set(rnd.en_edits):
                if n in rnd.de_edits and n in rnd.en_edits:
                    final[n] = (de_b if rnd.conflict_side == "de" else en_b)[n]
                else:
                    final[n] = (de_b if n in rnd.de_edits else en_b)[n]
            prev = case.base if ri == 0 else case.rounds[ri - 1].expected
            removed = set(prev) - set(rnd.expected)
            regrouped = sp._group_of(prev) != sp._group_of(rnd.expected)
            status = None
            withheld = False
            asked = False
            for _ in range(sp.MAX_PASSES):
                _, diff = sp._diff(de_path, en_path)
                if diff.is_clean:
                    status, detail = _check3(de_path, en_path, rnd, final, asked)
                    res.detail = detail
                    break
                res.passes.append([(i.key, i.action) for i in diff.items])
                decisions = {}
                for item in diff.items:
                    answers = sp.doc_apply.item_answers(item)
                    choice = None
                    body = None
                    if item.action in sp._ORDER_Q:
                        choice = rnd.order_answer or rnd.intent
                        asked = True
                    elif item.action == "conflict_shared":
                        choice = rnd.conflict_side
                    elif item.action == "translate_new" and item.key.startswith("id:"):
                        sid = item.key[3:]
                        nm = sid.split("-", 1)[1]
                        in_de, in_en = nm in rnd.de_order, nm in rnd.en_order
                        if in_de != in_en:
                            body = _slide_body(nm, "en" if in_de else "de")
                    elif item.action in sp._REMOVE_Q and rnd.rm is not None:
                        choice = rnd.rm if rnd.rm in answers else None
                    elif (
                        item.action in sp._REMOVE_Q
                        and (removed or regrouped)
                        and "remove" in answers
                    ):
                        choice = "remove"
                    elif (
                        item.action in ("verify_cold", "verify_translation")
                        and "confirm" in answers
                    ):
                        choice = "confirm"
                    if body is not None:
                        res.questions[item.action] = res.questions.get(item.action, 0) + 1
                        decisions[item.key] = sp.doc_apply.Decision(key=item.key, body=body)
                    elif choice is not None:
                        res.questions[item.action] = res.questions.get(item.action, 0) + 1
                        decisions[item.key] = sp.doc_apply.Decision(key=item.key, choice=choice)
                    elif sp.doc_apply.decision_vocabulary(item.action) and not answers:
                        status = "manual_stall"
                        res.detail = f"{item.action} {item.key}"
                        break
                    elif answers:
                        status = "unanswered_question"
                        res.detail = f"{item.action} {item.key} answers={list(answers)}"
                        break
                if status:
                    break
                try:
                    outcome = sp._apply(de_path, en_path, decisions)
                except Exception as exc:  # noqa: BLE001
                    status = "apply_exception"
                    res.detail = repr(exc)[:300]
                    break
                if outcome.error is not None:
                    status = "apply_error"
                    res.detail = str(outcome.error)[:300]
                    break
                withheld = bool(outcome.to_payload().get("verify_withheld"))
            if status is None:
                status = "livelock_withheld" if withheld else "livelock_stalled"
            if status != "converged":
                res.status = status if ri == 0 else f"r{ri + 1}:{status}"
                return res
            cur = {n: final[n] for n in cur}
        res.status = "converged"
        return res


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def pool(order):
    return [n for n in order if kind(n) in ("code", "md")]


def grp(order):
    return sp._group_of(order)


def moves1(order, x):
    """All orders with x re-inserted at another position (after the first slide)."""
    rest = [n for n in order if n != x]
    out = []
    for p in range(1, len(rest) + 1):
        new = (*rest[:p], x, *rest[p:])
        if new != tuple(order):
            out.append(new)
    return list(dict.fromkeys(out))


def cross_moves(order, x):
    g = grp(order)[x]
    return [o for o in moves1(order, x) if grp(o)[x] != g]


def one(R, base, new, ed_r=(), ed_o=(), cf=None, **kw):
    """R moves base->new; ed_r edits on R, ed_o on the other half."""
    o = OTHER[R]
    de, en = (new, base) if R == "de" else (base, new)
    de_e, en_e = (ed_r, ed_o) if R == "de" else (ed_o, ed_r)
    kw.setdefault("expected", new)
    return R3(de, en, tuple(de_e), tuple(en_e), cf or o, R, **kw)


def edit_variants(x, y):
    """(ed_r, ed_o, conflict_side_rel) with y a different pool cell (or None)."""
    v = [((), (), None), ((x,), (), None), ((), (x,), None)]
    if y:
        v += [((y,), (), None), ((), (y,), None), ((x,), (y,), None), ((y,), (x,), None)]
    v += [((x,), (x,), "R"), ((x,), (x,), "O")]
    return v


def _mk(R, base, new, ed_r, ed_o, rel, **kw):
    cf = None if rel is None else (R if rel == "R" else OTHER[R])
    return one(R, base, new, ed_r, ed_o, cf, **kw)


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------

MD_BASES = (
    ("S0", "a", "m1", "m2", "S1", "b", "k1", "S2"),
    ("S0", "a", "c1", "m1", "S1", "b", "m2", "c2", "S2"),
    ("S0", "m1", "a", "c1", "S1", "k1", "b", "S2", "e", "m2", "S3"),
    ("S0", "a", "m1", "U1", "c1", "m2", "S1", "b", "S2"),
)


def fam_md_cross():
    rng = random.Random(11421)
    for bi, base in enumerate(MD_BASES):
        pl = pool(base)
        for x in pl:
            ys = [n for n in pl if n != x]
            for new in moves1(base, x):
                cross = grp(new)[x] != grp(base)[x]
                for R in ("en", "de"):
                    y = rng.choice(ys)
                    vs = edit_variants(x, y) if cross else edit_variants(x, None)[:3]
                    for ed_r, ed_o, rel in vs:
                        yield C3(
                            f"md_cross1_b{bi}",
                            base,
                            (_mk(R, base, new, ed_r, ed_o, rel),),
                            "cross" if cross else "ingroup",
                        )
        # two cells moving at once (mixed pools)
        for x, y in itertools.combinations(pl, 2):
            cands = []
            for n1 in moves1(base, x):
                cands += list(moves1(n1, y))
            cands = [c for c in dict.fromkeys(cands) if c != base]
            for new in rng.sample(cands, min(40, len(cands))):
                for R in ("en", "de"):
                    for ed_r, ed_o, rel in rng.sample(edit_variants(x, y), 3):
                        yield C3(f"md_cross2_b{bi}", base, (_mk(R, base, new, ed_r, ed_o, rel),))


SPLIT_BASE = ("S0", "a", "c1", "c2", "m1", "S1", "b", "c3", "S2")


def _insert_after_pred(order, ref, new_name):
    """Insert new_name into order after its predecessor in ref (pred/succ variants)."""
    i = ref.index(new_name)
    pred = ref[i - 1]
    succ = ref[i + 1] if i + 1 < len(ref) else None
    a = list(order)
    a.insert(a.index(pred) + 1, new_name)
    b = list(order)
    if succ is not None and succ in order:
        b.insert(b.index(succ), new_name)
    else:
        b.append(new_name)
    return tuple(a), tuple(b)


def fam_split_insert():
    rng = random.Random(11422)
    base = SPLIT_BASE
    for q in range(2, len(base) + 1):
        with_s9 = (*base[:q], "S9", *base[q:])
        for x in ("c1", "c2", "m1", "c3"):
            for mv in moves1(with_s9, x):
                mv_wo = tuple(n for n in mv if n != "S9")
                for R in ("en", "de"):
                    for ed_r, ed_o in (((), ()), ((), (x,))):
                        # (1) S9 on BOTH halves: other half = base + S9 at same spot
                        a0, b0 = _insert_after_pred(mv_wo, with_s9, "S9")
                        acc = tuple(o for o in dict.fromkeys((a0, b0)) if o != mv)
                        yield C3(
                            "split_both",
                            base,
                            (one(R, with_s9, mv, ed_r, ed_o, accept=acc),),
                            f"S9@{q}",
                        )
                        # (2) S9 only on the mover half (agent translates S9)
                        yield C3(
                            "split_mover_only", base, (one(R, base, mv, ed_r, ed_o),), f"S9@{q}"
                        )
                        # (3) S9 only on the OTHER half; mover moves x
                        if x in with_s9 and with_s9.index("S9") > 0:
                            a, b = _insert_after_pred(mv_wo, with_s9, "S9")
                            if with_s9[with_s9.index("S9") - 1] == x:
                                continue
                            acc = (b,) if b != a else ()
                            yield C3(
                                "split_other_only",
                                base,
                                (one(R, base, mv_wo, ed_r, ed_o, other_order=with_s9),)
                                if False
                                else (
                                    R3(
                                        *((mv_wo, with_s9) if R == "de" else (with_s9, mv_wo)),
                                        *((ed_r, ed_o) if R == "de" else (ed_o, ed_r)),
                                        OTHER[R],
                                        R,
                                        a,
                                        accept=acc,
                                    ),
                                ),
                                f"S9@{q}",
                            )


TG = ("S0", "a", "c1", "m1", "S1", "b", "c2", "S2", "e", "c3", "S3")


def fam_move_insdel():
    rng = random.Random(11423)
    base = TG
    pl = pool(base)
    for x in pl:
        for new in cross_moves(base, x):
            for R in ("en", "de"):
                o = OTHER[R]
                ys = [n for n in pl if n != x]
                for y in ys:
                    # delete y on R
                    d_r = tuple(n for n in new if n != y)
                    yield C3("mid_del_other_cell@R", base, (one(R, base, d_r),))
                    yield C3("mid_del_other_cell@R", base, (one(R, base, d_r, (), (x,)),), "ed_x@O")
                    # delete y on other half
                    d_o = tuple(n for n in base if n != y)
                    r = one(R, base, new, expected=d_r)
                    r = R3(*((new, d_o) if R == "de" else (d_o, new)), (), (), o, R, d_r)
                    yield C3("mid_del_other_cell@O", base, (r,))
                    # insert n1 on R
                    p = rng.randrange(1, len(new) + 1)
                    i_r = (*new[:p], "n1", *new[p:])
                    yield C3("mid_ins@R", base, (one(R, base, i_r),))
                    # insert k1 on the other half
                    p = rng.randrange(1, len(base) + 1)
                    i_o = (*base[:p], "k1", *base[p:])
                    exp = (*new, "k1")
                    r = R3(*((new, i_o) if R == "de" else (i_o, new)), (), (), o, R, exp, ("k1",))
                    yield C3("mid_ins@O", base, (r,))
                    # other half deletes the moved cell (ambiguous: accept both)
                    gone = tuple(n for n in base if n != x)
                    no_x = tuple(n for n in new if n != x)
                    for ed_r in ((), (x,)):
                        r = R3(
                            *((new, gone) if R == "de" else (gone, new)),
                            *((ed_r, ()) if R == "de" else ((), ed_r)),
                            o,
                            R,
                            no_x,
                            accept=(new,),
                        )
                        yield C3("mid_other_deletes_moved", base, (r,), f"ed_r={ed_r}")


BM_BASE = ("S0", "a", "c1", "m1", "S1", "b", "c2", "S2", "e", "S3")


def _apply_move(order, mover_order, x):
    """Place x into order after its predecessor in mover_order."""
    rest = [n for n in order if n != x]
    pred = mover_order[mover_order.index(x) - 1]
    rest.insert(rest.index(pred) + 1, x)
    return tuple(rest)


def fam_both_move():
    rng = random.Random(11424)
    base = BM_BASE
    pl = pool(base)
    for x in pl:
        cms = cross_moves(base, x)
        for new in cms:
            # same cell, same destination on both halves
            for ed in ((), (x,)):
                yield C3("bm_same_dest", base, (R3(new, new, ed, (), "de", "de", new),))
                yield C3("bm_same_dest", base, (R3(new, new, (), ed, "en", "en", new),))
            # same cell, different destinations
            for new2 in rng.sample(moves1(base, x), min(4, len(moves1(base, x)))):
                if new2 == new:
                    continue
                for intent in ("de", "en"):
                    exp, alt = (new, new2) if intent == "de" else (new2, new)
                    for de_e, en_e, cf in (((), (), "de"), ((x,), (), "de"), ((x,), (x,), "en")):
                        yield C3(
                            "bm_diff_dest",
                            base,
                            (R3(new, new2, de_e, en_e, cf, intent, exp, alt=alt),),
                        )
        # different cells moved on each half
        for y in pl:
            if y == x:
                continue
            for de_o in cms:
                for en_o in rng.sample(moves1(base, y), 3):
                    if de_o[de_o.index(x) - 1] == y or en_o[en_o.index(y) - 1] == x:
                        continue
                    merged = _apply_move(en_o, de_o, x)
                    if _apply_move(de_o, en_o, y) != merged:
                        continue
                    for intent in ("de", "en"):
                        acc = (de_o if intent == "de" else en_o,)
                        for de_e, en_e in (((), ()), ((y,), (x,))):
                            yield C3(
                                "bm_diff_cells",
                                base,
                                (R3(de_o, en_o, de_e, en_e, "de", intent, merged, accept=acc),),
                            )


DUP_BASES = (
    ("S0", "a", "d1", "c1", "S1", "b", "d2", "S2"),
    ("S0", "d1", "a", "S1", "d2", "b", "c1", "S2"),
    ("S0", "a", "d1", "m1", "S1", "b", "S2", "e", "d2", "S3"),
)


def fam_dup_cross():
    for bi, base in enumerate(DUP_BASES):
        for x in pool(base):
            seen = set()
            for new in moves1(base, x):
                key = tuple("d" if n[0] == "d" else n for n in new)
                if key in seen or key == tuple("d" if n[0] == "d" else n for n in base):
                    continue
                seen.add(key)
                sw = tuple({"d1": "d2", "d2": "d1"}.get(n, n) for n in new)
                c = [n for n in pool(base) if n[0] != "d"][0]
                for R in ("en", "de"):
                    for ed_r, ed_o in (((), ()), ((x,), ()), ((), (x,)), ((), (c,)), ((c,), ())):
                        acc = (sw,) if (x[0] == "d" and ed_o) or "d1" in ed_o else ()
                        yield C3(
                            f"dup_cross_b{bi}", base, (one(R, base, new, ed_r, ed_o, accept=acc),)
                        )


def fam_r2_cross():
    rng = random.Random(11425)
    base = BM_BASE
    pl = pool(base)
    for x in pl:
        for new1 in cross_moves(base, x):
            for R1 in ("en", "de"):
                for e1 in ((), (x,)):
                    r1 = one(R1, base, new1, (), e1)
                    ops = []
                    ops.append(("back", base, (), ()))
                    fur = [o for o in cross_moves(new1, x) if o != base]
                    if fur:
                        ops.append(("further", rng.choice(fur), (), ()))
                    ops.append(("edit_x_R2", new1, (x,), ()))
                    ops.append(("edit_x_other", new1, (), (x,)))
                    y = rng.choice([n for n in pl if n != x])
                    cy = cross_moves(new1, y)
                    if cy:
                        ops.append(("move_y", rng.choice(cy), (), ()))
                        ops.append(("move_y_edit_x", rng.choice(cy), (x,), ()))
                    for tag, new2, er, eo in ops:
                        for R2 in ("en", "de"):
                            if tag in ("edit_x_R2", "edit_x_other"):
                                r2 = one(R2, new1, new1, er, eo)
                            else:
                                r2 = one(R2, new1, new2, er, eo)
                            yield C3("r2_cross_" + tag, base, (r1, r2))
                    # other half deletes x in round 2
                    gone = tuple(n for n in new1 if n != x)
                    yield C3("r2_cross_del", base, (r1, one(OTHER[R1], new1, gone)))


CH3 = ("S0", "a", "c1", "S1", "b", "c2", "S2", "e", "c3", "S3")
CH3M = ("S0", "a", "m1", "S1", "b", "c2", "S2", "e", "k1", "S3")
CH4 = ("S0", "a", "c1", "S1", "b", "c2", "S2", "e", "m3", "S3", "f", "c4", "S4")


def _layout(base, assign):
    """assign: cell -> (slide, 'start'|'end')."""
    slides = [n for n in base if kind(n) == "slide"]
    idd = {
        s: [n for n in base if grp(base).get(n) == s and kind(n) not in ("code", "md")]
        for s in slides
    }
    out = []
    for s in slides:
        out.append(s)
        st = sorted(c for c, (g, w) in assign.items() if g == s and w == "start")
        en = sorted(c for c, (g, w) in assign.items() if g == s and w == "end")
        out += st + idd[s] + en
    return tuple(out)


def fam_chains():
    rng = random.Random(11426)
    for name, base, sample in (("ch3", CH3, None), ("ch3m", CH3M, None), ("ch4", CH4, 2000)):
        slides = [n for n in base if kind(n) == "slide"][:-1]  # last slide: tail anchor
        cells = pool(base)
        spots = [(s, w) for s in slides for w in ("start", "end")]
        allas = list(itertools.product(spots, repeat=len(cells)))
        if sample:
            allas = rng.sample(allas, sample)
        for a in allas:
            new = _layout(base, dict(zip(cells, a)))
            if new == base:
                continue
            for R in ("en", "de"):
                for ed_r, ed_o in rng.sample(
                    [((), ()), ((cells[0],), ()), ((), (cells[1],)), ((cells[2],), (cells[0],))], 2
                ):
                    yield C3(name, base, (one(R, base, new, ed_r, ed_o),))


def fam_policy():
    """Answer-policy variants: order questions answered with the OTHER half,
    removal questions answered ``keep``. The result may be either half's order."""
    rng = random.Random(11427)
    srcs = []
    for base in (MD_BASES[1], TG, CH3):
        for x in pool(base):
            for new in cross_moves(base, x):
                srcs.append((base, new, x))
    for base, new, x in srcs:
        for R in ("en", "de"):
            o = OTHER[R]
            for ed_r, ed_o in (((), ()), ((x,), ()), ((), (x,))):
                yield C3(
                    "pol_order_other",
                    base,
                    (one(R, base, new, ed_r, ed_o, order_answer=o, accept=(base,)),),
                )
                yield C3(
                    "pol_rm_keep", base, (one(R, base, new, ed_r, ed_o, rm="keep", accept=(base,)),)
                )
            # other deletes the moved cell, keep on removal
            gone = tuple(n for n in base if n != x)
            r = R3(
                *((new, gone) if R == "de" else (gone, new)),
                (),
                (),
                o,
                R,
                new,
                accept=(tuple(n for n in new if n != x),),
                rm="keep",
            )
            yield C3("pol_rm_keep_other_deletes", base, (r,))
    # both halves move, order answered with the half NOT intended
    for x in pool(BM_BASE):
        for new in cross_moves(BM_BASE, x):
            for new2 in rng.sample(moves1(BM_BASE, x), 3):
                if new2 == new:
                    continue
                yield C3(
                    "pol_bm_answer_en", BM_BASE, (R3(new, new2, (), (), "de", "en", new2, alt=new),)
                )


FAMILIES = {
    "md_cross": fam_md_cross,
    "split_insert": fam_split_insert,
    "move_insdel": fam_move_insdel,
    "both_move": fam_both_move,
    "dup_cross": fam_dup_cross,
    "r2_cross": fam_r2_cross,
    "chains": fam_chains,
    "policy": fam_policy,
}
