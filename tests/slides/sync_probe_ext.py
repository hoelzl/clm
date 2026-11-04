"""Extended sync-probe families (#1130, from the #1144 adversarial probe).

Shapes the base probe (``sync_probe.py``) does not reach: two and three
rounds of moves (the ledger's recorded spans re-recorded between them), edits
on BOTH halves in one round (distinct cells and conflicts), larger groups,
mixed code + markdown pools, byte-identical duplicates among other moves,
moves plus inserts and deletes on one half, three slide groups, whole slide
groups swapped, and ``remove_vs_edit`` answered ``keep`` or ``remove``.

Families that fit the probe's ``Round`` model run through
``sync_probe.run_case``. Edits on both halves and answer policies need
:class:`Round2` / :func:`run_case2`: per-half edit sets (an edit appends a
per-half suffix, so one cell edited on both halves conflicts), the half that
answers ``conflict_shared``, and the ``remove_vs_edit`` answer.
``scripts/sync_probe.py`` runs these families with the base ones.
"""

from __future__ import annotations

import itertools
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tests.slides import sync_probe as sp
from tests.slides.sync_probe import OTHER, Case, Result, Round, kind, run_case, wrap

# ---------------------------------------------------------------------------
# Extended runner: per-half edits + answer policies
# ---------------------------------------------------------------------------

#: per-side edit suffix, so the same cell edited on both halves differs
SUFFIX = {"code": {"de": "0", "en": "7"}, "md": {"de": " edited", "en": " changed"}}


@dataclass(frozen=True)
class Round2:
    de_order: tuple[str, ...]
    en_order: tuple[str, ...]
    de_edits: tuple[str, ...]
    en_edits: tuple[str, ...]
    conflict_side: str  # answers conflict_shared
    intent: str  # answers order questions
    expected: tuple[str, ...]
    unordered: tuple[str, ...] = ()
    accept: tuple[tuple[str, ...], ...] = ()
    alt: tuple[str, ...] | None = None
    #: answer for remove_vs_edit ("remove"/"keep"/None = probe default)
    rve: str | None = None
    #: cells whose edit must NOT survive (e.g. remove_vs_edit answered remove)
    #: -- not used for the label; expected order decides presence
    #: final-body override: name -> side whose edit wins ("de"/"en"/"none")
    winner: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Case2:
    family: str
    base: tuple[str, ...]
    rounds: tuple[Round2, ...]
    tag: str = ""

    @property
    def id(self) -> str:
        parts = [self.family, self.tag, " ".join(self.base)]
        for r in self.rounds:
            parts.append(
                f"DE[{' '.join(r.de_order)}] EN[{' '.join(r.en_order)}] "
                f"edit@de:{','.join(r.de_edits) or '-'} edit@en:{','.join(r.en_edits) or '-'} "
                f"conflict={r.conflict_side} intent={r.intent} rve={r.rve}"
            )
        return " | ".join(p for p in parts if p)


def _cell(name: str, lang: str, body: dict[str, str]) -> str:
    k = kind(name)
    if k in ("slide", "subslide", "vo"):
        return sp.cell_text(name, lang, frozenset())
    if k == "code":
        return f"# %%\n{body[name]}\n\n"
    return f"# %% [markdown]\n#\n{body[name]}\n\n"


def _deck(lang, order, body):
    return (sp.HEADER[lang] + "".join(_cell(n, lang, body) for n in order)).rstrip("\n") + "\n"


def _lab(name: str, body: dict[str, str]) -> str:
    k = kind(name)
    if k in ("slide", "subslide", "vo"):
        return sp.label(name, frozenset())
    return "body:" + body[name]


def _bodies(cur: dict[str, str], edits, lang) -> dict[str, str]:
    out = dict(cur)
    for n in edits:
        out[n] = cur[n] + SUFFIX[kind(n)][lang]
    return out


