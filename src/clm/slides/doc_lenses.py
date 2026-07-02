"""Lens projections between the ≤4-file bundle and :class:`BilingualDeck`.

Sync v3 Phase 1 (#520): ``parse_bundle`` reads the two deck halves plus the
optional voiceover companions into one
:class:`~clm.slides.bilingual_doc.BilingualDeck`; ``project`` renders one
file's text back out of the model. The round-trip laws (design §4,
property-tested in ``tests/slides/test_doc_lenses.py``)::

    project(parse_bundle(...).deck, lang, part) == input text   # byte-identity
    parse_bundle(*(project(deck, ...) for each file)) == deck   # identity

Byte-identity is achieved structurally: every input cell is assigned to
exactly one member side, which stores the cell's verbatim lines, and
projection re-emits the sides of one ``(lang, part)`` sorted by their source
ordinal. Parsing never rewrites a header, never re-anchors a narrative, and
never mints an id — the §3.4 one-time normalization owns id stamping, which
is precisely what makes the round trip lossless (the #501 lesson: extract's
id minting is why ``inline ∘ extract`` was never byte-identity).

Mismatches between the projections (a one-sided member, a diverged shared
cell, the #443 id'd-on-one-half shape) are recorded as first-class
:class:`~clm.slides.bilingual_doc.Observation`\\ s on the document (design
§3.2). Input that fails the §3.4 normalize precondition — duplicate ids,
id-less anchors, id-less localized or narrative cells on both halves —
yields a framed :class:`~clm.slides.bilingual_doc.NormalizeRefusal` for the
whole deck: never an exception, never a degraded heuristic parse. Every
offending condition is enumerated, never just the first.

Parsing runs in strict phases so a cell can never land in two members:

1. read + segment each file (header zone / title group / anchored groups),
2. pair — globally by bare id, then positionally (rule 2) within each
   paired region, per kind-class,
3. emit — walk each region in merged order; an EN cell that is some DE
   cell's partner is always emitted by that pair, never solo.

This module must not import from the v2 sync core (``sync_plan`` /
``sync_apply`` / ``sync_code``) — enforced by the import-cleanliness test
(design §12.5).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from attrs import define, field

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.bilingual_doc import (
    HEADER_GROUP,
    ORPHAN_GROUP,
    PREFACE_GROUP,
    BilingualDeck,
    Lang,
    Member,
    MemberKey,
    NormalizeRefusal,
    Observation,
    ParseOutcome,
    Part,
    RefusalReason,
    SideCell,
    SlideGroup,
)
from clm.slides.pairing import (
    TITLE_SLIDE_ID,
    derive_split_pair,
    derive_split_pair_from_stem,
    is_title_macro_cell,
    order_split_pair,
)
from clm.slides.raw_cells import RawCell, split_cells
from clm.slides.slug import strip_preserve_marker

# The single vo_anchor read path (the attribute is not part of CellMetadata);
# importing the private helper rather than copying its regex keeps the two
# from drifting — the same pattern voiceover_tools itself uses on normalizer.
from clm.slides.voiceover_tools import _parse_vo_anchor, resolve_companion

logger = logging.getLogger(__name__)

__all__ = [
    "DocLensError",
    "LoadedBundle",
    "load_bundle",
    "parse_bundle",
    "project",
]

LANGS: tuple[Lang, ...] = ("de", "en")
PARTS: tuple[Part, ...] = ("deck", "companion")

# A cell reference within one language side: (part, cell index in that file).
_Ref = tuple[Part, int]


class DocLensError(Exception):
    """An internal lens invariant was violated (a bug, not bad input)."""


# ---------------------------------------------------------------------------
# Reading one source file
# ---------------------------------------------------------------------------


@define
class _Source:
    """One source file, losslessly split: verbatim preamble lines + cells."""

    lang: Lang
    part: Part
    preamble_lines: tuple[str, ...]
    raw: list[RawCell]
    cells: list[SideCell]


def _read_source(text: str, lang: Lang, part: Part, comment_token: str) -> _Source:
    """Split one file into verbatim preamble lines and :class:`SideCell`\\ s.

    The preamble is kept as a *line list* rather than the joined string that
    :func:`clm.slides.raw_cells.split_cells` returns, so a file whose first
    line is blank (preamble ``""``) survives the round trip —
    ``reconstruct`` drops a falsy preamble, this lens must not.
    """
    lines = text.split("\n")
    _, raw = split_cells(text, comment_token)
    if raw:
        preamble_lines = tuple(lines[: raw[0].line_number - 1])
    else:
        preamble_lines = tuple(lines)
    cells = [
        SideCell(
            lines=tuple(cell.lines),
            index=i,
            line_number=cell.line_number,
            part=part,
            lang_attr=cell.metadata.lang,
            tags=tuple(cell.metadata.tags),
            slide_id=cell.metadata.slide_id,
            for_slide=cell.metadata.for_slide,
            vo_anchor=_parse_vo_anchor(cell.header),
            cell_type=cell.metadata.cell_type,
        )
        for i, cell in enumerate(raw)
    ]
    return _Source(lang=lang, part=part, preamble_lines=preamble_lines, raw=raw, cells=cells)


# ---------------------------------------------------------------------------
# Per-side deck segmentation (header zone, title group, anchored groups)
# ---------------------------------------------------------------------------


@define
class _GroupSeg:
    """One side's slice of a slide group: anchor cell index + member indices."""

    group_id: str  # bare anchor id; TITLE_SLIDE_ID / PREFACE_GROUP for specials
    anchor_idx: int | None  # None only for the preface group
    member_idxs: list[int] = field(factory=list)


