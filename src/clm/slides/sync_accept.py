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

Edit reconciliation (``edit``), cold-start correspondence (``mint`` / ``adopt`` /
``reconcile``) and the hand-judged ambiguities (``conflict`` / ``issue``) are not
accepted yet — :func:`accept_answer` raises :class:`AcceptUnavailable` with the
right next step (``edit`` because its answer shape differs for code vs. markdown
cells, which also wants a ``task``-framing refinement; the cold-start kinds because
they mint identity, which ``autopilot`` / ``assign-ids`` still own).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from clm.slides.sync_recover import (
    AlignmentInvalid,
    StaticAlignmentRecoverer,
    validate_alignment,
)
from clm.slides.sync_report import build_report

if TYPE_CHECKING:
    from clm.slides.sync_plan import Proposal, SyncPlan
    from clm.slides.sync_report import ReconciliationItem

__all__ = [
    "AcceptRejected",
    "AcceptResult",
    "AcceptUnavailable",
    "accept_answer",
]


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

    The accept plan is pruned to a single ``add``, so the translator is asked for
    exactly that slide's twin; it returns the agent's body for any call (no
    key-matching to drift on).
    """

    body: str
    prompt_version: str = "agent-accept"

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        return self.body


def _load_json_answer(answer: Any, *, what: str) -> dict[str, Any]:
    """Coerce a parsed answer to a JSON object, or raise :class:`AcceptRejected`."""
    if not isinstance(answer, dict):
        raise AcceptRejected(f"{what} answer must be a JSON object, got {type(answer).__name__}")
    return answer


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
    from clm.slides.sync_apply import apply_plan

    proposal = _matching_proposal(plan, item)
    # Prune to the single add: other pending proposals (incl. other adds, which are
    # not decision-gated) must not be touched, and a missing translation for them
    # would error the whole atomic write. anchor_direction is dropped so accept does
    # not also carry unrelated neutral propagation — `accept` writes just this item.
    pruned = replace(plan, proposals=[proposal], anchor_direction=None)
    result = apply_plan(
        pruned,
        judge=None,
        translator=_SingleAnswerTranslator(body),
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
# Dispatch
# ---------------------------------------------------------------------------

_COLD_START_KINDS = frozenset({"mint", "adopt", "reconcile"})


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
        raise AcceptUnavailable(
            f"{item.item!r} is an edit reconciliation; `accept` does not write edits yet "
            "(a code-cell edit and a markdown edit take different answer shapes). Apply it "
            "with `clm slides sync autopilot` (needs a key), or hand-edit the twin and run "
            "`clm slides sync verify`."
        )
    if item.kind in _COLD_START_KINDS:
        raise AcceptUnavailable(
            f"{item.item!r} is a cold-start correspondence ({item.kind}); accepting it mints "
            "shared identity, which `clm slides sync autopilot` (needs a key) or "
            "`clm slides assign-ids` still own."
        )
    raise AcceptUnavailable(
        f"{item.item!r} ({item.kind}) is an ambiguity for you to resolve by hand. Edit the "
        "deck to resolve it, then re-run `clm slides sync report` / `verify`."
    )
