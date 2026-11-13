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
            wins; a cycle is an order question. A new member only one half
            places goes next to its neighbours on that half; two that both
            halves added in a different order are an order question too.
3. result   ONE merged sequence, from which both halves are rendered, so they
            agree by construction.

The rule for inference: a guess is acted on only where every reading of the
files gives the same result, or where it is the only one-to-one reading
("edited in place"). Otherwise the model asks which cell is which
(``identify``), and only when the other half changed one of those cells.
Byte-identical recorded cells get the same treatment: where each half edited
one of them, or one half removed one and the other changed another, the model
asks (``conflict_copies``, ``remove_vs_edit``).

A pass with open questions can still make progress (``Outcome.halves``
and ``Outcome.record``). Which questions allow it is a finding, not a choice
(``scripts/sync_model_passes.py``):

- a question about an id'd cell (``translate_new``,
  ``remove_localized_side``) holds that cell only. Each half keeps its own
  cell, the record keeps its entry, and everything else is applied and
  recorded around it. An id'd cell's identity rests on no inference, and the
  laws across passes hold with it held;
- any other open question holds the whole part: nothing is written and
  nothing is recorded. Whether such a question is asked at all depends on the
  cells around it, so applying a part of the pass would change what the next
  pass asks.

A cell's file (the deck, or a voiceover companion) is one more aspect, merged
like its tags, so a cell that moves between the two stays one cell. Order is
defined within a file only, and the files are kept as blocks, deck first.

A cell that changes between shared and per-language on one half only is a
question too: ``fork`` (a shared cell one half turned into its own language;
answered, the other half gets its own variant) or ``unify`` (the reverse).
Both hold the part.

Tags are one more aspect of a cell, merged like its content: 3-way, per
member, with a question (``conflict_tags``) when both halves changed them
differently. They play no part in which cell is which. Whether a cell starts
a slide is a tag, so an un-id'd cell that is promoted to a ``subslide`` is a
cell whose tags changed, and nothing about the cells under it changes. A
recorded cell that gains a ``slide_id`` on one half is still matched by its
content, and the other half gains the id.

The model sees what a sync engine can see: per half a sequence of cells
(slide id or none, kind, content, tags) and the recorded sequence. Content is
only ever compared for equality, so a fingerprint would do.

Scored on the repo's sync probe by ``scripts/sync_model_probe.py``; its laws
are checked by ``scripts/sync_model_laws.py`` (one pass) and
``scripts/sync_model_passes.py`` (several passes with questions left open).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

ANCHOR_KINDS = ("slide", "subslide")
#: Tags that make a cell start a slide, whatever its kind.
ANCHOR_TAGS = frozenset(ANCHOR_KINDS)
SIDES = ("de", "en")
#: Questions that hold back only the cell they are about (see the module
#: docstring). Any other open question holds the whole part.
LOCAL_QUESTIONS = ("translate_new", "remove_localized_side")


def other(side: str) -> str:
    return "en" if side == "de" else "de"


@dataclass(frozen=True)
class Cell:
    sid: str | None  # slide_id, None for an un-id'd cell
    kind: str  # slide | subslide | vo | code | md
    body: str  # content (per-language text for a localized cell)
    localized: bool
    #: Carried through a pass and never read by the model. The checks use it
    #: to recognise a cell across passes.
    mark: object = field(default=None, compare=False)
    tags: frozenset[str] = frozenset()
    #: the file the cell sits in on its half: ``deck`` or ``companion``
    part: str = "deck"


@dataclass(frozen=True)
class BaseMember:
    sid: str | None
    kind: str
    localized: bool
    de: str | None  # recorded content per half (None = absent on that half)
    en: str | None
    mark: object = field(default=None, compare=False)  # as ``Cell.mark``
    tags: frozenset[str] = frozenset()  # recorded tags, the same on both halves
    part: str = "deck"  # as ``Cell.part``, the same on both halves

    def content(self, side: str) -> str | None:
        return self.de if side == "de" else self.en