@define
class _DeckSeg:
    """One deck half segmented into header zone and groups, by cell index."""

    header_idxs: list[int] = field(factory=list)
    groups: list[_GroupSeg] = field(factory=list)


# The slide_id attribute, for parity checks that must ignore it (the same
# strip form assign_ids._SLIDE_ID_RE uses when rewriting ids).
_SLIDE_ID_ATTR_RE = re.compile(r'\s*slide_id="[^"]*"')


def _lines_sans_id(cell: SideCell) -> tuple[str, ...]:
    """The cell's verbatim lines with the header's slide_id attribute removed.

    Shared-member byte parity is judged modulo the id attribute: a shared
    pair differing *only* by a one-sided id stamp is the #443 transition
    (observed as ``id_stamp_pending_twin``), not a content divergence —
    reporting both would be noise.
    """
    return (_SLIDE_ID_ATTR_RE.sub("", cell.lines[0]), *cell.lines[1:])


def _bare(slide_id: str | None) -> str | None:
    """The bare id, or ``None`` for absent, empty, or marker-only ids.

    ``slide_id=""`` and ``slide_id="!"`` carry no usable identity — treating
    them as id'd would bypass the id-less refusals and mint empty keys.
    """
    if not slide_id:
        return None
    return strip_preserve_marker(slide_id) or None


def _segment_deck(source: _Source) -> _DeckSeg:
    """Segment one deck half into header zone / title group / anchored groups.

    The header zone is everything before the title macro (the j2 import line
    and friends). The title macro anchors the ``title`` group; every
    ``is_slide_start`` cell anchors its own group. Without a title macro,
    cells before the first slide form the synthetic preface group. Id-less
    anchors are refused before segmentation runs (phase 1), so every real
    group here has a usable id.
    """
    raw = source.raw
    first_slide = next((i for i, c in enumerate(raw) if c.metadata.is_slide_start), len(raw))
    title_idx = next(
        (i for i, c in enumerate(raw[:first_slide]) if is_title_macro_cell(c)),
        None,
    )
    seg = _DeckSeg()
    current: _GroupSeg | None = None
    for i, cell in enumerate(raw):
        if i == title_idx:
            current = _GroupSeg(group_id=TITLE_SLIDE_ID, anchor_idx=i)
            seg.groups.append(current)
            continue
        if cell.metadata.is_slide_start:
            group_id = _bare(cell.metadata.slide_id) or f"~idless@{cell.line_number}"
            current = _GroupSeg(group_id=group_id, anchor_idx=i)
            seg.groups.append(current)
            continue
        if current is not None:
            current.member_idxs.append(i)
        elif title_idx is not None:
            # Before the title macro: the header zone.
            seg.header_idxs.append(i)
        else:
            # No title macro: cells before the first slide form the preface.
            current = _GroupSeg(group_id=PREFACE_GROUP, anchor_idx=None)
            seg.groups.append(current)
            current.member_idxs.append(i)
    return seg


# ---------------------------------------------------------------------------
# Pairing helpers
# ---------------------------------------------------------------------------


def _pair_class(cell: SideCell) -> tuple[str, bool, str]:
    """The kind-class used for positional (rule 2) pairing of id-less cells.

    ``(cell_type, has-lang-attr, narrative-role)`` — finer than the key's
    kind so a shared cell never pairs with a localized one, while the
    rendered pos-key still uses the plain cell kind (design §3.3).
    """
    role = "voiceover" if "voiceover" in cell.tags else "notes" if "notes" in cell.tags else ""
    return (cell.cell_type, cell.lang_attr is not None, role)


def _companion_pool_key(cell: SideCell) -> tuple[str | None, str, bool, str]:
    """Positional pool key for companion cells: adds the bare ``for_slide``.

    Mirrors the shape rule of ``assign_ids.stamp_ids_in_companion_pair`` but
    deliberately without ``vo_anchor``: pairing wants recall (a drifted
    anchor is member state to report, not a reason to split the member),
    stamping wanted precision.
    """
    kind, localized, role = _pair_class(cell)
    return (_bare(cell.for_slide), kind, localized, role)


