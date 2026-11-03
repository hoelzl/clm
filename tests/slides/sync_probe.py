"""Brute-force probe of the bilingual sync engine over many deck shapes (#1130).

A case is one or more *rounds*. Each round starts from the current (converged)
pair, rewrites the DE and EN halves to a target order + edits, then drives
``report -> apply`` through the real pre-write verify gate until clean.

Cell names (the first char decides the kind):
  S*  slide           (id'd, localized)      slide_id="s-<name>"
  U*  subslide        (id'd, localized)      slide_id="u-<name>"
  a,b,e,f  voiceover  (id'd, localized)      slide_id="vo-<name>"
  c*/n*  shared code  (un-id'd)              body "<name> = 1"
  d*  duplicate code  (un-id'd)              body "dup = 0" (byte-identical)
  m*/k*  shared markdown (un-id'd, no lang)  body "# M <name>"

The oracle compares *label sequences*: id'd cells are labelled by slide_id,
un-id'd cells by body. Duplicates share a label, so a multiset/sequence
comparison handles them. It reports ``converged``, a visible failure
(``livelock_*``, ``unanswered_question``, ``manual_stall``, ``apply_*``) or
a silent one (:data:`SILENT`: the diff is clean but the halves are wrong).

The harness answers as a careful agent would: order questions with the half
whose order the case intends, ``conflict_shared`` with the edited half,
removal questions with ``remove`` when the round really removes or regroups
a cell, and ``verify_*`` with ``confirm``.

The families (:data:`FAMILIES`) widen the ratcheted sweep
(``sync_sweep.py``): DE as the reordering half, both halves reordering,
markdown and mixed pools, three id'd cells and subslides, inserts and
deletes, two slide groups, byte-identical cells, and a second round on the
ledger the first one wrote. About 19,000 cases: too slow for CI, so
``scripts/sync_probe.py gate`` runs it by hand against a base ref.
"""

from __future__ import annotations

import itertools
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import clm
from clm.slides import doc_apply, doc_ledger
from clm.slides.doc_lenses import load_bundle
from clm.slides.sync_diff import diff_outcome
from clm.slides.sync_verify import apply_verify_gate

MAX_PASSES = 6

HEADER = {
    "de": "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n",
    "en": "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n",
}


def kind(name: str) -> str:
    c = name[0]
    if c == "S":
        return "slide"
    if c == "U":
        return "subslide"
    if c in "abef":
        return "vo"
    if c in "cnd":
        return "code"
    if c in "mk":
        return "md"
    raise ValueError(name)


def base_body(name: str) -> str:
    k = kind(name)
    if k == "code":
        return "dup = 0" if name[0] == "d" else f"{name} = 1"
    if k == "md":
        return f"# M {name}"
    raise ValueError(name)


def edited_body(name: str) -> str:
    k = kind(name)
    if k == "code":
        return base_body(name) + "0"
    return base_body(name) + " edited"


def cell_text(name: str, lang: str, edits: frozenset[str]) -> str:
    k = kind(name)
    if k == "slide":
        return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="s-{name}"\n#\n# # {name} {lang}\n\n'
    if k == "subslide":
        return f'# %% [markdown] lang="{lang}" tags=["subslide"] slide_id="u-{name}"\n#\n# ## {name} {lang}\n\n'
    if k == "vo":
        return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-{name}"\n#\n# - {name.upper()} {lang}\n\n'
    body = edited_body(name) if name in edits else base_body(name)
    if k == "code":
        return f"# %%\n{body}\n\n"
    return f"# %% [markdown]\n#\n{body}\n\n"


def deck_text(lang: str, order: tuple[str, ...], edits: frozenset[str]) -> str:
    return (HEADER[lang] + "".join(cell_text(n, lang, edits) for n in order)).rstrip("\n") + "\n"


def label(name: str, edits: frozenset[str]) -> str:
    k = kind(name)
    if k == "slide":
        return f"id:s-{name}"
    if k == "subslide":
        return f"id:u-{name}"
    if k == "vo":
        return f"id:vo-{name}"
    return "body:" + (edited_body(name) if name in edits else base_body(name))


