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

**Cell text (dry-run only).** Each item carries its ``kind`` / ``role`` /
``direction`` / ``slide_id`` / ``reason`` and 0-based ``source_position`` /
``target_position``. For an **assisted** or **ambiguity** item produced by a
``--dry-run`` — the only mode where the files still hold the cells the positions
index — the report additionally resolves those positions back to the concrete
cell **bytes**: ``source_lang`` / ``source_excerpt`` / ``source_line`` (the
drifted/winning side the agent reconciles *from*) and the matching ``target_*``
triple (the existing counterpart, for an edit/conflict), so a delegated model
gets the exact text without re-deriving the engine's two position schemes.
Resolution mirrors those schemes exactly — the *sync-relevant* index for keyed
proposals, the *non-j2 ``lang``* index for the id-less localized ones — and is
**fail-closed**: a position it cannot resolve with certainty (an unknown
direction, an out-of-range index, an unreadable file) yields no excerpt rather
than a *wrong* one, because a mis-located excerpt misleads a delegated model
worse than none. Excerpts are omitted outside ``--dry-run`` (after an apply the
files no longer match the plan's positions) and for **mechanical** items (the
agent applies those without reading them). The Pydantic model is the stable,
versioned contract the MCP sync surface can share (Q5) once it is migrated off
its legacy engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from clm.notebooks.slide_parser import Cell, comment_token_for_path, parse_cells
from clm.slides.sync_plan import (
    LOCALIZED_CODE_ROLE,
    LOCALIZED_MARKDOWN_ROLE,
    PlanIssue,
    Proposal,
    SyncPlan,
)
from clm.slides.sync_writeback import role_of

Tier = Literal["mechanical", "assisted", "ambiguity"]

#: Proposal kinds the engine executes deterministically (no model).
_MECHANICAL_KINDS = frozenset({"move", "remove", "retag"})
#: Proposal kinds that require a scoped model call (translation / edit
#: reconciliation / cold-pair correspondence) — tier 2.
_ASSISTED_KINDS = frozenset({"add", "edit", "rename", "mint", "adopt", "reconcile"})

#: Proposal roles whose ``source_position`` / ``target_position`` index a language's
#: **non-j2 cells** (the engine's "localized" scheme) rather than its sync-relevant
#: cells (the default "sync" scheme). These four roles appear *only* on the id-less
#: localized membership proposals (edit / conflict / retag), so the role alone is a
#: sound discriminator between the two schemes.
_LOCALIZED_POSITION_ROLES = frozenset(
    {LOCALIZED_CODE_ROLE, LOCALIZED_MARKDOWN_ROLE, "code", "markdown"}
)


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
    # Cell-text enrichment, populated only for an assisted / ambiguity item produced
    # by ``--dry-run`` (see the module docstring). ``source_*`` is the drifted/winning
    # side the agent reconciles *from*; ``target_*`` is the existing counterpart (an
    # edit / conflict). Each is ``None`` when the side does not exist (e.g. an ``add``
    # has no target yet) or the position could not be resolved with certainty.
    source_lang: str | None = None
    target_lang: str | None = None
    source_excerpt: str | None = None
    source_line: int | None = None
    target_excerpt: str | None = None
    target_line: int | None = None


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


class _SourceIndex:
    """Resolve a proposal's ``source_position`` / ``target_position`` to cell bytes.

    Mirrors the engine's two position schemes exactly:

    * **sync** — the index among a language's *sync-relevant* cells (``role_of`` is
      not ``None``), as :func:`sync_plan.ordered_sync_cells` assigns. Used by every
      keyed proposal (add / edit / move / remove / rename / retag / narrative).
    * **localized** — the index among a language's *non-j2* cells, as
      :func:`sync_plan._localized_lang_cells` assigns. Used by the id-less localized
      membership proposals, whose positions are non-j2 ``lang`` indices.

    Built from the working-tree files (each read + parsed once) — sound **only**
    while they still hold the cells the plan's positions index, i.e. a ``--dry-run``
    before any apply. An out-of-range position resolves to ``(None, None)`` rather
    than the wrong cell.
    """

    def __init__(self, de_path: Path, en_path: Path) -> None:
        self._sync: dict[str, list[Cell]] = {}
        self._localized: dict[str, list[Cell]] = {}
        for lang, path in (("de", de_path), ("en", en_path)):
            cells = parse_cells(path.read_text(encoding="utf-8"), comment_token_for_path(path))
            self._sync[lang] = [
                c for c in cells if role_of(c.metadata) is not None and c.metadata.lang == lang
            ]
            self._localized[lang] = [
                c for c in cells if not c.metadata.is_j2 and c.metadata.lang == lang
            ]

    def resolve(
        self, lang: str | None, scheme: str, position: int | None
    ) -> tuple[str | None, int | None]:
        """The ``(body, 1-based line)`` of the cell at ``position``, or ``(None, None)``."""
        if lang is None or position is None or position < 0:
            return None, None
        cells = (self._localized if scheme == "localized" else self._sync).get(lang, [])
        if position >= len(cells):
            return None, None
        cell = cells[position]
        return cell.content, cell.line_number


