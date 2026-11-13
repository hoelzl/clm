"""The text layer of the sequence core (design note §5).

Phase B of ``docs/claude/design/sync-sequence-core.md``. The structural core
(:mod:`clm.slides.doc_core`) settles which cells each half holds, in which
order, with which tags; it treats a localized cell's content as opaque. This
module decides what the core leaves open, on the core's result:

- **the text of a localized cell** present on both halves, compared per half
  with its recorded state: edited on one half is ``translate_edit``
  (answers ``body``, ``keep_twin``), on both halves ``verify_translation``
  (``confirm``), a member with no recorded text ``verify_cold``
  (``confirm``). A localized cell new on one half is the core's
  ``translate_new``, a fork the core's ``fork``; neither is asked here;
- **the owner attributes** (``for_slide``, ``vo_anchor``) of a localized
  cell, merged 3-way like tags: one half changed it, take it; both changed
  it differently, ``conflict_owner`` (``de``, ``en``). The exception is a
  fingerprint anchor (``vo_anchor="fp:…"``) the halves recorded differently
  (it fingerprints a localized cell in each language): a new fingerprint, or
  dropping it, stays on its half. A
  ``for_slide`` that names no id of the merged deck is ``broken_owner``
  (held), as today; a stale ``vo_anchor`` is ``clm validate``'s, since
  placing narration falls back from it;
- **file preambles**, which both halves share: one half changed a preamble,
  ``propagate_preamble``; both identically, ``record_preamble``; both
  differently, ``conflict_preamble`` (``de``, ``en``); a preamble that was
  already diverged at base and moved, ``pending_divergence`` (held).

The result is a :class:`TextPlan` that :func:`clm.slides.doc_render.render`
applies. The text of a cell is "everything the file holds for it except the
id, the tags and the owner attributes", separator included, the same
comparison today's differ makes with the content fingerprint. So re-spacing a
localized cell on one half is a ``translate_edit`` like any other edit of its
text. Answering ``body`` gives the twin the translator's text; its separator
follows the edited cell's only where the edit changed that separator, and is
its own otherwise. With ``keep_twin`` the twin keeps its own bytes. Nothing
else here looks at whitespace.

A row with no answer holds the deck (decision O2), like an open question of
the core: :attr:`TextPlan.held` is then non-empty and nothing may be written.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence

from attrs import define, field, frozen

from clm.core.slide_text.pairing import HEADER_MACRO_RE
from clm.slides.bilingual_doc import SideCell
from clm.slides.doc_core import PARTS, SIDES, Cell
from clm.slides.doc_lenses import bare_slide_id
from clm.slides.doc_sequence import canonical, is_localized

__all__ = [
    "Owners",
    "TITLE_ID",
    "Recorded",
    "TextPlan",
    "TextRow",
    "owners_of",
    "plan_text",
    "recorded_by_half",
    "separator_of",
    "cell_key",
    "text_key",
    "text_of",
]

#: A file: ``(lang, part)``.
FileKey = tuple[str, str]

#: ``(for_slide, vo_anchor)`` of a cell, verbatim.
Owners = tuple[str | None, str | None]

_OWNER_ATTRS_RE = re.compile(r'\s*(?:for_slide|vo_anchor)="[^"]*"')


def text_of(cell: SideCell) -> str:
    """What the text layer compares for a localized cell: its content as the
    core reads it (:func:`clm.slides.doc_sequence.canonical`: every byte but
    the id and the tags) without the owner attributes, which are merged on
    their own."""
    content = canonical(cell)
    header, sep, rest = content.partition("\n")
    return _OWNER_ATTRS_RE.sub("", header) + sep + rest


def separator_of(lines: Sequence[str]) -> int:
    """The number of blank lines a cell's ``lines`` end in (the header,
    ``lines[0]``, never counts)."""
    n = 0
    for line in reversed(lines[1:]):
        if line != "":
            break
        n += 1
    return n


def text_key(cell: SideCell) -> str | None:
    """The key the text layer knows a localized cell by: its bare id, or
    :data:`TITLE_ID` for the deck's title macro (the ``j2`` ``header`` line, which
    carries no id, which the lens anchors to the reserved id, design §3.3)."""
    sid = bare_slide_id(cell.slide_id)
    if sid is not None:
        return sid
    if cell.cell_type == "j2" and cell.part == "deck" and HEADER_MACRO_RE.search(cell.header):
        return TITLE_ID
    return None


def cell_key(cell: Cell) -> str | None:
    """:func:`text_key` of a core cell (by the parsed cell it renders)."""
    if cell.sid is not None or not cell.localized:
        return cell.sid
    mark = cell.mark
    if isinstance(mark, tuple) and len(mark) == 2:
        mark = mark[1]
    return text_key(mark) if isinstance(mark, SideCell) else None


def owners_of(cell: SideCell) -> Owners:
    return (cell.for_slide, cell.vo_anchor)


@frozen
class Recorded:
    """A localized cell's recorded state on one half."""

    text: str
    owners: Owners