def token(sid: str | None, kind: str, body: str | None) -> tuple:
    """What a cell is matched on: its id when it has one, else its content."""
    return ("id", sid) if sid is not None else ("c", kind, body)


# ---------------------------------------------------------------------------
# Layer 1: alignment
# ---------------------------------------------------------------------------


def _lcs_pairs(a: list, b: list) -> list[tuple[int, int]]:
    """A longest common subsequence of two token lists, as index pairs.

    Byte-identical cells make several of them equally long, and which one is
    taken decides how the leftovers read. Two preferences settle it, so that
    the same files always read the same way:

    - an unmatched ``a`` beside an unmatched ``b`` of its kind ("replaced in
      place") is better than a reading that needs a move;
    - of several identical recorded cells, the earliest are the ones kept.
    """
    n, m = len(a), len(b)
    big = n + m + 1  # one match outweighs every in-place pairing

    def in_place(x: tuple, y: tuple) -> bool:
        return x[0] == "c" == y[0] and x[1] == y[1]

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = max(dp[i + 1][j], dp[i][j + 1])
            if a[i] == b[j]:
                best = max(best, dp[i + 1][j + 1] + big)
            elif in_place(a[i], b[j]):
                best = max(best, dp[i + 1][j + 1] + 1)
            dp[i][j] = best
    out, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j] and dp[i][j] == dp[i + 1][j + 1] + big:
            out.append((i, j))
            i, j = i + 1, j + 1
        elif a[i] != b[j] and in_place(a[i], b[j]) and dp[i][j] == dp[i + 1][j + 1] + 1:
            i, j = i + 1, j + 1
        elif dp[i][j] == dp[i][j + 1]:
            j += 1
        else:
            i += 1
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
    """Base index -> cell index, on identity evidence only (id or content).

    A cell whose id the record does not know is matched by its content: it
    may be a recorded cell that gained its id on this half."""
    present = [i for i, m in enumerate(base) if m.content(side) is not None]
    known = {m.sid for m in base if m.sid is not None}
    b_tok = [token(base[i].sid, base[i].kind, base[i].content(side)) for i in present]
    c_tok = [token(c.sid if c.sid in known else None, c.kind, c.body) for c in cells]
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
    kinds = sorted({base[i].kind for i in free_base()} | {cells[j].kind for j in free_cells()})
    chain = _lis_chain(list(of.items()))
    bounds = [(-1, -1), *chain, (len(base), len(cells))]
    for (bi0, cj0), (bi1, cj1) in zip(bounds, bounds[1:], strict=False):
        for kind in kinds:
            fb = [i for i in free_base() if bi0 < i < bi1 and base[i].kind == kind]
            fc = [j for j in free_cells() if cj0 < j < cj1 and cells[j].kind == kind]
            for i, j in zip(fb, fc, strict=False):
                of[i], evidence[i] = j, "in_place" if len(fb) == len(fc) else "block"
    # the only one left of its kind: moved and edited
    for kind in kinds:
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
    #: The kind of open question about this member, if any. When the pass
    #: goes on around it, a held member stays as it is: on both halves and in
    #: the record.
    held: str | None = None
    tags: dict[str, frozenset[str] | None] = field(default_factory=dict)  # side -> tags now
    base_tags: frozenset[str] | None = None
    merged_tags: frozenset[str] = frozenset()  # decided tags
    #: An id a half gave to a recorded cell that has none. ``sid`` stays as
    #: recorded: until the pass is over, the cell is still an un-id'd one.
    stamp: str | None = None
    #: The half that forked a shared member into per-language cells, or
    #: unified a localized one into a shared cell, when only one half did.
    lead: str | None = None
    parts: dict[str, str | None] = field(default_factory=dict)  # side -> part now
    base_part: str | None = None
    merged_part: str = "deck"  # decided part

    @property
    def new_sid(self) -> str | None:
        return self.sid or self.stamp


