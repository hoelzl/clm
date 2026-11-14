"""One pass of the sequence core over a deck bundle (design note §4).

Phase C of ``docs/claude/design/sync-sequence-core.md``. ``clm slides sync
report`` and ``apply`` run this instead of the positional engine when
``CLM_SYNC_CORE=sequence`` is set (``clm.cli.commands.slides.sync_seq``).
Both run the same function, so the two ends cannot disagree:

```
read     each file of each half on its own (doc_lenses.read_file)
base     the ledger section's recorded sequence (doc_sequence)
merge    the structural core (doc_core.merge)
text     the text layer (doc_text.plan_text)
render   both halves' files (doc_render.render)
assert   the rendered halves are in sync (deck_sequence)
```

``report`` is a pass with no answers; ``apply`` a pass with the decision
document's answers, which writes the rendered files and records the result
when nothing is left open.

**Handles** (design §7.1). A row names what it is about by a handle the
agent echoes:

- ``id:<sid>`` — a cell with a ``slide_id`` (``id:title`` for the deck's
  title macro, which carries none);
- ``seq:<n>`` — the n-th entry (0-based) of the recorded sequence, for an
  un-id'd recorded cell;
- ``new:<lang>:<file>:<n>`` — the n-th cell (0-based) of that file on that
  half, for a cell the record does not know, and for the candidates of an
  ``identify`` question;
- ``preamble:<file>`` — a file's preamble;
- ``order`` — the deck's cell order.

``seq:`` and ``new:`` handles mean something only against the files and the
ledger section a report was made from; ``report_id`` pins exactly those.

**What an open question holds** (decision O2): the whole deck. A pass with
any open question writes nothing and records nothing.

**A deck with no recorded sequence** (the ledger section predates step 4,
or the deck was never recorded since): when its halves are in sync now,
they are the base; otherwise the deck as committed at ``HEAD`` is, when that
state was in sync. Failing both, the pass refuses the deck. Either way the
positional ledger's member entries say which localized texts were verified:
a cell whose fingerprint they do not match counts as edited, one they do not
know as never verified (``verify_cold``).

**Trust.** The text layer compares a localized cell with its recorded text.
A sequence recorded before phase C carries no text records
(:class:`clm.slides.doc_sequence.TextRecord`); then a half whose cell is
unchanged since the record counts as recorded exactly, and a changed one
counts as an edit of its text with its owners and separator as they are now.
The next pass that records the deck writes the text records.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from attrs import define, evolve, field, frozen

from clm.slides import doc_core
from clm.slides.bilingual_doc import BilingualDeck, SideCell
from clm.slides.doc_apply import Decision
from clm.slides.doc_identity import lines_fingerprint
from clm.slides.doc_ledger import DeckLedger, TopicLedger, record_deck_snapshot
from clm.slides.doc_lenses import (
    LoadedBundle,
    bare_slide_id,
    load_bundle,
    parse_bundle,
    read_file,
)
from clm.slides.doc_render import FileKey, RenderError, render
from clm.slides.doc_sequence import (
    SequenceEntry,
    deck_sequence,
    is_localized,
    separator_of,
    sequence_fingerprint,
    text_fingerprint,
)
from clm.slides.doc_text import TITLE_ID, Recorded, TextRow, owners_of, plan_text, text_key
from clm.slides.doc_write import write_changed_files

__all__ = [
    "CORE_ANSWERS",
    "ENV_VAR",
    "MODEL_ACTIONS",
    "Pass",
    "Rejection",
    "Row",
    "land",
    "pair_payload",
    "read_files",
    "run_pass",
    "selected",
]

#: The switch of phase C: ``sequence`` runs ``report`` / ``apply`` (and the
#: MCP sync tool) on the sequence core; unset or ``positional``, today's engine.
ENV_VAR = "CLM_SYNC_CORE"


def selected() -> bool:
    """Is the sequence core selected? Raises :class:`ValueError` on a value
    that names no engine."""
    value = os.environ.get(ENV_VAR, "").strip().lower()
    if value in ("", "positional"):
        return False
    if value == "sequence":
        return True
    raise ValueError(f"{ENV_VAR}={value!r}: expected 'sequence' or 'positional'")


SIDES = doc_core.SIDES
PARTS = doc_core.PARTS

#: The answers each question of the core takes (``identify`` takes its
#: candidates' handles and ``removed``). Row names follow today's report:
#: the core's ``order`` is ``order_decision``.
CORE_ANSWERS: dict[str, tuple[str, ...]] = {
    "order_decision": ("de", "en"),
    "conflict_shared": ("de", "en"),
    "conflict_copies": ("de", "en", "distinct"),
    "conflict_tags": ("de", "en"),
    "conflict_part": ("de", "en"),
    "remove_vs_edit": ("remove", "keep"),
    "remove_localized_side": ("remove",),
    "translate_new": ("body",),
    "fork": ("body",),
    "unify": ("accept",),
}

#: The text layer's questions and their answers (``doc_text.ANSWERS``).
TEXT_ANSWERS: dict[str, tuple[str, ...]] = {
    "translate_edit": ("body", "keep_twin"),
    "verify_translation": ("confirm",),
    "verify_cold": ("confirm",),
    "conflict_owner": ("de", "en"),
    "conflict_preamble": ("de", "en"),
}

#: Questions a translator answers (the report's ``needs_model``); every
#: other open question needs the author or an agent (``needs_agent``).
MODEL_ACTIONS = frozenset(
    {"translate_new", "translate_edit", "fork", "verify_translation", "verify_cold"}
)

#: Answers that carry text: the decision's ``body`` is the twin's text.
_BODY_ANSWER = "body"

#: A recorded text that matches no cell: the half's text counts as edited.
_UNKNOWN_TEXT = "<not recorded>"


@frozen
class Row:
    """One row of a pass: a question (``answers`` non-empty) or a change
    the pass makes on its own (``answers`` empty).

    ``side`` is, for a question, the half whose change it carries (``None``:
    both, or none); for a change, the half it writes.
    """

    action: str
    key: str
    side: str | None = None
    detail: str = ""
    answers: tuple[str, ...] = ()
    #: the answer this pass used; ``None`` on an open question
    answer: str | None = None
    #: a row no answer resolves: the author edits the files
    manual: bool = False

    @property
    def is_question(self) -> bool:
        return bool(self.answers) or self.manual

    @property
    def is_open(self) -> bool:
        return self.manual or (bool(self.answers) and self.answer is None)

    def payload(self) -> dict[str, object]:
        out: dict[str, object] = {
            "key": self.key,
            "action": self.action,
            "side": self.side,
            "detail": self.detail,
            "answers": list(self.answers),
            "resolution": "manual" if self.manual else "decision" if self.answers else "mechanical",
        }
        if self.is_question:
            out["needs"] = "model" if self.action in MODEL_ACTIONS else "agent"
            out["answer"] = self.answer
        return out


@frozen
class Rejection:
    """A decision the pass could not use."""

    key: str
    action: str | None
    reason: str


@define
class Pass:
    """The result of :func:`run_pass`."""

    #: why the deck cannot be synced by the sequence core (nothing else is set)
    refusal: str | None = None
    #: a renderer assertion failed: a defect, never bad input (§6.4)
    error: str | None = None
    rows: list[Row] = field(factory=list)
    rejected: list[Rejection] = field(factory=list)
    #: each file's text as read (``None``: no such file)
    files: dict[FileKey, str | None] = field(factory=dict)
    #: each file's text after the pass; ``None`` when the pass holds the deck
    rendered: dict[FileKey, str | None] | None = None

    @property
    def held(self) -> bool:
        return any(row.is_open for row in self.rows)

    @property
    def changed(self) -> set[FileKey]:
        if self.rendered is None:
            return set()
        return {k for k, text in self.rendered.items() if text != self.files.get(k)}

    @property
    def is_clean(self) -> bool:
        return self.refusal is None and self.error is None and not self.rows and not self.changed

    @property
    def needs_model(self) -> bool:
        return any(r.is_open and r.action in MODEL_ACTIONS for r in self.rows)

    @property
    def needs_agent(self) -> bool:
        return (
            self.refusal is not None
            or self.error is not None
            or any(r.is_open and r.action not in MODEL_ACTIONS for r in self.rows)
        )


def _paths(bundle: LoadedBundle) -> dict[FileKey, Path | None]:
    return {
        ("de", "deck"): bundle.de_path,
        ("en", "deck"): bundle.en_path,
        ("de", "companion"): bundle.de_companion_path,
        ("en", "companion"): bundle.en_companion_path,
    }


def read_files(bundle: LoadedBundle) -> dict[FileKey, str | None]:
    """Each file of the bundle as :func:`clm.slides.doc_lenses.load_bundle`
    reads it."""
    return {
        key: path.read_text(encoding="utf-8") if path is not None else None
        for key, path in _paths(bundle).items()
    }


def run_pass(
    bundle: LoadedBundle,
    deck_ledger: DeckLedger | None,
    decisions: Sequence[Decision] = (),
    files: Mapping[FileKey, str | None] | None = None,
) -> Pass:
    """One pass over ``bundle`` against its ledger section, with
    ``decisions`` as the answers (none: a report). ``files`` are the
    bundle's files as read (default: read them now)."""
    result = Pass(files=dict(files) if files is not None else read_files(bundle))
    if bundle.outcome.refusal is not None:
        result.refusal = bundle.outcome.refusal.render()
        return result
    token = bundle.comment_token

    # -- read: each file of each half on its own --------------------------------
    preambles: dict[FileKey, tuple[str, ...] | None] = {}
    inputs: dict[str, list[SideCell]] = {}
    where: dict[int, tuple[str, str, int]] = {}  # id(cell) -> (lang, part, index)
    for lang in SIDES:
        inputs[lang] = []
        for part in PARTS:
            text = result.files.get((lang, part))
            if text is None:
                preambles[(lang, part)] = None
                continue
            preamble, cells = read_file(text, lang, part, token)  # type: ignore[arg-type]
            preambles[(lang, part)] = preamble
            for n, sc in enumerate(cells):
                where[id(sc)] = (lang, part, n)
            inputs[lang].extend(cells)

    # -- base: the recorded sequence ---------------------------------------------
    sequence: list[SequenceEntry] | None = deck_ledger.sequence if deck_ledger else None
    base_preambles: dict[FileKey, object | None]
    #: ``(lang, text key) -> "cold" | "untrusted"`` for a base taken from files
    trust: dict[tuple[str, str], str] = {}
    if sequence is None:
        based = _file_base(bundle.outcome.deck, deck_ledger) or _committed_base(bundle, deck_ledger)
        if based is None:
            why = deck_sequence(bundle.outcome.deck)
            result.refusal = (
                "the ledger records no cell sequence for this deck, its halves are "
                f"not in sync ({why}), and its last commit is not in sync either: "
                "finish the pending items with the positional engine (unset "
                "CLM_SYNC_CORE), or `clm slides sync record` the deck once its halves "
                "agree"
            )
            if _companion_order_only(bundle.outcome.deck):
                result.refusal = (
                    "the two voiceover companions list their narration cells in different "
                    "orders. The positional engine attaches narration by `for_slide` and "
                    "accepts that; the sequence core keeps a companion's cells in one "
                    "order on both halves. Reorder one companion to match the other, "
                    "then sync again"
                )
            return result
        sequence, base_preambles, trust = based
    else:
        base_preambles = dict(deck_ledger.preamble_fps) if deck_ledger else {}
    base = doc_core.base_of(sequence)

    def handle_of_cell(sc: SideCell) -> str:
        key = text_key(sc)
        if key is not None:
            return f"id:{key}"
        lang, part, n = where[id(sc)]
        return f"new:{lang}:{part}:{n}"

    # -- answers -----------------------------------------------------------------
    used: set[int] = set()
    asked: dict[str, list[str]] = {}  # handle -> "<action> (<answers>)" asked there

    def fits(d: Decision, answers: tuple[str, ...]) -> str | None:
        if d.body is not None and _BODY_ANSWER in answers and d.choice in (None, _BODY_ANSWER):
            return _BODY_ANSWER
        if d.choice is not None and d.choice in answers and d.choice != _BODY_ANSWER:
            return d.choice
        return None

    def lookup(
        action: str, key: str, answers: tuple[str, ...], side: str | None = None
    ) -> str | None:
        """The answer ``decisions`` give the row ``(action, key)``. A
        decision that names no action answers the first row on its handle
        it fits; one that names the action and does not fit is rejected.
        ``side`` is an ``identify`` row's half: a decision naming the other
        half does not answer it."""
        asked.setdefault(key, []).append(f"{action} ({', '.join(answers)})")
        for i, d in enumerate(decisions):
            if i in used or d.key != key or d.action not in (None, action):
                continue
            if side is not None and d.side not in (None, side):
                continue
            got = fits(d, answers)
            if got is not None:
                used.add(i)
                return got
            if d.action == action:
                used.add(i)
                result.rejected.append(
                    Rejection(
                        key,
                        action,
                        f"{action} takes {', '.join(answers)}"
                        + (" (with `body`: the text)" if _BODY_ANSWER in answers else ""),
                    )
                )
                return None
        return None

    core_rows: list[Row] = []
    #: open ``identify`` rows read as ``removed`` for the moment, to list the
    #: ones after them: ``(key, side)``
    provisional: set[tuple[str, str]] = set()

    def core_answer(q: doc_core.Question) -> object:
        action = "order_decision" if q.kind == "order" else q.kind
        if q.kind == "order":
            key, detail = "order", "both halves reordered the same cells"
        elif q.member is not None and q.member < len(base):
            entry = sequence[q.member]
            key = f"id:{entry.sid}" if entry.sid is not None else f"seq:{q.member}"
            detail = f"{entry.kind} cell in the {entry.file}"
        else:
            sc = q.mark if isinstance(q.mark, SideCell) else None
            key = handle_of_cell(sc) if sc is not None else f"member:{q.member}"
            detail = f"{sc.cell_type} cell" if sc is not None else ""
        if q.kind == "identify":
            assert q.side is not None
            cands = {handle_of_cell(inputs[q.side][j]): j for j in q.candidates}
            answers = (*cands, "removed")
            detail = f"which cell on {q.side} is this recorded cell"
            got = lookup(action, key, answers, q.side)
            core_rows.append(Row(action, key, q.side, detail, answers, got))
            if got is None:
                return "removed" if (key, q.side) in provisional else None
            return "removed" if got == "removed" else cands[got]
        if q.kind == "conflict_copies" and q.twin is not None and q.twin < len(base):
            detail = f"de edited this copy, en edited seq:{q.twin}"
        answers = CORE_ANSWERS[action]
        got = lookup(action, key, answers)
        core_rows.append(Row(action, key, q.side, detail, answers, got))
        if got is None:
            return None
        return "distinct" if got == "distinct" else got

    de_cells = doc_core.cells_of(inputs["de"], sequence_fingerprint)
    en_cells = doc_core.cells_of(inputs["en"], sequence_fingerprint)
    out = doc_core.merge(base, de_cells, en_cells, core_answer)
    if any(q.kind == "identify" for q in out.opened):
        # The core stops at the first open ``identify``: what follows rests on
        # its answer. So that one decision document can answer them all, list
        # the others too: run again with each open one read as ``removed``
        # (which takes no candidate from the next), keeping the ``identify``
        # rows only. The pass holds the deck either way.
        first, rejected = out, len(result.rejected)
        while True:
            rows = [r for r in core_rows if r.action == "identify"]
            fresh = {(r.key, r.side or "") for r in rows if r.is_open} - provisional
            if not fresh:
                break
            provisional |= fresh
            core_rows.clear()
            del result.rejected[rejected:]
            asked.clear()
            used.clear()
            doc_core.merge(base, de_cells, en_cells, core_answer)
        core_rows[:] = rows
        out = first
    result.rows.extend(core_rows)

    # -- text layer ----------------------------------------------------------------
    recorded = _recorded(sequence, out, inputs, trust)
    text_rows: list[Row] = []

    def judge(row: TextRow) -> str | None:
        answers = TEXT_ANSWERS[row.kind]
        got = lookup(row.kind, row.handle, answers)
        text_rows.append(Row(row.kind, row.handle, row.side, row.detail, answers, got))
        return got

    plan = plan_text(
        out.halves,
        recorded,
        preambles,
        base_preambles,
        judge,
        text_view=text_fingerprint,
        preamble_view=_preamble_fp,
    )
    result.rows.extend(text_rows)
    for row in plan.held:
        if row.kind not in TEXT_ANSWERS:  # broken_owner, pending_divergence
            result.rows.append(Row(row.kind, row.handle, row.side, row.detail, manual=True))
    for row in plan.mechanical:
        result.rows.append(Row(row.kind, row.handle, _target(row.side), row.detail))
    for i, d in enumerate(decisions):
        if i not in used:
            there = asked.get(d.key)
            reason = (
                f"fits no row on this handle: {'; '.join(there)}"
                if there
                else "no row of this pass has this handle"
            )
            result.rejected.append(Rejection(d.key, d.action, reason))
    if not out.opened:  # the core settled the structure: list what it changes
        result.rows.extend(_changes(out, inputs, handle_of_cell, sequence, plan.owners))
    if result.held or out.opened:
        return result

    # -- render and assert ---------------------------------------------------------
    def translate(source: SideCell, lang: str) -> str:
        key = handle_of_cell(source)
        for d in decisions:
            if d.key == key and d.body is not None:
                return d.body
        raise RenderError(f"{key}: no text for the {lang} twin")

    try:
        rendered = render(
            out.halves, preambles, inputs, translate, token, plan, content=sequence_fingerprint
        )
    except RenderError as exc:
        result.error = f"the renderer could not produce the files: {exc}"
        return result
    agreed = parse_bundle(
        _text(rendered, "de", "deck"),
        _text(rendered, "en", "deck"),
        rendered.get(("de", "companion")),
        rendered.get(("en", "companion")),
        comment_token=token,
    )
    in_sync = deck_sequence(agreed.deck)
    if isinstance(in_sync, str):
        result.error = (
            f"the rendered halves are not in sync ({in_sync}); nothing was written. "
            "This is a defect of the sequence core: please open an issue with the deck, "
            "and sync it with the positional engine meanwhile (unset CLM_SYNC_CORE)"
        )
        return result
    result.rendered = rendered
    return result


