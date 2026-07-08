"""Rename a ``slide_id`` across both halves of a split deck **and** its ledger.

Issue #572. A manual ``slide_id`` rename (``foo-old`` → ``foo-new``) on a split
DE/EN pair silently drops the pair's per-topic sync ledger baseline to *cold*
for that id: the v3 differ keys trust by ``id:<slide_id>`` and the **only**
sanctioned key migration is ``pos: → id:`` (id-less → id'd), so an
``id: → id:`` rename is not recovered — every renamed member reads as a cold
add (:func:`clm.slides.sync_diff` ``verify_cold``). A subsequent edit of the
renamed cell then frames ``verify_cold`` (whose only answer, ``confirm``, banks
the — possibly stale — existing twin) instead of ``translate_edit``.

This module does the rename the design-consistent way: it rewrites the id on
**both** halves and migrates the ledger baseline key in one step, keeping the
member's identity *total* across the rename. Crucially it **migrates, never
re-fingerprints** the baseline — the carried entry keeps the old content
fingerprints under the new key. So if the cell was *also* edited in the same
breath, the next ``clm slides sync report`` compares the edited bytes against
the carried baseline and frames a proper ``translate_edit`` — never a
stale-``confirm``. (Content fingerprints are computed modulo ``slide_id`` /
``for_slide``, so the id rewrite itself never perturbs them.)

The two pieces are pure and independently testable:

* :func:`rename_in_half` rewrites one half's text (``slide_id`` on the renamed
  cell, ``for_slide`` on every companion that owns it).
* :func:`migrate_ledger_key` re-keys the ledger entry, its owner references,
  the id-keyed member-order handles, and — when the renamed id anchors a slide
  group — the whole positional-key / order cascade (via
  :func:`clm.slides.doc_ledger.rename_group_scopes`).
"""

from __future__ import annotations

import re

from attrs import evolve, frozen

from clm.notebooks.slide_parser import parse_cell_header
from clm.slides.doc_ledger import DeckLedger, rename_group_scopes
from clm.slides.raw_cells import reconstruct, split_cells

#: A usable ``slide_id``: non-empty, no whitespace, no ``"`` (which would break
#: the header attribute). Deliberately permissive — slug *quality* is a separate
#: concern; this only rejects ids that cannot be written into a cell header.
_VALID_ID_RE = re.compile(r'^[^\s"]+$')


@frozen
class RenameResult:
    """The outcome of renaming one id across a pair (+ ledger)."""

    old: str
    new: str
    de_slide_id_hits: int  # slide_id occurrences rewritten on the DE half
    en_slide_id_hits: int
    de_for_slide_hits: int  # for_slide (owner) references rewritten on DE
    en_for_slide_hits: int
    ledger_migrated: bool  # a ledger baseline entry was re-keyed

    @property
    def slide_id_hits(self) -> int:
        return self.de_slide_id_hits + self.en_slide_id_hits

    @property
    def for_slide_hits(self) -> int:
        return self.de_for_slide_hits + self.en_for_slide_hits


def is_valid_slide_id(slide_id: str) -> bool:
    """A ``slide_id`` that can be written into a cell header without breaking it."""
    return bool(_VALID_ID_RE.match(slide_id))


def _split_marker(slide_id: str) -> tuple[str, str]:
    """Split a leading ``!`` preserve marker off a verbatim ``slide_id``.

    ``"!intro"`` → ``("!", "intro")``; ``"intro"`` → ``("", "intro")``. The
    marker (§ assign-ids "don't re-slug") is carried through a rename.
    """
    return ("!", slide_id[1:]) if slide_id.startswith("!") else ("", slide_id)


def _bare(slide_id: str) -> str:
    return _split_marker(slide_id)[1]


def _rewrite_attr(header: str, attr: str, value: str) -> str:
    """Replace the first ``attr="…"`` value in a cell header line."""
    return re.sub(rf'{attr}="[^"]*"', f'{attr}="{value}"', header, count=1)


