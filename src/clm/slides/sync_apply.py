"""Apply engine for the single-language authoring workflow.

Phase 2 of Issue #166. Consumes the typed :class:`~clm.slides.sync_plan.SyncPlan`
produced by the Phase 1 classifier and writes the agreed changes to the target
deck(s), then advances the structural watermark so the next run is a no-op.

Scope of this engine:

- **remove** — delete the target-side cell whose source-side counterpart the
  author removed (deterministic, no LLM).
- **edit** — ask the existing :class:`SyncJudge` to propose the target-side
  rewrite for a cell that drifted on the source side, then write it.
- **move** / **add** / **conflict** — *not applied here*. Moves need slide-group
  reordering (Phase 2b); adds need translation + id minting (Phase 3); conflicts
  are isolated by design. They are counted as ``deferred`` so nothing is silent.

Atomicity: each proposal is all-or-nothing for its target cell; the two decks
are flushed once at the end. The **watermark advances only on a complete,
clean apply** (no deferred proposals, no errors) — so an un-applied move can
never be silently baked into the baseline and lost.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from clm.infrastructure.llm.ollama_client import OllamaError
from clm.notebooks.slide_parser import parse_cells
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
    in_sync: int = 0  # an edit the judge decided needed no change
    deferred: int = 0  # move / add / conflict — not handled by this engine
    watermark_recorded: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return self.applied_edit + self.applied_remove

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
    ``None`` to skip edits (each is recorded as an error). Writes nothing for
    move/add/conflict proposals. The two decks are flushed once; the watermark
    is recorded only when the plan applied cleanly and completely.
    """
    result = ApplyResult()

    # Pre-apply content (parser-stripped) for the judge, keyed by (slide_id,
    # role). Read before FileState mutates anything.
    de_content = _content_index(plan.de_path, "de")
    en_content = _content_index(plan.en_path, "en")

    de_state = FileState.load(plan.de_path)
    en_state = FileState.load(plan.en_path)

    for proposal in plan.proposals:
        kind = proposal.kind
        if kind == "remove":
            _apply_remove(proposal, de_state, en_state, result)
        elif kind == "edit":
            _apply_edit(proposal, de_state, en_state, de_content, en_content, judge, result)
        else:
            # move / add / conflict — out of scope for this engine.
            result.deferred += 1

    de_state.flush()
    en_state.flush()

    # Advance the watermark only when everything we were asked to do was done.
    # Deferred moves/adds mean the decks are not yet fully in sync, so baking
    # the current state into the baseline would silently lose that work.
    if (
        watermark_cache is not None
        and plan.has_baseline
        and result.deferred == 0
        and not result.errors
        and not plan.has_errors
    ):
        _record_watermark(watermark_cache, plan.de_path, plan.en_path)
        result.watermark_recorded = True

    return result


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
