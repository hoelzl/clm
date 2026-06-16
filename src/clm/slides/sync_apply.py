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
- **refuse** — a structural decision the resolver made at plan time *not* to act
  (the both-directions cold-start / id-less case, #216); executed as a no-op
  ``deferred`` so the watermark holds and the dry-run preview matches the run.
- **mint** / **adopt** — the two cold-start id-bootstrap candidates (#216 §12),
  each the whole plan and short-circuited before any ``FileState`` load: ``mint``
  confirms a both-id-less pair corresponds, then mints fresh shared ids via
  ``assign_ids_in_split_pair``; ``adopt`` confirms a *half-id'd* pair, then stamps
  the id'd half's *existing* ids onto its id-less twin. Either downgrades to a
  ``deferred`` no-op when correspondence is not confirmed (a "no", a safe-abort, or
  no verifier), so nothing wrong ever reaches disk.

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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clm.infrastructure.llm.ollama_client import OllamaError
from clm.notebooks.slide_parser import Cell, comment_token_for_path, parse_cell_header, parse_cells
from clm.slides.raw_cells import RawCell
from clm.slides.slug import resolve_collision, slugify
from clm.slides.sync_code import TranslationOutcome, apply_code_structure
from clm.slides.sync_plan import (
    MEMBERSHIP_ROLES,
    NEUTRAL_CODE_ROLE,
    Proposal,
    SyncPlan,
    TagHold,
    ordered_sync_cells,
    watermark_rows,
    watermark_tag_map,
)
from clm.slides.sync_recover import (
    NEW,
    NONE,
    AlignmentInvalid,
    CorrespondenceError,
    CorrespondenceInvalid,
    RecoveryError,
    RegionCell,
    SlidePair,
    correspondence_fingerprint,
    decode_mapping,
    decode_verdicts,
    encode_mapping,
    encode_verdicts,
    region_fingerprint,
    validate_alignment,
    validate_correspondence,
)
from clm.slides.sync_translate import TranslationError
from clm.slides.sync_writeback import (
    CODE_ROLE,
    FileState,
    anchor_of,
    build_twin_cell,
    cell_content_hash,
    construct_of,
    role_of,
    row_anchor,
)

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import (
        SyncAlignmentCache,
        SyncCorrespondenceCache,
        SyncWatermarkCache,
    )
    from clm.infrastructure.llm.ollama_client import SyncJudge
    from clm.slides.sync_recover import AlignmentRecoverer, CorrespondenceVerifier
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
    "ColdDeferralDetail",
    "RejectedPair",
    "apply_plan",
    "content_index",
]


@dataclass(frozen=True)
class RejectedPair:
    """A DE/EN slide pair the correspondence verifier judged non-corresponding.

    Carries exactly what the verifier saw — the pair index and both
    headings — so the operator can locate the divergence (#231).
    """

    index: int
    de_heading: str
    en_heading: str


@dataclass(frozen=True)
class ColdDeferralDetail:
    """Why a cold-start ``mint``/``adopt`` wrote nothing (#231).

    ``reason`` is one of:

    - ``"rejected-pairs"`` — the verifier returned "no" for
      ``rejected_pairs`` (a genuine DE/EN content divergence is the usual
      cause: crossed translations, a missing/merged cell shifting the
      alignment);
    - ``"safe-abort"`` — verification failed (transport/parse/validation),
      so no verdict exists;
    - ``"no-verifier"`` — no correspondence verifier was configured
      (``--no-verify-cold-pairs`` or no API key);
    - ``"plan-errors"`` — the plan carried classifier errors (the write
      boundary's defense in depth);
    - ``"race"`` — the files changed between candidacy and apply.
    """

    kind: str  # "mint" | "adopt"
    reason: str
    rejected_pairs: tuple[RejectedPair, ...] = ()


@dataclass
class ApplyResult:
    """Outcome of applying a :class:`SyncPlan`."""

    applied_edit: int = 0
    applied_retag: int = 0  # a tag-only edit mirrored across the split halves (#198)
    applied_remove: int = 0
    applied_move: int = 0
    applied_add: int = 0
    applied_rename: int = 0
    applied_migrate: int = 0  # a drifted slide_id moved back onto its construct (§9)
    applied_mint: int = 0  # a confirmed both-id-less cold pair minted shared ids (#216 §12)
    applied_adopt: int = 0  # a confirmed half-id'd cold pair adopted the id'd half's ids (#216 §12)
    applied_reconcile: int = (
        0  # a confirmed mismatched-id twin had its divergent id rewritten (#228)
    )
    applied_structural: int = 0  # slide-group regions the structural pass rebuilt (#269):
    # propagated language-neutral / id-less-localized cells the per-cell walk cannot reach
    in_sync: int = 0  # an edit the judge decided needed no change
    deferred: int = 0  # conflict (or moves/adds declined this pass)
    watermark_recorded: bool = False
    errors: list[str] = field(default_factory=list)
    # Per-deferral detail for cold-start mint/adopt (#231) — why the pair
    # was refused, incl. the rejected SlidePair headings on a verifier "no".
    cold_deferrals: list[ColdDeferralDetail] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return (
            self.applied_edit
            + self.applied_retag
            + self.applied_remove
            + self.applied_move
            + self.applied_add
            + self.applied_rename
            + self.applied_migrate
            + self.applied_mint
            + self.applied_adopt
            + self.applied_reconcile
            + self.applied_structural
        )

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


@dataclass
class _EditOutcome:
    """The materialized result of one edit (#216 resolve-then-apply, stage 2b).

    Computed by :func:`_materialize_edits` so the execute pass writes mechanically
    and calls no model for an edit:

    - ``"update"`` — apply ``proposed_text`` to the target cell;
    - ``"in_sync"`` — the judge decided no change is needed (counts as in-sync);
    - ``"blocked"`` — the model was unavailable / failed, or the proposal was
      malformed; ``error`` is the message to record (the edit is not applied).

    Mirrors exactly what the former inline ``_apply_edit`` / ``_apply_code_edit``
    decided, so moving the decision earlier is behavior-preserving.
    """

    verdict: str  # "update" | "in_sync" | "blocked"
    proposed_text: str | None = None
    error: str | None = None


def apply_plan(
    plan: SyncPlan,
    *,
    judge: SyncJudge | None,
    translator: SlideTranslator | None = None,
    watermark_cache: SyncWatermarkCache | None = None,
    decisions: dict[int, str] | None = None,
    recoverer: AlignmentRecoverer | None = None,
    alignment_cache: SyncAlignmentCache | None = None,
    verifier: CorrespondenceVerifier | None = None,
    correspondence_cache: SyncCorrespondenceCache | None = None,
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

    ``recoverer`` (with ``alignment_cache``) is the opt-in bounded-LLM tier
    (``--llm-recover``, Issue #190 §10 / Phase 5): when the deterministic
    id-migration (§9) is stuck on an *ambiguous* drifted id — a function renamed
    while a cell was split, an unresolvable tie — the recoverer re-identifies the
    neutral code region with a validated, cached, body-free alignment map. ``None``
    (the default) leaves the ambiguous region untouched to re-surface next run.
    """
    result = ApplyResult()

    # Stage 2b/3 for a cold-start mint (#216 §12): a `pending` mint candidate is the
    # whole plan (a cold pair carries no other ops), so handle it on its own path —
    # verify correspondence, then mint shared ids via the file-level minter — and
    # return. Done before loading FileState so the file-level mint and the watermark
    # advance read the freshly-minted files, with no buffered-write to coordinate.
    if any(p.kind == "mint" for p in plan.proposals):
        _apply_cold_mint(plan, verifier, correspondence_cache, watermark_cache, result)
        return result

    # Stage 2b/3 for a cold-start *adopt* (#216 §12): a half-id'd cold pair (one
    # half fully id'd, the other fully id-less) whose id-less half adopts the id'd
    # half's existing ids. Like the mint above it is the whole plan, verified then
    # stamped on its own path, before any FileState load.
    if any(p.kind == "adopt" for p in plan.proposals):
        _apply_cold_adopt(plan, verifier, correspondence_cache, watermark_cache, result)
        return result

    # Stage 2b/3 for a strategy-B *reconcile* (#228): a committed partial-overlap pair
    # whose mismatched-id twins are the whole actionable plan (emitted only then). Like
    # mint/adopt it is handled as a coordinated whole-plan unit — verify the suspect
    # cross-product, rewrite the divergent id of each confirmed twin (EN-authority),
    # direction-guarded cross-add or defer the rest — and returns before any keyed walk,
    # so it never interacts with the per-cell add / partial-watermark machinery (the
    # source of the two doubling/watermark hazards a per-cell integration would carry).
    if any(p.kind == "reconcile" for p in plan.proposals):
        _apply_reconcile(plan, verifier, correspondence_cache, watermark_cache, translator, result)
        return result

    # Pre-apply content (parser-stripped) for the judge, keyed by (slide_id,
    # role). Read before FileState mutates anything.
    de_content = content_index(plan.de_path, "de")
    en_content = content_index(plan.en_path, "en")

    de_state = FileState.load(plan.de_path)
    en_state = FileState.load(plan.en_path)

    # Stage 2b (edit path): resolve every edit — and every conflict the caller
    # chose to win — into a materialized outcome up front, the only place an edit
    # touches the judge/translator. The execute walk below then writes each edit
    # mechanically (#216 resolve-then-apply). Read against the pre-mutation
    # snapshots/state, exactly as the inline edit appliers used to.
    edit_outcomes = _materialize_edits(
        plan, decisions, de_state, en_state, de_content, en_content, judge, translator
    )

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
                _apply_edit(proposal, de_state, en_state, edit_outcomes[id(proposal)], result)
            else:
                result.deferred += 1
                _note_deferred(deferred_keys, proposal)
        elif kind == "retag":
            if _accepted(decisions, proposal):
                _apply_retag(proposal, de_state, en_state, result)
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
                proposal, decisions, de_state, en_state, edit_outcomes, result, deferred_keys
            )
        elif kind == "refuse":
            # A structural refusal the resolver decided at plan time (#216): the
            # engine never acts on it — it is *deferred* so the watermark holds
            # for these cells and the exit code is "needs review", exactly what
            # the dry-run preview promised. (This is the both-directions
            # cold-start / id-less case; the old apply-time guard that re-decided
            # it — and that the id-carrying path silently bypassed — is gone.)
            result.deferred += 1
            _note_deferred(deferred_keys, proposal)
        # "add" / "rename" are handled by _apply_adds below (always applied).

    # The run's shared translation-outcome cache (#216 2b / #289 P2), keyed by
    # source-cell id: the add materializers fill it for the add walks, the
    # structural materializer below adds the changed id-less localized cells, and
    # both execute passes read it — one model-call site per cell for the run.
    translations: dict[int, TranslationOutcome] = {}

    # Adds run before moves so a freshly-inserted slide takes part in any
    # reorder. Adds are sticky via the stamped id (a re-run no longer sees an
    # id-less cell), so unlike moves they apply even on a *deferred* (non-clean)
    # pass — but, like every cell, the stamped id only reaches disk on an
    # error-free pass (the end-of-pass flush is gated on no errors).
    _apply_adds(de_state, en_state, result, plan, translator, translations)

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
    # Deterministic id-migration (Issue #190 §9): if the author split an id'd code
    # cell, the slide_id is left on the wrong half. Move it back onto the cell whose
    # construct it names BEFORE the structural pass propagates, so the corrected ids
    # ride along to the twin.
    _migrate_drifted_ids(plan, de_state, en_state, result, recoverer, alignment_cache)

    # Stage 2b for the structural pass (#289 P2): pre-resolve every translation
    # the region rebuild will need into the shared cache, so the rebuild itself
    # is mechanical. Runs after the id-migration (a freshly-stamped id moves a
    # cell out of the id-less set, so enumerating here matches the rebuild
    # exactly) and before any structural mutation.
    baseline_anchors = _baseline_anchor_hashes(plan)
    _materialize_structural(plan, de_state, en_state, translator, baseline_anchors, translations)
    apply_code_structure(
        plan, de_state, en_state, translator, result, baseline_anchors, translations
    )

    # Place a newly-added slide group at its source position. The per-cell add path
    # anchors a new group beside the nearest neighbour it can name by (slide_id,
    # role); a language-neutral or id-less neighbour is invisible to that anchor, so
    # a new group can land in the wrong inter-group slot. The structural pass above
    # rebuilds each group's CONTENTS but never reorders GROUPS, so reconcile group
    # order against the source here — otherwise the misplacement survives only to
    # trip the shared-cell / id-less parity fail-safe below (the reported bug).
    _reconcile_group_order(plan, de_state, en_state, result)

    # Fail-safe: a complete resolution leaves no duplicate id behind. If one
    # survives (a should-not-happen bug), error so the watermark cannot advance
    # over a corrupt state and the situation is surfaced rather than baselined.
    _flag_residual_duplicates(de_state, "de", result)
    _flag_residual_duplicates(en_state, "en", result)
    # On an otherwise-clean pass both decks must carry the same (slide_id, role)
    # set; a one-sided orphan is a silent cross-deck divergence. The shared-cell
    # parity check (Issue #269) is the cardinal-invariant fail-safe: language-neutral
    # cells must be byte-identical across the halves after a clean apply, so a residual
    # divergence means a shared-cell change was dropped — error rather than report
    # "consistent" and advance the watermark over it.
    if not result.errors and result.deferred == 0 and not plan.has_errors:
        _flag_cross_deck_orphans(de_state, en_state, result)
        _flag_shared_cell_divergence(de_state, en_state, result)
        _flag_idless_localized_divergence(plan, de_state, en_state, result)

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
    # ``not plan.blocking_issues`` gates BOTH paths: a both-decks reorder, an
    # ambiguous de/en state, or a shared-cell auto-heal is emitted as a *warning*
    # (no proposal, no error) whose order/ambiguity is deliberately not reconciled.
    # Advancing over it — on either path — would bake the new positions and
    # silently lose the "resolve manually" signal, so any such issue holds the
    # whole watermark. A *tag-only* both-decks conflict (Issue #202), by contrast,
    # touches no body and is scoped to one cell's tags: it does NOT block, but it
    # forces the partial path (the full path would record the divergent tags and
    # silently baseline them), where its own cell's tags are pinned at the old
    # baseline so the conflict re-surfaces while everything else banks.
    if watermark_cache is not None and not plan.blocking_issues:
        if _pass_is_clean(plan, result) and not plan.tag_holds:
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
                watermark_cache, plan.de_path, plan.en_path, deferred_keys, plan.tag_holds
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
    edit_outcomes: dict[int, _EditOutcome],
    result: ApplyResult,
    deferred_keys: set[tuple[str, str]],
) -> None:
    """Resolve a conflict per its decision, or defer it.

    ``de-wins`` / ``en-wins`` propagate the winning side as an ordinary edit
    (the judge's rewrite of the losing side was materialized up front by
    :func:`_materialize_edits`, keyed by this conflict proposal's id); any other
    decision defers, recording the key so the per-cell advance keeps its
    pre-conflict baseline and the conflict re-surfaces next run.
    """
    decision = _conflict_decision(decisions, proposal)
    if decision == DECISION_DE_WINS:
        _apply_edit(
            _conflict_as_edit(proposal, "de->en"),
            de_state,
            en_state,
            edit_outcomes[id(proposal)],
            result,
        )
    elif decision == DECISION_EN_WINS:
        _apply_edit(
            _conflict_as_edit(proposal, "en->de"),
            de_state,
            en_state,
            edit_outcomes[id(proposal)],
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


def _shared_cell_hashes(state: FileState) -> list[str]:
    """Ordered content hashes of a deck's language-neutral (``shared``) cells.

    Partitioned exactly like :func:`clm.slides.sync_plan.watermark_rows` — a non-j2
    cell whose ``lang`` is neither ``de`` nor ``en`` — so it spans neutral code,
    neutral markdown, AND a markdown cell that carries a narrative tag but no
    ``lang`` (the tagged-neutral blind spot). Hashed with the same
    :func:`cell_content_hash` the rest of the engine uses, so trailing-blank
    separator differences never register.
    """
    return [
        cell_content_hash(cell.body)
        for cell in state.cells
        if not cell.metadata.is_j2 and cell.metadata.lang not in ("de", "en")
    ]


def _cell_snippet(body: str, limit: int = 48) -> str:
    """A short, human-locatable excerpt of a cell body (its first meaningful line).

    Strips a leading line-comment prefix (``# `` / ``// ``) so a markdown cell reads
    as its heading text, and truncates to ``limit`` so an error line stays scannable.
    """
    for raw in body.split("\n"):
        line = raw.strip()
        for pre in ("# ", "// "):
            if line.startswith(pre):
                line = line[len(pre) :].strip()
                break
        else:
            if line in ("#", "//"):
                continue
        if line:
            return line[:limit] + ("…" if len(line) > limit else "")
    return "(empty cell)"


def _snippet_list(snippets: list[str], limit: int = 3) -> str:
    """Render up to ``limit`` cell snippets, with a ``(+N more)`` tail if truncated."""
    shown = ", ".join(repr(s) for s in snippets[:limit])
    extra = len(snippets) - limit
    return f"{shown}, … (+{extra} more)" if extra > 0 else shown


def _shared_cell_descriptors(state: FileState) -> list[tuple[str, str]]:
    """``(content_hash, snippet)`` of each language-neutral cell, in document order.

    Partitioned exactly like :func:`_shared_cell_hashes` (the parity invariant), but
    carries a snippet too so a divergence can name the offending cell(s).
    """
    return [
        (cell_content_hash(cell.body), _cell_snippet(cell.body))
        for cell in state.cells
        if not cell.metadata.is_j2 and cell.metadata.lang not in ("de", "en")
    ]


def _describe_shared_divergence(de: list[tuple[str, str]], en: list[tuple[str, str]]) -> str:
    """Name the language-neutral cells that diverge between the two halves.

    Reports cells present on one half but not the other (an un-propagated add /
    remove), else — when both halves hold the same cells in a different order — the
    first positional mismatch. Caps the listed snippets so the message stays short.
    """
    de_by_hash = dict(de)
    en_by_hash = dict(en)
    cde, cen = Counter(h for h, _ in de), Counter(h for h, _ in en)
    # ``dict.fromkeys`` de-dups the multiset diff to DISTINCT hashes (preserving
    # first-seen order), so two byte-identical added cells list one snippet, not the
    # same text twice.
    de_only = list(dict.fromkeys((cde - cen).elements()))
    en_only = list(dict.fromkeys((cen - cde).elements()))
    parts: list[str] = []
    if de_only:
        parts.append("on de but missing on en: " + _snippet_list([de_by_hash[h] for h in de_only]))
    if en_only:
        parts.append("on en but missing on de: " + _snippet_list([en_by_hash[h] for h in en_only]))
    if parts:
        return "; ".join(parts)
    for i, (a, b) in enumerate(zip(de, en, strict=False)):
        if a[0] != b[0]:
            return (
                f"the shared cells are in a different order across the halves (first "
                f"mismatch at shared-cell #{i + 1}: de has {a[1]!r}, en has {b[1]!r})"
            )
    return "the shared cells are in a different order across the halves"


def _flag_shared_cell_divergence(
    de_state: FileState, en_state: FileState, result: ApplyResult
) -> None:
    """Error if the two halves' language-neutral cells are not byte-identical (#269).

    The cardinal-invariant fail-safe. Language-neutral cells are shared verbatim
    across a split pair (the ``unify`` invariant), so after a clean apply their
    ordered content hashes MUST match. If they still diverge, a one-sided edit /
    add / remove of a neutral cell was *not* propagated — the engine must surface
    that as an error (holding the watermark and writing nothing) rather than let
    the pass report "decks already consistent". This backstops the neutral-cell
    propagation paths (``align_anchored`` + the structural pass) for every neutral
    cell shape, including a tagged-neutral cell the structural pass cannot rebuild.
    The message names the offending cell(s) so the author can find the divergence.

    Only invoked on an otherwise-clean pass (no prior error / deferral), so a
    genuine mismatch here is always an un-propagated divergence, never a
    double-report of an already-alerted problem.
    """
    de = _shared_cell_descriptors(de_state)
    en = _shared_cell_descriptors(en_state)
    if [h for h, _ in de] != [h for h, _ in en]:
        result.errors.append(
            "language-neutral (shared) cells differ between de and en after sync — "
            f"{_describe_shared_divergence(de, en)}; a change to a shared cell was "
            "not propagated; re-run sync (a watermark now exists) or resolve the "
            "divergence manually"
        )
        return
    # Issue #289: the bodies agree, but a neutral cell is shared verbatim
    # INCLUDING its header — so the tag sets must match too. The body hash is
    # blind to a tag-only change, which let a one-sided tag edit slip every body
    # detector; this is the post-apply net behind the plan-time
    # ``_classify_neutral_tag_drift`` detector. Positional zip is sound here:
    # the pass was otherwise clean, so the orders are reconciled (and the hash
    # sequences just compared equal).
    de_tags = _shared_cell_tag_sets(de_state)
    en_tags = _shared_cell_tag_sets(en_state)
    for i, (dt, et) in enumerate(zip(de_tags, en_tags, strict=True)):
        if dt != et:
            result.errors.append(
                "language-neutral (shared) cell tags differ between de and en after "
                f"sync (cell {de[i][1]!r}: de={sorted(dt)}, en={sorted(et)}); a "
                "tag-only change was not propagated — apply it to both halves or "
                "resolve manually"
            )
            return


def _shared_cell_tag_sets(state: FileState) -> list[frozenset[str]]:
    """Ordered tag sets of a deck's language-neutral cells (Issue #289).

    Partitioned exactly like :func:`_shared_cell_hashes` /
    :func:`_shared_cell_descriptors` (the parity invariant), so index *i* names the
    same cell in all three views.
    """
    return [
        frozenset(cell.metadata.tags)
        for cell in state.cells
        if not cell.metadata.is_j2 and cell.metadata.lang not in ("de", "en")
    ]


def _idless_localized_group_kinds(state: FileState) -> list[tuple[int, str]]:
    """Ordered ``(slide-group index, kind)`` of each id-less localized cell — structure only.

    A ``lang=`` cell with no per-cell role (``role_of`` ``None`` — the ``("L", kind)``
    set). Content- and language-free, so it is comparable **across** the two halves
    (which hold translations, not identical text). Under the unify/split invariant the
    halves place their id-less localized cells in the same slide groups in the same
    order, so a mismatch is a move the structural pass did not mirror (Issue #269).
    """
    out: list[tuple[int, str]] = []
    group = -1
    for cell in state.cells:
        meta = cell.metadata
        if meta.is_slide_start:
            group += 1
        if meta.is_j2:
            continue
        if meta.lang in ("de", "en") and role_of(meta) is None:
            out.append((group, "code" if meta.cell_type == "code" else "markdown"))
    return out


def _idless_localized_body_hashes(state: FileState) -> list[str]:
    """Ordered content hashes of a deck's id-less localized cells (document order)."""
    return [
        cell_content_hash(cell.body)
        for cell in state.cells
        if not cell.metadata.is_j2
        and cell.metadata.lang in ("de", "en")
        and role_of(cell.metadata) is None
    ]


def _describe_idless_divergence(
    de_kinds: list[tuple[int, str]], en_kinds: list[tuple[int, str]]
) -> str:
    """Name the first slide group where the id-less localized cell layout diverges.

    Each entry is ``(slide-group index, kind)``; the divergence is content-free
    (these cells are language-specific translations), so the most locatable handle
    is the 1-based slide-group number and the cell kind (code / markdown).
    """
    for i in range(max(len(de_kinds), len(en_kinds))):
        a = de_kinds[i] if i < len(de_kinds) else None
        b = en_kinds[i] if i < len(en_kinds) else None
        if a != b:
            de_desc = f"a {a[1]} cell in slide group {a[0] + 1}" if a is not None else "(none)"
            en_desc = f"a {b[1]} cell in slide group {b[0] + 1}" if b is not None else "(none)"
            return f"de has {de_desc} where en has {en_desc}"
    return "the id-less localized cell layout differs"


def _flag_idless_localized_divergence(
    plan: SyncPlan, de_state: FileState, en_state: FileState, result: ApplyResult
) -> None:
    """Error on an un-mirrored move/reorder of id-less localized cells (Issue #269).

    The neutral parity fail-safe (:func:`_flag_shared_cell_divergence`) cannot see
    these cells — they carry a ``lang``, so they are language-specific (translations),
    not byte-identical across halves. A one-sided **body edit** is handled by the
    drift detector + structural pass, but a one-sided **move** (across slide groups)
    or **reorder** (within a group) of an un-id'd cell can slip through: the flat,
    order-blind machinery may not detect or cannot rebuild it, which would otherwise
    leave the halves divergent while the run reports "consistent". Two content-free
    (so false-positive-free) checks restore propagate-or-**alert**:

    1. **Cross-half structure** — the ordered ``(group, kind)`` of id-less localized
       cells must match across de and en. A cross-group move breaks it.
    2. **One-sided pure reorder** — a half whose id-less localized cells are a pure
       reorder of its baseline (same multiset, different order) while the other half is
       unchanged. A pure reorder involves no translation, so comparing against the
       baseline cannot false-positive on a re-translated body edit.

    Documented residual: a one-sided reorder of two **same-kind** id-less cells within
    one group is alerted (check 2), not auto-propagated — give such cells ``slide_id``s
    for precise sync.
    """
    de_kinds = _idless_localized_group_kinds(de_state)
    en_kinds = _idless_localized_group_kinds(en_state)
    if de_kinds != en_kinds:
        result.errors.append(
            "id-less localized cells (lang= cells with no slide_id) are placed "
            f"differently across de and en after sync — {_describe_idless_divergence(de_kinds, en_kinds)}; "
            "a move was not propagated; give them slide_ids for precise sync, or "
            "resolve manually"
        )
        return
    de_base, en_base = plan.idless_baseline_de, plan.idless_baseline_en
    if de_base is None or en_base is None:
        return
    de_now = _idless_localized_body_hashes(de_state)
    en_now = _idless_localized_body_hashes(en_state)
    de_reorder = de_now != de_base and Counter(de_now) == Counter(de_base)
    en_reorder = en_now != en_base and Counter(en_now) == Counter(en_base)
    if (de_reorder and en_now == en_base) or (en_reorder and de_now == de_base):
        which = "de" if de_reorder else "en"
        result.errors.append(
            f"id-less localized cells were reordered on the {which} deck but not the "
            "other after sync — give them slide_ids so the order can be mirrored, or "
            "resolve manually"
        )


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


def _apply_retag(
    proposal: Proposal,
    de_state: FileState,
    en_state: FileState,
    result: ApplyResult,
) -> None:
    """Mirror a tag-only edit (#198) by copying the source cell's tags to the target.

    Tags are language-independent, so this is a pure header rewrite — no judge or
    translator. An id-carrying cell is matched on both decks by ``(slide_id,
    role)``, so the role tag is carried verbatim and the target's role never
    changes. An **id-less localized** cell (Tier C) has no key, so it is targeted
    by the carried position + tag set instead (:func:`_apply_retag_idless`).
    """
    if proposal.slide_id is None:
        _apply_retag_idless(proposal, de_state, en_state, result)
        return
    sid = proposal.slide_id
    if proposal.direction == "de->en":
        source_state, target_state = de_state, en_state
    else:
        source_state, target_state = en_state, de_state
    source_cell = source_state.find_cell(sid, proposal.role)
    if source_cell is None:
        result.errors.append(f"retag {sid}/{proposal.role}: source cell not found")
        return
    if target_state.replace_cell_tags(sid, proposal.role, list(source_cell.metadata.tags)):
        result.applied_retag += 1
    else:
        result.errors.append(f"retag {sid}/{proposal.role}: target cell not found")


def _apply_retag_idless(
    proposal: Proposal,
    de_state: FileState,
    en_state: FileState,
    result: ApplyResult,
) -> None:
    """Mirror a tag-only edit onto an id-less localized twin (Tier C, #198).

    The cell has no ``slide_id``, so the classifier identified it by position
    among its language's non-j2 cells and carried both the target position and the
    desired tag set on the proposal (a header rewrite, no LLM). The target is the
    side that did *not* change; its position equals the source's because the
    classifier only emits this when the localized streams are structurally aligned
    (no add/remove/move). :meth:`FileState.replace_idless_localized_tags` refuses
    if the cell at that position is not id-less localized, so a stream that drifted
    after planning errors rather than retagging the wrong cell.
    """
    if proposal.tags is None or proposal.target_position is None:
        result.errors.append(f"retag (id-less {proposal.role}): proposal missing tags/position")
        return
    if proposal.direction == "de->en":
        target_state, target_lang = en_state, "en"
    else:
        target_state, target_lang = de_state, "de"
    if target_state.replace_idless_localized_tags(
        target_lang, proposal.target_position, list(proposal.tags)
    ):
        result.applied_retag += 1
    else:
        result.errors.append(
            f"retag (id-less {proposal.role}) at {target_lang} #{proposal.target_position}: "
            "target cell not found or not id-less localized"
        )


def _materialize_edits(
    plan: SyncPlan,
    decisions: dict[int, str] | None,
    de_state: FileState,
    en_state: FileState,
    de_content: dict[tuple[str, str], str],
    en_content: dict[tuple[str, str], str],
    judge: SyncJudge | None,
    translator: SlideTranslator | None,
) -> dict[int, _EditOutcome]:
    """Resolve every edit (and every conflict the caller chose to win) up front.

    Stage 2b for the edit path (#216 resolve-then-apply): the only model calls for
    edits happen here, keyed by ``id(proposal)``, so the execute walk writes
    mechanically. A conflict resolved ``de-wins`` / ``en-wins`` is materialized as
    the directed edit it becomes (keyed by the *conflict* proposal's id, since the
    execute pass synthesizes a fresh edit proposal each run); a skipped or
    undecided conflict is left out so the execute walk simply defers it.

    Runs before any cell mutation, exactly where the inline appliers used to read
    their inputs (markdown from the ``content_index`` snapshots, localized code
    from the loaded state), so the move is behavior-preserving.
    """
    outcomes: dict[int, _EditOutcome] = {}
    for proposal in plan.proposals:
        if proposal.kind == "edit":
            outcomes[id(proposal)] = _resolve_edit(
                proposal, de_state, en_state, de_content, en_content, judge, translator
            )
        elif proposal.kind == "conflict":
            decision = _conflict_decision(decisions, proposal)
            if decision in (DECISION_DE_WINS, DECISION_EN_WINS):
                direction = "de->en" if decision == DECISION_DE_WINS else "en->de"
                outcomes[id(proposal)] = _resolve_edit(
                    _conflict_as_edit(proposal, direction),
                    de_state,
                    en_state,
                    de_content,
                    en_content,
                    judge,
                    translator,
                )
    return outcomes


def _resolve_edit(
    proposal: Proposal,
    de_state: FileState,
    en_state: FileState,
    de_content: dict[tuple[str, str], str],
    en_content: dict[tuple[str, str], str],
    judge: SyncJudge | None,
    translator: SlideTranslator | None,
) -> _EditOutcome:
    """Resolve one edit to a materialized :class:`_EditOutcome` (the model call).

    Mirrors the former inline ``_apply_edit`` / ``_apply_code_edit`` decision logic
    exactly — same verdicts, same error strings — but returns the outcome instead
    of writing, so the execute pass is mechanical. A localized **code** cell is
    reconciled by re-translating the source body (the markdown judge's prompt does
    not fit runnable code); a markdown cell goes through the judge.
    """
    if proposal.slide_id is None:
        return _EditOutcome("blocked", error=f"edit {proposal.role}: proposal has no slide_id")
    sid = proposal.slide_id
    key = (sid, proposal.role)
    if proposal.direction == "de->en":
        source_lang, target_lang = "de", "en"
        source_body = de_content.get(key, "")
        target_body = en_content.get(key, "")
        source_state = de_state
    else:
        source_lang, target_lang = "en", "de"
        source_body = en_content.get(key, "")
        target_body = de_content.get(key, "")
        source_state = en_state

    if proposal.role == CODE_ROLE:
        if translator is None:
            return _EditOutcome(
                "blocked", error=f"edit {sid}/code: no translator (LLM unavailable)"
            )
        src_cell = source_state.find_cell(sid, CODE_ROLE)
        if src_cell is None:
            return _EditOutcome("blocked", error=f"edit {sid}/code: source cell not found")
        try:
            new_body = translator.translate(
                source_body=src_cell.body.rstrip("\n"),
                source_lang=source_lang,
                target_lang=target_lang,
                role=CODE_ROLE,
            )
        except TranslationError as exc:
            return _EditOutcome("blocked", error=f"edit {sid}/code: translation failed: {exc}")
        return _EditOutcome("update", proposed_text=new_body)

    if judge is None:
        return _EditOutcome(
            "blocked", error=f"edit {sid}/{proposal.role}: no judge (LLM unavailable)"
        )
    try:
        sync_proposal = judge.propose(
            source_body, target_body, source_lang=source_lang, target_lang=target_lang
        )
    except OllamaError as exc:
        logger.info("edit judge failed on %s/%s: %s", sid, proposal.role, exc)
        return _EditOutcome("blocked", error=f"edit {sid}/{proposal.role}: {exc}")
    if sync_proposal.verdict != "update":
        return _EditOutcome("in_sync")
    return _EditOutcome("update", proposed_text=sync_proposal.proposed_text)


def _apply_edit(
    proposal: Proposal,
    de_state: FileState,
    en_state: FileState,
    outcome: _EditOutcome,
    result: ApplyResult,
) -> None:
    """Write a pre-materialized edit (mechanical — no judge/translator here).

    The model call (markdown judge / code re-translation) already happened in
    :func:`_materialize_edits`; this only records the outcome: ``blocked`` → its
    error, ``in_sync`` → the judge's no-change verdict, ``update`` → replace the
    target cell's body (the target is the non-source deck; ``proposal.role`` is the
    cell's role, ``"code"`` for a localized code cell).
    """
    if outcome.verdict == "blocked":
        result.errors.append(outcome.error or f"edit {proposal.role}: blocked")
        return
    if outcome.verdict == "in_sync":
        result.in_sync += 1
        return
    sid = proposal.slide_id
    if sid is None:  # unreachable: an id-less edit resolves to "blocked" above
        result.errors.append(f"edit {proposal.role}: proposal has no slide_id")
        return
    target_state = en_state if proposal.direction == "de->en" else de_state
    if target_state.replace_cell_body(sid, proposal.role, outcome.proposed_text or ""):
        result.applied_edit += 1
    else:
        result.errors.append(f"edit {sid}/{proposal.role}: target cell not found")


# ---------------------------------------------------------------------------
# Cold-start mint (#216 Phase 3 §12): verify correspondence, then mint shared ids
# ---------------------------------------------------------------------------


def _apply_cold_mint(
    plan: SyncPlan,
    verifier: CorrespondenceVerifier | None,
    correspondence_cache: SyncCorrespondenceCache | None,
    watermark_cache: SyncWatermarkCache | None,
    result: ApplyResult,
) -> None:
    """Confirm a cold-start pair corresponds, then mint shared ids onto both halves.

    The plan carries a single ``pending`` ``mint`` candidate (a both-id-less,
    unifiable cold pair the resolver admitted). Build the aligned slide pairs from
    the files, verify them (cached, validated, safe-abort); on **all-yes** mint the
    shared EN-authority ids via :func:`assign_ids_in_split_pair` and advance the
    watermark; on **any-no / safe-abort / no-verifier** downgrade to a deferral —
    the dry-run already disclosed this item as ``pending`` — writing nothing.

    Defers (writes nothing) on a **classifier error** — the same gate the normal
    flush path applies (``not plan.has_errors``). The candidacy guard already
    declines to emit a candidate for an errored plan, so this is defense in depth
    at the write boundary (a fully-id-less mint pair cannot carry such an error, but
    the guard keeps the short-circuit honest if one ever reaches here).
    """
    if plan.has_errors:
        _defer_cold(result, "mint", "plan-errors")
        return
    pairs = _build_slide_pairs(plan.de_path, plan.en_path)
    if verifier is None:
        _defer_cold(result, "mint", "no-verifier")  # e.g. --no-verify: pending → refuse
        return
    verdicts = _resolve_correspondence(verifier, correspondence_cache, pairs)
    if verdicts is None:
        _defer_cold(result, "mint", "safe-abort")  # never a wrong id
        return
    if not all(verdicts.get(i, False) for i in range(len(pairs))):
        # A "no" on specific pairs → refuse, and record which pairs failed
        # so the operator can find the DE/EN divergence (#231).
        _defer_cold(result, "mint", "rejected-pairs", _rejected_pairs(pairs, verdicts))
        return

    from clm.slides.assign_ids import AssignOptions, assign_ids_in_split_pair

    minted = assign_ids_in_split_pair(
        plan.de_path, plan.en_path, AssignOptions(accept_content_derived=True)
    )
    if minted is None:  # not unifiable after all (a race vs candidacy) → refuse
        _defer_cold(result, "mint", "race")
        return
    result.applied_mint += 1
    if watermark_cache is not None:
        _record_watermark(watermark_cache, plan.de_path, plan.en_path)
        result.watermark_recorded = True


def _defer_cold(
    result: ApplyResult,
    kind: str,
    reason: str,
    rejected: tuple[RejectedPair, ...] = (),
) -> None:
    """Count a cold-start deferral and record its actionable detail (#231)."""
    result.deferred += 1
    result.cold_deferrals.append(
        ColdDeferralDetail(kind=kind, reason=reason, rejected_pairs=rejected)
    )


def _rejected_pairs(pairs: list[SlidePair], verdicts: dict[int, bool]) -> tuple[RejectedPair, ...]:
    """The pairs the verifier judged non-corresponding, with their headings."""
    return tuple(
        RejectedPair(index=i, de_heading=p.de_heading, en_heading=p.en_heading)
        for i, p in enumerate(pairs)
        if not verdicts.get(i, False)
    )


def _slide_cells(path: Path, lang: str) -> list[Cell]:
    """The slide/subslide cells of ``lang`` — the units a cold mint stamps an id onto."""
    return [
        c
        for c in parse_cells(path.read_text(encoding="utf-8"), comment_token_for_path(path))
        if c.metadata.lang == lang and role_of(c.metadata) in _SLIDE_ROLES
    ]


def _build_slide_pairs(de_path: Path, en_path: Path) -> list[SlidePair]:
    """Positionally pair the slide cells of the two cold-start halves for the verifier.

    Both halves are fully id-less and structurally aligned (the candidacy gate), so
    the i-th sync slide of DE pairs with the i-th of EN. Slides are the unit of
    correspondence (companions follow their slide); each pair carries the heading +
    a short body snippet of each side.
    """
    de_slides = _slide_cells(de_path, "de")
    en_slides = _slide_cells(en_path, "en")
    pairs: list[SlidePair] = []
    # Equal length by the candidacy gate (_streams_aligned); strict=False is a
    # defensive no-crash fallback — the assign_ids unify guard backs correctness.
    for de_c, en_c in zip(de_slides, en_slides, strict=False):
        pairs.append(
            SlidePair(
                de_heading=_heading_line(de_c.content),
                en_heading=_heading_line(en_c.content),
                de_snippet=_snippet(de_c.content),
                en_snippet=_snippet(en_c.content),
                role=role_of(de_c.metadata) or "slide",
            )
        )
    return pairs


def _heading_line(body: str) -> str:
    """The first non-blank line of a cell body (the slide heading)."""
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _snippet(body: str, max_lines: int = 2) -> str:
    """The lines just after the heading (a short lead-in for the verifier)."""
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    return "\n".join(lines[1 : 1 + max_lines])


def _resolve_correspondence(
    verifier: CorrespondenceVerifier,
    cache: SyncCorrespondenceCache | None,
    pairs: list[SlidePair],
) -> dict[int, bool] | None:
    """Verify ``pairs`` (cached, validated), or ``None`` on a safe-abort.

    Mirrors :func:`_resolve_alignment`: caches only a *validated* verdict map, keyed
    by the pair fingerprint + the verifier's prompt version. Any failure — transport,
    parse, or a map that fails validation — returns ``None`` so the caller refuses
    (a wrong shared id is worse than a deferred pair), and nothing is cached so a
    fixed prompt re-derives it next run.
    """
    fp = correspondence_fingerprint(pairs)
    pv = verifier.prompt_version
    if cache is not None:
        hit = cache.get(fp, pv)
        if hit is not None:
            try:
                return validate_correspondence(decode_verdicts(hit), pairs)
            except CorrespondenceInvalid:
                pass  # corrupt cache row — re-derive
    try:
        verdicts = validate_correspondence(verifier.verify(pairs=pairs), pairs)
    except (CorrespondenceError, CorrespondenceInvalid):
        return None
    if cache is not None:
        cache.put(fp, pv, encode_verdicts(verdicts))
    return verdicts


# ---------------------------------------------------------------------------
# Cold-start adopt (#216 Phase 3.2 §12): verify, then stamp the id'd half's ids
# ---------------------------------------------------------------------------


def _apply_cold_adopt(
    plan: SyncPlan,
    verifier: CorrespondenceVerifier | None,
    correspondence_cache: SyncCorrespondenceCache | None,
    watermark_cache: SyncWatermarkCache | None,
    result: ApplyResult,
) -> None:
    """Confirm a half-id'd cold pair corresponds, then stamp the id'd half's ids onto its twin.

    The plan carries a single ``pending`` ``adopt`` candidate whose ``direction`` is
    ``"{authority}->{other}"`` (the authority is the fully-id'd half). Build the
    aligned slide pairs from the files and verify them (cached, validated,
    safe-abort); on **all-yes** stamp each authority slide_id onto its id-less
    positional twin — a header rewrite, **no translation** (both bodies already
    exist) — and advance the watermark; on **any-no / safe-abort / no-verifier**
    downgrade to a deferral, writing nothing (the dry-run disclosed it as
    ``pending``). Unlike the mint, ``assign_ids`` cannot do this: its
    ``_slide_ids_pair`` is ``de_id == en_id``, so an id-less cell never pairs with
    an id'd one — hence the explicit per-cell stamp.

    Defers (writes nothing) on a **classifier error** — the same gate the normal
    flush path applies (``not plan.has_errors``). This is the critical safety net:
    a half-id'd pair whose *authority* half carries a duplicated id (e.g. a slide
    with two same-id voiceover companions) would otherwise stamp that duplicate onto
    the id-less twin and advance the watermark over the corruption. The candidacy
    guard already declines such a plan; this defends the write boundary too.
    """
    if plan.has_errors:
        _defer_cold(result, "adopt", "plan-errors")
        return
    adopt = next(p for p in plan.proposals if p.kind == "adopt")
    authority = (adopt.direction or "en->de").split("->")[0]
    pairs = _build_slide_pairs(plan.de_path, plan.en_path)
    if verifier is None:
        _defer_cold(result, "adopt", "no-verifier")  # e.g. --no-verify: pending → refuse
        return
    verdicts = _resolve_correspondence(verifier, correspondence_cache, pairs)
    if verdicts is None:
        _defer_cold(result, "adopt", "safe-abort")  # never a wrong id
        return
    if not all(verdicts.get(i, False) for i in range(len(pairs))):
        _defer_cold(result, "adopt", "rejected-pairs", _rejected_pairs(pairs, verdicts))
        return
    stamped = _adopt_ids_in_split_pair(plan.de_path, plan.en_path, authority)
    if stamped == 0:  # the streams drifted since candidacy (a race) → refuse
        _defer_cold(result, "adopt", "race")
        return
    result.applied_adopt += 1
    if watermark_cache is not None:
        _record_watermark(watermark_cache, plan.de_path, plan.en_path)
        result.watermark_recorded = True


def _adopt_ids_in_split_pair(de_path: Path, en_path: Path, authority: str) -> int:
    """Stamp the ``authority`` half's slide_ids onto its id-less twin, in place.

    Walks both halves' localized cell streams positionally (the candidacy gate
    proved them aligned): wherever the authority cell carries a slide_id and its
    positional twin is id-less, write that id onto the twin's header
    (:func:`_stamp_slide_id` — the same byte-faithful rewrite assign-ids uses).
    Both halves are loaded with the *same* parser (:meth:`FileState.load`); only the
    id-less half is flushed. Returns the number of cells stamped — ``0`` when the
    streams no longer align (a race vs the plan) or a positional role drifted, so the
    caller refuses rather than mis-stamping a wrong id.
    """
    if authority == "en":
        idd_state, idless_state = FileState.load(en_path), FileState.load(de_path)
        idd_lang, idless_lang = "en", "de"
    else:
        idd_state, idless_state = FileState.load(de_path), FileState.load(en_path)
        idd_lang, idless_lang = "de", "en"
    idd_loc = [c for c in idd_state.cells if not c.metadata.is_j2 and c.metadata.lang == idd_lang]
    idless_loc = [
        c for c in idless_state.cells if not c.metadata.is_j2 and c.metadata.lang == idless_lang
    ]
    if len(idd_loc) != len(idless_loc):
        return 0  # streams drifted since the plan — refuse rather than mis-stamp
    stamped = 0
    for idd_cell, idless_cell in zip(idd_loc, idless_loc, strict=False):
        if role_of(idd_cell.metadata) != role_of(idless_cell.metadata):
            return 0  # a positional role drifted — refuse
        target_id = idd_cell.metadata.slide_id
        if target_id and not idless_cell.metadata.slide_id:
            _stamp_slide_id(idless_cell, target_id)
            stamped += 1
    if stamped:
        idless_state.dirty = True
        idless_state.flush()
    return stamped


# ---------------------------------------------------------------------------
# Strategy-B reconcile (#228): verify mismatched-id twins, rewrite the divergent id
# ---------------------------------------------------------------------------


def _apply_reconcile(
    plan: SyncPlan,
    verifier: CorrespondenceVerifier | None,
    correspondence_cache: SyncCorrespondenceCache | None,
    watermark_cache: SyncWatermarkCache | None,
    translator: SlideTranslator | None,
    result: ApplyResult,
) -> None:
    """Resolve a committed mismatched-id-twin bucket by content correspondence (#228 strategy B).

    The plan's ``reconcile`` candidates are the suspect cells of a partial-overlap pair
    (it shares an id, so it kept its git-HEAD baseline) whose halves may carry the same
    content under divergent ids. Handled as a coordinated whole-plan unit — like
    mint/adopt — so a confirmed twin is never independently cross-added in both
    directions (the doubling a per-cell route would risk):

    1. Locate each suspect's cell; split into DE-source / EN-source by direction.
    2. Verify the **DE×EN cross-product** of candidate pairs (cached, validated, safe-abort).
    3. Keep only **unambiguous mutual** matches (a unique "yes" for both sides).
    4. Reconcile each confirmed twin — **EN-authority**: both id'd → rewrite DE's id to
       EN's; mixed → stamp the id'd side's id onto the id-less twin (a header rewrite, no
       translation). A would-be id collision on the loser deck defers that pair.
    5. **Direction-guarded hybrid** for the leftovers (no confirmed correspondent): all
       one direction → cross-add the genuinely-distinct slide (cannot double); both
       directions → defer.

    No verifier / a safe-abort defers everything (strategy A — never a wrong id). Both
    decks are written once (error-free passes only) and the watermark advances **only**
    when every suspect was resolved (``deferred == 0``); otherwise it holds and the
    unresolved suspects re-surface next run (the confirmed rewrites already on disk read
    as in-sync, so nothing doubles).
    """
    reconciles = [p for p in plan.proposals if p.kind == "reconcile"]
    if plan.has_errors:  # defense in depth — the emission gate already excludes errored plans
        result.deferred += len(reconciles)
        return

    de_state = FileState.load(plan.de_path)
    en_state = FileState.load(plan.en_path)
    de_props = [p for p in reconciles if p.direction == "de->en"]
    en_props = [p for p in reconciles if p.direction == "en->de"]
    de_suspects = [c for p in de_props if (c := _suspect_cell(de_state, "de", p)) is not None]
    en_suspects = [c for p in en_props if (c := _suspect_cell(en_state, "en", p)) is not None]
    # A suspect that cannot be located (the files changed under us) or a single-direction
    # bucket (shouldn't reach here) is not safe to resolve — defer the whole set.
    if (
        verifier is None
        or not de_props
        or not en_props
        or len(de_suspects) != len(de_props)
        or len(en_suspects) != len(en_props)
    ):
        result.deferred += len(reconciles)
        return

    pairs = [
        SlidePair(
            de_heading=_heading_line(de_c.body),
            en_heading=_heading_line(en_c.body),
            de_snippet=_snippet(de_c.body),
            en_snippet=_snippet(en_c.body),
            role=role_of(de_c.metadata) or "slide",
        )
        for de_c in de_suspects
        for en_c in en_suspects
    ]
    verdicts = _resolve_correspondence(verifier, correspondence_cache, pairs)
    if verdicts is None:  # safe-abort → refuse the whole set (never a wrong id)
        result.deferred += len(reconciles)
        return

    confirmed = _mutual_matches(verdicts, len(de_suspects), len(en_suspects))
    matched_de = {i for i, _ in confirmed}
    matched_en = {j for _, j in confirmed}

    deferred = 0
    for i, j in confirmed:
        if _reconcile_twin(de_suspects[i], en_suspects[j], de_state, en_state):
            result.applied_reconcile += 1
        else:
            deferred += 2  # both-id-less (defensive) or an id collision — defer the pair

    de_left = [c for i, c in enumerate(de_suspects) if i not in matched_de]
    en_left = [c for j, c in enumerate(en_suspects) if j not in matched_en]
    if de_left and en_left:
        deferred += len(de_left) + len(en_left)  # both directions → can't be sure → defer
    elif de_left:
        deferred += _cross_add_leftovers(
            de_left, "de", "en", de_state, en_state, translator, result
        )
    elif en_left:
        deferred += _cross_add_leftovers(
            en_left, "en", "de", en_state, de_state, translator, result
        )
    result.deferred += deferred

    # Write the confirmed rewrites + any cross-adds (error-free passes only), then advance
    # the watermark only when nothing was deferred — a held pass re-reads git HEAD next run
    # and the now-paired twins read as in-sync, so the partial progress never doubles.
    if not result.has_errors and not plan.has_errors:
        _flush_states_atomically(de_state, en_state)
    if (
        watermark_cache is not None
        and deferred == 0
        and not result.has_errors
        and not plan.has_errors
    ):
        _record_watermark(watermark_cache, plan.de_path, plan.en_path)
        result.watermark_recorded = True


def _suspect_cell(state: FileState, lang: str, proposal: Proposal) -> RawCell | None:
    """Locate a reconcile suspect's cell in the loaded state.

    An id'd suspect is found by ``(slide_id, role)``; an id-less one by its
    ``source_position`` among the language's sync cells (the same index
    :func:`ordered_sync_cells` assigns, stable since nothing committed between plan and
    apply). ``None`` when it cannot be located, so the caller defers the whole set.
    """
    if proposal.slide_id is not None:
        return state.find_cell(proposal.slide_id, proposal.role)
    pos = -1
    for cell in state.cells:
        if role_of(cell.metadata) is None or cell.metadata.lang != lang:
            continue
        pos += 1
        if pos == proposal.source_position:
            return cell
    return None


def _mutual_matches(verdicts: dict[int, bool], n: int, m: int) -> list[tuple[int, int]]:
    """The unambiguous mutual matches over an N×M correspondence cross-product.

    ``verdicts`` is keyed by the flat pair index ``i * m + j`` (DE suspect ``i`` × EN
    suspect ``j``). A pair is confirmed only when it is the sole "yes" in **both** its
    row and its column — so a DE suspect matching two EN suspects (or vice versa) leaves
    every involved pair ambiguous, and none are reconciled (they fall to the hybrid).
    """
    yes = [(i, j) for i in range(n) for j in range(m) if verdicts.get(i * m + j, False)]
    row = Counter(i for i, _ in yes)
    col = Counter(j for _, j in yes)
    return [(i, j) for i, j in yes if row[i] == 1 and col[j] == 1]


def _reconcile_twin(
    de_cell: RawCell, en_cell: RawCell, de_state: FileState, en_state: FileState
) -> bool:
    """Rewrite a confirmed twin's divergent id so both halves share one (EN-authority).

    Both id'd → EN wins (rewrite DE's id to EN's); exactly one id'd → the id'd side wins
    (stamp its id onto the id-less twin). Returns ``False`` (defer the pair, no write) when
    neither side carries an id (a defensive case the suspect buckets exclude) or the
    winning id already exists on the loser deck (a collision — never overwrite a sibling).
    """
    de_id = de_cell.metadata.slide_id
    en_id = en_cell.metadata.slide_id
    if de_id and en_id:
        winner, loser_state, loser_cell = en_id, de_state, de_cell  # EN-authority
    elif de_id:
        winner, loser_state, loser_cell = de_id, en_state, en_cell  # the id'd side wins
    elif en_id:
        winner, loser_state, loser_cell = en_id, de_state, de_cell
    else:
        return False  # both id-less — not a mismatched-id twin (defensive)
    role = role_of(loser_cell.metadata)
    if role is not None and loser_state.find_cell(winner, role) is not None:
        return False  # the winning id already lives on the loser deck — defer, don't collide
    _stamp_slide_id(loser_cell, winner)
    loser_state.dirty = True
    return True


def _cross_add_leftovers(
    leftovers: list[RawCell],
    source_lang: str,
    target_lang: str,
    source_state: FileState,
    target_state: FileState,
    translator: SlideTranslator | None,
    result: ApplyResult,
) -> int:
    """Cross-add genuinely-distinct one-sided leftovers (the hybrid's single-direction arm).

    Each leftover has no confirmed correspondent, so it is a real one-sided slide:
    translate its body and insert the twin on the other deck (an id'd leftover keeps its
    id; an id-less one mints a fresh EN-authority slug onto both). Safe because the caller
    only reaches here when leftovers exist in a **single** direction, so no twin can be
    doubled. Returns the count that could not be cross-added (no translator / a translation
    failure) — those are deferred so the watermark holds.

    Each leftover cell is cross-added **independently** — unlike :func:`_add_one_direction`,
    there is no group-adjacency (``renaming_from``) handling for a slide's companions,
    because a leftover companion is itself a committed-id'd suspect that arrives as its own
    reconcile candidate (and so its own leftover, cross-added under its own id). The
    whole-plan emission gate keeps this sound; relaxing it would require companion handling.
    """
    if translator is None:
        return len(leftovers)
    used_ids = {
        cell.metadata.slide_id
        for state in (source_state, target_state)
        for cell in state.cells
        if cell.metadata.slide_id
    }
    deferred = 0
    for cell in leftovers:
        role = role_of(cell.metadata) or "slide"
        try:
            target_body = translator.translate(
                source_body=cell.body.rstrip("\n"),
                source_lang=source_lang,
                target_lang=target_lang,
                role=role,
            )
        except TranslationError as exc:
            logger.info("reconcile cross-add: translation failed (%s) — deferred", exc)
            deferred += 1
            continue
        if cell.metadata.slide_id is None:
            en_body = target_body if target_lang == "en" else cell.body.rstrip("\n")
            new_id = resolve_collision(_slug_or_default(en_body), used_ids)
            used_ids.add(new_id)
            _stamp_slide_id(cell, new_id)
            source_state.dirty = True
        twin = build_twin_cell(cell, target_lang, target_body)
        _insert_at_anchor(
            target_state, _preceding_shared_anchor(source_state, target_state, cell), twin
        )
        target_state.dirty = True
        result.applied_add += 1
    return deferred


def _preceding_shared_anchor(
    source_state: FileState, target_state: FileState, cell: RawCell
) -> tuple[str, str] | None:
    """The nearest sync cell *before* ``cell`` in the source that also exists on the target.

    Gives a cross-add a sensible insertion anchor without the structural pass (which the
    reconcile short-circuit does not run). ``None`` falls back to inserting before the
    target's first sync cell.
    """
    anchor: tuple[str, str] | None = None
    for c in source_state.cells:
        if c is cell:
            break
        role = role_of(c.metadata)
        sid = c.metadata.slide_id
        if role is not None and sid is not None and target_state.find_cell(sid, role) is not None:
            anchor = (sid, role)
    return anchor


# ---------------------------------------------------------------------------
# Add (translate + mint + insert)
# ---------------------------------------------------------------------------


def _apply_adds(
    de_state: FileState,
    en_state: FileState,
    result: ApplyResult,
    plan: SyncPlan,
    translator: SlideTranslator | None,
    translations: dict[int, TranslationOutcome],
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

    # Stage 2b for the add path (#216 resolve-then-apply): every translation is
    # materialized into ``translations`` — the run's SHARED outcome cache, keyed
    # by source-cell id (#289 P2: the structural pass later reads the same cache,
    # so a deferred add's outcome is already there when a rebuild reaches for the
    # cell) — just before the execute walk that consumes it, the only place the
    # add path calls the translator. The walks below mint ids (deterministic) and
    # insert twins, reading the cache; the ``translator`` they still receive is
    # only a safety fallback for a cache miss (the materialize walks are built to
    # enumerate a superset of what the execute walks translate, so a miss never
    # happens). The materialize calls sit right before their walks so cell
    # identity — and the post-id-carrying state the id-less walk reads — match
    # exactly.

    # id-carrying adds: a new cell (slide / subslide / narrative / aux markdown /
    # localized code) already minted with a slide_id on one side only — e.g. a
    # deck whose ids were assigned before the split, where the author then adds a
    # new id'd slide on one half. Translate and insert the twin under the *same*
    # id (no minting, no collision) at its source-order anchor.
    if idd:
        de_keys = {(p.slide_id, p.role) for p in idd if p.direction == "de->en"}
        en_keys = {(p.slide_id, p.role) for p in idd if p.direction == "en->de"}
        _materialize_idcarrying(de_state, "de", "en", de_keys, translator, translations)
        _materialize_idcarrying(en_state, "en", "de", en_keys, translator, translations)
        if de_keys:
            _add_idcarrying_one_direction(
                de_state, en_state, "de", "en", de_keys, translator, result, translations
            )
        if en_keys:
            _add_idcarrying_one_direction(
                en_state, de_state, "en", "de", en_keys, translator, result, translations
            )

    if len(idless) + len(rename_props) == 0:
        return

    # The both-directions id-less refusal that used to live here (a deck-doubling
    # guard, plus the unguarded id-carrying sibling that bypassed it) is now a
    # plan-time decision in the resolver (#216): such adds reach apply as
    # ``refuse`` proposals, deferred above. Every ``add`` that survives to here is
    # therefore a single-direction add that applies mechanically.
    de_copy_ids = _identify_copy_slides(
        de_state, "de", [p for p in rename_props if p.direction == "de->en"]
    )
    en_copy_ids = _identify_copy_slides(
        en_state, "en", [p for p in rename_props if p.direction == "en->de"]
    )

    _materialize_idless(de_state, "de", "en", de_copy_ids, translator, translations)
    _materialize_idless(en_state, "en", "de", en_copy_ids, translator, translations)

    used_ids = {
        cell.metadata.slide_id
        for state in (de_state, en_state)
        for cell in state.cells
        if cell.metadata.slide_id
    }
    _add_one_direction(
        de_state, en_state, "de", "en", translator, used_ids, result, de_copy_ids, translations
    )
    _add_one_direction(
        en_state, de_state, "en", "de", translator, used_ids, result, en_copy_ids, translations
    )


def _cache_translation(
    cell: RawCell,
    source_lang: str,
    target_lang: str,
    role: str,
    translator: SlideTranslator,
    cache: dict[int, TranslationOutcome],
) -> None:
    """Translate one add source cell into ``cache`` (no ``result`` accounting here).

    The deferral/error counting for a failed translation happens later, in the
    execute walk's :func:`_translate`, so its order matches the legacy inline pass.
    """
    if id(cell) in cache:
        return
    try:
        body = translator.translate(
            source_body=cell.body.rstrip("\n"),
            source_lang=source_lang,
            target_lang=target_lang,
            role=role,
        )
        cache[id(cell)] = TranslationOutcome(body=body)
    except TranslationError as exc:
        cache[id(cell)] = TranslationOutcome(error=f"{role}: translation failed: {exc}")


def _materialize_idcarrying(
    source_state: FileState,
    source_lang: str,
    target_lang: str,
    add_keys: set[tuple[str | None, str]],
    translator: SlideTranslator,
    cache: dict[int, TranslationOutcome],
) -> None:
    """Pre-translate every id-carrying add cell (those :func:`_add_idcarrying_one_direction` translates)."""
    for cell in list(source_state.cells):
        role = role_of(cell.metadata)
        if role is None or cell.metadata.lang != source_lang:
            continue
        sid = cell.metadata.slide_id
        if sid is None or (sid, role) not in add_keys:
            continue
        _cache_translation(cell, source_lang, target_lang, role, translator, cache)


def _materialize_idless(
    source_state: FileState,
    source_lang: str,
    target_lang: str,
    copy_slide_ids: set[int],
    translator: SlideTranslator,
    cache: dict[int, TranslationOutcome],
) -> None:
    """Pre-translate every cell :func:`_add_one_direction` will translate.

    Mirrors that walk's *translate selection* exactly — a new or copied slide, and
    a narrative companion that is id-less or copy-pasted with a preceding slide —
    without minting or mutating. It tracks ``has_slide`` / ``renaming_from`` the
    same way but assumes each translation succeeds, so it enumerates a **superset**
    of what the execute walk actually translates (an upstream failure can only make
    the walk translate *fewer* cells). A superset means the execute walk never
    misses the cache; the surplus entries are simply never read.
    """
    renaming_from: str | None = None
    has_slide = False
    for cell in list(source_state.cells):
        role = role_of(cell.metadata)
        if role is None or cell.metadata.lang != source_lang:
            continue
        sid = cell.metadata.slide_id
        if role in _SLIDE_ROLES:
            is_copy = id(cell) in copy_slide_ids
            has_slide = True
            if sid is not None and not is_copy:
                renaming_from = None  # existing slide — anchors, not translated
                continue
            renaming_from = sid if is_copy else None
            _cache_translation(cell, source_lang, target_lang, role, translator, cache)
            continue
        # narrative
        is_copy_companion = renaming_from is not None and sid is not None and sid == renaming_from
        if sid is not None and not is_copy_companion:
            continue  # existing companion — anchors, not translated
        if not has_slide:
            continue  # orphan companion — errors before translate, never translated
        _cache_translation(cell, source_lang, target_lang, role, translator, cache)


def _materialize_structural(
    plan: SyncPlan,
    de_state: FileState,
    en_state: FileState,
    translator: SlideTranslator | None,
    baseline_anchors: dict[str, dict[str, str]],
    translations: dict[int, TranslationOutcome],
) -> None:
    """Pre-translate the structural pass's changed id-less localized cells (#289 P2).

    Stage 2b for the structural path: enumerates the id-less localized source
    cells :func:`clm.slides.sync_code._rebuild_region` translates — those whose
    content anchor is **not** recorded unchanged in the baseline (an unchanged
    cell is reuse-spliced verbatim, Issue #190 item 3, and must not be translated
    here either) — and resolves each through the run's shared outcome cache. The
    execute pass then reads the cache; a miss (a reuse-eligible cell whose target
    twin turns out absent or ambiguous mid-rebuild) falls back to translating
    inline, the same documented safety net the add path carries. Keyed-cell
    fallback translations need no enumeration here: a deferred add's source cell
    already sits in the shared cache from the add materializers.

    Skipped without a baseline (``plan.baseline_bundle`` ``None``): a no-baseline
    run has no reuse path, so the rebuild translates every id-less cell of a
    rebuilding region — but only of *rebuilding* regions, which cannot be
    enumerated without replaying the rebuild decisions; the inline path keeps
    that rare cold shape exactly as it was.
    """
    if translator is None or plan.baseline_bundle is None:
        return
    direction = _effective_direction(plan)
    if direction is None:
        return
    if direction == "en->de":
        source_state, source_lang, target_lang = en_state, "en", "de"
    else:
        source_state, source_lang, target_lang = de_state, "de", "en"
    src_anchors = baseline_anchors.get(source_lang, {})
    for cell in list(source_state.cells):
        meta = cell.metadata
        if meta.is_j2 or meta.lang != source_lang or role_of(meta) is not None:
            continue
        if src_anchors.get(anchor_of(meta, cell.body)) == cell_content_hash(cell.body):
            continue  # unchanged since baseline → spliced verbatim, never translated
        kind = CODE_ROLE if meta.cell_type == "code" else "markdown"
        _cache_translation(cell, source_lang, target_lang, kind, translator, translations)


def _add_idcarrying_one_direction(
    source_state: FileState,
    target_state: FileState,
    source_lang: str,
    target_lang: str,
    add_keys: set[tuple[str | None, str]],
    translator: SlideTranslator,
    result: ApplyResult,
    translations: dict[int, TranslationOutcome],
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
        target_body = _translate(
            cell, source_lang, target_lang, translator, role, result, translations
        )
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
    translations: dict[int, TranslationOutcome],
) -> None:
    """Walk the source deck, minting ids for new and copy-pasted slides.

    ``copy_slide_ids`` is the set of ``id()`` of copy-paste *slide* cells to
    re-mint (chosen by :func:`_identify_copy_slides`). A copied slide's narrative
    companions are re-minted by group-adjacency — ``renaming_from`` holds the
    copy slide's old id so each following same-id companion inherits the freshly
    minted id — rather than by a per-companion hash match (which identical
    companions defeat).

    Every id-less cell here is a single-direction add (the both-directions case is
    refused at plan time, #216), so it always mints and places — no gating.
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
            # id-less add OR copy slide: translate, mint EN-authority id, place.
            target_body = _translate(
                cell, source_lang, target_lang, translator, role, result, translations
            )
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
        if current_slide_id is None:
            result.deferred += 1
            result.errors.append(f"add {role}: narrative with no preceding slide — deferred")
            continue
        target_body = _translate(
            cell, source_lang, target_lang, translator, role, result, translations
        )
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
    translations: dict[int, TranslationOutcome],
) -> str | None:
    """Return the materialized translation of a cell, recording a deferral on failure.

    The translation was computed up front (#216 2b) and read from ``translations``
    here, in walk order, so the deferral/error accounting matches the legacy inline
    pass exactly. A cache miss falls back to translating now — a safety net that
    keeps behavior correct even if the materialize walk under-enumerated; it is not
    expected to fire (the materialize walks enumerate a superset). rstrip drops the
    terminal-newline artifact so the translator input does not depend on position.
    """
    cached = translations.get(id(cell))
    if cached is not None:
        if cached.error is not None:
            result.deferred += 1
            result.errors.append(cached.error)
            return None
        return cached.body
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
    new_cell = _build_cell(
        target_lang,
        cell.metadata.tags,
        new_id,
        target_body,
        comment_token_for_path(target_state.path),
    )
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
        # Drop the comment prefix as a literal prefix (either comment family),
        # so a "**bold**" or "/path" lead-in survives.
        if raw.startswith("# "):
            md = raw[2:].strip()
        elif raw.startswith("// "):
            md = raw[3:].strip()
        elif raw.startswith("#"):
            md = raw[1:].strip()
        elif raw.startswith("//"):
            md = raw[2:].strip()
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


def _build_cell(
    lang: str, tags: list[str], slide_id: str, body: str, comment_token: str = "#"
) -> RawCell:
    """Build a fresh markdown RawCell carrying the translated counterpart.

    The body is built bare (no leading/trailing blank lines); the insert
    primitive grants the deck's inter-cell separator based on final position.
    ``comment_token`` is the target deck's line-comment token (``"#"`` / ``"//"``).
    """
    tag_repr = ", ".join(f'"{t}"' for t in tags) if tags else '"slide"'
    header = f'{comment_token} %% [markdown] lang="{lang}" tags=[{tag_repr}] slide_id="{slide_id}"'
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


def _reconcile_group_order(
    plan: SyncPlan, de_state: FileState, en_state: FileState, result: ApplyResult
) -> None:
    """Reorder the target deck's slide groups to match the source's group order.

    The per-cell add path (:func:`_add_idcarrying_one_direction` /
    :func:`_add_one_direction`) anchors a brand-new slide group beside the nearest
    preceding neighbour it can name by ``(slide_id, role)``. A language-neutral or
    id-less neighbour is invisible to that anchor, so a new group can be inserted in
    the wrong inter-group slot — e.g. a new subslide added right after a neutral
    code cell lands *before* it instead. The structural pass
    (:func:`apply_code_structure`) rebuilds each group's *contents* from the source
    but iterates the target's groups in their current order, so it never reorders
    *groups*; the misplacement would otherwise survive and surface only as the
    shared-cell / id-less-localized parity error.

    Run after adds + the structural rebuild, against the run's single propagation
    source (the keyed-proposal direction, else the neutral-cell anchor direction).
    Only a reorder that reproduces the source's group order **and** its localized
    ``(slide_id, role)`` order exactly is committed — the same bar
    :func:`_apply_moves` uses — so a partial or ambiguous reorder is left untouched
    for the parity fail-safe rather than guessed at. Whole groups move as verbatim
    units (each group's internal order was already reconciled above), so this only
    fixes placement, never content.

    Gated on a fully clean pass (:func:`_pass_is_clean`), exactly like
    :func:`_apply_moves`: a deferred pass still *writes* its applied changes, so
    without this guard a reorder a user **skipped** in ``--interactive`` (deferred,
    never added to ``moves``) would be silently re-applied here against the source
    and persisted — overriding the skip and re-surfacing nothing next run. Holding
    the reorder for a clean pass keeps the skip honoured; a misplaced new group on
    a deferred pass simply re-reconciles on the next (clean) run.
    """
    if not _pass_is_clean(plan, result):
        return
    directions = {p.direction for p in plan.proposals if p.direction in ("de->en", "en->de")}
    direction = next(iter(directions)) if len(directions) == 1 else plan.anchor_direction
    if direction is None:
        return
    source_state, target_state = (
        (de_state, en_state) if direction == "de->en" else (en_state, de_state)
    )
    reordered = _group_reorder(target_state.cells, _group_order(source_state.cells))
    if reordered is None:
        return  # group order already matches the source
    if _group_order(reordered) != _group_order(source_state.cells) or _sync_key_order(
        reordered
    ) != _sync_key_order(source_state.cells):
        return  # would not fully reconcile — leave the divergence for the fail-safe
    sep = target_state.separator_blanks()
    original_last = target_state.cells[-1] if target_state.cells else None
    target_state.cells = reordered
    target_state.dirty = True
    if original_last is not None:
        target_state.normalize_displaced_last(original_last, sep)
    result.applied_structural += 1


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
    for cell in parse_cells(path.read_text(encoding="utf-8"), comment_token_for_path(path)):
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


def _anchor_map_from_rows(rows: list[tuple[str | None, str | None, str]]) -> dict[str, str]:
    """``{anchor: content_hash}`` over ``(slide_id, construct, content_hash)`` rows.

    A construct anchor is only a *name* (``extract_from_code``), so it is not
    content-unique: two cells sharing it (two ``import os``, two ``def solution``)
    cannot be told apart by anchor. Admit an anchor to the reuse set only when it
    occurs exactly once — a non-unique anchor cannot reliably locate a twin, so its
    cells re-translate (the honest §12 residual) rather than risk a wrong-twin
    splice (Issue #190 review). Shared by the watermark and git-HEAD baselines.
    """
    anchors = [row_anchor(sid, construct, chash) for (sid, construct, chash) in rows]
    counts = Counter(anchors)
    return {
        anchor: chash
        for anchor, (_sid, _construct, chash) in zip(anchors, rows, strict=True)
        if counts[anchor] == 1
    }


def _baseline_anchor_hashes(plan: SyncPlan) -> dict[str, dict[str, str]]:
    """Per-language ``{anchor: content_hash}`` of the last-synced state.

    The structural pass uses it to tell an UNCHANGED id-less localized code cell
    (anchor present with the same hash) from an edited one — so the former is reused
    verbatim and the latter is re-translated (Issue #190 item 3 / §8), and the same
    drift signal lets a group with an otherwise-unchanged ``("L", kind)`` signature
    rebuild (Issue #269). Read straight off the plan's resolved
    :class:`~clm.slides.sync_plan.BaselineBundle` (#289 P1) — watermark or
    git-HEAD, the same rows the classifier diffed against, so plan and apply
    agree by construction (this replaces a per-source re-derivation whose
    git-HEAD branch keyed anchors over a *different* cell population than the
    watermark branch). Empty for a baseline of ``none`` (true cold start) — the
    structural pass then translates as before.
    """
    out: dict[str, dict[str, str]] = {"de": {}, "en": {}}
    bundle = plan.baseline_bundle
    if bundle is None:
        return out
    for lang in ("de", "en"):
        out[lang] = _anchor_map_from_rows(
            [(sid, construct, chash) for (_p, sid, _r, chash, construct) in bundle.rows[lang]]
        )
    return out


def _effective_direction(plan: SyncPlan) -> str | None:
    """The pass's single propagation direction: keyed proposals, else anchor diff."""
    keyed = {p.direction for p in plan.proposals if p.direction in ("de->en", "en->de")}
    if len(keyed) == 1:
        return next(iter(keyed))
    return plan.anchor_direction


def _baseline_shared(plan: SyncPlan) -> tuple[dict[str, str], set[str]]:
    """Baseline ``{slide_id: construct}`` and the set of content hashes, ``shared`` only.

    The §9 migration only touches language-neutral code cells, which live in the
    ``shared`` partition — so the baseline is read from there alone (a slide_id
    reused across partitions must not borrow another partition's construct). The
    content-hash set lets the migration tell a genuine split-product (a *new* cell)
    from a pre-existing cell that merely shares a construct name. Read from the
    plan's :class:`~clm.slides.sync_plan.BaselineBundle` (#289 P1) — so the
    migration now also runs on a committed (git-HEAD) baseline, where it was
    previously inert (the cache had no pair to read).
    """
    constructs: dict[str, str] = {}
    hashes: set[str] = set()
    bundle = plan.baseline_bundle
    if bundle is None:
        return constructs, hashes
    for _pos, sid, _role, chash, construct in bundle.rows["shared"]:
        hashes.add(chash)
        if sid is not None and construct is not None:
            constructs.setdefault(sid, construct)
    return constructs, hashes


def _migrate_drifted_ids(
    plan: SyncPlan,
    de_state: FileState,
    en_state: FileState,
    result: ApplyResult,
    recoverer: AlignmentRecoverer | None = None,
    alignment_cache: SyncAlignmentCache | None = None,
) -> None:
    """Move a slide_id that drifted off its construct back onto the right cell (§9).

    Scoped to language-**neutral** code cells (the maintainer's def-my-fun example):
    when the author splits an id'd cell — e.g. adds an ``import`` above a ``def``,
    leaving the id on the import — the id wears the wrong construct while a *new*
    id-less cell carries the construct the id names in the baseline. Move the id
    down and mint a fresh content slug on the orphan: one targeted header write each,
    no LLM.

    Applied to **both** decks (not just the propagation source): neutral cells are
    byte-identical across the split halves, so the move is deterministic on each,
    and the structural pass — which keys on the cell *body*, blind to a header-only
    id change — cannot be relied on to carry a migrated id to the twin (it would
    leave a silent cross-deck slide_id divergence). The direction merely gates
    *whether* a migration pass is active (so an idle no-op never writes).

    The matched id-less cell must be **new** (its content hash absent from the
    baseline), which distinguishes a real split-product from an unrelated
    pre-existing cell that coincidentally shares the construct name (the Phase 4
    review's false-move finding). Ambiguous cases — a non-unique construct (the
    recurring guard) or a co-occurring function rename — are left for §10 recovery.
    (Localized id'd cells, which need a symmetric paired ``de_id==en_id`` write, are
    deferred.)

    **Localized** id'd code cells (``lang=``, Phase 5d) are migrated through a
    separate *paired* chokepoint (:func:`_migrate_localized_paired`): their twins
    differ across decks (translated bodies) and the structural pass cannot carry a
    header-only id change between them, so the move is stamped onto **both** decks'
    twins at once to keep ``de_id == en_id``.

    When *every* deterministic tier is stuck on an ambiguous region (it made no
    move) and a ``recoverer`` is configured (``--llm-recover``), escalate the
    **neutral** region to the bounded-LLM alignment tier
    (:func:`_recover_drifted_ids`). If any deterministic move landed this pass, the
    tiers are **not** mixed — remaining ambiguity re-surfaces next run once the
    clean moves are baselined.
    """
    if _effective_direction(plan) is None:
        return
    before = result.applied_migrate
    baseline_constructs, baseline_hashes = _baseline_shared(plan)
    if baseline_constructs:
        _migrate_one_deck(de_state, baseline_constructs, baseline_hashes, result)
        _migrate_one_deck(en_state, baseline_constructs, baseline_hashes, result)
    loc_constructs, loc_de_hashes, loc_en_hashes = _baseline_localized(plan)
    if loc_constructs:
        _migrate_localized_paired(
            de_state, en_state, loc_constructs, loc_de_hashes, loc_en_hashes, result
        )
    if recoverer is not None and baseline_constructs and result.applied_migrate == before:
        _recover_drifted_ids(
            plan, de_state, en_state, baseline_constructs, recoverer, alignment_cache, result
        )


def _migrate_one_deck(
    state: FileState,
    baseline_constructs: dict[str, str],
    baseline_hashes: set[str],
    result: ApplyResult,
) -> None:
    """Apply the §9 id-migration to one deck's neutral code cells (see caller)."""
    used_ids = {cell.metadata.slide_id for cell in state.cells if cell.metadata.slide_id}
    idless_by_construct: dict[str, list[RawCell]] = {}
    construct_count: Counter = Counter()
    for cell in state.cells:
        meta = cell.metadata
        if meta.is_j2 or meta.lang is not None:
            continue  # neutral cells only
        construct = construct_of(meta, cell.body)
        if construct is None:
            continue
        construct_count[construct] += 1
        if meta.slide_id is None:
            idless_by_construct.setdefault(construct, []).append(cell)

    for cell in list(state.cells):
        meta = cell.metadata
        if meta.is_j2 or meta.lang is not None or meta.slide_id is None:
            continue
        base_construct = baseline_constructs.get(meta.slide_id)
        current_construct = construct_of(meta, cell.body)
        if (
            base_construct is None
            or current_construct is None
            or current_construct == base_construct
        ):
            continue  # the id still names its content (or there is nothing to key on)
        if construct_count[base_construct] != 1:
            continue  # the construct is not unique in this deck -> ambiguous, defer
        # The target must be a NEW cell (a split-product), not a pre-existing one
        # that merely shares the construct name (the review's false-move finding).
        targets = [
            c
            for c in idless_by_construct.get(base_construct, [])
            if cell_content_hash(c.body) not in baseline_hashes
        ]
        if len(targets) != 1:
            continue
        sid = meta.slide_id
        _stamp_slide_id(targets[0], sid)  # the id follows its construct
        new_slug = resolve_collision(current_construct, used_ids)
        used_ids.add(new_slug)
        _stamp_slide_id(cell, new_slug)  # the orphan gets a fresh content slug
        state.dirty = True
        result.applied_migrate += 1
        idless_by_construct.pop(base_construct, None)  # at most one migration per construct


def _baseline_localized(plan: SyncPlan) -> tuple[dict[str, str], set[str], set[str]]:
    """Baseline ``{slide_id: construct}`` for localized id'd code, + per-deck hashes.

    Localized cells live in the ``de``/``en`` partitions (not ``shared``): an id'd
    one carries the real :data:`CODE_ROLE`. The construct of a given ``slide_id`` is
    language-neutral (a function/class name does not translate), so the two decks
    agree — it is read from the ``de`` partition. The per-deck content-hash sets
    (every recorded row of each deck) let the paired migration tell a genuine
    split-product (a *new* cell) from a pre-existing one (each deck checked against
    its own baseline, since localized bodies differ across decks). Read from the
    plan's :class:`~clm.slides.sync_plan.BaselineBundle` (#289 P1), so it works on
    a committed (git-HEAD) baseline too.
    """
    constructs: dict[str, str] = {}
    de_hashes: set[str] = set()
    en_hashes: set[str] = set()
    bundle = plan.baseline_bundle
    if bundle is None:
        return constructs, de_hashes, en_hashes
    for _pos, sid, role, chash, construct in bundle.rows["de"]:
        de_hashes.add(chash)
        if role == CODE_ROLE and sid is not None and construct is not None:
            constructs.setdefault(sid, construct)
    for _pos, _sid, _role, chash, _construct in bundle.rows["en"]:
        en_hashes.add(chash)
    return constructs, de_hashes, en_hashes


def _localized_code_index(
    state: FileState, baseline_hashes: set[str]
) -> tuple[dict[str, RawCell], dict[str, list[RawCell]], Counter]:
    """Index one deck's localized code cells for the paired migration.

    Returns ``(idd_by_slide_id, new_idless_by_construct, construct_count)``: the
    id'd cells keyed by their ``slide_id``, the **new** (hash absent from this
    deck's baseline) id-less cells grouped by construct, and the per-construct count
    over all localized code cells (the uniqueness guard).
    """
    idd: dict[str, RawCell] = {}
    idless_by_construct: dict[str, list[RawCell]] = {}
    construct_count: Counter = Counter()
    for cell in state.cells:
        meta = cell.metadata
        if meta.is_j2 or meta.lang is None or meta.cell_type != "code":
            continue  # localized code cells only
        construct = construct_of(meta, cell.body)
        if construct is None:
            continue
        construct_count[construct] += 1
        if meta.slide_id is not None:
            idd[meta.slide_id] = cell
        elif cell_content_hash(cell.body) not in baseline_hashes:
            idless_by_construct.setdefault(construct, []).append(cell)
    return idd, idless_by_construct, construct_count


def _migrate_localized_paired(
    de_state: FileState,
    en_state: FileState,
    baseline_constructs: dict[str, str],
    de_hashes: set[str],
    en_hashes: set[str],
    result: ApplyResult,
) -> None:
    """Move a drifted localized slide_id symmetrically on both decks (§9 / Phase 5d).

    The localized analogue of :func:`_migrate_one_deck`: when an id'd localized code
    cell is split on **both** decks — the id left wearing the wrong construct while a
    new id-less twin carries the construct the id names — move the id onto both new
    twins and mint one shared fresh slug onto both orphans. Both decks are written in
    lockstep so ``_slide_ids_pair``'s ``de_id == en_id`` invariant holds (the
    structural pass cannot carry a header-only change between the two translated,
    non-byte-identical twins).

    Conservative by construction — every guard must hold or the id is left for §10:
    the id must be present and drifted on **both** decks, the two twins must have
    drifted to the **same** construct, the baseline construct must be unique on each
    deck, and each deck must have exactly one **new** id-less cell carrying it.
    """
    de_idd, de_idless, de_count = _localized_code_index(de_state, de_hashes)
    en_idd, en_idless, en_count = _localized_code_index(en_state, en_hashes)
    used_ids = {c.metadata.slide_id for c in de_state.cells if c.metadata.slide_id}
    used_ids |= {c.metadata.slide_id for c in en_state.cells if c.metadata.slide_id}
    for sid, base_construct in baseline_constructs.items():
        de_cell = de_idd.get(sid)
        en_cell = en_idd.get(sid)
        if de_cell is None or en_cell is None:
            continue  # the id must be a twinned pair present on both decks
        de_cur = construct_of(de_cell.metadata, de_cell.body)
        en_cur = construct_of(en_cell.metadata, en_cell.body)
        if de_cur is None or en_cur is None:
            continue
        if de_cur == base_construct or en_cur == base_construct:
            continue  # not drifted on both decks -> asymmetric or no-op, defer
        if de_cur != en_cur:
            continue  # twins drifted to different constructs -> ambiguous, defer
        if de_count[base_construct] != 1 or en_count[base_construct] != 1:
            continue  # base construct not unique on some deck -> ambiguous, defer
        de_targets = de_idless.get(base_construct, [])
        en_targets = en_idless.get(base_construct, [])
        if len(de_targets) != 1 or len(en_targets) != 1:
            continue  # no unique NEW split-product on both decks -> defer
        _stamp_slide_id(de_targets[0], sid)  # the id follows its construct, on both decks
        _stamp_slide_id(en_targets[0], sid)
        new_slug = resolve_collision(de_cur, used_ids)  # de_cur == en_cur (neutral construct)
        used_ids.add(new_slug)
        _stamp_slide_id(de_cell, new_slug)  # both orphans get the same fresh slug
        _stamp_slide_id(en_cell, new_slug)
        de_state.dirty = True
        en_state.dirty = True
        result.applied_migrate += 2  # one logical move, written to two decks
        de_idless.pop(base_construct, None)
        en_idless.pop(base_construct, None)


def _neutral_code_cells(state: FileState) -> list[RawCell]:
    """The deck's language-neutral code cells in document order (the §9/§10 region).

    Includes unparsable (``construct is None``) code cells so the region matches
    the watermark's ``neutral-code`` rows one-to-one — both index by the same cell
    set, so a recovery map's indices line up on either side.
    """
    return [
        cell
        for cell in state.cells
        if not cell.metadata.is_j2
        and cell.metadata.lang is None
        and cell.metadata.cell_type == "code"
    ]


def _region_of(cells: list[RawCell]) -> list[RegionCell]:
    """The body-free :class:`RegionCell` view of an ordered neutral-code cell list."""
    return [
        RegionCell(
            slide_id=cell.metadata.slide_id,
            construct=construct_of(cell.metadata, cell.body),
            content_hash=cell_content_hash(cell.body),
        )
        for cell in cells
    ]


def _shared_region(plan: SyncPlan) -> list[RegionCell]:
    """The baseline neutral-**code** region from the bundle's ``shared`` partition.

    Filters the membership-widened ``shared`` rows to the ``neutral-code`` role so
    the base region covers exactly the cells :func:`_neutral_code_cells` yields live
    (neutral markdown is excluded). Position order is preserved. Read from the
    plan's :class:`~clm.slides.sync_plan.BaselineBundle` (#289 P1).
    """
    bundle = plan.baseline_bundle
    if bundle is None:
        return []
    return [
        RegionCell(slide_id=sid, construct=construct, content_hash=chash)
        for (_pos, sid, role, chash, construct) in bundle.rows["shared"]
        if role == NEUTRAL_CODE_ROLE
    ]


def _has_drifted_id(baseline_constructs: dict[str, str], region: list[RegionCell]) -> bool:
    """Whether some current cell wears an id whose baseline construct it no longer names.

    The escalation trigger: only a genuine id-vs-construct drift is worth the LLM —
    a region with no drifted id has nothing to recover, so the recoverer never fires.
    """
    for cell in region:
        if cell.slide_id is None:
            continue
        base = baseline_constructs.get(cell.slide_id)
        if base is not None and cell.construct is not None and cell.construct != base:
            return True
    return False


def _recover_drifted_ids(
    plan: SyncPlan,
    de_state: FileState,
    en_state: FileState,
    baseline_constructs: dict[str, str],
    recoverer: AlignmentRecoverer,
    alignment_cache: SyncAlignmentCache | None,
    result: ApplyResult,
) -> None:
    """Bounded-LLM recovery of an ambiguous drifted-id region (§10 / Phase 5).

    Fires only when the deterministic §9 pass is stuck (its caller gates on
    ``applied_migrate`` unchanged). Builds the body-free base/current regions, asks
    the ``recoverer`` for a validated id↔cell map (cached by region fingerprints),
    and applies it **symmetrically** to both decks (neutral cells are byte-identical
    across halves, so the same map keeps ``de_id == en_id``).

    Any failure — the decks out of unify, no real drift, the recoverer down, or a
    map that fails :func:`validate_alignment` — **safe-aborts**: the region is left
    untouched and the pass is marked ``deferred`` (which holds the watermark, so the
    region re-surfaces next run) rather than erroring (which would block the whole
    deck's write). A wrong id is worse than a deferred one.
    """
    de_cells = _neutral_code_cells(de_state)
    en_cells = _neutral_code_cells(en_state)
    current_region = _region_of(de_cells)
    # The map is derived from DE's region and applied to BOTH decks by index, so the
    # two neutral regions must be byte-identical (the unify invariant). Require it
    # explicitly — a content divergence with the *same* length would otherwise let a
    # DE-derived map mis-stamp EN. A divergence is the align_anchored pass's job, not
    # the migration's; defer rather than risk de_id != en_id.
    if current_region != _region_of(en_cells):
        return
    if not _has_drifted_id(baseline_constructs, current_region):
        return  # nothing genuinely drifted -> do not spend the LLM
    base_region = _shared_region(plan)
    if not base_region:
        return
    # Only escalate when there is a genuine realignment target: a *new* id-less
    # neutral code cell (a split product — its content is absent from the baseline).
    # A pure in-place rename has none (its only change is an id'd cell whose body
    # changed), so there is nothing to realign — leaving it to re-baseline keeps its
    # stable slide_id, instead of handing the region to the LLM, which could strip
    # the id (Issue #190 Phase 5 review). validate_alignment is the hard backstop (it
    # refuses to drop a worn id); this just avoids a pointless, re-surfacing LLM call
    # when no split occurred.
    baseline_hashes = {c.content_hash for c in base_region}
    if not any(
        c.slide_id is None and c.content_hash not in baseline_hashes for c in current_region
    ):
        return

    mapping = _resolve_alignment(recoverer, alignment_cache, base_region, current_region)
    if mapping is None:
        # Safe-abort: leave the region untouched, hold the watermark so it
        # re-surfaces. Not an error — that would block the whole pass's write.
        result.deferred += 1
        return
    _apply_alignment(de_state, de_cells, mapping, result)
    _apply_alignment(en_state, en_cells, mapping, result)


def _resolve_alignment(
    recoverer: AlignmentRecoverer,
    alignment_cache: SyncAlignmentCache | None,
    base_region: list[RegionCell],
    current_region: list[RegionCell],
) -> dict[int, str] | None:
    """A validated alignment map for the region pair, or ``None`` to safe-abort.

    Prefers a cached, re-validated map (no LLM spend); on a miss it calls the
    recoverer once and caches only a *valid* result. Every path that cannot produce
    a sound map returns ``None`` so the caller defers the region.
    """
    fp_base = region_fingerprint(base_region)
    fp_cur = region_fingerprint(current_region)
    pv = recoverer.prompt_version
    if alignment_cache is not None:
        cached = alignment_cache.get(fp_base, fp_cur, pv)
        if cached is not None:
            try:
                return validate_alignment(decode_mapping(cached), base_region, current_region)
            except AlignmentInvalid:
                logger.warning("llm-recover: cached alignment failed validation; re-deriving")
    try:
        raw = recoverer.recover(base_region=base_region, current_region=current_region)
        mapping = validate_alignment(raw, base_region, current_region)
    except (RecoveryError, AlignmentInvalid) as exc:
        logger.warning("llm-recover: alignment unavailable/invalid (%s); region deferred", exc)
        return None
    if alignment_cache is not None:
        alignment_cache.put(fp_base, fp_cur, pv, encode_mapping(mapping))
    return mapping


def _apply_alignment(
    state: FileState,
    region_cells: list[RawCell],
    mapping: dict[int, str],
    result: ApplyResult,
) -> None:
    """Apply a validated alignment map to one deck's neutral-code region.

    Each region cell is re-identified to the id the map assigns: a base ``slide_id``
    (continuation), a freshly-minted content slug (:data:`NEW`), or no id
    (:data:`NONE`). ``used_ids`` spans the whole deck so a minted slug never collides;
    processing both decks with the same map and an identically-evolving ``used_ids``
    keeps ``de_id == en_id``. Cells already carrying the assigned id are no-ops.
    """
    used_ids = {cell.metadata.slide_id for cell in state.cells if cell.metadata.slide_id}
    for idx, cell in enumerate(region_cells):
        target = mapping.get(idx)
        if target is None:
            continue  # defensive: validation guarantees total coverage
        current_id = cell.metadata.slide_id
        if target == NONE:
            desired: str | None = None
        elif target == NEW:
            construct = construct_of(cell.metadata, cell.body)
            if construct is None:
                continue  # validation forbids NEW on a construct-less cell; defensive
            desired = resolve_collision(construct, used_ids)
        else:
            desired = target  # a base id (continuation)
        if desired == current_id:
            continue
        if desired is None:
            _clear_slide_id(cell)
        else:
            used_ids.add(desired)
            _stamp_slide_id(cell, desired)
        state.dirty = True
        result.applied_migrate += 1


def _clear_slide_id(cell: RawCell) -> None:
    """Remove any ``slide_id="…"`` from a cell header (the inverse of stamping)."""
    header = _SLIDE_ID_RE.sub("", cell.lines[0]).rstrip()
    cell.lines[0] = header
    cell.metadata = parse_cell_header(header)


def _header_rows(cells: list[Cell]) -> list[tuple[int, str | None, str, str, str | None]]:
    """Watermark rows for a file's j2 deck-header cells (Issue #269).

    ``(position, slide_id=None, role="header", content_hash, construct=None)`` in
    j2-cell order — the ``de-header`` / ``en-header`` partition the one-sided
    header-drift check diffs against.
    """
    rows: list[tuple[int, str | None, str, str, str | None]] = []
    pos = 0
    for cell in cells:
        if cell.metadata.is_j2:
            # A j2 directive's macro text is its header line; Cell.content is empty.
            rows.append((pos, None, "header", cell_content_hash(cell.header), None))
            pos += 1
    return rows


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
    de_cells = parse_cells(de_path.read_text(encoding="utf-8"), comment_token_for_path(de_path))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"), comment_token_for_path(en_path))
    de_rows = watermark_rows(de_cells)
    en_rows = watermark_rows(en_cells)
    # Issue #198: record each cell's tag set alongside its row so a later run can
    # detect a tag-only edit (invisible to the content hash) and mirror it.
    de_tags = watermark_tag_map(de_cells)
    en_tags = watermark_tag_map(en_cells)
    cache.put_deck(
        de_path=str(de_path),
        en_path=str(en_path),
        lang="de",
        cells=de_rows["de"],
        tags=de_tags["de"],
    )
    cache.put_deck(
        de_path=str(de_path),
        en_path=str(en_path),
        lang="en",
        cells=en_rows["en"],
        tags=en_tags["en"],
    )
    # Neutral cells are byte-identical across halves (the unify invariant), so the
    # single-entity "shared" partition is recorded once from the DE file.
    cache.put_deck(
        de_path=str(de_path),
        en_path=str(en_path),
        lang="shared",
        cells=de_rows["shared"],
        tags=de_tags["shared"],
    )
    # Issue #269: record each half's j2 deck-header cells (excluded from every other
    # partition) so a later run can detect a one-sided header edit — which sync never
    # auto-translates — instead of silently reporting the decks consistent.
    cache.put_deck(
        de_path=str(de_path),
        en_path=str(en_path),
        lang="de-header",
        cells=_header_rows(de_cells),
    )
    cache.put_deck(
        de_path=str(de_path),
        en_path=str(en_path),
        lang="en-header",
        cells=_header_rows(en_cells),
    )
    # Record the repo HEAD at sync time (Fix D): lets a later run name the exact
    # `--baseline <ref>` when the watermark has fallen behind committed edits.
    # Best-effort — git provenance must never fail a sync.
    from clm.core.git_info import get_git_info

    commit = get_git_info(de_path.parent).get("commit")
    cache.set_synced_commit(str(de_path), str(en_path), commit if isinstance(commit, str) else None)


def _eligible_for_partial_advance(plan: SyncPlan, result: ApplyResult) -> bool:
    """Whether a per-cell partial watermark advance is safe for this pass.

    Restricted to a **content-only** partial pass: every proposal is an
    ``edit`` / ``conflict`` / ``retag`` (no add / remove / move / rename) AND the
    plan carries **no blocking issues**. The blocking-issue guard matters: a
    both-decks reorder or an ambiguous de/en state is emitted as a *warning* (not
    a proposal and not an error), and its order/ambiguity is deliberately not
    reconciled — so a pass carrying any such issue must hold the whole watermark,
    else that un-propagated signal would be silently baselined. A *tag-only*
    both-decks conflict (Issue #202) is **not** blocking: it touches no body, so
    the body baseline (and current positions) are unchanged and the partial
    advance can bank everything while pinning just that cell's tags. With neither
    structural proposals nor blocking issues, both decks' ordered ``(slide_id,
    role)`` structure is unchanged, so current positions equal baseline positions.

    Requires a real baseline, no errors, and *something to bank*: either a
    reconciled write co-existing with a deferral (``applied_edit > 0`` and
    ``deferred > 0`` — a clean pass with neither already did a full advance), or a
    tag-only hold to pin (Issue #202). Any other partial pass falls back to
    holding the whole watermark — safe, just noisier on the next run.
    """
    if not plan.has_baseline or plan.blocking_issues or result.errors:
        return False
    # A tag-only hold (#202) is itself a per-cell deferral the partial path banks
    # around, so it makes the pass eligible on its own (a co-applied clean edit may
    # have ``deferred == 0``). Absent a tag hold, fall back to the original
    # content-conflict rule: bank only when a reconciled write and a true deferral
    # co-exist — there is nothing to bank in a pass that only deferred.
    if not plan.tag_holds and (result.deferred <= 0 or result.applied_edit <= 0):
        return False
    structural = (
        plan.count("add")
        + plan.count("remove")
        + plan.count("move")
        + plan.count("rename")
        # A reconcile plan is a whole-plan short-circuit that never reaches this path;
        # counting it here is cheap insurance against a stray reconcile ever partial-advancing.
        + plan.count("reconcile")
    )
    return structural == 0


def _tag_hold_position(cells: list[Cell], lang: str, hold: TagHold) -> int | None:
    """The membership-partition index of the cell a :class:`TagHold` pins (#202).

    An id-less hold carries its ``position`` directly — the watermark ``lang``
    partition index, identical on both aligned halves (the classifier emits it only
    once :func:`_streams_aligned` holds). An id-carrying hold is located by
    ``(slide_id, role)`` among the language's non-j2 cells, counting in the exact
    per-partition order :func:`watermark_rows` / :func:`watermark_tag_map` assign.
    Returns ``None`` when an id-carrying cell is absent (so the caller can hold the
    whole watermark rather than pin the wrong cell).
    """
    if hold.position is not None:
        return hold.position
    pos = 0
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.lang != lang:
            continue
        if meta.slide_id == hold.slide_id and role_of(meta) == hold.role:
            return pos
        pos += 1
    return None


def _record_watermark_partial(
    cache: SyncWatermarkCache,
    de_path: Path,
    en_path: Path,
    preserve_keys: set[tuple[str, str]],
    tag_holds: list[TagHold],
) -> bool:
    """Advance the watermark per-cell, holding the true deferrals at baseline.

    Records the **current** state for every cell — the reconciliation default,
    matching the full advance — EXCEPT for two kinds of held cell:

    - any ``(slide_id, role)`` in ``preserve_keys`` (an unresolved conflict or a
      user-skipped edit) keeps its **old** baseline *body hash* so it re-surfaces
      next run instead of being silently baselined; and
    - any cell named by a ``tag_hold`` (Issue #202 — a tag-only both-decks
      conflict) keeps its **old** baseline *tags* on both halves, so the tag
      conflict re-surfaces while its body baseline — and every other cell,
      including a co-applied clean edit — still banks.

    An ``in_sync`` edit is *not* a deferral — the judge reconciled it — so it banks
    like an applied edit.

    Valid only on a content-only pass with no *blocking* issues (see
    :func:`_eligible_for_partial_advance`): structure is unchanged, so current
    positions equal baseline positions. Returns ``False`` without writing if any
    preserved key is absent from *either* deck's old baseline **or** current
    cells, or if a tag hold cannot be faithfully located / lacks an old baseline
    tag set on either half. The current-cell check matters: a "removed on one deck
    / edited on the other" collision is classified as a ``conflict`` (not a
    ``remove``), so it slips the structural gate, yet the cell is gone from the
    removing deck — we cannot faithfully preserve it there, so we hold the whole
    watermark and let the conflict re-surface instead of dropping it.
    """
    parsed = {
        "de": parse_cells(de_path.read_text(encoding="utf-8"), comment_token_for_path(de_path)),
        "en": parse_cells(en_path.read_text(encoding="utf-8"), comment_token_for_path(en_path)),
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
    # Issue #198: a content-only pass leaves every cell's tags as they are on disk,
    # so the current tag map is the right baseline for every recorded cell — EXCEPT
    # the Issue #202 tag holds, whose tags we rewind to the old baseline below.
    tag_map = {"de": watermark_tag_map(parsed["de"]), "en": watermark_tag_map(parsed["en"])}
    if tag_holds:
        old_tags = {
            "de": cache.get_deck_tags(str(de_path), str(en_path), "de"),
            "en": cache.get_deck_tags(str(de_path), str(en_path), "en"),
        }
        for hold in tag_holds:
            for lang in ("de", "en"):
                pos = _tag_hold_position(parsed[lang], lang, hold)
                # A both-decks tag conflict only ever fires with a known baseline on
                # both halves, so a missing position / tag here means the held cell
                # could not be faithfully re-located — hold the whole watermark
                # rather than risk pinning (or silently advancing) the wrong cell.
                if pos is None or pos not in old_tags[lang]:
                    return False
                tag_map[lang][lang][pos] = old_tags[lang][pos]
    for lang in ("de", "en"):
        rows: list[tuple[int, str | None, str, str, str | None]] = []
        for pos, sid, role, chash, construct in widened[lang][lang]:
            if role not in MEMBERSHIP_ROLES and sid is not None and (sid, role) in preserve_keys:
                # Hold the deferral at its pre-conflict baseline so it re-surfaces.
                chash = old[lang][(sid, role)]
            rows.append((pos, sid, role, chash, construct))
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=rows,
            tags=tag_map[lang][lang],
        )
    cache.put_deck(
        de_path=str(de_path),
        en_path=str(en_path),
        lang="shared",
        cells=widened["de"]["shared"],
        tags=tag_map["de"]["shared"],
    )
    return True