def slide_ids_in(text: str, comment_token: str) -> set[str]:
    """Every bare ``slide_id`` present in one half (markers stripped)."""
    _pre, cells = split_cells(text, comment_token)
    return {_bare(c.metadata.slide_id) for c in cells if c.metadata.slide_id is not None}


def rename_in_half(text: str, comment_token: str, old: str, new: str) -> tuple[str, int, int]:
    """Rewrite ``old`` → ``new`` on one half; return ``(text', slide_id_hits, for_slide_hits)``.

    Rewrites the ``slide_id`` of the renamed cell **and** the ``for_slide`` of
    every companion that owns it (a group rename cascades into ``for_slide``
    references, exactly as the fingerprint's owner-free signature anticipates).
    Any ``!`` preserve marker on the matched attribute is carried through. The
    text is returned byte-identical when nothing matched. ``old``/``new`` are
    bare ids (no marker).
    """
    pre, cells = split_cells(text, comment_token)
    slide_id_hits = 0
    for_slide_hits = 0
    for cell in cells:
        meta = cell.metadata
        header = cell.lines[0]
        rewritten = header
        if meta.slide_id is not None and _bare(meta.slide_id) == old:
            marker, _ = _split_marker(meta.slide_id)
            rewritten = _rewrite_attr(rewritten, "slide_id", marker + new)
            slide_id_hits += 1
        if meta.for_slide is not None and _bare(meta.for_slide) == old:
            marker, _ = _split_marker(meta.for_slide)
            rewritten = _rewrite_attr(rewritten, "for_slide", marker + new)
            for_slide_hits += 1
        if rewritten != header:
            cell.lines[0] = rewritten
            cell.metadata = parse_cell_header(rewritten, comment_token)
    out = reconstruct(pre, cells) if (slide_id_hits or for_slide_hits) else text
    return out, slide_id_hits, for_slide_hits


def migrate_ledger_key(deck: DeckLedger, old: str, new: str) -> bool:
    """Re-key the ledger baseline for a renamed ``slide_id`` (``old`` → ``new``).

    Migrates — never re-fingerprints — so a simultaneous content edit surfaces
    as ``translate_edit`` on the next report, not a silent cold-confirm. Touches
    every place the id is encoded:

    * the ``id:<old>`` member entry (re-keyed, entry ``key`` rewritten);
    * ``owner`` references on companion entries the renamed slide owns;
    * id-keyed handles inside every member-order scope's list;
    * when ``old`` anchors a slide group, the whole positional-key / member-order
      / group-order cascade (its bare id tokens every ``pos:`` key of the group)
      via :func:`~clm.slides.doc_ledger.rename_group_scopes`.

    Returns ``True`` when anything changed (``False`` = the id was cold / absent,
    a no-op the caller reports as "ledger not updated").
    """
    old_key = f"id:{old}"
    new_key = f"id:{new}"
    changed = False

    # A slide group anchored by ``old`` tokens every pos: key + order scope of
    # the group with its bare id — cascade those first. group_order is the
    # canonical anchor list; the pos-key / by-side checks are belt-and-braces.
    is_anchor = (
        old in deck.group_order
        or any(old in order for order in deck.group_order_by_side.values())
        or any(k.startswith(f"pos:{old}/") for k in deck.members)
    )
    if is_anchor:
        rename_group_scopes(deck, old, new)
        changed = True

    # The member entry itself (rename_group_scopes leaves the id: entry to us).
    lm = deck.members.pop(old_key, None)
    if lm is not None:
        deck.members[new_key] = evolve(lm, entry=evolve(lm.entry, key=new_key))
        changed = True

    # Owner references: a companion owned by the renamed slide points at it.
    for key, member in list(deck.members.items()):
        if member.entry.owner == old_key:
            deck.members[key] = evolve(member, entry=evolve(member.entry, owner=new_key))
            changed = True

    # Member-order value lists carry id: handles (the anchor's own handle among
    # them for the group-rename case) — swap them regardless of scope key.
    for scope_key, handles in list(deck.member_order.items()):
        if old_key in handles:
            deck.member_order[scope_key] = [new_key if h == old_key else h for h in handles]
            changed = True

    return changed