#: A base taken from files: its sequence, its preambles' fingerprints, and
#: what the positional ledger says about each localized cell's text.
_FileBase = tuple[list[SequenceEntry], dict[FileKey, object | None], dict[tuple[str, str], str]]


def _file_base(deck: BilingualDeck | None, deck_ledger: DeckLedger | None) -> _FileBase | None:
    """``deck`` as the base of a deck whose ledger records no sequence (one
    not recorded since step 4), when its halves are in sync.

    Its structure is a state both halves agree on. Its localized texts are
    only as trusted as the positional engine's ledger says (design §2: the
    member entries of id'd cells carry the trust): a cell whose recorded
    body fingerprint matches is recorded as it is (its header attributes are
    the positional engine's own rows, and it reports a deck clean whose
    owner attributes moved since the record), one whose body differs
    counts as edited since the record (``untrusted``), one with no entry as
    never verified (``cold``, asked ``verify_cold``). The preambles are the
    ledger's where it has them."""
    from clm.slides.doc_identity import body_fingerprint, content_fingerprint
    from clm.slides.doc_ledger import baseline_from_ledger

    sequence = deck_sequence(deck)
    if deck is None or isinstance(sequence, str):
        return None
    members = baseline_from_ledger(deck_ledger).members if deck_ledger is not None else {}
    trust: dict[tuple[str, str], str] = {}
    for member in deck.members():
        for lang, sc in (("de", member.de), ("en", member.en)):
            key = text_key(sc) if sc is not None else None
            if sc is None or key is None or not is_localized(sc):
                continue
            entry = members.get(f"id:{key}")
            if entry is None:
                trust[(lang, key)] = "cold"
            elif sc.cell_type == "j2":  # the title macro: its text is its header line
                if entry.side_fp(lang) != content_fingerprint(sc):  # type: ignore[arg-type]
                    trust[(lang, key)] = "untrusted"
            elif (entry.de_body_fp if lang == "de" else entry.en_body_fp) != body_fingerprint(sc):
                trust[(lang, key)] = "untrusted"
    preambles: dict[FileKey, object | None] = {
        ("de", "deck"): _preamble_fp(deck.de_deck_preamble),
        ("en", "deck"): _preamble_fp(deck.en_deck_preamble),
        ("de", "companion"): _preamble_fp(deck.de_companion_preamble),
        ("en", "companion"): _preamble_fp(deck.en_companion_preamble),
    }
    if deck_ledger is not None and deck_ledger.preamble_fps:
        preambles.update(deck_ledger.preamble_fps)
    return sequence, preambles, trust