def recorded_by_half(
    cells: Mapping[str, Iterable[SideCell]],
) -> dict[tuple[str, str], Recorded]:
    """The recorded state of every id'd localized cell, from each half's
    parsed cells (``lang -> cells``), keyed ``(lang, bare id)``."""
    out: dict[tuple[str, str], Recorded] = {}
    for lang, side in cells.items():
        for sc in side:
            sid = text_key(sc)
            if sid is not None and is_localized(sc):
                out[(lang, sid)] = Recorded(text_of(sc), owners_of(sc))
    return out


@frozen
class TextRow:
    """One row of the text layer. ``handle`` is ``id:<sid>`` for a cell and
    ``preamble:<part>`` for a file preamble; ``side`` the half whose change
    the row carries (``None``: both, or none)."""

    kind: str
    handle: str
    side: str | None = None
    detail: str = ""


#: The answer to a question row (``None``: no answer, the deck is held).
Judge = Callable[[TextRow], str | None]

#: The answers each question row takes; the first is what a judge that
#: always agrees answers.
ANSWERS: dict[str, tuple[str, ...]] = {
    "translate_edit": ("body", "keep_twin"),
    "verify_translation": ("confirm",),
    "verify_cold": ("confirm",),
    "conflict_owner": ("de", "en"),
    "conflict_preamble": ("de", "en"),
}

#: The reserved owner of the title narration: it names the deck's header
#: macro, which carries no ``slide_id`` (``doc_lenses``, design §3.3).
TITLE_ID = "title"

#: Rows that hold the deck whatever is answered: they need the author.
HOLDING = frozenset({"broken_owner", "pending_divergence"})


@define
class TextPlan:
    """What the text layer decided; :func:`clm.slides.doc_render.render`
    applies it."""

    #: question rows with their answers (``None``: unanswered)
    asked: list[tuple[TextRow, str | None]] = field(factory=list)
    #: rows that need no answer (propagations, records)
    mechanical: list[TextRow] = field(factory=list)
    #: rows that hold the deck: unanswered questions and :data:`HOLDING` rows
    held: list[TextRow] = field(factory=list)
    #: ``(lang, sid) -> source``: the cell on ``lang`` takes the translator's
    #: text of ``source`` (its edited twin)
    retext: dict[tuple[str, str], SideCell] = field(factory=dict)
    #: ``(lang, sid) -> n``: the retexted cell ends in ``n`` blank lines
    #: (the edit re-spaced its source); absent, it keeps its own
    separators: dict[tuple[str, str], int] = field(factory=dict)
    #: ``(lang, sid) -> owners`` for every localized cell the pass settled
    owners: dict[tuple[str, str], Owners] = field(factory=dict)
    #: ``(lang, part) -> lines``: files whose preamble becomes these lines
    preambles: dict[FileKey, tuple[str, ...]] = field(factory=dict)


