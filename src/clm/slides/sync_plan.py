"""Structural change classifier for the single-language authoring workflow.

Phase 1 of Issue #166. After an author edits one half of a split deck
(``<deck>.de.py`` / ``<deck>.en.py``), this module diffs both decks against a
**structural watermark** (the last synced state) and produces a typed *plan*
of cross-language proposals: ``add`` / ``edit`` / ``move`` / ``remove`` /
``conflict``. It assigns a **per-cell direction** (which side drifted) and
isolates true conflicts (the same ``slide_id`` drifted on both sides).

This is pure analysis — **no files are written and no LLM is called**. The
``add`` proposals are emitted as *translation-pending*; later phases fill in
the translated counterpart and mint the ``slide_id``. See
``docs/claude/design/single-language-authoring-sync.md`` for the full design.

Baseline resolution (``build_sync_plan``):

1. the **watermark** (:class:`clm.infrastructure.llm.cache.SyncWatermarkCache`)
   when present — written only on a successful sync apply, so immune to the
   author's git-commit cadence;
2. else **git HEAD** of each deck (covers a fresh clone with no local cache);
3. else **no baseline** — only id-less adds and shared-id pairing can be
   inferred; a shared id that differs across decks needs an explicit
   ``--source-lang`` and is surfaced as an issue rather than guessed.

Key invariant from the design: after a sync, every sync-relevant cell carries a
``slide_id``. So a cell with **no** ``slide_id`` is, by construction, *added
since the last sync* — a git-immune signal that survives committing the deck.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from clm.notebooks.slide_parser import Cell, CellMetadata, parse_cells
from clm.slides.sync_writeback import (
    cell_content_hash,
    construct_of,
    role_of,
    row_anchor,
)

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncWatermarkCache

__all__ = [
    "MEMBERSHIP_ROLES",
    "AnchorAlignment",
    "BaselineCell",
    "CurrentCell",
    "PlanIssue",
    "Proposal",
    "SyncPlan",
    "TagHold",
    "align_anchored",
    "build_sync_plan",
    "classify_changes",
    "ordered_sync_cells",
    "render_explain",
    "render_plan",
    "watermark_rows",
    "watermark_tag_map",
]

# Roles that lead a slide group; narrative roles (voiceover/notes/code/aux)
# belong to the most recent slide group rather than starting their own.
_SLIDE_ROLES = {"slide", "subslide"}

# Synthetic roles for the *membership-widened* rows (Issue #190 §5.3): cells the
# per-cell engine does not own (``role_of`` is ``None``) but which the watermark
# now records so the anchor pass can locate them. The classifier filters these
# out (:func:`_baseline_from_watermark`), so move detection / pairing is
# unchanged; only the Phase 2+ anchor reuse reads them.
NEUTRAL_CODE_ROLE = "neutral-code"
NEUTRAL_MARKDOWN_ROLE = "neutral-markdown"
LOCALIZED_CODE_ROLE = "localized-code"
LOCALIZED_MARKDOWN_ROLE = "localized-markdown"
MEMBERSHIP_ROLES = frozenset(
    {NEUTRAL_CODE_ROLE, NEUTRAL_MARKDOWN_ROLE, LOCALIZED_CODE_ROLE, LOCALIZED_MARKDOWN_ROLE}
)


def _membership_role(metadata: CellMetadata) -> str:
    """The synthetic membership role for a non-j2 cell with no per-cell role."""
    is_code = metadata.cell_type == "code"
    if metadata.lang is None:
        return NEUTRAL_CODE_ROLE if is_code else NEUTRAL_MARKDOWN_ROLE
    return LOCALIZED_CODE_ROLE if is_code else LOCALIZED_MARKDOWN_ROLE


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineCell:
    """One cell as recorded in the watermark (or derived from git HEAD)."""

    position: int
    slide_id: str | None
    role: str
    content_hash: str
    # Tag set at the last sync (Issue #198). ``None`` means "undeterminable" — a
    # pre-#198 watermark row that recorded no tags — so the classifier never
    # guesses a tag direction from it. An empty frozenset is a *known* no-tags cell.
    tags: frozenset[str] | None = None


@dataclass(frozen=True)
class CurrentCell:
    """One sync-relevant cell as it exists in the working tree right now."""

    position: int  # index among sync-relevant cells of this deck's language
    slide_id: str | None
    role: str
    content_hash: str
    line_number: int  # 1-based header line, for anchoring / messaging
    construct: str | None = None  # AST construct slug (Issue #190 §4); None for non-code
    tags: frozenset[str] = frozenset()  # current tag set (Issue #198)


@dataclass
class Proposal:
    """One cross-language change the sync would make.

    ``kind`` is ``add`` / ``edit`` / ``retag`` / ``move`` / ``remove`` /
    ``conflict`` / ``rename`` / ``refuse`` / ``mint`` / ``adopt``. ``direction``
    is ``"de->en"`` / ``"en->de"`` (the side that drifted is the source), or
    ``None`` for a conflict / ``mint``. ``slide_id`` is ``None`` for an id-less
    add, and the *duplicated* id for a ``rename``. A ``retag`` mirrors a tag-only
    edit (the content hash is unchanged) onto the other half (Issue #198).
    Positions are 0-based indices among sync-relevant cells and are best-effort
    context for later phases (anchoring, walker rendering).

    ``mint`` and ``adopt`` are the two cold-start id-bootstrap candidates (#216
    §12; ``disposition == "pending"``): a ``mint`` stands for a both-id-less
    cold pair whose halves get a *fresh* shared ``slide_id`` per slide; an
    ``adopt`` stands for a *half-id'd* cold pair (one half fully id'd, the other
    fully id-less) where the id-less half **adopts** the id'd half's *existing*
    ids — its ``direction`` is ``"{authority}->{other}"`` (the id-bearing side
    first). Both are confirmed by the apply-time correspondence verifier before
    any id reaches disk; an unconfirmed candidate downgrades to a deferral.

    ``disposition`` is the resolved verdict the classifier assigns (the
    resolve-then-apply redesign, #216): ``"apply"`` (the default — apply executes
    a concrete mechanical op) or ``"refuse"`` (a structural decision *not* to act,
    surfaced in the plan and held at the baseline). A ``refuse`` proposal carries
    ``kind == "refuse"`` and ``disposition == "refuse"`` so it renders in the plan,
    drives the dry-run exit code, and is deferred — never applied — by the engine.
    Moving this decision to plan time is what makes ``--dry-run`` predict the
    writing run instead of diverging from it (the apply engine used to re-decide
    a both-directions refusal that the plan never recorded). Later phases add
    ``"pending"`` / ``"conflict"`` dispositions to apply-kind items.

    ``content_hash`` is set on a ``rename`` proposal: it identifies which of the
    duplicate-id cells is the copy (the apply re-mints the cell matching this
    hash, leaving the original alone).

    ``tags`` is set on an **id-less localized** ``retag`` (Issue #198 Tier C):
    such a cell has no ``slide_id``, so the apply cannot find its twin by
    ``(slide_id, role)`` and instead targets the ``target_position``-th non-j2
    cell of the target language and writes exactly this tag set onto it. ``None``
    for an id-carrying ``retag`` (whose tags are read from the matched source
    cell) and for every other kind.
    """

    kind: str
    role: str
    direction: str | None
    slide_id: str | None
    reason: str = ""
    translation_pending: bool = False  # True for ``add`` / ``rename`` (content not yet made)
    source_position: int | None = None
    target_position: int | None = None
    old_position: int | None = None
    new_position: int | None = None
    content_hash: str | None = None  # the copy's hash, for ``rename``
    tags: tuple[str, ...] | None = None  # desired tag set for an id-less localized ``retag``
    disposition: str = "apply"  # "apply" | "refuse" — the resolved verdict (#216)


@dataclass(frozen=True)
class TagHold:
    """A tag-only both-decks conflict to pin at its old baseline (Issue #202).

    A both-sides tag drift (the ``both`` branch of :func:`_retag_direction`) never
    touches a cell body, so the body baseline is safe to advance for every cell
    while *this one cell's tags* are held at the old baseline value — so the
    conflict re-surfaces next run instead of being silently baselined, yet a
    co-applied clean edit still banks. Identifies the held cell the same two ways
    the retag paths do: ``(slide_id, role)`` for an id-carrying cell
    (:func:`_maybe_retag`, #200) or ``position`` — the watermark partition index,
    identical on both halves under stream alignment — for an id-less localized cell
    (:func:`_classify_localized_idless_retags`, #201). Exactly one identity is set.
    """

    slide_id: str | None = None
    role: str | None = None
    position: int | None = None


@dataclass
class PlanIssue:
    """A structural situation the classifier will not turn into a proposal."""

    severity: str  # "warning" | "error"
    slide_id: str | None
    reason: str
    # Issue #202: set only on a *tag-only* both-decks conflict, which is scoped to
    # one cell's tags and touches no body. A warning carrying a ``tag_hold`` no
    # longer holds the whole-deck watermark: the partial advance banks everything
    # else and pins just this cell's tags at the old baseline. ``None`` for every
    # structural warning (reorder, ambiguous de/en state, shared-cell auto-heal),
    # which must keep holding the whole watermark.
    tag_hold: TagHold | None = None


@dataclass
class SyncPlan:
    """The full result of classifying one split pair against its baseline."""

    de_path: Path
    en_path: Path
    baseline_source: str  # "watermark" | "git-head" | "none"
    proposals: list[Proposal] = field(default_factory=list)
    issues: list[PlanIssue] = field(default_factory=list)
    in_sync_count: int = 0
    # Direction a code-only (language-neutral) change must propagate when the
    # keyed classifier found none — the Issue #190 item-2 signal. ``None`` when
    # no non-keyed cell drifted. Consumed by the structural pass (sync_code).
    anchor_direction: str | None = None

    @property
    def has_baseline(self) -> bool:
        return self.baseline_source != "none"

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def blocking_issues(self) -> list[PlanIssue]:
        """Issues that hold the whole-deck watermark (Issue #202).

        Every issue *except* a tag-only both-decks conflict (one carrying a
        :class:`TagHold`): errors, both-decks reorders, ambiguous de/en states,
        and the shared-cell auto-heal warning all concern structure that cannot be
        partially advanced safely, so any of them holds the whole watermark.
        """
        return [i for i in self.issues if i.tag_hold is None]

    @property
    def tag_holds(self) -> list[TagHold]:
        """The per-cell tag-only conflicts safe to hold while the rest advances (#202)."""
        return [i.tag_hold for i in self.issues if i.tag_hold is not None]

    @property
    def refusals(self) -> list[Proposal]:
        """Structural ``refuse`` items (#216): decisions NOT to act, shown in the plan.

        A refusal is resolved at plan time (the both-directions cold-start /
        id-less case), so the dry-run preview lists it and a writing run defers
        it — never silently doubling a deck the way the old apply-time guard
        could be bypassed for id-carrying adds.
        """
        return [p for p in self.proposals if p.kind == "refuse"]

    @property
    def is_noop(self) -> bool:
        """True when there is nothing to apply (and nothing went wrong).

        A neutral code-only edit (item 2) produces no proposal but sets
        ``anchor_direction``, so it is *not* a no-op even with an empty plan.
        """
        return not self.proposals and not self.has_errors and self.anchor_direction is None

    def count(self, kind: str) -> int:
        return sum(1 for p in self.proposals if p.kind == kind)

    def summary(self) -> str:
        """One-line, no-silent-no-op headline.

        Distinguishes *"0 changes — already consistent"* from *"could not
        establish a baseline"* so a quiet run is never ambiguous.
        """
        if self.proposals or self.has_errors:
            parts = [
                f"{self.count('add')} add",
                f"{self.count('edit')} edit",
                f"{self.count('retag')} retag",
                f"{self.count('move')} move",
                f"{self.count('remove')} remove",
                f"{self.count('conflict')} conflict",
                f"{self.count('refuse')} refuse",
                f"{self.count('mint')} mint",
                f"{self.count('adopt')} adopt",
            ]
            tail = f"; {len(self.issues)} issue(s)" if self.issues else ""
            return (
                f"baseline={self.baseline_source}: "
                + ", ".join(parts)
                + f"; {self.in_sync_count} in sync"
                + tail
            )
        if not self.has_baseline:
            return (
                "baseline=none: no watermark and no git HEAD to diff against — "
                "cannot detect edits/removes. Pass --source-lang or commit a "
                "baseline. (id-less adds are still detected.)"
            )
        return (
            f"baseline={self.baseline_source}: 0 changes — decks already "
            f"consistent ({self.in_sync_count} cell(s) in sync)."
        )


# ---------------------------------------------------------------------------
# Cell extraction
# ---------------------------------------------------------------------------


def _role_for_cell(cell: Cell) -> str | None:
    """Per-cell sync role of ``cell`` (delegates to the canonical predicate).

    Kept as a thin wrapper so the classifier and the apply engine can never
    disagree about which cells participate in per-cell reconciliation.
    """
    return role_of(cell.metadata)


def ordered_sync_cells(cells: list[Cell], expected_lang: str) -> list[CurrentCell]:
    """Return sync-relevant cells of ``expected_lang`` in source order.

    ``position`` is the 0-based index among the returned cells, so it is
    stable for a fixed file and shifts predictably when cells are added or
    removed (move detection uses relative order, not absolute position).
    """
    out: list[CurrentCell] = []
    position = 0
    for cell in cells:
        role = _role_for_cell(cell)
        if role is None:
            continue
        if cell.metadata.lang != expected_lang:
            continue
        out.append(
            CurrentCell(
                position=position,
                slide_id=cell.metadata.slide_id or None,
                role=role,
                content_hash=cell_content_hash(cell.content),
                line_number=cell.line_number,
                construct=construct_of(cell.metadata, cell.content),
                tags=frozenset(cell.metadata.tags),
            )
        )
        position += 1
    return out


def watermark_rows(
    cells: list[Cell],
) -> dict[str, list[tuple[int, str | None, str, str, str | None]]]:
    """Every non-j2 cell as watermark 5-tuples, partitioned by ``de``/``en``/``shared``.

    Membership widening (Issue #190 §5.3): the watermark records *every* non-j2
    cell, not just the per-cell-synced ones. A cell with a real role
    (``role_of != None``) keeps it; a membership-only cell gets a synthetic role
    (:data:`MEMBERSHIP_ROLES`). The partition is the cell's language — ``shared``
    for language-neutral cells, which the single-entity model tracks once.
    Positions are per-partition file order; only their *relative* order is load-
    bearing (the classifier sorts by it), so interleaved membership rows do not
    perturb the legacy view once they are filtered out
    (:func:`_baseline_from_watermark`).
    """
    out: dict[str, list[tuple[int, str | None, str, str, str | None]]] = {
        "de": [],
        "en": [],
        "shared": [],
    }
    pos = {"de": 0, "en": 0, "shared": 0}
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2:
            continue
        real = role_of(meta)
        role = real if real is not None else _membership_role(meta)
        partition = meta.lang if meta.lang in ("de", "en") else "shared"
        out[partition].append(
            (
                pos[partition],
                meta.slide_id,
                role,
                cell_content_hash(cell.content),
                construct_of(meta, cell.content),
            )
        )
        pos[partition] += 1
    return out


def watermark_tag_map(cells: list[Cell]) -> dict[str, dict[int, frozenset[str]]]:
    """Per-cell tag sets, partitioned and positioned exactly like :func:`watermark_rows`.

    Issue #198: the watermark records each non-j2 cell's tag set (keyed by the
    same per-partition position ``watermark_rows`` assigns) so a later sync can
    detect a tag-only edit — invisible to the content hash — and mirror it across
    the split halves. Kept beside ``watermark_rows`` and iterating identically so
    the two never drift out of position lock-step.
    """
    out: dict[str, dict[int, frozenset[str]]] = {"de": {}, "en": {}, "shared": {}}
    pos = {"de": 0, "en": 0, "shared": 0}
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2:
            continue
        partition = meta.lang if meta.lang in ("de", "en") else "shared"
        out[partition][pos[partition]] = frozenset(meta.tags)
        pos[partition] += 1
    return out


def _shared_hashes(cells: list[Cell]) -> list[str]:
    """Ordered content hashes of the language-neutral (``shared``) cells of a file.

    An **ordered sequence**, deliberately *not* an ``anchor -> hash`` map: a
    construct anchor is only a name (``extract_from_code``), so it is not
    content-unique — two neutral cells sharing one (two ``import os``, two
    ``def solution``, two ``print(...)``) would collapse last-writer-wins in a map
    and hide a one-sided edit to the non-last one, silently reintroducing the very
    item-2 drop this detects (Issue #190 review). The ``shared`` partition is
    intrinsically ordered (``watermark_rows`` records positions) and byte-identical
    across halves under ``unify``, so an ordered-hash compare is simultaneously the
    unify check (de == en) and the drift check (current vs baseline), with no
    anchor-uniqueness assumption — mirroring the Counter / unique-match guards the
    Phase 2 anchor paths already apply.
    """
    return [chash for (_pos, _sid, _role, chash, _construct) in watermark_rows(cells)["shared"]]


@dataclass(frozen=True)
class AnchorAlignment:
    """How the language-neutral cells drifted, and what the sync should do.

    - ``direction`` set, ``diverged``/``irreconcilable`` false → a clean one-sided
      drift to propagate.
    - ``diverged`` → a *same-cell* §7a conflict (both decks edited the same neutral
      cell differently); apply the §7a winner policy. ``direction``, if set, is the
      only **safe** healing direction (the other side also has independent edits to
      preserve); ``None`` means either direction is safe → use winner-selection.
    - ``irreconcilable`` → the two decks edited **different** neutral cells, so no
      single propagation direction can reconcile them without reverting one →
      surface an error, never auto-heal (Issue #190 Phase 3c review).
    """

    direction: str | None
    diverged: bool = False
    irreconcilable: bool = False


def align_anchored(
    de_cells: list[Cell],
    en_cells: list[Cell],
    baseline_shared: list[str],
) -> AnchorAlignment:
    """Detect a code-only (language-neutral) change the keyed classifier missed.

    Issue #190 item 2 (Phase 3a/3c). A neutral shared cell is byte-identical across
    the split halves (the ``unify`` invariant), and the keyed engine never sees
    it — so an author editing one half alone yields no proposal and no direction,
    and the change is silently dropped.

    First gate on whether the halves even **disagree**: if every neutral cell is
    byte-identical across de and en, ``unify`` holds and there is nothing to
    propagate, *whatever the baseline says*. This keeps the pass robust to a
    watermark with no recorded ``shared`` partition (a deck with no neutral cells,
    or a pre-Phase-1b baseline), which must not be mistaken for a divergence.

    When the halves disagree, classify **per cell** (positionally) against the
    baseline — a whole-file verdict would conflate "de edited cell A, en edited a
    *different* cell B" (two compatible edits) with a real conflict, and then
    auto-healing one direction would silently revert the other's edit (the Phase 3c
    review's data-loss finding). A *loser-only* drift (a cell one half changed and
    the other left at baseline) cannot be overwritten safely; if both halves have
    one, no single direction is safe → irreconcilable.
    """
    de_shared = _shared_hashes(de_cells)
    en_shared = _shared_hashes(en_cells)
    if de_shared == en_shared:
        return AnchorAlignment(direction=None)

    if not baseline_shared:
        # The halves disagree but no ``shared`` baseline was recorded (a
        # pre-Phase-1b watermark, or a deck synced before membership widening).
        # We cannot tell which half drifted, so defer entirely to the keyed
        # direction rather than inventing a divergence/error. A later clean sync
        # records the partition, after which analysis proceeds normally.
        return AnchorAlignment(direction=None)

    # Positional classification needs the three sequences aligned; a length change
    # means a neutral cell was added/removed. Then a one-sided structural change is
    # a clean direction, but a both-sided one cannot be positionally reconciled.
    if not (len(de_shared) == len(en_shared) == len(baseline_shared)):
        de_drifted = de_shared != baseline_shared
        en_drifted = en_shared != baseline_shared
        if de_drifted and not en_drifted:
            return AnchorAlignment(direction="de->en")
        if en_drifted and not de_drifted:
            return AnchorAlignment(direction="en->de")
        return AnchorAlignment(direction=None, irreconcilable=True)

    de_only = en_only = conflict = False
    for d, e, b in zip(de_shared, en_shared, baseline_shared, strict=True):
        if d == e:
            continue
        if d != b and e != b:
            conflict = True  # same cell, both edited differently — the §7a case
        elif d != b:
            de_only = True  # de edited a cell en left at baseline
        else:
            en_only = True  # en edited a cell de left at baseline

    if de_only and en_only:
        # Independent edits to DIFFERENT neutral cells — a single direction would
        # revert one. Refuse to guess.
        return AnchorAlignment(direction=None, irreconcilable=True)
    if de_only:
        # de carries the only loser-safe edits; de->en is the one safe direction.
        return AnchorAlignment(direction="de->en", diverged=conflict)
    if en_only:
        return AnchorAlignment(direction="en->de", diverged=conflict)
    # Only same-cell conflicts: either direction is §7a-safe → winner-selection.
    return AnchorAlignment(direction=None, diverged=True)


# ``CLM_SYNC__SHARED_DIVERGENCE`` (the §7a knob): how to handle a language-neutral
# cell edited differently on both decks. ``auto-heal`` propagates the winning side
# with a warning; ``error`` surfaces it and writes nothing.
_SHARED_DIVERGENCE_ENV = "CLM_SYNC__SHARED_DIVERGENCE"


def _shared_divergence_mode() -> str:
    """The ``sync.shared_divergence`` mode: ``auto-heal`` (default) or ``error``."""
    value = os.environ.get(_SHARED_DIVERGENCE_ENV, "auto-heal").strip().lower()
    if value not in ("auto-heal", "error"):
        logger.warning(
            "%s=%r is invalid (expected 'auto-heal' or 'error'); using 'auto-heal'",
            _SHARED_DIVERGENCE_ENV,
            value,
        )
        return "auto-heal"
    return value


def _keyed_direction(plan: SyncPlan) -> str | None:
    """The single keyed propagation direction of the plan, or ``None``."""
    directions = {p.direction for p in plan.proposals if p.direction in ("de->en", "en->de")}
    return next(iter(directions)) if len(directions) == 1 else None


def _resolve_divergence_winner(plan: SyncPlan, de_path: Path, en_path: Path) -> str | None:
    """Pick the winning direction for a diverged shared cell (§7a), or ``None``.

    Precedence: (i) the run's established keyed edit direction (the deck the author
    touched this session — the common case); else (ii) the newer-mtime file as a
    tiebreak; else (iii) ``None`` — no signal, cannot heal, treat as an error even
    in auto-heal mode.
    """
    keyed = _keyed_direction(plan)
    if keyed is not None:
        return keyed
    try:
        de_mtime = de_path.stat().st_mtime
        en_mtime = en_path.stat().st_mtime
    except OSError:
        return None
    if de_mtime > en_mtime:
        return "de->en"
    if en_mtime > de_mtime:
        return "en->de"
    return None


def _baseline_from_watermark(
    rows: list[tuple[int, str | None, str, str, str | None]],
    tags_by_position: dict[int, frozenset[str]] | None = None,
) -> list[BaselineCell]:
    # Drop the membership-widened rows (Issue #190 §5.3) and **re-index** the
    # survivors into the legacy-only position space. The stored positions count
    # *all* non-j2 cells of the partition, but the classifier compares baseline
    # positions against ``ordered_sync_cells`` positions, which count only the
    # real-role cells. Most consumers read position through a sort (relative
    # order), but ``_resolve_duplicates`` compares it by *absolute* difference —
    # so the spaces must match. The rows are in file order, so ``enumerate``
    # reproduces exactly the indices ``ordered_sync_cells`` (and
    # ``_baseline_from_git_head``) assign. ``construct`` is carried in the raw
    # watermark for the Phase 2 anchor reuse but is not consumed by the classifier.
    # ``tags_by_position`` (Issue #198) maps the *stored* position to the recorded
    # tag set; ``None`` for a row absent from it (a pre-#198 watermark) leaves the
    # cell's baseline tags undeterminable so no tag direction is ever guessed.
    tbp = tags_by_position or {}
    legacy = [
        (pos, sid, role, chash)
        for (pos, sid, role, chash, _construct) in rows
        if role not in MEMBERSHIP_ROLES
    ]
    return [
        BaselineCell(position=i, slide_id=sid, role=role, content_hash=chash, tags=tbp.get(pos))
        for i, (pos, sid, role, chash) in enumerate(legacy)
    ]


def _lang_for_path(path: Path) -> str | None:
    name = path.name
    if name.endswith(".de.py"):
        return "de"
    if name.endswith(".en.py"):
        return "en"
    return None


def _baseline_from_git_head(path: Path) -> list[BaselineCell] | None:
    """Derive a baseline from the committed (HEAD) version of ``path``.

    Returns ``None`` when git is unavailable, the file is untracked, or the
    deck's language cannot be inferred from its name. An empty list means the
    file existed at HEAD but had no sync-relevant cells.
    """
    lang = _lang_for_path(path)
    if lang is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "show", f"HEAD:./{path.name}"],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode != 0:
        return None
    cells = parse_cells(completed.stdout)
    return [
        BaselineCell(
            position=c.position,
            slide_id=c.slide_id,
            role=c.role,
            content_hash=c.content_hash,
            tags=c.tags,
        )
        for c in ordered_sync_cells(cells, lang)
    ]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _index_by_key(
    cells: list[CurrentCell],
) -> tuple[dict[tuple[str, str], list[CurrentCell]], list[CurrentCell]]:
    """Index id-carrying cells by ``(slide_id, role)``, keeping duplicates.

    Returns ``(by_key, idless)`` where ``by_key`` maps each key to *all* cells
    carrying it (a list of length > 1 is a duplicate-id collision, resolved by
    :func:`_resolve_duplicates`).
    """
    by_key: dict[tuple[str, str], list[CurrentCell]] = {}
    idless: list[CurrentCell] = []
    for cell in cells:
        if cell.slide_id is None:
            idless.append(cell)
            continue
        by_key.setdefault((cell.slide_id, cell.role), []).append(cell)
    return by_key, idless


def _slide_groups(cells: list[CurrentCell]) -> list[list[CurrentCell]]:
    """Split ordered cells into slide groups (slide + trailing companions).

    Each group's first cell is a slide/subslide; the rest are its narrative
    companions. A companion that precedes any slide forms its own one-cell
    group (an orphan, never a copied-group member).
    """
    groups: list[list[CurrentCell]] = []
    current: list[CurrentCell] | None = None
    for cell in cells:
        if cell.role in _SLIDE_ROLES:
            current = [cell]
            groups.append(current)
        elif current is not None:
            current.append(cell)
        else:
            groups.append([cell])  # orphan companion before the first slide
            current = None
    return groups


def _resolve_duplicates(
    current: list[CurrentCell],
    by_key: dict[tuple[str, str], list[CurrentCell]],
    base_index: dict[tuple[str, str], BaselineCell],
    direction: str,
    plan: SyncPlan,
) -> tuple[dict[tuple[str, str], CurrentCell], set[tuple[str, str]]]:
    """Collapse duplicate-id cells to one original each, renaming the copies.

    Copy-pasting a slide produces two slide *groups* sharing an id (the slide
    cell and its narrative companions). Resolution is done at the **group**
    level: when the watermark identifies the original group (its slide matches
    the baseline), the other groups are copies — a single ``rename`` for the
    copy's slide cell re-mints the whole group (the apply re-binds the
    companions). When the original cannot be identified, or a duplicate is *not*
    explained by a copied slide group (e.g. a lone duplicated companion), it is
    surfaced as an error and left for manual resolution — never guessed.
    """
    excluded: set[int] = set()  # id() of copy-group cells sharing the slide's id
    errored_ids: set[str] = set()

    groups_by_slide_id: dict[str, list[list[CurrentCell]]] = {}
    for group in _slide_groups(current):
        head = group[0]
        if head.role in _SLIDE_ROLES and head.slide_id is not None:
            groups_by_slide_id.setdefault(head.slide_id, []).append(group)

    for sid, group_list in groups_by_slide_id.items():
        if len(group_list) < 2:
            continue
        slide_role = group_list[0][0].role
        base = base_index.get((sid, slide_role))
        candidates = [
            g for g in group_list if base is not None and g[0].content_hash == base.content_hash
        ]
        if base is None or not candidates:
            plan.issues.append(
                PlanIssue(
                    severity="error",
                    slide_id=sid,
                    reason=f"slide_id appears {len(group_list)}x and the original cannot be "
                    "identified (no baseline match) — resolve the duplicate manually",
                )
            )
            errored_ids.add(sid)
            for group in group_list:
                # Only the slide cells carry the duplicated id here; drop them
                # from pairing (surfaced as an error). Companions with a
                # different id stay and are paired/added normally.
                excluded.update(id(c) for c in group if c.slide_id == sid)
            continue
        original = min(candidates, key=lambda g: abs(g[0].position - base.position))
        for group in group_list:
            if group is original:
                continue
            slide_cell = group[0]
            plan.proposals.append(
                Proposal(
                    kind="rename",
                    role=slide_cell.role,
                    direction=direction,
                    slide_id=sid,
                    content_hash=slide_cell.content_hash,
                    translation_pending=True,
                    source_position=slide_cell.position,
                    reason="copy-pasted duplicate slide group — re-minted as a new slide",
                )
            )
            # Exclude only the cells that actually carry the duplicated id (the
            # slide and its same-id companions). A companion whose id differs is
            # not part of this group's identity — leave it to normal pairing so
            # it can't become a silent cross-deck orphan.
            excluded.update(id(c) for c in group if c.slide_id == sid)

    singular: dict[tuple[str, str], CurrentCell] = {}
    for key, cells in by_key.items():
        survivors = [c for c in cells if id(c) not in excluded]
        if not survivors:
            continue
        if len(survivors) == 1:
            singular[key] = survivors[0]
        elif key[0] not in errored_ids:
            sid, role = key
            plan.issues.append(
                PlanIssue(
                    severity="error",
                    slide_id=sid,
                    reason=f"role={role!r} slide_id appears {len(survivors)}x without a copied "
                    "slide group to explain it (a lone duplicated companion?) — resolve manually",
                )
            )
    # Keys that had cells but produced no original (errored) must be dropped
    # from the diff universe, else they re-enter as phantom `remove`s.
    error_keys = set(by_key) - set(singular)
    return singular, error_keys


def _baseline_index(
    baseline: list[BaselineCell] | None,
) -> dict[tuple[str, str], BaselineCell]:
    if baseline is None:
        return {}
    out: dict[tuple[str, str], BaselineCell] = {}
    for cell in baseline:
        if cell.slide_id is None:
            continue
        out.setdefault((cell.slide_id, cell.role), cell)
    return out


def _state(now: CurrentCell | None, base: BaselineCell | None) -> str:
    """One side's status for a key: same / edited / added / removed / absent."""
    if now is not None and base is not None:
        return "same" if now.content_hash == base.content_hash else "edited"
    if now is not None:
        return "added"
    if base is not None:
        return "removed"
    return "absent"


def _lcs_complement(base_order: list, now_order: list) -> set:
    """Items NOT in the longest common subsequence of two orderings.

    Both arguments are orderings of the *same set* of items, so the LCS is the
    largest subset already in agreeing relative order; the complement is the
    minimal set of items that must have moved.
    """
    a, b = base_order, now_order
    n, m = len(a), len(b)
    if n == 0:
        return set()
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                dp[i][j] = 1 + dp[i + 1][j + 1]
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    keep: set = set()
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            keep.add(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return set(a) - keep


def _moved_keys(
    by_key: dict[tuple[str, str], CurrentCell],
    base_index: dict[tuple[str, str], BaselineCell],
    states: dict[tuple[str, str], str],
) -> set[tuple[str, str]]:
    """Keys that are unchanged in content but repositioned for one deck."""
    stable = [k for k, st in states.items() if st == "same" and k in by_key and k in base_index]
    base_order = sorted(stable, key=lambda k: base_index[k].position)
    now_order = sorted(stable, key=lambda k: by_key[k].position)
    return _lcs_complement(base_order, now_order)


def classify_changes(
    de_current: list[CurrentCell],
    en_current: list[CurrentCell],
    de_baseline: list[BaselineCell] | None,
    en_baseline: list[BaselineCell] | None,
    *,
    de_path: Path,
    en_path: Path,
    baseline_source: str,
) -> SyncPlan:
    """Diff both decks against their baselines into a typed :class:`SyncPlan`.

    Pure: no IO, no LLM. ``de_baseline`` / ``en_baseline`` are ``None`` when no
    baseline exists for that deck; if either is ``None`` the pair runs in the
    limited cold-start path (id-less adds + shared-id pairing only).
    """
    plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source=baseline_source)

    de_lists, de_idless = _index_by_key(de_current)
    en_lists, en_idless = _index_by_key(en_current)

    has_baseline = de_baseline is not None and en_baseline is not None
    de_base = _baseline_index(de_baseline)
    en_base = _baseline_index(en_baseline)

    # Collapse duplicate-id cells to one original each, emitting a `rename` for
    # every copy (or an error when the original can't be identified). Duplicate
    # resolution only renames against a real both-deck baseline; with a missing
    # or asymmetric baseline it gets no baseline (every duplicate then errors).
    dup_de_base = de_base if has_baseline else {}
    dup_en_base = en_base if has_baseline else {}
    de_by_key, de_error_keys = _resolve_duplicates(
        de_current, de_lists, dup_de_base, "de->en", plan
    )
    en_by_key, en_error_keys = _resolve_duplicates(
        en_current, en_lists, dup_en_base, "en->de", plan
    )

    if not has_baseline:
        _classify_cold(plan, de_by_key, en_by_key, de_idless, en_idless, set())
        _append_idless_adds(plan, de_idless, en_idless)
        _refuse_cold_both_directions(plan)
        plan.proposals.sort(key=_proposal_sort_key)
        return plan

    # Drop keys an errored duplicate left unresolved, so a still-in-baseline key
    # whose original was dropped does not re-enter the diff as a phantom remove.
    keys = (set(de_by_key) | set(en_by_key) | set(de_base) | set(en_base)) - (
        de_error_keys | en_error_keys
    )

    states_de: dict[tuple[str, str], str] = {}
    states_en: dict[tuple[str, str], str] = {}
    for key in keys:
        states_de[key] = _state(de_by_key.get(key), de_base.get(key))
        states_en[key] = _state(en_by_key.get(key), en_base.get(key))

    moved_de = _moved_keys(de_by_key, de_base, states_de)
    moved_en = _moved_keys(en_by_key, en_base, states_en)
    order_conflict_reported = False

    for key in sorted(keys):
        sid, role = key
        de_st = states_de[key]
        en_st = states_en[key]
        de_now = de_by_key.get(key)
        en_now = en_by_key.get(key)

        # --- both present now -------------------------------------------------
        if de_now is not None and en_now is not None:
            if de_st == "edited" and en_st == "edited":
                plan.proposals.append(
                    Proposal(
                        kind="conflict",
                        role=role,
                        direction=None,
                        slide_id=sid,
                        reason="edited on both sides since last sync",
                    )
                )
            elif de_st == "edited" and en_st == "same":
                plan.proposals.append(_edit(sid, role, "de->en", de_now, en_now))
            elif en_st == "edited" and de_st == "same":
                plan.proposals.append(_edit(sid, role, "en->de", en_now, de_now))
            elif de_st == "same" and en_st == "same":
                # Content is in sync, but a tag-only edit (invisible to the hash)
                # may have drifted on one side — mirror it (Issue #198).
                _maybe_retag(plan, key, role, de_now, en_now, de_base.get(key), en_base.get(key))
                _emit_same(
                    plan,
                    key,
                    role,
                    de_now,
                    en_now,
                    moved_de,
                    moved_en,
                    order_conflict_reported,
                )
                if key in moved_de and key in moved_en:
                    order_conflict_reported = True
            else:
                # added/edited mixes that aren't a clean edit (e.g. one side
                # freshly minted the id while the other changed). Safe default:
                # treat newly-paired-but-unverifiable as in sync; flag the genuinely
                # ambiguous edit-vs-add combos.
                if "edited" in (de_st, en_st):
                    plan.issues.append(
                        PlanIssue(
                            severity="warning",
                            slide_id=sid,
                            reason=f"role={role!r} ambiguous state (de={de_st}, en={en_st}); "
                            "resolve manually",
                        )
                    )
                else:
                    plan.in_sync_count += 1
            continue

        # --- removed on one side ---------------------------------------------
        if de_st == "removed" and en_now is not None:
            if en_st == "edited":
                plan.proposals.append(
                    Proposal(
                        kind="conflict",
                        role=role,
                        direction=None,
                        slide_id=sid,
                        reason="removed on DE but edited on EN since last sync",
                    )
                )
            else:
                plan.proposals.append(
                    Proposal(
                        kind="remove",
                        role=role,
                        direction="de->en",
                        slide_id=sid,
                        reason="removed on DE",
                        target_position=en_now.position,
                    )
                )
            continue
        if en_st == "removed" and de_now is not None:
            if de_st == "edited":
                plan.proposals.append(
                    Proposal(
                        kind="conflict",
                        role=role,
                        direction=None,
                        slide_id=sid,
                        reason="removed on EN but edited on DE since last sync",
                    )
                )
            else:
                plan.proposals.append(
                    Proposal(
                        kind="remove",
                        role=role,
                        direction="en->de",
                        slide_id=sid,
                        reason="removed on EN",
                        target_position=de_now.position,
                    )
                )
            continue

        # --- present on one side, no counterpart -> add ----------------------
        if de_now is not None and en_st == "absent":
            plan.proposals.append(_add(sid, role, "de->en", de_now))
            continue
        if en_now is not None and de_st == "absent":
            plan.proposals.append(_add(sid, role, "en->de", en_now))
            continue

        # --- removed on both / removed+absent -> nothing to propagate --------
        # (de removed & en absent/removed, or mirror) falls through to no-op.

    _append_idless_adds(plan, de_idless, en_idless)
    _refuse_idless_both_directions(plan)
    _refuse_idcarrying_mismatched(plan, de_base, en_base, baseline_source)
    plan.proposals.sort(key=_proposal_sort_key)
    return plan


def _emit_same(
    plan: SyncPlan,
    key: tuple[str, str],
    role: str,
    de_now: CurrentCell,
    en_now: CurrentCell,
    moved_de: set,
    moved_en: set,
    order_conflict_reported: bool,
) -> None:
    sid = key[0]
    in_de = key in moved_de
    in_en = key in moved_en
    if in_de and not in_en:
        plan.proposals.append(
            Proposal(
                kind="move",
                role=role,
                direction="de->en",
                slide_id=sid,
                reason="reordered on DE",
                old_position=en_now.position,
                new_position=de_now.position,
            )
        )
    elif in_en and not in_de:
        plan.proposals.append(
            Proposal(
                kind="move",
                role=role,
                direction="en->de",
                slide_id=sid,
                reason="reordered on EN",
                old_position=de_now.position,
                new_position=en_now.position,
            )
        )
    elif in_de and in_en:
        if not order_conflict_reported:
            plan.issues.append(
                PlanIssue(
                    severity="warning",
                    slide_id=None,
                    reason="cell order drifted on both decks; order not propagated "
                    "(resolve ordering manually)",
                )
            )
        plan.in_sync_count += 1
    else:
        plan.in_sync_count += 1


def _classify_cold(
    plan: SyncPlan,
    de_by_key: dict[tuple[str, str], CurrentCell],
    en_by_key: dict[tuple[str, str], CurrentCell],
    de_idless: list[CurrentCell],
    en_idless: list[CurrentCell],
    excluded: set[tuple[str, str]],
) -> None:
    """No-baseline path: pair by shared id, add id-less, flag ambiguities.

    Without a baseline we cannot tell an edit from a removal or assign a
    direction, so a shared id whose content differs across decks is surfaced
    as an issue (needs explicit ``--source-lang``) rather than guessed.
    """
    keys = (set(de_by_key) | set(en_by_key)) - excluded
    for key in sorted(keys):
        sid, role = key
        de_now = de_by_key.get(key)
        en_now = en_by_key.get(key)
        if de_now is not None and en_now is not None:
            # Cross-language hashes never match (different prose), so we cannot
            # assert "in sync" from content; treat a shared id as a confirmed
            # pair and leave content reconciliation to a baseline-backed run.
            plan.in_sync_count += 1
        elif de_now is not None:
            plan.proposals.append(_add(sid, role, "de->en", de_now, cold=True))
        elif en_now is not None:
            plan.proposals.append(_add(sid, role, "en->de", en_now, cold=True))


def _append_idless_adds(
    plan: SyncPlan,
    de_idless: list[CurrentCell],
    en_idless: list[CurrentCell],
) -> None:
    for cell in de_idless:
        plan.proposals.append(_add(None, cell.role, "de->en", cell))
    for cell in en_idless:
        plan.proposals.append(_add(None, cell.role, "en->de", cell))


def _refuse(source: Proposal, reason: str) -> Proposal:
    """A structural ``refuse`` standing in for an add we decline to apply (#216).

    Keeps the source cell's identity (direction / slide_id / source_position) so
    the plan can show *which* cell was refused, but carries ``kind="refuse"`` /
    ``disposition="refuse"`` so it is never counted as an ``add`` and never
    applied — the engine defers it and the watermark holds at the baseline.
    """
    return Proposal(
        kind="refuse",
        role=source.role,
        direction=source.direction,
        slide_id=source.slide_id,
        reason=reason,
        source_position=source.source_position,
        disposition="refuse",
    )


def _replace_adds_with_refusals(plan: SyncPlan, doomed: list[Proposal], reason: str) -> None:
    """Swap the ``doomed`` add proposals out of the plan for ``refuse`` items."""
    doomed_ids = {id(p) for p in doomed}
    plan.proposals = [p for p in plan.proposals if id(p) not in doomed_ids]
    plan.proposals.extend(_refuse(p, reason) for p in doomed)


def _refuse_cold_both_directions(plan: SyncPlan) -> None:
    """Cold start: if adds would flow BOTH ways, refuse them all (#216).

    With no baseline, adds in both directions mean each half carries content the
    other lacks: a freshly-split parallel pair (all id-less), a per-half
    ``assign-ids`` run (both id'd, *mismatched* ids), or a half-id'd pair. None
    can be safely auto-paired *here* — pairing structurally-parallel halves is the
    cross-language similarity-guess the base design forbids (§3.2 of
    ``single-language-authoring-sync``). Applying both directions would
    translate-and-insert both sets and silently **double** both decks, so refuse
    instead. (Phase 3's provider-gated correspondence check may later mint shared
    ids for a confirmed pair.) A one-directional cold start — new content on one
    side only — keeps its adds and applies normally.
    """
    adds = [p for p in plan.proposals if p.kind == "add"]
    if len({p.direction for p in adds}) <= 1:
        return
    _replace_adds_with_refusals(
        plan,
        adds,
        "cold-start pair drifted on both decks (no baseline to pair against) — "
        "sync one direction at a time, or assign shared slide_ids first (#216)",
    )


def _refuse_idless_both_directions(plan: SyncPlan) -> None:
    """Baseline path: id-less adds on BOTH decks can't be paired — refuse them (#216).

    An id-less new cell on each side has no ``slide_id`` to pair on, so translating
    and inserting both would cross-add (each deck gets the other's untranslatable
    twin). This is the situation the old apply-time guard deferred; deciding it
    here, at plan time, is what makes the dry-run show it. id-*carrying*
    both-direction adds are handled separately by
    :func:`_refuse_idcarrying_mismatched`: against a *watermark* baseline their ids
    were absent from it, so they are genuinely distinct new slides and apply; against
    a committed *git-HEAD* baseline that already carried them they may be a
    mismatched-id pair and are refused (#226).
    """
    idless = [p for p in plan.proposals if p.kind == "add" and p.slide_id is None]
    if len({p.direction for p in idless}) <= 1:
        return
    _replace_adds_with_refusals(
        plan,
        idless,
        "id-less new slides on both decks — edit one deck at a time "
        "(no slide_id to pair the halves; #216)",
    )


def _refuse_idcarrying_mismatched(
    plan: SyncPlan,
    de_base: dict[tuple[str, str], BaselineCell],
    en_base: dict[tuple[str, str], BaselineCell],
    baseline_source: str,
) -> None:
    """Refuse ambiguous one-sided adds against a committed baseline (#226).

    Against a *git-HEAD* baseline, an add that puts content the other deck **may
    already carry** onto it is ambiguous when it cannot be content-matched by id:

    - a **committed id'd** slide present on one deck only whose id was already in that
      deck's baseline ("same" / "edited" state) — a genuinely one-sided slide, **or**
      one half of a *mismatched-id* pair (same content, divergent ids: per-half
      ``assign-ids``);
    - an **id-less** new slide — a genuinely new cell, **or** a hand-typed
      counterpart of a slide the other deck already carries id'd (the *half-id'd
      sharing-a-key* shape: id'd "B" on one deck, id-less "B" on the other).

    These two buckets are considered **together**: when adds across **both** of them
    span both directions, applying them would translate-and-insert content the other
    deck already has → silently **double** it. ``slide_id`` alone cannot tell a twin
    from a genuinely-distinct slide — only cross-language content correspondence can
    (the planned strategy-B follow-up, #226) — so refuse the whole ambiguous set
    rather than guess. (Considering the buckets separately misses the *mixed* case,
    where the id'd half and the id-less half each flow in a single — but opposite —
    direction.)

    Conservative gating that leaves every safe path untouched:

    - **git-HEAD only.** A *watermark* baseline records both decks, so a slide added
      since the last sync reads as "added" (id absent from the watermark) and stays a
      genuine cross-add — the by-design distinct-new-slides behavior
      (:func:`_refuse_idless_both_directions`' note). A committed never-synced deck
      has no such signal. (A pair sharing **no** ids is already routed to the cold
      path by :func:`_pair_is_unbootstrapped`, #225; this catches the *partial-overlap*
      pair that shares one id and so kept its git-HEAD baseline.)
    - **committed id, or id-less.** A genuinely-new *id'd* slide (id *absent* from the
      git-HEAD baseline — authored but not yet committed) is **not** suspect and still
      cross-adds; only a committed id'd slide, or an id-less one, can be a twin.
    - **both directions.** A one-sided add (id'd or id-less) in a single direction is
      the ordinary "translate the missing counterpart" sync and is kept; the
      both-directions id-less-only case is already refused upstream
      (:func:`_refuse_idless_both_directions`).
    """
    if baseline_source != "git-head":
        return
    ambiguous = [
        p
        for p in plan.proposals
        if p.kind == "add"
        and (
            p.slide_id is None
            or (p.slide_id, p.role) in (de_base if p.direction == "de->en" else en_base)
        )
    ]
    if len({p.direction for p in ambiguous}) <= 1:
        return
    _replace_adds_with_refusals(
        plan,
        ambiguous,
        "slides present on one deck only, in both directions, against a committed "
        "baseline — possibly the same content carrying divergent (or one-sided id-less) "
        "slide_ids, which would double on apply. Reconcile the slide_ids (make both "
        "halves share one id) or sync one deck at a time (#226)",
    )


def _add(
    slide_id: str | None,
    role: str,
    direction: str,
    source: CurrentCell,
    *,
    cold: bool = False,
) -> Proposal:
    if slide_id is None:
        reason = "new id-less slide"
    elif cold:
        reason = "slide_id present on one side only (no baseline)"
    else:
        reason = "new slide (id unknown to baseline)"
    return Proposal(
        kind="add",
        role=role,
        direction=direction,
        slide_id=slide_id,
        reason=reason,
        translation_pending=True,
        source_position=source.position,
    )


def _edit(
    slide_id: str,
    role: str,
    direction: str,
    source: CurrentCell | None,
    target: CurrentCell | None,
) -> Proposal:
    return Proposal(
        kind="edit",
        role=role,
        direction=direction,
        slide_id=slide_id,
        reason=f"edited on {direction.split('->')[0].upper()}",
        source_position=source.position if source else None,
        target_position=target.position if target else None,
    )


def _retag_direction(
    de_now: frozenset[str],
    en_now: frozenset[str],
    de_base: frozenset[str] | None,
    en_base: frozenset[str] | None,
) -> str | None:
    """Which side a one-sided tag drift came from: the retag decision rule.

    Returns ``"de->en"`` / ``"en->de"`` when exactly one side's tag set drifted
    from its baseline (that side is the source), ``"both"`` when both drifted
    (a tag conflict the caller surfaces as a warning), or ``None`` when there is
    nothing to mirror: the halves already agree, a baseline tag set is unknown (a
    pre-#198 watermark — direction undeterminable, so never guessed), or neither
    side changed (a pre-existing divergence the validator flags, not this edit's
    doing). Shared by the id-carrying (:func:`_maybe_retag`) and the id-less
    localized (:func:`_classify_localized_idless_retags`) retag paths so the two
    can never disagree about what counts as a one-sided drift.
    """
    if de_now == en_now:
        return None  # already consistent — nothing to mirror
    if de_base is None or en_base is None:
        return None  # no tag baseline to attribute the drift — degrade gracefully
    de_changed = de_now != de_base
    en_changed = en_now != en_base
    if de_changed and not en_changed:
        return "de->en"
    if en_changed and not de_changed:
        return "en->de"
    if de_changed and en_changed:
        return "both"
    return None


def _maybe_retag(
    plan: SyncPlan,
    key: tuple[str, str],
    role: str,
    de_now: CurrentCell,
    en_now: CurrentCell,
    de_base: BaselineCell | None,
    en_base: BaselineCell | None,
) -> None:
    """Emit a ``retag`` when an id'd cell's tag set drifted on exactly one side.

    Tags are language-independent, so a synced pair carries identical tag sets;
    the content hash is blind to a tag-only edit (Issue #198). Delegates the
    one-sided-drift decision to :func:`_retag_direction` and mirrors the changed
    side's tags onto the other; a both-sides-changed tag conflict is surfaced as a
    warning rather than guessed.
    """
    direction = _retag_direction(
        de_now.tags,
        en_now.tags,
        de_base.tags if de_base is not None else None,
        en_base.tags if en_base is not None else None,
    )
    if direction == "de->en":
        plan.proposals.append(_retag(key[0], role, "de->en", de_now, en_now))
    elif direction == "en->de":
        plan.proposals.append(_retag(key[0], role, "en->de", en_now, de_now))
    elif direction == "both":
        plan.issues.append(
            PlanIssue(
                severity="warning",
                slide_id=key[0],
                reason=f"role={role!r} tags changed on both decks "
                f"(de={sorted(de_now.tags)}, en={sorted(en_now.tags)}); "
                "not propagated — reconcile tags manually",
                # Issue #202: tag-only, body untouched — pin this cell's tags at the
                # old baseline (by id) while the rest of the pass advances.
                tag_hold=TagHold(slide_id=key[0], role=role),
            )
        )


def _retag(
    slide_id: str,
    role: str,
    direction: str,
    source: CurrentCell,
    target: CurrentCell,
) -> Proposal:
    return Proposal(
        kind="retag",
        role=role,
        direction=direction,
        slide_id=slide_id,
        reason=f"tags changed on {direction.split('->')[0].upper()} ({sorted(source.tags)})",
        source_position=source.position,
        target_position=target.position,
    )


# ---------------------------------------------------------------------------
# id-less localized tag mirroring (Issue #198 Tier C / #190 item 3)
# ---------------------------------------------------------------------------


def _localized_lang_cells(cells: list[Cell], lang: str) -> list[Cell]:
    """Non-j2 cells of ``lang`` in document order — the watermark ``lang`` partition.

    Mirrors :func:`watermark_rows`' partitioning exactly (``meta.lang == lang`` and
    not j2), so the *i*-th cell here lines up with watermark position *i* of that
    language. Includes both id-carrying localized cells (which anchor the
    alignment) and the id-less ones whose tags this pass mirrors.
    """
    return [c for c in cells if not c.metadata.is_j2 and c.metadata.lang == lang]


def _streams_aligned(de_loc: list[Cell], en_loc: list[Cell]) -> bool:
    """Whether the two localized streams are positional twins (cell-by-cell).

    Requires each positionally-paired ``(de, en)`` cell to agree on per-cell role,
    cell type, and ``slide_id`` (both id-less, or the *same* id). A reorder or a
    structural edit breaks this — at which point positional pairing of the id-less
    cells would be unsound, so the pass declines (the validator's split-tag-parity
    check still surfaces any standing asymmetry). Lengths are equal by the caller's
    gate; ``strict=True`` makes that an assertion rather than a silent truncation.
    """
    for de_cell, en_cell in zip(de_loc, en_loc, strict=True):
        if role_of(de_cell.metadata) != role_of(en_cell.metadata):
            return False
        if de_cell.metadata.cell_type != en_cell.metadata.cell_type:
            return False
        if (de_cell.metadata.slide_id or None) != (en_cell.metadata.slide_id or None):
            return False
    return True


def _retag_idless(
    source_cell: Cell, direction: str, position: int, tags: frozenset[str]
) -> Proposal:
    """An id-less localized ``retag`` — targets the twin by position, carries tags."""
    kind_label = "code" if source_cell.metadata.cell_type == "code" else "markdown"
    return Proposal(
        kind="retag",
        role=kind_label,
        direction=direction,
        slide_id=None,
        reason=f"tags changed on {direction.split('->')[0].upper()} "
        f"({sorted(tags)}) — id-less localized {kind_label}",
        source_position=position,
        target_position=position,
        tags=tuple(source_cell.metadata.tags),
    )


def _classify_localized_idless_retags(
    de_cells: list[Cell],
    en_cells: list[Cell],
    watermark_cache: SyncWatermarkCache,
    de_path: Path,
    en_path: Path,
    plan: SyncPlan,
) -> None:
    """Mirror a tag-only edit on an **id-less localized** cell across the halves.

    Issue #198 Tier C (with #190 item 3): the per-cell engine cannot reach an
    id-less localized cell — ``role_of`` is ``None`` because it has no
    ``slide_id`` — so a one-sided tag edit on such a cell (the exact cell the #198
    report hit: a ``lang=`` code cell with no id that gained ``keep``) is invisible
    to :func:`_maybe_retag`. This pass gives those cells a cross-language identity
    by **position** in their language's cell stream (the #190 item-3 identity,
    already recorded in the membership-widened watermark) and applies the same
    one-sided-drift rule (:func:`_retag_direction`) against the watermark's recorded
    tag set, emitting an id-less ``retag`` the apply targets by position.

    Conservative by construction — any doubt declines either the whole pass or the
    individual cell rather than risk mirroring a tag onto the wrong cell:

    - **watermark baseline only** (the caller gates on ``source == "watermark"``):
      the git-HEAD baseline records no tags for id-less cells, so direction would
      be undeterminable;
    - **no ``move``**: a reorder invalidates positional pairing;
    - **structural alignment**: each language's localized stream must have the same
      length as the other *and* as its own baseline (so position *i* still names the
      same cell), and every positionally-paired cell must be a true twin
      (:func:`_streams_aligned`);
    - **per-cell body-hash anchor**: even within an aligned, same-length stream, two
      *id-less* localized cells (both ``role_of`` ``None``, same kind, no id) could be
      swapped without tripping :func:`_streams_aligned` — and a body edit leaves the
      ``("L", kind)`` signature unchanged. So a cell is retagged only when **both**
      halves' current body hash still equals the baseline body hash recorded at that
      position: a tag-only edit never changes the body, so a hash mismatch means the
      position now names a *different* (reordered) or *body-edited* cell — leave it to
      the structural pass / validator instead of guessing;
    - **body-uniqueness anchor**: the per-position hash check is defeated when two
      cells share a body (an identical-body swap leaves every position's hash
      matching), so a position is retagged only when its body hash is **unique** in its
      language's stream on both the current and the baseline side — the non-unique-anchor
      guard used throughout the structural pass. Two byte-identical id-less cells are
      therefore never auto-mirrored (the validator flags any standing asymmetry).

    A both-sides tag change is surfaced as a warning, mirroring the id-carrying path.
    (Like every classifier warning, a ``both`` tag conflict holds the whole-deck
    watermark until resolved — a pre-existing property shared with the id-carrying
    ``_maybe_retag`` ``both`` path and the reorder/ambiguity warnings; see #198.)
    """
    if plan.count("move") > 0:
        return  # a reorder this pass — positional pairing is unsound
    de_loc = _localized_lang_cells(de_cells, "de")
    en_loc = _localized_lang_cells(en_cells, "en")
    de_rows = watermark_cache.get_deck(str(de_path), str(en_path), "de")
    en_rows = watermark_cache.get_deck(str(de_path), str(en_path), "en")
    if not (len(de_loc) == len(en_loc) == len(de_rows) == len(en_rows)):
        return  # structural drift — positions unreliable; validator flags asymmetry
    if not _streams_aligned(de_loc, en_loc):
        return  # reordered / mismatched twins — do not trust positional pairing

    de_base_hash = {pos: chash for (pos, _sid, _role, chash, _construct) in de_rows}
    en_base_hash = {pos: chash for (pos, _sid, _role, chash, _construct) in en_rows}
    de_cur_hash = [cell_content_hash(c.content) for c in de_loc]
    en_cur_hash = [cell_content_hash(c.content) for c in en_loc]
    # A body hash shared by two cells of a language's stream (current OR baseline)
    # cannot anchor a position against a reorder: two id-less cells with the *same*
    # body are interchangeable to the per-position hash check, so swapping them would
    # masquerade as a tag edit and could mirror a tag onto the wrong twin. Decline any
    # non-unique-bodied position — the same non-unique-anchor guard the structural pass
    # applies (``_find_by_anchor`` / ``_baseline_anchor_hashes``' ``Counter``). The
    # validator's split-tag-parity check still flags any standing asymmetry.
    de_cur_counts = Counter(de_cur_hash)
    en_cur_counts = Counter(en_cur_hash)
    de_base_counts = Counter(de_base_hash.values())
    en_base_counts = Counter(en_base_hash.values())
    de_base_tags = watermark_cache.get_deck_tags(str(de_path), str(en_path), "de")
    en_base_tags = watermark_cache.get_deck_tags(str(de_path), str(en_path), "en")
    for i, (de_cell, en_cell) in enumerate(zip(de_loc, en_loc, strict=True)):
        # Only the id-less localized cells; id-carrying twins ride the per-cell path.
        if role_of(de_cell.metadata) is not None or role_of(en_cell.metadata) is not None:
            continue
        # Body unchanged on BOTH halves vs the recorded baseline at this position —
        # else position i no longer names the same cell (reorder) or the body itself
        # was edited (the structural pass's job), so a tag mirror would be unsound.
        if de_cur_hash[i] != de_base_hash.get(i) or en_cur_hash[i] != en_base_hash.get(i):
            continue
        # ...and that body uniquely anchors the position on BOTH halves (current and
        # baseline). A duplicated body defeats the hash anchor (an identical-body swap
        # leaves every position's hash matching), so leave it to the validator.
        if de_cur_counts[de_cur_hash[i]] != 1 or de_base_counts[de_cur_hash[i]] != 1:
            continue
        if en_cur_counts[en_cur_hash[i]] != 1 or en_base_counts[en_cur_hash[i]] != 1:
            continue
        de_now = frozenset(de_cell.metadata.tags)
        en_now = frozenset(en_cell.metadata.tags)
        direction = _retag_direction(de_now, en_now, de_base_tags.get(i), en_base_tags.get(i))
        if direction == "de->en":
            plan.proposals.append(_retag_idless(de_cell, "de->en", i, de_now))
        elif direction == "en->de":
            plan.proposals.append(_retag_idless(en_cell, "en->de", i, en_now))
        elif direction == "both":
            plan.issues.append(
                PlanIssue(
                    severity="warning",
                    slide_id=None,
                    reason=f"id-less localized cell #{i} tags changed on both decks "
                    f"(de={sorted(de_now)}, en={sorted(en_now)}); "
                    "not propagated — reconcile tags manually",
                    # Issue #202: tag-only, body untouched — pin this cell's tags at
                    # the old baseline (by position, identical on both aligned
                    # halves) while the rest of the pass advances.
                    tag_hold=TagHold(position=i),
                )
            )


_KIND_ORDER = {
    "conflict": 0,
    "remove": 1,
    "edit": 2,
    "retag": 3,
    "move": 4,
    "add": 5,
    "rename": 6,
    "refuse": 7,
    "mint": 8,
    "adopt": 9,
}


def _proposal_sort_key(p: Proposal) -> tuple:
    return (
        _KIND_ORDER.get(p.kind, 10),
        p.source_position if p.source_position is not None else 1_000_000,
        p.slide_id or "",
        p.role,
    )


# ---------------------------------------------------------------------------
# IO wrapper + report
# ---------------------------------------------------------------------------


def _pair_is_unbootstrapped(de_current: list[CurrentCell], en_current: list[CurrentCell]) -> bool:
    """Whether the two halves share **no** ``slide_id`` — a cold-start shape (#225).

    The per-cell engine reconciles a pair by its shared ``(slide_id, role)`` keys, so
    a pair is "bootstrapped" only if the two halves have **at least one ``slide_id`` in
    common** (the engine can then pair at least one cell across the decks). When they
    share none, a git-HEAD baseline is **no more informative than no baseline at all**
    — and running the keyed baseline path against it is actively harmful: every slide
    reads as "present on one deck, missing on the other", which the engine
    translate-and-inserts in **both** directions and so **doubles both decks** (Issue
    #225). So a no-shared-keying committed pair is treated as a true cold start
    (``source="none"``) and routed to the correspondence-gated ``mint`` / ``adopt`` /
    ``refuse`` bootstrap, exactly like a never-committed pair, instead of the keyed
    git-HEAD diff.

    "No shared keying" covers every committed shape the keyed path would double:
    a **fully id-less** pair (→ ``mint``), a **half-id'd** pair (one side fully id'd,
    the other id-less → ``adopt``), a **mismatched-id** pair (both id'd but disjoint —
    per-half ``assign-ids`` → ``refuse``), and a partial-disjoint mix. A pair that
    shares even one id is (at least partly) bootstrapped: its baseline keys are real
    and kept, so a genuinely new id-less cell appended to it stays an ordinary ``add``.
    The cheap correspondence verifier is the safety net for the bootstrap path, so an
    aggressive demotion never bakes a wrong id — a non-corresponding pair refuses. A
    pair with **no** sync cells at all is a no-op, not a cold start.
    """
    if not de_current and not en_current:
        return False
    de_ids = {c.slide_id for c in de_current if c.slide_id is not None}
    en_ids = {c.slide_id for c in en_current if c.slide_id is not None}
    return de_ids.isdisjoint(en_ids)


def build_sync_plan(
    de_path: Path,
    en_path: Path,
    *,
    watermark_cache: SyncWatermarkCache | None = None,
    allow_git_fallback: bool = True,
    provider_available: bool = False,
) -> SyncPlan:
    """Resolve the baseline and classify the pair into a :class:`SyncPlan`.

    Baseline priority: watermark → git HEAD → none (see module docstring).
    Reads the two files; writes nothing.

    ``provider_available`` (#216 Phase 3, design §12) is the plan-time fact "a
    correspondence verifier will be available at apply time" (an LLM provider is
    configured **and** ``--verify-cold-pairs`` is on). When true, a cold-start
    both-id-less refusal over a *unifiable* pair is upgraded to a ``pending`` mint
    candidate instead of a `refuse`; the verifier (stage 2b, in apply) confirms the
    halves correspond before a shared id is minted. Identical in dry-run and apply,
    so the two agree on `refuse`-vs-`pending`.
    """
    de_cells = parse_cells(de_path.read_text(encoding="utf-8"))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"))
    de_current = ordered_sync_cells(de_cells, "de")
    en_current = ordered_sync_cells(en_cells, "en")

    de_baseline: list[BaselineCell] | None = None
    en_baseline: list[BaselineCell] | None = None
    baseline_shared: list[str] | None = None
    source = "none"

    if watermark_cache is not None and watermark_cache.has_pair(str(de_path), str(en_path)):
        de_baseline = _baseline_from_watermark(
            watermark_cache.get_deck(str(de_path), str(en_path), "de"),
            watermark_cache.get_deck_tags(str(de_path), str(en_path), "de"),
        )
        en_baseline = _baseline_from_watermark(
            watermark_cache.get_deck(str(de_path), str(en_path), "en"),
            watermark_cache.get_deck_tags(str(de_path), str(en_path), "en"),
        )
        # Ordered content hashes of the baseline's neutral cells (position order),
        # matching _shared_hashes — see align_anchored for why this is a sequence,
        # not an anchor map.
        baseline_shared = [
            chash
            for (_pos, _sid, _role, chash, _construct) in watermark_cache.get_deck(
                str(de_path), str(en_path), "shared"
            )
        ]
        source = "watermark"
    elif allow_git_fallback and not _pair_is_unbootstrapped(de_current, en_current):
        gb_de = _baseline_from_git_head(de_path)
        gb_en = _baseline_from_git_head(en_path)
        if gb_de is not None and gb_en is not None:
            de_baseline, en_baseline = gb_de, gb_en
            source = "git-head"

    plan = classify_changes(
        de_current,
        en_current,
        de_baseline,
        en_baseline,
        de_path=de_path,
        en_path=en_path,
        baseline_source=source,
    )

    # Item-2 (Phase 3a): detect a language-neutral code-only change the keyed
    # classifier cannot see, and hand its direction to the structural pass. Only
    # against a real (watermark) baseline; the keyed direction, when present,
    # already drives the structural pass, so the anchor direction is a *fallback*.
    if baseline_shared is not None:
        alignment = align_anchored(de_cells, en_cells, baseline_shared)
        if alignment.irreconcilable:
            plan.issues.append(
                PlanIssue(
                    severity="error",
                    slide_id=None,
                    reason="language-neutral cells were edited independently on both "
                    "decks (different cells); a single-direction sync cannot reconcile "
                    "them without reverting one — resolve manually",
                )
            )
        elif alignment.diverged:
            _apply_divergence(plan, de_path, en_path, forced=alignment.direction)
        else:
            plan.anchor_direction = alignment.direction

    # Tier C (Issue #198 / #190 item 3): mirror a tag-only edit on an id-less
    # localized cell — the per-cell engine cannot key it (no slide_id) and the
    # body-hash classifier is blind to a tag change. Only against a real watermark
    # baseline (the git-HEAD baseline records no id-less tags). Appends id-less
    # ``retag`` proposals, then re-sorts so they interleave with the keyed plan.
    if source == "watermark" and watermark_cache is not None:
        _classify_localized_idless_retags(
            de_cells, en_cells, watermark_cache, de_path, en_path, plan
        )
        plan.proposals.sort(key=_proposal_sort_key)

    # Phase 3 (#216 §12): a cold-start refusal becomes a `pending` bootstrap
    # candidate when a provider/verifier is available — apply (2b) then confirms
    # correspondence before any id reaches disk. Two shapes, mutually exclusive:
    # a both-id-less *unifiable* pair → `mint` (fresh shared ids); a *half-id'd*
    # pair (one half fully id'd, the other fully id-less) → `adopt` (the id-less
    # half adopts the id'd half's existing ids). `source == "none"` covers both a
    # never-committed pair AND a committed un-bootstrapped one: an id-less committed
    # pair is demoted to "none" above (`_pair_is_unbootstrapped`, Issue #225), since
    # its git-HEAD baseline carries no usable ids. A watermark-baseline both-sides-
    # idless deck (a synced deck whose ids were later stripped — against the design
    # invariant) still refuses, a documented edge. `adopt` runs after `mint` and is a
    # no-op when `mint` already consumed the refusals.
    if source == "none" and provider_available:
        _maybe_emit_cold_mint(plan, de_cells, en_cells, de_path, en_path)
        _maybe_emit_cold_adopt(plan, de_cells, en_cells, de_path, en_path)

    return plan


def _maybe_emit_cold_mint(
    plan: SyncPlan,
    de_cells: list[Cell],
    en_cells: list[Cell],
    de_path: Path,
    en_path: Path,
) -> None:
    """Upgrade an all-id-less cold refusal to a ``pending`` mint candidate (#216 §12).

    Gated three ways, conservative by construction: every refusal must be id-less
    (the both-id-less cold pair — a mismatched-id refusal is left to stand and a
    half-id'd refusal is handled by :func:`_maybe_emit_cold_adopt` instead), the
    localized streams must be positionally aligned (so the verifier and the minter
    pair the same cells), and the pair must be **unifiable** (a read-only
    ``unify→split`` byte-faithful round-trip — the same guard
    :func:`assign_ids_in_split_pair` applies before it writes). Any gate failing
    keeps the `refuse`. Emits a single ``mint`` proposal (``disposition="pending"``)
    standing for the whole pair; the aligned heading/snippet pairs are rebuilt in
    apply from the (unchanged) files.

    Never offered when the plan already carries a **classifier error** (e.g. an
    unresolvable duplicate id): bootstrapping ids onto a structurally-broken pair
    would bake the error in — the same posture as the apply-time flush gate, which
    writes nothing on ``plan.has_errors``. (Unreachable for a true both-id-less pair,
    which has no id-carrying cells to collide — kept for symmetry with adopt.)
    """
    if plan.has_errors:
        return
    refusals = plan.refusals
    if not refusals or any(r.slide_id is not None for r in refusals):
        return
    de_loc = _localized_lang_cells(de_cells, "de")
    en_loc = _localized_lang_cells(en_cells, "en")
    if len(de_loc) != len(en_loc) or not _streams_aligned(de_loc, en_loc):
        return
    if not _is_unifiable(de_path, en_path):
        return
    refused_ids = {id(r) for r in refusals}
    plan.proposals = [p for p in plan.proposals if id(p) not in refused_ids]
    plan.proposals.append(
        Proposal(
            kind="mint",
            role="slide",
            direction=None,
            slide_id=None,
            reason="cold-start pair — pending correspondence verification (#216)",
            disposition="pending",
        )
    )
    plan.proposals.sort(key=_proposal_sort_key)


def _is_unifiable(de_path: Path, en_path: Path) -> bool:
    """Read-only: does ``unify→split`` round-trip byte-faithfully? (the mint guard).

    Mirrors :func:`assign_ids_in_split_pair`'s own gate, so a `pending` candidate
    this admits is one the minter will actually be able to write. A pair that does
    not round-trip (structurally misaligned, divergent shared cell) is not mintable,
    so the candidacy declines and the `refuse` stands.
    """
    from clm.slides.split import SplitError, UnifyError, split_text, unify_texts

    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")
    try:
        unified = unify_texts(de_text, en_text)
        rt_de, rt_en = split_text(unified)
    except (SplitError, UnifyError):
        return False
    return (rt_de, rt_en) == (de_text, en_text)


def _cold_adopt_authority(de_loc: list[Cell], en_loc: list[Cell]) -> str | None:
    """The fully-id'd side of a half-id'd cold pair (``"de"``/``"en"``), or ``None``.

    A half-id'd pair is one positionally-aligned localized stream where the
    *sync-relevant* cells are id'd on exactly one side (the **authority**) and
    id-less on the other, while the non-sync cells (id-less localized code) are
    id-less on both. The id-less half can then **adopt** the authority's existing
    ids verbatim (:func:`clm.slides.sync_apply._apply_cold_adopt`) — no minting,
    no translation. Returns the authority language, or ``None`` when the pair is
    not a clean half-id'd shape (so the cold refusal stands):

    - **lengths differ** — the streams are not positionally comparable;
    - **role / cell-type mismatch** at any position — the streams are not twins,
      so positional pairing would be unsound (this also excludes an aux-markdown or
      a localized-code half-id'd cell, whose ``role_of`` *depends on* the
      ``slide_id`` and so differs across an id'd/id-less twin — those stay refused
      rather than guessing an adopt);
    - **a sync pair that is not XOR** — both id-less (the :func:`_maybe_emit_cold_mint`
      case, not adopt) or both id'd (a mismatched-id pair → refuse);
    - **mixed authority** — some sync pairs id'd on DE, others on EN → refuse;
    - **an id on a non-sync cell** — unexpected; refuse rather than mis-stamp.

    Conservative by construction: any doubt returns ``None`` and the visible-in-git
    refuse stands, exactly the safety posture of the mint candidacy (§3.2 of
    ``single-language-authoring-sync``: never bake an id from a similarity guess).
    """
    if not de_loc or len(de_loc) != len(en_loc):
        return None
    authority: str | None = None
    saw_xor = False
    for de_cell, en_cell in zip(de_loc, en_loc, strict=True):
        de_role = role_of(de_cell.metadata)
        en_role = role_of(en_cell.metadata)
        if de_role != en_role:
            return None
        if de_cell.metadata.cell_type != en_cell.metadata.cell_type:
            return None
        de_id = de_cell.metadata.slide_id or None
        en_id = en_cell.metadata.slide_id or None
        if de_role is None:
            # A non-sync localized cell (id-less code) — must be id-less on both.
            if de_id is not None or en_id is not None:
                return None
            continue
        # A sync-relevant pair: exactly one side must carry the id (XOR).
        if (de_id is None) == (en_id is None):
            return None  # both id-less (mint's job) or both id'd (mismatched → refuse)
        side = "en" if en_id is not None else "de"
        if authority is None:
            authority = side
        elif authority != side:
            return None  # mixed authority → refuse
        saw_xor = True
    return authority if saw_xor else None


def _maybe_emit_cold_adopt(
    plan: SyncPlan,
    de_cells: list[Cell],
    en_cells: list[Cell],
    de_path: Path,
    en_path: Path,
) -> None:
    """Upgrade a half-id'd cold refusal to a ``pending`` adopt candidate (#216 §12).

    The sibling of :func:`_maybe_emit_cold_mint` for the *half-id'd* shape (one half
    fully id'd, the other fully id-less): the id-less half adopts the id'd half's
    *existing* ids rather than minting fresh ones — ``unify``/``assign_ids`` cannot
    do this (its ``_slide_ids_pair`` is ``de_id == en_id``, so an id-less cell never
    pairs with an id'd one), so apply takes an explicit stamp path. Runs **after**
    the mint pass and no-ops when mint already consumed the refusals (its removal
    empties ``plan.refusals``), so the two are mutually exclusive. The authority is
    decided by :func:`_cold_adopt_authority`; any non-clean shape leaves the refusal
    standing. Emits a single ``adopt`` proposal (``disposition="pending"``,
    ``direction="{authority}->{other}"``); apply rebuilds the aligned heading/snippet
    pairs from the (unchanged) files and verifies them before stamping.

    Never offered when the plan already carries a **classifier error** — most
    importantly a *duplicated id on the authority half* (e.g. a slide whose two
    voiceover companions share its id, which :func:`_resolve_duplicates` flags as a
    "lone duplicated companion"). Stamping the authority's id positionally onto the
    id-less twin would then propagate the duplicate onto the previously-clean half
    and advance the watermark over the corruption. Declining on ``plan.has_errors``
    matches the apply-time flush gate (which writes nothing on a classifier error)
    and keeps the dry-run honest (it shows the error + refusals, exit 2, not a
    phantom "adopt pending").
    """
    if plan.has_errors:
        return
    refusals = plan.refusals
    if not refusals:
        return
    de_loc = _localized_lang_cells(de_cells, "de")
    en_loc = _localized_lang_cells(en_cells, "en")
    authority = _cold_adopt_authority(de_loc, en_loc)
    if authority is None:
        return
    other = "en" if authority == "de" else "de"
    refused_ids = {id(r) for r in refusals}
    plan.proposals = [p for p in plan.proposals if id(p) not in refused_ids]
    plan.proposals.append(
        Proposal(
            kind="adopt",
            role="slide",
            direction=f"{authority}->{other}",
            slide_id=None,
            reason=f"half-id'd cold-start pair — {authority.upper()} ids pending "
            "correspondence verification (#216)",
            disposition="pending",
        )
    )
    plan.proposals.sort(key=_proposal_sort_key)


def _apply_divergence(
    plan: SyncPlan, de_path: Path, en_path: Path, *, forced: str | None = None
) -> None:
    """Resolve a same-cell shared divergence per the §7a policy.

    ``auto-heal`` (default): propagate the winning side and emit a *warning*; the
    heal is written but the watermark is held by the issue, so a second run
    confirms it. ``error`` mode — or auto-heal with no determinable winner — emits
    an *error* issue, so the buffered apply writes nothing and the divergence is
    surfaced rather than guessed. ``forced`` is the only loser-safe direction when
    one side also carries independent edits; otherwise the winner is selected
    (keyed direction → newer mtime).
    """
    mode = _shared_divergence_mode()
    winner = forced if forced is not None else _resolve_divergence_winner(plan, de_path, en_path)
    if winner is not None and mode == "auto-heal":
        plan.anchor_direction = winner
        plan.issues.append(
            PlanIssue(
                severity="warning",
                slide_id=None,
                reason=f"a language-neutral cell diverged on both decks; auto-healed "
                f"toward {winner} (set CLM_SYNC__SHARED_DIVERGENCE=error to surface "
                f"instead) — review the git diff",
            )
        )
    else:
        detail = (
            "CLM_SYNC__SHARED_DIVERGENCE=error"
            if winner is not None
            else "no winner (edited on both decks, mtimes tie)"
        )
        plan.issues.append(
            PlanIssue(
                severity="error",
                slide_id=None,
                reason=f"a language-neutral cell diverged on both decks; {detail} — "
                f"resolve manually",
            )
        )


def render_plan(plan: SyncPlan) -> str:
    """Render a human-readable, no-silent-no-op report of the plan."""
    lines: list[str] = []
    for issue in plan.issues:
        sid = f" {issue.slide_id}" if issue.slide_id else ""
        lines.append(f"issue-{issue.severity}{sid}: {issue.reason}")
    for p in plan.proposals:
        sid = p.slide_id if p.slide_id is not None else "(id-less)"
        direction = f" {p.direction}" if p.direction else ""
        suffix = " [translation pending]" if p.translation_pending else ""
        detail = f" — {p.reason}" if p.reason else ""
        lines.append(f"{p.kind}{direction} {sid}/{p.role}{suffix}{detail}")
    lines.append("")
    lines.append(plan.summary())
    return "\n".join(lines)


def _anchor_diff_section(
    label: str,
    base_rows: list[tuple[int, str | None, str, str, str | None]],
    cur_rows: list[tuple[int, str | None, str, str, str | None]],
) -> list[str]:
    """Format one partition's anchor-keyed diff (the §6 pass, made visible).

    Status per current cell against the watermark: ``=`` unchanged (anchor + hash
    match), ``~`` edited (anchor matches, hash differs), ``+`` new (anchor absent
    from the baseline). Anchors present in the baseline but gone from the current
    deck are listed as ``-`` (removed). A construct anchor is not content-unique, so
    matching is by anchor→{hashes} multimap (a diagnostic, last-writer-wins-free).
    """
    base_anchor_hashes: dict[str, set[str]] = {}
    for _pos, sid, _role, chash, construct in base_rows:
        base_anchor_hashes.setdefault(row_anchor(sid, construct, chash), set()).add(chash)

    n = len(cur_rows)
    lines = [f"{label} ({n} cell{'s' if n != 1 else ''}):"]
    seen: set[str] = set()
    for pos, sid, role, chash, construct in cur_rows:
        anchor = row_anchor(sid, construct, chash)
        seen.add(anchor)
        if anchor in base_anchor_hashes:
            status = "=" if chash in base_anchor_hashes[anchor] else "~"
        else:
            status = "+"
        lines.append(f"  #{pos:<3} {status}  {anchor:<36} {role}")
    for anchor in base_anchor_hashes:
        if anchor not in seen:
            lines.append(f"  ·    -  {anchor:<36} (removed)")
    if not cur_rows and not base_anchor_hashes:
        lines.append("  (none)")
    return lines


def _drifted_id_lines(
    de_cells: list[Cell],
    watermark_cache: SyncWatermarkCache | None,
    de_path: Path,
    en_path: Path,
) -> list[str]:
    """List id'd code cells whose construct drifted from the baseline (§9 candidates).

    A cell wearing a ``slide_id`` whose *current* construct no longer matches the
    construct the watermark recorded for that id is an id-migration candidate (the
    author split or renamed it). Scans the DE file, which carries the neutral and
    DE-localized id'd code; the EN twin shares the id (``de_id == en_id``).
    """
    if watermark_cache is None:
        return []
    base_construct: dict[str, str] = {}
    for partition in ("de", "en", "shared"):
        for _pos, sid, _role, _hash, construct in watermark_cache.get_deck(
            str(de_path), str(en_path), partition
        ):
            if sid is not None and construct is not None:
                base_construct.setdefault(sid, construct)
    out: list[str] = []
    for cell in de_cells:
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "code" or meta.slide_id is None:
            continue
        current = construct_of(meta, cell.content)
        base = base_construct.get(meta.slide_id)
        if base is not None and current is not None and current != base:
            out.append(f'  "{meta.slide_id}": was {base} → now {current}')
    return out


def render_explain(
    de_path: Path,
    en_path: Path,
    *,
    plan: SyncPlan,
    watermark_cache: SyncWatermarkCache | None,
) -> str:
    """Anchor-level diagnostic for ``clm slides sync --explain`` (Issue #190 §13).

    Dumps the content-anchor view the engine works in — every non-j2 cell's anchor
    (``id:`` / ``construct:`` / ``hash:``) and whether it is unchanged / edited /
    new / removed against the watermark baseline (the §6 anchor-keyed diff made
    visible) — then the neutral-cell propagation direction, any drifted ``slide_id``s
    (the §9 id-migration candidates), and finally the ordinary plan. Read-only:
    ``--explain`` writes nothing, like ``--dry-run``.
    """
    lines = [
        f"anchor diff — {de_path.name} / {en_path.name}",
        f"baseline: {plan.baseline_source}",
    ]
    has_baseline = watermark_cache is not None and watermark_cache.has_pair(
        str(de_path), str(en_path)
    )
    if not has_baseline:
        lines.append("  (no watermark for this pair — every cell reads as new; legend +)")
    lines.append("  legend: = unchanged  ~ edited  + new  - removed")
    lines.append("")

    de_cells = parse_cells(de_path.read_text(encoding="utf-8"))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"))
    de_current = watermark_rows(de_cells)
    en_current = watermark_rows(en_cells)
    current_by_partition = {
        "de": de_current["de"],
        "en": en_current["en"],
        # Neutral cells are recorded once from DE under the single-entity partition.
        "shared": de_current["shared"],
    }
    for label, partition in (
        ("DE (localized + keyed)", "de"),
        ("EN (localized + keyed)", "en"),
        ("SHARED (language-neutral)", "shared"),
    ):
        base_rows = (
            watermark_cache.get_deck(str(de_path), str(en_path), partition)
            if watermark_cache is not None
            else []
        )
        lines.extend(_anchor_diff_section(label, base_rows, current_by_partition[partition]))
        lines.append("")

    lines.append(
        "neutral propagation direction: "
        + (plan.anchor_direction if plan.anchor_direction else "none (halves agree)")
    )
    drifted = _drifted_id_lines(de_cells, watermark_cache, de_path, en_path)
    lines.append("drifted slide_ids (id-migration candidates):")
    lines.extend(drifted if drifted else ["  (none)"])
    lines.append("")

    lines.append("plan:")
    lines.append(render_plan(plan))
    return "\n".join(lines)
