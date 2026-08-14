"""The v3 per-item apply executor (#520 Phase 3, design §6.2/§8).

Executes the deterministic rows of a :class:`~clm.slides.sync_diff.DeckDiff`
(the :data:`~clm.slides.sync_diff.MECHANICAL_ACTIONS` registry) plus any
*validated decisions* an agent supplied for framed items — **per item**,
value-keyed by :class:`~clm.slides.bilingual_doc.MemberKey` handle: invalid
answers are rejected individually with reasons, valid ones land, and nothing
already applied is lost. The ledger records each landed item.

Write discipline:

* Mutations happen on an in-memory per-``(lang, part)`` cell-stream view of
  the parsed deck; unmutated cells re-emit their verbatim bytes (the lens
  guarantee lifted into the executor — emission is preamble + concatenated
  cell lines, exactly what :func:`~clm.slides.doc_lenses.project` produces).
* Before anything touches disk the mutated bundle is **re-parsed**
  (:func:`~clm.slides.doc_lenses.parse_bundle`): a refusal aborts the whole
  write, leaving every file untouched — the executor can never write a bundle
  the lens cannot read back.
* Writes go through :func:`~clm.infrastructure.utils.path_utils.atomic_write_all`
  (≤4 files per deck), the same boundary ``split``/``unify`` use.
* P8 stays load-bearing: this module executes only what the differ *emitted*
  as mechanical (the differ already refuses to emit a mechanical row when the
  base carried a divergence, a pool has a deficit, or a twin is estranged) —
  and any executor-side ambiguity (a twin cell it cannot locate uniquely, a
  base fingerprint that no longer matches) fails that one item, never guesses.

Decision documents (design §8) re-home the ``sync_accept`` guards: a body
answer is validated as ONE cell's body (the multi-cell smuggling rejection —
a body carrying a ``<token> %%`` boundary would re-split on read-back and
mint phantom cells / duplicate ids), choices are validated against the
per-action vocabulary, and a stale handle is rejected, not guessed at.

This module is part of the v3 core: it must not import from the v2 sync core
(``sync_plan`` / ``sync_apply`` / ``sync_code``) — enforced by the
import-cleanliness test (design §12.5).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from attrs import define, evolve, field, frozen

from clm.core.slide_text.raw_cells import is_cell_boundary
from clm.slides.bilingual_doc import BilingualDeck, Lang, Member, MemberKey, SideCell
from clm.slides.doc_identity import (
    content_fingerprint,
    iter_with_groups,
    member_group_token,
)
from clm.slides.doc_ledger import (
    DeckLedger,
    LedgerMember,
    TopicLedger,
    preserve_unchanged_member,
    record_group_order,
    record_order_scope,
    record_preamble_scope,
    rename_group_scopes,
    rerecord_pool,
    seed_order_scopes,
    snapshot_deck,
)
from clm.slides.doc_lenses import LoadedBundle, parse_bundle
from clm.slides.doc_write import DeckEmitter, DeckWriteError, write_changed_files
from clm.slides.sync_diff import (
    FRAMED_ACTIONS,
    MECHANICAL_ACTIONS,
    DeckDiff,
    DiffItem,
)
from clm.slides.sync_wire import ACCEPTED_DECISION_SCHEMAS, WIRE_SCHEMA
from clm.slides.sync_writeback import set_header_tags, swap_lang

__all__ = [
    "ApplyOutcome",
    "Decision",
    "ItemResult",
    "apply_deck",
    "decision_vocabulary",
    "item_answers",
    "load_decision_document",
    "parse_decision_rows",
    "parse_decisions",
]

_SIDES: tuple[Lang, Lang] = ("de", "en")
_SLIDE_ID_ATTR_RE = re.compile(r'\s*slide_id="[^"]*"')
_FOR_SLIDE_ATTR_RE = re.compile(r'\s*for_slide="[^"]*"')


def _other(lang: Lang) -> Lang:
    return "en" if lang == "de" else "de"


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@frozen
class Decision:
    """One agent answer to a framed item, keyed by the member handle.

    ``side`` (``"de"`` / ``"en"``) is only meaningful alongside a ``body`` on a
    two-sided ``verify_cold`` item: it names the stale twin to overwrite with
    the supplied text. Every other framed action derives its target side from
    the item itself, so ``side`` there is rejected as a mistake.
    """

    key: str
    choice: str | None = None
    body: str | None = None
    side: str | None = None
    #: The framed action this answer is FOR. Normally omitted — one member
    #: frames one row. When a member frames two (the engine suppresses most
    #: same-key pairs, but not all), naming the action is the only way to
    #: answer both in one document; and naming it makes any answer
    #: self-checking, since a row whose action matches no framed row on that
    #: member is reported instead of silently landing on the other one.
    action: str | None = None


#: Framed actions this executor can resolve from a decision, with the answer
#: shapes each accepts. Everything else is agent-manual in Phase 3: edit the
#: files, re-run ``report``, then ``record``.
_DECISION_VOCABULARY: dict[str, tuple[str, ...]] = {
    "translate_edit": ("body", "keep_twin"),
    "translate_new": ("body",),
    #: ``body`` is symmetric with ``verify_cold``'s #572 recovery: both
    #: sides moved, the agent read them, and one is wrong — name it with
    #: ``side`` and supply the correction instead of hand-editing the file
    #: and then confirming (findings M7 / Q6a; the info topic promised it).
    "verify_translation": ("confirm", "body"),
    "verify_cold": ("confirm", "body"),
    "conflict_shared": ("de", "en", "body"),
    "pending_divergence": ("de", "en"),
    "remove_vs_edit": ("remove", "keep"),
    "remove_localized_side": ("remove", "body"),
    "unify_choose_body": ("de", "en", "body"),
    "conflict_owner": ("de", "en"),
    "conflict_tags": ("de", "en"),
    "conflict_preamble": ("de", "en"),
    "order_decision": ("de", "en"),
    "stamp_vs_new": ("treat_as_new",),
    "remove_vs_split": ("remove",),
    #: #650: prune the orphaned narration whose owning slide was removed or
    #: renamed — both halves' cells, surfaced as this framed decision, never
    #: silently. The non-remove remedies (retarget for_slide, restore the
    #: slide) stay hand-edits: they need a target no answer shape carries.
    "broken_owner": ("remove",),
    #: The fork transition's only route used to be the hand edit the doctrine
    #: forbids: the detail said "mark the twin", the vocabulary was empty, and
    #: `sync-agents` says never to hand-edit the other language (M11 / F1).
    #: `mark_twin` writes the twin's `lang=` attribute — nothing else; the
    #: body adaptation stays the agent's, framed as translate_edit next pass.
    "fork_pending_twin": ("mark_twin",),
}


def decision_vocabulary(action: str) -> tuple[str, ...]:
    """The answer shapes ``apply --decisions`` accepts for a framed action."""
    return _DECISION_VOCABULARY.get(action, ())


def item_answers(item: DiffItem) -> tuple[str, ...]:
    """The key-aware answer vocabulary the report advertises for one item.

    Identical to :func:`decision_vocabulary` except for ``verify_cold``, where
    two narrowings keep the advertisement honest — the design's standing rule
    is that advertising an answer the executor then rejects is a defect:

    * A ``body`` recovery targets a named ``side`` and can only be placed on
      an **id-keyed** two-sided member. A *positional* cold member has no
      stable id to address and its ordinal aliases a neighboring slot, so it
      drops ``body`` (mint a ``slide_id`` and re-report instead).
    * A **one-sided** cold member accepts *nothing*: `confirm` asserts the two
      halves agree, and the executor unconditionally refuses to confirm a
      member with only one side. Advertising it sent agents into a rejection
      that then blocked the whole positional pool (finding M6). The item stays
      framed, with ``resolution: manual`` and a detail naming the repair.
    """
    answers = decision_vocabulary(item.action)
    if item.action != "verify_cold":
        return answers
    if item.member is not None and item.member.is_one_sided:
        return ()
    if not item.key.startswith("id:"):
        return tuple(a for a in answers if a != "body")
    return answers


def item_resolution(item: DiffItem) -> str:
    """How this item gets resolved: ``mechanical`` / ``decision`` / ``manual``.

    Schema 4's discriminator for the ``answers: []`` ambiguity (finding M6):
    the empty list meant "mechanical — apply executes it" on 21 actions and
    "blocked — go edit the files" on the framed ones, the info topic
    documented only the first meaning as universal, and its own example
    filter script misclassified every instance of the second.

    Derived from the sets ``apply_deck`` itself branches on, so the report
    cannot drift from the executor.
    """
    if item.action in _RECORD_ONLY or item.action in MECHANICAL_ACTIONS:
        return "mechanical"
    return "decision" if item_answers(item) else "manual"


def parse_decisions(payload: object) -> tuple[dict[str, Decision], list[str]]:
    """Decode a decision document into a handle-keyed map.

    Convenience view over :func:`parse_decision_rows` for the common case of
    one answer per member; when a document answers two framed rows on one
    member, this map holds the first and the executor uses the row list.
    """
    rows, errors = parse_decision_rows(payload)
    decisions: dict[str, Decision] = {}
    for row in rows:
        decisions.setdefault(row.key, row)
    return decisions, errors


def parse_decision_rows(payload: object) -> tuple[list[Decision], list[str]]:
    """Decode a decision document; malformed rows are collected as errors.

    Accepts ``{"decisions": [...]}`` or a bare list; each row is
    ``{"key": "<member handle>", "choice": "..."} | {"key": ..., "body": "..."}``,
    optionally with ``"action"`` naming the framed row it answers.
    """
    errors: list[str] = []
    rows = payload.get("decisions") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        # The first schema error an agent ever sees — teach the whole shape at
        # once instead of one field name per round-trip.
        return [], [
            "decision document must be a list or {'decisions': [...]} — e.g. "
            '{"decisions": [{"key": "id:intro", "choice": "confirm"}, '
            '{"key": "id:motivation", "body": "# the translated cell body"}]}; '
            "each item's accepted answers are in report --json (`answers`), "
            "and a body is the cell text WITHOUT its '# %%' delimiter line — "
            "see `clm info sync-agents`"
        ]
    out: list[Decision] = []
    seen: set[tuple[str, str | None]] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("key"), str):
            errors.append(f"decision #{i}: needs a 'key' string (the member handle)")
            continue
        choice = row.get("choice")
        body = row.get("body")
        side = row.get("side")
        if choice is not None and not isinstance(choice, str):
            errors.append(f"decision #{i} ({row['key']}): 'choice' must be a string")
            continue
        if body is not None and not isinstance(body, str):
            errors.append(f"decision #{i} ({row['key']}): 'body' must be a string")
            continue
        if choice is not None and body is not None:
            errors.append(
                f"decision #{i} ({row['key']}): give exactly one of 'choice' or "
                "'body' — a 'body' alone already selects the body answer, so "
                "never add choice: 'body' alongside it"
            )
            continue
        if choice is None and body is None:
            errors.append(
                f"decision #{i} ({row['key']}): supply either a 'choice' (one of "
                "the item's advertised answers) or a 'body'"
            )
            continue
        if side is not None and side not in ("de", "en"):
            errors.append(f"decision #{i} ({row['key']}): 'side' must be 'de' or 'en'")
            continue
        if side is not None and body is None:
            errors.append(f"decision #{i} ({row['key']}): 'side' only accompanies a 'body' answer")
            continue
        action = row.get("action")
        if action is not None and not isinstance(action, str):
            errors.append(f"decision #{i} ({row['key']}): 'action' must be a string")
            continue
        if (row["key"], action) in seen:
            errors.append(
                f"decision #{i} ({row['key']}): duplicate key"
                + (f" for action '{action}'" if action else "")
                + " — one answer per framed row; to answer two rows on one "
                "member, give each row the item's 'action'"
            )
            continue
        seen.add((row["key"], action))
        out.append(Decision(key=row["key"], choice=choice, body=body, side=side, action=action))
    return out, errors


def load_decisions_text(text: str) -> tuple[dict[str, Decision], list[str]]:
    """:func:`parse_decisions` over raw JSON text."""
    document, errors = load_decision_document(text)
    return document.decisions, errors


@frozen
class DecisionDocument:
    """A parsed decision document: its answers plus its schema-4 envelope.

    ``report_id`` is the freshness token copied out of the report envelope
    (:func:`clm.slides.doc_report.report_id_for`). ``None`` means the document
    omitted it — refused by the caller since schema 3's one-release grace
    ended (:mod:`clm.slides.sync_wire`).
    """

    decisions: dict[str, Decision] = field(factory=dict)
    #: Every answer row in document order — the executor's view, which unlike
    #: ``decisions`` can hold two answers for one member.
    rows: list[Decision] = field(factory=list)
    report_id: str | None = None
    schema: int | None = None


def load_decision_document(text: str) -> tuple[DecisionDocument, list[str]]:
    """Decode a decision document's answers *and* its envelope from JSON text.

    The envelope is validated here rather than at the CLI so every consumer
    (CLI, MCP, a future driver) refuses the same documents: an unknown schema
    is an error, not a field to ignore — silently accepting a document written
    for a contract this engine does not implement is how an answer means one
    thing to its author and another to the executor.
    """
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return DecisionDocument(), [f"decision document is not valid JSON: {exc}"]
    rows, errors = parse_decision_rows(payload)
    decisions: dict[str, Decision] = {}
    for row in rows:
        decisions.setdefault(row.key, row)
    report_id: str | None = None
    schema: int | None = None
    if isinstance(payload, dict):
        raw_id = payload.get("report_id")
        if raw_id is not None and not isinstance(raw_id, str):
            errors.append("'report_id' must be the string from the report envelope")
        else:
            report_id = raw_id
        raw_schema = payload.get("schema")
        if raw_schema is not None:
            if not isinstance(raw_schema, int) or isinstance(raw_schema, bool):
                errors.append("'schema' must be an integer")
            elif raw_schema not in ACCEPTED_DECISION_SCHEMAS:
                accepted = ", ".join(str(s) for s in ACCEPTED_DECISION_SCHEMAS)
                errors.append(
                    f"decision document announces schema {raw_schema}; this clm "
                    f"reads {accepted} (see `clm info sync-agents`)"
                )
            else:
                schema = raw_schema
    return (
        DecisionDocument(decisions=decisions, rows=rows, report_id=report_id, schema=schema),
        errors,
    )


def _validate_body(body: str, comment_token: str) -> str | None:
    """The re-homed ``sync_accept`` body guards (single-cell shape)."""
    if not body.strip():
        return "the answer body is empty"
    if any(is_cell_boundary(line, comment_token) for line in body.split("\n")):
        return (
            f"the answer body contains a '{comment_token} %%' cell delimiter that "
            "would split it into multiple cells on read-back (minting a phantom "
            "cell / duplicate slide_id) — return just the one cell's body"
        )
    return None


def _is_macro_cell(cell: SideCell) -> bool:
    """A single-line j2 cell (e.g. the ``id:title`` header macro).

    Its j2 line is simultaneously the cell's boundary AND its whole content,
    so the generic body guards/writer cannot apply: any valid replacement
    text *is* a boundary line, and a "body" written after ``lines[0]`` would
    be a raw appended line, not a title change (issue #609).
    """
    return cell.cell_type == "j2" and all(line == "" for line in cell.lines[1:])


_MACRO_QUOTED_ARG_RE = re.compile(r'"[^"]*"')


def _macro_header_from_body(cell: SideCell, body: str, comment_token: str, *, bare_ok: bool) -> str:
    r"""The replacement j2 line for a macro cell, from a decision ``body``.

    Accepts the full j2 line verbatim (``# {{ header_de("...") }}``) or —
    when ``bare_ok`` and the existing line carries exactly one quoted
    argument — the bare replacement text, which is spliced into that
    argument. Bare text must be a single line without ``"`` or ``\`` (the
    characters that could terminate or escape out of the j2 string
    literal); with zero or multiple quoted arguments the splice target is
    absent or ambiguous, so only the full j2 line is accepted. ``bare_ok``
    is off for the create-a-new-cell paths (translate_new,
    remove_localized_side): there the template line is derived from the
    *other* side, so splicing bare text would silently keep the wrong
    language's macro name.
    """
    line = body.rstrip("\n")
    if not line.strip():
        raise _ItemError("the answer body is empty")
    if "\n" in line:
        raise _ItemError(
            "this member is a single-line j2 macro cell — supply just the one "
            f"replacement line ('{comment_token} {{{{ ... }}}}') or the bare "
            "replacement text for its quoted argument"
        )
    if line.startswith(comment_token + " %%"):
        raise _ItemError(
            f"a '{comment_token} %%' delimiter cannot replace a j2 macro line — "
            "supply the full j2 line or the bare replacement text"
        )
    if is_cell_boundary(line, comment_token):
        return line
    if not bare_ok:
        raise _ItemError(
            "this answer mints a new j2 macro cell — supply the full "
            f"'{comment_token} {{{{ ... }}}}' line (bare text cannot name the "
            "macro to wrap it in)"
        )
    if '"' in line or "\\" in line:
        raise _ItemError(
            "bare replacement text for a j2 macro argument cannot contain "
            "'\"' or '\\' — supply the full j2 line instead"
        )
    n_args = len(_MACRO_QUOTED_ARG_RE.findall(cell.header))
    if n_args == 0:
        raise _ItemError(
            "the existing j2 macro line has no quoted argument to replace — "
            "supply the full j2 line instead"
        )
    if n_args > 1:
        raise _ItemError(
            "the existing j2 macro line has multiple quoted arguments — bare "
            "text is ambiguous here; supply the full j2 line instead"
        )
    return _MACRO_QUOTED_ARG_RE.sub(lambda _m: f'"{line}"', cell.header, count=1)


def _replacement_lines(
    cell: SideCell, body: str, comment_token: str, *, bare_ok: bool = True
) -> tuple[str, ...]:
    """Validated replacement ``lines`` for one target cell from a ``body``.

    Normal cells keep their header line and take the body below it (after
    the smuggling guards); single-line j2 macro cells replace the j2 line
    itself (issue #609 — see :func:`_macro_header_from_body`).
    """
    if _is_macro_cell(cell):
        header = _macro_header_from_body(cell, body, comment_token, bare_ok=bare_ok)
        return (header, *cell.lines[1:])
    error = _validate_body(body, comment_token)
    if error:
        raise _ItemError(error)
    return _replace_body(cell, body, comment_token)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@frozen
class ItemResult:
    """One item's per-item outcome."""

    key: str
    action: str
    #: applied  — a file mutation landed (and was recorded)
    #: recorded — a ledger-only record landed
    #: deferred — a record-only row whose ledger write was deferred because
    #:            the member still carries an unresolved sibling item (#615);
    #:            nothing happened — the row re-frames on the next report
    #: pending  — framed item without a decision (untouched residue)
    #: already_applied — the answered member frames nothing now: the effect
    #:            this decision asks for already holds (a sibling pass, an
    #:            earlier apply, or the two CLI spellings of one deck). Not a
    #:            failure — the schema-4 split of the verdict that used to
    #:            read "rejected — stale handle" while the write had landed
    #:            (#649)
    #: rejected — a supplied decision failed validation, or named a member
    #:            this deck does not have (nothing changed)
    #: failed   — a mechanical row the executor could not resolve safely
    #: skipped  — excluded by the ``--member`` filter
    status: str
    reason: str = ""

    def payload(self) -> dict:
        return {
            "key": self.key,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
        }


@define
class ApplyOutcome:
    """The whole apply pass over one deck."""

    results: list[ItemResult] = field(factory=list)
    wrote: bool = False
    written_paths: list[Path] = field(factory=list)
    #: A whole-write abort (the re-parse gate refused the mutated bundle, or
    #: an I/O error) — no file and no ledger entry was touched.
    error: str | None = None
    dry_run: bool = False
    #: The in-memory ledger was updated (landed items recorded) — the caller
    #: must persist it. Independent of ``wrote``: a confirm-only pass changes
    #: the ledger without touching any file.
    ledger_changed: bool = False

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def all_applied(self) -> bool:
        # ``already_applied`` is a success: the state the decision asks for
        # holds. Counting it as residue is what made a re-run of a landed
        # apply exit 1 while reporting nothing left to do (#649).
        return self.error is None and all(
            r.status in ("applied", "recorded", "already_applied") for r in self.results
        )

    def to_payload(self) -> dict:
        return {
            "schema": WIRE_SCHEMA,
            "engine": "v3",
            "dry_run": self.dry_run,
            "error": self.error,
            "wrote": self.wrote,
            "written": [str(p) for p in self.written_paths],
            "counts": {
                status: self.count(status)
                for status in (
                    "applied",
                    "recorded",
                    "deferred",
                    "pending",
                    "already_applied",
                    "rejected",
                    "failed",
                    "skipped",
                )
            },
            "items": [r.payload() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Header surgery helpers
# ---------------------------------------------------------------------------


def _with_slide_id_of(source_lines: tuple[str, ...], target_header: str) -> tuple[str, ...]:
    """``source_lines`` with the *target's* verbatim ``slide_id`` attribute.

    A verbatim propagate must not overwrite the twin's id bytes: the two
    halves can legitimately differ in the ``!`` preserve marker, and the
    fingerprint ignores the attribute anyway (parity is judged modulo id).
    """
    target_match = _SLIDE_ID_ATTR_RE.search(target_header)
    header = _SLIDE_ID_ATTR_RE.sub("", source_lines[0])
    if target_match:
        header = header + target_match.group(0)
    return (header, *source_lines[1:])


def _set_for_slide(header: str, for_slide: str | None) -> str:
    """Set (or drop) the header's ``for_slide`` attribute."""
    stripped = _FOR_SLIDE_ATTR_RE.sub("", header)
    if for_slide is None:
        return stripped
    return stripped.rstrip() + f' for_slide="{for_slide}"'


def _replace_body(
    cell: SideCell, body: str, comment_token: str, *, normalize: bool = True
) -> tuple[str, ...]:
    """The cell's lines with a new body, preserving its trailing separator.

    An agent-supplied body is normalized to the engine's canonical cell
    shape at this write boundary (issue #655): a markdown cell's body
    starts with a blank comment line (the normalizer's
    ``markdown-blank-lead`` rule) — without this, a decision body opening
    directly with ``# ## Title`` landed verbatim and ``clm validate``
    warned on the engine's own output, forcing an out-of-band fix plus a
    zero-content ``keep_twin`` round. Idempotent on canonical input; j2
    cells never reach this function (:func:`_replacement_lines` routes
    macro cells separately, and ``cell_type == "j2"`` iff ``is_j2``).

    ``normalize=False`` is for **parity copies** (``unify_choose_body``'s
    side choice), where the body comes from the twin's own parsed cell and
    byte-parity with that side is the whole point — normalizing only the
    written side would bank a divergent pair and frame a phantom
    ``pending_divergence`` round (#655 adversarial review). Same doctrine
    as ``propagate``'s verbatim copy: repo content is the normalizer's
    job, not apply's.
    """
    old_body = cell.lines[1:]
    trailing = 0
    for line in reversed(old_body):
        if line == "":
            trailing += 1
        else:
            break
    new_body = body.split("\n")
    while new_body and new_body[-1] == "":
        new_body.pop()
    if normalize and new_body and cell.cell_type == "markdown":
        first = new_body[0]
        if first.strip() == "":
            new_body[0] = comment_token  # promote a bare blank to the blank comment
        elif first.strip() != comment_token:
            new_body.insert(0, comment_token)
    return (cell.lines[0], *new_body, *([""] * trailing))


# ---------------------------------------------------------------------------
# The executor
# ---------------------------------------------------------------------------

#: Execution phases: content rewrites, then inserts, then removes, then
#: layout moves, then reorders, then preambles — so a reorder always sees the
#: post-insert/post-remove streams. Records are ledger-only and phase-free.
_PHASES: dict[str, int] = {
    "propagate_shared_edit": 0,
    "mirror_tags": 0,
    "mirror_owner": 0,
    "retarget_owner": 0,
    "stamp_twin_id": 0,
    "translate_edit": 0,
    "conflict_shared": 0,
    "pending_divergence": 0,
    "unify_choose_body": 0,
    "conflict_owner": 0,
    "conflict_tags": 0,
    "verify_translation": 0,
    "verify_cold": 0,
    "copy_new_shared": 1,
    "translate_new": 1,
    "remove_vs_edit": 1,
    "mirror_remove": 2,
    "remove_vs_split": 2,
    "remove_localized_side": 2,
    "mirror_layout": 3,
    "mirror_order": 4,
    "order_decision": 4,
    "propagate_preamble": 5,
    "conflict_preamble": 5,
}

#: The two member-less preamble handles (``pos:~preamble/<part>/0``). Rows on
#: them act on the per-``(lang, part)`` preamble streams, not on any member —
#: matched EXACTLY, never by prefix: the parser accepts an anchor literally
#: named ``~preamble``, whose real member keys (``pos:~preamble/code/0``)
#: share the prefix.
_PREAMBLE_HANDLES = frozenset({f"pos:~preamble/{part}/0" for part in ("deck", "companion")})


def _item_phase(item: DiffItem) -> int:
    """The execution phase of one item.

    ``stamp_vs_new`` is the one action whose resolution spans two phases: the
    id-view row inserts (a ``treat_as_new`` grows the twin — phase 1, with the
    other inserts) while the pos-view row removes (phase 2, with the other
    removes) — so the grown twin is placed before its superseded positional
    neighbor disappears.
    """
    if item.action == "stamp_vs_new":
        return 1 if item.key.startswith("id:") else 2
    return _PHASES.get(item.action, 9)


#: Mechanical rows that are pure ledger records (no file mutation).
_RECORD_ONLY = frozenset(
    {
        "record_symmetric_edit",
        "record_symmetric_add",
        "record_remove",
        "record_tags",
        "record_fork",
        "record_unify",
        "record_key_migration",
        "record_relayout",
        "record_owner",
        "record_order",
        "record_group_rename",
        "record_preamble",
        "record_neutral",
    }
)


class _ItemError(DeckWriteError):
    """A per-item execution failure: this item fails, others proceed."""


@define
class _Executor(DeckEmitter):
    """The emitter's stream view plus the per-item action methods.

    Emission and the generic stream plumbing (``set_side`` /
    ``stream_remove`` / ``insert_mirrored``) live on
    :class:`~clm.slides.doc_write.DeckEmitter`; this subclass adds the
    diff-item executors on top.
    """

    bundle: LoadedBundle = field(kw_only=True)
    comment_token: str = field(kw_only=True)

    @staticmethod
    def _holder(item: DiffItem, lang: Lang) -> Member | None:
        """The member carrying the item's ``lang`` cell.

        The :class:`DiffItem` side convention: ``member`` carries the DE
        cell (and, when ``twin`` is ``None``, every present side); a set
        ``twin`` carries the EN cell of a pool slot whose cross-side pairing
        shifted. Resolving each side through this rule is what keeps the
        executor from ever acting on a neighboring slot's cell.
        """
        if item.twin is not None and lang == "en":
            return item.twin
        return item.member

    def _locate_twin(self, item: DiffItem, twin: Lang) -> tuple[Member, SideCell]:
        """The twin-side cell a row acts on (holder rule, then fp search).

        Anything not uniquely locatable fails the item (P8: never guess).
        """
        holder = self._holder(item, twin)
        if holder is not None:
            cell = holder.side(twin)
            if cell is not None:
                return holder, cell
        base_fp = item.base.side_fp(twin) if item.base is not None else None
        if base_fp is None:
            raise _ItemError(f"cannot locate the {twin} twin of {item.key} (no base fingerprint)")
        part = self._item_part(item)
        matches = [
            (m, c)
            for m in self.streams.get((twin, part), [])
            if (c := m.side(twin)) is not None
            and c.part == part
            and content_fingerprint(c) == base_fp
        ]
        if len(matches) != 1:
            raise _ItemError(
                f"the {twin} twin of {item.key} is not uniquely locatable "
                f"({len(matches)} base-identical candidates) — reconcile manually"
            )
        return matches[0]

    @staticmethod
    def _item_part(item: DiffItem) -> str:
        for holder in (item.member, item.twin):
            if holder is None:
                continue
            for lang in _SIDES:
                cell = holder.side(lang)
                if cell is not None:
                    return cell.part
        if item.base is not None:
            return "companion" if item.base.layout == "companion" else "deck"
        return "deck"

    def _moved_cell(self, item: DiffItem, side: Lang) -> tuple[Member, SideCell]:
        holder = self._holder(item, side)
        if holder is None:
            raise _ItemError(f"item {item.key} carries no member (executor bug)")
        cell = holder.side(side)
        if cell is None:
            raise _ItemError(f"the {side} side of {item.key} is missing")
        return holder, cell

    # -- mechanical rows -------------------------------------------------------

    def propagate(self, item: DiffItem, source: Lang) -> None:
        """Verbatim copy of the ``source`` cell onto its twin (id kept)."""
        _, moved = self._moved_cell(item, source)
        twin_member, twin_cell = self._locate_twin(item, _other(source))
        new_lines = _with_slide_id_of(moved.lines, twin_cell.header)
        self.set_side(twin_member, _other(source), evolve(twin_cell, lines=new_lines))

    def copy_new(self, item: DiffItem, source: Lang) -> None:
        member, moved = self._moved_cell(item, source)
        target = _other(source)
        if member.side(target) is not None:
            raise _ItemError(f"the {target} side of {item.key} already exists")
        if moved.lang_attr:
            # A lang-attr'd source must mint the TARGET half's variant (#717):
            # copying `lang="en"` verbatim into the DE file would make the
            # re-parse gate refuse the whole pass as a wrong_language_cell.
            # Same swap `translate_new` performs when minting a twin.
            header = swap_lang(moved.header, target)
            new_cell = evolve(moved, lines=(header, *moved.lines[1:]), lang_attr=target)
        else:
            new_cell = evolve(moved, lines=moved.lines)
        self.insert_mirrored(member, source, target, moved.part, new_cell)

    def keep_survivor(self, item: DiffItem, gone: Lang) -> None:
        """remove_vs_edit ``keep``: retain the survivor, re-add it on the gone side.

        Slot-aware twin of :meth:`copy_new`. Under a shifted pool pairing the
        survivor's parse member can carry a NEIGHBORING slot's cell on the
        ``gone`` side, so ``copy_new``'s ``member.side(target)`` existence
        check answers for the neighbor and wrongly rejects a keep that should
        land (#824 review round 2). There is deliberately NO fingerprint
        staleness scan here: a gone-side cell that byte-matches the slot's
        recorded fingerprint is not necessarily the slot's own cell (pool
        duplicates; a stamped twin — fingerprints are modulo slide_id), and
        the CLI re-diffs from current files at apply time, so a genuinely
        stale removal row never reaches the executor (#824 review round 3).
        """
        present = _other(gone)
        holder, cell = self._locate_twin(item, present)
        if cell.lang_attr:
            # Same mint as copy_new (#717): the target half's lang variant.
            header = swap_lang(cell.header, gone)
            new_cell = evolve(cell, lines=(header, *cell.lines[1:]), lang_attr=gone)
        else:
            new_cell = evolve(cell, lines=cell.lines)
        into = None
        if holder.side(gone) is not None:
            # Shifted pool pairing: the survivor's member already carries a
            # NEIGHBORING slot's gone-side cell — the re-add needs its own
            # member, or the insert would overwrite the neighbor (#824 review
            # round 2).
            into = evolve(holder, de=None, en=None)
        self.insert_mirrored(holder, present, gone, cell.part, new_cell, into=into)

    def mirror_remove(self, item: DiffItem, gone: Lang) -> None:
        present = _other(gone)
        member, cell = self._locate_twin(item, present)
        if item.base is not None:
            base_fp = item.base.side_fp(present)
            if base_fp is not None and content_fingerprint(cell) != base_fp:
                raise _ItemError(
                    f"the surviving {present} side of {item.key} moved off base "
                    f"since the diff — re-run report"
                )
        self.stream_remove(present, cell.part, member)
        self.set_side(member, present, None)

    def mirror_tags(self, item: DiffItem, source: Lang) -> None:
        _, moved = self._moved_cell(item, source)
        twin_member, twin_cell = self._locate_twin(item, _other(source))
        new_header = set_header_tags(twin_cell.header, moved.tags)
        self.set_side(
            twin_member,
            _other(source),
            evolve(twin_cell, lines=(new_header, *twin_cell.lines[1:]), tags=moved.tags),
        )

    def stamp_twin_id(self, item: DiffItem, unstamped: Lang) -> None:
        member = item.member
        if member is None:
            raise _ItemError(f"item {item.key} carries no member")
        idd_cell = member.side(_other(unstamped))
        twin_cell = member.side(unstamped)
        if idd_cell is None or twin_cell is None or idd_cell.slide_id is None:
            raise _ItemError(f"the id-stamp shape of {item.key} no longer holds — re-run report")
        if twin_cell.slide_id is not None:
            raise _ItemError(f"the {unstamped} side of {item.key} already carries an id")
        new_header = twin_cell.header.rstrip() + f' slide_id="{idd_cell.slide_id}"'
        self.set_side(
            member,
            unstamped,
            evolve(
                twin_cell,
                lines=(new_header, *twin_cell.lines[1:]),
                slide_id=idd_cell.slide_id,
            ),
        )

    def mirror_owner(self, item: DiffItem, source: Lang) -> None:
        _, moved = self._moved_cell(item, source)
        twin_member, twin_cell = self._locate_twin(item, _other(source))
        new_header = _set_for_slide(twin_cell.header, moved.for_slide)
        self.set_side(
            twin_member,
            _other(source),
            evolve(
                twin_cell,
                lines=(new_header, *twin_cell.lines[1:]),
                for_slide=moved.for_slide,
            ),
        )

    def retarget_owner(self, item: DiffItem) -> None:
        """Follow a same-pass owner-slide rename on every present half (#650).

        ``item.group`` carries the renamed-to anchor id (the differ's
        ``group_map`` evidence); the companion cells still reference the old
        id, so both headers are rewritten in place — no cell moves.
        """
        member = item.member
        new_owner = item.group
        if member is None or not new_owner:
            raise _ItemError(f"{item.key} carries no member/renamed owner to retarget")
        for lang in _SIDES:
            holder = self._holder(item, lang)
            cell = holder.side(lang) if holder is not None else None
            if holder is None or cell is None:
                continue
            new_header = _set_for_slide(cell.header, new_owner)
            self.set_side(
                holder,
                lang,
                evolve(cell, lines=(new_header, *cell.lines[1:]), for_slide=new_owner),
            )

    def mirror_layout(self, item: DiffItem, moved_side: Lang) -> None:
        """Complete a half-done inline↔companion relayout on the twin."""
        member = item.member
        if member is None:
            raise _ItemError(f"item {item.key} carries no member")
        moved = member.side(moved_side)
        twin_lang = _other(moved_side)
        twin = member.side(twin_lang)
        if moved is None or twin is None:
            raise _ItemError(f"the relayout shape of {item.key} no longer holds")
        target_part = moved.part
        if twin.part == target_part:
            raise _ItemError(f"the {twin_lang} side of {item.key} already relayouted")
        # Every raising validation runs BEFORE the first mutation: a failed
        # item must be a strict no-op (never leave the twin half-removed).
        if target_part == "companion":
            owner = moved.for_slide or (member.owner.value if member.owner is not None else None)
            if owner is None:
                raise _ItemError(f"cannot derive the owning slide for {item.key}")
            header = _set_for_slide(twin.header, owner)
            new_cell = evolve(
                twin, lines=(header, *twin.lines[1:]), part="companion", for_slide=owner
            )
        else:
            header = _set_for_slide(twin.header, None)
            new_cell = evolve(twin, lines=(header, *twin.lines[1:]), part="deck", for_slide=None)
        if not any(m is member for m in self.streams.get((moved_side, target_part), [])):
            raise _ItemError(f"the relayouted {moved_side} cell of {item.key} is unlocatable")
        if target_part == "companion" and self.preambles.get((twin_lang, "companion")) is None:
            mirror = self.preambles.get((moved_side, "companion"))
            self.preambles[(twin_lang, "companion")] = mirror if mirror is not None else ()
        self.stream_remove(twin_lang, twin.part, member)
        self.insert_mirrored(member, moved_side, twin_lang, target_part, new_cell)

    def propagate_preamble(self, item: DiffItem, source: Lang) -> None:
        part = item.key.rsplit("/", 2)[1]  # pos:~preamble/<part>/0
        lines = self.preambles.get((source, part))
        if lines is None:
            raise _ItemError(f"the {source} {part} preamble is absent")
        self.preambles[(_other(source), part)] = tuple(lines)
        self.mutated = True

    # -- reorders ---------------------------------------------------------------

    def mirror_order(self, item: DiffItem, source: Lang) -> None:
        key = item.key
        if key.startswith("id:"):
            self._mirror_member_move(item, source)
        elif "/pool." in key:
            group, kind = self._scope_of(key, "pool.")
            self._mirror_pool_order(group, kind, source, item)
        elif "/order." in key:
            group, part = self._scope_of(key, "order.")
            if group == "~groups":
                self._mirror_group_order(source, part)
            else:
                self._mirror_scope_order(group, part, source)
        else:
            raise _ItemError(f"unrecognized order scope {key}")

    @staticmethod
    def _scope_of(key: str, marker: str) -> tuple[str, str]:
        body = key.split(":", 1)[1]
        group, tail, _ordinal = body.rsplit("/", 2)
        if not tail.startswith(marker):
            raise _ItemError(f"unrecognized order scope {key}")
        return group, tail[len(marker) :]

    def _pool_members(self, group: str, kind: str, lang: Lang) -> list[Member]:
        members: list[tuple[int, Member]] = []
        for member, owner_group in iter_with_groups(self.deck):
            if member.key.scheme != "pos" or member.kind != kind:
                continue
            if member_group_token(member, owner_group) != group:
                continue
            cell = member.side(lang)
            if cell is not None:
                members.append((cell.index, member))
        return [m for _, m in sorted(members, key=lambda e: e[0])]

    def _permute_occupants(self, lang: Lang, part: str, desired: list[Member]) -> None:
        """Reorder ``desired`` members among their own stream slots."""
        stream = self.streams.get((lang, part), [])
        desired_ids = {id(m) for m in desired}
        slots = [i for i, m in enumerate(stream) if id(m) in desired_ids]
        if len(slots) != len(desired):
            raise _ItemError("order scope changed since the diff — re-run report")
        for slot, member in zip(slots, desired, strict=True):
            stream[slot] = member
        self.mutated = True

    def _mirror_pool_order(self, group: str, kind: str, source: Lang, item: DiffItem) -> None:
        target = _other(source)
        src_members = self._pool_members(group, kind, source)
        tgt_members = self._pool_members(group, kind, target)
        src_fps = [content_fingerprint(c) for m in src_members if (c := m.side(source)) is not None]
        by_fp: dict[str, list[Member]] = {}
        for m in tgt_members:
            cell = m.side(target)
            assert cell is not None
            by_fp.setdefault(content_fingerprint(cell), []).append(m)
        desired: list[Member] = []
        for fp in src_fps:
            bucket = by_fp.get(fp)
            if not bucket:
                raise _ItemError(
                    f"pool {group}/{kind}: the sides' contents no longer mirror — re-run report"
                )
            desired.append(bucket.pop(0))
        leftovers = [m for bucket in by_fp.values() for m in bucket]
        if leftovers:
            raise _ItemError(
                f"pool {group}/{kind}: {len(leftovers)} target cell(s) have no source "
                f"twin — re-run report"
            )
        # A pool's cells may span parts (the pool key carries none): permute
        # each part's occupants within their own stream.
        by_part: dict[str, list[Member]] = {}
        for m in desired:
            cell = m.side(target)
            assert cell is not None
            by_part.setdefault(cell.part, []).append(m)
        for part, members in by_part.items():
            self._permute_occupants(target, part, members)

    def _mirror_scope_order(self, group: str, part: str, source: Lang) -> None:
        target = _other(source)
        scope: dict[Lang, list[Member]] = {"de": [], "en": []}
        for member, owner_group in iter_with_groups(self.deck):
            if member.key.scheme != "id":
                continue
            if member_group_token(member, owner_group) != group:
                continue
            for lang in _SIDES:
                cell = member.side(lang)
                if cell is not None and cell.part == part:
                    scope[lang].append(member)
        for lang in _SIDES:
            stream = self.streams.get((lang, part), [])
            pos = {id(m): i for i, m in enumerate(stream)}
            scope[lang].sort(key=lambda m: pos.get(id(m), -1))
        target_ids = {id(m) for m in scope[target]}
        common = [m for m in scope[source] if id(m) in target_ids]
        self._permute_occupants(target, part, common)

    def _mirror_group_order(self, source: Lang, part: str) -> None:
        """Reorder whole slide groups on the target side to the source order.

        The target stream is clustered per owning group (on a deck part the
        anchors bracket cells, so a group's cells are contiguous by
        construction; on a companion part cells cluster by owner). The
        clusters are re-emitted in the source side's group order; cells owned
        by no group (headers before, orphans after) keep their edges.
        """
        target = _other(source)
        stream = self.streams.get((target, part), [])
        member_group: dict[int, str] = {}
        for group in self.deck.groups:
            for member in group.all_members():
                member_group[id(member)] = group.anchor_id
        prefix: list[Member] = []
        clusters: dict[str, list[Member]] = {}
        cluster_order: list[str] = []
        suffix: list[Member] = []
        for member in stream:
            gid = member_group.get(id(member))
            if gid is None:
                (prefix if not clusters else suffix).append(member)
                continue
            if gid not in clusters:
                clusters[gid] = []
                cluster_order.append(gid)
            clusters[gid].append(member)
        anchored: list[tuple[int, str]] = []
        for g in self.deck.groups:
            if g.anchor is None:
                continue
            cell = g.anchor.side(source)
            if cell is not None and cell.part == part:
                anchored.append((cell.index, g.anchor_id))
        source_order = [gid for _, gid in sorted(anchored)]
        desired = [gid for gid in source_order if gid in clusters]
        desired += [gid for gid in cluster_order if gid not in desired]
        new_stream = prefix + [m for gid in desired for m in clusters[gid]] + suffix
        if len(new_stream) != len(stream):  # pragma: no cover - pure regrouping
            raise _ItemError("group reorder would lose cells — reorder manually")
        self.streams[(target, part)] = new_stream
        self.mutated = True

    def _mirror_member_move(self, item: DiffItem, source: Lang) -> None:
        """Cross-group move: re-home the twin cell under the moved group."""
        member = item.member
        if member is None:
            raise _ItemError(f"item {item.key} carries no member")
        target = _other(source)
        cell = member.side(target)
        if cell is None:
            raise _ItemError(f"the {target} side of {item.key} is missing")
        # Validate before mutating: a failed item must be a strict no-op.
        if not any(m is member for m in self.streams.get((source, cell.part), [])):
            raise _ItemError(f"the moved {source} cell of {item.key} is unlocatable")
        self.stream_remove(target, cell.part, member)
        self.insert_mirrored(member, source, target, cell.part, cell)


# ---------------------------------------------------------------------------
# Ledger updates for landed items
# ---------------------------------------------------------------------------


def _pool_scope(item: DiffItem) -> tuple[str, str] | None:
    """The ``(group, kind)`` pool an applied pos-keyed item belongs to."""
    key = item.key
    if key.startswith("pos:"):
        body = key.split(":", 1)[1]
        group, kind, _ordinal = body.rsplit("/", 2)
        for marker in ("pool.", "order."):
            if kind.startswith(marker):
                return None
        return group, kind
    return None


#: Removal-family rows whose presence in a pool means the pool's positional
#: parse pairing is SHIFTED: a slot is missing on one side, so the lens's
#: cursor pairs every later cell with the wrong twin. (``record_remove`` is
#: absent from both files and shifts nothing.)
_SHIFTING_REMOVAL_ACTIONS = frozenset(
    {"mirror_remove", "remove_vs_edit", "remove_vs_split", "stamp_vs_new"}
)


def _earlier_pool_removal(items: list[DiffItem], item: DiffItem) -> DiffItem | None:
    """The first removal-family row PRECEDING ``item`` in its pool, if any.

    Pool slots are emitted in ordinal order, and a shifted pairing makes a
    later slot's keep insert anchor on a cross-paired member — the target
    stream comes out mis-ordered and the re-parse banks the swap (#824
    review round 4). A keep is only safe when it is the pool's earliest
    unresolved removal row; every later one defers to a follow-up pass,
    which re-pairs the pool first.
    """
    scope = _pool_scope(item)
    if scope is None:
        return None
    for other in items:
        if other is item:
            break
        if other.action in _SHIFTING_REMOVAL_ACTIONS and _pool_scope(other) == scope:
            return other
    return None


#: Framed actions whose pos-view evidence is a two-sided base entry facing a
#: one-sided survivor. While one is unresolved, its pool must NOT be
#: re-recorded wholesale: the fresh snapshot only knows the one-sided present
#: state, and :func:`_drop_unresolved_from_pools` then erases even that — the
#: only record that the gone side ever existed would vanish, silently
#: downgrading the pending conflict to mechanical duplication/resurrection on
#: the next report. (For two-sided members the drop-to-cold fail-safe is
#: sound; one-sided evidence has no cold state to fall back to.)
#: ``remove_vs_split`` joined for the #610 group-split shape: its reframed
#: removal rows likewise carry a two-sided base entry whose gone side
#: survives only as the base fingerprint. The freeze is gated to that
#: dedicated action (#630 F2) — keying it on ``ambiguous_alignment``, as
#: #625 briefly did, changed the recording behavior of every unrelated
#: pre-existing emitter of that action.
_POOL_FREEZING_ACTIONS = frozenset({"stamp_vs_new", "remove_vs_edit", "remove_vs_split"})


def _frozen_pools(unresolved_items: list[DiffItem]) -> set[tuple[str, str]]:
    """Pools whose ledger entries must stay untouched this pass (#600)."""
    frozen: set[tuple[str, str]] = set()
    for item in unresolved_items:
        if item.action not in _POOL_FREEZING_ACTIONS:
            continue
        pool = _pool_scope(item)
        if pool is not None:
            frozen.add(pool)
    return frozen


def _record_item(
    target: DeckLedger,
    fresh: DeckLedger,
    item: DiffItem,
    *,
    provenance: str,
    frozen_pools: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Update the ledger for one landed item (surgical, never wholesale).

    Returns the ``(group, kind)`` pools that were re-recorded wholesale, so
    the caller can un-bless the pool siblings that still carry unresolved
    items (:func:`_drop_unresolved_from_pools`). Pools in ``frozen_pools``
    are never re-recorded (nor patched — renumbered ordinals make a per-entry
    patch unsafe): the landed item simply stays unrecorded and re-frames
    mechanically once the pool's pending conflicts are answered.
    """
    key = item.key
    action = item.action

    if action == "conflict_tags":
        # A conflict_tags resolution records NOTHING (#615 F2): it mutates
        # one header line; the member re-frames and records on the next
        # pass. Recording here would bless bodies the framed row's
        # co-emission rule suppressed — those never reach unresolved_items,
        # so the unresolved-key guard in apply_deck cannot see them.
        return set()
    if action in ("record_key_migration",):
        if item.base is not None:
            target.members.pop(item.base.key, None)
        _upsert(target, fresh, key, provenance)
        return set()
    if action == "record_group_rename":
        if item.base is not None and item.base.key.startswith("id:"):
            old_group = item.base.key.split(":", 1)[1]
            new_group = key.split(":", 1)[1] if key.startswith("id:") else None
            target.members.pop(item.base.key, None)
            if new_group is not None:
                rename_group_scopes(target, old_group, new_group)
        _upsert(target, fresh, key, provenance)
        return set()
    if action in ("record_order", "mirror_order", "order_decision"):
        if key.startswith("id:"):
            _upsert(target, fresh, key, provenance)
            return set()
        if "/pool." in key:
            body = key.split(":", 1)[1]
            group, tail, _ = body.rsplit("/", 2)
            kind = tail[len("pool.") :]
            if (group, kind) in frozen_pools:
                return set()
            rerecord_pool(target, fresh, group, kind)
            return {(group, kind)}
        if "/order." in key:
            body = key.split(":", 1)[1]
            group, tail, _ = body.rsplit("/", 2)
            part = tail[len("order.") :]
            if group == "~groups":
                record_group_order(target, fresh)
            else:
                record_order_scope(target, fresh, group, part)
            return set()
        return set()
    if action in ("record_preamble", "propagate_preamble", "conflict_preamble") or (
        key in _PREAMBLE_HANDLES
    ):
        # Preamble rows bank exactly the preamble scope — keyed on the handle,
        # not the action, so a resolved pending_divergence frame on a
        # (member-less) preamble handle records here too instead of falling
        # through to the member-table upsert. The match is exact: an anchor
        # literally named ``~preamble`` is parseable, and its real member
        # keys (``pos:~preamble/code/0``) must not be swallowed here.
        part = key.split(":", 1)[1].rsplit("/", 2)[1]
        record_preamble_scope(target, fresh, part)
        return set()
    if action in (
        "record_remove",
        "mirror_remove",
        "remove_vs_edit",
        "remove_localized_side",
        "remove_vs_split",
    ):
        pool = _pool_scope(item)
        if pool is not None:
            if pool in frozen_pools:
                return set()
            rerecord_pool(target, fresh, *pool)
            return {pool}
        if key not in fresh.members:
            target.members.pop(key, None)
        else:  # a "keep"/re-add resolution: the member persists — record it
            _upsert(target, fresh, key, provenance)
        return set()

    pool = _pool_scope(item)
    if pool is not None:
        if pool in frozen_pools:
            return set()
        rerecord_pool(target, fresh, *pool)
        return {pool}
    _upsert(target, fresh, key, provenance)
    return set()


def _stamp_structural(target: DeckLedger, keys: set[str]) -> None:
    """Mark the engine-observed entries, and drop ownership they cannot back.

    Two corrections to whatever the record paths wrote for a ``record_neutral``
    row (#764), applied **once, after every landed item has been recorded**.

    Doing it per item was wrong and measurably so: a ``pos:`` record re-records
    its whole pool, so in a pool holding N neutral members each item's
    ``rerecord_pool`` reset the stamp the previous one had just written — 65% of
    positional neutral members ended up carrying the pass provenance and a
    re-created dangling owner. A landing ``record_order`` on the same pool did
    the same. Stamping after the loop is immune to both, because nothing
    re-records afterwards.

    **Provenance.** The pass provenance says who ran the verb. This row was not
    established by them; the engine observed that the two halves are the same
    bytes. Leaving the pass provenance would let a later "distrust everything
    from <source>" sweep keep or discard this trust on an attribution nobody
    made.

    **Ownership.** The row fires on cold decks, where the member's anchor is
    normally still cold, so the entry would name an owner the store has no entry
    for. :func:`~clm.slides.doc_ledger.save` prunes exactly that and warns that
    nothing should create it (#718) — creating it on every cold apply would turn
    a corruption detector into noise. The ownership claim is not what this row
    observed anyway, so drop it and let ownership record when the anchor lands.

    A key absent from ``target`` is skipped: the pool it belonged to may have
    been frozen (#600/#630), dropped as unresolved, or renumbered by a landed
    removal in the same pass.
    """
    for key in keys:
        landed = target.members.get(key)
        if landed is None:
            continue
        entry = landed.entry
        if entry.owner is not None and entry.owner not in target.members:
            entry = evolve(entry, owner=None)
        target.members[key] = evolve(landed, entry=entry, provenance="structural")


def _upsert(target: DeckLedger, fresh: DeckLedger, key: str, provenance: str) -> None:
    lm = fresh.members.get(key)
    if lm is None:
        target.members.pop(key, None)
        return
    target.members[key] = preserve_unchanged_member(
        target.members.get(key),
        LedgerMember(
            entry=lm.entry,
            provenance=provenance,
            state=lm.state,
            hash_version=lm.hash_version,
            confirmed_commit=lm.confirmed_commit,
        ),
    )


def _member_fp_pair(member: Member) -> tuple[str | None, str | None]:
    return (
        content_fingerprint(member.de) if member.de else None,
        content_fingerprint(member.en) if member.en else None,
    )


def _sweep_migrated_pos(target: DeckLedger, landed: list[tuple[DiffItem, str]]) -> None:
    """Drop the stale ``pos:`` entry a landed stamp/migration superseded.

    Targeted, never a blanket fp sweep (duplicated boilerplate content
    legitimately shares fingerprints with id'd cells — a blanket sweep would
    delete fresh confirmations): only the fingerprints of the *migrated base
    entries themselves* (or the stamped member, whose fp the id attribute
    does not change) are matched.
    """
    migrated_fps: set[tuple[str | None, str | None]] = set()
    for item, _provenance in landed:
        if item.action not in ("stamp_twin_id", "record_key_migration", "record_group_rename"):
            continue
        if item.base is not None:
            migrated_fps.add((item.base.de_fp, item.base.en_fp))
        if item.member is not None:
            migrated_fps.add(_member_fp_pair(item.member))
    if not migrated_fps:
        return
    for key in [k for k in target.members if k.startswith("pos:")]:
        lm = target.members[key]
        if (lm.entry.de_fp, lm.entry.en_fp) in migrated_fps:
            del target.members[key]


def _drop_unresolved_from_pools(
    target: DeckLedger,
    rerecorded_pools: set[tuple[str, str]],
    unresolved: list[Member],
) -> None:
    """Un-bless pool entries whose members still carry unresolved items.

    ``rerecord_pool`` is wholesale by necessity (ordinals renumber
    together), but a pool sibling whose framed item was left pending /
    rejected / failed must NOT come out of the pass trusted at its diverged
    state — its fresh entries are dropped back to cold (fail-safe: it
    re-checks as ``unverified`` next round, never silently in sync).
    """
    if not rerecorded_pools or not unresolved:
        return
    unresolved_fps = {_member_fp_pair(m) for m in unresolved}
    for group, kind in rerecorded_pools:
        prefix = f"pos:{group}/{kind}/"
        for key in [k for k in target.members if k.startswith(prefix)]:
            lm = target.members[key]
            if (lm.entry.de_fp, lm.entry.en_fp) in unresolved_fps:
                del target.members[key]


# ---------------------------------------------------------------------------
# The apply pass
# ---------------------------------------------------------------------------


def _decision_target_side(item: DiffItem) -> Lang:
    """The side a ``translate_edit`` body answer lands on: the twin of the
    moved/present side. (``translate_new`` derives its target from the
    one-sided member directly — see :func:`_apply_body_decision` — because the
    reporters set ``side`` inconsistently for that action, #570.)"""
    if item.side is not None:
        return _other(item.side)
    if item.member is not None and item.member.is_one_sided:
        return "de" if item.member.de is None else "en"
    raise _ItemError("cannot derive the target side for this item — answer with a choice")


def _execute_decision(
    ex: _Executor, item: DiffItem, decision: Decision, comment_token: str
) -> None:
    allowed = decision_vocabulary(item.action)
    if not allowed:
        raise _ItemError(
            f"'{item.action}' takes no answer (resolution: manual) — read the "
            f"item's detail, repair the files yourself, then re-run report; "
            f"`sync record` banks the result once the pair is in sync"
        )
    if decision.body is not None:
        if "body" not in allowed:
            raise _ItemError(
                f"'{item.action}' does not accept a body answer (allowed: {', '.join(allowed)})"
            )
        if decision.side is not None and item.action not in _SIDE_BODY_ACTIONS:
            raise _ItemError(
                f"'side' is only meaningful on a two-sided "
                f"{' / '.join(sorted(_SIDE_BODY_ACTIONS))} body answer, not on "
                f"'{item.action}' (which derives its target side itself)"
            )
        # A j2-kind member may be a single-line macro cell whose only valid
        # replacement text IS a boundary line — its validation is
        # target-aware and lives in _replacement_lines (issue #609).
        if not any(h is not None and h.kind == "j2" for h in (item.member, item.twin)):
            error = _validate_body(decision.body, comment_token)
            if error:
                raise _ItemError(error)
        _apply_body_decision(ex, item, decision.body, side=decision.side)
        return
    choice = decision.choice or ""
    if choice not in allowed:
        raise _ItemError(
            f"choice {choice!r} is not valid for '{item.action}' (allowed: {', '.join(allowed)})"
        )
    _apply_choice_decision(ex, item, choice)


#: Framed actions whose ``body`` answer targets a *named* side. Both are
#: two-sided review states — the agent read both halves and judged one of them
#: wrong — so neither can derive its target from the item.
_SIDE_BODY_ACTIONS = frozenset({"verify_cold", "verify_translation"})


def _apply_body_decision(
    ex: _Executor, item: DiffItem, body: str, *, side: str | None = None
) -> None:
    member = item.member
    if item.action == "verify_translation":
        # Both sides moved off base. `confirm` banks them as-is; a body says
        # "the named side is the wrong one" and replaces it in the same pass —
        # the review-after-translate case that otherwise costs an out-of-band
        # edit plus a second report (M7 / Q6a).
        if member is None or member.is_one_sided:
            raise _ItemError("a verify_translation body answer needs a two-sided member")
        if side is None:
            raise _ItemError(
                "a verify_translation body answer must name the 'side' to "
                "overwrite ('de' or 'en') — both sides moved, so the engine "
                "cannot tell which one you corrected"
            )
        target_side: Lang = side  # type: ignore[assignment]
        holder = ex._holder(item, target_side)
        cell = holder.side(target_side) if holder is not None else None
        if holder is None or cell is None:
            raise _ItemError(f"the {target_side} side of {item.key} is missing")
        ex.set_side(
            holder,
            target_side,
            evolve(cell, lines=_replacement_lines(cell, body, ex.comment_token)),
        )
        return
    if item.action == "verify_cold":
        # Cold recovery (issue #572): the agent read both bodies, judged the
        # named twin stale, and supplies the corrected text — a one-pass fix
        # instead of hand-editing the file then `confirm`-ing the stale twin.
        # Scoped to id-keyed two-sided members: a positional cold member has
        # no addressable id and its ordinal aliases a neighboring slot, so it
        # cannot take a body (mint a slide_id and re-report). Because the target
        # is always id-keyed, it is never in a `pos:` pool — the pool-coherence
        # guard (which blesses pool siblings wholesale) is untouched here.
        if not item.key.startswith("id:"):
            raise _ItemError(
                "a positional cold member cannot take a body — confirm the pool "
                "(after checking the pair is in sync), or mint a slide_id and "
                "re-report so the twin can be framed"
            )
        if member is None or member.is_one_sided:
            raise _ItemError(
                "cold body recovery needs a two-sided member — supply the missing "
                "twin with translate_new instead"
            )
        if side is None:
            raise _ItemError(
                "a verify_cold body answer must name the 'side' to overwrite "
                "(the stale twin: 'de' or 'en')"
            )
        target: Lang = side  # type: ignore[assignment]
        holder = ex._holder(item, target)
        cell = holder.side(target) if holder is not None else None
        if holder is None or cell is None:
            raise _ItemError(f"the {target} side of {item.key} is missing")
        ex.set_side(
            holder, target, evolve(cell, lines=_replacement_lines(cell, body, ex.comment_token))
        )
        return
    if item.action == "translate_edit":
        target = _decision_target_side(item)
        twin_member, twin_cell = ex._locate_twin(item, target)
        ex.set_side(
            twin_member,
            target,
            evolve(twin_cell, lines=_replacement_lines(twin_cell, body, ex.comment_token)),
        )
        return
    if item.action == "translate_new":
        if member is None:
            raise _ItemError("item carries no member")
        # A ``translate_new`` mints the *absent* twin, so the target is the
        # member's missing side — derived from the member itself, never from
        # ``item.side``. The reporters disagree on what ``side`` means here
        # (``_classify_new`` sets the present side; ``_classify_localized`` /
        # ``_classify_fork`` set the missing side), so trusting it inverts the
        # direction for a standing one-sided member — the harvest → sync handoff
        # for a separated voiceover companion (issue #570).
        if not member.is_one_sided:
            raise _ItemError("cannot mint a twin: both sides already exist — answer with a choice")
        target = "de" if member.de is None else "en"
        source = _other(target)
        source_cell = member.side(source)
        if source_cell is None:
            raise _ItemError(f"the {source} source cell of {item.key} is missing")
        header = (
            swap_lang(source_cell.header, target) if source_cell.lang_attr else (source_cell.header)
        )
        template = evolve(source_cell, lines=(header, *source_cell.lines[1:]))
        new_cell = evolve(
            source_cell,
            lines=_replacement_lines(template, body, ex.comment_token, bare_ok=False),
            lang_attr=target if source_cell.lang_attr else None,
        )
        ex.insert_mirrored(member, source, target, source_cell.part, new_cell)
        return
    if item.action in ("conflict_shared", "unify_choose_body", "remove_localized_side"):
        if member is None:
            raise _ItemError("item carries no member")
        if item.action == "remove_localized_side":
            # Recreate the deleted variant with the provided body.
            gone = item.side
            if gone is None:
                raise _ItemError("item names no deleted side")
            surviving = _other(gone)
            source_cell = member.side(surviving)
            if source_cell is None or member.side(gone) is not None:
                raise _ItemError(f"the shape of {item.key} no longer holds — re-run report")
            header = swap_lang(source_cell.header, gone)
            base = evolve(source_cell, lines=(header, *source_cell.lines[1:]))
            new_cell = evolve(
                base,
                lines=_replacement_lines(base, body, ex.comment_token, bare_ok=False),
                lang_attr=gone,
            )
            ex.insert_mirrored(member, surviving, gone, source_cell.part, new_cell)
            return
        # Compute every side's replacement before the first mutation: a failed
        # item must be a strict no-op (never leave one side rewritten).
        updates: list[tuple[Member, Lang, SideCell]] = []
        for lang in _SIDES:
            holder = ex._holder(item, lang)
            cell = holder.side(lang) if holder is not None else None
            if holder is None or cell is None:
                raise _ItemError(f"the {lang} side of {item.key} is missing")
            updates.append(
                (holder, lang, evolve(cell, lines=_replacement_lines(cell, body, ex.comment_token)))
            )
        for holder, lang, new_cell in updates:
            ex.set_side(holder, lang, new_cell)
        return
    raise _ItemError(f"'{item.action}' does not accept a body answer")


def _reject_divergent_tags(de_cell: SideCell, en_cell: SideCell) -> None:
    """The confirm tag guard (#615 F2, belt-and-braces for S4 and any
    future classification gap): a pure ledger record must never bank a
    cross-side tag divergence. Safe in-pass: ``set_side`` mutates the
    member in place, so a same-pass ``mirror_tags`` (phase 0, emitted
    before the body row) is visible here and a co-answered ``confirm``
    still lands in one pass."""
    if set(de_cell.tags) != set(en_cell.tags):
        raise _ItemError(
            f"tag sets diverge cross-side (de: {list(de_cell.tags)}, "
            f"en: {list(en_cell.tags)}) — tags are language-independent — "
            f"answer the tag item (or align the tag lines manually), then "
            f"re-run report"
        )


def _apply_choice_decision(ex: _Executor, item: DiffItem, choice: str) -> None:
    action = item.action
    if choice == "mark_twin":
        # The fork's structural half, executed instead of hand-edited: give the
        # unmarked twin its own `lang=` attribute. The BODY adaptation is not
        # done here — the two cells still say the same thing, so the next
        # report frames the localized pair's translate_edit and the agent
        # answers that with the adapted text. Two passes, both in-engine
        # (the fork two-pass recipe, previously documented nowhere).
        marked = item.side
        if marked is None:
            raise _ItemError("item names no marked side")
        target: Lang = _other(marked)
        holder = ex._holder(item, target)
        cell = holder.side(target) if holder is not None else None
        if holder is None or cell is None:
            raise _ItemError(
                f"the {target} side of {item.key} is missing — a fork marks an "
                "EXISTING twin; supply the twin first"
            )
        if cell.lang_attr is not None:  # pragma: no cover - differ frames only unmarked twins
            raise _ItemError(f"the {target} side already carries a lang attribute")
        header = swap_lang(cell.lines[0], target)
        ex.set_side(holder, target, evolve(cell, lines=(header, *cell.lines[1:])))
        return
    if choice == "confirm":
        de_holder = ex._holder(item, "de")
        en_holder = ex._holder(item, "en")
        de_cell = de_holder.side("de") if de_holder is not None else None
        en_cell = en_holder.side("en") if en_holder is not None else None
        if de_cell is None and en_cell is None:
            raise _ItemError("item carries no member to confirm")
        if de_cell is None or en_cell is None:
            raise _ItemError(
                "cannot confirm a one-sided member — supply the twin first (translate/edit)"
            )
        if (de_cell.lang_attr is not None) != (en_cell.lang_attr is not None):
            raise _ItemError(
                "cannot confirm a member mid-transition (the sides disagree about "
                "lang attributes) — complete or revert the transition first"
            )
        _reject_divergent_tags(de_cell, en_cell)
        return  # confirmation is a pure ledger record; nothing mutates
    if choice in ("de", "en"):
        side: Lang = choice  # type: ignore[assignment]
        if action == "conflict_tags":
            # Mirror ONLY the tag set from the chosen side (#615) — never a
            # whole-cell propagate, which would overwrite a translated body.
            ex.mirror_tags(item_with_side(item, side), side)
            return
        if action in ("conflict_shared", "pending_divergence"):
            if item.key in _PREAMBLE_HANDLES:
                # The preamble frame carries no member — route to the
                # preamble copy, exactly as conflict_preamble does. Sending
                # it through the cell propagate dead-ends the frame on the
                # "carries no member" error (Y6 follow-up).
                ex.propagate_preamble(item, side)
            else:
                ex.propagate(item_with_side(item, side), side)
            return
        if action == "unify_choose_body":
            _, chosen = ex._moved_cell(item, side)
            twin_holder, twin = ex._locate_twin(item, _other(side))
            ex.set_side(
                twin_holder,
                _other(side),
                # Parity copy: byte-equality with the chosen side completes
                # the unify — never normalize one side of a parity pair.
                evolve(
                    twin,
                    lines=_replace_body(twin, chosen.body, ex.comment_token, normalize=False),
                ),
            )
            return
        if action == "conflict_owner":
            _, chosen = ex._moved_cell(item, side)
            twin_holder, twin = ex._locate_twin(item, _other(side))
            header = _set_for_slide(twin.header, chosen.for_slide)
            ex.set_side(
                twin_holder,
                _other(side),
                evolve(twin, lines=(header, *twin.lines[1:]), for_slide=chosen.for_slide),
            )
            return
        if action == "conflict_preamble":
            ex.propagate_preamble(item, side)
            return
        if action == "order_decision":
            ex.mirror_order(item, side)
            return
        raise _ItemError(f"'{action}' does not accept a side choice")
    if choice == "remove":
        if action == "broken_owner":
            # #650: the owning slide is gone (removed or renamed) — prune the
            # orphaned narration from EVERY present half. Unlike the survivor
            # removals below there is no gone side: the companion cells still
            # exist on both halves, only their owner does not.
            removed = False
            for lang in _SIDES:
                holder = ex._holder(item, lang)
                cell = holder.side(lang) if holder is not None else None
                if holder is None or cell is None:
                    continue
                ex.stream_remove(lang, cell.part, holder)
                ex.set_side(holder, lang, None)
                removed = True
            if not removed:
                raise _ItemError(f"{item.key} has no cells to remove")
            return
        # A deliberate removal: delete the SURVIVING side of the slot. Only
        # ``item.side`` (the already-gone side) tells which cell is the
        # slot's — under a shifted pool pairing the member's other-side cell
        # belongs to a neighboring slot and must never be touched.
        if item.side is None:
            raise _ItemError(
                f"{item.key} names no removed side — reconcile manually (edit + record)"
            )
        if action == "remove_vs_split":
            # The agent judged the byte-match a coincidental duplicate, not a
            # group split (#630): execute the removal the guard blocked, with
            # mirror_remove's survivor-on-base check intact (the row was
            # framed off an unchanged twin — a twin that moved since must
            # re-frame, never be deleted).
            ex.mirror_remove(item, item.side)
            return
        survivor = _other(item.side)
        holder = ex._holder(item, survivor)
        cell = holder.side(survivor) if holder is not None else None
        if holder is None or cell is None:
            raise _ItemError(f"{item.key} has no remaining cell to remove")
        ex.stream_remove(survivor, cell.part, holder)
        ex.set_side(holder, survivor, None)
        return
    if choice == "keep":
        # remove_vs_edit: keep the survivor and re-add it on the removed side.
        # Slot-aware (NOT copy_new): under a shifted pool pairing copy_new's
        # member.side(target) existence check answers for a NEIGHBORING slot's
        # cell and wrongly rejects a keep that should land (#824 review
        # round 2) — and the report advertises keep on every remove_vs_edit
        # row, so advertising a rejected answer would be a defect (:174).
        if item.side is None:
            raise _ItemError(
                f"{item.key} names no removed side — reconcile manually (edit + record)"
            )
        ex.keep_survivor(item, item.side)
        return
    if choice == "treat_as_new":
        # stamp_vs_new (#600): the agent judged the suspected stamped-edit to
        # be a genuine add/remove instead. The id-view row (the new id'd cell)
        # grows the twin verbatim — the copy_new_shared path it would have
        # taken without the pool deficit; the pos-view row (the vanished
        # positional base cell) mirrors the removal. ``item.side`` names the
        # source side (id view) / the gone side (pos view) at emission.
        if item.side is None:
            raise _ItemError(f"{item.key} names no side — reconcile manually (edit + record)")
        if item.key.startswith("id:"):
            if item.member is None or not item.member.is_one_sided:
                raise _ItemError(
                    f"the one-sided shape of {item.key} no longer holds — re-run report"
                )
            ex.copy_new(item, item.side)
            return
        ex.mirror_remove(item, item.side)
        return
    if choice == "keep_twin":
        # translate_edit only: the edited side did not change what the twin
        # should say, so record the new (edited) baseline and keep the existing
        # twin verbatim instead of re-supplying an unchanged body. A
        # translate_edit member always carries both sides; nothing mutates — a
        # pure ledger record, like confirm (issue #566, minor #1).
        de_holder = ex._holder(item, "de")
        en_holder = ex._holder(item, "en")
        de_cell = de_holder.side("de") if de_holder is not None else None
        en_cell = en_holder.side("en") if en_holder is not None else None
        if de_cell is None or en_cell is None:
            raise _ItemError("keep_twin needs both sides present — supply the twin body instead")
        _reject_divergent_tags(de_cell, en_cell)
        return
    raise _ItemError(f"unsupported choice {choice!r}")


def item_with_side(item: DiffItem, side: Lang) -> DiffItem:
    """A view of ``item`` re-pointed at ``side`` as the moved side."""
    return evolve(item, side=side)


def _execute_mechanical(ex: _Executor, item: DiffItem) -> None:
    action = item.action
    side = item.side
    if action == "propagate_shared_edit":
        if side is None:
            raise _ItemError("item names no moved side")
        ex.propagate(item, side)
    elif action == "copy_new_shared":
        if side is None:
            raise _ItemError("item names no source side")
        ex.copy_new(item, side)
    elif action == "mirror_remove":
        if side is None:
            raise _ItemError("item names no removed side")
        ex.mirror_remove(item, side)
    elif action == "mirror_tags":
        if side is None:
            raise _ItemError("item names no moved side")
        ex.mirror_tags(item, side)
    elif action == "stamp_twin_id":
        if side is None:
            raise _ItemError("item names no unstamped side")
        ex.stamp_twin_id(item, side)
    elif action == "mirror_owner":
        if side is None:
            raise _ItemError("item names no moved side")
        ex.mirror_owner(item, side)
    elif action == "retarget_owner":
        ex.retarget_owner(item)
    elif action == "mirror_layout":
        if side is None:
            raise _ItemError("item names no moved side")
        ex.mirror_layout(item, side)
    elif action == "mirror_order":
        if side is None:
            raise _ItemError("item names no moved side")
        ex.mirror_order(item, side)
    elif action == "propagate_preamble":
        if side is None:
            raise _ItemError("item names no moved side")
        ex.propagate_preamble(item, side)
    else:  # pragma: no cover - the registry and this executor move together
        raise _ItemError(f"no executor for mechanical action '{action}'")


def _unmatched_decision_result(row: Decision, deck: BilingualDeck, diff: DeckDiff) -> ItemResult:
    """Classify a decision row that matched no item in the current diff.

    Three situations wore one verdict before schema 4:

    * the row names an ``action`` the member does not currently frame — the
      answer is aimed at a row that is not there, which is a mistake worth
      naming rather than a silent no-op;
    * the member exists and frames nothing at all: the answer is redundant,
      not wrong — the effect it asks for already holds, because a sibling
      pass, an earlier apply, or the other CLI spelling of this deck already
      did it. Reporting that as ``rejected`` is what made #649 read as
      "rejected" while the write had demonstrably landed;
    * the handle names no member of this deck — a genuine stale handle.

    Plus the fourth situation the member-centric reading predates: a
    **preamble handle** never names a member, so it is judged against the
    deck's preamble streams instead — ``already_applied`` when the part's
    frame could have existed and is gone, ``rejected`` when the deck does
    not carry companion files on both halves (no companion preamble row can
    ever have been framed for it).
    """
    framed = [i.action for i in diff.items if i.key == row.key]
    if row.action is not None and framed:
        return ItemResult(
            row.key,
            row.action,
            "rejected",
            f"this handle frames {', '.join(sorted(set(framed)))}, not "
            f"'{row.action}' — the answer names a row that is not in the "
            "current report",
        )
    if row.key in _PREAMBLE_HANDLES:
        # A preamble handle never names a member — the member lookup below
        # would misreport it as a stale key. Nothing framed means the
        # preamble state the answer asked for already holds (a prior apply
        # landed it, or the divergence was resolved another way) — but only
        # claim that for a part the deck actually carries on BOTH halves:
        # the differ never frames a companion preamble row unless both
        # companion files exist, so with a one-sided (or absent) companion
        # no such row can ever have been framed and the answer is wrong.
        part = row.key.rsplit("/", 2)[1]
        if part == "companion" and (
            deck.de_companion_preamble is None or deck.en_companion_preamble is None
        ):
            return ItemResult(
                row.key,
                row.action or "?",
                "rejected",
                "the deck does not carry companion files on both halves, so "
                "no companion preamble row can have been framed for it — "
                "check the key against `report --json`",
            )
        return ItemResult(
            row.key,
            row.action or "?",
            "already_applied",
            "the preamble frames nothing in the current report — the state "
            "this answer asks for already holds (nothing to do)",
        )
    try:
        member = deck.member_by_key(MemberKey.parse(row.key))
    except ValueError:
        member = None
    if member is not None:
        return ItemResult(
            row.key,
            row.action or "?",
            "already_applied",
            "the member frames nothing in the current report — the state this "
            "answer asks for already holds (nothing to do)",
        )
    return ItemResult(
        row.key,
        row.action or "?",
        "rejected",
        "no member with this handle in the deck — check the key against "
        "`report --json`, and note that a companion path and its deck are one "
        "deck with one ledger section",
    )


def _match_decision(rows: list[Decision], item: DiffItem, matched: set[int]) -> Decision | None:
    """The answer for ``item``: exact ``(key, action)`` first, else key-only.

    An action-less row is the ordinary case and answers whichever row the
    member frames. A row naming an action binds only to that action, so two
    rows on one member each land where they were aimed — and a row aimed at an
    action the member does not frame stays unmatched and is reported.
    """
    fallback: Decision | None = None
    for row in rows:
        if row.key != item.key or id(row) in matched:
            continue
        if row.action == item.action:
            return row
        if row.action is None and fallback is None:
            fallback = row
    return fallback


def apply_deck(
    bundle: LoadedBundle,
    deck: BilingualDeck,
    diff: DeckDiff,
    ledger: TopicLedger,
    deck_key: str,
    *,
    decisions: dict[str, Decision] | None = None,
    decision_rows: list[Decision] | None = None,
    only_members: set[str] | None = None,
    dry_run: bool = False,
    commit: str | None = None,
) -> ApplyOutcome:
    """Apply a deck's diff per item; update ``ledger`` in memory.

    Mechanical rows execute unconditionally (subject to ``only_members``);
    framed rows execute only with a valid decision, otherwise they stay
    ``pending``. On success the mutated bundle is re-parsed (abort on
    refusal), written atomically, and every landed item is recorded into
    ``ledger`` — which the **caller** persists (so a CLI can batch or refuse
    the save on a failed verify gate).
    """
    decisions = decisions or {}
    # One internal representation. ``decisions`` (handle -> answer) is the
    # convenience form every caller used before schema 4 and cannot hold two
    # answers for one member; ``decision_rows`` is the full document. Rows win
    # when both are given.
    rows = list(decision_rows) if decision_rows is not None else list(decisions.values())
    matched: set[int] = set()
    outcome = ApplyOutcome(dry_run=dry_run)
    ex = _Executor(bundle=bundle, deck=deck, comment_token=bundle.comment_token)
    originals = ex.emit_all()

    # Positional entries are recorded per pool (ordinals renumber together),
    # so a lone `confirm` on one pos-keyed cold member would silently bless
    # its still-unverified pool siblings. Require the whole pool's cold items
    # to be confirmed in the same document.
    incoherent_pools = _incoherent_pool_confirms(diff, {row.key: row for row in rows})

    ordered = sorted(enumerate(diff.items), key=lambda e: (_item_phase(e[1]), e[0]))
    landed: list[tuple[DiffItem, str]] = []  # (item, provenance)
    unresolved_items: list[DiffItem] = []  # pending / rejected / failed
    seen_decisions: set[str] = set()
    for _, item in ordered:
        if only_members is not None and item.key not in only_members:
            unresolved_items.append(item)  # a skipped pool sibling must not be blessed
            answered = _match_decision(rows, item, matched) is not None
            if answered:
                # Claim the handle so the unmatched-decision sweep below does
                # not then classify it: the answer was neither stale nor
                # already satisfied — the filter simply did not run it.
                seen_decisions.add(item.key)
            outcome.results.append(
                ItemResult(
                    item.key,
                    item.action,
                    "skipped",
                    "excluded by --member" + (" (its answer was not used)" if answered else ""),
                )
            )
            continue
        decision = _match_decision(rows, item, matched)
        if decision is not None:
            seen_decisions.add(item.key)
            if item.action in FRAMED_ACTIONS:
                # Only a FRAMED row consumes an answer. A member can frame a
                # mechanical row and a decision row under one handle (a
                # co-emitted `mirror_tags` beside a `verify_cold`, say); the
                # mechanical row executes on its own and must leave the answer
                # for its sibling, or the sibling silently stays pending.
                matched.add(id(decision))
        try:
            if (
                decision is not None
                and decision.choice == "confirm"
                and item.key.startswith("pos:")
                and _pool_scope(item) in incoherent_pools
            ):
                raise _ItemError(
                    "positional members are recorded per pool — confirm every cold "
                    "member of this (group, kind) pool in one document, or use "
                    "`sync record`"
                )
            if item.action in _RECORD_ONLY:
                landed.append((item, "apply"))
                outcome.results.append(ItemResult(item.key, item.action, "recorded", item.detail))
            elif item.action in MECHANICAL_ACTIONS:
                _execute_mechanical(ex, item)
                landed.append((item, "apply"))
                outcome.results.append(ItemResult(item.key, item.action, "applied", item.detail))
            elif decision is not None:
                blocker = (
                    _earlier_pool_removal(diff.items, item)
                    if decision.choice == "keep" and item.action == "remove_vs_edit"
                    else None
                )
                if blocker is not None:
                    # #824 review round 4: a keep behind an unresolved pool
                    # removal inserts at the wrong position (the shifted
                    # pairing anchors the walk on a cross-paired member).
                    # Defer: the pool freeze keeps the row unbanked, and the
                    # next pass re-pairs the pool so the keep lands in order.
                    unresolved_items.append(item)
                    outcome.results.append(
                        ItemResult(
                            item.key,
                            item.action,
                            "deferred",
                            f"the pool's pairing is shifted by the earlier "
                            f"{blocker.action} row {blocker.key} — resolve that row "
                            f"first, re-run report, then answer this one (a shifted "
                            f"pool takes one keep per pass)",
                        )
                    )
                else:
                    _execute_decision(ex, item, decision, bundle.comment_token)
                    landed.append((item, "agent"))
                    outcome.results.append(
                        ItemResult(
                            item.key,
                            item.action,
                            "applied",
                            f"decision: {decision.choice or 'body'}",
                        )
                    )
            else:
                assert item.action in FRAMED_ACTIONS
                unresolved_items.append(item)
                outcome.results.append(
                    ItemResult(
                        item.key,
                        item.action,
                        "pending",
                        item.detail,
                    )
                )
        except DeckWriteError as exc:
            status = "rejected" if decision is not None else "failed"
            unresolved_items.append(item)
            outcome.results.append(ItemResult(item.key, item.action, status, str(exc)))
    for row in rows:
        if id(row) not in matched and row.key not in seen_decisions:
            outcome.results.append(_unmatched_decision_result(row, deck, diff))

    if not landed:
        return outcome

    finals = ex.emit_all()
    changed = {key for key in finals if finals[key] != originals[key]}
    final_deck = deck
    if changed:
        parse = parse_bundle(
            finals[("de", "deck")] or "",
            finals[("en", "deck")] or "",
            finals[("de", "companion")],
            finals[("en", "companion")],
            comment_token=bundle.comment_token,
        )
        if parse.refusal is not None or parse.deck is None:
            reasons = (
                "; ".join(f"[{r.code}] {r.detail}" for r in parse.refusal.reasons)
                if parse.refusal
                else "no deck"
            )
            outcome.error = (
                f"the mutated bundle failed the re-parse gate ({reasons}) — nothing was written"
            )
            for i, result in enumerate(outcome.results):
                if result.status in ("applied", "recorded"):
                    outcome.results[i] = ItemResult(
                        result.key, result.action, "failed", "aborted by the re-parse gate"
                    )
            return outcome
        final_deck = parse.deck

    if dry_run:
        return outcome

    if changed:
        try:
            outcome.written_paths = write_changed_files(bundle, finals, changed)
        except OSError as exc:
            outcome.error = f"write failed: {exc}"
            return outcome
        outcome.wrote = True

    # Ledger updates for landed items — renames/migrations first, then the rest.
    fresh = snapshot_deck(final_deck, provenance="apply", commit=commit)
    target = ledger.decks.setdefault(deck_key, DeckLedger())
    priority = {"record_group_rename": 0, "record_key_migration": 1}
    frozen_pools = _frozen_pools(unresolved_items)
    rerecorded_pools: set[tuple[str, str]] = set()
    #: `record_neutral` keys to re-stamp once every record has landed (#764).
    structural_keys: set[str] = set()
    # Never bless a member with unresolved rows (#615 F2, fixes S3): a
    # landed row on a member whose framed sibling row is still pending /
    # rejected / failed must not record the member's fresh snapshot — that
    # would bank the unverified drifted state wholesale. The file mutation
    # stays; the ledger entry keeps its old baseline and the member
    # re-frames on the next report.
    unresolved_keys = {i.key for i in unresolved_items}
    # An ANSWERED conflict_tags is unresolved for recording purposes too
    # (adversarial review of #615): its resolution mutates one header line
    # and the member re-frames next pass — but the framed body row it
    # suppressed at the differ was never emitted, so it is in no
    # unresolved list. Without this, a co-landed same-key row (e.g. a
    # conflict_owner answered by the very same handle-keyed decision)
    # would _upsert the fresh snapshot and bank the suppressed body drift.
    # A landed no-evidence placement row (#654 round 2) is unresolved for
    # recording purposes exactly like conflict_tags: its verify_cold
    # sibling was suppressed at the differ, so nothing on this member was
    # reviewed — recording the fresh snapshot would bless an unreviewed
    # pair while bypassing every confirm guard. The placement mutation
    # stays; the member re-frames cold and banks on the next pass.
    unresolved_keys |= {
        item.key for item, _ in landed if item.action == "conflict_tags" or item.defer_recording
    }
    for item, provenance in sorted(landed, key=lambda e: priority.get(e[0].action, 2)):
        if item.key in unresolved_keys:
            if item.action == "conflict_tags" or item.defer_recording:
                continue  # records nothing BY DESIGN — no deferral suffix
            deferral = " (recording deferred: unresolved sibling item on this member)"
            for i, result in enumerate(outcome.results):
                if (
                    result.key == item.key
                    and result.action == item.action
                    and result.status in ("applied", "recorded")
                    and not result.reason.endswith(deferral)
                ):
                    # ItemResult is frozen — replace by index; (key, action)
                    # matching because keys repeat across a member's rows.
                    # A record-only row that landed nothing at all must not
                    # read "recorded" — downstream agents key on the status
                    # (design F2.1: honest status, not just an amended
                    # reason).
                    status = "deferred" if result.status == "recorded" else result.status
                    outcome.results[i] = ItemResult(
                        result.key, result.action, status, result.reason + deferral
                    )
                    break
            continue
        rerecorded_pools |= _record_item(
            target, fresh, item, provenance=provenance, frozen_pools=frozen_pools
        )
        if item.action == "record_neutral":
            structural_keys.add(item.key)
    # After every record, so no later pool re-record can reset these (#764).
    _stamp_structural(target, structural_keys)
    # Never bless the unresolved: a wholesale pool re-record must not trust
    # siblings whose framed items were left pending/rejected/failed — nor
    # the slot of an answered conflict_tags (which re-frames next pass).
    unresolved_members = [
        holder
        for item in unresolved_items
        for holder in (item.member, item.twin)
        if holder is not None
    ] + [
        holder
        for item, _ in landed
        if item.action == "conflict_tags" or item.defer_recording
        for holder in (item.member, item.twin)
        if holder is not None
    ]
    _drop_unresolved_from_pools(target, rerecorded_pools, unresolved_members)
    # The stale-pos: sweep must not touch members whose recording was
    # deferred above — the "stale" entry IS their surviving old baseline
    # (adversarial review of #615: a landed stamp_twin_id with a pending
    # framed sibling would otherwise lose the divergence's only record).
    _sweep_migrated_pos(target, [e for e in landed if e[0].key not in unresolved_keys])
    # Order-trust seeding (issue #654, review C3): a pass that resolved
    # every item may bank order trust for scopes whose sides agree — the
    # only way a confirm-seeded deck ever acquires order trust through the
    # verb loop. Divergent scopes are never seeded, and the caller's
    # structural gate still arbitrates whether this save happens at all.
    if not unresolved_keys:
        seed_order_scopes(target, fresh)
    # Order scopes must not carry handles the ledger cannot back: a landed
    # order item beside still-pending members of its scope would otherwise
    # record handles that prune_dangling_refs strips at save with a
    # spurious #718 damage warning (#654 review finding 2). Quiet, local
    # filter; the scope re-records complete once the members resolve.
    for scope_key, handles in list(target.member_order.items()):
        kept = [h for h in handles if h in target.members]
        if not kept:
            target.member_order.pop(scope_key)
        elif len(kept) != len(handles):
            target.member_order[scope_key] = kept
    outcome.ledger_changed = True
    return outcome


def _incoherent_pool_confirms(
    diff: DeckDiff, decisions: dict[str, Decision]
) -> set[tuple[str, str]]:
    """Pools where only *some* cold members received a confirm decision.

    Keyed strictly on ``choice == "confirm"`` — the only answer a *positional*
    cold member accepts. A ``body`` (issue #572) is rejected on a positional
    member (:func:`_apply_body_decision`), so it never lands a wholesale
    ``rerecord_pool``; counting it as a resolving answer here would bless the
    pool while the body itself is rejected downstream — the exact silent-bless
    bug this guard exists to prevent. Do not widen it to body/keep_twin.
    """
    cold_by_pool: dict[tuple[str, str], set[str]] = {}
    for item in diff.items:
        if item.action not in ("verify_cold", "verify_translation"):
            continue
        pool = _pool_scope(item)
        if pool is not None:
            cold_by_pool.setdefault(pool, set()).add(item.key)
    confirmed = {key for key, decision in decisions.items() if decision.choice == "confirm"}
    return {pool for pool, keys in cold_by_pool.items() if not keys <= confirmed}