def _companion_order_only(deck: BilingualDeck | None) -> bool:
    """Do the halves differ only in the order of their companions' cells?"""
    if deck is None or deck.observations:
        return False
    members = list(deck.members())
    differs = set()
    for part in PARTS:
        here = [m for m in members if m.de is not None and m.de.part == part]
        if any(m.en is None or m.en.part != part for m in here):
            return False
        by_de = [id(m) for m in sorted(here, key=lambda m: m.de.index)]  # type: ignore[union-attr]
        by_en = [id(m) for m in sorted(here, key=lambda m: m.en.index)]  # type: ignore[union-attr]
        if by_de != by_en:
            differs.add(part)
    return differs == {"companion"}


def _committed_base(bundle: LoadedBundle, deck_ledger: DeckLedger | None) -> _FileBase | None:
    """:func:`_file_base` of the deck as committed at ``HEAD``: the base of
    a deck with no recorded sequence that an edit took out of sync."""
    from clm.slides.git_text import bundle_texts_at_ref

    de, en, de_companion, en_companion = bundle_texts_at_ref(bundle.de_path, bundle.en_path, "HEAD")
    if de is None or en is None:
        return None
    outcome = parse_bundle(de, en, de_companion, en_companion, comment_token=bundle.comment_token)
    return _file_base(outcome.deck, deck_ledger)


