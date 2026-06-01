"""Apply engine for the single-language authoring workflow.

Phase 2 of Issue #166. Consumes the typed :class:`~clm.slides.sync_plan.SyncPlan`
produced by the Phase 1 classifier and writes the agreed changes to the target
deck(s), then advances the structural watermark so the next run is a no-op.

Scope of this engine:

- **remove** — delete the target-side cell whose source-side counterpart the
  author removed (deterministic, no LLM).
- **edit** — ask the existing :class:`SyncJudge` to propose the target-side
  rewrite for a cell that drifted on the source side, then write it.
- **move** — reorder the target deck's slide groups to match the source deck's
  order (deterministic, no LLM). Applied only when the rest of the pass is
  clean (real baseline, no errors, no deferred add/conflict), because a reorder
  is idempotent only once the watermark advances to record the new order.
- **add** — translate a brand-new id-less slide, mint its EN-authority id onto
  *both* decks, and insert the counterpart at the anchor (needs a
  ``translator``; without one, deferred). A narrative companion inherits the
  slide's id. id-carrying "missing counterpart" adds are a follow-up.
- **conflict** — isolated by design; counted as ``deferred`` so nothing is
  silent.

Atomicity: each proposal is all-or-nothing for its target cell; the two decks
are persisted once at the end, via a buffered temp-swap (Issue #190 item 1)
that writes **nothing** unless the whole pass is error-free — so a mid-pass
failure (e.g. an LLM error) can never leave a half-applied deck on disk. Each
deck's new text is rendered in memory and swapped in with an atomic
``os.replace``. The **watermark advances only on a complete, clean apply** (no
deferred proposals, no errors) — so un-applied work can never be silently baked
into the baseline and lost.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clm.infrastructure.llm.ollama_client import OllamaError
from clm.notebooks.slide_parser import parse_cell_header, parse_cells
from clm.slides.raw_cells import RawCell
from clm.slides.slug import resolve_collision, slugify
from clm.slides.sync_code import apply_code_structure
from clm.slides.sync_plan import (
    MEMBERSHIP_ROLES,
    Proposal,
    SyncPlan,
    ordered_sync_cells,
    watermark_rows,
)
from clm.slides.sync_translate import TranslationError
from clm.slides.sync_writeback import (
    CODE_ROLE,
    FileState,
    build_twin_cell,
    cell_content_hash,
    role_of,
)

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncWatermarkCache
    from clm.infrastructure.llm.ollama_client import SyncJudge
    from clm.slides.sync_translate import SlideTranslator

_SLIDE_ID_RE = re.compile(r'\s*slide_id="[^"]*"')
_SLIDE_ROLES = {"slide", "subslide"}

logger = logging.getLogger(__name__)

# Per-proposal decisions an interactive walker can hand to :func:`apply_plan`.
# ``apply`` / ``skip`` gate the deterministic kinds (edit / remove / move);
# ``de-wins`` / ``en-wins`` resolve a conflict by propagating the winning side.
# A conflict with no decision (or ``skip``) is deferred. The decisions map is
# keyed by ``id(proposal)`` of the proposals in the same :class:`SyncPlan`.
DECISION_APPLY = "apply"
DECISION_SKIP = "skip"
DECISION_DE_WINS = "de-wins"
DECISION_EN_WINS = "en-wins"

__all__ = [
    "DECISION_APPLY",
    "DECISION_DE_WINS",
    "DECISION_EN_WINS",
    "DECISION_SKIP",
    "ApplyResult",
    "apply_plan",
    "content_index",
]


@dataclass
class ApplyResult:
    """Outcome of applying a :class:`SyncPlan`."""

    applied_edit: int = 0
    applied_remove: int = 0
    applied_move: int = 0
    applied_add: int = 0
    applied_rename: int = 0
    in_sync: int = 0  # an edit the judge decided needed no change
    deferred: int = 0  # conflict (or moves/adds declined this pass)
    watermark_recorded: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return (
            self.applied_edit
            + self.applied_remove
            + self.applied_move
            + self.applied_add
            + self.applied_rename
        )

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def apply_plan(
    plan: SyncPlan,
    *,
    judge: SyncJudge | None,
    translator: SlideTranslator | None = None,
    watermark_cache: SyncWatermarkCache | None = None,
    decisions: dict[int, str] | None = None,
) -> ApplyResult:
    """Apply ``plan``'s proposals to the decks and advance the watermark.

    ``judge`` provides the target-side rewrite for ``edit`` proposals (``None``
    records each edit as an error). ``translator`` produces the counterpart for
    a brand-new id-less slide (``None`` defers adds). Applies remove / edit /
    move / id-less add; conflicts and id-carrying adds are ``deferred``. The two
    decks are persisted once at the end via a buffered temp-swap, but **only on
    an error-free pass** (no apply-time or classifier error) — an erroring pass
    writes neither deck (Issue #190 item 1). The watermark advances only on a
    clean, complete apply.

    ``decisions`` drives interactive review (``None`` = the batch default, which
    applies every deterministic kind and defers conflicts). When provided, it
    maps ``id(proposal)`` to a decision (:data:`DECISION_APPLY` /
    :data:`DECISION_SKIP` for edit / remove / move; :data:`DECISION_DE_WINS` /
    :data:`DECISION_EN_WINS` / :data:`DECISION_SKIP` for a conflict). A skipped
    proposal is counted as ``deferred`` so the watermark cannot advance over it.
    Add / rename proposals are always applied (they are non-destructive and the
    counterpart is reviewed in the resulting ``git diff``).
    """
    result = ApplyResult()

    # Pre-apply content (parser-stripped) for the judge, keyed by (slide_id,
    # role). Read before FileState mutates anything.
    de_content = content_index(plan.de_path, "de")
    en_content = content_index(plan.en_path, "en")

    de_state = FileState.load(plan.de_path)
    en_state = FileState.load(plan.en_path)

    moves: list[Proposal] = []
    # (slide_id, role) of every TRUE deferral this pass: an unresolved conflict
    # or a user-skipped edit/remove/move. The per-cell partial watermark advance
    # preserves exactly these cells at their old baseline so they re-surface next
    # run; everything else (an applied edit, an unchanged cell, and an edit the
    # judge reconciled with an in_sync verdict) banks its current state. in_sync
    # is a reconciliation decision — "the target already reflects the source",
    # per SyncProposal — so it advances, the same as on a fully clean pass; were
    # it preserved it would re-surface every run forever (the judge re-declines).
    deferred_keys: set[tuple[str, str]] = set()
    for proposal in plan.proposals:
        kind = proposal.kind
        if kind == "remove":
            if _accepted(decisions, proposal):
                _apply_remove(proposal, de_state, en_state, result)
            else:
                result.deferred += 1
                _note_deferred(deferred_keys, proposal)
        elif kind == "edit":
            if _accepted(decisions, proposal):
                _apply_edit(
                    proposal, de_state, en_state, de_content, en_content, judge, translator, result
                )
            else:
                result.deferred += 1
                _note_deferred(deferred_keys, proposal)
        elif kind == "move":
            if _accepted(decisions, proposal):
                moves.append(proposal)
            else:
                result.deferred += 1
                _note_deferred(deferred_keys, proposal)
        elif kind == "conflict":
            _apply_conflict(
                proposal,
                decisions,
                de_state,
                en_state,
                de_content,
                en_content,
                judge,
                translator,
                result,
                deferred_keys,
            )
        # "add" / "rename" are handled by _apply_adds below (always applied).

    # Adds run before moves so a freshly-inserted slide takes part in any
    # reorder. Adds are sticky via the stamped id (a re-run no longer sees an
    # id-less cell), so unlike moves they apply even on a *deferred* (non-clean)
    # pass — but, like every cell, the stamped id only reaches disk on an
    # error-free pass (the end-of-pass flush is gated on no errors).
    _apply_adds(de_state, en_state, result, plan, translator)

    # Moves are applied last, and only if the rest of the pass is clean (so the
    # watermark will advance and the reorder stays idempotent).
    if moves:
        _apply_moves(moves, de_state, en_state, result, plan)

    # Structural pass: propagate the cells the per-(slide_id, role) walk does not
    # reach — language-neutral code (copied verbatim across halves), id-less
    # localized code (translated) — and rebuild the cell order of changed slide
    # groups, reusing the narrative / aux / id'd-code twins the steps above just
    # placed. Cross-group code moves fall out of rebuilding both groups from the
    # source. Runs after adds/moves so every twin it pulls is in place.
    baseline_anchors = _baseline_anchor_hashes(watermark_cache, plan.de_path, plan.en_path)
    apply_code_structure(plan, de_state, en_state, translator, result, baseline_anchors)

    # Fail-safe: a complete resolution leaves no duplicate id behind. If one
    # survives (a should-not-happen bug), error so the watermark cannot advance
    # over a corrupt state and the situation is surfaced rather than baselined.
    _flag_residual_duplicates(de_state, "de", result)
    _flag_residual_duplicates(en_state, "en", result)
    # On an otherwise-clean pass both decks must carry the same (slide_id, role)
    # set; a one-sided orphan is a silent cross-deck divergence.
    if not result.errors and result.deferred == 0 and not plan.has_errors:
        _flag_cross_deck_orphans(de_state, en_state, result)

    # Buffered temp-swap (Issue #190 item 1): persist both decks atomically, and
    # ONLY when the whole pass is error-free — neither an apply-time error
    # (``result.has_errors``) nor a classifier error (``plan.has_errors``, e.g. an
    # unresolvable duplicate id). This matches design §11 ("write only if the
    # whole pass is error-free") and future-proofs the later phases that add new
    # classifier errors (§6/§10) which are *not* backed by a physical residual
    # duplicate, so they would otherwise slip the result-error fail-safe. A
    # *deferred*-but-error-free pass still writes — applying the deterministic
    # edits and partial-advancing the watermark is the designed outcome; only a
    # genuine error rolls the whole pass back, so a mid-pass LLM failure never
    # leaves a half-applied deck on disk. The watermark advance below
    # independently declines on any plan issue / error, so "wrote nothing" and
    # "held the watermark" stay consistent.
    if not result.has_errors and not plan.has_errors:
        _flush_states_atomically(de_state, en_state)

    # Watermark advance. A fully clean pass advances the whole deck. A
    # *content-only* partial pass (edits/conflicts only, so structure is
    # unchanged) advances the reconciled cells per-cell while preserving the
    # deferred cells' pre-conflict baseline — so a deferred conflict no longer
    # forces every reconciled edit to re-surface next run. Any other partial
    # pass holds the whole watermark (nothing un-applied is ever baselined).
    #
    # ``not plan.issues`` gates BOTH paths: a both-decks reorder or an ambiguous
    # de/en state is emitted as a *warning* (no proposal, no error) whose order/
    # ambiguity is deliberately not reconciled. Advancing over it — on either the
    # full or the partial path — would bake the new positions and silently lose
    # the "resolve manually" signal, so any issue holds the whole watermark.
    if watermark_cache is not None and not plan.issues:
        if _pass_is_clean(plan, result):
            _record_watermark(watermark_cache, plan.de_path, plan.en_path)
            result.watermark_recorded = True
        elif (
            _eligible_for_partial_advance(plan, result)
            # Completeness invariant: in a content-only pass every deferral is one
            # distinct (slide_id, role), so the recorded keys must account for
            # *every* deferral. If they don't (an unforeseen deferral with no
            # key), hold the whole watermark rather than risk advancing over it.
            and len(deferred_keys) == result.deferred
            and _record_watermark_partial(
                watermark_cache, plan.de_path, plan.en_path, deferred_keys
            )
        ):
            result.watermark_recorded = True

    return result


def _note_deferred(deferred_keys: set[tuple[str, str]], proposal: Proposal) -> None:
    """Record a deferred proposal's ``(slide_id, role)`` for the partial advance."""
    if proposal.slide_id is not None:
        deferred_keys.add((proposal.slide_id, proposal.role))


def _accepted(decisions: dict[int, str] | None, proposal: Proposal) -> bool:
    """Whether a deterministic proposal (edit / remove / move) should apply.

    ``None`` is batch mode — every deterministic kind applies. Otherwise the
    proposal applies only on an explicit :data:`DECISION_APPLY`.
    """
    if decisions is None:
        return True
    return decisions.get(id(proposal)) == DECISION_APPLY


def _conflict_decision(decisions: dict[int, str] | None, proposal: Proposal) -> str:
    """The resolution for a conflict proposal (defaults to skip/defer)."""
    if decisions is None:
        return DECISION_SKIP
    return decisions.get(id(proposal), DECISION_SKIP)


def _conflict_as_edit(proposal: Proposal, direction: str) -> Proposal:
    """Recast a resolved conflict as an ``edit`` flowing the winning direction."""
    return Proposal(
        kind="edit",
        role=proposal.role,
        direction=direction,
        slide_id=proposal.slide_id,
    )


def _apply_conflict(
    proposal: Proposal,
    decisions: dict[int, str] | None,
    de_state: FileState,
    en_state: FileState,
    de_content: dict[tuple[str, str], str],
    en_content: dict[tuple[str, str], str],
    judge: SyncJudge | None,
    translator: SlideTranslator | None,
    result: ApplyResult,
    deferred_keys: set[tuple[str, str]],
) -> None:
    """Resolve a conflict per its decision, or defer it.

    ``de-wins`` / ``en-wins`` propagate the winning side as an ordinary edit
    (the judge rewrites the losing side to match); any other decision defers,
    recording the key so the per-cell advance keeps its pre-conflict baseline
    and the conflict re-surfaces next run.
    """
    decision = _conflict_decision(decisions, proposal)
    if decision == DECISION_DE_WINS:
        _apply_edit(
            _conflict_as_edit(proposal, "de->en"),
            de_state,
            en_state,
            de_content,
            en_content,
            judge,
            translator,
            result,
        )
    elif decision == DECISION_EN_WINS:
        _apply_edit(
            _conflict_as_edit(proposal, "en->de"),
            de_state,
            en_state,
            de_content,
            en_content,
            judge,
            translator,
            result,
        )
    else:
        result.deferred += 1
        _note_deferred(deferred_keys, proposal)


def _pass_is_clean(plan: SyncPlan, result: ApplyResult) -> bool:
    """Whether the pass fully reconciled both decks against a real baseline.

    The single predicate shared by the move gate and the watermark-advance
    decision so the two can never drift apart: a real baseline, no
    classifier errors, no apply-time errors, and nothing deferred.
    """
    return plan.has_baseline and not plan.has_errors and not result.errors and result.deferred == 0


def _flag_residual_duplicates(state: FileState, label: str, result: ApplyResult) -> None:
    """Error on any ``(slide_id, role)`` left duplicated after the apply walk."""
    seen: set[tuple[str, str]] = set()
    for cell in state.cells:
        role = role_of(cell.metadata)
        sid = cell.metadata.slide_id
        if role is None or sid is None:
            continue
        key = (sid, role)
        if key in seen:
            result.errors.append(f"unresolved duplicate slide_id {sid!r}/{role} on {label}")
        else:
            seen.add(key)


def _sync_keys(state: FileState) -> set[tuple[str, str]]:
    """The set of ``(slide_id, role)`` sync keys in a deck."""
    keys: set[tuple[str, str]] = set()
    for cell in state.cells:
        role = role_of(cell.metadata)
        sid = cell.metadata.slide_id
        if role is not None and sid is not None:
            keys.add((sid, role))
    return keys


def _flag_cross_deck_orphans(de_state: FileState, en_state: FileState, result: ApplyResult) -> None:
    """Error on any sync key present on one deck but not the other."""
    de_keys, en_keys = _sync_keys(de_state), _sync_keys(en_state)
    for sid, role in sorted(de_keys - en_keys):
        result.errors.append(f"slide_id {sid!r}/{role} present on de but missing on en")
    for sid, role in sorted(en_keys - de_keys):
        result.errors.append(f"slide_id {sid!r}/{role} present on en but missing on de")


# ---------------------------------------------------------------------------
# Per-kind appliers
# ---------------------------------------------------------------------------


def _apply_remove(
    proposal: Proposal,
    de_state: FileState,
    en_state: FileState,
    result: ApplyResult,
) -> None:
    if proposal.slide_id is None:
        result.errors.append(f"remove {proposal.role}: proposal has no slide_id")
        return
    target_state = en_state if proposal.direction == "de->en" else de_state
    if target_state.delete_cell(proposal.slide_id, proposal.role):
        result.applied_remove += 1
    else:
        result.errors.append(f"remove {proposal.slide_id}/{proposal.role}: target cell not found")


def _apply_edit(
    proposal: Proposal,
    de_state: FileState,
    en_state: FileState,
    de_content: dict[tuple[str, str], str],
    en_content: dict[tuple[str, str], str],
    judge: SyncJudge | None,
    translator: SlideTranslator | None,
    result: ApplyResult,
) -> None:
    if proposal.slide_id is None:
        result.errors.append(f"edit {proposal.role}: proposal has no slide_id")
        return
    sid = proposal.slide_id
    key = (sid, proposal.role)
    if proposal.direction == "de->en":
        source_lang, target_lang = "de", "en"
        source_body = de_content.get(key, "")
        target_body = en_content.get(key, "")
        source_state, target_state = de_state, en_state
    else:
        source_lang, target_lang = "en", "de"
        source_body = en_content.get(key, "")
        target_body = de_content.get(key, "")
        source_state, target_state = en_state, de_state

    # A localized code cell is reconciled by re-translating the source body (the
    # markdown judge's prompt does not fit runnable code); only the human-facing
    # string literals / comments differ across languages.
    if proposal.role == CODE_ROLE:
        _apply_code_edit(
            sid, source_state, target_state, source_lang, target_lang, translator, result
        )
        return

    if judge is None:
        result.errors.append(f"edit {sid}/{proposal.role}: no judge (LLM unavailable)")
        return

    try:
        sync_proposal = judge.propose(
            source_body, target_body, source_lang=source_lang, target_lang=target_lang
        )
    except OllamaError as exc:
        logger.info("edit judge failed on %s/%s: %s", sid, proposal.role, exc)
        result.errors.append(f"edit {sid}/{proposal.role}: {exc}")
        return

    if sync_proposal.verdict != "update":
        result.in_sync += 1
        return

    if target_state.replace_cell_body(sid, proposal.role, sync_proposal.proposed_text):
        result.applied_edit += 1
    else:
        result.errors.append(f"edit {sid}/{proposal.role}: target cell not found")


def _apply_code_edit(
    sid: str,
    source_state: FileState,
    target_state: FileState,
    source_lang: str,
    target_lang: str,
    translator: SlideTranslator | None,
    result: ApplyResult,
) -> None:
    """Reconcile a localized code cell edit by re-translating the source body."""
    if translator is None:
        result.errors.append(f"edit {sid}/code: no translator (LLM unavailable)")
        return
    src_cell = source_state.find_cell(sid, CODE_ROLE)
    if src_cell is None:
        result.errors.append(f"edit {sid}/code: source cell not found")
        return
    try:
        new_body = translator.translate(
            source_body=src_cell.body.rstrip("\n"),
            source_lang=source_lang,
            target_lang=target_lang,
            role=CODE_ROLE,
        )
    except TranslationError as exc:
        result.errors.append(f"edit {sid}/code: translation failed: {exc}")
        return
    if target_state.replace_cell_body(sid, CODE_ROLE, new_body):
        result.applied_edit += 1
    else:
        result.errors.append(f"edit {sid}/code: target cell not found")


# ---------------------------------------------------------------------------
# Add (translate + mint + insert)
# ---------------------------------------------------------------------------


def _apply_adds(
    de_state: FileState,
    en_state: FileState,
    result: ApplyResult,
    plan: SyncPlan,
    translator: SlideTranslator | None,
) -> None:
    """Translate and insert counterparts for new and copy-pasted cells.

    Three cases:

    - **id-carrying add**: a new cell already minted with a ``slide_id`` on one
      side only — translate and insert the twin under the *same* id at its
      source anchor (:func:`_add_idcarrying_one_direction`). Covers a new id'd
      slide, narrative companion, aux-markdown cell, or localized code cell.
    - **add** (id-less cell): a new slide mints a fresh EN-authority id stamped
      onto *both* siblings; a narrative companion inherits the preceding slide's
      id; the translated counterpart is inserted on the other deck.
    - **rename** (copy-pasted duplicate id): the copy *slide* cell chosen by
      :func:`_identify_copy_slides` (by position, so identical copies don't
      defeat it) is re-minted to a fresh id — its companions follow by
      group-adjacency — leaving the original alone, then handled like an add.

    All cases are sticky: the id is stamped into the in-memory deck and reaches
    disk on any error-free pass, so they apply even on a *deferred* pass. An
    erroring pass writes nothing (the end-of-pass flush is error-gated) and the
    add simply re-surfaces next run.
    """
    add_props = [p for p in plan.proposals if p.kind == "add"]
    rename_props = [p for p in plan.proposals if p.kind == "rename"]
    if not add_props and not rename_props:
        return

    idd = [p for p in add_props if p.slide_id is not None]
    idless = [p for p in add_props if p.slide_id is None]

    if translator is None:
        result.deferred += len(idd) + len(idless) + len(rename_props)
        result.errors.append("add/rename present but no translator available")
        return

    # id-carrying adds: a new cell (slide / subslide / narrative / aux markdown /
    # localized code) already minted with a slide_id on one side only — e.g. a
    # deck whose ids were assigned before the split, where the author then adds a
    # new id'd slide on one half. Translate and insert the twin under the *same*
    # id (no minting, no collision) at its source-order anchor.
    if idd:
        de_keys = {(p.slide_id, p.role) for p in idd if p.direction == "de->en"}
        en_keys = {(p.slide_id, p.role) for p in idd if p.direction == "en->de"}
        if de_keys:
            _add_idcarrying_one_direction(
                de_state, en_state, "de", "en", de_keys, translator, result
            )
        if en_keys:
            _add_idcarrying_one_direction(
                en_state, de_state, "en", "de", en_keys, translator, result
            )

    if len(idless) + len(rename_props) == 0:
        return

    process_idless = True
    if idless and len({p.direction for p in idless}) > 1:
        # id-less new slides on BOTH decks: off the single-language path. Defer
        # the adds (renames are independent and still apply).
        result.deferred += len(idless)
        result.errors.append(
            "id-less new slides on both decks — edit one deck at a time (deferred)"
        )
        process_idless = False

    de_copy_ids = _identify_copy_slides(
        de_state, "de", [p for p in rename_props if p.direction == "de->en"]
    )
    en_copy_ids = _identify_copy_slides(
        en_state, "en", [p for p in rename_props if p.direction == "en->de"]
    )

    used_ids = {
        cell.metadata.slide_id
        for state in (de_state, en_state)
        for cell in state.cells
        if cell.metadata.slide_id
    }
    _add_one_direction(
        de_state, en_state, "de", "en", translator, used_ids, result, de_copy_ids, process_idless
    )
    _add_one_direction(
        en_state, de_state, "en", "de", translator, used_ids, result, en_copy_ids, process_idless
    )


def _add_idcarrying_one_direction(
    source_state: FileState,
    target_state: FileState,
    source_lang: str,
    target_lang: str,
    add_keys: set[tuple[str | None, str]],
    translator: SlideTranslator,
    result: ApplyResult,
) -> None:
    """Insert the twin of each id-carrying new cell, under the same id, at anchor.

    Walks the source deck once. A sync cell whose ``(slide_id, role)`` is in
    ``add_keys`` is brand-new (present on the source only): translate it, build
    a twin that preserves its id, tags, and cell type (markdown vs code) with the
    language swapped, and insert it after the previously-seen cell that already
    exists on the target. Any other sync cell is an existing one whose twin is
    already on the target and serves as the running anchor. Language-neutral /
    id-less cells are skipped here (handled structurally by
    :mod:`clm.slides.sync_code`); the structural pass later rebuilds the order of
    every changed group, so the anchor here only needs to be *near* right.
    """
    anchor: tuple[str, str] | None = None
    for cell in list(source_state.cells):
        role = role_of(cell.metadata)
        if role is None or cell.metadata.lang != source_lang:
            continue
        sid = cell.metadata.slide_id
        if sid is None:
            continue
        key = (sid, role)
        if key not in add_keys:
            anchor = key  # an existing cell already twinned on the target
            continue
        target_body = _translate(cell, source_lang, target_lang, translator, role, result)
        if target_body is None:
            continue  # _translate recorded the deferral/error; keep the anchor
        twin = build_twin_cell(cell, target_lang, target_body)
        _insert_at_anchor(target_state, anchor, twin)
        result.applied_add += 1
        anchor = key


def _identify_copy_slides(
    source_state: FileState, source_lang: str, rename_props: list[Proposal]
) -> set[int]:
    """Pick the physical copy *slide* cell for each rename, by position.

    For an edited copy the hash is already unique; for a byte-identical copy
    several slide cells share the hash, so each rename is bound to the matching
    cell whose sync-cell index is closest to its ``source_position`` — the copy
    the classifier designated, never the in-place original. Returns the set of
    ``id()`` of the cells to re-mint.
    """
    by_sig: dict[tuple[str, str, str], list[tuple[int, RawCell]]] = {}
    pos = -1
    for cell in source_state.cells:
        role = role_of(cell.metadata)
        if role is None or cell.metadata.lang != source_lang:
            continue
        pos += 1
        sid = cell.metadata.slide_id
        if role in _SLIDE_ROLES and sid is not None:
            by_sig.setdefault((sid, role, cell_content_hash(cell.body)), []).append((pos, cell))

    copy_ids: set[int] = set()
    for prop in rename_props:
        if prop.slide_id is None or prop.content_hash is None:
            continue
        want = prop.source_position if prop.source_position is not None else 0
        candidates = [
            (p, c)
            for (p, c) in by_sig.get((prop.slide_id, prop.role, prop.content_hash), [])
            if id(c) not in copy_ids
        ]
        if candidates:
            copy_ids.add(id(min(candidates, key=lambda pc: abs(pc[0] - want))[1]))
    return copy_ids


def _add_one_direction(
    source_state: FileState,
    target_state: FileState,
    source_lang: str,
    target_lang: str,
    translator: SlideTranslator,
    used_ids: set[str],
    result: ApplyResult,
    copy_slide_ids: set[int],
    process_idless: bool,
) -> None:
    """Walk the source deck, minting ids for new and copy-pasted slides.

    ``copy_slide_ids`` is the set of ``id()`` of copy-paste *slide* cells to
    re-mint (chosen by :func:`_identify_copy_slides`). A copied slide's narrative
    companions are re-minted by group-adjacency — ``renaming_from`` holds the
    copy slide's old id so each following same-id companion inherits the freshly
    minted id — rather than by a per-companion hash match (which identical
    companions defeat).
    """
    current_slide_id: str | None = None
    renaming_from: str | None = None  # old id of a copy slide whose companions follow
    anchor: tuple[str, str] | None = None
    for cell in list(source_state.cells):
        role = role_of(cell.metadata)
        if role is None or cell.metadata.lang != source_lang:
            continue
        sid = cell.metadata.slide_id

        if role in _SLIDE_ROLES:
            is_copy = id(cell) in copy_slide_ids
            if sid is not None and not is_copy:
                current_slide_id = sid  # existing slide; anchors what follows
                renaming_from = None
                anchor = (sid, role)
                continue
            if sid is None and not process_idless:
                renaming_from = None
                continue  # deferred parallel id-less add
            # id-less add OR copy slide: translate, mint EN-authority id, place.
            target_body = _translate(cell, source_lang, target_lang, translator, role, result)
            if target_body is None:
                renaming_from = None
                continue
            en_body = target_body if target_lang == "en" else cell.body.rstrip("\n")
            new_id = resolve_collision(_slug_or_default(en_body), used_ids)
            used_ids.add(new_id)
            _place_new_cell(
                cell, new_id, target_lang, target_body, source_state, target_state, anchor
            )
            anchor = (new_id, role)
            current_slide_id = new_id
            renaming_from = sid if is_copy else None  # for a copy, sid is the old dup id
            if is_copy:
                result.applied_rename += 1
            else:
                result.applied_add += 1
            continue

        # narrative role
        is_copy_companion = renaming_from is not None and sid is not None and sid == renaming_from
        if sid is not None and not is_copy_companion:
            anchor = (sid, role)  # existing companion (of a non-copied slide)
            continue
        if sid is None and not process_idless:
            continue
        if current_slide_id is None:
            result.deferred += 1
            result.errors.append(f"add {role}: narrative with no preceding slide — deferred")
            continue
        target_body = _translate(cell, source_lang, target_lang, translator, role, result)
        if target_body is None:
            continue
        # Companions inherit the slide's id (the freshly minted one for a copy).
        _place_new_cell(
            cell, current_slide_id, target_lang, target_body, source_state, target_state, anchor
        )
        anchor = (current_slide_id, role)
        if is_copy_companion:
            result.applied_rename += 1
        else:
            result.applied_add += 1


def _translate(
    cell: RawCell,
    source_lang: str,
    target_lang: str,
    translator: SlideTranslator,
    role: str,
    result: ApplyResult,
) -> str | None:
    """Translate a cell body, recording a deferral + error on failure.

    rstrip drops the terminal-newline artifact so the translator input does not
    depend on cell position.
    """
    try:
        return translator.translate(
            source_body=cell.body.rstrip("\n"),
            source_lang=source_lang,
            target_lang=target_lang,
            role=role,
        )
    except TranslationError as exc:
        result.deferred += 1
        result.errors.append(f"{role}: translation failed: {exc}")
        return None


def _place_new_cell(
    cell: RawCell,
    new_id: str,
    target_lang: str,
    target_body: str,
    source_state: FileState,
    target_state: FileState,
    anchor: tuple[str, str] | None,
) -> None:
    """Stamp the source cell's id and insert the translated counterpart."""
    _stamp_slide_id(cell, new_id)
    source_state.dirty = True
    new_cell = _build_cell(target_lang, cell.metadata.tags, new_id, target_body)
    _insert_at_anchor(target_state, anchor, new_cell)


def _slug_or_default(en_body: str) -> str:
    return slugify(_extract_heading(en_body)) or "slide"


def _extract_heading(body: str) -> str:
    """Best-effort slide heading text from a percent-format cell body.

    Drops the ``# `` comment prefix as a fixed prefix (not a char set, so a
    ``**bold**`` lead-in survives), returns a Markdown heading's text, and
    treats a line as a bullet only when it starts with an actual ``-``/``*``/
    ``+`` *list marker* (followed by whitespace). ``slugify`` then strips any
    residual Markdown.
    """
    for raw in body.split("\n"):
        if raw.startswith("# "):
            md = raw[2:].strip()
        elif raw.startswith("#"):
            md = raw[1:].strip()
        else:
            md = raw.strip()
        if not md:
            continue
        heading = re.match(r"#{1,6}\s+(.*)", md)
        if heading:
            return heading.group(1).strip()
        if not re.match(r"[-*+]\s", md):
            return md
    return ""


def _stamp_slide_id(cell: RawCell, slide_id: str) -> None:
    """Write ``slide_id="…"`` onto a cell header (mirrors assign-ids)."""
    stripped = _SLIDE_ID_RE.sub("", cell.lines[0]).rstrip()
    header = f'{stripped} slide_id="{slide_id}"'
    cell.lines[0] = header
    cell.metadata = parse_cell_header(header)


def _build_cell(lang: str, tags: list[str], slide_id: str, body: str) -> RawCell:
    """Build a fresh markdown RawCell carrying the translated counterpart.

    The body is built bare (no leading/trailing blank lines); the insert
    primitive grants the deck's inter-cell separator based on final position.
    """
    tag_repr = ", ".join(f'"{t}"' for t in tags) if tags else '"slide"'
    header = f'# %% [markdown] lang="{lang}" tags=[{tag_repr}] slide_id="{slide_id}"'
    body_lines = body.split("\n")
    while body_lines and body_lines[0] == "":  # drop a stray leading blank
        body_lines.pop(0)
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
    return RawCell(lines=[header, *body_lines], line_number=0, metadata=parse_cell_header(header))


def _insert_at_anchor(
    target_state: FileState, anchor: tuple[str, str] | None, new_cell: RawCell
) -> None:
    if anchor is None or not target_state.insert_after(anchor[0], anchor[1], new_cell):
        target_state.insert_before_first_sync_cell(new_cell)


def _apply_moves(
    moves: list[Proposal],
    de_state: FileState,
    en_state: FileState,
    result: ApplyResult,
    plan: SyncPlan,
) -> None:
    """Reorder the target deck's slide groups to match the source deck.

    Gated on the rest of the pass being clean (real baseline, no errors, no
    deferred add/conflict) so the watermark will advance — a reorder is
    idempotent only once the new order is recorded. Mixed-direction moves
    (each deck reordered different cells) are ambiguous and deferred.

    A group-level reorder cannot express every move the per-cell classifier
    can detect (e.g. a narrative companion reassigned to a *different* slide).
    So we only commit when the reorder actually reconciles the full
    ``(slide_id, role)`` order with the source; otherwise the moves are
    deferred and surfaced rather than silently counted as applied (which would
    advance the watermark over a divergence).
    """
    if not _pass_is_clean(plan, result):
        result.deferred += len(moves)
        return

    directions = {m.direction for m in moves}
    if len(directions) > 1:
        result.deferred += len(moves)
        result.errors.append("moves in both directions; order ambiguous — deferred")
        return

    if "de->en" in directions:
        source_state, target_state = de_state, en_state
    else:
        source_state, target_state = en_state, de_state

    reordered = _group_reorder(target_state.cells, _group_order(source_state.cells))
    candidate = reordered if reordered is not None else target_state.cells
    if _sync_key_order(candidate) == _sync_key_order(source_state.cells):
        if reordered is not None:
            sep = target_state.separator_blanks()
            original_last = target_state.cells[-1] if target_state.cells else None
            target_state.cells = reordered
            target_state.dirty = True
            if original_last is not None:
                target_state.normalize_displaced_last(original_last, sep)
        result.applied_move += len(moves)
    else:
        result.deferred += len(moves)
        result.errors.append(
            "some moves are not expressible by a slide-group reorder "
            "(narrative companion reassigned to a different slide) — deferred"
        )


def _group_order(cells: list[RawCell]) -> list[str]:
    """Ordered ``slide_id``s of the slide/subslide cells (one per slide group)."""
    return [c.metadata.slide_id for c in cells if c.metadata.is_slide_start and c.metadata.slide_id]


def _sync_key_order(cells: list[RawCell]) -> list[tuple[str, str]]:
    """Ordered ``(slide_id, role)`` of every id-carrying sync cell.

    The full-granularity order the classifier reasons about — used to confirm
    a group-level reorder actually reconciled both decks before committing it.
    """
    out: list[tuple[str, str]] = []
    for cell in cells:
        role = role_of(cell.metadata)
        if role is not None and cell.metadata.slide_id:
            out.append((cell.metadata.slide_id, role))
    return out


def _split_groups(cells: list[RawCell]) -> tuple[list[RawCell], list[list[RawCell]]]:
    """Split ``cells`` into a head and a list of slide groups.

    A group is a slide/subslide cell plus every following cell (narrative
    companions, code) until the next slide/subslide. Cells before the first
    slide (j2 header, intro) are the head and never move.
    """
    head: list[RawCell] = []
    groups: list[list[RawCell]] = []
    current: list[RawCell] | None = None
    for cell in cells:
        if cell.metadata.is_slide_start:
            current = [cell]
            groups.append(current)
        elif current is None:
            head.append(cell)
        else:
            current.append(cell)
    return head, groups


def _group_reorder(cells: list[RawCell], source_order: list[str]) -> list[RawCell] | None:
    """Return ``cells`` with slide groups reordered to follow ``source_order``.

    Pure: returns a new cell list, or ``None`` if the group order is already
    correct. Groups whose id is absent from ``source_order`` keep their
    relative position (stable sort); each group's cells move as a verbatim
    unit, so the round-trip is preserved.
    """
    head, groups = _split_groups(cells)
    if not groups:
        return None
    index = {sid: i for i, sid in enumerate(source_order)}
    fallback = len(source_order) + 1

    def _key(group: list[RawCell]) -> int:
        sid = group[0].metadata.slide_id
        return index.get(sid, fallback) if sid is not None else fallback

    reordered = sorted(groups, key=_key)
    if [g[0].metadata.slide_id for g in groups] == [g[0].metadata.slide_id for g in reordered]:
        return None
    return head + [cell for group in reordered for cell in group]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flush_states_atomically(de_state: FileState, en_state: FileState) -> None:
    """Persist both decks via a per-deck temp-file + atomic ``os.replace`` swap.

    Issue #190 item 1: the previous code called :meth:`FileState.flush` on each
    deck unconditionally, so a pass that errored mid-way still persisted the edits
    that happened to succeed. Here both decks' new text is rendered in memory
    first and each file is replaced in a single atomic step, so a crash can never
    leave a half-written deck. (The two files are still two writes — the only
    residual non-atomic window is the gap between the two ``os.replace`` calls,
    narrowed as far as a two-file swap allows.) The caller gates this on an
    error-free pass, so an erroring pass writes nothing at all.

    Bytes are identical to the old per-deck ``flush`` path: :meth:`FileState.render`
    reuses flush's reconstruct + terminal-newline contract, and the swap writes
    utf-8 / LF with no newline translation.
    """
    pending = [(state, state.render()) for state in (de_state, en_state) if state.dirty]
    for state, text in pending:
        _atomic_write_text(state.path, text)
        state.dirty = False


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` via a same-directory temp file.

    Same byte contract as :meth:`FileState.flush` (utf-8, LF, no translation).
    The temp file is created in ``path``'s own directory so ``os.replace`` is a
    true same-filesystem atomic swap (incl. on Windows, where it replaces an
    existing destination); on any failure the temp file is removed and the
    destination is left untouched.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    try:
        Path(tmp_name).write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp_name, str(path))
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def content_index(path: Path, lang: str) -> dict[tuple[str, str], str]:
    """Map ``(slide_id, role) -> parser-stripped content`` for one deck.

    Filtered to ``lang`` so the apply-side lookup matches exactly what the
    Phase 1 classifier (``ordered_sync_cells``) extracted — the same
    role+language predicate, so the judge can never be fed an
    other-language cell that happens to share a key. Public so the interactive
    walker can render each proposal's current source/target bodies.
    """
    index: dict[tuple[str, str], str] = {}
    for cell in parse_cells(path.read_text(encoding="utf-8")):
        role = role_of(cell.metadata)
        if role is None:
            continue
        if cell.metadata.lang != lang:
            continue
        sid = cell.metadata.slide_id
        if not sid:
            continue
        index.setdefault((sid, role), cell.content)
    return index


def _row_anchor(slide_id: str | None, construct: str | None, content_hash: str) -> str:
    """The content anchor of a watermark row (mirrors :func:`sync_writeback.anchor_of`).

    Derives the same ``id: > construct: > hash:`` identity from a stored row that
    ``anchor_of`` derives from a live cell, so the structural pass can match a
    current cell against its baseline by anchor.
    """
    if slide_id is not None:
        return f"id:{slide_id}"
    if construct is not None:
        return f"construct:{construct}"
    return f"hash:{content_hash}"


def _baseline_anchor_hashes(
    cache: SyncWatermarkCache | None,
    de_path: Path,
    en_path: Path,
) -> dict[str, dict[str, str]]:
    """Per-language ``{anchor: content_hash}`` of the last-synced state.

    Built from the widened watermark (every non-j2 cell). The structural pass
    uses it to tell an UNCHANGED id-less localized code cell (anchor present with
    the same hash) from an edited one, so the former is reused verbatim instead of
    re-translated (Issue #190 item 3 / §8). Empty when there is no watermark — the
    structural pass then translates as before.
    """
    out: dict[str, dict[str, str]] = {"de": {}, "en": {}}
    if cache is None:
        return out
    for lang in ("de", "en"):
        for _pos, sid, _role, chash, construct in cache.get_deck(str(de_path), str(en_path), lang):
            out[lang][_row_anchor(sid, construct, chash)] = chash
    return out


def _record_watermark(
    cache: SyncWatermarkCache,
    de_path: Path,
    en_path: Path,
) -> None:
    """Record both decks' post-apply state as the new baseline.

    Membership-widened (Issue #190 §5.3): every non-j2 cell is recorded — the
    per-cell-synced cells under ``de``/``en`` and the language-neutral cells once
    under ``shared`` — so the Phase 2 anchor pass can locate id-less localized and
    shared cells. The classifier still reads only the real-role rows
    (``_baseline_from_watermark`` filters the synthetic membership roles), so this
    does not change its behavior.
    """
    de_rows = watermark_rows(parse_cells(de_path.read_text(encoding="utf-8")))
    en_rows = watermark_rows(parse_cells(en_path.read_text(encoding="utf-8")))
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="de", cells=de_rows["de"])
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="en", cells=en_rows["en"])
    # Neutral cells are byte-identical across halves (the unify invariant), so the
    # single-entity "shared" partition is recorded once from the DE file.
    cache.put_deck(
        de_path=str(de_path), en_path=str(en_path), lang="shared", cells=de_rows["shared"]
    )


def _eligible_for_partial_advance(plan: SyncPlan, result: ApplyResult) -> bool:
    """Whether a per-cell partial watermark advance is safe for this pass.

    Restricted to a **content-only** partial pass: every proposal is an
    ``edit`` or ``conflict`` (no add / remove / move / rename) AND the plan
    carries **no issues**. The no-issues guard matters: a both-decks reorder or
    an ambiguous de/en state is emitted as a *warning* (not a proposal and not
    an error), and its order/ambiguity is deliberately not reconciled — so a
    pass carrying any issue must hold the whole watermark, else that
    un-propagated signal would be silently baselined. With neither structural
    proposals nor issues, both decks' ordered ``(slide_id, role)`` structure is
    unchanged, so current positions equal baseline positions.

    Requires a real baseline, no errors, no issues, at least one deferral (a
    clean pass already did a full advance), and at least one *reconciled write*
    (``applied_edit > 0``) — there is nothing to bank in a pass that only
    deferred, so it just holds. Any other partial pass falls back to holding the
    whole watermark — safe, just noisier on the next run.
    """
    if not plan.has_baseline or plan.issues or result.errors:
        return False
    if result.deferred <= 0 or result.applied_edit <= 0:
        return False
    structural = (
        plan.count("add") + plan.count("remove") + plan.count("move") + plan.count("rename")
    )
    return structural == 0


def _record_watermark_partial(
    cache: SyncWatermarkCache,
    de_path: Path,
    en_path: Path,
    preserve_keys: set[tuple[str, str]],
) -> bool:
    """Advance the watermark per-cell, holding the true deferrals at baseline.

    Records the **current** state for every cell — the reconciliation default,
    matching the full advance — EXCEPT for any ``(slide_id, role)`` in
    ``preserve_keys`` (an unresolved conflict or a user-skipped edit), which
    keeps its **old** baseline hash so it re-surfaces next run instead of being
    silently baselined. An ``in_sync`` edit is *not* a deferral — the judge
    reconciled it — so it banks like an applied edit.

    Valid only on a content-only, issue-free pass (see
    :func:`_eligible_for_partial_advance`): structure is unchanged, so current
    positions equal baseline positions. Returns ``False`` without writing if any
    preserved key is absent from *either* deck's old baseline **or** current
    cells. The current-cell check matters: a "removed on one deck / edited on the
    other" collision is classified as a ``conflict`` (not a ``remove``), so it
    slips the structural gate, yet the cell is gone from the removing deck — we
    cannot faithfully preserve it there, so we hold the whole watermark and let
    the conflict re-surface instead of dropping it.
    """
    parsed = {
        "de": parse_cells(de_path.read_text(encoding="utf-8")),
        "en": parse_cells(en_path.read_text(encoding="utf-8")),
    }
    cells_by_lang = {
        "de": ordered_sync_cells(parsed["de"], "de"),
        "en": ordered_sync_cells(parsed["en"], "en"),
    }
    old: dict[str, dict[tuple[str, str], str]] = {}
    current: dict[str, set[tuple[str, str]]] = {}
    for lang in ("de", "en"):
        old[lang] = {
            (sid, role): chash
            for (_pos, sid, role, chash, _construct) in cache.get_deck(
                str(de_path), str(en_path), lang
            )
            if sid is not None and role not in MEMBERSHIP_ROLES
        }
        current[lang] = {
            (c.slide_id, c.role) for c in cells_by_lang[lang] if c.slide_id is not None
        }
    for key in preserve_keys:
        if key not in old["de"] or key not in old["en"]:
            return False
        if key not in current["de"] or key not in current["en"]:
            return False

    # Re-record the membership-widened watermark (Issue #190 §5.3), holding only
    # the legacy deferred keys at their pre-conflict baseline. A content-only pass
    # leaves structure (and every neutral / id-less cell) unchanged, so the
    # membership rows re-derive faithfully from the current files.
    widened = {"de": watermark_rows(parsed["de"]), "en": watermark_rows(parsed["en"])}
    for lang in ("de", "en"):
        rows: list[tuple[int, str | None, str, str, str | None]] = []
        for pos, sid, role, chash, construct in widened[lang][lang]:
            if role not in MEMBERSHIP_ROLES and sid is not None and (sid, role) in preserve_keys:
                # Hold the deferral at its pre-conflict baseline so it re-surfaces.
                chash = old[lang][(sid, role)]
            rows.append((pos, sid, role, chash, construct))
        cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang=lang, cells=rows)
    cache.put_deck(
        de_path=str(de_path), en_path=str(en_path), lang="shared", cells=widened["de"]["shared"]
    )
    return True
