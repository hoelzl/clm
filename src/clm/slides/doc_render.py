"""Render the sequence core's result into files, byte by byte.

Phase B of ``docs/claude/design/sync-sequence-core.md`` (§6). The core
(:mod:`clm.slides.doc_core`) ends a pass with ONE merged sequence per half:
``Outcome.halves[lang]``, a list of core cells, each carrying in ``mark`` the
parsed cell it came from. This module turns that into the text of each file
(deck and companion, per half). Nothing in ``clm slides sync`` calls it yet;
``scripts/sync_shadow.py`` runs it on real history.

**Bytes come from cells, never from text transforms** (§6.1). Every cell a
half holds after the pass is one of:

1. its own cell, byte for byte, when nothing about it changed;
2. its own cell, adjusted: the ``slide_id`` attribute an id gained on the
   other half, the ``tags`` attribute the merge decided;
3. the other half's cell, byte for byte, for a shared cell that half added or
   changed (shared cells are byte-identical on both halves modulo the id and
   the tags, which ``doc_sequence.canonical`` leaves out), with this half's
   id and the merged tags;
4. a twin built from the other half's cell (:func:`doc_apply.twin_lines`,
   the ``translate_new`` writer) with the translator's text, for a localized
   cell new on one half and for the twin of a cell one half forked.

**Separators** (§6.2): a cell's trailing blank lines are part of its
``lines`` and so part of its content. A cell keeps its own separator wherever
it goes, a copied shared cell brings the separator of the cell it copies
(which is the same content), and a twin keeps the separator of the cell it is
built from. Nothing here adds or trims blank lines.

**Preambles**: each file keeps its own. A file this pass creates (a companion
only the other half had) takes the other half's, as ``doc_write.DeckEmitter``
does. A file that ends up with no cells keeps its preamble; a file that never
existed and gets none stays absent (``None``).

Ids: the core compares bare ids; a header carries the id verbatim, possibly
with the ``!`` preserve marker. A cell keeps its own id bytes when its id is
the merged one, and takes the bytes of the cell that carries the merged id
otherwise (an id gained on the other half).

Tags: the merged tags of a member always equal one half's tags (a 3-way
merge either takes a half's change or asks), so their bytes, order included,
are taken from a cell that has them: its own, else the cell its content or
its id comes from. Only when none has them is the attribute written fresh,
in sorted order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from attrs import evolve

from clm.slides.bilingual_doc import Lang, SideCell
from clm.slides.doc_apply import header_with_slide_id_of, twin_lines
from clm.slides.doc_core import PARTS, SIDES, Cell
from clm.slides.doc_lenses import bare_slide_id
from clm.slides.doc_sequence import canonical
from clm.slides.sync_writeback import set_header_tags

__all__ = ["FileKey", "RenderError", "Translator", "render"]

#: ``(lang, part)``: one file of one half.
FileKey = tuple[str, str]

#: The text of a localized cell on ``target`` that the other half has and
#: this half does not: ``(source cell, target lang) -> body`` in the form a
#: ``translate_new`` answer takes (``SideCell.decision_body``).
Translator = Callable[[SideCell, str], str]


class RenderError(Exception):
    """The core's result names a cell the renderer cannot produce bytes for
    (a bug in the core or its adapter, never bad input)."""


def render(
    halves: Mapping[str, Sequence[Cell]],
    preambles: Mapping[FileKey, tuple[str, ...] | None],
    inputs: Mapping[str, Sequence[SideCell]],
    translate: Translator,
    comment_token: str,
) -> dict[FileKey, str | None]:
    """The text of every file after a pass.

    ``halves`` is ``Outcome.halves``; ``preambles`` holds each file's
    preamble lines as read (``None`` for a file that does not exist);
    ``inputs`` the parsed cells of each half the pass read (both files), so
    a shared cell's content can be found in the half that has it.
    """
    by_content: dict[str, list[SideCell]] = {}
    by_id: dict[str, list[SideCell]] = {}
    for lang in SIDES:
        for sc in inputs.get(lang, ()):
            by_content.setdefault(canonical(sc), []).append(sc)
            sid = bare_slide_id(sc.slide_id)
            if sid is not None:
                by_id.setdefault(sid, []).append(sc)

    def tag_source(cell: Cell, *candidates: SideCell | None) -> tuple[str, ...]:
        """The merged tags in the order of a cell that has them."""
        pool = [*candidates, *by_id.get(cell.sid or "", [])]
        if not cell.localized:
            pool += by_content.get(cell.body, [])
        for sc in pool:
            if sc is not None and frozenset(sc.tags) == cell.tags:
                return sc.tags
        return tuple(sorted(cell.tags))

    def with_aspects(
        lines: tuple[str, ...],
        origin: SideCell,
        own: SideCell | None,
        cell: Cell,
        *candidates: SideCell | None,
    ) -> tuple[str, ...]:
        """``lines`` (whose header carries ``origin``'s id and tags) with the
        merged id and tags."""
        header = lines[0]
        id_holder = _id_holder(cell, own, candidates, by_id)
        if id_holder is None:
            if origin.slide_id is not None:
                header = header_with_slide_id_of(header, "")
        elif id_holder.slide_id != origin.slide_id:
            header = header_with_slide_id_of(header, id_holder.header)
        if frozenset(origin.tags) != cell.tags:
            header = set_header_tags(header, tag_source(cell, own, *candidates))
        return (header, *lines[1:])

    def cell_lines(lang: str, cell: Cell) -> tuple[str, ...]:
        mark = cell.mark
        if isinstance(mark, SideCell):
            own = mark
            if cell.localized or cell.body == canonical(own):
                return with_aspects(own.lines, own, own, cell)
            source = next(iter(by_content.get(cell.body, ())), None)
            if source is None:
                raise RenderError(f"no cell holds the content of {cell.sid or cell.kind}")
            return with_aspects(source.lines, source, own, cell, source)
        if isinstance(mark, tuple) and len(mark) == 2 and isinstance(mark[1], SideCell):
            how, source = mark
            if how == "copy" and not cell.localized:
                return with_aspects(source.lines, source, None, cell, source)
            if how in ("copy", "fork"):
                body = translate(source, lang)
                lines = twin_lines(source, _lang(lang), body, comment_token)
                twin = evolve(source, lines=lines, lang_attr=lang if source.lang_attr else None)
                return with_aspects(twin.lines, source, None, cell, source)
        raise RenderError(f"a cell of the result carries no source: {mark!r}")

    files: dict[FileKey, str | None] = {}
    for lang in SIDES:
        for part in PARTS:
            cells = [c for c in halves.get(lang, ()) if c.part == part]
            preamble = preambles.get((lang, part))
            if preamble is None:
                if not cells:
                    files[(lang, part)] = None
                    continue
                twin = preambles.get((_other(lang), part))
                preamble = twin if twin is not None else ()
            lines = list(preamble)
            for cell in cells:
                lines.extend(cell_lines(lang, cell))
            files[(lang, part)] = "\n".join(lines)
    return files


def _id_holder(
    cell: Cell,
    own: SideCell | None,
    candidates: Sequence[SideCell | None],
    by_id: Mapping[str, list[SideCell]],
) -> SideCell | None:
    """The cell whose ``slide_id`` bytes this cell takes (``None``: no id)."""
    if cell.sid is None:
        return None
    for sc in (own, *candidates, *by_id.get(cell.sid, [])):
        if sc is not None and bare_slide_id(sc.slide_id) == cell.sid:
            return sc
    raise RenderError(f"no cell carries the id {cell.sid}")


def _other(lang: str) -> str:
    return "en" if lang == "de" else "de"


def _lang(lang: str) -> Lang:
    if lang == "de":
        return "de"
    if lang == "en":
        return "en"
    raise RenderError(f"not a language: {lang}")