def _text(files: Mapping[FileKey, str | None], lang: str, part: str) -> str:
    text = files.get((lang, part))
    return text if text is not None else ""


def _preamble_fp(lines: tuple[str, ...] | None) -> str | None:
    return lines_fingerprint(lines)


def _target(side: str | None) -> str | None:
    """The half a propagation from ``side`` writes."""
    return None if side is None else ("en" if side == "de" else "de")


def _recorded(
    sequence: Sequence[SequenceEntry],
    out: doc_core.Outcome,
    inputs: Mapping[str, Sequence[SideCell]],
    trust: Mapping[tuple[str, str], str],
) -> dict[tuple[str, str], Recorded]:
    """The recorded state of each localized cell the pass holds, keyed
    ``(lang, text key)``: the entry the core aligned it to (by id, else by
    the member it settled, else by an unchanged fingerprint). ``trust``
    overrides it for a base taken from files (:func:`_file_base`)."""
    entry_of: dict[tuple[str, str], tuple[SequenceEntry, SideCell]] = {}
    by_sid = {e.sid: e for e in sequence if e.localized and e.sid is not None}
    for lang in SIDES:
        for sc in inputs[lang]:
            key = text_key(sc)
            if key is None or not is_localized(sc):
                continue
            entry = by_sid.get(key) if key != TITLE_ID or bare_slide_id(sc.slide_id) else None
            if entry is not None:
                entry_of[(lang, key)] = (entry, sc)
    for m in out.members:
        if m.base_idx is None or m.sid is not None or not m.localized:
            continue
        for lang in SIDES:
            j = m.pos[lang]
            if j is None:
                continue
            sc = inputs[lang][j]
            key = text_key(sc)
            if key is not None:
                entry_of.setdefault((lang, key), (sequence[m.base_idx], sc))
    if not out.members:  # an open ``identify``: match un-id'd cells by content
        for lang in SIDES:
            for sc in inputs[lang]:
                key = text_key(sc)
                if key is None or (lang, key) in entry_of or not is_localized(sc):
                    continue
                fp = sequence_fingerprint(sc)
                for e in sequence:
                    if e.sid is None and e.localized and (e.de if lang == "de" else e.en) == fp:
                        entry_of[(lang, key)] = (e, sc)
                        break
    out_rec: dict[tuple[str, str], Recorded] = {}
    for (lang, key), (entry, sc) in entry_of.items():
        side = 0 if lang == "de" else 1
        if entry.texts is not None:
            r = entry.texts[side]
            out_rec[(lang, key)] = Recorded(r.text, (r.for_slide, r.vo_anchor), r.separator)
            continue
        same = sequence_fingerprint(sc) == (entry.de if lang == "de" else entry.en)
        out_rec[(lang, key)] = Recorded(
            text_fingerprint(sc) if same else _UNKNOWN_TEXT, owners_of(sc), separator_of(sc.lines)
        )
    for (lang, key), verdict in trust.items():
        if verdict == "cold":
            out_rec.pop((lang, key), None)
        elif (lang, key) in out_rec:
            out_rec[(lang, key)] = evolve(out_rec[(lang, key)], text=_UNKNOWN_TEXT)
    return out_rec


