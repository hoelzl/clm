"""The recorded cell sequence of a deck in sync (structural-core step 4).

``docs/claude/sync-structural-core-investigation.md`` proposes a structural
core that aligns each half to the **recorded sequence** of cells instead of to
positional keys (its §4.1). This module defines that sequence and when it can
be recorded. Nothing in the engine reads it yet: the ledger stores it beside
the current entries (:mod:`clm.slides.doc_ledger`), so that every deck carries
a base by the time the core is switched over (§9, steps 4 and 5).

The sequence is one list over both files of a deck (the deck file, then the
voiceover companion), in file order. An entry is what the core compares:

* ``sid`` — the cell's bare ``slide_id``, or ``None``;
* ``kind`` — the cell type (``markdown`` / ``code`` / ``j2``);
* ``file`` — ``deck`` or ``companion``, the part the cell sits in;
* ``localized`` — each half holds its own text (a ``lang`` attribute, or a
  ``j2`` header macro, which differs per half by design);
* ``tags`` — sorted; the core merges tags as an aspect of their own;
* ``de`` / ``en`` — the content per half: every byte of the cell except the
  ``slide_id`` and ``tags`` attributes, as a fingerprint
  (:func:`sequence_fingerprint`).

A sequence is only recorded from a deck **in sync**
(:func:`deck_sequence` says why not, otherwise): the bundle parses with no
refusal and no observation, every member has both halves in the same file, the
halves hold each file's cells in one order, a cell's tags agree, and a shared
cell's content is the same on both halves. A recorded sequence is therefore
always a state both halves agreed on, which is what a 3-way merge needs as its
base. ``scripts/sync_shadow.py`` builds the prototype's base with the same
function, so the shadow on real history checks what the ledger records.

Pure: no I/O, no git.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from attrs import frozen

from clm.slides.bilingual_doc import BilingualDeck, SideCell
from clm.slides.doc_lenses import bare_slide_id

__all__ = [
    "FILES",
    "SequenceEntry",
    "canonical",
    "deck_sequence",
    "is_localized",
    "sequence_fingerprint",
]

#: The parts of a deck bundle, in the order the sequence lists them.
FILES = ("deck", "companion")

_ID_ATTR_RE = re.compile(r'\s*slide_id="[^"]*"')
_TAGS_ATTR_RE = re.compile(r"\s*tags=\[[^\]]*\]")


def canonical(cell: SideCell) -> str:
    """A cell's content as the core compares it: every byte the file holds
    for it, except the ``slide_id`` and ``tags`` attributes, which the core
    carries as identity and as an aspect of their own."""
    header = _TAGS_ATTR_RE.sub("", _ID_ATTR_RE.sub("", cell.lines[0]))
    return "\n".join((header, *cell.lines[1:]))


def sequence_fingerprint(cell: SideCell) -> str:
    """The fingerprint of :func:`canonical`, as the ledger records it."""
    return hashlib.sha256(canonical(cell).encode("utf-8")).hexdigest()


def is_localized(cell: SideCell) -> bool:
    """A cell each half holds in its own language. A ``j2`` cell counts: the
    header macros differ per half by design (``header_de`` / ``header_en``)."""
    return cell.lang_attr is not None or cell.cell_type == "j2"


@frozen
class SequenceEntry:
    """One recorded cell (see the module docstring)."""

    sid: str | None
    kind: str
    file: str
    localized: bool
    tags: tuple[str, ...]
    de: str
    en: str


def deck_sequence(
    deck: BilingualDeck | None,
    content: Callable[[SideCell], str] = sequence_fingerprint,
) -> list[SequenceEntry] | str:
    """The deck's cell sequence, or why the deck is not in sync.

    ``content`` maps a cell to what the entry holds per half: the fingerprint
    for the ledger, :func:`canonical` for the prototype, which compares
    contents only for equality either way.
    """
    if deck is None:
        return "refused"
    if deck.observations:
        return "observation:" + deck.observations[0].kind
    out: list[SequenceEntry] = []
    members = list(deck.members())
    for file in FILES:
        here = [m for m in members if m.de is not None and m.de.part == file]
        if any(m.en is None or m.en.part != file for m in here):
            return "one_sided_member"
        if sum(1 for m in members if m.en is not None and m.en.part == file) != len(here):
            return "one_sided_member"
        by_de = sorted(here, key=lambda m: m.de.index)  # type: ignore[union-attr]
        by_en = sorted(here, key=lambda m: m.en.index)  # type: ignore[union-attr]
        if [id(m) for m in by_de] != [id(m) for m in by_en]:
            return "order_differs"
        for m in by_de:
            de, en = m.de, m.en
            assert de is not None and en is not None
            if frozenset(de.tags) != frozenset(en.tags):
                return "tags_differ"
            if is_localized(de) != is_localized(en):
                return "localized_differs"
            if not is_localized(de) and canonical(de) != canonical(en):
                return "shared_differs"
            out.append(
                SequenceEntry(
                    sid=bare_slide_id(de.slide_id) or bare_slide_id(en.slide_id),
                    kind=de.cell_type,
                    file=file,
                    localized=is_localized(de),
                    tags=tuple(sorted(set(de.tags))),
                    de=content(de),
                    en=content(en),
                )
            )
    if any(m.de is None and m.en is None for m in members):  # pragma: no cover - parser invariant
        return "empty_member"
    return out