def _scheme_for(role: str | None) -> str:
    """``"localized"`` if ``role`` indexes non-j2 ``lang`` cells, else ``"sync"``."""
    return "localized" if role in _LOCALIZED_POSITION_ROLES else "sync"


def _item_languages(item: ReconciliationItem) -> tuple[str | None, str | None]:
    """The ``(source, target)`` languages a positional item refers to, or ``(None, None)``.

    A ``de->en`` / ``en->de`` direction names them outright (the drifted side is the
    source). The id-less localized **both-sided conflict** carries no direction, but
    its producer (``_classify_idless_localized_conflicts``) always records the DE
    index in ``source_position`` and the EN index in ``target_position`` — so it
    resolves as DE→EN. Every other directionless item (a keyed conflict / mint /
    issue) has no positional source/target and stays unresolved.
    """
    if item.direction in ("de->en", "en->de"):
        source, target = item.direction.split("->")
        return source, target
    if (
        item.kind == "conflict"
        and _scheme_for(item.role) == "localized"
        and item.source_position is not None
        and item.target_position is not None
    ):
        return "de", "en"
    return None, None


def _enrich(item: ReconciliationItem, index: _SourceIndex) -> None:
    """Resolve an item's positions to concrete cell bytes (mutates ``item`` in place)."""
    source_lang, target_lang = _item_languages(item)
    scheme = _scheme_for(item.role)
    if source_lang is not None:
        item.source_lang = source_lang
        item.source_excerpt, item.source_line = index.resolve(
            source_lang, scheme, item.source_position
        )
    if target_lang is not None:
        item.target_lang = target_lang
        item.target_excerpt, item.target_line = index.resolve(
            target_lang, scheme, item.target_position
        )


def _enrich_report(report: ReconciliationReport, de_path: Path, en_path: Path) -> None:
    """Attach cell bytes to every assisted / ambiguity item (best-effort, in place).

    Mechanical items are skipped — the agent applies them without reading the cell.
    A file that cannot be read or parsed leaves the whole report un-enriched: the
    tiers stay valid; only the excerpts are absent.
    """
    try:
        index = _SourceIndex(de_path, en_path)
    except (OSError, ValueError):  # unreadable / undecodable file — degrade to no excerpts
        return
    for item in (*report.assisted, *report.ambiguity):
        _enrich(item, index)


def build_report(plan: SyncPlan, *, with_excerpts: bool = False) -> ReconciliationReport:
    """Project a :class:`SyncPlan` into the tiered agent contract.

    Every proposal is bucketed by :func:`_proposal_tier`; every
    :class:`PlanIssue` is a tier-3 ambiguity. A language-neutral propagation the
    engine will copy verbatim (``plan.anchor_direction``) is surfaced as a single
    mechanical item, so the report names *every* change the engine would make — not
    only the keyed proposals.

    ``with_excerpts`` resolves each assisted / ambiguity item's positions back to the
    concrete cell bytes (see :func:`_enrich_report`). The caller must pass it **only**
    for a ``--dry-run`` plan — the resolver reads the working-tree files, which match
    the plan's positions only before an apply mutates them.
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

    report = ReconciliationReport(
        de_path=str(plan.de_path),
        en_path=str(plan.en_path),
        baseline_source=plan.baseline_source,
        in_sync=plan.in_sync_count,
        mechanical=mechanical,
        assisted=assisted,
        ambiguity=ambiguity,
    )
    if with_excerpts:
        _enrich_report(report, plan.de_path, plan.en_path)
    return report