def _check2(de_path, en_path, rnd: Round2, final: dict[str, str], asked: bool):
    exp = [_lab(n, final) for n in rnd.expected]
    got = {}
    for lang, path in (("de", de_path), ("en", en_path)):
        labels, anomalies = sp.parse_labels(path.read_text(encoding="utf-8"), lang)
        if anomalies:
            return "lang_or_kind_anomaly", f"{lang}: {anomalies}"
        got[lang] = labels
    for lang in ("de", "en"):
        if sorted(got[lang]) != sorted(exp):
            return "lost_or_duplicated", f"{lang}: got={got[lang]} exp={exp}"
    if got["de"] != got["en"]:
        return "halves_disagree", f"de={got['de']} en={got['en']}"
    skip = {_lab(n, final) for n in rnd.unordered}
    seq = [x for x in got["de"] if x not in skip]
    oks = [[_lab(n, final) for n in o] for o in (rnd.expected, *rnd.accept)]
    if any(seq == [x for x in o if x not in skip] for o in oks):
        return "converged", ""
    if rnd.alt is not None and seq == [_lab(n, final) for n in rnd.alt]:
        return ("misplaced_to_other" if asked else "silent_pick"), f"got={seq}"
    return "misplaced", f"got={seq} exp={exp}"


def run_case2(case: Case2) -> Result:
    res = Result(status="?")
    with tempfile.TemporaryDirectory() as tmp:
        de_path = Path(tmp) / "slides_t.de.py"
        en_path = Path(tmp) / "slides_t.en.py"
        names = set(case.base)
        for r in case.rounds:
            names |= set(r.de_order) | set(r.en_order)
        cur = {n: sp.base_body(n) for n in names if kind(n) in ("code", "md")}
        de_path.write_text(_deck("de", case.base, cur), encoding="utf-8")
        en_path.write_text(_deck("en", case.base, cur), encoding="utf-8")
        sp._record(de_path, en_path)
        for ri, rnd in enumerate(case.rounds):
            res.round = ri
            res.passes = []
            de_b = _bodies(cur, rnd.de_edits, "de")
            en_b = _bodies(cur, rnd.en_edits, "en")
            de_path.write_text(_deck("de", rnd.de_order, de_b), encoding="utf-8")
            en_path.write_text(_deck("en", rnd.en_order, en_b), encoding="utf-8")
            final = dict(cur)
            for n in set(rnd.de_edits) | set(rnd.en_edits):
                if n in rnd.de_edits and n in rnd.en_edits:
                    final[n] = (de_b if rnd.conflict_side == "de" else en_b)[n]
                else:
                    final[n] = (de_b if n in rnd.de_edits else en_b)[n]
            for n, w in rnd.winner:
                final[n] = {"de": de_b, "en": en_b, "none": cur}[w][n]
            prev = case.base if ri == 0 else case.rounds[ri - 1].expected
            removed = set(prev) - set(rnd.expected)
            regrouped = sp._group_of(prev) != sp._group_of(rnd.expected)
            status = None
            withheld = False
            asked = False
            for _ in range(sp.MAX_PASSES):
                _, diff = sp._diff(de_path, en_path)
                if diff.is_clean:
                    status, detail = _check2(de_path, en_path, rnd, final, asked)
                    res.detail = detail
                    break
                res.passes.append([(i.key, i.action) for i in diff.items])
                decisions = {}
                for item in diff.items:
                    answers = sp.doc_apply.item_answers(item)
                    choice = None
                    if item.action in sp._ORDER_Q:
                        choice = rnd.intent
                        asked = True
                    elif item.action == "conflict_shared":
                        choice = rnd.conflict_side
                    elif item.action == "remove_vs_edit" and rnd.rve is not None:
                        choice = rnd.rve if rnd.rve in answers else None
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
                    if choice is not None:
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


def run_any(case) -> Result:
    return run_case2(case) if isinstance(case, Case2) else run_case(case)


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------


def _perms(grp, n, rng, include_identity=False):
    allp = list(itertools.permutations(grp))
    if not include_identity:
        allp.remove(tuple(grp))
    return allp if len(allp) <= n else rng.sample(allp, n)


