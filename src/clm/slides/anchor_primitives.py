"""Positional voiceover-anchor primitives shared by the voiceover and sync engines.

A narrative (``voiceover`` / ``notes``) cell is positioned *within* a slide group
by recording its **immediate predecessor content cell**, occurrence-qualified. This
is the ``vo_anchor`` model introduced by PR #199 for ``voiceover extract``/``inline``
and the build-time merge; it is factored here so the ``clm slides sync`` engine can
key and place narrative cells by the *same* algorithm (Issue #403) — otherwise the
two subsystems anchor voiceovers differently and a deck round-trips one way but not
the other.

``for_slide`` already records a voiceover's *owning slide* — enough for the build
merge and ``voiceover sync`` — but it cannot say *where among that slide's
continuation cells* the voiceover originally sat. The anchor records the voiceover's
immediate predecessor content cell so ``inline``/``sync`` can restore it to its exact
position instead of dumping every voiceover at the end of its slide group.

The anchor is either ``id:<slide_id>`` (when the predecessor carries a slide_id — the
common "right after the heading" case) or ``fp:<fingerprint>`` of the predecessor's
body. The fingerprint is body-only on purpose: header-tag edits (e.g. adding
``keep``) between extract and inline must not break the anchor.

Neither a slide_id nor a body fingerprint is guaranteed unique *within* one slide
group (repeated boilerplate code cells, two cells sharing a slide_id, a de/en pair).
So the token also carries a 0-based occurrence ordinal — ``id:<sid>#<n>`` /
``fp:<hash>#<n>`` — meaning "the n-th cell in the group matching this token".
Resolution is always scoped to the owning slide group; it never searches across
groups.

A third kind ``tm:title#0`` (the title-macro anchor) addresses the macro-generated
title slide directly. That slide is a j2 ``header`` macro cell carrying no slide_id,
so it can be neither ``id:``- nor ``fp:``-anchored (j2 cells are excluded from anchor
candidates). A title greeting authored *before* the title slide's trailing
continuation cells therefore has no content predecessor; ``tm:title#0`` records
"right after the title macro" so the merge restores it at the start of the title
group rather than the end (#246). ``occ`` is always 0 — a deck has exactly one title
macro.
"""

from __future__ import annotations

import hashlib

from clm.slides.pairing import TITLE_SLIDE_ID, is_title_macro_cell
from clm.slides.raw_cells import RawCell

# The title-macro anchor (#246): kind ``tm`` resolves to the j2 title macro cell
# itself rather than a content cell within a slide group.
TITLE_MACRO_KIND = "tm"
TITLE_MACRO_ANCHOR = f"{TITLE_MACRO_KIND}:{TITLE_SLIDE_ID}#0"


def body_fingerprint(cell: RawCell) -> str:
    """Return a short, stable fingerprint of a cell's body.

    Blank lines are dropped entirely (not just leading/trailing) so the
    fingerprint is invariant under the ``\\n{3,}`` -> ``\\n\\n`` blank-line
    cleanup that ``extract`` applies to the whole slide text *after* the
    anchor is recorded. Trailing whitespace is stripped and the cell type
    is folded in to avoid markdown/code collisions.

    A j2 cell carries its entire content on its header line ``lines[0]`` (the
    ``# {{ ... }}`` / ``# j2 ...`` macro call); its ``lines[1:]`` are only
    inter-cell blanks. So for a j2 cell the header *is* the body and must be
    hashed — otherwise every j2 cell collapses to the same empty fingerprint
    and two different macros become indistinguishable, leaving the occurrence
    ordinal as the only (brittle) discriminator (#247). This is safe because
    the companion merge runs host-side on the raw slide text *before* j2
    expansion, so the macro call is byte-identical at extract and at merge
    time. Content cells keep hashing only ``lines[1:]`` so a header-only edit
    (e.g. adding a tag) never moves their voiceover.
    """
    src = cell.lines if cell.metadata.is_j2 else cell.lines[1:]
    body_lines = [ln.rstrip() for ln in src]
    body_lines = [ln for ln in body_lines if ln]
    norm = "\n".join(body_lines)
    payload = f"{cell.metadata.cell_type}\x00{norm}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def anchor_key(cell: RawCell) -> tuple[str, str]:
    """Return the ``(kind, value)`` half of an anchor for ``cell``."""
    sid = cell.metadata.slide_id
    if sid:
        return ("id", sid)
    return ("fp", body_fingerprint(cell))


