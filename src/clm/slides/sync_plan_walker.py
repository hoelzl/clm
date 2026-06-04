"""Interactive review walker over a :class:`~clm.slides.sync_plan.SyncPlan`.

Phase 4 (part 2) of Issue #166. The Phase 1 classifier emits a typed plan of
``add`` / ``edit`` / ``move`` / ``remove`` / ``rename`` / ``conflict``
proposals; this module renders each one and lets the author decide what to do
before the apply engine writes anything:

- **edit / remove / move** — ``[a]pply`` / ``[s]kip`` / ``[q]uit``. Skipping
  defers the proposal (the watermark will not advance over it, so it re-surfaces
  next run rather than being silently baselined).
- **conflict** (the same ``slide_id`` drifted on both decks since the last sync)
  — a two-up of the current DE and EN bodies plus
  ``[d]e-wins`` / ``[e]n-wins`` / ``[s]kip``. A winner is propagated as an
  ordinary edit; skip leaves both decks untouched and lists the conflict.
- **add / rename** — non-destructive, so they are auto-applied (translate +
  insert + mint id). They are still rendered so the author sees them; the
  generated counterpart is reviewed in the resulting ``git diff`` (the design's
  primary review surface).

The walker collects per-proposal decisions during the walk and then calls
:func:`~clm.slides.sync_apply.apply_plan` **once** (the apply is atomic and
flushes each deck a single time). It performs no writes itself.

``prompt_fn`` and ``echo`` are injected so the test suite can drive the walker
with a scripted list of answers and capture output without a real terminal.
The live ``clm slides sync`` wiring is Phase 5; this module is tested in
isolation against a mocked judge / translator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import click

from clm.slides.sync_apply import (
    DECISION_APPLY,
    DECISION_DE_WINS,
    DECISION_EN_WINS,
    DECISION_SKIP,
    ApplyResult,
    apply_plan,
    content_index,
)

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import (
        SyncAlignmentCache,
        SyncCorrespondenceCache,
        SyncWatermarkCache,
    )
    from clm.infrastructure.llm.ollama_client import SyncJudge
    from clm.slides.sync_plan import Proposal, SyncPlan
    from clm.slides.sync_recover import AlignmentRecoverer, CorrespondenceVerifier
    from clm.slides.sync_translate import SlideTranslator


__all__ = [
    "APPLY",
    "AUTO",
    "DE_WINS",
    "EN_WINS",
    "QUIT",
    "REFUSE",
    "SKIP",
    "PlanWalkResult",
    "WalkerAction",
    "WalkerOptions",
    "render_proposal",
    "run_plan_walker",
]


# Walker action keys — bare strings so a test can scrub the action log trivially.
APPLY = "apply"
SKIP = "skip"
QUIT = "quit"
DE_WINS = "de-wins"
EN_WINS = "en-wins"
AUTO = "auto"  # add (id-less or id-carrying) / rename, applied without prompting
REFUSE = "refuse"  # a structural refusal (#216): shown, never prompted, deferred

_GATED_KINDS = {"edit", "retag", "remove", "move"}
_AUTO_KINDS = {"add", "rename"}


@dataclass
class WalkerAction:
    """One decision the walker recorded for a proposal (telemetry / tests)."""

    kind: str
    slide_id: str | None
    role: str
    direction: str | None
    action: str  # APPLY / SKIP / QUIT / DE_WINS / EN_WINS / AUTO


@dataclass
class WalkerOptions:
    """Knobs for one walker pass.

    ``prompt_fn`` returns the raw answer for one prompt (the walker reads its
    first character); ``echo`` receives each line of output. Defaults dispatch
    to :func:`click.prompt` / :func:`click.echo`.
    """

    prompt_fn: Callable[[str], str] | None = None
    echo: Callable[[str], None] | None = None


@dataclass
class PlanWalkResult:
    """Outcome of an interactive walk: the apply result plus the decision log."""

    plan: SyncPlan
    apply_result: ApplyResult
    actions: list[WalkerAction] = field(default_factory=list)

    def _count(self, *, kinds: set[str] | None = None, action: str | None = None) -> int:
        return sum(
            1
            for a in self.actions
            if (kinds is None or a.kind in kinds) and (action is None or a.action == action)
        )

    @property
    def accepted(self) -> int:
        """Gated proposals the author accepted (edit / remove / move).

        A *decision* count, not an outcome — an accepted edit can still error
        (e.g. its target cell is missing). Actual writes are in
        ``apply_result.applied_*``; the two summary lines keep the distinction.
        """
        return self._count(action=APPLY)

    @property
    def conflicts_resolved(self) -> int:
        return self._count(action=DE_WINS) + self._count(action=EN_WINS)

    @property
    def skipped(self) -> int:
        """Gated proposals + conflicts the author skipped."""
        return self._count(action=SKIP)

    @property
    def auto_applied(self) -> int:
        """add / rename proposals routed for auto-apply (id-less or id-carrying).

        Reflects what was *routed*; what was actually written is
        ``apply_result.applied_add + apply_result.applied_rename``.
        """
        return self._count(action=AUTO)

    @property
    def refused(self) -> int:
        """Structural refusals shown but not acted on (#216) — deferred by design."""
        return self._count(action=REFUSE)

    @property
    def unvisited(self) -> int:
        """Proposals not reached because the author quit the walk."""
        return self._count(action=QUIT)

    @property
    def exit_code(self) -> int:
        """0 = clean, 1 = needs review (deferred), 2 = structural / LLM error.

        Mirrors the legacy ``clm slides sync`` buckets: a structural plan issue
        or an apply-time error (LLM unavailable, residual duplicate, cross-deck
        orphan) is exit 2; anything merely deferred (a skipped proposal or an
        unresolved conflict) is exit 1.
        """
        if self.plan.has_errors or self.apply_result.has_errors:
            return 2
        if self.apply_result.deferred > 0:
            return 1
        return 0

    def summary(self) -> list[str]:
        """Headline lines for the end-of-run report (Phase 5 prints these).

        Two lines that never conflate intent with outcome: the first reports the
        author's *decisions* on the gated kinds, the second reports what the
        engine actually *did* (including auto-applied adds/renames, deferrals,
        and errors).
        """
        r = self.apply_result
        return [
            f"walker decisions: {self.accepted} accepted, "
            f"{self.conflicts_resolved} conflict(s) resolved, "
            f"{self.skipped} skipped, "
            f"{self.refused} refused, "
            f"{self.unvisited} unvisited (quit).",
            f"applied: {r.applied_edit} edit, {r.applied_retag} retag, "
            f"{r.applied_remove} remove, "
            f"{r.applied_move} move, {r.applied_add} add, {r.applied_rename} rename; "
            f"{r.in_sync} already in sync; {r.deferred} deferred; "
            f"{len(r.errors)} error(s); "
            f"watermark {'advanced' if r.watermark_recorded else 'held'}.",
        ]


def run_plan_walker(
    plan: SyncPlan,
    *,
    judge: SyncJudge | None,
    translator: SlideTranslator | None = None,
    watermark_cache: SyncWatermarkCache | None = None,
    options: WalkerOptions | None = None,
    recoverer: AlignmentRecoverer | None = None,
    alignment_cache: SyncAlignmentCache | None = None,
    verifier: CorrespondenceVerifier | None = None,
    correspondence_cache: SyncCorrespondenceCache | None = None,
) -> PlanWalkResult:
    """Walk ``plan``'s proposals, prompt per proposal, then apply once.

    Returns a :class:`PlanWalkResult` carrying the apply outcome and the
    per-proposal decision log. The apply (and therefore every file write) runs
    once, after the whole walk, so quitting mid-walk simply defers the
    not-yet-decided gated proposals.
    """
    options = options or WalkerOptions()
    prompt_fn = options.prompt_fn or _default_prompt
    echo = options.echo or click.echo

    de_bodies = content_index(plan.de_path, "de")
    en_bodies = content_index(plan.en_path, "en")

    decisions: dict[int, str] = {}
    actions: list[WalkerAction] = []
    quitting = False

    for proposal in plan.proposals:
        kind = proposal.kind

        if kind in _AUTO_KINDS:
            echo(render_proposal(proposal, de_bodies, en_bodies))
            # add (id-less or id-carrying) and rename are non-destructive, so
            # they auto-apply (translate + insert; the counterpart is reviewed in
            # the resulting git diff). An id-carrying add inserts the twin under
            # the existing id; an id-less add mints one.
            echo("  → will auto-apply (add/rename; counterpart reviewed in git diff)")
            actions.append(_action(proposal, AUTO))
            continue

        if kind == "refuse":
            # A structural refusal the resolver decided at plan time (#216): there
            # is no decision to make — show it and let the apply engine defer it
            # (the watermark holds, exit code is "needs review"). Never prompted.
            echo(render_proposal(proposal, de_bodies, en_bodies))
            echo("  → refused (structural; sync one direction at a time)")
            actions.append(_action(proposal, REFUSE))
            continue

        if kind in ("mint", "adopt"):
            # A cold-start bootstrap candidate (#216 §12): correspondence is verified
            # in apply (2b), not here — show it and let the engine mint/adopt or
            # downgrade to refuse. Never prompted (the author reviews the resulting
            # ids in git diff). `mint` creates fresh shared ids for a both-id-less
            # pair; `adopt` stamps the id'd half's existing ids onto its id-less twin.
            echo(render_proposal(proposal, de_bodies, en_bodies))
            verb = "mint shared" if kind == "mint" else "adopt the id'd half's"
            echo(f"  → pending correspondence verification (will {verb} ids if confirmed)")
            actions.append(_action(proposal, AUTO))
            continue

        if quitting:
            decisions[id(proposal)] = DECISION_SKIP
            actions.append(_action(proposal, QUIT))
            continue

        echo(render_proposal(proposal, de_bodies, en_bodies))

        if kind == "conflict":
            choice = _prompt_conflict(prompt_fn, echo)
        else:  # edit / remove / move
            choice = _prompt_gated(prompt_fn, echo)

        if choice == QUIT:
            quitting = True
            decisions[id(proposal)] = DECISION_SKIP
            actions.append(_action(proposal, QUIT))
            continue

        decisions[id(proposal)] = _DECISION_FOR_CHOICE[choice]
        actions.append(_action(proposal, choice))
        echo(f"  {choice} {_label(proposal)}")

    apply_result = apply_plan(
        plan,
        judge=judge,
        translator=translator,
        watermark_cache=watermark_cache,
        decisions=decisions,
        recoverer=recoverer,
        alignment_cache=alignment_cache,
        verifier=verifier,
        correspondence_cache=correspondence_cache,
    )
    return PlanWalkResult(plan=plan, apply_result=apply_result, actions=actions)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_proposal(
    proposal: Proposal,
    de_bodies: dict[tuple[str, str], str],
    en_bodies: dict[tuple[str, str], str],
) -> str:
    """Render one proposal as a human-readable review block.

    ``edit`` and ``conflict`` get a two-up of the current DE and EN bodies so
    the author can judge the change / pick a winner; the deterministic single
    structural kinds get a one-line description (their effect is reviewed in the
    resulting ``git diff``).
    """
    lines = [_header(proposal)]
    if proposal.reason:
        lines.append(f"  reason: {proposal.reason}")

    if proposal.kind in ("edit", "conflict") and proposal.slide_id is not None:
        key = (proposal.slide_id, proposal.role)
        lines.extend(_two_up(de_bodies.get(key, ""), en_bodies.get(key, "")))
    return "\n".join(lines)


def _header(proposal: Proposal) -> str:
    sid = proposal.slide_id if proposal.slide_id is not None else "(id-less)"
    direction = f" {proposal.direction}" if proposal.direction else ""
    pending = " [translation pending]" if proposal.translation_pending else ""
    label = "CONFLICT" if proposal.kind == "conflict" else proposal.kind
    return f"{label}{direction} {sid}/{proposal.role}{pending}"


def _two_up(de_body: str, en_body: str) -> list[str]:
    out = ["  --- DE (current) ---"]
    out.extend(f"  {line}" for line in de_body.splitlines() or [""])
    out.append("  --- EN (current) ---")
    out.extend(f"  {line}" for line in en_body.splitlines() or [""])
    return out


def _label(proposal: Proposal) -> str:
    sid = proposal.slide_id if proposal.slide_id is not None else "(id-less)"
    return f"{sid}/{proposal.role}"


def _action(proposal: Proposal, action: str) -> WalkerAction:
    return WalkerAction(
        kind=proposal.kind,
        slide_id=proposal.slide_id,
        role=proposal.role,
        direction=proposal.direction,
        action=action,
    )


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------

# Map a (non-quit) walk choice to the apply-engine decision it produces.
_DECISION_FOR_CHOICE = {
    APPLY: DECISION_APPLY,
    SKIP: DECISION_SKIP,
    DE_WINS: DECISION_DE_WINS,
    EN_WINS: DECISION_EN_WINS,
}


def _default_prompt(message: str) -> str:
    answer: str = click.prompt(message, default="s", show_default=True)
    return answer


def _prompt_gated(prompt_fn: Callable[[str], str], echo: Callable[[str], None]) -> str:
    """Prompt until the author picks apply / skip / quit (empty = skip)."""
    while True:
        first = (prompt_fn("[a]pply / [s]kip / [q]uit") or "").strip().lower()[:1]
        if first == "a":
            return APPLY
        if first in ("s", ""):
            return SKIP
        if first == "q":
            return QUIT
        echo("unknown choice; type a / s / q")


def _prompt_conflict(prompt_fn: Callable[[str], str], echo: Callable[[str], None]) -> str:
    """Prompt until the author picks de-wins / en-wins / skip / quit."""
    while True:
        first = (prompt_fn("[d]e-wins / [e]n-wins / [s]kip / [q]uit") or "").strip().lower()[:1]
        if first == "d":
            return DE_WINS
        if first == "e":
            return EN_WINS
        if first in ("s", ""):
            return SKIP
        if first == "q":
            return QUIT
        echo("unknown choice; type d / e / s / q")