def fam_r2_moves():
    """Two rounds, moves in both; the round-2 mover and edit side vary; groups
    beyond ``four`` (5-cell with markdown, 3 code cells)."""
    rng = random.Random(1144)
    groups = (("a", "c1", "b", "c2"), ("a", "c1", "m1", "b", "c2"), ("c0", "a", "c1", "b", "c2"))
    for grp in groups:
        pool = [n for n in grp if n[0] in "cm"]
        base = wrap(grp)
        for R1 in ("en", "de"):
            for perm1 in _perms(grp, 24, rng):
                new1 = wrap(perm1)
                for ed1, s1 in (((), R1), ((pool[0],), OTHER[R1]), ((pool[-1],), R1)):
                    de1, en1 = (new1, base) if R1 == "de" else (base, new1)
                    r1 = Round(de1, en1, ed1, s1, R1, new1)
                    for R2 in ("en", "de"):
                        for perm2 in _perms(perm1, 3, rng):
                            new2 = wrap(perm2)
                            de2, en2 = (new2, new1) if R2 == "de" else (new1, new2)
                            ed2 = rng.choice([(), (pool[1],), tuple(ed1)])
                            s2 = rng.choice(("de", "en"))
                            yield Case(
                                f"r2_moves_{len(grp)}",
                                base,
                                (r1, Round(de2, en2, ed2, s2, R2, new2)),
                            )


def fam_r3_moves():
    """Three rounds of moves alternating halves (stale spans after two re-records)."""
    rng = random.Random(3)
    grp = ("a", "c1", "b", "c2", "c3")
    base = wrap(grp)
    for _ in range(600):
        prev = base
        rounds = []
        cur = grp
        for _ri in range(3):
            perm = rng.choice(_perms(cur, 10, rng))
            new = wrap(perm)
            R = rng.choice(("de", "en"))
            de, en = (new, prev) if R == "de" else (prev, new)
            ed = rng.choice([(), ("c1",), ("c3",), ("c2", "c3")])
            rounds.append(Round(de, en, ed, rng.choice(("de", "en")), R, new))
            prev, cur = new, perm
        yield Case("r3_moves", base, tuple(rounds))


def fam_both_edit():
    """Edits on both halves in one round (distinct cells, and one cell on both =
    conflict), with one half reordering."""
    grp = ("a", "c1", "b", "c2", "m1")
    base = wrap(grp)
    pool = ("c1", "c2", "m1")
    for R in ("en", "de"):
        for perm in itertools.permutations(grp):
            new = wrap(perm)
            de, en = (new, base) if R == "de" else (base, new)
            for x, y in itertools.permutations(pool, 2):
                # distinct cells, one per half
                yield Case2("both_edit_distinct", base, (Round2(de, en, (x,), (y,), "de", R, new),))
            for x in pool:
                for cs in ("de", "en"):
                    yield Case2(
                        "both_edit_conflict", base, (Round2(de, en, (x,), (x,), cs, R, new),)
                    )


def fam_both_edit_both_move():
    """Both halves reorder AND both halves edit (conflicts included)."""
    rng = random.Random(7)
    grp = ("a", "c1", "b", "c2")
    base = wrap(grp)
    perms = _perms(grp, 23, rng)
    for p, q in itertools.permutations(perms, 2):
        for intent in ("de", "en"):
            de, en = wrap(p), wrap(q)
            exp, alt = (de, en) if intent == "de" else (en, de)
            for dee, ene, cs in rng.sample(
                [
                    (("c1",), ("c2",), "de"),
                    (("c2",), ("c1",), "en"),
                    (("c1",), ("c1",), "de"),
                    (("c1",), ("c1",), "en"),
                    (("c1", "c2"), ("c2",), "en"),
                ],
                2,
            ):
                yield Case2(
                    "both_edit_both_move",
                    base,
                    (Round2(de, en, dee, ene, cs, intent, exp, alt=alt),),
                )


def fam_big_group():
    """3 id'd cells and 3-4 code cells, sampled permutations, edits either side."""
    rng = random.Random(1130)
    for grp in (("a", "c1", "b", "c2", "e", "c3"), ("a", "c1", "b", "c2", "e", "c3", "c4")):
        code = [n for n in grp if n[0] == "c"]
        for R in ("en", "de"):
            for perm in _perms(grp, 400, rng):
                for ed, side in (
                    ((), R),
                    ((code[0],), OTHER[R]),
                    ((code[1], code[-1]), OTHER[R]),
                    ((code[-1],), R),
                ):
                    yield sp.one_half(f"big_{len(grp)}", grp, R, perm, ed, side)