def _merged_order(
    de_items: list[int],
    en_items: list[int],
    pair_de_to_en: dict[int, int],
) -> list[tuple[int | None, int | None]]:
    """Merge two per-side index sequences into one document order.

    ``pair_de_to_en`` maps paired DE items to their EN partner *within these
    sequences*. Paired items are sync points; between two sync points
    DE-only items come first (in DE order), then EN-only items (in EN order)
    — the deterministic analogue of ``unify_texts``'s cursor walk. Returns
    ``(de_item, en_item)`` tuples with ``None`` for the absent side.
    """
    paired_en = set(pair_de_to_en.values())
    en_pos = {item: i for i, item in enumerate(en_items)}
    out: list[tuple[int | None, int | None]] = []
    en_cursor = 0

    def emit_en_solos_before(limit: int) -> None:
        nonlocal en_cursor
        while en_cursor < limit:
            item = en_items[en_cursor]
            if item not in paired_en:
                out.append((None, item))
            en_cursor += 1

    for de_item in de_items:
        partner = pair_de_to_en.get(de_item)
        if partner is None:
            out.append((de_item, None))
            continue
        emit_en_solos_before(en_pos[partner])
        en_cursor = max(en_cursor, en_pos[partner] + 1)
        out.append((de_item, partner))
    emit_en_solos_before(len(en_items))
    return out


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


