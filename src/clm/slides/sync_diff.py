"""The generic 3-way differ for sync v3 (#520 Phase 2).

One diff over the unified member stream of a :class:`BilingualDeck`
(design §6): per member, ``base`` is a recorded :class:`MemberBaseline`
(from the committed ledger in Phase 3, or a :func:`baseline_from_deck`
snapshot of a parsed bundle — the Phase 2 shadow / ``--since`` forensic
view) and ``current`` is the member as parsed from the working tree, both
languages jointly. Direction is decided per member by which side's
fingerprint moved off base — no deck-level direction inference.

The outcome vocabulary is design §6.2, closed: ``in_sync`` members are
counted, everything else becomes a :class:`DiffItem` whose ``action`` names
one row of the §6.2 table or the §7.2/§7.3 transition tables. The action
registry (:data:`MECHANICAL_ACTIONS` / :data:`FRAMED_ACTIONS`) is the §7.4
enumeration surface: the transition-matrix test walks the closed product of
class states and asserts every combination lands on exactly one registered
row. Anything that fits no row must become a framed decision carrying the
member's full state (P8) — never a refusal of the deck, never a silent
default.

Comparable aspects are fields of one record (§6.3): the content
fingerprint covers every byte of a cell modulo the ``slide_id`` attribute
(the same parity rule the lens uses), and the classifier drills down into
named fields (body, tags, lang attr, layout, owner, id state) only to pick
the row — so a new serialized field is compared by construction, and the
field-coverage test asserts every :class:`Member`/:class:`SideCell` field
is either compared or explicitly annotated cosmetic.

Identity discipline (P1/P2): id-keyed members match by key; the only key
migration is ``pos:… → id:…`` when a previously positional member gained an
id (§7.3, an explicit, logged rename). Id-less members — after the §3.4
precondition these are shared cells and per-language headers — align to
base per (group, kind) pool by per-side sequence matching over content
fingerprints, which is what localizes an insert or reorder to its own slot
instead of cascading mis-pairs down the group (the W10 noise shape).

This module is pure (no I/O, no git) and must not import from the v2 sync
core (``sync_plan`` / ``sync_apply`` / ``sync_code``) — enforced by the
import-cleanliness test (design §12.5).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Literal

from attrs import define, field, frozen

from clm.slides.bilingual_doc import (
    HEADER_GROUP,
    ORPHAN_GROUP,
    BilingualDeck,
    Lang,
    Member,
    MemberKey,
    NormalizeRefusal,
    Observation,
    ParseOutcome,
    SideCell,
)
from clm.slides.doc_lenses import _lines_sans_id

_SIDES: tuple[Lang, Lang] = ("de", "en")

__all__ = [
    "COSMETIC_SIDECELL_FIELDS",
    "COMPARED_MEMBER_FIELDS",
    "COMPARED_SIDECELL_FIELDS",
    "COSMETIC_MEMBER_FIELDS",
    "FRAMED_ACTIONS",
    "MECHANICAL_ACTIONS",
    "DeckBaseline",
    "DeckDiff",
    "DiffItem",
    "MemberBaseline",
    "baseline_from_deck",
    "content_fingerprint",
    "diff_deck",
    "diff_outcome",
]

Direction = Literal["de_to_en", "en_to_de", "both", "none"]
Outcome = Literal[
    "in_sync",
    "mechanical",
    "edit",
    "add",
    "remove",
    "conflict",
    "transition",
    "unverified",
    "order",
]

# ---------------------------------------------------------------------------
# The closed action registry (§6.2 rows + §7.2/§7.3 transition rows).
# ---------------------------------------------------------------------------

#: Rows apply can execute deterministically (design §6.2 "mechanical").
MECHANICAL_ACTIONS = frozenset(
    {
        "record_symmetric_edit",  # shared: both sides equal, ≠ base
        "propagate_shared_edit",  # shared: one side moved — verbatim copy
        "copy_new_shared",  # shared add on one side — verbatim copy
        "record_symmetric_add",  # both sides added identical content
        "mirror_remove",  # one side removed, twin untouched
        "record_remove",  # both sides removed
        "mirror_tags",  # tag-set change on one side, body unchanged
        "record_tags",  # identical tag-set change on both sides
        "record_fork",  # §7.3 complete fork (lang attrs + both bodies)
        "record_unify",  # §7.2 unify complete, bodies byte-equal
        "stamp_twin_id",  # §7.3 id-stamp: id'd on one half (#443)
        "record_key_migration",  # §7.3 pos→id key rename, both halves id'd
        "record_relayout",  # §7.3 inline↔companion, both halves moved
        "mirror_layout",  # §7.3 relayout on one half — complete on twin
        "mirror_owner",  # owner reference moved on one side
        "record_owner",  # owner reference moved identically on both
        "mirror_order",  # §6.2 order: one side reordered
        "record_order",  # both sides reordered identically
        "record_group_rename",  # anchor id renamed, anchor content matched
        "propagate_preamble",  # file preamble moved on one side
        "record_preamble",  # file preambles moved identically
    }
)

#: Rows that need judgment: framed tasks/decisions (design §6.2/§7, P7).
FRAMED_ACTIONS = frozenset(
    {
        "translate_edit",  # localized: one side moved — translate/adapt
        "translate_new",  # localized add / missing twin body
        "verify_translation",  # localized: both sides moved off base
        "conflict_shared",  # shared: both sides moved, differ
        "pending_divergence",  # shared sides differ, neither moved off base
        "remove_vs_edit",  # one side removed, other edited
        "remove_localized_side",  # one half of a localized pair deleted
        "unify_choose_body",  # §7.2 unify intent, bodies differ
        "fork_pending_twin",  # §7.2 fork in progress (one side marked)
        "unify_pending_twin",  # §7.2 unify in progress (one attr removed)
        "conflict_owner",  # owner references disagree
        "broken_owner",  # owner matches no anchor
        "kind_mismatch",  # paired sides disagree about cell kind
        "order_decision",  # both sides reordered differently
        "ambiguous_alignment",  # §3.3 residue: dup-fp reorder + edit
        "conflict_preamble",  # preambles moved differently on both sides
        "verify_cold",  # no baseline entry (ledger mode)
    }
)


def content_fingerprint(cell: SideCell) -> str:
    """The cell's byte fingerprint, modulo the ``slide_id`` attribute.

    Covers every byte the projection emits (header attrs, body, trailing
    separator lines) except the id attribute — the same parity rule the
    lens applies to shared members, so an id stamp is a §7.3 transition,
    never a content change. This is the fingerprint the ledger records.
    """
    return hashlib.sha256("\n".join(_lines_sans_id(cell)).encode("utf-8")).hexdigest()


# The for_slide attribute, stripped alongside slide_id for the owner-free
# signature: an owner re-home (or a group rename cascading into for_slide)
# must not read as a content edit.
_FOR_SLIDE_ATTR_RE = re.compile(r'\s*for_slide="[^"]*"')


def _pair_sig(cell: SideCell) -> str:
    """The content fingerprint additionally modulo the ``for_slide`` attribute.

    Everything except identity (slide_id) and ownership (for_slide) —
    body, tags, lang attr, vo_anchor, unknown header attrs, separator
    bytes. When this signature is base-identical on both sides, the only
    thing that can have moved is the owner reference, which has its own
    rows — so the content classification can be skipped without silencing
    any other one-sided drift (the review's early-return finding).
    """
    lines = _lines_sans_id(cell)
    header = _FOR_SLIDE_ATTR_RE.sub("", lines[0])
    return hashlib.sha256("\n".join((header, *lines[1:])).encode("utf-8")).hexdigest()


def _body_fp(cell: SideCell) -> str:
    return hashlib.sha256(cell.body.encode("utf-8")).hexdigest()


def _lines_fp(lines: tuple[str, ...] | None) -> str | None:
    if lines is None:
        return None
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The baseline
# ---------------------------------------------------------------------------


@frozen
class MemberBaseline:
    """One member's recorded state (design §5 entry, engine view).

    ``de_fp`` / ``en_fp`` are per-side content fingerprints (``None`` = the
    side was absent). A shared member normally records one value in both.
    Tags and body fingerprints are carried separately so the classifier can
    name the changed aspect without re-reading base bytes.
    """

    key: str  # rendered MemberKey
    langness: str  # shared | localized
    layout: str  # inline | companion
    kind: str
    role: str
    owner: str | None  # rendered owner MemberKey
    de_fp: str | None
    en_fp: str | None
    de_body_fp: str | None
    en_body_fp: str | None
    de_tags: tuple[str, ...] | None
    en_tags: tuple[str, ...] | None
    de_sig: str | None = None  # owner-free signature (see _pair_sig)
    en_sig: str | None = None

    def side_fp(self, lang: Lang) -> str | None:
        return self.de_fp if lang == "de" else self.en_fp

    def side_sig(self, lang: Lang) -> str | None:
        return self.de_sig if lang == "de" else self.en_sig

    @property
    def one_sided(self) -> bool:
        return (self.de_fp is None) != (self.en_fp is None)


@define
class DeckBaseline:
    """The recorded state of one deck: member entries plus order context.

    ``complete`` distinguishes a *snapshot* (parsed from a git ref or the
    Phase 2 shadow input — every member of the base state is present, so a
    current member with no entry is genuinely **new**) from a *ledger*
    (entries accumulate per verified member — a missing entry is **cold**,
    an ``unverified`` framed item, design §5).
    """

    members: dict[str, MemberBaseline] = field(factory=dict)
    #: anchor ids in base document order (rename detection)
    group_order: list[str] = field(factory=list)
    #: per side: anchor ids in that side's file order (order diff — per
    #: side, never the DE-biased merged order)
    group_order_by_side: dict[Lang, list[str]] = field(factory=dict)
    #: per (lang, group, part): ID-KEYED member handles in that side's file
    #: order. Positional handles are excluded — their ordinals renumber on
    #: any insert/remove, so they alias different cells across states (the
    #: pool alignment owns their order instead).
    member_order: dict[tuple[Lang, str, str], list[str]] = field(factory=dict)
    #: per (lang, part): preamble fingerprint (None = file absent at base)
    preamble_fps: dict[tuple[str, str], str | None] = field(factory=dict)
    complete: bool = True


def _pair_twin(de_member: Member | None, en_member: Member | None) -> Member | None:
    """The EN-carrier of a pool slot when it differs from the DE-carrier.

    ``DiffItem.twin`` side convention: ``member`` = the DE-carrying member
    (`de_member or en_member`), ``twin`` = the distinct EN-carrying member.
    """
    if de_member is not None and en_member is not None and en_member is not de_member:
        return en_member
    return None


def _member_group_token(member: Member, owner_group: str) -> str:
    if member.key.scheme == "pos":
        # rsplit: a group token may itself contain "/" (ids are free-form)
        return member.key.value.rsplit("/", 2)[0]
    return owner_group


def _iter_with_groups(deck: BilingualDeck):
    """Yield ``(member, group_token)`` over the whole document."""
    for member in deck.header:
        yield member, HEADER_GROUP
    for group in deck.groups:
        if group.anchor is not None:
            yield group.anchor, group.anchor_id
        for member in group.members:
            yield member, group.anchor_id
    for member in deck.orphans:
        yield member, ORPHAN_GROUP


def baseline_from_deck(deck: BilingualDeck) -> DeckBaseline:
    """Snapshot a parsed deck as a complete baseline (design §6.1).

    This is how the Phase 2 shadow mode and the ``--since`` forensic view
    obtain a base: parse the bundle at the base ref and record every
    member's state. Phase 3 constructs the same shape from the ledger with
    ``complete=False``.
    """
    base = DeckBaseline(complete=True)
    order: dict[tuple[Lang, str, str], list[tuple[int, str]]] = {}
    for member, group_token in _iter_with_groups(deck):
        entry = MemberBaseline(
            key=member.key.render(),
            langness=member.langness,
            layout=member.layout,
            kind=member.kind,
            role=member.role,
            owner=member.owner.render() if member.owner else None,
            de_fp=content_fingerprint(member.de) if member.de else None,
            en_fp=content_fingerprint(member.en) if member.en else None,
            de_body_fp=_body_fp(member.de) if member.de else None,
            en_body_fp=_body_fp(member.en) if member.en else None,
            de_tags=member.de.tags if member.de else None,
            en_tags=member.en.tags if member.en else None,
            de_sig=_pair_sig(member.de) if member.de else None,
            en_sig=_pair_sig(member.en) if member.en else None,
        )
        base.members[entry.key] = entry
        if member.key.scheme != "id":
            continue  # pos handles alias across states — never order-tracked
        for lang in _SIDES:
            cell = member.side(lang)
            if cell is not None:
                order.setdefault((lang, group_token, cell.part), []).append((cell.index, entry.key))
    base.member_order = {
        key: [handle for _, handle in sorted(entries)] for key, entries in order.items()
    }
    base.group_order = [g.anchor_id for g in deck.groups]
    for lang in _SIDES:
        seq: list[tuple[int, str]] = []
        for group in deck.groups:
            if group.anchor is None:
                continue
            cell = group.anchor.side(lang)
            if cell is not None and cell.part == "deck":
                seq.append((cell.index, group.anchor_id))
        base.group_order_by_side[lang] = [gid for _, gid in sorted(seq)]
    base.preamble_fps = {
        ("de", "deck"): _lines_fp(deck.de_deck_preamble),
        ("en", "deck"): _lines_fp(deck.en_deck_preamble),
        ("de", "companion"): _lines_fp(deck.de_companion_preamble),
        ("en", "companion"): _lines_fp(deck.en_companion_preamble),
    }
    return base


# ---------------------------------------------------------------------------
# Diff items
# ---------------------------------------------------------------------------


@frozen
class DiffItem:
    """One non-in-sync member verdict — the §6.4 report row.

    ``key`` is the member's canonical handle (the base entry's key for a
    matched member, the current parse key otherwise). ``member`` carries
    the full current bytes (both sides), so excerpts are structurally free;
    ``base`` carries the recorded state the verdict compared against.
    """

    key: str
    outcome: Outcome
    action: str
    direction: Direction
    detail: str
    group: str | None = None
    side: Lang | None = None
    member: Member | None = None
    base: MemberBaseline | None = None
    #: For positional pool slots whose two sides live on DIFFERENT parsed
    #: members (a one-sided insert shifts the cross-side pairing). Fixed
    #: side convention: when ``twin`` is set, ``member`` carries the slot's
    #: **DE** cell and ``twin`` its **EN** cell; when ``None``, ``member``
    #: carries every present side. The Phase-3 executor resolves each side
    #: through this convention so it always acts on the right cell.
    twin: Member | None = None

    def payload(self) -> dict:
        entry: dict = {
            "key": self.key,
            "outcome": self.outcome,
            "action": self.action,
            "direction": self.direction,
            "detail": self.detail,
        }
        if self.group is not None:
            entry["group"] = self.group
        if self.side is not None:
            entry["side"] = self.side
        for lang in _SIDES:
            holder = self.twin if (self.twin is not None and lang == "en") else self.member
            cell = holder.side(lang) if holder is not None else None
            if cell is not None:
                entry[lang] = "\n".join(cell.lines)
        return entry


@define
class DeckDiff:
    """The full verdict over one deck (design §6.4)."""

    items: list[DiffItem] = field(factory=list)
    in_sync_count: int = 0
    observations: list[Observation] = field(factory=list)
    refusal: NormalizeRefusal | None = None

    @property
    def is_clean(self) -> bool:
        return not self.items and self.refusal is None

    @property
    def needs_model(self) -> bool:
        """A model-frameable task exists (translate/adapt rows)."""
        return any(i.action in ("translate_edit", "translate_new") for i in self.items)

    @property
    def needs_agent(self) -> bool:
        """Judgment beyond a framed translation is required."""
        if self.refusal is not None:
            return True
        return any(
            i.action in FRAMED_ACTIONS and i.action not in ("translate_edit", "translate_new")
            for i in self.items
        )

    def to_payload(self) -> dict:
        """The self-describing JSON envelope (design §12.5, ``schema: 3``)."""
        counts = Counter(i.outcome for i in self.items)
        return {
            "schema": 3,
            "engine": "v3",
            "is_clean": self.is_clean,
            "needs_model": self.needs_model,
            "needs_agent": self.needs_agent,
            "in_sync": self.in_sync_count,
            "counts": dict(sorted(counts.items())),
            "items": [i.payload() for i in self.items],
            "observations": [
                {
                    "kind": o.kind,
                    "member": o.member.render() if o.member else None,
                    "side": o.side,
                    "detail": o.detail,
                }
                for o in self.observations
            ],
            "refusal": (
                {"reasons": [{"code": r.code, "detail": r.detail} for r in self.refusal.reasons]}
                if self.refusal
                else None
            ),
        }


# ---------------------------------------------------------------------------
# Field coverage (§6.3): every serialized field is compared or cosmetic.
# ---------------------------------------------------------------------------

#: Member fields the differ compares (directly or via the content
#: fingerprint + drill-down). ``key`` is identity itself (P1), never data.
COMPARED_MEMBER_FIELDS = frozenset({"kind", "role", "langness", "layout", "owner", "de", "en"})
COSMETIC_MEMBER_FIELDS = frozenset({"key"})

#: SideCell fields. ``lines`` is the content fingerprint (covers every
#: byte incl. tags / lang / for_slide / vo_anchor / cell markers, modulo
#: slide_id); named fields refine the row choice. ``index``/``line_number``
#: are file positions — order divergence is compared at group level
#: (the ``order`` outcome), the raw numbers themselves are cosmetic.
COMPARED_SIDECELL_FIELDS = frozenset(
    {"lines", "part", "lang_attr", "tags", "slide_id", "for_slide", "cell_type", "vo_anchor"}
)
COSMETIC_SIDECELL_FIELDS = frozenset({"index", "line_number"})


# ---------------------------------------------------------------------------
# The differ
# ---------------------------------------------------------------------------


def _direction(moved_de: bool, moved_en: bool) -> Direction:
    if moved_de and moved_en:
        return "both"
    if moved_de:
        return "de_to_en"
    if moved_en:
        return "en_to_de"
    return "none"


class _Differ:
    def __init__(self, current: BilingualDeck, base: DeckBaseline | None) -> None:
        self.current = current
        self.base = base
        self.items: list[DiffItem] = []
        self.in_sync = 0
        self.matched_base_keys: set[str] = set()
        #: pos→id key migrations discovered while matching (base key → new)
        self.key_migrations: dict[str, str] = {}
        #: base group id → current group id (identity + detected renames)
        self.group_map: dict[str, str] = {}
        #: current pos members, for mid-transition twin absorption
        self._pos_members: list[tuple[Member, str]] = []
        #: current id-keyed members, for conflicting-stamp detection
        self._id_members: list[tuple[Member, str]] = []
        #: pos members absorbed into a transition item (skipped by pools)
        self.absorbed_pos: set[int] = set()
        #: id-keyed members consumed by another member's conflict item
        self.consumed_id_members: set[str] = set()
        #: content fps of migrated base entries → the winning id handle
        self.migrated_fps: dict[str, str] = {}

    # -- plumbing ---------------------------------------------------------

    def emit(
        self,
        key: str,
        outcome: Outcome,
        action: str,
        direction: Direction,
        detail: str,
        *,
        group: str | None = None,
        side: Lang | None = None,
        member: Member | None = None,
        base: MemberBaseline | None = None,
        twin: Member | None = None,
    ) -> None:
        if action not in MECHANICAL_ACTIONS and action not in FRAMED_ACTIONS:
            raise ValueError(f"unregistered diff action: {action}")  # pragma: no cover
        self.items.append(
            DiffItem(
                key=key,
                outcome=outcome,
                action=action,
                direction=direction,
                detail=detail,
                group=group,
                side=side,
                member=member,
                base=base,
                twin=twin if twin is not member else None,
            )
        )

    # -- main -------------------------------------------------------------

    def run(self) -> DeckDiff:
        if self.base is None:
            for member, group in _iter_with_groups(self.current):
                self.emit(
                    member.key.render(),
                    "unverified",
                    "verify_cold",
                    "none",
                    "no baseline entry — cold member, needs verification",
                    group=group,
                    member=member,
                )
            return self._finish()

        members = list(_iter_with_groups(self.current))
        id_members = [(m, g) for m, g in members if m.key.scheme == "id"]
        pos_members = [(m, g) for m, g in members if m.key.scheme == "pos"]
        self._pos_members = pos_members
        self._id_members = id_members

        self._detect_group_renames()
        self._emit_pending_id_stamps()
        for member, group in id_members:
            self._diff_id_member(member, group)
        self._diff_pos_pools(pos_members)
        self._diff_removed_id_members()
        self._diff_cross_group_moves(id_members)
        self._diff_order()
        self._diff_preambles()
        return self._finish()

    def _emit_pending_id_stamps(self) -> None:
        """§7.3 id-stamp: the #443 one-sided-id shape, observed at parse.

        The lens records ``id_stamp_pending_twin`` when an id'd cell adopted
        a positional twin; the mechanical resolution is stamping the twin,
        regardless of whether the member's content also moved."""
        for obs in self.current.observations:
            if obs.kind != "id_stamp_pending_twin" or obs.member is None:
                continue
            member = self.current.member_by_key(obs.member)
            self.emit(
                obs.member.render(),
                "transition",
                "stamp_twin_id",
                "en_to_de" if obs.side == "de" else "de_to_en",
                f"id'd on one half only — stamp the {obs.side} twin (#443)",
                side=obs.side,
                member=member,
            )

    def _finish(self) -> DeckDiff:
        return DeckDiff(
            items=self.items,
            in_sync_count=self.in_sync,
            observations=list(self.current.observations),
        )

    # -- group renames ------------------------------------------------------

    def _detect_group_renames(self) -> None:
        """Match a vanished base group to a new current group by anchor
        content (§10 rename recovery, lifted to groups): the anchor id is
        authoring identity, so an id edit with unchanged anchor content is
        a rename transition, not a group remove + add."""
        assert self.base is not None
        current_ids = {g.anchor_id for g in self.current.groups}
        base_ids = set(self.base.group_order)
        for gid in current_ids & base_ids:
            self.group_map[gid] = gid
        gone = [gid for gid in self.base.group_order if gid not in current_ids]
        new = [g for g in self.current.groups if g.anchor_id not in base_ids]
        for new_group in new:
            if new_group.anchor is None:
                continue
            anchor_fps = (
                content_fingerprint(new_group.anchor.de) if new_group.anchor.de else None,
                content_fingerprint(new_group.anchor.en) if new_group.anchor.en else None,
            )
            for gid in gone:
                old = self.base.members.get(MemberKey.for_id(gid).render())
                if old is None:
                    continue
                if (old.de_fp, old.en_fp) == anchor_fps:
                    self.group_map[gid] = new_group.anchor_id
                    self.key_migrations[old.key] = new_group.anchor.key.render()
                    self.matched_base_keys.add(old.key)
                    gone.remove(gid)
                    self.emit(
                        new_group.anchor.key.render(),
                        "transition",
                        "record_group_rename",
                        "both",
                        f'group "{gid}" renamed to "{new_group.anchor_id}" '
                        f"(anchor content unchanged)",
                        group=new_group.anchor_id,
                        member=new_group.anchor,
                        base=old,
                    )
                    break

    # -- id-keyed members ----------------------------------------------------

    def _diff_id_member(self, member: Member, group: str) -> None:
        assert self.base is not None
        handle = member.key.render()
        if handle in self.key_migrations.values():
            return  # already emitted as a group rename transition
        if handle in self.consumed_id_members:
            return  # claimed by another member's conflict item
        entry = self.base.members.get(handle)
        if entry is None:
            entry = self._match_key_migration(member, group)
        if entry is None:
            self._diff_unmatched_current(member, group)
            return
        self.matched_base_keys.add(entry.key)
        self._classify_matched(member, group, entry)

    def _match_key_migration(self, member: Member, group: str) -> MemberBaseline | None:
        """§7.3 id-stamp: a previously positional member gained an id.

        The single sanctioned key migration: an unmatched id-keyed current
        member matching an unconsumed positional base entry of the same
        group and kind migrates that entry's key. Matching is by content
        fingerprint (a plain id stamp leaves the fingerprint unchanged —
        the id attribute is outside it); a member observed *localized* also
        matches by body fingerprint on either side, because a fork always
        rewrites the header (the new ``lang=`` attribute is exactly the
        §7.3 intent channel) while a pure fork keeps the bodies.
        """
        assert self.base is not None
        base_group = self._base_group_for(group)
        fps = self._member_fps(member)
        body_fps = {_body_fp(cell) for cell in (member.de, member.en) if cell is not None}
        forking = self._observed_langness(member) != "shared"
        for entry in self.base.members.values():
            if entry.key in self.matched_base_keys or not entry.key.startswith("pos:"):
                continue
            token, kind, _ = entry.key.split(":", 1)[1].rsplit("/", 2)
            if token != base_group or kind != member.kind:
                continue
            content_match = (entry.de_fp, entry.en_fp) == fps or (
                entry.langness == "shared"
                and entry.de_fp is not None
                and entry.de_fp in fps
                and entry.de_fp == entry.en_fp
            )
            fork_match = (
                forking
                and entry.langness == "shared"
                and entry.de_body_fp is not None
                and entry.de_body_fp in body_fps
            )
            if content_match or fork_match:
                new_handle = member.key.render()
                self.key_migrations[entry.key] = new_handle
                for fp in (entry.de_fp, entry.en_fp):
                    if fp is not None:
                        self.migrated_fps[fp] = new_handle
                return entry
        return None

    def _stamped_candidate_exists(self, group: str, kind: str, side: Lang) -> bool:
        """An unmatched one-sided id'd current member on ``side`` in this
        pool's group/kind — the shape a stamped(+edited) pool cell takes."""
        assert self.base is not None
        migration_targets = set(self.key_migrations.values())
        for candidate, owner_group in self._id_members:
            if not candidate.is_one_sided or candidate.kind != kind:
                continue
            if owner_group != group or candidate.side(side) is None:
                continue
            handle = candidate.key.render()
            if handle in self.base.members or handle in migration_targets:
                continue
            return True
        return False

    def _pool_side_deficit(self, group: str, kind: str, side: Lang) -> bool:
        """True when the (group, kind) pool has fewer current cells on
        ``side`` than unclaimed base entries recorded that side — some base
        cell is unaccounted for there."""
        assert self.base is not None
        base_token = self._base_group_for(group)
        base_count = 0
        for entry in self.base.members.values():
            if not entry.key.startswith("pos:") or entry.key in self.matched_base_keys:
                continue
            token, entry_kind, _ = entry.key.split(":", 1)[1].rsplit("/", 2)
            if token == base_token and entry_kind == kind and entry.side_fp(side) is not None:
                base_count += 1
        cur_count = 0
        for candidate, owner_group in self._pos_members:
            if id(candidate) in self.absorbed_pos or candidate.kind != kind:
                continue
            if _member_group_token(candidate, owner_group) != group:
                continue
            if candidate.side(side) is not None:
                cur_count += 1
        return base_count > cur_count

    def _conflicting_stamp(self, member: Member) -> str | None:
        """The winner's handle when this cell's content already migrated a
        positional base entry to a *different* id — the two halves stamped
        competing ids onto the same cell (never resolved silently, P8)."""
        my_handle = member.key.render()
        for fp in self._member_fps(member):
            if fp is None:
                continue
            winner = self.migrated_fps.get(fp)
            if winner is not None and winner != my_handle:
                return winner
        return None

    def _base_group_for(self, current_group: str) -> str:
        for base_gid, cur_gid in self.group_map.items():
            if cur_gid == current_group:
                return base_gid
        return current_group

    @staticmethod
    def _member_fps(member: Member) -> tuple[str | None, str | None]:
        return (
            content_fingerprint(member.de) if member.de else None,
            content_fingerprint(member.en) if member.en else None,
        )

    def _absorb_pos_twin(
        self,
        group: str,
        kind: str,
        lang: Lang,
        *,
        body_fp: str | None,
        content_fp: str | None,
    ) -> Member | None:
        """Claim the still-untransitioned twin cell of a mid-transition member.

        A half-completed fork/unify leaves its unmarked twin in a different
        pair class, so the parse could not pair them — the twin surfaces as
        a one-sided positional member. Absorbing it into the transition item
        prevents the false ``copy_new_shared`` that would *duplicate* the
        cell on apply.
        """
        for candidate, owner_group in self._pos_members:
            if id(candidate) in self.absorbed_pos or candidate.kind != kind:
                continue
            token = _member_group_token(candidate, owner_group)
            if token != group or not candidate.is_one_sided:
                continue
            cell = candidate.side(lang)
            if cell is None:
                continue
            if (body_fp is not None and _body_fp(cell) == body_fp) or (
                content_fp is not None and content_fingerprint(cell) == content_fp
            ):
                self.absorbed_pos.add(id(candidate))
                return candidate
        return None

    def _diff_unmatched_current(self, member: Member, group: str) -> None:
        """A current member with no base entry: add (snapshot) / cold (ledger)."""
        assert self.base is not None
        handle = member.key.render()
        if not self.base.complete:
            self.emit(
                handle,
                "unverified",
                "verify_cold",
                "none",
                "no ledger entry — cold member, needs verification",
                group=group,
                member=member,
            )
            return
        rival = self._conflicting_stamp(member)
        if rival is not None:
            self.emit(
                handle,
                "conflict",
                "ambiguous_alignment",
                "both",
                f"conflicting id stamps: this cell's content matched a positional "
                f"base entry already claimed by {rival} — the halves stamped "
                f"different ids onto the same cell; decide which id wins",
                group=group,
                member=member,
            )
            return
        if member.role == "header":
            side_h: Lang = "de" if member.de is not None else "en"
            self.emit(
                handle,
                "add",
                "translate_new",
                "de_to_en" if side_h == "de" else "en_to_de",
                f"new header member on the {side_h} side — headers are "
                f"per-language, adapt for the twin",
                group=group,
                side=side_h,
                member=member,
            )
            return
        if member.langness == "localized" and member.role != "header":
            side: Lang | None = "de" if member.en is None else "en" if member.de is None else None
            self.emit(
                handle,
                "add",
                "translate_new",
                _direction(member.en is None, member.de is None),
                "new localized member — twin needs a translation"
                if member.is_one_sided
                else "new localized member on both sides — verify the pairing",
                group=group,
                side=side,
                member=member,
            )
            return
        if member.is_one_sided:
            side = "de" if member.de is not None else "en"
            if self._pool_side_deficit(group, member.kind, side):
                # A base cell of this pool is unaccounted for on this side:
                # the "new" id'd cell is plausibly that cell, stamped AND
                # edited. A mechanical copy could duplicate it on apply —
                # frame instead (P8).
                self.emit(
                    handle,
                    "conflict",
                    "ambiguous_alignment",
                    "both",
                    f"new id'd cell on the {side} side while a positional base "
                    f"cell of this pool is unaccounted for — possibly the same "
                    f"cell stamped and edited; reconcile before copying",
                    group=group,
                    side=side,
                    member=member,
                )
                return
            self.emit(
                handle,
                "add",
                "copy_new_shared",
                "de_to_en" if side == "de" else "en_to_de",
                f"new shared member on the {side} side — verbatim copy to the twin",
                group=group,
                side=side,
                member=member,
            )
            return
        de_fp, en_fp = self._member_fps(member)
        if de_fp == en_fp:
            self.emit(
                handle,
                "add",
                "record_symmetric_add",
                "both",
                "identical new member on both sides — record",
                group=group,
                member=member,
            )
        else:
            self.emit(
                handle,
                "conflict",
                "conflict_shared",
                "both",
                "new shared member differs between the sides",
                group=group,
                member=member,
            )

    # -- classification of one matched member ----------------------------------

    def _classify_matched(self, member: Member, group: str, entry: MemberBaseline) -> None:
        handle = entry.key if entry.key.startswith("id:") else member.key.render()
        migrated = self.key_migrations.get(entry.key)

        # Layout transitions (§7.3 relayout) — orthogonal to content rows.
        self._check_layout(member, group, entry, handle)
        self._check_owner(member, group, entry, handle)
        stable_class = (
            member.role == "header"
            or entry.role == "header"
            or self._observed_langness(member) == entry.langness
        )
        two_sided = member.de is not None and member.en is not None
        base_two_sided = entry.de_fp is not None and entry.en_fp is not None
        cross_divergent = (
            entry.langness == "shared"
            and member.role != "header"
            and member.de is not None
            and member.en is not None
            and _pair_sig(member.de) != _pair_sig(member.en)
        )
        if (
            stable_class
            and two_sided
            and base_two_sided
            and not cross_divergent
            and self._only_owner_moved(member, entry)
        ):
            # The owner-free signature is base-identical on every side:
            # whatever moved the content fingerprint was the owner attribute
            # alone. The owner/layout rows above cover an actual re-home; an
            # owner delta fully explained by a group rename (mapped through
            # key_migrations) needs nothing at all. One-sided states never
            # take this shortcut — a pending twin is work regardless.
            if migrated is not None and not self._has_item(handle):
                self.emit(
                    handle,
                    "transition",
                    "record_key_migration",
                    "none",
                    f"member key migrates {entry.key} → {migrated}",
                    group=group,
                    member=member,
                    base=entry,
                )
            elif not self._has_item(handle):
                self.in_sync += 1
            return
        if member.de and member.en and member.de.cell_type != member.en.cell_type:
            self.emit(
                handle,
                "conflict",
                "kind_mismatch",
                "both",
                f"sides disagree about the cell kind "
                f"({member.de.cell_type} vs {member.en.cell_type})",
                group=group,
                member=member,
                base=entry,
            )
            return

        base_class = entry.langness
        observed = self._observed_langness(member)

        if member.role == "header" or entry.role == "header":
            # Headers (and the title anchor) are per-language BY DESIGN —
            # their langness never transitions, whatever the lang attrs say.
            self._classify_localized(member, group, entry, handle)
        elif base_class == "shared" and observed == "shared":
            self._classify_shared(member, group, entry, handle)
        elif base_class == "localized" and observed == "localized":
            self._classify_localized(member, group, entry, handle)
        elif base_class == "shared":
            self._classify_fork(member, group, entry, handle)
        else:
            self._classify_unify(member, group, entry, handle)

        if migrated is not None and not self._has_item(handle):
            # Content unchanged, only the key migrated: an explicit, logged
            # rename of the ledger key (§7.3) — mechanical. (The one-sided
            # #443 shape already carries its stamp_twin_id item.)
            self.emit(
                handle,
                "transition",
                "record_key_migration",
                "none",
                f"member key migrates {entry.key} → {migrated}",
                group=group,
                member=member,
                base=entry,
            )

    def _has_item(self, handle: str) -> bool:
        return any(i.key == handle for i in self.items)

    @staticmethod
    def _only_owner_moved(member: Member, entry: MemberBaseline) -> bool:
        """True when the owner-free signature (:func:`_pair_sig` — every
        byte except the slide_id and for_slide attributes) is base-identical
        on every present side: the content fingerprint can only have moved
        through the owner reference."""
        for cell, sig in ((member.de, entry.de_sig), (member.en, entry.en_sig)):
            if cell is None:
                continue
            if sig is None or _pair_sig(cell) != sig:
                return False
        return True

    @staticmethod
    def _observed_langness(member: Member) -> str:
        """Joint observed class from the lang attributes (§7.1)."""
        de_attr = member.de.lang_attr is not None if member.de else None
        en_attr = member.en.lang_attr is not None if member.en else None
        stated = [a for a in (de_attr, en_attr) if a is not None]
        if stated and all(stated):
            return "localized"
        if stated and not any(stated):
            return "shared"
        return "mixed"

    def _check_layout(self, member: Member, group: str, entry: MemberBaseline, handle: str) -> None:
        de_layout = (
            ("companion" if member.de.part == "companion" else "inline") if member.de else None
        )
        en_layout = (
            ("companion" if member.en.part == "companion" else "inline") if member.en else None
        )
        layouts = {v for v in (de_layout, en_layout) if v is not None}
        if len(layouts) == 2:
            moved: Lang = "de" if de_layout != entry.layout else "en"
            self.emit(
                handle,
                "transition",
                "mirror_layout",
                "de_to_en" if moved == "de" else "en_to_de",
                f"relayout in progress: the {moved} half moved to "
                f"{de_layout if moved == 'de' else en_layout}, the twin is still "
                f"{entry.layout}",
                group=group,
                side=moved,
                member=member,
                base=entry,
            )
        elif layouts and layouts != {entry.layout}:
            self.emit(
                handle,
                "transition",
                "record_relayout",
                "both",
                f"member relayouted {entry.layout} → {layouts.pop()} on both halves",
                group=group,
                member=member,
                base=entry,
            )

    def _check_owner(self, member: Member, group: str, entry: MemberBaseline, handle: str) -> None:
        de_owner = member.de.for_slide if member.de else None
        en_owner = member.en.for_slide if member.en else None
        if member.layout != "companion" and entry.layout != "companion":
            return
        both_companion = (
            member.de is not None
            and member.en is not None
            and member.de.part == "companion"
            and member.en.part == "companion"
        )
        if both_companion and de_owner != en_owner:
            self.emit(
                handle,
                "conflict",
                "conflict_owner",
                "both",
                f"owner references disagree (de: {de_owner!r}, en: {en_owner!r})",
                group=group,
                member=member,
                base=entry,
            )
            return
        current_owner = member.owner.render() if member.owner else None
        if group == ORPHAN_GROUP:
            self.emit(
                handle,
                "conflict",
                "broken_owner",
                "none",
                f"owner reference {de_owner or en_owner!r} matches no slide anchor",
                group=group,
                member=member,
                base=entry,
            )
            return
        base_owner = entry.owner
        if base_owner is not None and current_owner is not None and base_owner != current_owner:
            mapped = self.key_migrations.get(base_owner, base_owner)
            if mapped != current_owner:
                self.emit(
                    handle,
                    "mechanical",
                    "record_owner",
                    "both",
                    f"owner moved {base_owner} → {current_owner}",
                    group=group,
                    member=member,
                    base=entry,
                )

    # -- §7.2 base class shared -------------------------------------------------

    def _classify_shared(
        self, member: Member, group: str, entry: MemberBaseline, handle: str
    ) -> None:
        if (entry.de_fp is None) != (entry.en_fp is None):
            self._classify_base_one_sided(member, group, entry, handle)
            return
        de_fp, en_fp = self._member_fps(member)
        # Each side moves against ITS OWN recorded fingerprint: a baseline
        # that itself carried a divergence must never make the unchanged
        # twin look edited (the review's false-propagate finding).
        moved_de = member.de is not None and de_fp != entry.de_fp
        moved_en = member.en is not None and en_fp != entry.en_fp
        base_diverged = entry.de_fp != entry.en_fp

        if member.de is None or member.en is None:
            self._classify_one_sided(member, group, entry, handle)
            return
        if not moved_de and not moved_en:
            if de_fp != en_fp:  # base itself carried the divergence
                self.emit(
                    handle,
                    "conflict",
                    "pending_divergence",
                    "none",
                    "sides differ byte-wise and neither moved off base — "
                    "in-flight divergence carried at the baseline",
                    group=group,
                    member=member,
                    base=entry,
                )
            else:
                self.in_sync += 1
            return
        if de_fp == en_fp:
            self.emit(
                handle,
                "mechanical",
                "record_symmetric_edit",
                "both",
                "the sides converged on identical bytes — record the new fingerprint",
                group=group,
                member=member,
                base=entry,
            )
            return
        if (moved_de and moved_en) or base_diverged:
            # Both moved — or one moved while the pair was already diverged
            # at base: either way no side is a safe verbatim source.
            self.emit(
                handle,
                "conflict",
                "conflict_shared" if moved_de and moved_en else "pending_divergence",
                "both" if moved_de and moved_en else "none",
                "both sides moved off base and differ"
                if moved_de and moved_en
                else "one side moved while the pair was already diverged at base — "
                "align before recording",
                group=group,
                member=member,
                base=entry,
            )
            return
        moved_side: Lang = "de" if moved_de else "en"
        moved_cell = member.side(moved_side)
        twin_cell = member.side("en" if moved_de else "de")
        assert moved_cell is not None and twin_cell is not None
        direction: Direction = "de_to_en" if moved_de else "en_to_de"
        if moved_cell.body == twin_cell.body and moved_cell.tags != twin_cell.tags:
            self.emit(
                handle,
                "mechanical",
                "mirror_tags",
                direction,
                f"tag set changed on the {moved_side} side "
                f"({list(twin_cell.tags)} → {list(moved_cell.tags)})",
                group=group,
                side=moved_side,
                member=member,
                base=entry,
            )
            return
        self.emit(
            handle,
            "mechanical",
            "propagate_shared_edit",
            direction,
            f"shared member edited on the {moved_side} side — verbatim copy to the twin",
            group=group,
            side=moved_side,
            member=member,
            base=entry,
        )

    def _classify_base_one_sided(
        self, member: Member, group: str, entry: MemberBaseline, handle: str
    ) -> None:
        """Base class shared but the twin was ALREADY missing at base.

        The twin's absence is carried state, never a removal — classifying
        it ``mirror_remove`` would delete the surviving side's content on
        apply. The pending twin stays a mechanical copy; a twin that landed
        with different bytes is a framed divergence.
        """
        recorded: Lang = "de" if entry.de_fp is not None else "en"
        pending: Lang = "en" if recorded == "de" else "de"
        recorded_cell = member.side(recorded)
        pending_cell = member.side(pending)
        if pending_cell is None:
            if recorded_cell is None:
                self.emit(
                    handle,
                    "remove",
                    "record_remove",
                    "both",
                    "the recorded side is gone (its twin was never present) — record the removal",
                    group=group,
                    base=entry,
                )
                return
            self.emit(
                handle,
                "add",
                "copy_new_shared",
                "de_to_en" if recorded == "de" else "en_to_de",
                f"the {pending} twin is still missing — verbatim copy from the {recorded} side",
                group=group,
                side=recorded,
                member=member,
                base=entry,
            )
            return
        if recorded_cell is None:
            self.emit(
                handle,
                "conflict",
                "remove_vs_edit",
                "both",
                f"the recorded {recorded} side vanished while the {pending} twin appeared — decide",
                group=group,
                side=recorded,
                member=member,
                base=entry,
            )
            return
        de_fp, en_fp = self._member_fps(member)
        if de_fp == en_fp:
            self.emit(
                handle,
                "add",
                "record_symmetric_add",
                "both",
                f"the pending {pending} twin landed byte-identically — record",
                group=group,
                member=member,
                base=entry,
            )
            return
        self.emit(
            handle,
            "conflict",
            "pending_divergence",
            "none",
            f"the pending {pending} twin landed but differs from the {recorded} "
            f"side — align before recording",
            group=group,
            member=member,
            base=entry,
        )

    def _classify_one_sided(
        self,
        member: Member,
        group: str,
        entry: MemberBaseline,
        handle: str,
    ) -> None:
        """Base class shared (two-sided), current one-sided.

        A removal in progress — unless the "removed" side is actually still
        present as an estranged cell the parse could not pair (a rival id
        stamp, or a mid-transition twin combined with an edit). Concluding
        ``mirror_remove`` in those states would delete real content on
        apply, so any estranged candidate downgrades the row to a framed
        decision (P8: never a silent default).
        """
        present: Lang = "de" if member.de is not None else "en"
        gone: Lang = "en" if present == "de" else "de"
        cell = member.side(present)
        assert cell is not None

        rival = self._find_rival_stamp(member, group, gone, entry)
        if rival is not None:
            self.consumed_id_members.add(rival.key.render())
            self.emit(
                handle,
                "conflict",
                "ambiguous_alignment",
                "both",
                f"the halves stamped competing ids onto the same cell "
                f"({handle} vs {rival.key.render()}) — decide which id wins",
                group=group,
                member=member,
                base=entry,
            )
            return
        if entry.key.startswith("pos:"):
            # The entry only just migrated pos→id: the "gone" twin is very
            # likely the estranged id-less cell, not a removal.
            estranged = self._absorb_any_pos_twin(group, member.kind, gone)
            if estranged is not None:
                self.emit(
                    handle,
                    "conflict",
                    "ambiguous_alignment",
                    "both",
                    f"the {gone} half holds an unpaired cell where this member's twin "
                    f"used to be (mid-stamp or mid-transition combined with an edit) — "
                    f"reconcile the pair manually",
                    group=group,
                    member=member,
                    base=entry,
                )
                return
        if content_fingerprint(cell) == entry.side_fp(present):
            self.emit(
                handle,
                "remove",
                "mirror_remove",
                "de_to_en" if gone == "de" else "en_to_de",
                f"member removed on the {gone} side, the {present} side is unchanged — "
                f"mirror the removal (surfaced, never silent)",
                group=group,
                side=gone,
                member=member,
                base=entry,
            )
        else:
            self.emit(
                handle,
                "conflict",
                "remove_vs_edit",
                "both",
                f"removed on the {gone} side but edited on the {present} side",
                group=group,
                side=gone,
                member=member,
                base=entry,
            )

    def _find_rival_stamp(
        self, member: Member, group: str, gone: Lang, entry: MemberBaseline
    ) -> Member | None:
        """A one-sided id-keyed member on the ``gone`` side whose bytes match
        this member's base entry: the same base cell stamped with two ids."""
        assert self.base is not None
        for candidate, owner_group in self._id_members:
            if candidate is member or not candidate.is_one_sided:
                continue
            handle = candidate.key.render()
            if handle in self.consumed_id_members or handle in self.base.members:
                continue  # already handled / a genuinely matched member
            cell = candidate.side(gone)
            if cell is None or candidate.kind != member.kind or owner_group != group:
                continue
            if content_fingerprint(cell) == entry.side_fp(gone) or _body_fp(cell) == (
                entry.de_body_fp if gone == "de" else entry.en_body_fp
            ):
                return candidate
        return None

    def _absorb_any_pos_twin(self, group: str, kind: str, lang: Lang) -> Member | None:
        """Claim the single unpaired positional cell on ``lang`` in this pool.

        Used only for framed rows: when exactly one estranged candidate
        exists it is almost certainly the mid-transition twin, and claiming
        it prevents the pool from re-reporting it as a mechanical
        copy/remove. Zero or several candidates → no claim (ambiguity stays
        with the pool's own alignment)."""
        candidates = []
        for candidate, owner_group in self._pos_members:
            if id(candidate) in self.absorbed_pos or candidate.kind != kind:
                continue
            token = _member_group_token(candidate, owner_group)
            if token != group or not candidate.is_one_sided:
                continue
            if candidate.side(lang) is None:
                continue
            candidates.append(candidate)
        if len(candidates) != 1:
            return None
        self.absorbed_pos.add(id(candidates[0]))
        return candidates[0]

    # -- base class localized ---------------------------------------------------

    def _classify_localized(
        self, member: Member, group: str, entry: MemberBaseline, handle: str
    ) -> None:
        de_fp, en_fp = self._member_fps(member)
        moved_de = member.de is not None and de_fp != entry.de_fp
        moved_en = member.en is not None and en_fp != entry.en_fp

        if member.de is None and entry.de_fp is not None:
            self._localized_side_gone(member, group, entry, handle, "de", moved_en)
            return
        if member.en is None and entry.en_fp is not None:
            self._localized_side_gone(member, group, entry, handle, "en", moved_de)
            return
        if member.is_one_sided:
            # The side was already missing at base: still a pending twin.
            missing: Lang = "de" if member.de is None else "en"
            self.emit(
                handle,
                "add",
                "translate_new",
                "en_to_de" if missing == "de" else "de_to_en",
                f"the {missing} twin body is still pending",
                group=group,
                side=missing,
                member=member,
                base=entry,
            )
            return
        landed = [
            lang
            for lang, base_fp, cell in (
                ("de", entry.de_fp, member.de),
                ("en", entry.en_fp, member.en),
            )
            if base_fp is None and cell is not None
        ]
        if landed:
            self.emit(
                handle,
                "conflict",
                "verify_translation",
                "both",
                f"the pending {landed[0]} variant landed since base — verify the pair "
                f"is a faithful rendering",
                group=group,
                member=member,
                base=entry,
            )
            return
        if not moved_de and not moved_en:
            self.in_sync += 1
            return
        # A tags-only change is mechanical even on localized members: tag
        # sets mirror across languages (§3.1), bodies do not.
        if self._tags_only_change(member, entry, moved_de, moved_en):
            return
        if moved_de and moved_en:
            self.emit(
                handle,
                "conflict",
                "verify_translation",
                "both",
                "both language variants moved off base — verify the pair is still a "
                "faithful rendering",
                group=group,
                member=member,
                base=entry,
            )
            return
        moved_side: Lang = "de" if moved_de else "en"
        self.emit(
            handle,
            "edit",
            "translate_edit",
            "de_to_en" if moved_de else "en_to_de",
            f"the {moved_side} variant was edited — translate/adapt the twin",
            group=group,
            side=moved_side,
            member=member,
            base=entry,
        )

    def _tags_only_change(
        self, member: Member, entry: MemberBaseline, moved_de: bool, moved_en: bool
    ) -> bool:
        de, en = member.de, member.en
        if de is None or en is None:
            return False
        de_body_same = entry.de_body_fp is not None and _body_fp(de) == entry.de_body_fp
        en_body_same = entry.en_body_fp is not None and _body_fp(en) == entry.en_body_fp
        if not (de_body_same and en_body_same):
            return False
        de_tags_moved = entry.de_tags is not None and de.tags != entry.de_tags
        en_tags_moved = entry.en_tags is not None and en.tags != entry.en_tags
        if not (de_tags_moved or en_tags_moved):
            return False
        handle = entry.key
        if de_tags_moved and en_tags_moved:
            if de.tags == en.tags:
                self.emit(
                    handle,
                    "mechanical",
                    "record_tags",
                    "both",
                    f"tag set changed identically on both sides → {list(de.tags)}",
                    member=member,
                    base=entry,
                )
                return True
            self.emit(
                handle,
                "conflict",
                "conflict_shared",
                "both",
                f"tag sets moved differently (de: {list(de.tags)}, en: {list(en.tags)})",
                member=member,
                base=entry,
            )
            return True
        moved_side: Lang = "de" if de_tags_moved else "en"
        moved_cell = de if de_tags_moved else en
        self.emit(
            handle,
            "mechanical",
            "mirror_tags",
            "de_to_en" if de_tags_moved else "en_to_de",
            f"tag set changed on the {moved_side} side → {list(moved_cell.tags)}",
            side=moved_side,
            member=member,
            base=entry,
        )
        return True

    def _localized_side_gone(
        self,
        member: Member,
        group: str,
        entry: MemberBaseline,
        handle: str,
        gone: Lang,
        other_moved: bool,
    ) -> None:
        twin = self._absorb_pos_twin(
            group,
            member.kind,
            gone,
            body_fp=entry.de_body_fp if gone == "de" else entry.en_body_fp,
            content_fp=None,  # the twin dropped its lang attr, so bytes moved
        )
        if twin is not None:
            # The "deleted" variant is actually present, stripped of its lang
            # attribute (and id): a unify started on one side only.
            self.emit(
                handle,
                "transition",
                "unify_pending_twin",
                "de_to_en" if gone == "de" else "en_to_de",
                f"unify in progress: the {gone} side dropped its lang attribute "
                f"(and id), the twin still carries them — complete on the twin "
                f"or revert (the id stays either way, P3)",
                group=group,
                side=gone,
                member=member,
                base=entry,
            )
            return
        # A byte-exact twin was not found — the estranged cell may ALSO have
        # been edited. Claim a lone unpaired candidate so the pool cannot
        # re-report it as a mechanical copy that would duplicate the cell.
        self._absorb_any_pos_twin(group, member.kind, gone)
        self.emit(
            handle,
            "conflict",
            "remove_localized_side",
            "both" if other_moved else ("de_to_en" if gone == "en" else "en_to_de"),
            f"the {gone} variant of a localized member was deleted (or stripped of "
            f"its lang attribute and edited) — removal intent, unify intent, or an "
            f"accident: decide",
            group=group,
            side=gone,
            member=member,
            base=entry,
        )

    # -- §7.2/§7.3 class transitions -----------------------------------------------

    def _classify_fork(
        self, member: Member, group: str, entry: MemberBaseline, handle: str
    ) -> None:
        """Base shared, lang attributes observed: the member is forking."""
        observed = self._observed_langness(member)
        if observed == "mixed":
            marked: Lang = "de" if (member.de and member.de.lang_attr) else "en"
            self.emit(
                handle,
                "transition",
                "fork_pending_twin",
                "de_to_en" if marked == "de" else "en_to_de",
                f"fork in progress: the {marked} side carries a lang attribute, "
                f"the twin does not — mark the twin (and adapt its body) or revert",
                group=group,
                side=marked,
                member=member,
                base=entry,
            )
            return
        if member.is_one_sided:
            missing: Lang = "de" if member.de is None else "en"
            marked = "en" if missing == "de" else "de"
            twin = self._absorb_pos_twin(
                group,
                member.kind,
                missing,
                body_fp=entry.de_body_fp if missing == "de" else entry.en_body_fp,
                content_fp=entry.side_fp(missing),
            )
            if twin is None:
                # The unmarked twin may ALSO have been edited: claim a lone
                # unpaired candidate so the pool cannot re-report it as a
                # mechanical copy that would duplicate the cell (§7.3: a
                # fork cannot destabilize its neighbors).
                twin = self._absorb_any_pos_twin(group, member.kind, missing)
            if twin is not None:
                self.emit(
                    handle,
                    "transition",
                    "fork_pending_twin",
                    "de_to_en" if marked == "de" else "en_to_de",
                    f"fork in progress: the {marked} side carries a lang attribute "
                    f"and an id, the {missing} twin cell is still unmarked — mark "
                    f"it (lang + id) and adapt its body, or revert",
                    group=group,
                    side=marked,
                    member=member,
                    base=entry,
                )
                return
            self.emit(
                handle,
                "add",
                "translate_new",
                "en_to_de" if missing == "de" else "de_to_en",
                f"fork of a shared member: the {missing} variant body is missing",
                group=group,
                side=missing,
                member=member,
                base=entry,
            )
            return
        self.emit(
            handle,
            "transition",
            "record_fork",
            "both",
            "complete fork: both sides carry lang attributes and bodies — the entry "
            "upgrades to per-language fingerprints under the same key",
            group=group,
            member=member,
            base=entry,
        )

    def _classify_unify(
        self, member: Member, group: str, entry: MemberBaseline, handle: str
    ) -> None:
        """Base localized, lang attributes (partially) removed: unifying."""
        observed = self._observed_langness(member)
        if observed == "mixed":
            unmarked: Lang = "de" if (member.de and member.de.lang_attr is None) else "en"
            self.emit(
                handle,
                "transition",
                "unify_pending_twin",
                "de_to_en" if unmarked == "de" else "en_to_de",
                f"unify in progress: the {unmarked} side dropped its lang attribute, "
                f"the twin still carries one — complete on the twin or revert",
                group=group,
                side=unmarked,
                member=member,
                base=entry,
            )
            return
        de_fp, en_fp = self._member_fps(member)
        if member.de is not None and member.en is not None and de_fp == en_fp:
            self.emit(
                handle,
                "transition",
                "record_unify",
                "both",
                "unify complete: lang attributes removed and bodies byte-equal — the "
                "entry drops to one fingerprint under the same key (the id stays, P3)",
                group=group,
                member=member,
                base=entry,
            )
            return
        self.emit(
            handle,
            "transition",
            "unify_choose_body",
            "both",
            "unify intent: lang attributes removed but the bodies still differ — "
            "choose or author the shared body",
            group=group,
            member=member,
            base=entry,
        )

    # -- removed id-keyed members -------------------------------------------------

    def _diff_removed_id_members(self) -> None:
        assert self.base is not None
        for key, entry in self.base.members.items():
            if key in self.matched_base_keys or not key.startswith("id:"):
                continue
            self.emit(
                key,
                "remove",
                "record_remove",
                "both",
                "member present at base is gone from both sides — record the removal "
                "(surfaced, never silent)",
                base=entry,
            )

    # -- positional pools (§3.3 rule 2) ----------------------------------------------

    def _diff_pos_pools(self, pos_members: list[tuple[Member, str]]) -> None:
        """Per (group, kind) pool: per-side 3-way alignment over fingerprints.

        Decomposing pool members into their per-side cell sequences lets the
        base disambiguate what the cross-side positional pairing cannot: a
        one-sided insert shifts the twin pairing of every later sibling, but
        each side still aligns to *base* perfectly, so the insert is one
        ``add`` item instead of a cascade of false edits (the W10 shape).
        """
        assert self.base is not None
        pools: dict[tuple[str, str], list[Member]] = {}
        for member, group in pos_members:
            if id(member) in self.absorbed_pos:
                continue  # claimed by a mid-transition item
            token = _member_group_token(member, group)
            pools.setdefault((token, member.kind), []).append(member)

        consumed_base: set[str] = set(self.matched_base_keys)
        base_pools: dict[tuple[str, str], list[MemberBaseline]] = {}
        for key, entry in self.base.members.items():
            if not key.startswith("pos:") or key in consumed_base:
                continue
            token, kind, _ordinal = key.split(":", 1)[1].rsplit("/", 2)
            mapped = self.group_map.get(token, token)
            base_pools.setdefault((mapped, kind), []).append(entry)
        for pool in base_pools.values():
            pool.sort(key=lambda e: int(e.key.rsplit("/", 1)[1]))

        for pool_key in sorted(set(pools) | set(base_pools), key=str):
            group, kind = pool_key
            self._align_pool(group, kind, pools.get(pool_key, []), base_pools.get(pool_key, []))

    def _align_pool(
        self, group: str, kind: str, members: list[Member], base_entries: list[MemberBaseline]
    ) -> None:
        localized_pool = any(m.langness == "localized" for m in members) or any(
            e.langness == "localized" for e in base_entries
        )
        per_side: dict[Lang, list[tuple[str, Member]]] = {"de": [], "en": []}
        for member in members:
            for lang in _SIDES:
                cell = member.side(lang)
                if cell is not None:
                    per_side[lang].append((content_fingerprint(cell), member))

        # ``absent`` marks a base slot whose side never existed — it takes
        # no part in that side's alignment (a phantom slot could steal a
        # byte-identical real cell, the review's critical finding).
        status: dict[Lang, list[tuple[str, Member | None]]] = {}
        news: dict[Lang, list[Member]] = {}
        moved_sides: dict[Lang, bool] = {"de": False, "en": False}
        for lang in _SIDES:
            slot_map = [i for i, e in enumerate(base_entries) if e.side_fp(lang) is not None]
            base_fps = [base_entries[i].side_fp(lang) or "" for i in slot_map]
            aligned, side_new, moved = self._align_side(base_fps, per_side[lang])
            side_status: list[tuple[str, Member | None]] = [("absent", None) for _ in base_entries]
            for pos, verdict in zip(slot_map, aligned, strict=True):
                side_status[pos] = verdict
            status[lang] = side_status
            news[lang] = side_new
            moved_sides[lang] = moved

        # A pending twin that LANDED shows up as a "new" cell on the side
        # its base entry never had: claim it for the entry before the news
        # are classified (otherwise it reads as an add to copy — duplicating
        # the cell). Byte-identical claims first; then a LONE remaining
        # candidate (the twin landed with edits — framed downstream); with
        # several candidates the slot stays ambiguous and never mechanical.
        for idx, entry in enumerate(base_entries):
            if not entry.one_sided:
                continue
            pending: Lang = "de" if entry.de_fp is None else "en"
            recorded: Lang = "en" if pending == "de" else "de"
            rec_state, rec_member = status[recorded][idx]
            if rec_state in ("absent", "missing"):
                continue
            want = {entry.side_fp(recorded) or ""}
            rec_cell = rec_member.side(recorded) if rec_member else None
            if rec_cell is not None:
                want.add(content_fingerprint(rec_cell))
            claimed = None
            for candidate in news[pending]:
                cell = candidate.side(pending)
                if cell is not None and content_fingerprint(cell) in want:
                    claimed = candidate
                    break
            if claimed is None and len(news[pending]) == 1:
                claimed = news[pending][0]
            if claimed is not None:
                news[pending].remove(claimed)
                status[pending][idx] = ("landed", claimed)
            elif news[pending]:
                status[pending][idx] = ("ambiguous", None)

        for idx, entry in enumerate(base_entries):
            de_state, de_member = status["de"][idx]
            en_state, en_member = status["en"][idx]
            self.matched_base_keys.add(entry.key)
            self._classify_pool_slot(group, entry, de_state, en_state, de_member, en_member)

        self._classify_pool_news(group, news["de"], news["en"], localized_pool)
        self._emit_pool_moves(group, kind, moved_sides, per_side)

    def _emit_pool_moves(
        self,
        group: str,
        kind: str,
        moved_sides: dict[Lang, bool],
        per_side: dict[Lang, list[tuple[str, Member]]],
    ) -> None:
        if not (moved_sides["de"] or moved_sides["en"]):
            return
        handle = MemberKey.positional(group, f"pool.{kind}", 0).render()
        if moved_sides["de"] and moved_sides["en"]:
            de_fps = [fp for fp, _ in per_side["de"]]
            en_fps = [fp for fp, _ in per_side["en"]]
            if de_fps == en_fps:
                self.emit(
                    handle,
                    "order",
                    "record_order",
                    "both",
                    f"positional {kind} members of group {group!r} reordered "
                    f"identically on both sides — record",
                    group=group,
                )
            else:
                self.emit(
                    handle,
                    "order",
                    "order_decision",
                    "both",
                    f"positional {kind} members of group {group!r} moved on both sides — align",
                    group=group,
                )
            return
        moved: Lang = "de" if moved_sides["de"] else "en"
        self.emit(
            handle,
            "order",
            "mirror_order",
            "de_to_en" if moved == "de" else "en_to_de",
            f"positional {kind} members of group {group!r} moved on the {moved} "
            f"side — mirror the new order to the twin",
            group=group,
            side=moved,
        )

    @staticmethod
    def _align_side(
        base_fps: list[str], current: list[tuple[str, Member]]
    ) -> tuple[list[tuple[str, Member | None]], list[Member], bool]:
        """Align one side's cell sequence to the base pool by fingerprint.

        Two passes (§3.3's discipline): fingerprints that occur exactly once
        on each side match directly — wherever they sit, so a non-adjacent
        reorder is a *move*, never an edit+remove+add cascade — and the
        residue aligns positionally via ``SequenceMatcher`` (equal blocks =
        untouched duplicates, replace = edits, delete = removals, insert =
        additions). Returns per-base-slot ``(state, member)`` (state ∈
        ``same`` / ``changed`` / ``missing``), the genuinely new members,
        and whether the matched pairs are out of order (a reorder).
        """
        cur_fps = [fp for fp, _ in current]
        base_count = Counter(base_fps)
        cur_count = Counter(cur_fps)
        status: list[tuple[str, Member | None]] = [("missing", None)] * len(base_fps)
        pairs: list[tuple[int, int]] = []
        used: set[int] = set()
        for i, fp in enumerate(base_fps):
            if fp and base_count[fp] == 1 and cur_count.get(fp) == 1:
                j = cur_fps.index(fp)
                status[i] = ("same", current[j][1])
                used.add(j)
                pairs.append((i, j))
        residue_base = [i for i in range(len(base_fps)) if status[i][0] == "missing"]
        residue_cur = [j for j in range(len(cur_fps)) if j not in used]
        matcher = SequenceMatcher(
            a=[base_fps[i] for i in residue_base],
            b=[cur_fps[j] for j in residue_cur],
            autojunk=False,
        )
        new: list[Member] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    bi, cj = residue_base[i1 + offset], residue_cur[j1 + offset]
                    status[bi] = ("same", current[cj][1])
                    pairs.append((bi, cj))
            elif tag == "replace":
                span = min(i2 - i1, j2 - j1)
                for offset in range(span):
                    bi, cj = residue_base[i1 + offset], residue_cur[j1 + offset]
                    status[bi] = ("changed", current[cj][1])
                    # deliberately NOT a `pairs` entry: a changed slot is a
                    # positional guess, not fingerprint-identity evidence,
                    # and must never count as a reorder.
                for j in range(j1 + span, j2):
                    new.append(current[residue_cur[j]][1])
            elif tag == "insert":
                for j in range(j1, j2):
                    new.append(current[residue_cur[j]][1])
            # "delete": base slots stay "missing"
        pairs.sort()
        moved = any(c2 < c1 for (_, c1), (_, c2) in zip(pairs, pairs[1:], strict=False))
        return status, new, moved

    def _classify_pool_slot(
        self,
        group: str,
        entry: MemberBaseline,
        de_state: str,
        en_state: str,
        de_member: Member | None,
        en_member: Member | None,
    ) -> None:
        """Classify one base slot from its per-side alignment states.

        ``de_member`` / ``en_member`` may be *different* members: a
        one-sided insert shifts the cross-side parse pairing, but each side
        still aligns to base — the slot's DE cell lives on ``de_member.de``
        and its EN cell on ``en_member.en``.
        """
        handle = entry.key
        member = de_member or en_member
        # The DiffItem side convention: `member` carries the slot's DE cell,
        # `twin` its EN cell when the two live on different parsed members.
        pair_twin = _pair_twin(de_member, en_member)

        if entry.one_sided:
            self._classify_pool_slot_base_one_sided(
                group, entry, de_state, en_state, de_member, en_member
            )
            return
        if de_state == "same" and en_state == "same":
            if (
                entry.langness == "shared"
                and entry.de_fp is not None
                and entry.en_fp is not None
                and entry.de_fp != entry.en_fp
            ):
                self.emit(
                    handle,
                    "conflict",
                    "pending_divergence",
                    "none",
                    "sides differ byte-wise and neither moved off base — in-flight "
                    "divergence carried at the baseline",
                    group=group,
                    member=member,
                    base=entry,
                    twin=pair_twin,
                )
            else:
                self.in_sync += 1
            return
        if member is None:  # missing on both sides
            self.emit(
                handle,
                "remove",
                "record_remove",
                "both",
                "positional member gone from both sides — record the removal",
                group=group,
                base=entry,
            )
            return
        if entry.langness == "localized":
            self._classify_pool_slot_localized(
                group, entry, de_state, en_state, de_member, en_member
            )
            return
        states = (de_state, en_state)
        if "missing" in states:
            gone: Lang = "de" if de_state == "missing" else "en"
            other_state = en_state if gone == "de" else de_state
            if self._stamped_candidate_exists(group, entry.kind, gone):
                # An unmatched one-sided id'd cell exists on the "removed"
                # side: the cell was plausibly stamped (and edited), not
                # removed — a mechanical removal could delete real content.
                self.emit(
                    handle,
                    "conflict",
                    "ambiguous_alignment",
                    "both",
                    f"the {gone} side lost this positional cell while gaining an "
                    f"unmatched id'd cell — possibly the same cell stamped and "
                    f"edited; reconcile before removing",
                    group=group,
                    member=member,
                    base=entry,
                )
            elif other_state == "same":
                self.emit(
                    handle,
                    "remove",
                    "mirror_remove",
                    "de_to_en" if gone == "de" else "en_to_de",
                    f"positional member removed on the {gone} side — mirror the removal",
                    group=group,
                    side=gone,
                    member=member,
                    base=entry,
                )
            else:
                self.emit(
                    handle,
                    "conflict",
                    "remove_vs_edit",
                    "both",
                    f"removed on the {gone} side but edited on the twin",
                    group=group,
                    side=gone,
                    member=member,
                    base=entry,
                )
            return
        de_cell = de_member.de if de_member else None
        en_cell = en_member.en if en_member else None
        if de_state == "changed" and en_state == "changed":
            de_fp = content_fingerprint(de_cell) if de_cell else None
            en_fp = content_fingerprint(en_cell) if en_cell else None
            if de_fp == en_fp:
                self.emit(
                    handle,
                    "mechanical",
                    "record_symmetric_edit",
                    "both",
                    "identical edit on both sides — record the new fingerprint",
                    group=group,
                    member=member,
                    base=entry,
                    twin=pair_twin,
                )
            else:
                self.emit(
                    handle,
                    "conflict",
                    "conflict_shared",
                    "both",
                    "both sides moved off base and differ",
                    group=group,
                    member=member,
                    base=entry,
                    twin=pair_twin,
                )
            return
        moved: Lang = "de" if de_state == "changed" else "en"
        moved_cell = de_cell if moved == "de" else en_cell
        twin_cell = en_cell if moved == "de" else de_cell
        if moved_cell is None:  # pragma: no cover - changed implies present
            return
        if entry.de_fp != entry.en_fp:
            # One side moved while the pair was already diverged at base —
            # no side is a safe verbatim source (same rule as id-keyed).
            self.emit(
                handle,
                "conflict",
                "pending_divergence",
                "none",
                f"the {moved} side moved while the pair was already diverged at "
                f"base — align before recording",
                group=group,
                member=member,
                base=entry,
            )
            return
        if (
            twin_cell is not None
            and moved_cell.body == twin_cell.body
            and moved_cell.tags != twin_cell.tags
        ):
            self.emit(
                handle,
                "mechanical",
                "mirror_tags",
                "de_to_en" if moved == "de" else "en_to_de",
                f"tag set changed on the {moved} side "
                f"({list(twin_cell.tags)} → {list(moved_cell.tags)})",
                group=group,
                side=moved,
                member=member,
                base=entry,
                twin=pair_twin,
            )
            return
        self.emit(
            handle,
            "mechanical",
            "propagate_shared_edit",
            "de_to_en" if moved == "de" else "en_to_de",
            f"shared positional member edited on the {moved} side — verbatim copy",
            group=group,
            side=moved,
            member=member,
            base=entry,
            twin=pair_twin,
        )

    def _classify_pool_slot_base_one_sided(
        self,
        group: str,
        entry: MemberBaseline,
        de_state: str,
        en_state: str,
        de_member: Member | None,
        en_member: Member | None,
    ) -> None:
        """A pool slot whose twin was already missing at base (§ same rule
        as :meth:`_classify_base_one_sided`: carried absence ≠ removal).

        The pending side's state is ``absent`` (it never joined that side's
        alignment) unless the landed-twin claim in :meth:`_align_pool`
        upgraded it to ``landed``.
        """
        handle = entry.key
        recorded: Lang = "de" if entry.de_fp is not None else "en"
        pending: Lang = "en" if recorded == "de" else "de"
        rec_state, rec_member = (de_state, de_member) if recorded == "de" else (en_state, en_member)
        pen_state, pen_member = (en_state, en_member) if recorded == "de" else (de_state, de_member)
        member = de_member or en_member  # the DiffItem convention: DE carrier first
        pair_twin = _pair_twin(de_member, en_member)
        if rec_state == "missing":
            if pen_state == "absent":
                self.emit(
                    handle,
                    "remove",
                    "record_remove",
                    "both",
                    "the recorded side is gone (its twin was never present) — record the removal",
                    group=group,
                    base=entry,
                )
            else:
                self.emit(
                    handle,
                    "conflict",
                    "remove_vs_edit",
                    "both",
                    f"the recorded {recorded} side vanished while the {pending} "
                    f"twin landed — decide",
                    group=group,
                    side=recorded,
                    member=member,
                    base=entry,
                    twin=pair_twin,
                )
            return
        if pen_state == "ambiguous":
            self.emit(
                handle,
                "conflict",
                "ambiguous_alignment",
                "none",
                f"the {pending} twin is pending while several unmatched new cells "
                f"exist on that side — align manually before copying",
                group=group,
                member=member,
                base=entry,
            )
            return
        if pen_state == "absent":
            self.emit(
                handle,
                "add",
                "copy_new_shared",
                "de_to_en" if recorded == "de" else "en_to_de",
                f"the {pending} twin is still missing — verbatim copy from the {recorded} side",
                group=group,
                side=recorded,
                member=member,
                base=entry,
            )
            return
        # pen_state == "landed": the pending twin appeared. Byte-equal to
        # the recorded side → record; anything else → align first.
        rec_cell = rec_member.side(recorded) if rec_member else None
        pen_cell = pen_member.side(pending) if pen_member else None
        rec_fp = content_fingerprint(rec_cell) if rec_cell else None
        pen_fp = content_fingerprint(pen_cell) if pen_cell else None
        if rec_fp is not None and rec_fp == pen_fp:
            self.emit(
                handle,
                "add",
                "record_symmetric_add",
                "both",
                f"the pending {pending} twin landed byte-identically — record",
                group=group,
                member=member,
                base=entry,
                twin=pair_twin,
            )
            return
        self.emit(
            handle,
            "conflict",
            "pending_divergence",
            "none",
            f"the pending {pending} twin landed but differs from the {recorded} "
            f"side — align before recording",
            group=group,
            member=member,
            base=entry,
            twin=pair_twin,
        )

    def _classify_pool_slot_localized(
        self,
        group: str,
        entry: MemberBaseline,
        de_state: str,
        en_state: str,
        de_member: Member | None,
        en_member: Member | None,
    ) -> None:
        """Localized positional pools exist only for per-language headers."""
        handle = entry.key
        member = de_member or en_member
        pair_twin = _pair_twin(de_member, en_member)
        if "missing" in (de_state, en_state):
            gone: Lang = "de" if de_state == "missing" else "en"
            self.emit(
                handle,
                "conflict",
                "remove_localized_side",
                "both",
                f"the {gone} header line was deleted — decide removal or revert",
                group=group,
                side=gone,
                member=member,
                base=entry,
            )
            return
        moved = [lang for lang, state in (("de", de_state), ("en", en_state)) if state == "changed"]
        if len(moved) == 2:
            self.emit(
                handle,
                "conflict",
                "verify_translation",
                "both",
                "both header variants moved off base",
                group=group,
                member=member,
                base=entry,
                twin=pair_twin,
            )
            return
        self.emit(
            handle,
            "edit",
            "translate_edit",
            "de_to_en" if moved == ["de"] else "en_to_de",
            f"the {moved[0]} header variant was edited — adapt the twin",
            group=group,
            side=moved[0],  # type: ignore[arg-type]
            member=member,
            base=entry,
            twin=pair_twin,
        )

    def _classify_pool_news(
        self,
        group: str,
        de_new: list[Member],
        en_new: list[Member],
        localized_pool: bool,
    ) -> None:
        """Leftover cells with no base slot: adds — or, when both sides added
        different content into the same pool, a framed alignment decision
        (copying both would duplicate; §3.3's honest residue)."""
        de_by_fp: dict[str, list[Member]] = {}
        for m in de_new:
            cell = m.de
            assert cell is not None
            de_by_fp.setdefault(content_fingerprint(cell), []).append(m)
        matched_pairs: list[Member] = []
        en_solo: list[Member] = []
        for m in en_new:
            cell = m.en
            assert cell is not None
            fp = content_fingerprint(cell)
            bucket = de_by_fp.get(fp)
            if bucket:
                matched_pairs.append(bucket.pop(0))
            else:
                en_solo.append(m)
        de_solo = [m for bucket in de_by_fp.values() for m in bucket]

        assert self.base is not None
        if not self.base.complete:
            # Ledger mode: a pos member with no entry is COLD, never a
            # mechanical add/copy (design §5 — the id-keyed path's rule,
            # mirrored here).
            for member in {id(m): m for m in matched_pairs + de_solo + en_solo}.values():
                self.emit(
                    member.key.render(),
                    "unverified",
                    "verify_cold",
                    "none",
                    "no ledger entry — cold member, needs verification",
                    group=group,
                    member=member,
                )
            return
        for member in matched_pairs:
            self.emit(
                member.key.render(),
                "add",
                "record_symmetric_add",
                "both",
                "identical new member on both sides — record",
                group=group,
                member=member,
            )
        # A two-sided member with divergent sides lands in BOTH solo lists:
        # one framed item, not two duplicate rows.
        divergent_pairs = [m for m in de_solo if m in en_solo]
        for member in divergent_pairs:
            de_solo.remove(member)
            en_solo.remove(member)
            self.emit(
                member.key.render(),
                "conflict",
                "conflict_shared",
                "both",
                "new shared member differs between the sides",
                group=group,
                member=member,
            )
        if de_solo and en_solo:
            for member in de_solo + en_solo:
                side: Lang = "de" if member in de_solo else "en"
                self.emit(
                    member.key.render(),
                    "conflict",
                    "ambiguous_alignment",
                    "both",
                    "both sides added different content into the same positional pool — "
                    "align manually (minting ids resolves this permanently)",
                    group=group,
                    side=side,
                    member=member,
                )
            return
        for member in de_solo + en_solo:
            side = "de" if member in de_solo else "en"
            if localized_pool:
                self.emit(
                    member.key.render(),
                    "add",
                    "translate_new",
                    "de_to_en" if side == "de" else "en_to_de",
                    f"new header line on the {side} side — adapt for the twin",
                    group=group,
                    side=side,
                    member=member,
                )
            else:
                self.emit(
                    member.key.render(),
                    "add",
                    "copy_new_shared",
                    "de_to_en" if side == "de" else "en_to_de",
                    f"new shared member on the {side} side — verbatim copy to the twin",
                    group=group,
                    side=side,
                    member=member,
                )

    # -- cross-group moves ---------------------------------------------------------

    def _diff_cross_group_moves(self, id_members: list[tuple[Member, str]]) -> None:
        """A member whose two sides live under *different* group anchors.

        Global by-id pairing deliberately keeps a mid-move member one member
        (P2), so the parse records no divergence — the differ derives each
        side's physical group by bracketing the cell index between anchor
        indexes and compares against that side's OWN base group. A side that
        moved off its base placement → mechanical order mirror; both moved
        differently → decision; a split already carried at base (or forced
        by a one-sided anchor) is never reported as a fresh move.
        """
        assert self.base is not None
        anchor_index: dict[Lang, list[tuple[int, str]]] = {"de": [], "en": []}
        two_sided_anchor: dict[str, bool] = {}
        for group in self.current.groups:
            if group.anchor is None:
                continue
            two_sided_anchor[group.anchor_id] = (
                group.anchor.de is not None and group.anchor.en is not None
            )
            for lang in _SIDES:
                cell = group.anchor.side(lang)
                if cell is not None and cell.part == "deck":
                    anchor_index[lang].append((cell.index, group.anchor_id))
        for lang in _SIDES:
            anchor_index[lang].sort()

        def group_of(lang: Lang, cell: SideCell) -> str | None:
            token = None
            for idx, gid in anchor_index[lang]:
                if idx <= cell.index:
                    token = gid
                else:
                    break
            return token

        base_group_of: dict[tuple[Lang, str], str] = {}
        for (lang, base_token, part), handles in self.base.member_order.items():
            if part != "deck":
                continue
            for h in handles:
                base_group_of[(lang, self.key_migrations.get(h, h))] = self.group_map.get(
                    base_token, base_token
                )

        for member, _group in id_members:
            de, en = member.de, member.en
            if de is None or en is None or de.part != "deck" or en.part != "deck":
                continue
            if member.role in ("slide", "subslide") or member.key.value in {
                g.anchor_id for g in self.current.groups
            }:
                continue  # anchors define groups
            de_group = group_of("de", de)
            en_group = group_of("en", en)
            if de_group == en_group:
                continue
            if not (
                de_group
                and en_group
                and two_sided_anchor.get(de_group)
                and two_sided_anchor.get(en_group)
            ):
                # A one-sided anchor forces the twin's cells under another
                # bracket — placement there is an artifact, not a choice
                # (the group's own add/remove items carry the real work).
                continue
            handle = member.key.render()
            base_de = base_group_of.get(("de", handle))
            base_en = base_group_of.get(("en", handle))
            moved_de = base_de is not None and de_group != base_de
            moved_en = base_en is not None and en_group != base_en
            if not moved_de and not moved_en:
                if base_de is not None and base_en is not None:
                    self.emit(
                        handle,
                        "order",
                        "order_decision",
                        "none",
                        f"the sides place this member under different groups and "
                        f"already did at base (de: {de_group!r}, en: {en_group!r}) — "
                        f"carried divergent placement, decide",
                        member=member,
                    )
                continue
            if moved_de != moved_en:
                moved: Lang = "de" if moved_de else "en"
                self.emit(
                    handle,
                    "order",
                    "mirror_order",
                    "de_to_en" if moved == "de" else "en_to_de",
                    f"member moved to group "
                    f"{de_group if moved == 'de' else en_group!r} on the {moved} side "
                    f"only — mirror the move to the twin",
                    group=de_group if moved == "de" else en_group,
                    side=moved,
                    member=member,
                )
            else:
                self.emit(
                    handle,
                    "order",
                    "order_decision",
                    "both",
                    f"the sides moved this member under different groups "
                    f"(de: {de_group!r}, en: {en_group!r}) — decide",
                    member=member,
                )

    # -- order (§6.2) -----------------------------------------------------------------

    def _diff_order(self) -> None:
        assert self.base is not None
        self._diff_group_order()
        current_orders = self._current_member_orders()
        scopes = {
            (self.group_map.get(group, group), part)
            for (_lang, group, part) in self.base.member_order
        }
        for group, part in sorted(scopes):
            base_of: dict[Lang, list[str]] = {}
            cur_of: dict[Lang, list[str]] = {}
            for lang in _SIDES:
                base_token = self._base_group_for(group)
                base_seq = self.base.member_order.get((lang, base_token, part), [])
                base_of[lang] = [self.key_migrations.get(h, h) for h in base_seq]
                cur_of[lang] = current_orders.get((lang, group, part), [])
            self._compare_order(group, part, base_of, cur_of)

    def _current_member_orders(self) -> dict[tuple[Lang, str, str], list[str]]:
        orders: dict[tuple[Lang, str, str], list[tuple[int, str]]] = {}
        for member, group in _iter_with_groups(self.current):
            if member.key.scheme != "id":
                continue  # pos handles alias across states (ordinal renumbering)
            token = _member_group_token(member, group)
            for lang in _SIDES:
                cell = member.side(lang)
                if cell is None:
                    continue
                orders.setdefault((lang, token, cell.part), []).append(
                    (cell.index, member.key.render())
                )
        return {key: [handle for _, handle in sorted(entries)] for key, entries in orders.items()}

    def _compare_order(
        self,
        group: str,
        part: str,
        base_of: dict[Lang, list[str]],
        cur_of: dict[Lang, list[str]],
    ) -> None:
        """Order divergence, judged per side against that side's OWN base.

        A merged base sequence would be DE-biased: a bundle whose sides
        already disagreed about order at base must diff clean-of-mechanics
        (a carried divergence is a framed decision, never a fresh
        one-sided reorder with an arbitrary direction).
        """
        common_set = set(base_of["de"]) & set(base_of["en"]) & set(cur_of["de"]) & set(cur_of["en"])
        if len(common_set) < 2:
            return
        base_orders = {lang: [h for h in base_of[lang] if h in common_set] for lang in _SIDES}
        cur_orders = {lang: [h for h in cur_of[lang] if h in common_set] for lang in _SIDES}
        moved = {lang: cur_orders[lang] != base_orders[lang] for lang in _SIDES}
        handle = MemberKey.positional(group, f"order.{part}", 0).render()
        if not moved["de"] and not moved["en"]:
            if cur_orders["de"] != cur_orders["en"]:
                self.emit(
                    handle,
                    "order",
                    "order_decision",
                    "none",
                    f"the sides order group {group!r} differently and already did "
                    f"at base — carried divergence, decide",
                    group=group,
                )
            return
        if moved["de"] and moved["en"]:
            action = "record_order" if cur_orders["de"] == cur_orders["en"] else "order_decision"
            self.emit(
                handle,
                "order",
                action,
                "both",
                f"members of group {group!r} reordered on both sides "
                f"(de: {cur_orders['de']}, en: {cur_orders['en']})",
                group=group,
            )
            return
        moved_side: Lang = "de" if moved["de"] else "en"
        if cur_orders["de"] == cur_orders["en"]:
            # The moved side converged on the twin's order: record, don't mirror.
            self.emit(
                handle,
                "order",
                "record_order",
                "both",
                f"the {moved_side} side aligned group {group!r} to the twin's order — record",
                group=group,
            )
            return
        self.emit(
            handle,
            "order",
            "mirror_order",
            "de_to_en" if moved_side == "de" else "en_to_de",
            f"members of group {group!r} reordered on the {moved_side} side — "
            f"mirror the new order to the twin",
            group=group,
            side=moved_side,
        )

    def _diff_group_order(self) -> None:
        assert self.base is not None
        base_of = {
            lang: [self.group_map.get(g, g) for g in self.base.group_order_by_side.get(lang, [])]
            for lang in _SIDES
        }
        cur_of: dict[Lang, list[str]] = {}
        current_by_side: dict[Lang, list[tuple[int, str]]] = {"de": [], "en": []}
        for group in self.current.groups:
            anchor = group.anchor
            if anchor is None:
                continue
            for lang in _SIDES:
                cell = anchor.side(lang)
                if cell is not None and cell.part == "deck":
                    current_by_side[lang].append((cell.index, group.anchor_id))
        for lang in _SIDES:
            cur_of[lang] = [gid for _, gid in sorted(current_by_side[lang])]
        self._compare_order("~groups", "deck", base_of, cur_of)

    # -- preambles ------------------------------------------------------------------

    def _diff_preambles(self) -> None:
        assert self.base is not None
        current = {
            ("de", "deck"): self.current.de_deck_preamble,
            ("en", "deck"): self.current.en_deck_preamble,
            ("de", "companion"): self.current.de_companion_preamble,
            ("en", "companion"): self.current.en_companion_preamble,
        }
        for part in ("deck", "companion"):
            de_lines = current[("de", part)]
            en_lines = current[("en", part)]
            if de_lines is None or en_lines is None:
                continue  # companion file existence is layout state, not preamble
            base_de = self.base.preamble_fps.get(("de", part))
            base_en = self.base.preamble_fps.get(("en", part))
            if base_de is None and base_en is None:
                continue  # file creation is layout state, itemized per member
            de_fp = _lines_fp(de_lines)
            en_fp = _lines_fp(en_lines)
            moved_de = base_de is not None and de_fp != base_de
            moved_en = base_en is not None and en_fp != base_en
            handle = MemberKey.positional("~preamble", part, 0).render()
            if not moved_de and not moved_en:
                if de_fp != en_fp and base_de != base_en:
                    self.emit(
                        handle,
                        "conflict",
                        "pending_divergence",
                        "none",
                        f"{part} preambles differ and neither moved off base — "
                        f"in-flight divergence carried at the baseline",
                    )
                continue
            if de_fp == en_fp:
                self.emit(
                    handle,
                    "mechanical",
                    "record_preamble",
                    "both",
                    f"{part} preambles changed identically on both sides — record",
                )
                continue
            if moved_de and moved_en:
                self.emit(
                    handle,
                    "conflict",
                    "conflict_preamble",
                    "both",
                    f"{part} preambles moved differently on both sides",
                )
                continue
            moved: Lang = "de" if moved_de else "en"
            self.emit(
                handle,
                "mechanical",
                "propagate_preamble",
                "de_to_en" if moved_de else "en_to_de",
                f"{part} preamble changed on the {moved} side — copy to the twin "
                f"(review language-specific header fields before applying)",
                side=moved,
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def diff_deck(current: BilingualDeck, base: DeckBaseline | None) -> DeckDiff:
    """Diff a parsed deck against its recorded baseline (design §6).

    ``base=None`` is the fully cold state: every member becomes an
    ``unverified`` framed verification item.
    """
    return _Differ(current, base).run()


def diff_outcome(outcome: ParseOutcome, base: DeckBaseline | None) -> DeckDiff:
    """Diff a :func:`~clm.slides.doc_lenses.parse_bundle` outcome.

    A normalize refusal yields a :class:`DeckDiff` carrying the framed
    refusal (design §3.4: one "run normalize" item per deck) — never an
    exception, never a partial diff.
    """
    if outcome.refusal is not None:
        return DeckDiff(refusal=outcome.refusal)
    assert outcome.deck is not None
    return diff_deck(outcome.deck, base)
