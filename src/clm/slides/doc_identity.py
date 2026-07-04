"""Sync-free identity and snapshot utilities for the v3 deck model (#546 Phase 1).

The fingerprint functions and the structural baseline snapshot were born
inside the v3 differ (:mod:`clm.slides.sync_diff`) but are not diff logic:
they define *what a member's identity and recorded state are*, which every
consumer of the :class:`~clm.slides.bilingual_doc.BilingualDeck` model that
mutates or records deck state needs — the sync ledger, the apply executor,
and the ``clm harvest`` toolkit. This module carves them out so those
consumers can import identity without pulling in the differ.

Contents:

* :func:`content_fingerprint` / :func:`pair_signature` /
  :func:`body_fingerprint` / :func:`lines_fingerprint` — the hashing forms
  the ledger records (versioned by
  :data:`~clm.slides.doc_ledger.LEDGER_HASH_VERSION`).
* :class:`MemberBaseline` / :class:`DeckBaseline` — one member's / one
  deck's recorded state (design §5 / §6.1).
* :func:`iter_with_groups` / :func:`member_group_token` — the canonical
  walk over a deck's unified member stream with each member's group token.
* :func:`baseline_from_deck` — snapshot a parsed deck as a complete
  baseline.

This module is pure (no I/O, no git) and must not import from the v2 sync
core (``sync_plan`` / ``sync_apply`` / ``sync_code``) **nor from the v3
differ** (``sync_diff``) — enforced by the import-cleanliness tests
(design §12.5).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator

from attrs import define, field, frozen

from clm.slides.bilingual_doc import (
    HEADER_GROUP,
    ORPHAN_GROUP,
    BilingualDeck,
    Lang,
    Member,
    SideCell,
)
from clm.slides.doc_lenses import _lines_sans_id

__all__ = [
    "DeckBaseline",
    "MemberBaseline",
    "baseline_from_deck",
    "body_fingerprint",
    "content_fingerprint",
    "iter_with_groups",
    "lines_fingerprint",
    "member_group_token",
    "pair_signature",
]

_SIDES: tuple[Lang, Lang] = ("de", "en")


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


def pair_signature(cell: SideCell) -> str:
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


def body_fingerprint(cell: SideCell) -> str:
    return hashlib.sha256(cell.body.encode("utf-8")).hexdigest()


def lines_fingerprint(lines: tuple[str, ...] | None) -> str | None:
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
    de_sig: str | None = None  # owner-free signature (see pair_signature)
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


def member_group_token(member: Member, owner_group: str) -> str:
    if member.key.scheme == "pos":
        # rsplit: a group token may itself contain "/" (ids are free-form)
        return member.key.value.rsplit("/", 2)[0]
    return owner_group


def iter_with_groups(deck: BilingualDeck) -> Iterator[tuple[Member, str]]:
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
    for member, group_token in iter_with_groups(deck):
        entry = MemberBaseline(
            key=member.key.render(),
            langness=member.langness,
            layout=member.layout,
            kind=member.kind,
            role=member.role,
            owner=member.owner.render() if member.owner else None,
            de_fp=content_fingerprint(member.de) if member.de else None,
            en_fp=content_fingerprint(member.en) if member.en else None,
            de_body_fp=body_fingerprint(member.de) if member.de else None,
            en_body_fp=body_fingerprint(member.en) if member.en else None,
            de_tags=member.de.tags if member.de else None,
            en_tags=member.en.tags if member.en else None,
            de_sig=pair_signature(member.de) if member.de else None,
            en_sig=pair_signature(member.en) if member.en else None,
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
        ("de", "deck"): lines_fingerprint(deck.de_deck_preamble),
        ("en", "deck"): lines_fingerprint(deck.en_deck_preamble),
        ("de", "companion"): lines_fingerprint(deck.de_companion_preamble),
        ("en", "companion"): lines_fingerprint(deck.en_companion_preamble),
    }
    return base