def parse_labels(text: str, lang: str) -> tuple[list[str], list[str]]:
    """Label sequence of the file's cells + a list of anomalies."""
    import re

    anomalies: list[str] = []
    chunks = re.split(r"(?m)^(?=# %%)", text)
    out: list[str] = []
    for ch in chunks:
        if not ch.startswith("# %%"):
            continue
        head, _, body = ch.partition("\n")
        body = body.strip("\n")
        m = re.search(r'slide_id="([^"]+)"', head)
        if m:
            sid = m.group(1)
            out.append(f"id:{sid}")
            lm = re.search(r'lang="([^"]+)"', head)
            if lm is None or lm.group(1) != lang or f" {lang}" not in body:
                anomalies.append(f"lang:{sid}")
        elif "[markdown]" in head:
            lines = [ln for ln in body.splitlines() if ln.strip() not in ("#", "")]
            out.append("body:" + "\n".join(lines))
            if 'lang="' in head:
                anomalies.append(f"md-gained-lang:{lines}")
        else:
            out.append("body:" + body)
    return out, anomalies


@dataclass(frozen=True)
class Round:
    de_order: tuple[str, ...]
    en_order: tuple[str, ...]
    edits: tuple[str, ...]  # un-id'd cells edited on ``edit_side``
    edit_side: str  # "de" / "en"
    intent: str  # the half whose order is intended; answers order questions
    expected: tuple[str, ...]  # intended final order (names)
    #: names left out of the order comparison (e.g. a cell inserted on the
    #: non-intent half, whose intended position is ambiguous)
    unordered: tuple[str, ...] = ()
    #: further acceptable final orders (duplicate-body ambiguity)
    accept: tuple[tuple[str, ...], ...] = ()
    #: the other half's order when both halves reorder: landing on it is a
    #: ``silent_pick`` (no order question asked) or ``misplaced_to_other``
    alt: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Case:
    family: str
    base: tuple[str, ...]
    rounds: tuple[Round, ...]
    tag: str = ""

    @property
    def id(self) -> str:
        parts = [self.family, self.tag, " ".join(self.base)]
        for r in self.rounds:
            parts.append(
                f"DE[{' '.join(r.de_order)}] EN[{' '.join(r.en_order)}] "
                f"edit@{r.edit_side}:{','.join(r.edits) or '-'} intent={r.intent}"
            )
        return " | ".join(p for p in parts if p)


@dataclass
class Result:
    status: str
    round: int = 0
    passes: list[list[tuple[str, str]]] = field(default_factory=list)
    detail: str = ""
    questions: dict[str, int] = field(default_factory=dict)


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


def _apply(de_path: Path, en_path: Path, decisions):
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


_ORDER_Q = {"order_decision", "pool_placement_divergence"}
_REMOVE_Q = {"remove_localized_side", "remove_vs_split", "broken_owner", "remove_vs_edit"}


def _check(
    de_path: Path, en_path: Path, rnd: Round, cum_edits: frozenset[str], asked: bool
) -> tuple[str, str]:
    exp = [label(n, cum_edits) for n in rnd.expected]
    got = {}
    for lang, path in (("de", de_path), ("en", en_path)):
        labels, anomalies = parse_labels(path.read_text(encoding="utf-8"), lang)
        if anomalies:
            return "lang_or_kind_anomaly", f"{lang}: {anomalies}"
        got[lang] = labels
    for lang in ("de", "en"):
        if sorted(got[lang]) != sorted(exp):
            return "lost_or_duplicated", f"{lang}: got={got[lang]} exp={exp}"
    if got["de"] != got["en"]:
        return "halves_disagree", f"de={got['de']} en={got['en']}"
    skip = {label(n, cum_edits) for n in rnd.unordered}
    seq = [x for x in got["de"] if x not in skip]
    oks = [[label(n, cum_edits) for n in o] for o in (rnd.expected, *rnd.accept)]
    if any(seq == [x for x in o if x not in skip] for o in oks):
        return "converged", ""
    if rnd.alt is not None and seq == [label(n, cum_edits) for n in rnd.alt]:
        return ("misplaced_to_other" if asked else "silent_pick"), f"got={seq}"
    return "misplaced", f"got={seq} exp={exp}"


