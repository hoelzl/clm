"""Symmetrize voiceover / notes ``slide_id``s across the two halves of a split deck.

Issue #403 fix #3 (the AI-dev DE/EN sync report, §10). Within a single deck the two
halves can disagree on whether a *narrative* cell (``voiceover`` / ``notes``) carries a
``slide_id``: e.g. the DE half's voiceovers are id-less
(``# %% [markdown] lang="de" tags=["voiceover"]``) while the EN half's twins carry
``slide_id="…"``. Phase B made ``clm slides sync`` *non-destructive* for this case — it
now pairs the two by occurrence-under-slide instead of duplicating the German track —
but the asymmetry itself is still messy and confusing.

This reconciler makes the two halves **agree** on the voiceover-id convention, *safely*.
The documented ``assign-ids`` is unsafe on a single split half: it slugs each id from
that file's own heading, so running it on the DE and EN halves independently mints
**divergent** ids for the same logical cell (the #162 hazard). This reconciler instead
pairs the halves' narratives by the **same occurrence-under-slide identity** the Phase B
sync engine uses — the n-th narrative of its role under its owning slide — and then
either strips the id from the id'd side or stamps the id'd side's *existing* id onto the
id-less side. It never mints a fresh id and never derives one from per-file content, so
the two halves can never diverge.

Only **paired** narratives that *disagree* on id-ness are touched. A narrative present on
only one half (a structural difference — ``sync``'s job) is left alone, as is a pair that
already agrees (both id-less, or both id'd).
"""

from __future__ import annotations

import re
from collections import Counter

from attrs import define, field

from clm.notebooks.slide_parser import parse_cell_header
from clm.slides.anchor_primitives import owning_group
from clm.slides.raw_cells import RawCell, reconstruct, split_cells
from clm.slides.sync_writeback import role_of

# The narrative identity used to pair the two halves: the n-th narrative of its role
# under its owning slide (the Phase B occurrence-under-slide key — stable under a
# sibling-cell insertion, and language-agnostic so DE pairs with EN).
_NarrKey = tuple[str | None, str, int]

#: Reconcile direction — strip the id'd side to id-less, or stamp the id-less side id'd.
TO_IDLESS = "id-less"
TO_IDS = "ids"

_SLIDE_ID_RE = re.compile(r'\s*slide_id="[^"]*"')


@define
class VoiceoverIdChange:
    """One narrative cell whose ``slide_id`` was added or removed to match its twin."""

    lang: str  # "de" / "en" — the half that was edited
    line_number: int  # 1-based header line, for human-locatable reporting
    role: str  # "voiceover" / "notes"
    owning_slide_id: str | None  # the slide the narrative sits under
    occurrence: int  # its index among same-role narratives under that slide
    action: str  # "strip" / "stamp"
    old_id: str | None
    new_id: str | None


@define
class ReconcileResult:
    """The outcome of one pair's reconciliation."""

    changes: list[VoiceoverIdChange] = field(factory=list)
    unpaired: int = 0  # narratives present on one half only (left to ``sync``)
    already_symmetric: int = 0  # paired narratives that already agreed

    @property
    def de_changed(self) -> bool:
        return any(c.lang == "de" for c in self.changes)

    @property
    def en_changed(self) -> bool:
        return any(c.lang == "en" for c in self.changes)

    @property
    def is_noop(self) -> bool:
        return not self.changes


def _narrative_index(cells: list[RawCell]) -> dict[_NarrKey, tuple[int, RawCell]]:
    """Index a half's narratives by ``(owning_slide_id, role, occurrence)``.

    Counts the occurrence over *all* narratives under a slide — id-less and id'd alike —
    so the n-th voiceover on DE pairs with the n-th on EN regardless of which side
    carries an id (that is exactly the asymmetry we are reconciling). Mirrors
    :func:`clm.slides.sync_apply._narrative_keys_by_index`, but does not filter by
    id-ness.
    """
    out: dict[_NarrKey, tuple[int, RawCell]] = {}
    seen: Counter[tuple[str | None, str]] = Counter()
    for idx, cell in enumerate(cells):
        meta = cell.metadata
        if meta.is_j2 or not meta.is_narrative:
            continue
        role = role_of(meta)
        if role is None:
            continue
        owning, _bounds = owning_group(cells, idx, meta.lang)
        base = (owning, role)
        out[(owning, role, seen[base])] = (idx, cell)
        seen[base] += 1
    return out


def _strip_slide_id(cell: RawCell) -> None:
    """Remove ``slide_id="…"`` from a cell header in place (byte-preserving)."""
    new_header = _SLIDE_ID_RE.sub("", cell.lines[0]).rstrip()
    cell.lines[0] = new_header
    cell.metadata = parse_cell_header(new_header, cell.metadata.comment_token)


def _stamp_slide_id(cell: RawCell, slide_id: str) -> None:
    """Set ``slide_id="…"`` on a cell header in place (canonical trailing position)."""
    stripped = _SLIDE_ID_RE.sub("", cell.lines[0]).rstrip()
    new_header = f'{stripped} slide_id="{slide_id}"'
    cell.lines[0] = new_header
    cell.metadata = parse_cell_header(new_header, cell.metadata.comment_token)


