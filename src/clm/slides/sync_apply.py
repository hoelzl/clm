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
- **add** / **conflict** — *not applied here*. Adds need translation + id minting
  (Phase 3); conflicts are isolated by design. Counted as ``deferred`` so
  nothing is silent.

Atomicity: each proposal is all-or-nothing for its target cell; the two decks
are flushed once at the end. The **watermark advances only on a complete,
clean apply** (no deferred proposals, no errors) — so un-applied work can
never be silently baked into the baseline and lost.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from clm.infrastructure.llm.ollama_client import OllamaError
from clm.notebooks.slide_parser import parse_cells
from clm.slides.raw_cells import RawCell
from clm.slides.sync_plan import Proposal, SyncPlan, ordered_sync_cells
from clm.slides.sync_writeback import FileState, role_of

if TYPE_CHECKING:
    from pathlib import Path

    from clm.infrastructure.llm.cache import SyncWatermarkCache
    from clm.infrastructure.llm.ollama_client import SyncJudge

logger = logging.getLogger(__name__)

__all__ = ["ApplyResult", "apply_plan"]


@dataclass
class ApplyResult:
    """Outcome of applying a :class:`SyncPlan`."""

    applied_edit: int = 0
    applied_remove: int = 0
    applied_move: int = 0
    in_sync: int = 0  # an edit the judge decided needed no change
    deferred: int = 0  # add / conflict (or moves declined this pass)
    watermark_recorded: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return self.applied_edit + self.applied_remove + self.applied_move

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def apply_plan(
    plan: SyncPlan,
    *,
    judge: SyncJudge | None,
    watermark_cache: SyncWatermarkCache | None = None,
) -> ApplyResult:
    """Apply the remove/edit proposals in ``plan`` and advance the watermark.

    ``judge`` provides the target-side rewrite for ``edit`` proposals; pass
    ``None`` to skip edits (each is recorded as an error). Applies remove / edit
    / move; add and conflict are counted as ``deferred``. The two decks are
    flushed once; the watermark is recorded only when the plan applied cleanly
    and completely.
    """
    result = ApplyResult()

    # Pre-apply content (parser-stripped) for the judge, keyed by (slide_id,
    # role). Read before FileState mutates anything.
    de_content = _content_index(plan.de_path, "de")
    en_content = _content_index(plan.en_path, "en")

    de_state = FileState.load(plan.de_path)
    en_state = FileState.load(plan.en_path)

    moves: list[Proposal] = []
    for proposal in plan.proposals:
        kind = proposal.kind
        if kind == "remove":
            _apply_remove(proposal, de_state, en_state, result)
        elif kind == "edit":
            _apply_edit(proposal, de_state, en_state, de_content, en_content, judge, result)
        elif kind == "move":
            moves.append(proposal)
        else:
            # add / conflict — out of scope for this engine.
            result.deferred += 1

    # Moves are applied last, and only if the rest of the pass is clean (so the
    # watermark will advance and the reorder stays idempotent).
    if moves:
        _apply_moves(moves, de_state, en_state, result, plan)

    de_state.flush()
    en_state.flush()

    # Advance the watermark only when everything we were asked to do was done.
    # Deferred moves/adds mean the decks are not yet fully in sync, so baking
    # the current state into the baseline would silently lose that work.
    if watermark_cache is not None and _pass_is_clean(plan, result):
        _record_watermark(watermark_cache, plan.de_path, plan.en_path)
        result.watermark_recorded = True

    return result


def _pass_is_clean(plan: SyncPlan, result: ApplyResult) -> bool:
    """Whether the pass fully reconciled both decks against a real baseline.

    The single predicate shared by the move gate and the watermark-advance
    decision so the two can never drift apart: a real baseline, no
    classifier errors, no apply-time errors, and nothing deferred.
    """
    return plan.has_baseline and not plan.has_errors and not result.errors and result.deferred == 0


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
    result: ApplyResult,
) -> None:
    if proposal.slide_id is None:
        result.errors.append(f"edit {proposal.role}: proposal has no slide_id")
        return
    key = (proposal.slide_id, proposal.role)
    if proposal.direction == "de->en":
        source_lang, target_lang = "de", "en"
        source_body = de_content.get(key, "")
        target_body = en_content.get(key, "")
        target_state = en_state
    else:
        source_lang, target_lang = "en", "de"
        source_body = en_content.get(key, "")
        target_body = de_content.get(key, "")
        target_state = de_state

    if judge is None:
        result.errors.append(
            f"edit {proposal.slide_id}/{proposal.role}: no judge (LLM unavailable)"
        )
        return

    try:
        sync_proposal = judge.propose(
            source_body, target_body, source_lang=source_lang, target_lang=target_lang
        )
    except OllamaError as exc:
        logger.info("edit judge failed on %s/%s: %s", proposal.slide_id, proposal.role, exc)
        result.errors.append(f"edit {proposal.slide_id}/{proposal.role}: {exc}")
        return

    if sync_proposal.verdict != "update":
        result.in_sync += 1
        return

    if target_state.replace_cell_body(
        proposal.slide_id, proposal.role, sync_proposal.proposed_text
    ):
        result.applied_edit += 1
    else:
        result.errors.append(f"edit {proposal.slide_id}/{proposal.role}: target cell not found")


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
            _heal_terminal_newline(reordered, target_state)
            target_state.cells = reordered
            target_state.dirty = True
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


def _heal_terminal_newline(new_cells: list[RawCell], target: FileState) -> None:
    """Drop the terminal-newline artifact off a reordered ex-last cell.

    ``split_cells`` parks the file's final newline as a trailing ``""`` on the
    last cell. After a reorder that cell may no longer be last, where the
    ``""`` would render as a spurious mid-file blank line. Strip that one
    ``""`` (genuine trailing blanks are preserved); :meth:`FileState.flush`
    restores the terminal newline.
    """
    if not target.ends_with_newline or not new_cells or not target.cells:
        return
    original_last = target.cells[-1]
    if new_cells[-1] is not original_last and original_last.lines and original_last.lines[-1] == "":
        original_last.lines.pop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_index(path: Path, lang: str) -> dict[tuple[str, str], str]:
    """Map ``(slide_id, role) -> parser-stripped content`` for one deck.

    Filtered to ``lang`` so the apply-side lookup matches exactly what the
    Phase 1 classifier (``ordered_sync_cells``) extracted — the same
    role+language predicate, so the judge can never be fed an
    other-language cell that happens to share a key.
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


def _record_watermark(
    cache: SyncWatermarkCache,
    de_path: Path,
    en_path: Path,
) -> None:
    """Record both decks' post-apply state as the new baseline."""
    for lang, path in (("de", de_path), ("en", en_path)):
        cells = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8")), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash) for c in cells],
        )
