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
  (:func:`sequence_fingerprint`);
* ``texts`` — for a localized cell, what the text layer of the sequence core
  compares per half (:class:`TextRecord`: the text without the owner
  attributes, as a fingerprint, its trailing blank lines, and the owner
  attributes). Not part of the entry's equality: it is derived from the same
  bytes as ``de`` / ``en``, and a ledger written before phase C has none.

A sequence is only recorded from a deck **in sync**
(:func:`deck_sequence` says why not, otherwise): the bundle parses with no
refusal and no observation about its cells (differing preambles are no such
observation), every member has both halves in the same file, the
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

from attrs import field, frozen

from clm.slides.bilingual_doc import BilingualDeck, SideCell
from clm.slides.doc_lenses import bare_slide_id

__all__ = [
    "FILES",
    "SequenceEntry",
    "TextRecord",
    "canonical",
    "deck_sequence",
    "is_localized",
    "sequence_fingerprint",
    "separator_of",
    "text_fingerprint",
    "text_of",
    "text_record",
]

#: The parts of a deck bundle, in the order the sequence lists them.
FILES = ("deck", "companion")

_ID_ATTR_RE = re.compile(r'\s*slide_id="[^"]*"')
_TAGS_ATTR_RE = re.compile(r"\s*tags=\[[^\]]*\]")
_OWNER_ATTRS_RE = re.compile(r'\s*(?:for_slide|vo_anchor)="[^"]*"')


def canonical(cell: SideCell) -> str:
    """A cell's content as the core compares it: every byte the file holds
    for it, except the ``slide_id`` and ``tags`` attributes, which the core
    carries as identity and as an aspect of their own."""
    header = _TAGS_ATTR_RE.sub("", _ID_ATTR_RE.sub("", cell.lines[0]))
    return "\n".join((header, *cell.lines[1:]))


def sequence_fingerprint(cell: SideCell) -> str:
    """The fingerprint of :func:`canonical`, as the ledger records it."""
    return hashlib.sha256(canonical(cell).encode("utf-8")).hexdigest()


def text_of(cell: SideCell) -> str:
    """What the text layer compares for a localized cell: :func:`canonical`
    without the owner attributes (``for_slide``, ``vo_anchor``), which are
    merged on their own."""
    content = canonical(cell)
    header, sep, rest = content.partition("\n")
    return _OWNER_ATTRS_RE.sub("", header) + sep + rest


def text_fingerprint(cell: SideCell) -> str:
    """The fingerprint of :func:`text_of`, as the ledger records it."""
    return hashlib.sha256(text_of(cell).encode("utf-8")).hexdigest()


def separator_of(lines: tuple[str, ...] | list[str]) -> int:
    """The number of blank lines a cell's ``lines`` end in (the header,
    ``lines[0]``, never counts)."""
    n = 0
    for line in reversed(lines[1:]):
        if line != "":
            break
        n += 1
    return n


@frozen
class TextRecord:
    """A localized cell's recorded state on one half, as the text layer
    reads it: ``text`` is :func:`text_fingerprint`, ``separator`` the
    trailing blank lines, ``for_slide`` / ``vo_anchor`` verbatim."""

    text: str
    separator: int
    for_slide: str | None
    vo_anchor: str | None


def text_record(cell: SideCell) -> TextRecord:
    return TextRecord(
        text_fingerprint(cell), separator_of(cell.lines), cell.for_slide, cell.vo_anchor
    )


def is_localized(cell: SideCell) -> bool:
    """A cell each half holds in its own language. A ``j2`` cell counts: the
    header macros differ per half by design (``header_de`` / ``header_en``)."""
    return cell.lang_attr is not None or cell.cell_type == "j2"


#: Parse observations that say nothing about the cells: the preambles are
#: not part of the sequence (the sequence core's text layer merges them), so
#: a deck whose halves hold different preambles, a translated README for one,
#: still has a sequence.
_NOT_STRUCTURAL = frozenset({"preamble_divergence"})


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
    #: ``(de, en)`` text records of a localized entry, ``None`` when not known
    texts: tuple[TextRecord, TextRecord] | None = field(default=None, eq=False)


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
    structural = [o for o in deck.observations if o.kind not in _NOT_STRUCTURAL]
    if structural:
        return "observation:" + structural[0].kind
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
                    texts=(text_record(de), text_record(en)) if is_localized(de) else None,
                )
            )
    if any(m.de is None and m.en is None for m in members):  # pragma: no cover - parser invariant
        return "empty_member"
    return out