@dataclass
class Question:
    #: order | conflict_shared | conflict_copies | conflict_tags |
    #: remove_vs_edit | remove_localized_side | translate_new | identify |
    #: fork | unify | conflict_part
    kind: str
    member: int | None
    inferred: bool = False
    #: ``identify`` only: the half, and the cells that may be the member
    side: str | None = None
    candidates: tuple[int, ...] = ()
    #: ``conflict_copies`` only: the byte-identical copy the English half
    #: edited (``member`` is the one the German half edited)
    twin: int | None = None
    #: the ``mark`` of the member the question is about (see ``Cell.mark``)
    mark: object = None
    #: what the question is about, in terms that last across passes: an id,
    #: or the kind and recorded content of an un-id'd cell
    about: tuple = ()


@dataclass
class Outcome:
    status: str  # merged | question
    sequence: list[Member] = field(default_factory=list)
    asked: list[Question] = field(default_factory=list)
    #: the first open question, and all of them
    open: Question | None = None
    opened: list[Question] = field(default_factory=list)
    #: What each half holds after the pass, and what is recorded. With open
    #: questions this is the progress the pass made, if any (see the module
    #: docstring).
    halves: dict[str, list[Cell]] = field(default_factory=dict)
    record: list[BaseMember] = field(default_factory=list)

    def labels(self) -> list[str]:
        return [
            (f"id:{m.new_sid}" if m.new_sid else f"body:{m.content}")
            + "".join(f"^{t}" for t in sorted(m.merged_tags))
            for m in self.sequence
        ]


