"""A reference model of a structural core for bilingual deck sync.

Not production code: the prototype behind
``docs/claude/sync-structural-core-investigation.md``. It is the part of
``clm slides sync`` that decides which cell is which and where cells go, done
without positional identity (no ``pos:<slide>/<kind>/<n>`` keys, pools, spans,
order scopes or placement rows). Three layers:

1. align    each half is aligned to the RECORDED SEQUENCE on its own, never
            half-to-half by position: by id, then by exact content. What is
            left is paired by two inferences, "replaced in place" and "the
            only one left".
2. merge    membership and content are 3-way value merges per member. The
            order of recorded members is a 3-way merge of pairwise precedence
            ("x before y"): where only one half changed a pair, the change
            wins; a cycle is an order question. A member only one half places
            goes next to its neighbours on that half.
3. result   ONE merged sequence, from which both halves are rendered, so they
            agree by construction.

The rule for inference: a guess is acted on only where every reading of the
files gives the same result, or where it is the only one-to-one reading
("edited in place"). Otherwise the model asks which cell is which
(``identify``), and only when the other half changed one of those cells.
Byte-identical recorded cells get the same treatment: where each half edited
one of them, or one half removed one and the other changed another, the model
asks (``conflict_copies``, ``remove_vs_edit``).

The model sees what a sync engine can see: per half a sequence of cells
(slide id or none, kind, content) and the recorded sequence. Content is only
ever compared for equality, so a fingerprint would do.

Scored on the repo's sync probe by ``scripts/sync_model_probe.py``; its laws
are checked by ``scripts/sync_model_laws.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ANCHOR_KINDS = ("slide", "subslide")
SIDES = ("de", "en")


def other(side: str) -> str:
    return "en" if side == "de" else "de"


@dataclass(frozen=True)
class Cell:
    sid: str | None  # slide_id, None for an un-id'd cell
    kind: str  # slide | subslide | vo | code | md
    body: str  # content (per-language text for a localized cell)
    localized: bool


@dataclass(frozen=True)
class BaseMember:
    sid: str | None
    kind: str
    localized: bool
    de: str | None  # recorded content per half (None = absent on that half)
    en: str | None

    def content(self, side: str) -> str | None:
        return self.de if side == "de" else self.en


def token(sid: str | None, kind: str, body: str | None) -> tuple:
    """What a cell is matched on: its id when it has one, else its content."""
    return ("id", sid) if sid is not None else ("c", kind, body)


# ---------------------------------------------------------------------------
# Layer 1: alignment
# ---------------------------------------------------------------------------


def _lcs_pairs(a: list, b: list) -> list[tuple[int, int]]:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.append((i, j))
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def _lis_len(seq: list[int]) -> int:
    best: list[int] = []
    for k, v in enumerate(seq):
        best.append(1 + max((best[p] for p in range(k) if seq[p] < v), default=0))
    return max(best, default=0)


def _lis_chain(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """A longest chain of pairs increasing in both coordinates."""
    pairs = sorted(pairs)
    best = [1] * len(pairs)
    prev = [-1] * len(pairs)
    for k, (_, j) in enumerate(pairs):
        for p in range(k):
            if pairs[p][1] < j and best[p] + 1 > best[k]:
                best[k], prev[k] = best[p] + 1, p
    if not pairs:
        return []
    k = max(range(len(pairs)), key=lambda x: best[x])
    chain = []
    while k != -1:
        chain.append(pairs[k])
        k = prev[k]
    return chain[::-1]


def align_exact(base: list[BaseMember], cells: list[Cell], side: str) -> dict[int, int]:
    """Base index -> cell index, on identity evidence only (id or content)."""
    present = [i for i, m in enumerate(base) if m.content(side) is not None]
    b_tok = [token(base[i].sid, base[i].kind, base[i].content(side)) for i in present]
    c_tok = [token(c.sid, c.kind, c.body) for c in cells]
    of: dict[int, int] = {}
    used: set[int] = set()
    for bi, cj in _lcs_pairs(b_tok, c_tok):  # in order
        of[present[bi]] = cj
        used.add(cj)
    left: dict[tuple, list[int]] = {}
    for j, t in enumerate(c_tok):
        if j not in used:
            left.setdefault(t, []).append(j)
    for bi, i in enumerate(present):  # moved: same token, occurrence order
        if i not in of and left.get(b_tok[bi]):
            of[i] = left[b_tok[bi]].pop(0)
    return of


def infer(
    base: list[BaseMember],
    cells: list[Cell],
    side: str,
    of: dict[int, int],
    claimed: set[int],
) -> dict[int, str]:
    """Extend ``of`` with the two inferences; returns base index -> evidence.

    ``claimed`` are cells already known to be new on both halves.
    """
    evidence: dict[int, str] = {}

    def free_base() -> list[int]:
        return [
            i
            for i, m in enumerate(base)
            if m.content(side) is not None and i not in of and m.sid is None
        ]

    def free_cells() -> list[int]:
        taken = set(of.values()) | claimed
        return [j for j, c in enumerate(cells) if j not in taken and c.sid is None]

    # edited in place: between two neighbours that kept their relative order,
    # the unmatched base members and the unmatched cells of one kind pair up
    # in order (a diff's "replaced block"). A surplus is removed / new.
    chain = _lis_chain(list(of.items()))
    bounds = [(-1, -1), *chain, (len(base), len(cells))]
    for (bi0, cj0), (bi1, cj1) in zip(bounds, bounds[1:], strict=False):
        for kind in ("code", "md"):
            fb = [i for i in free_base() if bi0 < i < bi1 and base[i].kind == kind]
            fc = [j for j in free_cells() if cj0 < j < cj1 and cells[j].kind == kind]
            for i, j in zip(fb, fc, strict=False):
                of[i], evidence[i] = j, "in_place" if len(fb) == len(fc) else "block"
    # the only one left of its kind: moved and edited
    for kind in ("code", "md"):
        fb = [i for i in free_base() if base[i].kind == kind]
        fc = [j for j in free_cells() if cells[j].kind == kind]
        if len(fb) == 1 and len(fc) == 1:
            of[fb[0]], evidence[fb[0]] = fc[0], "eliminated"
    return evidence


# ---------------------------------------------------------------------------
# Layer 2: merge
# ---------------------------------------------------------------------------


@dataclass
class Member:
    key: int
    sid: str | None
    kind: str
    localized: bool
    base_idx: int | None
    pos: dict[str, int | None]  # side -> cell index on that half now
    body: dict[str, str | None]  # side -> content now
    base_body: dict[str, str | None]
    inferred: bool = False
    content: str | None = None  # decided content of a shared member
    alive: bool = True


@dataclass
class Question:
    #: order | conflict_shared | conflict_copies | remove_vs_edit |
    #: remove_localized_side | translate_new | identify
    kind: str
    member: int | None
    inferred: bool = False
    #: ``identify`` only: the half, and the cells that may be the member
    side: str | None = None
    candidates: tuple[int, ...] = ()
    #: ``conflict_copies`` only: the byte-identical copy the English half
    #: edited (``member`` is the one the German half edited)
    twin: int | None = None


@dataclass
class Outcome:
    status: str  # merged | question
    sequence: list[Member] = field(default_factory=list)
    asked: list[Question] = field(default_factory=list)
    open: Question | None = None

    def labels(self) -> list[str]:
        return [f"id:{m.sid}" if m.sid else f"body:{m.content}" for m in self.sequence]


def _align(base, cells):
    """Exact alignment of both halves, the cells new on both, the inferences."""
    of = {s: align_exact(base, cells[s], s) for s in SIDES}
    news = {s: [j for j in range(len(cells[s])) if j not in of[s].values()] for s in SIDES}
    # cells new on BOTH halves with the same id or the same content
    twins: list[tuple[int, int]] = []
    taken_en: set[int] = set()
    for j in news["de"]:
        c = cells["de"][j]
        k = next(
            (
                k
                for k in news["en"]
                if k not in taken_en
                and token(cells["en"][k].sid, cells["en"][k].kind, cells["en"][k].body)
                == token(c.sid, c.kind, c.body)
            ),
            None,
        )
        if k is not None:
            taken_en.add(k)
            twins.append((j, k))
    claimed = {"de": {j for j, _ in twins}, "en": {k for _, k in twins}}
    evidence = {s: infer(base, cells[s], s, of[s], claimed[s]) for s in SIDES}
    return of, twins, claimed, evidence


def _members(base, cells, of, twins, claimed, evidence) -> tuple[list[Member], dict[str, set[str]]]:
    out: list[Member] = []
    for i, bm in enumerate(base):
        m = Member(len(out), bm.sid, bm.kind, bm.localized, i, {}, {}, {"de": bm.de, "en": bm.en})
        for s in SIDES:
            j = of[s].get(i)
            m.pos[s] = j
            m.body[s] = cells[s][j].body if j is not None else None
            if j is not None:
                m.sid = m.sid or cells[s][j].sid
                m.inferred = m.inferred or i in evidence[s]
        out.append(m)
    for j, k in twins:
        c = cells["de"][j]
        out.append(
            Member(
                len(out),
                c.sid,
                c.kind,
                c.localized,
                None,
                {"de": j, "en": k},
                {"de": c.body, "en": cells["en"][k].body},
                {"de": None, "en": None},
            )
        )
    #: per side: kinds with a cell no member claims (it may be an edited one)
    loose: dict[str, set[str]] = {"de": set(), "en": set()}
    for s in SIDES:
        for j, c in enumerate(cells[s]):
            if j in of[s].values() or j in claimed[s]:
                continue
            pos = {"de": None, "en": None}
            body = {"de": None, "en": None}
            pos[s], body[s] = j, c.body
            out.append(
                Member(
                    len(out), c.sid, c.kind, c.localized, None, pos, body, {"de": None, "en": None}
                )
            )
            if c.sid is None:
                loose[s].add(c.kind)
    return out, loose


def _groups(base: list[BaseMember]) -> dict[int, object]:
    """Base index -> the slide (or subslide) it sits under at base. Only used
    to decide how wide an order QUESTION is; identity never depends on it."""
    out: dict[int, object] = {}
    cur: object = "~head"
    for i, m in enumerate(base):
        if m.kind in ANCHOR_KINDS:
            cur = i
            out[i] = "~anchors"
        else:
            out[i] = cur
    return out


def _linearize(members: list[Member], before: set[tuple[int, int]], follows: dict[int, int]):
    """A total order satisfying ``before`` (pairs of member keys), or ``None``
    when it is cyclic. A member listed in ``follows`` goes right behind the
    member it names when it can; other ties: DE's order, then EN's."""
    by_key = {m.key: m for m in members}
    preds: dict[int, set[int]] = {m.key: set() for m in members}
    for x, y in before:
        if x in preds and y in preds:
            preds[y].add(x)
    rest = set(preds)
    out: list[Member] = []

    def rank(k: int) -> tuple:
        m = by_key[k]
        return (0, m.pos["de"]) if m.pos["de"] is not None else (1, m.pos["en"])

    last: int | None = None
    while rest:
        ready = [k for k in rest if not (preds[k] & rest)]
        if not ready:
            return None
        sticky = [k for k in ready if follows.get(k) == last and last is not None]
        nxt = min(sticky or ready, key=rank)
        out.append(by_key[nxt])
        rest.remove(nxt)
        last = nxt
    return out


def merge(
    base: list[BaseMember],
    de_cells: list[Cell],
    en_cells: list[Cell],
    answer,
    *,
    identify: bool = True,
) -> Outcome:
    """3-way merge. ``answer(Question) -> str | None`` plays the judge."""
    cells = {"de": de_cells, "en": en_cells}
    of, twins, claimed, evidence = _align(base, cells)
    out = Outcome(status="merged")

    def ask(q: Question):
        got = answer(q)
        if got is None:
            out.status, out.open = "question", q
        else:
            out.asked.append(q)
        return got

    # -- identity: ask which cell is which, where it is unknown AND matters ----
    def touched_by(i: int, side: str) -> bool:
        """Did ``side`` change base member ``i`` (content, presence, place)?"""
        if i not in of[side] or i in evidence[side]:
            return True
        exact = sorted((k for k in of[side] if k not in evidence[side]), key=lambda k: of[side][k])
        return _lis_len([k for k in exact if k != i]) == _lis_len(exact)

    if identify:
        for s in SIDES:
            for kind in ("code", "md"):
                exact = {i for i in of[s] if i not in evidence[s]}
                open_base = [
                    i
                    for i, m in enumerate(base)
                    if m.sid is None
                    and m.kind == kind
                    and m.content(s) is not None
                    and i not in exact
                ]
                taken = {of[s][i] for i in exact} | claimed[s]
                open_cells = [
                    j
                    for j, c in enumerate(cells[s])
                    if c.sid is None and c.kind == kind and j not in taken
                ]
                if not open_base or not open_cells:
                    continue  # plain removals, or plain additions
                if len(open_base) < 2 and len(open_cells) < 2:
                    continue  # one for one: read as an edit
                if not any(touched_by(i, other(s)) for i in open_base):
                    continue  # the other half left them alone: every reading agrees
                for i in open_base:
                    of[s].pop(i, None)
                    evidence[s].pop(i, None)
                for i in open_base:
                    if not open_cells:
                        break
                    got = ask(Question("identify", i, side=s, candidates=tuple(open_cells)))
                    if got is None:
                        return out
                    if got != "removed":
                        of[s][i] = got
                        open_cells.remove(got)

    members, loose = _members(base, cells, of, twins, claimed, evidence)

    recorded = [m for m in members if m.base_idx is not None]

    def maybe_moved(m: Member, side: str) -> bool:
        """Could ``side`` have moved ``m``? True when some longest run of
        recorded members in base order on that half does without it."""
        seq = sorted((x for x in recorded if x.pos[side] is not None), key=lambda x: x.pos[side])
        full = _lis_len([x.base_idx for x in seq])
        return _lis_len([x.base_idx for x in seq if x is not m]) == full

    def copy_changed(m: Member, side: str) -> bool:
        """``m`` is one of several byte-identical recorded cells and ``side``
        changed another of them. Which copy the other half removed cannot be
        known then, and the result depends on it."""
        return any(
            x is not m
            and x.sid is None
            and x.kind == m.kind
            and x.base_body[side] == m.base_body[side]
            and (x.pos[side] is None or x.body[side] != x.base_body[side] or maybe_moved(x, side))
            for x in recorded
        )

    # -- byte-identical recorded cells where each half edited one -------------
    # Which copy a half edited cannot be read from the files. If the halves
    # edited different copies, both edits stand; if they edited the same one,
    # it is a conflict and the other copy survives. The result depends on the
    # reading, so the model asks: ``distinct``, or ``de`` / ``en`` for one
    # cell with that half's edit.
    copies: dict[tuple, list[Member]] = {}
    for m in recorded:
        if m.sid is None and not m.localized and None not in (*m.body.values(), m.base_body["en"]):
            copies.setdefault((m.kind, m.base_body["de"]), []).append(m)
    for group in copies.values():
        only = {
            s: [
                m
                for m in group
                if m.body[s] != m.base_body[s] and m.body[other(s)] == m.base_body[other(s)]
            ]
            for s in SIDES
        }
        for m_de, m_en in zip(only["de"], only["en"], strict=False):
            got = ask(Question("conflict_copies", m_de.key, inferred=True, twin=m_en.key))
            if got is None:
                return out
            if got == "de":  # one cell: German's edit stands, the other copy is as recorded
                m_en.body["en"] = m_en.base_body["en"]
            elif got == "en":
                m_de.body["de"] = m_de.base_body["de"]

    # -- membership and content, per member ---------------------------------
    for m in members:
        d, e = m.body["de"], m.body["en"]
        if m.base_idx is None:
            if d is not None and e is not None:
                m.content = None if m.localized else d
            elif m.localized:
                if ask(Question("translate_new", m.key)) is None:
                    return out
            else:
                m.content = d if d is not None else e  # mechanical copy
            continue
        if d is None and e is None:
            m.alive = False
            continue
        if d is None or e is None:
            gone = "de" if d is None else "en"
            kept = other(gone)
            if m.localized:
                if ask(Question("remove_localized_side", m.key)) is None:
                    return out
                m.alive = False
                continue
            edited = m.body[kept] != m.base_body[kept]
            # "removed" is only one reading when that half also holds a cell
            # nothing claims: it may be this member, edited. The readings
            # differ exactly when the other half changed the member too.
            unsure = (m.kind in loose[gone] and maybe_moved(m, kept)) or copy_changed(m, kept)
            if not edited and not unsure:
                m.alive = False  # removed on one half, untouched on the other
                continue
            got = ask(Question("remove_vs_edit", m.key, inferred=unsure and not edited))
            if got is None:
                return out
            if got == "remove":
                m.alive = False
            else:
                m.content = m.body[kept]
                m.pos[gone] = None
            continue
        if m.localized:
            continue
        if d == e:
            m.content = d
        elif d == m.base_body["de"]:
            m.content = e
        elif e == m.base_body["en"]:
            m.content = d
        else:
            got = ask(Question("conflict_shared", m.key, m.inferred))
            if got is None:
                return out
            m.content = d if got == "de" else e

    alive = [m for m in members if m.alive]
    both = [m for m in alive if m.base_idx is not None and None not in m.pos.values()]
    floating = [m for m in alive if m not in both]

    # -- order of recorded members: 3-way merge of pairwise precedence --------
    groups = _groups(base)
    touched: dict[str, set] = {"de": set(), "en": set()}
    for a_i, x in enumerate(both):
        for y in both[a_i + 1 :]:
            bb = x.base_idx < y.base_idx
            for s in SIDES:
                if (x.pos[s] < y.pos[s]) != bb:
                    touched[s] |= {groups[x.base_idx], groups[y.base_idx]}

    def recorded_order(choose: str | None) -> set[tuple[int, int]]:
        rel: set[tuple[int, int]] = set()
        for a_i, x in enumerate(both):
            for y in both[a_i + 1 :]:
                db, eb = x.pos["de"] < y.pos["de"], x.pos["en"] < y.pos["en"]
                if choose is not None:
                    first = db if choose == "de" else eb
                elif db == eb:
                    first = db
                else:  # exactly one half changed this pair: take the change
                    first = db if db != (x.base_idx < y.base_idx) else eb
                rel.add((x.key, y.key) if first else (y.key, x.key))
        return rel

    # -- a member only one half places: next to its neighbours there ----------
    def neighbours(
        with_next: bool, choose: str | None
    ) -> tuple[set[tuple[int, int]], dict[int, int]]:
        rel: set[tuple[int, int]] = set()
        follows: dict[int, int] = {}
        for s in SIDES:
            seq = sorted((m for m in alive if m.pos[s] is not None), key=lambda m: m.pos[s])
            for at, m in enumerate(seq):
                if m not in floating:
                    continue
                if choose is not None and s != choose and m.pos[choose] is not None:
                    continue  # the chosen half places it
                if at > 0:
                    rel.add((seq[at - 1].key, m.key))
                    follows.setdefault(m.key, seq[at - 1].key)
                if with_next and at + 1 < len(seq):
                    rel.add((m.key, seq[at + 1].key))
        return rel, follows

    def order(choose: str | None) -> list[Member] | None:
        rel = recorded_order(choose)
        for with_next in (True, False):
            extra, follows = neighbours(with_next, choose)
            seq = _linearize(alive, rel | extra, follows)
            if seq is not None:
                return seq
        return None

    contested = bool(touched["de"] & touched["en"])
    sequence = None if contested else order(None)
    if sequence is None:
        got = ask(Question("order", None))
        if got is None:
            return out
        sequence = order(got)
        if sequence is None:  # pragma: no cover - one half's order is acyclic
            raise AssertionError("no order after an order answer")
    out.sequence = sequence
    return out