def fam_mixed_pool():
    """Code + markdown pool, edits on either half; 6 cells."""
    rng = random.Random(11)
    grp = ("a", "m1", "c1", "b", "m2", "c2")
    pool = ("m1", "c1", "m2", "c2")
    for R in ("en", "de"):
        for perm in _perms(grp, 300, rng):
            for ed, side in (
                ((), R),
                (("m1",), OTHER[R]),
                (("c2",), OTHER[R]),
                (("m1", "c1"), OTHER[R]),
                (("m2", "c2"), R),
            ):
                yield sp.one_half("mixed_pool6", grp, R, perm, ed, side, tag="+".join(pool))


def fam_dup_mix():
    """Byte-identical duplicates with other moved cells and edits."""
    rng = random.Random(5)
    for grp in (("a", "d1", "c1", "b", "d2", "m1"), ("d1", "a", "c1", "d2", "b", "c2")):
        seen = set()
        uniq = []
        for perm in itertools.permutations(grp):
            key = tuple("d" if n[0] == "d" else n for n in perm)
            if key not in seen:
                seen.add(key)
                uniq.append(perm)
        sample = rng.sample(uniq, min(150, len(uniq)))
        others = [n for n in grp if n[0] in "cm"]
        for perm in sample:
            swapped = tuple({"d1": "d2", "d2": "d1"}.get(n, n) for n in perm)
            for R in ("en", "de"):
                for ed, side in (
                    ((), R),
                    ((others[0],), OTHER[R]),
                    (("d1",), OTHER[R]),
                    (("d1", others[-1]), R),
                ):
                    acc = (wrap(swapped),) if ("d1" in ed and side != R) else ()
                    yield sp.one_half(f"dup_mix_{grp[0]}", grp, R, perm, ed, side, accept=acc)


def fam_move_ins_del():
    """Moves plus inserts and deletes on the SAME half, other half edits."""
    rng = random.Random(9)
    grp = ("a", "c1", "b", "c2", "c3")
    base = wrap(grp)
    for R in ("en", "de"):
        for perm in _perms(grp, 60, rng):
            for gone in ("c1", "c3", "b"):
                pr = [n for n in perm if n != gone]
                for new in ("n1", "k1"):
                    for pos in (0, 2, len(pr)):
                        order = (*pr[:pos], new, *pr[pos:])
                        r_order = wrap(order)
                        keep = [n for n in ("c2", "c3", "c1") if n != gone]
                        for ed, side in (((), R), ((keep[0],), OTHER[R]), ((keep[0],), R)):
                            de, en = (r_order, base) if R == "de" else (base, r_order)
                            yield Case(
                                "move_ins_del",
                                base,
                                (Round(de, en, ed, side, R, r_order),),
                                f"del_{gone} ins_{new}@{pos}",
                            )


def fam_three_groups_fixed():
    """Three slide groups with the anchors fixed: cells permuted within and
    across groups (3 slots per group kept), edits either side."""
    rng = random.Random(34)
    cells = ("a", "c1", "m1", "b", "c2", "e", "c3", "c4")
    base = ("S0", "a", "c1", "m1", "S1", "b", "c2", "S2", "e", "c3", "c4", "S3")
    perms = rng.sample(list(itertools.permutations(cells)), 1500)
    for R in ("en", "de"):
        for perm in perms:
            new = ("S0", *perm[:3], "S1", *perm[3:5], "S2", *perm[5:], "S3")
            de, en = (new, base) if R == "de" else (base, new)
            ed, side = rng.choice(
                (((), R), (("c1",), OTHER[R]), (("c2", "m1"), OTHER[R]), (("c4",), R))
            )
            yield Case("three_groups_fixed", base, (Round(de, en, ed, side, R, new),))
    # within-group only permutations (no regroup): the clean shape
    for R in ("en", "de"):
        for p1 in itertools.permutations(("a", "c1", "m1")):
            for p3 in itertools.permutations(("e", "c3", "c4")):
                new = ("S0", *p1, "S1", "c2", "b", "S2", *p3, "S3")
                de, en = (new, base) if R == "de" else (base, new)
                for ed, side in (((), R), (("c1", "c4"), OTHER[R]), (("c2",), OTHER[R])):
                    yield Case("three_groups_ingroup", base, (Round(de, en, ed, side, R, new),))