def _group_of(order) -> dict[str, str]:
    cur, out = "", {}
    for n in order:
        if kind(n) == "slide":
            cur = n
        else:
            out[n] = cur
    return {n: g for n, g in out.items() if n in order and kind(n) != "slide"}


def run_case(case: Case) -> Result:
    res = Result(status="?")
    with tempfile.TemporaryDirectory() as tmp:
        de_path = Path(tmp) / "slides_t.de.py"
        en_path = Path(tmp) / "slides_t.en.py"
        none: frozenset[str] = frozenset()
        de_path.write_text(deck_text("de", case.base, none), encoding="utf-8")
        en_path.write_text(deck_text("en", case.base, none), encoding="utf-8")
        _record(de_path, en_path)
        cum: frozenset[str] = frozenset()
        for ri, rnd in enumerate(case.rounds):
            res.round = ri
            res.passes = []
            new = cum | frozenset(rnd.edits)
            de_edits = new if rnd.edit_side == "de" else cum
            en_edits = new if rnd.edit_side == "en" else cum
            de_path.write_text(deck_text("de", rnd.de_order, de_edits), encoding="utf-8")
            en_path.write_text(deck_text("en", rnd.en_order, en_edits), encoding="utf-8")
            cum = new
            prev = case.base if ri == 0 else case.rounds[ri - 1].expected
            removed = set(prev) - set(rnd.expected)
            regrouped = _group_of(prev) != _group_of(rnd.expected)
            status = None
            withheld = False
            asked = False
            for _ in range(MAX_PASSES):
                _, diff = _diff(de_path, en_path)
                if diff.is_clean:
                    status, detail = _check(de_path, en_path, rnd, cum, asked)
                    res.detail = detail
                    break
                res.passes.append([(i.key, i.action) for i in diff.items])
                decisions = {}
                for item in diff.items:
                    answers = doc_apply.item_answers(item)
                    choice = None
                    if item.action in _ORDER_Q:
                        choice = rnd.intent
                        asked = True
                    elif item.action == "conflict_shared":
                        choice = rnd.edit_side
                    elif (
                        item.action in _REMOVE_Q and (removed or regrouped) and "remove" in answers
                    ):
                        choice = "remove"
                    elif (
                        item.action in ("verify_cold", "verify_translation")
                        and "confirm" in answers
                    ):
                        choice = "confirm"
                    if choice is not None:
                        res.questions[item.action] = res.questions.get(item.action, 0) + 1
                        decisions[item.key] = doc_apply.Decision(key=item.key, choice=choice)
                    elif doc_apply.decision_vocabulary(item.action) and not answers:
                        status = "manual_stall"
                        res.detail = f"{item.action} {item.key}"
                        break
                    elif answers:
                        status = "unanswered_question"
                        res.detail = f"{item.action} {item.key}"
                        break
                if status:
                    break
                try:
                    outcome = _apply(de_path, en_path, decisions)
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
        res.status = "converged"
        return res


def engine_file() -> str:
    return clm.__file__


#: Outcomes where the engine reports success but the halves are wrong.
SILENT = frozenset(
    {
        "misplaced",
        "lost_or_duplicated",
        "silent_pick",
        "misplaced_to_other",
        "halves_disagree",
        "lang_or_kind_anomaly",
    }
)


def rank(status: str) -> int:
    """0 converged, 1 a visible failure, 2 a silent one; a case gets worse
    when its rank rises. A later round's ``rN:`` prefix is ignored."""
    kind = status.split(":")[-1]
    if kind == "converged":
        return 0
    return 2 if kind in SILENT else 1


# ---------------------------------------------------------------------------
# Case families
# ---------------------------------------------------------------------------