def _changes(
    out: doc_core.Outcome,
    inputs: Mapping[str, Sequence[SideCell]],
    handle_of_cell: Callable[[SideCell], str],
    sequence: Sequence[SequenceEntry],
    owners: Mapping[tuple[str, str], tuple[str | None, str | None]],
) -> list[Row]:
    """The changes a settled pass makes on its own, per member and half
    (design §7.2: the report keeps listing what ``apply`` will do)."""
    rows: list[Row] = []

    def handle(m: doc_core.Member) -> str:
        if m.base_idx is not None:
            entry = sequence[m.base_idx]
            return f"id:{entry.sid}" if entry.sid is not None else f"seq:{m.base_idx}"
        if m.sid is not None:
            return f"id:{m.sid}"
        lang = next(s for s in SIDES if m.pos[s] is not None)
        return handle_of_cell(inputs[lang][doc_core._idx(m.pos[lang])])

    for m in out.members:
        if m.alive:
            continue
        for lang in SIDES:
            if m.pos[lang] is not None:
                rows.append(Row("remove", handle(m), lang, "removed on the other half"))
    for i, m in enumerate(out.sequence):
        for lang in SIDES:
            cell = out.halves[lang][i]
            j = m.pos[lang]
            if j is None:
                if not cell.localized:
                    rows.append(Row("copy", handle(m), lang, "added on the other half"))
                continue
            own = inputs[lang][j]
            if cell.localized:
                continue  # its text, owners and twin are the text layer's rows
            what = []
            if cell.body != sequence_fingerprint(own):
                what.append("content")
            if cell.tags != frozenset(own.tags):
                what.append("tags")
            if cell.sid != bare_slide_id(own.slide_id):
                what.append("slide_id")
            if cell.part != own.part:
                what.append("file")
            if what:
                rows.append(Row("mirror", handle(m), lang, " and ".join(what)))
        # tags and id of a localized cell are structural too
        for lang in SIDES:
            cell, j = out.halves[lang][i], m.pos[lang]
            if not cell.localized or j is None:
                continue
            own = inputs[lang][j]
            what = []
            if cell.tags != frozenset(own.tags):
                what.append("tags")
            if cell.sid != bare_slide_id(own.slide_id):
                what.append("slide_id")
            if cell.part != own.part:
                what.append("file")
            if what:
                rows.append(Row("mirror", handle(m), lang, " and ".join(what)))
    for lang in SIDES:
        for part in PARTS:
            now = [
                doc_core._idx(m.pos[lang])
                for i, m in enumerate(out.sequence)
                if m.pos[lang] is not None and out.halves[lang][i].part == part
            ]
            if now != sorted(now):
                rows.append(Row("mirror_order", "order", lang, f"the {part}'s cell order"))
    for lang in SIDES:
        for sc in inputs[lang]:
            key = text_key(sc)
            if key is None or not is_localized(sc):
                continue
            want = owners.get((lang, key))
            if want is not None and want != owners_of(sc):
                rows.append(Row("mirror_owner", f"id:{key}", lang, "for_slide / vo_anchor"))
    return rows


