"""The canonical bilingual document model for sync v3 (#520 Phase 1).

One deck is *one* :class:`BilingualDeck`, and the up-to-four on-disk files
(``slides_x.de.py`` / ``slides_x.en.py`` plus the optional
``voiceover_x.de.py`` / ``voiceover_x.en.py`` companions) are lens
projections of it — the ``split``/``unify`` discipline lifted from the text
layer to a parsed model, per
``docs/claude/design/sync-total-identity-document-model.md`` §3–§4.

The load-bearing rules (design §2):

* **P1 — identity is total.** Every logical cell has exactly one
  :class:`MemberKey`, computed once at parse time by the §3.3 rule:
  ``id:<slide_id>`` when the cell carries a slide id, else
  ``pos:<group>/<kind>/<i>`` (owning group's anchor id, kind-class, ordinal
  among the group's id-less members of that kind-class).
* **P2 — identity is invariant under every mutable attribute.** Lang-ness,
  tags, content, and layout (inline/companion) are member *state*, never
  identity-regime selectors.
* **P4 — one document, N projections.** A member stores its verbatim
  per-side cells (:class:`SideCell`); projection re-emits those bytes and
  never regenerates headers, so ``project ∘ parse`` is byte-identity by
  construction wherever parsing assigned every input cell to exactly one
  member.

The model deliberately stores *per-side* content even for ``shared``
members: a shared member whose two projections have diverged (an in-flight
edit) is recorded as an :class:`Observation` — first-class evidence for the
Phase 2 differ — while the bytes of both sides remain reproducible.

This module is a pure data model: no I/O, no imports from the v2 sync core
(``sync_plan`` / ``sync_apply`` / ``sync_code`` — enforced by the
import-cleanliness test, design §12.5). Parsing and projection live in
:mod:`clm.slides.doc_lenses`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from attrs import define, field, frozen

__all__ = [
    "HEADER_GROUP",
    "ORPHAN_GROUP",
    "PREFACE_GROUP",
    "BilingualDeck",
    "Member",
    "MemberKey",
    "NormalizeRefusal",
    "Observation",
    "ParseOutcome",
    "RefusalReason",
    "SideCell",
    "SlideGroup",
]

Lang = Literal["de", "en"]
Part = Literal["deck", "companion"]

# Group tokens for positional keys outside anchored slide groups. ``~`` is not
# a valid slug character, so these can never collide with a real slide id.
HEADER_GROUP = "~header"
PREFACE_GROUP = "~preface"
ORPHAN_GROUP = "~orphan"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@frozen
class MemberKey:
    """The one identity of a logical cell (design §3.3).

    ``scheme == "id"``: ``value`` is the bare slide id (preserve marker ``!``
    stripped). ``scheme == "pos"``: ``value`` is
    ``<group>/<kind>/<ordinal>`` where ``group`` is the owning group's bare
    anchor id (or :data:`HEADER_GROUP` / :data:`PREFACE_GROUP`), ``kind`` the
    cell kind-class (``markdown`` / ``code`` / ``j2``), and ``ordinal`` the
    0-based position among the group's id-less members of that kind-class in
    merged document order.
    """

    scheme: Literal["id", "pos"]
    value: str

    @classmethod
    def for_id(cls, slide_id: str) -> MemberKey:
        return cls(scheme="id", value=slide_id)

    @classmethod
    def positional(cls, group: str, kind: str, ordinal: int) -> MemberKey:
        return cls(scheme="pos", value=f"{group}/{kind}/{ordinal}")

    def render(self) -> str:
        """The canonical string handle (``id:intro`` / ``pos:title/j2/0``)."""
        return f"{self.scheme}:{self.value}"

    @classmethod
    def parse(cls, text: str) -> MemberKey:
        scheme, sep, value = text.partition(":")
        if not sep or scheme not in ("id", "pos") or not value:
            raise ValueError(f"not a MemberKey: {text!r}")
        return cls(scheme=scheme, value=value)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@frozen
class SideCell:
    """One side's verbatim cell — the byte-identity carrier.

    ``lines`` is the raw line list of the percent-format cell (header line
    first, trailing blank separator lines included), exactly as split from
    the source file. ``index`` is the cell's 0-based ordinal within its
    source file's cell stream; projection re-emits cells sorted by it.
    """

    lines: tuple[str, ...]
    index: int
    line_number: int  # 1-based line of the header in the source file
    part: Part
    lang_attr: str | None  # the cell's lang="…" attribute, verbatim
    tags: tuple[str, ...]
    slide_id: str | None  # verbatim, may carry the ``!`` preserve marker
    for_slide: str | None
    vo_anchor: str | None
    cell_type: str  # markdown | code | j2

    @property
    def header(self) -> str:
        return self.lines[0]

    @property
    def body(self) -> str:
        return "\n".join(self.lines[1:])


@define
class Member:
    """One logical cell of the bilingual document (design §3.1).

    A shared member normally appears byte-identically on both sides; a
    localized member appears as its ``lang="de"`` variant in the DE
    projection and its ``lang="en"`` variant in the EN projection; a
    ``layout == "companion"`` member appears in the ``voiceover_*`` files.
    A missing side (``de``/``en`` is ``None``) is a pending state, recorded
    as an observation, never an error.
    """

    key: MemberKey
    kind: str  # markdown | code | j2
    role: str  # header | slide | subslide | voiceover | notes | code | aux
    langness: str  # shared | localized
    layout: str  # inline | companion
    owner: MemberKey | None  # owning slide anchor; serialized as for_slide
    de: SideCell | None
    en: SideCell | None

    @property
    def is_one_sided(self) -> bool:
        return (self.de is None) != (self.en is None)

    def side(self, lang: Lang) -> SideCell | None:
        return self.de if lang == "de" else self.en


@define
class SlideGroup:
    """An anchored slide group: the id'd anchor member plus everything until
    the next anchor (design §3.1).

    ``anchor`` is ``None`` only for the synthetic preface group (content
    before the first slide in a deck without a title macro). ``anchor_id``
    is the bare anchor id (``"title"`` for the title-macro group,
    :data:`PREFACE_GROUP` for the preface group).
    """

    anchor_id: str
    anchor: Member | None
    members: list[Member] = field(factory=list)

    def all_members(self) -> Iterator[Member]:
        if self.anchor is not None:
            yield self.anchor
        yield from self.members


# ---------------------------------------------------------------------------
# Observations and refusals
# ---------------------------------------------------------------------------

# Observation kinds (design §3.2 — recorded mismatches, never errors):
#   shared_divergence      shared member whose two sides differ byte-wise
#   one_sided_member       member present on one side only
#   one_sided_group        whole group present on one side only
#   id_stamp_pending_twin  id'd on one half, id-less positional twin (#443)
#   lang_attr_mismatch     paired sides disagree about lang-ness
#   member_kind_mismatch   paired sides disagree about the cell kind
#   wrong_language_cell    a cell whose lang attr contradicts its file side
#   preamble_divergence    DE/EN deck preambles differ
#   layout_mixed           one half has inline voiceover AND a companion
#   layout_cross_language  the halves use different voiceover layouts
#   owner_missing          companion member's for_slide matches no anchor
#   owner_mismatch         paired companion sides name different owners
#   group_order_divergence paired groups appear in different order per side
#   unexpected_companion_cell  non-narrative cell in a companion file
ObservationKind = str


@frozen
class Observation:
    """A first-class recorded mismatch on the document (design §3.2)."""

    kind: ObservationKind
    member: MemberKey | None = None
    side: Lang | None = None
    detail: str = ""


@frozen
class RefusalReason:
    """One reason a bundle failed the §3.4 normalize precondition."""

    # duplicate_id | idless_anchor | idless_localized | idless_narrative
    # | legacy_title_companion
    code: str
    detail: str
    member: MemberKey | None = None


@define
class NormalizeRefusal:
    """A framed "run normalize first" refusal for a whole deck (design §3.2).

    Every offending condition is enumerated (never first-error-only), so one
    normalize pass can fix them all.
    """

    reasons: list[RefusalReason] = field(factory=list)

    def render(self) -> str:
        lines = ["deck is not normalized — run `clm slides normalize --stamp-ids` first:"]
        lines += [f"  - [{r.code}] {r.detail}" for r in self.reasons]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


@define
class BilingualDeck:
    """The canonical parsed document over the ≤4-file bundle (design §3.1).

    ``*_preamble`` fields hold the verbatim lines before the first cell
    boundary of each source file; a ``None`` companion preamble means that
    companion file was absent from the bundle (an empty present file is an
    empty tuple). ``header`` holds the per-language j2/header members that
    precede the title group. ``orphans`` holds companion members whose
    ``for_slide`` matched no anchor — kept so projection never drops them.
    """

    comment_token: str
    de_deck_preamble: tuple[str, ...]
    en_deck_preamble: tuple[str, ...]
    de_companion_preamble: tuple[str, ...] | None
    en_companion_preamble: tuple[str, ...] | None
    header: list[Member] = field(factory=list)
    groups: list[SlideGroup] = field(factory=list)
    orphans: list[Member] = field(factory=list)
    observations: list[Observation] = field(factory=list)

    def members(self) -> Iterator[Member]:
        """Every member in document order (header, groups, orphans)."""
        yield from self.header
        for group in self.groups:
            yield from group.all_members()
        yield from self.orphans

    def member_by_key(self, key: MemberKey) -> Member | None:
        for member in self.members():
            if member.key == key:
                return member
        return None

    def has_companion(self, lang: Lang) -> bool:
        preamble = self.de_companion_preamble if lang == "de" else self.en_companion_preamble
        return preamble is not None


@define
class ParseOutcome:
    """Result of :func:`clm.slides.doc_lenses.parse_bundle`.

    Exactly one of ``deck`` / ``refusal`` is set. A refusal is the framed
    §3.4 "normalize first" outcome — never an exception, never a degraded
    heuristic parse.
    """

    deck: BilingualDeck | None = None
    refusal: NormalizeRefusal | None = None

    @property
    def ok(self) -> bool:
        return self.deck is not None