class _Parser:
    """One parse run over a bundle — collects members, observations, refusals."""

    def __init__(
        self,
        de_deck: _Source,
        en_deck: _Source,
        de_comp: _Source | None,
        en_comp: _Source | None,
        comment_token: str,
    ) -> None:
        self.de_deck = de_deck
        self.en_deck = en_deck
        self.de_comp = de_comp
        self.en_comp = en_comp
        self.comment_token = comment_token
        self.observations: list[Observation] = []
        self.refusals: list[RefusalReason] = []
        # DE cell -> EN cell pairing, across parts (one map per bundle).
        self.pairs: dict[_Ref, _Ref] = {}
        self.paired_en: set[_Ref] = set()
        # Member-keyed observations are recorded against the Member OBJECT and
        # materialized only after positional ordinals are assigned — otherwise
        # a frozen Observation would carry the pre-ordinal sentinel key and
        # never resolve via member_by_key (P1: identity carried unchanged).
        self._member_obs: list[tuple[str, Member, Lang | None, str]] = []

    # -- plumbing -------------------------------------------------------------

    def _source(self, lang: Lang, part: Part) -> _Source | None:
        if part == "deck":
            return self.de_deck if lang == "de" else self.en_deck
        return self.de_comp if lang == "de" else self.en_comp

    def _cell(self, lang: Lang, ref: _Ref) -> SideCell:
        source = self._source(lang, ref[0])
        if source is None:  # pragma: no cover - guarded by construction
            raise DocLensError(f"no {ref[0]} source on the {lang} side")
        return source.cells[ref[1]]

    def observe(
        self,
        kind: str,
        member: MemberKey | None = None,
        side: Lang | None = None,
        detail: str = "",
    ) -> None:
        self.observations.append(Observation(kind=kind, member=member, side=side, detail=detail))

    def refuse(self, code: str, detail: str, member: MemberKey | None = None) -> None:
        self.refusals.append(RefusalReason(code=code, detail=detail, member=member))

    def observe_member(
        self,
        kind: str,
        member: Member,
        side: Lang | None = None,
        detail: str = "",
    ) -> None:
        """Record an observation about ``member``, keyed once keys are final."""
        self._member_obs.append((kind, member, side, detail))

    def _materialize_member_observations(self) -> None:
        for kind, member, side, detail in self._member_obs:
            self.observe(kind, member=member.key, side=side, detail=detail)
        self._member_obs.clear()

    # -- phase 1: keying preconditions ------------------------------------------

    def check_keying(self) -> None:
        """Enumerate duplicate ids and id-less anchors (design §3.3).

        Ids must be unique per *side* across the deck half and its companion
        (the ≤4-file namespace of #527/#528): one id names one member, whose
        DE and EN sides are the only sanctioned repetition. The legacy
        inherited-id narrative (``slide_id`` equal to the owning slide's) is
        exactly such a duplicate — a normalize-first refusal, per §12.1.

        Keying refusals return *before* pairing, so the §3.4 conditions that
        need pairing to judge (id-less localized/narrative vs the #443
        transition) are not enumerated alongside them — rule-2 resolution is
        meaningless without unique keys. That costs no extra author round
        trip: ``normalize --stamp-ids`` repairs both classes in one pass.
        """
        for lang in LANGS:
            counts: Counter[str] = Counter()
            where: dict[str, list[str]] = {}
            for part in PARTS:
                source = self._source(lang, part)
                if source is None:
                    continue
                for cell in source.cells:
                    bare = _bare(cell.slide_id)
                    if bare is None:
                        continue
                    counts[bare] += 1
                    where.setdefault(bare, []).append(f"{part}.{lang} line {cell.line_number}")
            for bare, n in sorted(counts.items()):
                if n > 1:
                    self.refuse(
                        "duplicate_id",
                        f'slide_id "{bare}" appears {n} times on the {lang} side '
                        f"({', '.join(where[bare])})",
                        member=MemberKey.for_id(bare),
                    )
        for lang, deck in (("de", self.de_deck), ("en", self.en_deck)):
            for cell in deck.cells:
                if ("slide" in cell.tags or "subslide" in cell.tags) and _bare(
                    cell.slide_id
                ) is None:
                    self.refuse(
                        "idless_anchor",
                        f"slide-start cell without a usable slide_id "
                        f"(deck.{lang} line {cell.line_number})",
                    )

    # -- phase 2: pairing --------------------------------------------------------

    def pair_by_id(self) -> None:
        """Global by-id pairing over each side's deck + companion cells.

        Anchors (slide starts and the title macro) are excluded — they pair
        via group pairing. Pairing across parts and across groups is allowed
        on purpose: a member mid-relayout (inline on one half, companion on
        the other) or mid-move is still *one* member (design §7.3 / P2).
        """
        by_id: dict[Lang, dict[str, _Ref]] = {"de": {}, "en": {}}
        for lang in LANGS:
            for part in PARTS:
                source = self._source(lang, part)
                if source is None:
                    continue
                for i, raw in enumerate(source.raw):
                    if part == "deck" and self._is_anchor(raw):
                        continue
                    bare = _bare(source.cells[i].slide_id)
                    if bare is not None:
                        by_id[lang][bare] = (part, i)
        for bare, de_ref in by_id["de"].items():
            en_ref = by_id["en"].get(bare)
            if en_ref is not None:
                self.pairs[de_ref] = en_ref

    @staticmethod
    def _is_anchor(raw: RawCell) -> bool:
        return raw.metadata.is_slide_start or is_title_macro_cell(raw)

    def _unpaired(self, lang: Lang, part: Part, idxs: list[int]) -> list[int]:
        if lang == "de":
            return [i for i in idxs if (part, i) not in self.pairs]
        taken = set(self.pairs.values())
        return [i for i in idxs if (part, i) not in taken]

    def pair_positionally(self, de_idxs: list[int], en_idxs: list[int], part: Part) -> None:
        """Rule-2 pairing of one kind-class pool of leftover cells.

        Two cursors walk the pools slot by slot, so an id'd-on-one-half cell
        *occupies its positional slot* rather than being pushed behind the
        id-less residue (which would shift every later sibling onto the
        wrong twin — the interleaved-#443 mis-adoption):

        - id-less × id-less: the steady-state positional pair.
        - exactly one side id'd: the #443 id-stamp-pending shape — adopted
          when the id-less twin is localized (lang-tagged, where cross-side
          bodies differ by nature) or, for shared cells, when the bodies are
          byte-equal (an id stamped onto one half of an unchanged cell). A
          shared-class id'd cell with a *different* body is a genuinely new
          one-sided member: only its own side's cursor advances, so the
          remaining id-less cells still align.
        - both sides id'd (necessarily different ids, or the global by-id
          pass would have paired them): two one-sided members.

        Whatever remains past either pool's end stays one-sided.
        """
        de_pool = self._unpaired("de", part, de_idxs)
        en_pool = self._unpaired("en", part, en_idxs)
        i = j = 0
        while i < len(de_pool) and j < len(en_pool):
            de_cell = self._cell("de", (part, de_pool[i]))
            en_cell = self._cell("en", (part, en_pool[j]))
            de_id = _bare(de_cell.slide_id)
            en_id = _bare(en_cell.slide_id)
            if de_id is None and en_id is None:
                self.pairs[(part, de_pool[i])] = (part, en_pool[j])
                i += 1
                j += 1
                continue
            if de_id is not None and en_id is not None:
                i += 1
                j += 1
                continue
            idd_cell, idless_cell = (de_cell, en_cell) if de_id else (en_cell, de_cell)
            adopt = idless_cell.lang_attr is not None or idd_cell.lines[1:] == idless_cell.lines[1:]
            if adopt:
                self.pairs[(part, de_pool[i])] = (part, en_pool[j])
                idless_side: Lang = "en" if de_id else "de"
                idd_side = "de" if de_id else "en"
                self.observe(
                    "id_stamp_pending_twin",
                    member=MemberKey.for_id(de_id or en_id or "?"),
                    side=idless_side,
                    detail=(
                        f"id'd on the {idd_side} half only; the {idless_side} twin "
                        f"pairs positionally (#443)"
                    ),
                )
                i += 1
                j += 1
                continue
            # Unrelated one-sided id'd shared cell: skip it, keep alignment.
            if de_id is not None:
                i += 1
            else:
                j += 1

    def pair_region(
        self,
        de_idxs: list[int],
        en_idxs: list[int],
        part: Part,
        pool_of: Callable[[SideCell], object] | None = None,
    ) -> None:
        """Bucket one region's cells by kind-class, then pair each bucket."""
        classify = pool_of or _pair_class
        pools: dict[object, tuple[list[int], list[int]]] = {}
        for i in de_idxs:
            pools.setdefault(classify(self._cell("de", (part, i))), ([], []))[0].append(i)
        for i in en_idxs:
            pools.setdefault(classify(self._cell("en", (part, i))), ([], []))[1].append(i)
        for de_pool, en_pool in pools.values():
            self.pair_positionally(de_pool, en_pool, part)

    def _region_pair_map(
        self, de_idxs: list[int], en_idxs: list[int], part: Part
    ) -> dict[int, int]:
        """Same-region subset of the global pairing, for merge ordering."""
        en_set = set(en_idxs)
        out: dict[int, int] = {}
        for de_i in de_idxs:
            partner = self.pairs.get((part, de_i))
            if partner is not None and partner[0] == part and partner[1] in en_set:
                out[de_i] = partner[1]
        return out

    # -- phase 3: emission ---------------------------------------------------------

    def emit_region(
        self,
        de_idxs: list[int],
        en_idxs: list[int],
        part: Part,
        role_hint: str | None = None,
    ) -> list[Member]:
        """Build the members of one region in merged document order.

        A DE cell always emits (with its partner wherever that partner
        lives); an EN cell that is *any* DE cell's partner is skipped here —
        its member is emitted from the DE side, even when the partner sits
        in another group or part (a moved / mid-relayout member).
        """
        members: list[Member] = []
        merged = _merged_order(de_idxs, en_idxs, self._region_pair_map(de_idxs, en_idxs, part))
        for de_i, en_i in merged:
            if de_i is not None:
                de_ref: _Ref = (part, de_i)
                partner = self.pairs.get(de_ref)
                if partner is not None and (en_i is None or partner != (part, en_i)):
                    # Partner lives in another region (moved or mid-relayout).
                    members.append(self.build_member(de_ref, partner, role_hint))
                    continue
                members.append(
                    self.build_member(de_ref, (part, en_i) if en_i is not None else None, role_hint)
                )
                continue
            if en_i is None:  # pragma: no cover - _merged_order never emits (None, None)
                continue
            if (part, en_i) in self.paired_en:
                continue  # emitted by its DE partner's region
            members.append(self.build_member(None, (part, en_i), role_hint))
        return members

    def build_member(
        self,
        de_ref: _Ref | None,
        en_ref: _Ref | None,
        role_hint: str | None = None,
    ) -> Member:
        de_cell = self._cell("de", de_ref) if de_ref else None
        en_cell = self._cell("en", en_ref) if en_ref else None
        primary = de_cell or en_cell
        if primary is None:  # pragma: no cover - callers pass at least one ref
            raise DocLensError("member with no sides")
        kind = primary.cell_type
        langness = (
            "localized"
            if (de_cell and de_cell.lang_attr) or (en_cell and en_cell.lang_attr)
            else "shared"
        )
        role = role_hint or self._role_of(primary, kind)
        if role == "header":
            langness = "localized"  # header members are per-language by design
        member = Member(
            key=self._key_of(de_cell, en_cell),
            kind=kind,
            role=role,
            langness=langness,
            layout="companion" if primary.part == "companion" else "inline",
            owner=None,
            de=de_cell,
            en=en_cell,
        )
        if de_cell and en_cell:
            if de_cell.cell_type != en_cell.cell_type:
                self.observe_member(
                    "member_kind_mismatch",
                    member,
                    detail=f"paired cells have kinds {de_cell.cell_type}/{en_cell.cell_type}",
                )
            if (de_cell.lang_attr is None) != (en_cell.lang_attr is None):
                self.observe_member(
                    "lang_attr_mismatch",
                    member,
                    detail=(
                        f"lang attribute present on one side only "
                        f"(de: {de_cell.lang_attr!r}, en: {en_cell.lang_attr!r})"
                    ),
                )
            if de_cell.part != en_cell.part:
                self.observe_member(
                    "layout_cross_language",
                    member,
                    detail="member is inline on one half and in the companion on the other",
                )
        if member.is_one_sided:
            side: Lang = "de" if de_cell else "en"
            self.observe_member(
                "one_sided_member",
                member,
                side=side,
                detail=f"present on the {side} side only",
            )
        if (
            member.langness == "shared"
            and de_cell is not None
            and en_cell is not None
            and _lines_sans_id(de_cell) != _lines_sans_id(en_cell)
        ):
            self.observe_member(
                "shared_divergence",
                member,
                detail="shared member's projections differ byte-wise",
            )
        return member

    @staticmethod
    def _role_of(cell: SideCell, kind: str) -> str:
        if "slide" in cell.tags:
            return "slide"
        if "subslide" in cell.tags:
            return "subslide"
        if "voiceover" in cell.tags:
            return "voiceover"
        if "notes" in cell.tags:
            return "notes"
        if kind == "code":
            return "code"
        return "aux"

    def _key_of(self, de_cell: SideCell | None, en_cell: SideCell | None) -> MemberKey:
        for cell in (de_cell, en_cell):
            if cell is None:
                continue
            bare = _bare(cell.slide_id)
            if bare is not None:
                return MemberKey.for_id(bare)
        # Positional key — the ordinal is assigned once the merged member
        # order of the owning group is known (``_assign_positional_ordinals``).
        kind = de_cell.cell_type if de_cell else en_cell.cell_type  # type: ignore[union-attr]
        return MemberKey.positional("?", kind, -1)

    # -- §3.4 precondition -----------------------------------------------------------

    def check_normalized(self, member: Member) -> None:
        """Every localized and every lang-tagged narrative member must carry
        an id on at least one side (the #443 one-sided-id shape is a
        transition, not a violation)."""
        if member.key.scheme == "id":
            return
        if member.langness != "localized" or member.role == "header":
            return
        code = "idless_narrative" if member.role in ("voiceover", "notes") else "idless_localized"
        lines = [
            f"{side}.{cell.part} line {cell.line_number}"
            for side, cell in (("de", member.de), ("en", member.en))
            if cell is not None
        ]
        self.refuse(
            code,
            f"id-less {member.role} cell ({', '.join(lines)}) — localized and narrative "
            f"cells must carry their own slide_id",
        )

    # -- main ------------------------------------------------------------------------

    def run(self) -> ParseOutcome:
        self.check_keying()
        if self.refusals:
            return ParseOutcome(refusal=NormalizeRefusal(reasons=self.refusals))

        de_seg = _segment_deck(self.de_deck)
        en_seg = _segment_deck(self.en_deck)

        # Phase 2: all pairing, before any member is built.
        self.pair_by_id()
        group_merge = self._pair_groups(de_seg, en_seg)
        self.pair_region(de_seg.header_idxs, en_seg.header_idxs, "deck")
        for de_gi, en_gi in group_merge:
            de_members = de_seg.groups[de_gi].member_idxs if de_gi is not None else []
            en_members = en_seg.groups[en_gi].member_idxs if en_gi is not None else []
            if de_gi is not None and en_gi is not None:
                self.pair_region(de_members, en_members, "deck")
        de_comp_idxs = list(range(len(self.de_comp.cells))) if self.de_comp else []
        en_comp_idxs = list(range(len(self.en_comp.cells))) if self.en_comp else []
        if de_comp_idxs or en_comp_idxs:
            self.pair_region(de_comp_idxs, en_comp_idxs, "companion", pool_of=_companion_pool_key)
        self.paired_en = set(self.pairs.values())

        # Phase 3: emission.
        header = self.emit_region(
            de_seg.header_idxs, en_seg.header_idxs, "deck", role_hint="header"
        )
        groups = self._emit_groups(de_seg, en_seg, group_merge)
        orphans = self._place_companions(groups, de_comp_idxs, en_comp_idxs)
        self._assign_positional_ordinals(header, groups, orphans)
        self._materialize_member_observations()

        # Document-level observations.
        self._check_layout_invariant()
        if self.de_deck.preamble_lines != self.en_deck.preamble_lines:
            self.observe("preamble_divergence", detail="deck preambles differ between halves")
        self._check_wrong_language()

        deck = BilingualDeck(
            comment_token=self.comment_token,
            de_deck_preamble=self.de_deck.preamble_lines,
            en_deck_preamble=self.en_deck.preamble_lines,
            de_companion_preamble=self.de_comp.preamble_lines if self.de_comp else None,
            en_companion_preamble=self.en_comp.preamble_lines if self.en_comp else None,
            header=header,
            groups=groups,
            orphans=orphans,
            observations=self.observations,
        )
        for member in deck.members():
            self.check_normalized(member)
        self._check_key_uniqueness(deck)
        if self.refusals:
            return ParseOutcome(refusal=NormalizeRefusal(reasons=self.refusals))
        return ParseOutcome(deck=deck)

    # -- groups ------------------------------------------------------------------------

    def _pair_groups(
        self, de_seg: _DeckSeg, en_seg: _DeckSeg
    ) -> list[tuple[int | None, int | None]]:
        de_ids = [g.group_id for g in de_seg.groups]
        en_ids = [g.group_id for g in en_seg.groups]
        common_de = [gid for gid in de_ids if gid in set(en_ids)]
        common_en = [gid for gid in en_ids if gid in set(de_ids)]
        if common_de != common_en:
            self.observe(
                "group_order_divergence",
                detail=f"group order differs between halves (de: {common_de}, en: {common_en})",
            )
        en_by_id = {g.group_id: gi for gi, g in enumerate(en_seg.groups)}
        pair_map = {
            de_gi: en_by_id[g.group_id]
            for de_gi, g in enumerate(de_seg.groups)
            if g.group_id in en_by_id
        }
        return _merged_order(
            list(range(len(de_seg.groups))), list(range(len(en_seg.groups))), pair_map
        )

    def _emit_groups(
        self,
        de_seg: _DeckSeg,
        en_seg: _DeckSeg,
        group_merge: list[tuple[int | None, int | None]],
    ) -> list[SlideGroup]:
        groups: list[SlideGroup] = []
        for de_gi, en_gi in group_merge:
            de_group = de_seg.groups[de_gi] if de_gi is not None else None
            en_group = en_seg.groups[en_gi] if en_gi is not None else None
            some_group = de_group or en_group
            if some_group is None:  # pragma: no cover - merge never yields (None, None)
                continue
            group_id = some_group.group_id
            if de_group is None or en_group is None:
                self.observe(
                    "one_sided_group",
                    side="de" if de_group else "en",
                    detail=f'group "{group_id}" exists on one half only',
                )
            anchor = self._build_anchor(group_id, de_group, en_group)
            group = SlideGroup(anchor_id=group_id, anchor=anchor)
            anchor_key = anchor.key if anchor else None
            group.members = self.emit_region(
                de_group.member_idxs if de_group else [],
                en_group.member_idxs if en_group else [],
                "deck",
            )
            for member in group.members:
                member.owner = anchor_key
            groups.append(group)
        return groups

    def _build_anchor(
        self,
        group_id: str,
        de_group: _GroupSeg | None,
        en_group: _GroupSeg | None,
    ) -> Member | None:
        de_idx = de_group.anchor_idx if de_group else None
        en_idx = en_group.anchor_idx if en_group else None
        if de_idx is None and en_idx is None:
            return None  # the preface group
        is_title = group_id == TITLE_SLIDE_ID
        member = self.build_member(
            ("deck", de_idx) if de_idx is not None else None,
            ("deck", en_idx) if en_idx is not None else None,
            role_hint="header" if is_title else None,
        )
        # The anchor's identity is the group identity; the title macro
        # carries no slide_id and anchors the reserved id "title" (§3.3).
        member.key = MemberKey.for_id(group_id)
        return member

    # -- companions ----------------------------------------------------------------------

    def _place_companions(
        self,
        groups: list[SlideGroup],
        de_idxs: list[int],
        en_idxs: list[int],
    ) -> list[Member]:
        if not de_idxs and not en_idxs:
            return []
        members = self.emit_region(de_idxs, en_idxs, "companion")
        by_anchor = {group.anchor_id: group for group in groups}
        orphans: list[Member] = []
        for member in members:
            primary = member.de or member.en
            if primary is None:  # pragma: no cover
                continue
            if member.role not in ("voiceover", "notes"):
                self.observe_member(
                    "unexpected_companion_cell",
                    member,
                    detail=f"companion cell with role {member.role!r}",
                )
            de_owner = _bare(member.de.for_slide) if member.de else None
            en_owner = _bare(member.en.for_slide) if member.en else None
            if member.de and member.en and de_owner != en_owner:
                self.observe_member(
                    "owner_mismatch",
                    member,
                    detail=f"for_slide differs between halves (de: {de_owner}, en: {en_owner})",
                )
            owner = de_owner or en_owner
            if owner is None and _bare(primary.slide_id) == TITLE_SLIDE_ID:
                # Pre-#242 legacy: slide_id="title" with no for_slide is title
                # *intent* — the "title" id is an owner reference, not the
                # member's identity, and keying it id:title would collide with
                # the title anchor. The stamp machinery refuses this unowned
                # shape rather than guessing (#527), and so do we — with the
                # actual fix spelled out instead of a misleading duplicate_id.
                # Re-keying positionally keeps the remaining enumeration clean.
                owner = TITLE_SLIDE_ID
                member.key = MemberKey.positional("?", member.kind, -1)
                self.refuse(
                    "legacy_title_companion",
                    f'companion cell with slide_id="title" and no for_slide '
                    f"(line {primary.line_number}) — pre-#242 legacy title intent; "
                    f'give the cell for_slide="title" and its own slide_id',
                )
            group = by_anchor.get(owner) if owner is not None else None
            if group is None:
                self.observe_member(
                    "owner_missing",
                    member,
                    detail=f"for_slide {owner!r} matches no slide anchor",
                )
                orphans.append(member)
            else:
                member.owner = group.anchor.key if group.anchor else None
                group.members.append(member)
        return orphans

    # -- finishing -------------------------------------------------------------------------

    def _assign_positional_ordinals(
        self,
        header: list[Member],
        groups: list[SlideGroup],
        orphans: list[Member],
    ) -> None:
        """Fill in rule-2 pos keys now that merged member order is known."""

        def assign(members: list[Member], group_token: str) -> None:
            counters: Counter[str] = Counter()
            for member in members:
                if member.key.scheme == "id":
                    continue
                ordinal = counters[member.kind]
                counters[member.kind] += 1
                member.key = MemberKey.positional(group_token, member.kind, ordinal)

        assign(header, HEADER_GROUP)
        for group in groups:
            assign(group.members, group.anchor_id)
        assign(orphans, ORPHAN_GROUP)

    def _check_layout_invariant(self) -> None:
        for lang in LANGS:
            deck = self._source(lang, "deck")
            comp = self._source(lang, "companion")
            if deck is None:  # pragma: no cover
                continue
            has_inline_vo = any("voiceover" in c.tags for c in deck.cells)
            separated = comp is not None and bool(comp.cells)
            if has_inline_vo and separated:
                self.observe(
                    "layout_mixed",
                    side=lang,
                    detail="half has both inline voiceover cells and a companion file",
                )
        de_separated = self.de_comp is not None and bool(self.de_comp.cells)
        en_separated = self.en_comp is not None and bool(self.en_comp.cells)
        if de_separated != en_separated:
            self.observe(
                "layout_cross_language",
                detail="one half uses a voiceover companion, the other does not",
            )

    def _check_wrong_language(self) -> None:
        for lang in LANGS:
            for part in PARTS:
                source = self._source(lang, part)
                if source is None:
                    continue
                for cell in source.cells:
                    if cell.lang_attr is not None and cell.lang_attr != lang:
                        self.observe(
                            "wrong_language_cell",
                            side=lang,
                            detail=(
                                f"{part}.{lang} line {cell.line_number} carries "
                                f'lang="{cell.lang_attr}"'
                            ),
                        )

    def _check_key_uniqueness(self, deck: BilingualDeck) -> None:
        counts: Counter[str] = Counter()
        for member in deck.members():
            counts[member.key.render()] += 1
        for handle, n in sorted(counts.items()):
            if n > 1:
                self.refuse(
                    "duplicate_id",
                    f"member key {handle} resolves to {n} distinct members",
                    member=MemberKey.parse(handle),
                )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_bundle(
    de_text: str,
    en_text: str,
    de_companion: str | None = None,
    en_companion: str | None = None,
    *,
    comment_token: str = "#",
) -> ParseOutcome:
    """Parse the ≤4-file bundle into one :class:`BilingualDeck` (design §3.2).

    ``de_text`` / ``en_text`` are the two deck halves; the companions are
    optional (``None`` = the file does not exist; an empty string is an
    existing empty file). Mismatches between the projections are recorded as
    observations on the returned deck; input failing the §3.4 normalize
    precondition yields a framed
    :class:`~clm.slides.bilingual_doc.NormalizeRefusal` instead — never an
    exception, never a heuristic parse.
    """
    parser = _Parser(
        de_deck=_read_source(de_text, "de", "deck", comment_token),
        en_deck=_read_source(en_text, "en", "deck", comment_token),
        de_comp=(
            _read_source(de_companion, "de", "companion", comment_token)
            if de_companion is not None
            else None
        ),
        en_comp=(
            _read_source(en_companion, "en", "companion", comment_token)
            if en_companion is not None
            else None
        ),
        comment_token=comment_token,
    )
    return parser.run()