def _align(base, cells):
    """Exact alignment of both halves, the cells new on both, the inferences."""
    of = {s: align_exact(base, cells[s], s) for s in SIDES}
    news = {s: [j for j in range(len(cells[s])) if j not in of[s].values()] for s in SIDES}
    # cells new on BOTH halves: the same id, or the same content where the
    # halves did not give them different ids (one half may have stamped it)
    twins: list[tuple[int, int]] = []
    taken: dict[str, set[int]] = {"de": set(), "en": set()}

    def same_new_cell(c: Cell, d: Cell, by_id: bool) -> bool:
        if by_id:
            return c.sid is not None and c.sid == d.sid
        return not c.localized and (c.kind, c.body) == (d.kind, d.body) and None in (c.sid, d.sid)

    for by_id in (True, False):
        for j in news["de"]:
            if j in taken["de"]:
                continue
            k = next(
                (
                    k
                    for k in news["en"]
                    if k not in taken["en"] and same_new_cell(cells["de"][j], cells["en"][k], by_id)
                ),
                None,
            )
            if k is not None:
                taken["de"].add(j)
                taken["en"].add(k)
                twins.append((j, k))
    twins.sort()
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
                m.stamp = m.stamp or cells[s][j].sid
                m.inferred = m.inferred or i in evidence[s]
        out.append(m)
    for j, k in twins:
        c = cells["de"][j]
        out.append(
            Member(
                len(out),
                c.sid or cells["en"][k].sid,
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
    for m in out:
        m.tags = {s: cells[s][m.pos[s]].tags if m.pos[s] is not None else None for s in SIDES}
        m.base_tags = base[m.base_idx].tags if m.base_idx is not None else None
        m.parts = {s: cells[s][m.pos[s]].part if m.pos[s] is not None else None for s in SIDES}
        m.base_part = base[m.base_idx].part if m.base_idx is not None else None
        m.merged_part = m.base_part or next(p for p in m.parts.values() if p is not None)
    return out, loose


def _groups(base: list[BaseMember]) -> dict[int, object]:
    """Base index -> the slide (or subslide) it sits under at base. Only used
    to decide how wide an order QUESTION is; identity never depends on it."""
    out: dict[int, object] = {}
    cur: object = "~head"
    for i, m in enumerate(base):
        if m.kind in ANCHOR_KINDS or m.tags & ANCHOR_TAGS:
            cur = i
            out[i] = "~anchors"
        else:
            out[i] = cur
    return out


def _linearize(members: list[Member], before: set[tuple[int, int]], follows: dict[int, int]):
    """A total order satisfying ``before`` (pairs of member keys), or ``None``
    when it is cyclic. A member listed in ``follows`` goes right behind the
    member it names when it can. Other ties: two recorded members that share
    no half keep their recorded order (nothing else relates them, and the
    record must not reorder them from one pass to the next); otherwise DE's
    order, then EN's."""
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
        for k in sticky or ready:
            m, n = by_key[k], by_key[nxt]
            if (
                m.base_idx is not None
                and n.base_idx is not None
                and m.base_idx < n.base_idx
                and not any(m.pos[s] is not None and n.pos[s] is not None for s in SIDES)
            ):
                nxt = k
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
    """One pass of a 3-way merge. ``answer(Question) -> str | None`` plays the
    judge; ``None`` leaves the question open."""
    cells = {"de": de_cells, "en": en_cells}
    of, twins, claimed, evidence = _align(base, cells)
    out = Outcome(status="merged")

    def ask(q: Question):
        got = answer(q)
        if got is None:
            out.opened.append(q)
        else:
            out.asked.append(q)
        return got

    def unchanged() -> Outcome:
        """The pass ends with a question that holds the whole part."""
        out.status, out.open = "question", out.opened[0]
        out.halves = {s: list(cells[s]) for s in SIDES}
        out.record = list(base)
        return out

    # -- identity: ask which cell is which, where it is unknown AND matters ----
    def touched_by(i: int, side: str) -> bool:
        """Did ``side`` change base member ``i`` (content, tags, id, presence,
        place)?"""
        if i not in of[side] or i in evidence[side]:
            return True
        now = cells[side][of[side][i]]
        if now.tags != base[i].tags or now.sid != base[i].sid:
            return True
        exact = sorted((k for k in of[side] if k not in evidence[side]), key=lambda k: of[side][k])
        return _lis_len([k for k in exact if k != i]) == _lis_len(exact)

    if identify:
        for s in SIDES:
            un_ided = {m.kind for m in base if m.sid is None} | {
                c.kind for c in cells[s] if c.sid is None
            }
            for kind in sorted(un_ided):
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
                    q = Question(
                        "identify",
                        i,
                        side=s,
                        candidates=tuple(open_cells),
                        mark=base[i].mark,
                        about=("cell", kind, base[i].content(s)),
                    )
                    got = ask(q)
                    if got is None:  # everything below rests on the answer
                        return unchanged()
                    if got != "removed":
                        of[s][i] = got
                        open_cells.remove(got)

    members, loose = _members(base, cells, of, twins, claimed, evidence)

    recorded = [m for m in members if m.base_idx is not None]

    def mark_of(m: Member) -> object:
        if m.base_idx is not None:
            return base[m.base_idx].mark
        return next(cells[s][m.pos[s]].mark for s in SIDES if m.pos[s] is not None)

    def about(m: Member) -> tuple:
        if m.sid is not None:
            return ("id", m.sid)
        if m.base_idx is not None:
            return ("cell", m.kind, m.base_body["de"] or m.base_body["en"])
        return ("new", m.kind, m.body["de"] or m.body["en"])

    def changed(m: Member, side: str) -> bool:
        """Does ``side`` hold recorded ``m`` with another content, tags or id?"""
        return (
            m.body[side] != m.base_body[side]
            or m.tags[side] != m.base_tags
            or cells[side][m.pos[side]].sid != base[m.base_idx].sid
        )

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
            and (x.pos[side] is None or changed(x, side) or maybe_moved(x, side))
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
            got = ask(
                Question(
                    "conflict_copies",
                    m_de.key,
                    inferred=True,
                    twin=m_en.key,
                    mark=mark_of(m_de),
                    about=about(m_de),
                )
            )
            if got is None:
                m_de.held = m_en.held = "conflict_copies"
            elif got == "de":  # one cell: German's edit stands, the other copy is as recorded
                m_en.body["en"] = m_en.base_body["en"]
            elif got == "en":
                m_de.body["de"] = m_de.base_body["de"]

    # -- membership and content, per member ---------------------------------
    def decide(m: Member) -> None:
        """Settle ``m``, or hold it when its question stays open."""

        def asks(kind: str, inferred: bool = False):
            got = ask(Question(kind, m.key, inferred, mark=mark_of(m), about=about(m)))
            if got is None:
                m.held = kind
            return got

        d, e = m.body["de"], m.body["en"]
        if m.base_idx is not None and None not in m.pos.values():
            now = {s: cells[s][m.pos[s]].localized for s in SIDES}
            if now["de"] == now["en"] != m.localized:
                m.localized = now["de"]  # both halves forked, or both unified
            elif now["de"] != now["en"]:
                # One half forked a shared cell into its own language, or
                # unified a localized one: the other half needs its own
                # variant written, or loses it. Either way someone has to say.
                lead = next(s for s in SIDES if now[s] != m.localized)
                if asks("unify" if m.localized else "fork") is None:
                    return
                m.localized, m.lead = not m.localized, lead
                if not m.localized:
                    m.content = m.body[lead]
                    return
        if m.base_idx is None:
            if d is not None and e is not None:
                m.content = None if m.localized else d
            elif m.localized:
                asks("translate_new")
            else:
                m.content = d if d is not None else e  # mechanical copy
            return
        if d is None and e is None:
            m.alive = False
            return
        if d is None or e is None:
            gone = "de" if d is None else "en"
            kept = other(gone)
            if m.localized:
                if asks("remove_localized_side") is not None:
                    m.alive = False
                return
            edited = changed(m, kept)
            # "removed" is only one reading when that half also holds a cell
            # nothing claims: it may be this member, edited. The readings
            # differ exactly when the other half changed the member too.
            unsure = (m.kind in loose[gone] and maybe_moved(m, kept)) or copy_changed(m, kept)
            if not edited and not unsure:
                m.alive = False  # removed on one half, untouched on the other
                return
            got = asks("remove_vs_edit", unsure and not edited)
            if got == "remove":
                m.alive = False
            elif got is not None:
                m.content = m.body[kept]
                m.pos[gone] = None
            return
        if m.localized:
            return
        if d == e:
            m.content = d
        elif d == m.base_body["de"]:
            m.content = e
        elif e == m.base_body["en"]:
            m.content = d
        else:
            got = asks("conflict_shared", m.inferred)
            if got is not None:
                m.content = d if got == "de" else e

    for m in members:
        if m.held is None:
            decide(m)

    # -- tags: one more 3-way value merge per member --------------------------
    for m in members:
        if m.held is not None or not m.alive:
            continue
        d_tags, e_tags = m.tags["de"], m.tags["en"]
        if d_tags is None or e_tags is None:
            m.merged_tags = d_tags if d_tags is not None else e_tags
        elif d_tags == e_tags or e_tags == m.base_tags:
            m.merged_tags = d_tags
        elif d_tags == m.base_tags:
            m.merged_tags = e_tags
        else:
            got = ask(Question("conflict_tags", m.key, mark=mark_of(m), about=about(m)))
            if got is None:
                m.held = "conflict_tags"
            else:
                m.merged_tags = d_tags if got == "de" else e_tags

    # -- the part (deck or companion file): the same 3-way merge ---------------
    for m in members:
        if m.held is not None or not m.alive:
            continue
        d_part, e_part = m.parts["de"], m.parts["en"]
        if d_part is None or e_part is None:
            m.merged_part = d_part if d_part is not None else e_part  # type: ignore[assignment]
        elif d_part == e_part or e_part == m.base_part:
            m.merged_part = d_part
        elif d_part == m.base_part:
            m.merged_part = e_part
        else:  # pragma: no cover - two parts exist, so one half kept the recorded one
            got = ask(Question("conflict_part", m.key, mark=mark_of(m), about=about(m)))
            if got is None:
                m.held = "conflict_part"
            else:
                m.merged_part = d_part if got == "de" else e_part

    def ppos(m: Member, side: str) -> int | None:
        """Where ``side`` holds ``m``, if it holds it in the part it ends in. A
        cell the other half moved to another file has no place in that file
        on this half yet: like a new cell, it is placed by the half that moved
        it."""
        j = m.pos[side]
        return j if j is not None and cells[side][j].part == m.merged_part else None

    alive = [m for m in members if m.alive]  # a held member counts as it stands
    known = [m for m in alive if m.base_idx is not None]
    both = [m for m in known if all(ppos(m, s) is not None for s in SIDES)]
    #: cells both halves added: each half has a word on their order, the record has none
    added = [m for m in alive if m.base_idx is None and all(ppos(m, s) is not None for s in SIDES)]

    # -- order: 3-way merge of pairwise precedence -----------------------------
    # Order is defined within a part (a file) only. Cells in different parts
    # are never compared; the parts are kept as blocks, deck first.
    def says(x: Member, y: Member, side: str) -> bool | None:
        """``side``'s word on "x before y": ``None`` when it lacks one of them."""
        px, py = ppos(x, side), ppos(y, side)
        if px is None or py is None:
            return None
        return px < py

    def record_says(x: Member, y: Member) -> bool | None:
        """The record's word on "x before y": ``None`` across parts."""
        assert x.base_idx is not None and y.base_idx is not None
        if base[x.base_idx].part != base[y.base_idx].part:
            return None
        return x.base_idx < y.base_idx

    def same_part(x: Member, y: Member) -> bool:
        return x.merged_part == y.merged_part

    groups = _groups(base)
    touched: dict[str, set] = {"de": set(), "en": set()}
    #: two cells the halves order differently where no record says which half
    #: changed it: both added them, or both moved them into a part they share
    clash = False
    for a_i, x in enumerate(known):
        for y in known[a_i + 1 :]:
            if not same_part(x, y):
                continue
            was = record_says(x, y)
            if was is None:
                de_says, en_says = says(x, y, "de"), says(x, y, "en")
                clash = clash or None not in (de_says, en_says) and de_says != en_says
                continue
            for s in SIDES:
                if says(x, y, s) not in (None, was):
                    touched[s] |= {groups[x.base_idx], groups[y.base_idx]}
    clash = clash or any(
        says(x, y, "de") != says(x, y, "en")
        for a_i, x in enumerate(added)
        for y in added[a_i + 1 :]
        if same_part(x, y)
    )

    def recorded_order(choose: str | None) -> set[tuple[int, int]]:
        """Which member goes first, per pair of recorded members and per pair
        of cells both halves added. Unasked, a pair of recorded members has
        the word of the half that holds both, the changed one if the halves
        differ, and the record's where no half holds both. After an order
        answer, the chosen half orders the members both halves hold, and the
        others are placed like new ones."""
        rel: set[tuple[int, int]] = set()
        pool = known if choose is None else both
        for a_i, x in enumerate(pool):
            for y in pool[a_i + 1 :]:
                if not same_part(x, y):
                    continue
                recorded = record_says(x, y)
                was = recorded if recorded is not None else x.base_idx < y.base_idx  # type: ignore[operator]
                de_says, en_says = says(x, y, "de"), says(x, y, "en")
                if choose is not None:
                    first = de_says if choose == "de" else en_says
                elif de_says is None or en_says is None:  # one half's word, or nobody's
                    first = de_says if de_says is not None else en_says
                elif de_says == en_says:
                    first = de_says
                else:  # exactly one half changed this pair: take the change
                    first = de_says if de_says != was else en_says
                if first is None:  # no half holds both: they stay as recorded
                    first = was
                rel.add((x.key, y.key) if first else (y.key, x.key))
        for a_i, x in enumerate(added):
            for y in added[a_i + 1 :]:  # where the halves differ, the order question decides
                if not same_part(x, y):
                    continue
                first = says(x, y, choose or "de")
                rel.add((x.key, y.key) if first else (y.key, x.key))
        # the parts as blocks, deck first
        for x in alive:
            for y in alive:
                if x.merged_part == "deck" != y.merged_part:
                    rel.add((x.key, y.key))
        return rel

    # -- a member only one half places: next to its neighbours there ----------
    def neighbours(
        with_next: bool, choose: str | None
    ) -> tuple[set[tuple[int, int]], dict[int, int]]:
        rel: set[tuple[int, int]] = set()
        follows: dict[int, int] = {}
        placed_by_pairs = known if choose is None else both
        for s in SIDES:
            seq = sorted((m for m in alive if ppos(m, s) is not None), key=lambda m: ppos(m, s))
            for at, m in enumerate(seq):
                if m in placed_by_pairs:
                    continue
                if choose is not None and s != choose and ppos(m, choose) is not None:
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

    contested = clash or bool(touched["de"] & touched["en"])
    sequence = None if contested else order(None)
    if sequence is None:
        got = ask(Question("order", None))
        if got is not None:
            sequence = order(got)
            if sequence is None:  # pragma: no cover - one half's order is acyclic
                raise AssertionError("no order after an order answer")
    if any(q.kind not in LOCAL_QUESTIONS for q in out.opened):
        return unchanged()
    assert sequence is not None

    # -- result: what each half holds, and what is recorded --------------------
    def cell_for(m: Member, s: str) -> Cell | None:
        """The cell half ``s`` holds for ``m`` after this pass."""
        j = m.pos[s]
        own = cells[s][j] if j is not None else None
        if m.held is not None:
            return own
        if own is not None:
            if m.localized:
                if m.lead is not None and s != m.lead:  # the answer to ``fork``
                    lead = cells[m.lead][m.pos[m.lead]]
                    body = f"{lead.body} >{s}"
                    mark = ("fork", lead.mark)
                    return Cell(lead.sid, lead.kind, body, True, mark, m.merged_tags, m.merged_part)
                return replace(own, tags=m.merged_tags, part=m.merged_part)
            return replace(
                own,
                sid=m.new_sid,
                body=m.content,
                localized=False,
                tags=m.merged_tags,
                part=m.merged_part,
            )
        o = other(s)
        theirs = cells[o][m.pos[o]]
        mark = ("copy", theirs.mark)
        if m.localized:  # the answer to ``translate_new``; the text is not modelled
            body = f"{theirs.body} >{s}"
            return Cell(theirs.sid, theirs.kind, body, True, mark, m.merged_tags, m.merged_part)
        return Cell(m.new_sid, m.kind, m.content, False, mark, m.merged_tags, m.merged_part)

    def entry_for(m: Member) -> BaseMember | None:
        """What the record holds for ``m`` after this pass."""
        if m.held is not None:
            return base[m.base_idx] if m.base_idx is not None else None
        de, en = (cell_for(m, s) for s in SIDES)
        assert de is not None and en is not None
        mark = mark_of(m) if m.base_idx is not None else ("new", mark_of(m))
        return BaseMember(
            m.new_sid, m.kind, m.localized, de.body, en.body, mark, m.merged_tags, m.merged_part
        )

    for s in SIDES:
        out.halves[s] = [c for m in sequence if (c := cell_for(m, s)) is not None]
    out.record = [r for m in sequence if (r := entry_for(m)) is not None]
    if out.opened:
        out.status, out.open = "question", out.opened[0]
    else:
        out.sequence = sequence
    return out