OTHER = {"en": "de", "de": "en"}
G4 = ("a", "c1", "b", "c2")


def wrap(group, head=("S0",), tail=("S1",)):
    return (*head, *group, *tail)


def one_half(family, base_group, R, perm, edits, side, tag="", **kw):
    """R reorders ``base_group`` into ``perm``; edits land on ``side``."""
    base = wrap(base_group)
    new = wrap(perm)
    de, en = (new, base) if R == "de" else (base, new)
    return Case(family, base, (Round(de, en, tuple(edits), side, R, new, **kw),), tag)


def subsets(names, max_r=None):
    names = sorted(names)
    for r in range(len(names) + 1 if max_r is None else max_r + 1):
        yield from itertools.combinations(names, r)


def fam_ctrl_four():
    # Mirror of the existing "four" domain (EN reorders) -- oracle control.
    for side in ("en", "de"):
        for perm in itertools.permutations(G4):
            for ed in subsets(("c1", "c2")):
                yield one_half("ctrl_four", G4, "en", perm, ed, side)


def fam_de_reorder():
    for grp in (G4, ("c0", "a", "c1", "b", "c2")):
        code = [n for n in grp if n.startswith("c")]
        for side in ("en", "de"):
            for perm in itertools.permutations(grp):
                for ed in subsets(code):
                    yield one_half(f"de_reorder_{len(grp)}", grp, "de", perm, ed, side)


def fam_both_reorder():
    perms = [p for p in itertools.permutations(G4) if p != G4]
    for p, q in itertools.permutations(perms, 2):
        for intent in ("de", "en"):
            for ed, side in (((), "en"), (("c1",), "de"), (("c1",), "en")):
                de, en = wrap(p), wrap(q)
                exp = de if intent == "de" else en
                alt = en if intent == "de" else de
                yield Case(
                    "both_reorder",
                    wrap(G4),
                    (Round(de, en, ed, side, intent, exp, alt=alt),),
                )


def fam_markdown_pool():
    for grp in (("a", "m1", "b", "c1"), ("a", "m1", "b", "m2")):
        pool = [n for n in grp if n[0] in "cm"]
        for R in ("en", "de"):
            for side in ("en", "de"):
                for perm in itertools.permutations(grp):
                    for ed in subsets(pool):
                        yield one_half("md_pool4", grp, R, perm, ed, side, tag="+".join(pool))
    grp = ("m1", "a", "c1", "b", "c2")
    for R in ("en", "de"):
        for side in ("en", "de"):
            for perm in itertools.permutations(grp):
                for ed in ((), ("m1",), ("c1",), ("c2", "m1")):
                    yield one_half("md_pool5", grp, R, perm, ed, side)


def fam_three_ids():
    for grp, fam in (
        (("a", "c1", "b", "c2", "e"), "three_vo"),
        (("a", "c1", "U1", "c2", "b"), "subslide_in_group"),
    ):
        for R in ("en", "de"):
            for perm in itertools.permutations(grp):
                for ed, side in (
                    ((), R),
                    (("c1",), R),
                    (("c1",), OTHER[R]),
                    (("c1", "c2"), OTHER[R]),
                ):
                    yield one_half(fam, grp, R, perm, ed, side)