def pair_payload(bundle: LoadedBundle, ledger: TopicLedger, result: Pass) -> dict:
    """One deck's entry of the schema-6 report (``clm info sync-agents``)."""
    from clm.slides.doc_ledger import deck_key_for, ledger_path_for
    from clm.slides.doc_report import report_id_for

    return {
        "de_path": str(bundle.de_path),
        "en_path": str(bundle.en_path),
        "deck_key": deck_key_for(bundle.de_path),
        "ledger": str(ledger_path_for(bundle.de_path)),
        "report_id": report_id_for(bundle, ledger),
        "is_clean": result.is_clean,
        "needs_model": result.needs_model,
        "needs_agent": result.needs_agent,
        "refusal": result.refusal,
        "error": result.error,
        "items": [r.payload() for r in result.rows if r.is_question],
        "changes": [r.payload() for r in result.rows if not r.is_question],
        "files_changed": sorted(f"{lang}:{part}" for lang, part in result.changed),
    }


def land(
    bundle: LoadedBundle,
    ledger: TopicLedger,
    deck_key: str,
    result: Pass,
    *,
    commit: str | None = None,
) -> list[Path]:
    """Write a settled pass's changed files and record the deck (in memory:
    the caller saves ``ledger``). Returns the paths written.

    Nothing is open after a settled pass, so the whole deck is in sync and
    verified: it is recorded as a full snapshot (members, order scopes,
    sequence and text records), the same record ``clm slides sync record``
    makes, stamped ``apply``.
    """
    if result.rendered is None or result.held or result.error or result.refusal:
        raise ValueError("only a settled pass lands")
    written = write_changed_files(bundle, result.rendered, result.changed)  # type: ignore[arg-type]
    after = load_bundle(bundle.de_path, bundle.en_path)
    if after.outcome.deck is None:  # pragma: no cover - the gate parsed the same text
        raise RenderError("the written files no longer parse")
    record_deck_snapshot(ledger, deck_key, after.outcome.deck, provenance="apply", commit=commit)
    return written
