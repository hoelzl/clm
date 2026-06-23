"""Validated write-back of an agent's answer (epic #440, decision B).

``clm slides sync accept DECK --item ID --answer -`` takes the answer an agent
produced for the framed :class:`~clm.slides.sync_task.SyncTask`, runs it through
the **deterministic validator the task named**, and writes the result to **both**
split halves iff it passes — maintaining ``de_id == en_id`` and the byte-identity
of language-neutral cells. On a validation failure it rejects with the precise
reason and **writes nothing**. The engine never calls a model here: the model ran
*between* ``task`` and ``accept``, and ``accept`` only validates + applies.

It is the agent-path twin of the embedded clients ``autopilot`` drives — the write
itself reuses the existing :func:`~clm.slides.sync_apply.apply_plan` machinery, but
with the agent's answer substituted for the model call:

* **realign** (a drifted ``slide_id`` the deterministic id-migration could not
  resolve) → the answer is the body-free ``{index → assignment}`` map; it is gated
  by :func:`~clm.slides.sync_recover.validate_alignment` and applied through the
  same id-migration recovery tier ``--llm-recover`` used, only with a
  :class:`~clm.slides.sync_recover.StaticAlignmentRecoverer` carrying the agent's
  map instead of an OpenRouter call.
* **add** (a brand-new slide) → the answer is the translated cell body; the new
  slide's twin is translated by a single-answer stand-in over a plan **pruned to
  that one add**, so no other pending item is touched.
* **edit** (a drifted localized cell — **keyed**, a **narrative** companion #403, or an
  **id-less localized** cell #365) → a **code** edit's answer is the re-translated body
  (``{translated_body}``); a **prose** (markdown / narrative) edit's answer is the judge
  verdict (``{verdict, proposed_text}``, with ``in_sync`` accepted as a no-op). The write
  reuses the engine's edit path with a static judge / translator carrying the agent's
  answer, over a plan pruned to that one edit and applied in the engine's
  ``scope_to_proposals`` mode — only the per-cell write runs, no structural pass, so a
  *co-drifted* sibling in the same slide group is never re-translated with this one answer.

* **mint** / **adopt** / **reconcile** (a pair whose correspondence is unconfirmed) →
  the answer is the ``{pair_index → bool}`` correspondence verdict map; it is gated by
  :func:`~clm.slides.sync_recover.validate_correspondence` and applied through the same
  cold-start / reconcile path ``autopilot`` drives, only with a
  :class:`~clm.slides.sync_recover.StaticCorrespondenceVerifier` carrying the agent's
  verdicts instead of an OpenRouter call. For a cold-start ``mint`` / ``adopt`` an
  **all-yes** map mints / stamps the shared ids and any **no** declines; for a committed
  ``reconcile`` (#228) each unambiguous mutual match has its divergent id rewritten
  (EN-authority) and a genuinely-distinct leftover is deferred (it re-surfaces as an
  ``add``). Either way a decline writes nothing and names the next step.

The hand-judged ambiguities (``conflict`` / ``issue``) are not accepted — :func:`accept_answer`
raises :class:`AcceptUnavailable` with the right next step.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.raw_cells import is_cell_boundary
from clm.slides.sync_plan import LOCALIZED_CODE_ROLE
from clm.slides.sync_recover import (
    AlignmentInvalid,
    StaticAlignmentRecoverer,
    validate_alignment,
)
from clm.slides.sync_report import build_report
from clm.slides.sync_writeback import CODE_ROLE

if TYPE_CHECKING:
    from clm.slides.sync_plan import Proposal, SyncPlan
    from clm.slides.sync_report import ReconciliationItem

__all__ = [
    "AcceptRejected",
    "AcceptResult",
    "AcceptUnavailable",
    "accept_answer",
]

#: Edit roles the engine reconciles by re-translating the source (vs. the prose judge) —
#: mirrors the code branches of ``sync_apply._resolve_edit`` and ``sync_task._CODE_EDIT_ROLES``.
_CODE_EDIT_ROLES = frozenset({CODE_ROLE, LOCALIZED_CODE_ROLE})


class AcceptRejected(Exception):
    """The answer failed validation; nothing was written (carries the precise reason)."""


class AcceptUnavailable(Exception):
    """The selected item has no ``accept`` path yet (carries the next step)."""


@dataclass
class AcceptResult:
    """The outcome of accepting an answer for one item (always a *write* attempt).

    ``applied`` is True when the answer validated and the write-back ran (even a
    valid no-op map — the ids were already correct — counts as accepted). ``detail``
    is a one-line human summary; ``changed`` reports how many cells the write-back
    actually mutated (0 for a valid no-op).
    """

    item: str
    kind: str
    applied: bool
    changed: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "kind": self.kind,
            "applied": self.applied,
            "changed": self.changed,
            "detail": self.detail,
        }


def _find_item(plan: SyncPlan, item_id: str) -> ReconciliationItem:
    """The report item carrying ``item_id``, or raise :class:`KeyError`.

    Builds the report with excerpts (a read-only ``--dry-run`` over the plan) so the
    realign residue items are present and the item ids match ``task`` / ``report``
    exactly. The plan is not mutated.
    """
    report = build_report(plan, with_excerpts=True)
    by_id = {it.item: it for it in (*report.mechanical, *report.assisted, *report.ambiguity)}
    item = by_id.get(item_id)
    if item is None:
        raise KeyError(item_id)
    return item


def _matching_proposal(plan: SyncPlan, item: ReconciliationItem) -> Proposal:
    """The single plan :class:`Proposal` that produced ``item``, or raise.

    A report item is a verbatim projection of a proposal's identifying fields
    (:func:`clm.slides.sync_report._item_from_proposal`), so the proposal is the one
    whose ``(kind, role, slide_id, direction, source_position, target_position)``
    tuple equals the item's — effectively unique (slide_id or the position pair
    disambiguates). Raises :class:`AcceptRejected` on no / ambiguous match (the plan
    drifted from the report the agent read — re-run ``report``).
    """
    key = (
        item.kind,
        item.role,
        item.slide_id,
        item.direction,
        item.source_position,
        item.target_position,
    )
    matches = [
        p
        for p in plan.proposals
        if (p.kind, p.role, p.slide_id, p.direction, p.source_position, p.target_position) == key
    ]
    if len(matches) != 1:
        raise AcceptRejected(
            f"{item.item!r} no longer maps to exactly one plan proposal "
            f"({len(matches)} match) — the deck changed since `report`; re-run it."
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Single-answer stand-ins: return the agent's (already-validated) answer for the
# one item the pruned plan carries. They make the write-back path model-free.
# ---------------------------------------------------------------------------


@dataclass
class _SingleAnswerTranslator:
    """A :class:`~clm.slides.sync_translate.SlideTranslator` that returns one body.

    The accept plan is pruned to a single ``add`` / ``edit``, so the translator is asked
    for exactly that one cell's twin; it returns the agent's body.

    ``strict_single`` (the ``add`` accept) raises after the **first** call: an ``add``
    reuses the full apply, whose structural pass re-derives drift from the working tree —
    so a *second* translation request means a cell other than the one targeted needs
    translating (a co-drifted sibling the author also edited, or a fresh companion of the
    new slide). The agent supplied only one body, so returning it for that other cell would
    silently corrupt it; raising instead makes the structural pass record an error, the
    end-of-pass flush is skipped (nothing reaches disk), and ``accept`` rejects with the
    next step. The add's own cell is always the first call (``_materialize_idless`` runs and
    caches it before the structural pass), so a clean single-cell add never trips this.
    """

    body: str
    strict_single: bool = False
    prompt_version: str = "agent-accept"
    _calls: int = field(default=0, init=False)

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        if self.strict_single and self._calls >= 1:
            from clm.slides.sync_translate import TranslationError

            raise TranslationError(
                "this accept carries one translated body, but the apply needs another cell "
                "translated too — a co-drifted sibling the author also edited, or a companion "
                "of the new slide. Accept that other cell's item first (then re-run `report`), "
                "or use `clm slides sync autopilot`."
            )
        self._calls += 1
        return self.body


def _load_json_answer(answer: Any, *, what: str) -> dict[str, Any]:
    """Coerce a parsed answer to a JSON object, or raise :class:`AcceptRejected`."""
    if not isinstance(answer, dict):
        raise AcceptRejected(f"{what} answer must be a JSON object, got {type(answer).__name__}")
    return answer


def _reject_multicell_body(body: str, comment_token: str, *, what: str) -> None:
    """Reject an answer body that smuggles a cell delimiter (#442 review, CRITICAL).

    ``accept`` writes the agent's answer as the body of ONE cell. If that body carries
    a ``<token> %%`` boundary line it re-splits on read-back into extra cells — minting
    a phantom cell and, when the line names one, a **duplicate ``slide_id``** — silently
    corrupting the deck while ``accept`` reports success (only a separate ``verify`` would
    catch it). This is the "structural cell-shape" check the task contract promises: a
    body that opens a new cell is refused here, byte-unchanged, before any write. Uses the
    SAME boundary predicate the parser re-splits with, so it can never drift.
    """
    if any(is_cell_boundary(line, comment_token) for line in body.split("\n")):
        raise AcceptRejected(
            f"{what} answer must be the target cell's body only, but it contains a "
            f"'{comment_token} %%' cell delimiter that would split it into multiple cells "
            "on read-back (minting a phantom cell / duplicate slide_id). Return just the "
            "body, with no cell markers."
        )


# ---------------------------------------------------------------------------
# add — translate the new slide's twin and insert it (both halves), id minted.
# ---------------------------------------------------------------------------


def _accept_add(plan: SyncPlan, item: ReconciliationItem, answer: Any) -> AcceptResult:
    obj = _load_json_answer(answer, what="translation")
    body = obj.get("translated_body")
    if not isinstance(body, str) or not body.strip():
        raise AcceptRejected(
            "translation answer needs a non-empty 'translated_body' string "
            "(the target-language cell body in percent-format)."
        )
    _reject_multicell_body(body, comment_token_for_path(plan.de_path), what="translation")
    from clm.slides.sync_apply import apply_plan

    proposal = _matching_proposal(plan, item)
    # Prune to the single add: other pending proposals (incl. other adds, which are
    # not decision-gated) must not be touched, and a missing translation for them
    # would error the whole atomic write. anchor_direction is dropped so accept does
    # not also carry unrelated neutral propagation — `accept` writes just this item.
    # ``strict_single``: the add reuses the full apply (its structural pass places the new
    # slide), which re-derives drift from disk — so if a co-drifted sibling in a rebuilt
    # group would be re-translated with this one answer, the translator raises rather than
    # corrupting it, and the error-gated flush leaves the deck untouched.
    pruned = replace(plan, proposals=[proposal], anchor_direction=None)
    result = apply_plan(
        pruned,
        judge=None,
        translator=_SingleAnswerTranslator(body, strict_single=True),
        watermark_cache=None,
    )
    if result.errors:
        raise AcceptRejected("; ".join(result.errors))
    if result.applied_add == 0:
        # The translation validated but nothing was inserted — the plan no longer
        # carries this add as an applyable op (the deck moved under us).
        raise AcceptRejected(
            f"{item.item!r}: the add did not apply (the deck changed since `report`); "
            "re-run `clm slides sync report`."
        )
    return AcceptResult(
        item=item.item,
        kind=item.kind,
        applied=True,
        changed=result.applied_add,
        detail=f"translated and inserted the new slide on both halves ({result.applied_add} add).",
    )


# ---------------------------------------------------------------------------
# realign — re-identify the stuck drifted-id region from the agent's map.
# ---------------------------------------------------------------------------


def _effective_direction(plan: SyncPlan) -> str | None:
    """The pass's single propagation direction (mirrors ``sync_apply._effective_direction``).

    The id-migration recovery tier only fires when a propagation direction is
    active, so accept reconstructs it from the plan and pins it on the pruned plan
    (which carries no keyed proposals to supply it).
    """
    keyed = {p.direction for p in plan.proposals if p.direction in ("de->en", "en->de")}
    if len(keyed) == 1:
        return next(iter(keyed))
    return plan.anchor_direction


def _accept_realign(plan: SyncPlan, item: ReconciliationItem, answer: Any) -> AcceptResult:
    from clm.slides.sync_apply import apply_plan, idmigration_regions

    obj = _load_json_answer(answer, what="alignment")
    try:
        mapping = {int(k): str(v) for k, v in obj.items()}
    except (TypeError, ValueError) as exc:
        raise AcceptRejected(
            f"alignment answer must map each current-region index (a string) to an "
            f"assignment string: {exc}"
        ) from exc

    regions = idmigration_regions(plan)
    if regions is None:
        raise AcceptRejected(
            f"the drifted-id region for {item.slide_id!r} is no longer stuck "
            "(the deck changed since `report`); re-run `clm slides sync report`."
        )
    base_region, current_region = regions
    # Gate on the SAME validator the task named, up front, so the rejection reason is
    # precise (apply's recovery tier re-validates and would only safe-abort silently).
    try:
        validate_alignment(mapping, base_region, current_region)
    except AlignmentInvalid as exc:
        raise AcceptRejected(f"alignment map rejected: {exc}") from exc

    direction = _effective_direction(plan)
    if direction is None:
        raise AcceptRejected(
            f"{item.item!r}: no active propagation direction, so the region cannot be "
            "realigned in this state; re-run `clm slides sync report`."
        )
    # Prune to no proposals (the realign is residue, not a proposal) so no edit/add
    # is touched; pin the direction so the deterministic id-migration's recovery tier
    # activates and consumes the agent's map (via the static recoverer) on both decks.
    pruned = replace(plan, proposals=[], anchor_direction=direction)
    result = apply_plan(
        pruned,
        judge=None,
        recoverer=StaticAlignmentRecoverer(mapping=mapping),
        watermark_cache=None,
    )
    if result.errors:
        raise AcceptRejected("; ".join(result.errors))
    changed = result.applied_migrate
    detail = (
        f"realigned {changed} cell(s) across both halves."
        if changed
        else "the ids were already aligned (valid no-op); nothing written."
    )
    return AcceptResult(
        item=item.item, kind=item.kind, applied=True, changed=changed, detail=detail
    )


# ---------------------------------------------------------------------------
# edit — reconcile a drifted localized cell onto the target half.
# ---------------------------------------------------------------------------


def _accept_edit(plan: SyncPlan, item: ReconciliationItem, answer: Any) -> AcceptResult:
    """Validate an edit answer and write the reconciled body onto the target half.

    Covers every drifted localized cell — **keyed** (the cell carries a ``slide_id``),
    **narrative** (an id-less ``voiceover`` / ``notes`` companion, #403), and **id-less
    localized** (a ``lang=`` cell with no id, #365) — split exactly how the engine
    reconciles each: a **code** edit's answer is the re-translated body
    (``{translated_body}``, validator ``translation``); a **prose** (markdown / narrative)
    edit's answer is the judge verdict (``{verdict, proposed_text}``, validator ``edit``,
    with ``in_sync`` accepted as a no-op). The write reuses the engine's own edit path
    over a plan pruned to that one edit, with a static stand-in carrying the agent's
    answer — the code branch consults the translator, the prose branch the judge, so
    providing both keeps either path model-free.

    The pruned plan is applied in :func:`~clm.slides.sync_apply.apply_plan`'s
    ``scope_to_proposals`` mode: only the per-cell write runs, with no structural pass.
    That is what makes the id-less kinds safe to accept one at a time — the per-cell walk
    targets the narrative by its ``(owning_slide_id, role, occ)`` anchor and the id-less
    localized cell by its carried position, while skipping the structural pass keeps a
    *co-drifted* sibling in the same slide group from being re-translated with this one
    answer (which the single-answer stand-in would otherwise do).
    """
    from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
    from clm.slides.sync_apply import apply_plan

    proposal = _matching_proposal(plan, item)
    if item.role in _CODE_EDIT_ROLES:
        obj = _load_json_answer(answer, what="translation")
        translated = obj.get("translated_body")
        if not isinstance(translated, str) or not translated.strip():
            raise AcceptRejected(
                "translation answer needs a non-empty 'translated_body' string "
                "(the re-translated target-language code cell, runnable, no code fences)."
            )
        body, verdict, reason = translated, "update", ""
    else:
        obj = _load_json_answer(answer, what="edit")
        raw_verdict = obj.get("verdict")
        if raw_verdict not in ("update", "in_sync"):
            raise AcceptRejected(
                f"edit answer needs a 'verdict' of 'update' or 'in_sync' (got {raw_verdict!r})."
            )
        proposed = obj.get("proposed_text", "")
        if not isinstance(proposed, str):
            raise AcceptRejected("edit answer 'proposed_text' must be a string.")
        if raw_verdict == "update" and not proposed.strip():
            raise AcceptRejected(
                "an 'update' edit answer needs a non-empty 'proposed_text' "
                "(the reconciled target-language cell body)."
            )
        body, verdict, reason = proposed, str(raw_verdict), str(obj.get("reason", "") or "")

    # The reconciled body is written as a SINGLE cell's body; reject one that smuggles a
    # cell delimiter (#442 review). Only an ``update`` writes a body (``in_sync`` is a
    # no-op), so validate only then.
    if verdict == "update":
        _reject_multicell_body(body, comment_token_for_path(plan.de_path), what=item.kind)

    # Prune to just this edit (anchor_direction dropped so no unrelated neutral
    # propagation rides along) and reuse the engine's edit path. Both static stand-ins
    # carry the agent's body so whichever branch the engine takes returns it.
    judge = StaticSyncJudge(
        default_proposal=SyncProposal(verdict=verdict, proposed_text=body, reason=reason)
    )
    pruned = replace(plan, proposals=[proposal], anchor_direction=None)
    result = apply_plan(
        pruned,
        judge=judge,
        translator=_SingleAnswerTranslator(body),
        watermark_cache=None,
        scope_to_proposals=True,
    )
    if result.errors:
        raise AcceptRejected("; ".join(result.errors))
    if result.applied_edit:
        return AcceptResult(
            item=item.item,
            kind=item.kind,
            applied=True,
            changed=result.applied_edit,
            detail="reconciled the edit onto the target half.",
        )
    if result.in_sync:
        return AcceptResult(
            item=item.item,
            kind=item.kind,
            applied=True,
            changed=0,
            detail="the target already reflects the source (verdict in_sync); nothing written.",
        )
    raise AcceptRejected(
        f"{item.item!r}: the edit did not apply (the deck changed since `report`); "
        "re-run `clm slides sync report`."
    )


# ---------------------------------------------------------------------------
# mint / adopt — verify a cold-start pair's correspondence, then mint / stamp ids.
# ---------------------------------------------------------------------------


def _decode_verdicts(answer: Any) -> dict[int, bool]:
    """Coerce a correspondence answer to a ``{pair_index → bool}`` map, or raise.

    Mirrors :func:`clm.slides.sync_recover.decode_verdicts` (string keys → int, values
    must be real booleans) but reports a precise :class:`AcceptRejected` rather than the
    engine's safe-abort, so a malformed map is a clear rejection that writes nothing.
    """
    obj = _load_json_answer(answer, what="correspondence")
    verdicts: dict[int, bool] = {}
    for key, value in obj.items():
        try:
            idx = int(key)
        except (TypeError, ValueError) as exc:
            raise AcceptRejected(
                f"correspondence key {key!r} is not an integer pair index"
            ) from exc
        if not isinstance(value, bool):
            raise AcceptRejected(
                f"correspondence value for pair {key!r} must be a boolean (true/false)"
            )
        verdicts[idx] = value
    return verdicts


def _cold_decline_reason(item: ReconciliationItem, result: Any) -> str:
    """A precise reason a verified cold-start pair was NOT minted/adopted (no write)."""
    detail = result.cold_deferrals[0] if result.cold_deferrals else None
    if detail is not None and detail.reason == "rejected-pairs":
        idxs = ", ".join(str(p.index) for p in detail.rejected_pairs)
        verb = "minted" if item.kind == "mint" else "adopted"
        return (
            f"{item.item!r}: your verdicts judged pair(s) {idxs} non-corresponding, so no "
            f"shared ids were {verb} — the deck halves are misaligned there; re-pair them "
            "or fix the alignment, then re-run `clm slides sync report`."
        )
    reason = detail.reason if detail is not None else "no cold-start candidate"
    return (
        f"{item.item!r}: the cold-start {item.kind} did not apply ({reason}); the deck "
        "likely changed since `report` — re-run it."
    )


def _accept_cold(plan: SyncPlan, item: ReconciliationItem, answer: Any) -> AcceptResult:
    """Validate the agent's correspondence verdicts and apply the cold-start mint/adopt.

    The agent ran the ``correspondence`` task (the deck's aligned slide pairs) and
    returned a ``{pair_index → bool}`` map. Rebuild the SAME pairs the task showed, gate
    the map with :func:`~clm.slides.sync_recover.validate_correspondence` (it must cover
    every pair index exactly), then apply through the engine's own cold-start path with a
    :class:`~clm.slides.sync_recover.StaticCorrespondenceVerifier` carrying the verdicts —
    so the engine never calls a model. An **all-yes** map mints (mint) / stamps (adopt)
    the shared ids onto both halves; any **no** (or a race) declines and writes nothing,
    reported with the rejected pairs.
    """
    from clm.slides.sync_apply import apply_plan, cold_slide_pairs
    from clm.slides.sync_recover import (
        CorrespondenceInvalid,
        StaticCorrespondenceVerifier,
        validate_correspondence,
    )

    verdicts = _decode_verdicts(answer)
    pairs = cold_slide_pairs(plan)
    try:
        validate_correspondence(verdicts, pairs)
    except CorrespondenceInvalid as exc:
        raise AcceptRejected(f"correspondence verdicts rejected: {exc}") from exc

    # default=False so a hypothetical apply-side pair the agent did not answer defers
    # (never mints on an assumed yes); validate_correspondence already proved a total map.
    result = apply_plan(
        plan,
        judge=None,
        verifier=StaticCorrespondenceVerifier(verdicts=verdicts, default=False),
        watermark_cache=None,
    )
    if result.errors:
        raise AcceptRejected("; ".join(result.errors))
    changed = result.applied_mint + result.applied_adopt
    if changed:
        detail = (
            "verified correspondence; minted shared ids onto both halves."
            if item.kind == "mint"
            else "verified correspondence; stamped the authority half's ids onto its twin."
        )
        return AcceptResult(
            item=item.item, kind=item.kind, applied=True, changed=changed, detail=detail
        )
    raise AcceptRejected(_cold_decline_reason(item, result))


# ---------------------------------------------------------------------------
# reconcile (#228) — verify the mismatched-id-twin cross-product, rewrite the id.
# ---------------------------------------------------------------------------


def _accept_reconcile(plan: SyncPlan, item: ReconciliationItem, answer: Any) -> AcceptResult:
    """Validate the agent's cross-product verdicts and reconcile the mismatched-id twins.

    The agent ran the ``correspondence`` task over the DE×EN suspect cross-product (#228)
    and returned a ``{flat_index → bool}`` verdict map. Rebuild the SAME cross-product
    (:func:`~clm.slides.sync_apply.reconcile_pairs`), gate the map (it must cover every
    cross-product index), then apply through the engine's reconcile path with a
    :class:`~clm.slides.sync_recover.StaticCorrespondenceVerifier` carrying the verdicts and
    **no translator** — so an unambiguous mutual match has its divergent id rewritten
    (EN-authority), while a genuinely-distinct leftover is *deferred* (the engine never
    translates it; it re-surfaces as an ``add``). A bucket with no confirmed twin writes
    nothing and is reported with the next step.
    """
    from clm.slides.sync_apply import apply_plan, reconcile_pairs
    from clm.slides.sync_recover import (
        CorrespondenceInvalid,
        StaticCorrespondenceVerifier,
        validate_correspondence,
    )

    pairs = reconcile_pairs(plan)
    if not pairs:
        raise AcceptRejected(
            f"{item.item!r}: the reconcile bucket has no cross-product to verify (its "
            "suspects are one-sided) — handle them as adds; re-run `clm slides sync report`."
        )
    verdicts = _decode_verdicts(answer)
    try:
        validate_correspondence(verdicts, pairs)
    except CorrespondenceInvalid as exc:
        raise AcceptRejected(f"correspondence verdicts rejected: {exc}") from exc

    result = apply_plan(
        plan,
        judge=None,
        translator=None,
        verifier=StaticCorrespondenceVerifier(verdicts=verdicts, default=False),
        watermark_cache=None,
    )
    if result.errors:
        raise AcceptRejected("; ".join(result.errors))
    if result.applied_reconcile:
        detail = (
            f"verified correspondence; reconciled {result.applied_reconcile} mismatched-id "
            "twin(s) (EN-authority)."
        )
        if result.deferred:
            detail += (
                f" {result.deferred} leftover(s) deferred — re-run `clm slides sync report` "
                "to handle them as adds."
            )
        return AcceptResult(
            item=item.item,
            kind=item.kind,
            applied=True,
            changed=result.applied_reconcile,
            detail=detail,
        )
    raise AcceptRejected(
        f"{item.item!r}: no twins were reconciled (your verdicts found no unambiguous "
        "correspondence); the suspects are distinct slides — re-run `clm slides sync "
        "report` to handle them as adds."
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def accept_answer(
    plan: SyncPlan,
    item_id: str,
    answer: Any,
) -> AcceptResult:
    """Validate ``answer`` for item ``item_id`` and write it to both halves.

    Locates the item in a freshly-built report (so the ids match ``report`` /
    ``task``), dispatches by kind, validates the answer with the kind's deterministic
    validator, and applies it through :func:`~clm.slides.sync_apply.apply_plan` over a
    plan pruned to that one item — writing **both** halves only on success. Raises
    :class:`KeyError` for an unknown id, :class:`AcceptRejected` when validation fails
    (nothing is written), or :class:`AcceptUnavailable` when the kind has no accept
    path yet.
    """
    item = _find_item(plan, item_id)
    if item.kind == "add":
        return _accept_add(plan, item, answer)
    if item.kind == "realign":
        return _accept_realign(plan, item, answer)
    if item.kind == "edit":
        return _accept_edit(plan, item, answer)
    if item.kind in ("mint", "adopt"):
        return _accept_cold(plan, item, answer)
    if item.kind == "reconcile":
        return _accept_reconcile(plan, item, answer)
    raise AcceptUnavailable(
        f"{item.item!r} ({item.kind}) is an ambiguity for you to resolve by hand. Edit the "
        "deck to resolve it, then re-run `clm slides sync report` / `verify`."
    )