def fam_insert_delete():
    base = wrap(G4)
    for R in ("en", "de"):
        other = OTHER[R]
        for perm in itertools.permutations(G4):
            ops = []
            for new in ("n1", "k1"):
                for pos in range(5):
                    p2 = (*perm[:pos], new, *perm[pos:])
                    ops.append((f"ins_{new}@R pos{pos}", wrap(p2), base, wrap(p2), ()))
                    # insertion on the other (non-reordering) half
                    b2 = (*G4[:pos], new, *G4[pos:])
                    if pos in (0, 4):
                        ops.append(
                            (
                                f"ins_{new}@other pos{pos}",
                                wrap(perm),
                                wrap(b2),
                                wrap((*perm, new)),
                                (new,),
                            )
                        )
                    # the author adds the shared cell to BOTH halves, after the
                    # same predecessor (R: in its new order; other: in base order)
                    pred = perm[pos - 1] if pos else None
                    bpos = 0 if pred is None else G4.index(pred) + 1
                    b3 = (*G4[:bpos], new, *G4[bpos:])
                    ops.append((f"ins_{new}@both pos{pos}", wrap(p2), wrap(b3), wrap(p2), ()))
            for gone in ("c1", "a"):
                pr = tuple(n for n in perm if n != gone)
                br = tuple(n for n in G4 if n != gone)
                ops.append((f"del_{gone}@R", wrap(pr), base, wrap(pr), ()))
                ops.append((f"del_{gone}@other", wrap(perm), wrap(br), wrap(pr), ()))
            for tag, r_order, o_order, exp, unordered in ops:
                for ed in ((), ("c2",)):
                    de, en = (r_order, o_order) if R == "de" else (o_order, r_order)
                    yield Case(
                        "insert_delete",
                        base,
                        (Round(de, en, ed, other, R, exp, unordered=unordered),),
                        tag,
                    )


def fam_two_groups(n_sample=500):
    head, mid, tail = ("S0",), ("a", "c1", "b", "S1", "e", "c2", "c3"), ("S2",)
    base = (*head, *mid, *tail)
    rng = random.Random(1130)
    perms = list(itertools.permutations(mid))
    perms.remove(mid)
    sample = rng.sample(perms, n_sample)
    for R in ("en", "de"):
        for perm in sample:
            new = (*head, *perm, *tail)
            for ed, side in (((), R), (("c1",), R), (("c1",), OTHER[R])):
                de, en = (new, base) if R == "de" else (base, new)
                yield Case("two_groups", base, (Round(de, en, ed, side, R, new),))


def fam_duplicates():
    for grp in (("a", "d1", "b", "d2"), ("a", "c1", "b", "d1", "d2")):
        seen = set()
        for perm in itertools.permutations(grp):
            key = tuple("d" if n[0] == "d" else n for n in perm)
            if key in seen:
                continue
            seen.add(key)
            swapped = tuple({"d1": "d2", "d2": "d1"}.get(n, n) for n in perm)
            for R in ("en", "de"):
                for ed, side in (((), R), (("d1",), R), (("d1",), OTHER[R])):
                    # editing d1 on the other half: which moved dup carries the
                    # edit is ambiguous, so either assignment is accepted
                    acc = (wrap(swapped),) if side != R else ()
                    yield one_half(f"dup_{len(grp)}", grp, R, perm, ed, side, accept=acc)


def fam_round2(k=4):
    rng = random.Random(42)
    for side1 in ("en", "de"):
        for perm1 in itertools.permutations(G4):
            for ed1 in subsets(("c1", "c2")):
                r1_new = wrap(perm1)
                r1 = Round(wrap(G4), r1_new, ed1, side1, "en", r1_new)
                left = [n for n in ("c2", "c1") if n not in ed1]
                perms2 = rng.sample(list(itertools.permutations(G4)), k)
                # identity second round: nothing changes after convergence
                yield Case(
                    "round2_identity", wrap(G4), (r1, Round(r1_new, r1_new, (), "en", "en", r1_new))
                )
                for R2 in ("en", "de"):
                    for perm2 in perms2:
                        new2 = wrap(perm2)
                        de, en = (new2, r1_new) if R2 == "de" else (r1_new, new2)
                        for ed2, s2 in (((), R2), ((left[0],), OTHER[R2]) if left else ((), R2)):
                            yield Case("round2", wrap(G4), (r1, Round(de, en, ed2, s2, R2, new2)))


FAMILIES = {
    "ctrl_four": fam_ctrl_four,
    "de_reorder": fam_de_reorder,
    "both_reorder": fam_both_reorder,
    "markdown_pool": fam_markdown_pool,
    "three_ids": fam_three_ids,
    "insert_delete": fam_insert_delete,
    "two_groups": fam_two_groups,
    "duplicates": fam_duplicates,
    "round2": fam_round2,
}