def plan_text(
    halves: Mapping[str, Sequence[Cell]],
    recorded: Mapping[tuple[str, str], Recorded],
    preambles: Mapping[FileKey, tuple[str, ...] | None],
    base_preambles: Mapping[FileKey, tuple[str, ...] | None],
    judge: Judge,
) -> TextPlan:
    """The text layer over the core's merged ``halves`` (``Outcome.halves``).

    ``recorded`` is the recorded state of the localized cells
    (:func:`recorded_by_half`), ``preambles`` / ``base_preambles`` each
    file's preamble now and as recorded (``None``: no such file).
    """
    plan = TextPlan()

    def ask(row: TextRow) -> str | None:
        answer = judge(row)
        if answer is not None and answer not in ANSWERS[row.kind]:
            answer = None
        plan.asked.append((row, answer))
        if answer is None:
            plan.held.append(row)
        return answer

    own: dict[str, dict[str, SideCell]] = {lang: {} for lang in SIDES}
    present: dict[str, dict[str, SideCell]] = {lang: {} for lang in SIDES}
    ids: set[str] = set()
    for lang in SIDES:
        for cell in halves.get(lang, ()):
            if cell.sid is not None:
                ids.add(cell.sid)
            key = cell_key(cell)
            if not cell.localized or key is None:
                continue
            mark = cell.mark
            if isinstance(mark, SideCell):
                own[lang][key] = mark
                present[lang][key] = mark
            elif isinstance(mark, tuple) and len(mark) == 2 and isinstance(mark[1], SideCell):
                present[lang][key] = mark[1]  # the twin's source, its owners

    order = {sid: None for lang in SIDES for sid in present[lang]}
    for sid in order:
        handle = f"id:{sid}"
        _text(plan, ask, sid, handle, own, recorded)
        merged = _owners(ask, sid, handle, present, recorded)
        if merged is None:
            continue
        broken: set[str] = set()
        for lang, owners in merged.items():
            plan.owners[(lang, sid)] = owners
            target = _bare_or_none(owners[0])
            if target is not None and target != TITLE_ID and target not in ids:
                broken.add(target)
        for target in sorted(broken):
            plan.held.append(
                TextRow("broken_owner", handle, None, f"{target!r} names no id of the deck")
            )

    for part in PARTS:
        _preamble(plan, ask, part, preambles, base_preambles)
    return plan


def _text(
    plan: TextPlan,
    ask: Callable[[TextRow], str | None],
    sid: str,
    handle: str,
    own: Mapping[str, Mapping[str, SideCell]],
    recorded: Mapping[tuple[str, str], Recorded],
) -> None:
    cells = {lang: own[lang].get(sid) for lang in SIDES}
    if any(c is None for c in cells.values()):
        return  # new or forked on one half: the core's translate_new / fork
    entries = {lang: recorded.get((lang, sid)) for lang in SIDES}
    if any(e is None for e in entries.values()):
        ask(TextRow("verify_cold", handle, None, "no recorded text"))
        return
    moved = [lang for lang in SIDES if text_of(_cell(cells[lang])) != _entry(entries[lang]).text]
    if not moved:
        return
    if len(moved) == 2:
        ask(TextRow("verify_translation", handle, None, "edited on both halves"))
        return
    lead = moved[0]
    if ask(TextRow("translate_edit", handle, lead, f"edited on {lead}")) == "body":
        source = _cell(cells[lead])
        plan.retext[(_other(lead), sid)] = source
        now = separator_of(source.lines)
        if now != separator_of(_entry(entries[lead]).text.split("\n")):
            plan.separators[(_other(lead), sid)] = now