def fam_slide_swap():
    """Whole slide groups swapped on one half, then a second round moves cells."""
    rng = random.Random(44)
    g = {"S1": ("S1", "a", "c1"), "S2": ("S2", "b", "c2", "c3"), "S3": ("S3", "e", "m1")}
    base = ("S0", *g["S1"], *g["S2"], *g["S3"])
    for order in itertools.permutations(("S1", "S2", "S3")):
        new1 = ("S0", *[x for s in order for x in g[s]])
        for R in ("en", "de"):
            for ed, side in (((), R), (("c2",), OTHER[R]), (("m1",), R)):
                de, en = (new1, base) if R == "de" else (base, new1)
                r1 = Round(de, en, ed, side, R, new1)
                yield Case("slide_swap", base, (r1,))
                # second round: random in-place permutation of the non-S0 tail
                tailcells = new1[1:]
                for _ in range(15):
                    # move one non-anchor cell to a random spot after S0
                    movable = [n for n in tailcells if kind(n) != "slide"]
                    x = rng.choice(movable)
                    rest = [n for n in tailcells if n != x]
                    pos = rng.randrange(1, len(rest) + 1)
                    new2 = ("S0", *rest[:pos], x, *rest[pos:])
                    R2 = rng.choice(("de", "en"))
                    de2, en2 = (new2, new1) if R2 == "de" else (new1, new2)
                    ed2 = rng.choice(((), ("c1",), ("c3",)))
                    yield Case(
                        "slide_swap_r2",
                        base,
                        (r1, Round(de2, en2, ed2, rng.choice(("de", "en")), R2, new2)),
                    )


def fam_policy_rve():
    """Delete on one half + edit on the other (remove_vs_edit), with moves;
    answered ``keep`` and ``remove``."""
    grp = ("a", "c1", "b", "c2", "m1")
    base = wrap(grp)
    rng = random.Random(77)
    for D in ("de", "en"):  # half that deletes
        E = OTHER[D]
        for gone in ("c1", "c2", "m1"):
            for mover in ("none", "del", "edit"):
                perms = [grp] if mover == "none" else _perms(grp, 30, rng)
                for perm in perms:
                    moved = wrap(perm)
                    del_order = tuple(n for n in (moved if mover == "del" else base) if n != gone)
                    edit_order = moved if mover == "edit" else base
                    de, en = (del_order, edit_order) if D == "de" else (edit_order, del_order)
                    de_e = (gone,) if E == "de" else ()
                    en_e = (gone,) if E == "en" else ()
                    intent = D if mover == "del" else E
                    # keep: the edited cell survives at its edit-half spot;
                    # other cells in the intent order
                    if mover == "del":
                        keep_exp = None  # position of kept cell ambiguous
                        keep_exp = tuple(del_order)
                        unordered = (gone,)
                        keep_exp = (*del_order, gone)
                    else:
                        keep_exp = edit_order
                        unordered = ()
                    yield Case2(
                        "rve_keep",
                        base,
                        (
                            Round2(
                                de,
                                en,
                                de_e,
                                en_e,
                                E,
                                intent,
                                keep_exp,
                                unordered=unordered,
                                rve="keep",
                            ),
                        ),
                        f"del_{gone}@{D} mover={mover}",
                    )
                    yield Case2(
                        "rve_remove",
                        base,
                        (
                            Round2(
                                de,
                                en,
                                de_e,
                                en_e,
                                E,
                                intent,
                                tuple(
                                    n
                                    for n in (del_order if mover == "del" else edit_order)
                                    if n != gone
                                ),
                                rve="remove",
                            ),
                        ),
                        f"del_{gone}@{D} mover={mover}",
                    )


FAMILIES = {
    "r2_moves": fam_r2_moves,
    "r3_moves": fam_r3_moves,
    "both_edit": fam_both_edit,
    "both_edit_both_move": fam_both_edit_both_move,
    "big_group": fam_big_group,
    "mixed_pool": fam_mixed_pool,
    "dup_mix": fam_dup_mix,
    "move_ins_del": fam_move_ins_del,
    "three_groups_fixed": fam_three_groups_fixed,
    "slide_swap": fam_slide_swap,
    "policy_rve": fam_policy_rve,
}