def project(deck: BilingualDeck, lang: Lang, part: Part = "deck") -> str | None:
    """Render one file's text from the model (design §4).

    Returns ``None`` for a companion part the bundle never had. Emission is
    verbatim: the preamble lines plus every member side of ``(lang, part)``
    sorted by source ordinal — projection never rewrites a header or a body,
    so ``project ∘ parse`` is byte-identity by construction.
    """
    if part == "deck":
        preamble = deck.de_deck_preamble if lang == "de" else deck.en_deck_preamble
    else:
        maybe = deck.de_companion_preamble if lang == "de" else deck.en_companion_preamble
        if maybe is None:
            return None
        preamble = maybe
    sides = [
        side
        for member in deck.members()
        if (side := member.side(lang)) is not None and side.part == part
    ]
    sides.sort(key=lambda side: side.index)
    indexes = [side.index for side in sides]
    if indexes != list(range(len(sides))):
        raise DocLensError(
            f"projection of ({lang}, {part}) lost or duplicated cells: indexes {indexes}"
        )
    lines = list(preamble)
    for side in sides:
        lines.extend(side.lines)
    return "\n".join(lines)


@define
class LoadedBundle:
    """A bundle read from disk plus its parse outcome."""

    de_path: Path
    en_path: Path
    de_companion_path: Path | None
    en_companion_path: Path | None
    comment_token: str
    outcome: ParseOutcome