def reconcile_voiceover_ids(
    de_text: str,
    en_text: str,
    de_token: str,
    en_token: str,
    *,
    direction: str = TO_IDLESS,
) -> tuple[str, str, ReconcileResult]:
    """Symmetrize the voiceover/notes ``slide_id`` convention across a split pair.

    Pairs the halves' narratives by occurrence-under-slide and, for each pair that
    *disagrees* on id-ness, resolves it toward ``direction``:

    - :data:`TO_IDLESS` (default) — strip the id from whichever half carries one, so the
      pair ends up id-less (the engine's post-#6 canonical form, and collision-proof);
    - :data:`TO_IDS` — stamp the id'd half's *existing* ``slide_id`` onto the id-less
      half, so the pair ends up id'd under the **same** id on both halves.

    Returns ``(de_text', en_text', result)``; a half's text is returned unchanged
    (byte-identical) when it had no change. Only narrative headers are ever rewritten —
    slides, code, and bodies are untouched.
    """
    if direction not in (TO_IDLESS, TO_IDS):
        raise ValueError(f"direction must be {TO_IDLESS!r} or {TO_IDS!r}, got {direction!r}")

    de_pre, de_cells = split_cells(de_text, de_token)
    en_pre, en_cells = split_cells(en_text, en_token)
    de_index = _narrative_index(de_cells)
    en_index = _narrative_index(en_cells)

    result = ReconcileResult()
    for key in sorted(set(de_index) | set(en_index), key=lambda k: (str(k[0]), k[1], k[2])):
        de_entry = de_index.get(key)
        en_entry = en_index.get(key)
        if de_entry is None or en_entry is None:
            result.unpaired += 1  # present on one half only — a structural diff (sync's job)
            continue
        _de_i, de_cell = de_entry
        _en_i, en_cell = en_entry
        de_id = de_cell.metadata.slide_id
        en_id = en_cell.metadata.slide_id
        if (de_id is None) == (en_id is None):
            result.already_symmetric += 1  # both id-less or both id'd — leave alone
            continue

        owning, role, occ = key
        if direction == TO_IDLESS:
            lang, cell, old = (
                ("de", de_cell, de_id) if de_id is not None else ("en", en_cell, en_id)
            )
            _strip_slide_id(cell)
            result.changes.append(
                VoiceoverIdChange(lang, cell.line_number, role, owning, occ, "strip", old, None)
            )
        else:  # TO_IDS — copy the id'd twin's existing id onto the id-less side
            src_id = de_id if de_id is not None else en_id
            assert src_id is not None  # exactly one side is id'd in this branch
            lang, cell = ("en", en_cell) if de_id is not None else ("de", de_cell)
            _stamp_slide_id(cell, src_id)
            result.changes.append(
                VoiceoverIdChange(lang, cell.line_number, role, owning, occ, "stamp", None, src_id)
            )

    de_out = reconstruct(de_pre, de_cells) if result.de_changed else de_text
    en_out = reconstruct(en_pre, en_cells) if result.en_changed else en_text
    return de_out, en_out, result


def _collapse_one_half(cells: list[RawCell]) -> int:
    """Strip the id from every narrative cell in a duplicated ``(slide_id, role)`` group.

    Returns the number of cells stripped. Mutates ``cells`` in place.
    """
    groups: dict[tuple[str, str], list[RawCell]] = {}
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or not meta.is_narrative or meta.slide_id is None:
            continue
        role = role_of(meta)
        if role is None:
            continue
        groups.setdefault((meta.slide_id, role), []).append(cell)
    stripped = 0
    for group in groups.values():
        if len(group) <= 1:
            continue
        for cell in group:
            _strip_slide_id(cell)
            stripped += 1
    return stripped


def collapse_intra_half_duplicates(
    de_text: str,
    en_text: str,
    de_token: str,
    en_token: str,
) -> tuple[str, str, int]:
    """Strip ``slide_id`` from narrative cells that duplicate a ``(slide_id, role)`` key
    within a half — the **symmetric** over-stamp :func:`reconcile_voiceover_ids` leaves
    alone.

    A slide with several ``voiceover`` / ``notes`` cells all stamped with the slide's id
    (``assign-ids`` ``source=voiceover-inherit``) collides on ``(slide_id, role)`` within
    each half. When *both* halves are over-stamped the same way, the occurrence pairing
    sees them *agree* (both id'd), so ``reconcile_voiceover_ids`` does not touch them —
    yet ``verify`` flags a ``duplicate-id`` on each half. This strips the id from
    **every** narrative cell in a duplicated ``(slide_id, role)`` group → the engine's
    canonical id-less narration form (collision-proof). It never mints or derives an id
    (#162). Returns ``(de_text', en_text', stripped_count)``; a half is returned
    byte-identical when nothing changed.
    """
    de_pre, de_cells = split_cells(de_text, de_token)
    en_pre, en_cells = split_cells(en_text, en_token)
    de_stripped = _collapse_one_half(de_cells)
    en_stripped = _collapse_one_half(en_cells)
    de_out = reconstruct(de_pre, de_cells) if de_stripped else de_text
    en_out = reconstruct(en_pre, en_cells) if en_stripped else en_text
    return de_out, en_out, de_stripped + en_stripped
