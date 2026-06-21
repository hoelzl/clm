"""The tiered reconciliation report — `clm slides sync`'s agent contract.

A coding agent is both the *driver* of `clm slides sync` (it invokes the command
and holds the full deck source) and the *consumer* of its output. So the output
is tailored as an actionable, machine-readable plan rather than a human report:
``clm slides sync --dry-run --json`` carries a ``report`` block — this model —
that partitions the engine's work into the **three tiers** the agent acts on
differently (the design note in PR #422, §8 / §11 Q6):

* **mechanical** (tier 1) — the engine resolves these deterministically with *no
  model* (move / remove / retag / a verbatim neutral-cell propagation). The agent
  can trust-and-ignore them: a real (non-dry-run) sync applies them for free.
* **assisted** (tier 2) — a *scoped* model task the engine has already framed
  (which cell, which direction, what is needed): translate a new slide, reconcile
  an edit on an id'd localized cell, or confirm a cold-pair correspondence. The
  agent can let the engine's own LLM do it, delegate it to a *cheap* model, or do
  it itself.
* **ambiguity** (tier 3) — the engine *refuses* to guess (a both-sided conflict, a
  structural issue the classifier will not turn into a proposal). These need the
  strong agent's judgement; the report states *what* is ambiguous, never a
  fabricated resolution.

This is the difference between *agent-assisted* sync (the engine does the
unambiguous bulk, the agent spends attention only on the residue) and
*agent-does-everything* sync (every cell through a model — slow, costly,
non-reproducible). The report exists to shrink the agent's attention surface to
tiers 2–3.

**Scope of this version.** Each item carries its ``kind`` / ``role`` /
``direction`` / ``slide_id`` / ``reason`` and 0-based ``source_position`` /
``target_position`` (the indices among each language's sync-relevant cells) — so
the agent can locate the exact cell in the files it already holds. The cell
*text* is intentionally not inlined yet: mapping a proposal position back to its
source bytes is direction- and partition-dependent, and an *incorrect* excerpt
would mislead worse than none — a careful follow-up will add it. The
Pydantic model is the stable, versioned contract the MCP sync surface can share
(Q5) once it is migrated off its legacy engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

from clm.slides.sync_plan import PlanIssue, Proposal, SyncPlan

Tier = Literal["mechanical", "assisted", "ambiguity"]

#: Proposal kinds the engine executes deterministically (no model).
_MECHANICAL_KINDS = frozenset({"move", "remove", "retag"})
#: Proposal kinds that require a scoped model call (translation / edit
#: reconciliation / cold-pair correspondence) — tier 2.
_ASSISTED_KINDS = frozenset({"add", "edit", "rename", "mint", "adopt", "reconcile"})


class ReconciliationItem(BaseModel):
    """One cell-level unit of work, assigned to a tier."""

    tier: Tier
    kind: str
    role: str | None = None
    slide_id: str | None = None
    # ``"de->en"`` / ``"en->de"`` (the drifted side is the source), or ``None`` for
    # a conflict / an issue / an id mint.
    direction: str | None = None
    reason: str = ""
    # Set only on an ``ambiguity`` item derived from a ``PlanIssue`` ("error" /
    # "warning"); ``None`` for items derived from a proposal.
    severity: str | None = None
    # 0-based indices among the source / target language's sync-relevant cells, so
    # the agent can locate the cell in the file. ``None`` when the kind has no
    # positional anchor.
    source_position: int | None = None
    target_position: int | None = None


class ReconciliationReport(BaseModel):
    """The tiered plan a `clm slides sync --dry-run` produces for an agent."""

    de_path: str
    en_path: str
    # "watermark" | "git-head" | "git:<ref>" | "none" — what the drift was diffed
    # against (an agent should know whether a baseline existed).
    baseline_source: str
    in_sync: int
    mechanical: list[ReconciliationItem] = Field(default_factory=list)
    assisted: list[ReconciliationItem] = Field(default_factory=list)
    ambiguity: list[ReconciliationItem] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_clean(self) -> bool:
        """True iff there is no work in any tier (the pair is already in sync)."""
        return not (self.mechanical or self.assisted or self.ambiguity)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_agent(self) -> bool:
        """True iff a tier-3 item needs the *driving* agent's judgement."""
        return bool(self.ambiguity)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_model(self) -> bool:
        """True iff a model call is needed (a tier-2 task or tier-3 judgement)."""
        return bool(self.assisted or self.ambiguity)


def _proposal_tier(p: Proposal) -> Tier:
    """Assign a proposal to a tier from its disposition / kind / pending flag.

    A ``refuse`` (or a ``conflict``) is the engine declining to guess → tier 3. A
    pending content op or any kind in :data:`_ASSISTED_KINDS` needs a scoped model
    call → tier 2. A kind in :data:`_MECHANICAL_KINDS` the engine applies
    deterministically → tier 1. An **unrecognised** kind defaults to ``ambiguity``,
    not ``mechanical``: a future proposal kind must never be silently trusted as a
    no-model deterministic op — surface it to the agent until it is categorised.
    """
    if p.disposition == "refuse" or p.kind in ("conflict", "refuse"):
        return "ambiguity"
    if p.translation_pending or p.kind in _ASSISTED_KINDS:
        return "assisted"
    if p.kind in _MECHANICAL_KINDS:
        return "mechanical"
    return "ambiguity"


def _item_from_proposal(p: Proposal) -> ReconciliationItem:
    return ReconciliationItem(
        tier=_proposal_tier(p),
        kind=p.kind,
        role=p.role,
        slide_id=p.slide_id,
        direction=p.direction,
        reason=p.reason,
        source_position=p.source_position,
        target_position=p.target_position,
    )


def _item_from_issue(issue: PlanIssue) -> ReconciliationItem:
    return ReconciliationItem(
        tier="ambiguity",
        kind="issue",
        slide_id=issue.slide_id,
        reason=issue.reason,
        severity=issue.severity,
    )


def build_report(plan: SyncPlan) -> ReconciliationReport:
    """Project a :class:`SyncPlan` into the tiered agent contract.

    Every proposal is bucketed by :func:`_proposal_tier`; every
    :class:`PlanIssue` is a tier-3 ambiguity. A language-neutral propagation the
    engine will copy verbatim (``plan.anchor_direction``) is surfaced as a single
    mechanical item, so the report names *every* change the engine would make — not
    only the keyed proposals.
    """
    mechanical: list[ReconciliationItem] = []
    assisted: list[ReconciliationItem] = []
    ambiguity: list[ReconciliationItem] = []

    for p in plan.proposals:
        item = _item_from_proposal(p)
        {"mechanical": mechanical, "assisted": assisted, "ambiguity": ambiguity}[item.tier].append(
            item
        )

    ambiguity.extend(_item_from_issue(i) for i in plan.issues)

    if plan.anchor_direction is not None:
        mechanical.append(
            ReconciliationItem(
                tier="mechanical",
                kind="neutral-propagate",
                direction=plan.anchor_direction,
                reason=(
                    "a language-neutral cell changed on one half; the engine copies it "
                    "verbatim to the twin (no model)"
                ),
            )
        )

    return ReconciliationReport(
        de_path=str(plan.de_path),
        en_path=str(plan.en_path),
        baseline_source=plan.baseline_source,
        in_sync=plan.in_sync_count,
        mechanical=mechanical,
        assisted=assisted,
        ambiguity=ambiguity,
    )