def anchor_candidates(
    cells: list[RawCell],
    bounds: tuple[int, int],
    kind: str,
    value: str,
    vo_lang: str | None,
) -> list[int]:
    """Indices within ``bounds`` matching an anchor ``(kind, value)``.

    Returned in document order. Narrative cells (other voiceover/notes
    cells) and cells of a conflicting language are excluded so the
    occurrence ordinal counts the same cells at extract time and at inline
    time. A j2 cell *is* eligible: a voiceover authored after a mid-group j2
    cell (e.g. an inline widget macro) anchors to it via an ``fp:`` body
    fingerprint, and the host-side merge runs *before* j2 expansion so that
    fingerprint is stable (#247). The title macro never resolves here — it
    carries the dedicated ``tm:`` anchor (#246), matched separately — so its
    presence among the candidates is harmless.
    """
    lo, hi = bounds
    out: list[int] = []
    for i in range(lo, hi):
        meta = cells[i].metadata
        if meta.is_narrative:
            continue
        if vo_lang and meta.lang and meta.lang != vo_lang:
            continue
        if kind == "id" and meta.slide_id == value:
            out.append(i)
        elif kind == "fp" and body_fingerprint(cells[i]) == value:
            out.append(i)
    return out


def anchor_token(
    cells: list[RawCell],
    pred_idx: int,
    bounds: tuple[int, int],
    vo_lang: str | None,
) -> str:
    """Build the occurrence-qualified anchor token for a predecessor cell.

    ``bounds`` is the predecessor's owning slide group. The ordinal is the
    predecessor's position among same-token candidates in that group, so a
    voiceover after the second of two identical cells resolves back to the
    second, not the first.

    The j2 title macro is anchored with its dedicated, content-independent
    ``tm:`` token (#246) rather than a fingerprint of its ``header(...)`` call,
    so a title greeting sitting directly under the macro restores to the start
    of the title group regardless of the title text. Every *other* j2 cell (an
    inline widget macro, say) gets an ordinary ``fp:`` anchor (#247).
    """
    if is_title_macro_cell(cells[pred_idx]):
        return TITLE_MACRO_ANCHOR
    kind, value = anchor_key(cells[pred_idx])
    candidates = anchor_candidates(cells, bounds, kind, value, vo_lang)
    occ = candidates.index(pred_idx) if pred_idx in candidates else 0
    return f"{kind}:{value}#{occ}"


def split_anchor(anchor: str) -> tuple[str, str, int]:
    """Parse ``kind:value#occ`` into ``(kind, value, occ)``.

    A legacy token without the ``#occ`` suffix yields occurrence 0.
    """
    kind, _, rest = anchor.partition(":")
    value, _, occ_s = rest.partition("#")
    occ = int(occ_s) if occ_s.isdigit() else 0
    return kind, value, occ


def find_predecessor_index(
    cells: list[RawCell],
    voiceover_idx: int,
    vo_lang: str | None,
) -> int | None:
    """Index of the cell immediately preceding a voiceover cell.

    Walks backward over narrative cells (other voiceover/notes cells) and
    over cells of a conflicting language, returning the first cell that can
    serve as a positional anchor. A j2 cell *is* eligible: a voiceover
    authored after a mid-group j2 macro must anchor to it, not skip over it
    onto the content cell above — otherwise the merge re-inserts the
    voiceover *before* the j2 and the round-trip is not byte-identical
    (#247). The title macro is one such j2 cell and is given the dedicated
    ``tm:`` anchor by :func:`anchor_token` (#246). Returns ``None`` if the
    voiceover has no eligible cell above it.
    """
    for i in range(voiceover_idx - 1, -1, -1):
        meta = cells[i].metadata
        if meta.is_narrative:
            continue
        if meta.lang is not None and vo_lang is not None and meta.lang != vo_lang:
            continue
        return i
    return None