def load_bundle(path: Path, twin: Path | None = None) -> LoadedBundle:
    """Read a deck bundle from disk and parse it.

    ``path`` may be a deck half (``slides_x.de.py``), a deck stem
    (``slides_x.py``), or one half with ``twin`` naming the other.
    Companions are resolved through the standard subdir-then-sibling rule
    (:func:`clm.slides.voiceover_tools.resolve_companion`).
    """
    if twin is not None:
        pair = order_split_pair(path, twin)
        if pair is None:
            raise DocLensError(f"{path} and {twin} do not form a de/en deck pair")
    else:
        pair = derive_split_pair(path) or derive_split_pair_from_stem(path)
        if pair is None:
            raise DocLensError(f"{path} is not a split deck half/stem with an existing twin")
    de_path, en_path = pair
    comment_token = comment_token_for_path(de_path)
    de_companion = resolve_companion(de_path)
    en_companion = resolve_companion(en_path)
    outcome = parse_bundle(
        de_path.read_text(encoding="utf-8"),
        en_path.read_text(encoding="utf-8"),
        de_companion.read_text(encoding="utf-8") if de_companion else None,
        en_companion.read_text(encoding="utf-8") if en_companion else None,
        comment_token=comment_token,
    )
    return LoadedBundle(
        de_path=de_path,
        en_path=en_path,
        de_companion_path=de_companion,
        en_companion_path=en_companion,
        comment_token=comment_token,
        outcome=outcome,
    )