def _owners(
    ask: Callable[[TextRow], str | None],
    sid: str,
    handle: str,
    present: Mapping[str, Mapping[str, SideCell]],
    recorded: Mapping[tuple[str, str], Recorded],
) -> dict[str, Owners] | None:
    """Each half's owners after the merge (``None``: held).

    Merged 3-way per attribute: a value one half changed reaches the other.
    Except where the halves already recorded different values, which a
    fingerprint anchor (``vo_anchor="fp:…"``) does when the cell before the
    narration is localized (it fingerprints that cell's bytes in each
    language). Then a half that moves to another fingerprint, or drops its
    anchor, keeps that to itself; only a value that names the same thing on
    both halves (an ``id:`` or ``tm:`` anchor, ``for_slide``) reaches the
    other half. A fingerprint of a shared cell is the same on both halves
    and propagates like any value."""
    now = {lang: owners_of(sc) for lang in SIDES if (sc := present[lang].get(sid)) is not None}
    if len(now) == 1:
        return dict(now)
    out: dict[str, list[str | None]] = {lang: [] for lang in SIDES}
    for i in range(2):
        values = {lang: now[lang][i] for lang in SIDES}
        base = {
            lang: entry.owners[i]
            for lang in SIDES
            if (entry := recorded.get((lang, sid))) is not None
        }
        moved = [lang for lang in SIDES if lang in base and values[lang] != base[lang]]
        own = values["de"] == values["en"] or len(base) < 2 or not moved
        if (
            not own
            and base["de"] != base["en"]
            and all(values[lang] is None or _per_half(values[lang]) for lang in moved)
        ):
            own = True
        if own:
            for lang in SIDES:
                out[lang].append(values[lang])
            continue
        if len(moved) == 1:
            taken = values[moved[0]]
        else:
            answer = ask(
                TextRow(
                    "conflict_owner",
                    handle,
                    None,
                    f"{('for_slide', 'vo_anchor')[i]}: de {values['de']!r}, en {values['en']!r}",
                )
            )
            if answer is None:
                return None
            taken = values[answer]
        for lang in SIDES:
            out[lang].append(taken)
    return {lang: (v[0], v[1]) for lang, v in out.items()}


def _per_half(value: str | None) -> bool:
    """A fingerprint anchor: language-specific by construction."""
    return value is not None and value.startswith("fp:")


def _preamble(
    plan: TextPlan,
    ask: Callable[[TextRow], str | None],
    part: str,
    preambles: Mapping[FileKey, tuple[str, ...] | None],
    base_preambles: Mapping[FileKey, tuple[str, ...] | None],
) -> None:
    now = {lang: preambles.get((lang, part)) for lang in SIDES}
    base = {lang: base_preambles.get((lang, part)) for lang in SIDES}
    if now["de"] is None or now["en"] is None:
        return  # a file one half lacks: the renderer gives it the twin's
    if base["de"] is None and base["en"] is None:
        return  # both files are new: each keeps its own
    moved = [lang for lang in SIDES if base[lang] is not None and now[lang] != base[lang]]
    handle = f"preamble:{part}"
    if not moved:
        return
    if now["de"] == now["en"]:
        plan.mechanical.append(TextRow("record_preamble", handle))
        return
    if len(moved) == 2:
        answer = ask(TextRow("conflict_preamble", handle, None, "changed on both halves"))
        if answer is not None:
            plan.preambles[(_other(answer), part)] = _lines(now[answer])
        return
    lead = moved[0]
    if base["de"] != base["en"]:
        plan.held.append(
            TextRow("pending_divergence", handle, lead, "the preambles differed at base")
        )
        return
    plan.mechanical.append(TextRow("propagate_preamble", handle, lead))
    plan.preambles[(_other(lead), part)] = _lines(now[lead])


def _bare_or_none(value: str | None) -> str | None:
    return bare_slide_id(value) if value else None


def _cell(cell: SideCell | None) -> SideCell:
    assert cell is not None
    return cell


def _entry(entry: Recorded | None) -> Recorded:
    assert entry is not None
    return entry


def _lines(lines: tuple[str, ...] | None) -> tuple[str, ...]:
    assert lines is not None
    return lines


def _other(lang: str) -> str:
    return "en" if lang == "de" else "de"
