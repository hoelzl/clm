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

from clm.notebooks.slide_parser import Cell, CellMetadata, comment_token_for_path, parse_cells
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
    ``conflict`` / ``rename`` / ``refuse`` / ``mint`` / ``adopt`` /
    ``reconcile``. ``direction``
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

    ``reconcile`` is the strategy-B mismatched-id-twin candidate (#228;
    ``disposition == "pending"``): one suspect cell of a committed partial-overlap
    pair whose halves carry the *same* content under *divergent* ids (per-half
    ``assign-ids``). It is emitted only when the ambiguous both-directions bucket
    (:func:`_refuse_idcarrying_mismatched`) is the whole actionable plan and a
    provider is available; apply (2b) cross-pairs the suspects by content
    correspondence and, for a confirmed twin, **rewrites** the divergent id
    (EN-authority) so the two halves share one — distinct from ``adopt`` (which
    only stamps onto an id-*less* twin) and ``mint`` (fresh ids). ``direction`` is
    the suspect's own source direction; ``slide_id`` is its current id (or ``None``
    for the id-less half of a *mixed* twin).

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


#: One membership-widened watermark row: (position, slide_id, role, content_hash, construct).
_WatermarkRow = tuple[int, str | None, str, str, str | None]


@dataclass(frozen=True)
class BaselineBundle:
    """The single baseline representation every consumer reads (#289 P1).

    Both baseline sources produce this one membership-widened shape — the
    watermark cache by reading its stored partitions
    (:func:`_bundle_from_watermark`), git HEAD by re-deriving the *same* rows
    from the committed text via :func:`watermark_rows` /
    :func:`watermark_tag_map` (:func:`_bundle_from_git_head`). Every baseline
    consumer — the keyed diff, the shared / id-less / header drift detectors,
    Tier C retag, and the apply-side anchor / id-migration passes (via
    ``SyncPlan.baseline_bundle``) — reads from here, so the two sources can
    never again diverge in *coverage*: the per-aspect parallel git-HEAD
    plumbing this replaces is how #269 (git path missing the shared/id-less/
    header baselines), #225/#226 (git-gate predicates), and the #289 git-HEAD
    tag drop shipped. ``source`` is provenance, not plumbing — the #225/#226
    committed-pair semantics still key on it.
    """

    source: str  # "watermark" | "git-head"
    rows: dict[str, list[_WatermarkRow]]  # partitions "de" | "en" | "shared"
    tags: dict[str, dict[int, frozenset[str]]]  # partitions "de" | "en" | "shared"
    header_hashes: dict[str, list[str]]  # "de" | "en" (ordered j2 header hashes)


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
    # Issue #269: per-language ordered content hashes of the baseline's id-less
    # localized cells (the ("L", kind) set). ``None`` when no baseline was
    # resolved. The structural pass uses them (as a multiset) to detect a
    # hash-anchored id-less body edit a construct anchor cannot see, and apply uses
    # them post-pass to fail-safe on any id-less drift that was not propagated.
    idless_baseline_de: list[str] | None = None
    idless_baseline_en: list[str] | None = None
    # The resolved baseline itself (#289 P1), carried so the apply engine reads
    # the SAME rows the classifier diffed against (anchor reuse, id-migration)
    # instead of re-deriving them — plan and apply agree by construction.
    baseline_bundle: BaselineBundle | None = None

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
                f"{self.count('reconcile')} reconcile",
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
        if self.anchor_direction is not None:
            # A language-neutral / id-less-localized (structural) change carries no
            # proposal but DID drift one half: the structural pass propagates it this
            # run, so the headline must not claim "already consistent" (mirrors
            # ``is_noop``, which already counts ``anchor_direction``). Issue #269.
            return (
                f"baseline={self.baseline_source}: language-neutral/structural "
                f"change propagating {self.anchor_direction} "
                f"({self.in_sync_count} cell(s) in sync)."
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


def _header_hashes(cells: list[Cell]) -> list[str]:
    """Ordered content hashes of a file's j2 deck-header cells (Issue #269).

    The deck header (``# j2 … {{ header_xx(…) }}``) is excluded from every sync
    partition (``watermark_rows`` / ``role_of`` / ``_shared_hashes`` all skip
    ``is_j2`` cells) and is language-specific, so sync never auto-translates it. To
    keep the "never report consistent while a change was dropped" invariant, the
    header is hashed here so a one-sided header edit can be detected and surfaced.
    A j2 cell's macro text lives in its **header line** (``Cell.content`` is empty
    for a directive cell), so that is what is hashed.
    """
    return [cell_content_hash(c.header) for c in cells if c.metadata.is_j2]


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


def _idless_localized_hashes(cells: list[Cell], lang: str) -> list[str]:
    """Ordered content hashes of the id-less localized cells of ``lang`` (Issue #269).

    The ``("L", kind)`` set the structural pass owns: a ``lang=``-bearing cell with
    no per-cell role (``role_of`` ``None`` — i.e. no ``slide_id`` and no narrative
    tag). The keyed walk skips it (no slide_id) and :func:`align_anchored` skips it
    (not in the ``shared`` partition), so this ordered sequence is what a one-sided
    drift detector compares — the localized analog of :func:`_shared_hashes`.
    """
    return [
        cell_content_hash(c.content)
        for c in cells
        if not c.metadata.is_j2 and c.metadata.lang == lang and role_of(c.metadata) is None
    ]


def _idless_localized_baseline_from_rows(
    rows: list[tuple[int, str | None, str, str, str | None]],
) -> list[str]:
    """Ordered id-less localized baseline hashes from a watermark ``de``/``en`` partition.

    The membership-widened watermark files an id-less localized cell under the
    synthetic :data:`LOCALIZED_CODE_ROLE` / :data:`LOCALIZED_MARKDOWN_ROLE` role
    (its ``role_of`` is ``None``); an id-carrying cell keeps a real role. Selecting
    those two roles reproduces :func:`_idless_localized_hashes` over the baseline.
    """
    return [
        chash
        for (_pos, _sid, role, chash, _construct) in rows
        if role in (LOCALIZED_CODE_ROLE, LOCALIZED_MARKDOWN_ROLE)
    ]


def _classify_idless_localized_drift(
    plan: SyncPlan,
    de_cells: list[Cell],
    en_cells: list[Cell],
    de_baseline: list[str],
    en_baseline: list[str],
) -> None:
    """Feed a direction for a one-sided id-less localized drift, or alert (Issue #269).

    Compares each half's ordered id-less localized hashes against its baseline. A
    one-sided drift yields a propagation direction handed to the structural pass
    (which re-translates the changed cell via ``_region_has_localized_drift`` /
    signature rebuild); a two-sided drift, or one whose direction conflicts with the
    direction the rest of the pass already established (keyed proposals or the
    neutral anchor), is irreconcilable and surfaces as an error so the watermark
    holds and the user is alerted — never a silent drop.
    """
    de_drifted = _idless_localized_hashes(de_cells, "de") != de_baseline
    en_drifted = _idless_localized_hashes(en_cells, "en") != en_baseline
    if not de_drifted and not en_drifted:
        return
    # The direction the rest of the pass already established: a single keyed edit
    # direction if any, else the neutral anchor.
    established = _keyed_direction(plan)
    if established is None:
        established = plan.anchor_direction
    if de_drifted and en_drifted:
        # Both halves' id-less localized streams changed. When a direction is already
        # established (a keyed edit or the neutral anchor), the existing id-migration
        # + structural pass resolve these along it — e.g. a localized id'd code cell
        # split into import+def on BOTH decks, where the new id-less def appears on
        # each half. Only when there is NO direction signal at all is this a genuine
        # both-sides edit a single-direction sync cannot reconcile -> alert.
        if established is None:
            plan.issues.append(
                PlanIssue(
                    severity="error",
                    slide_id=None,
                    reason="id-less localized cells (lang= cells with no slide_id) were "
                    "edited on both decks; sync cannot determine a single direction — "
                    "resolve manually or assign slide_ids so the cells can be paired",
                )
            )
        return
    direction = "de->en" if de_drifted else "en->de"
    # Reconcile with the established direction. A conflict means the author edited
    # different cell classes in opposite directions — not safely applicable in one
    # pass, so alert rather than overwrite one side's edit.
    if established is not None and established != direction:
        plan.issues.append(
            PlanIssue(
                severity="error",
                slide_id=None,
                reason=f"an id-less localized cell drifted {direction} but other cells "
                f"drifted {established}; sync cannot apply both directions at once — "
                "resolve manually",
            )
        )
        return
    if plan.anchor_direction is None:
        plan.anchor_direction = direction


def _classify_header_drift(
    plan: SyncPlan,
    de_cells: list[Cell],
    en_cells: list[Cell],
    de_baseline: list[str],
    en_baseline: list[str],
) -> None:
    """Alert on a one-sided j2 deck-header edit (Issue #269).

    The deck header is language-specific (``header_de`` vs ``header_en``) and is
    excluded from every sync partition, so sync neither translates nor propagates
    it — the structural pass deliberately keeps each half's own header. That is
    correct behavior, but it must NOT be reported as "decks already consistent"
    when one half's header was edited and the other's was not. Compare each half's
    header against its baseline: a one-sided drift is an error so the watermark
    holds, the run exits non-zero, and the user is told to update the other header
    (or run ``clm slides translate``). A both-sided drift is treated as "both
    halves were updated" (no alert); neither-side is a no-op.

    An error rather than a warning because (a) sync genuinely cannot resolve it and
    (b) only an error makes ``is_noop`` False and the summary non-"consistent" — a
    warning would still report the decks consistent and exit 0.
    """
    de_drifted = _header_hashes(de_cells) != de_baseline
    en_drifted = _header_hashes(en_cells) != en_baseline
    if de_drifted == en_drifted:
        return  # neither changed, or both updated — no one-sided header divergence
    side, other = ("de", "en") if de_drifted else ("en", "de")
    plan.issues.append(
        PlanIssue(
            severity="error",
            slide_id=None,
            reason=f"the {side.upper()} deck header changed since the last sync but the "
            f"{other.upper()} header did not — sync does not auto-translate the deck "
            f"header; update the {other.upper()} header to match (or run "
            "`clm slides translate`), then re-run sync",
        )
    )


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


def _conflicts_with_keyed(plan: SyncPlan, direction: str | None) -> bool:
    """Whether ``direction`` opposes the plan's single keyed propagation direction.

    ``True`` only when both a keyed direction (a move/edit proposal) and
    ``direction`` are present and they disagree — the Issue #282 conflict where a
    structural change flows one way and a language-neutral edit the other. A
    missing keyed direction (no keyed proposals, or keyed proposals both ways) or
    a matching one is not a conflict.
    """
    if direction is None:
        return False
    keyed = _keyed_direction(plan)
    return keyed is not None and keyed != direction


def _grouped_neutral_map(cells: list[Cell]) -> dict[str | None, list[str]]:
    """Map ``group_slide_id -> ordered list of neutral content hashes in that group``.

    Associates each language-neutral (``shared``) cell with the ``slide_id`` of the
    slide group it sits in (``None`` for the head, before the first slide). A GROUP
    reorder moves whole groups as units — it never reorders cells *within* a group nor
    changes which group a neutral cell belongs to — so comparing two halves' maps with
    ``==`` is invariant to a one-sided group reorder (dict key order is irrelevant) yet
    sensitive to (a) a cross-group reassignment (the cell appears under a different
    key), (b) an intra-group neutral reorder (the per-group list is ordered), and (c)
    an edit / add / remove (the list contents change). A flat multiset misses (a) and
    (b); a flat ordered compare is fooled by the group reorder. The ``shared``
    predicate (non-j2, language-neutral) matches :func:`watermark_rows`.
    """
    out: dict[str | None, list[str]] = {}
    group_sid: str | None = None
    for cell in cells:
        meta = cell.metadata
        if meta.is_slide_start:
            group_sid = meta.slide_id
        if meta.is_j2 or meta.lang in ("de", "en"):
            continue
        out.setdefault(group_sid, []).append(cell_content_hash(cell.content))
    return out


def _classify_move_target_edit_conflict(
    plan: SyncPlan,
    de_cells: list[Cell],
    en_cells: list[Cell],
    baseline_shared: list[str] | None,
    de_idless_baseline: list[str] | None,
    en_idless_baseline: list[str] | None,
) -> None:
    """Alert when a one-sided group reorder coincides with an opposite-half edit (Issue #282).

    A single-direction ``move`` proposal means one half reordered its slide GROUPS.
    A reorder makes positional pairing of language-neutral / id-less-localized cells
    UNSOUND (the same reason the keyed walk refuses positional pairing under a move,
    `_maybe_retag`): the structural pass (:func:`apply_code_structure`) rebuilds each
    group's contents from the reordering (source) half and the per-cell detectors
    (:func:`align_anchored`, :func:`_classify_idless_localized_drift`) compare the now
    permuted sequences positionally. So ANY concurrent change the OTHER (target) half
    made to its own neutral / id-less cells — a body edit, an add/remove, or a
    cross-group reassignment — is mis-read or shadowed and silently overwritten with
    the source's version. Observed failure modes (Issue #282 review):

    - the target's edit is dropped and the watermark advances (no issue at all);
    - the positional collision is mis-classified as a same-cell §7a divergence and
      *auto-healed*, overwriting the target edit on disk with only a warning;
    - a cross-group reassignment (content multiset unchanged) is rebuilt away.

    All are #269 cardinal-invariant violations. Rather than chase each positional
    artifact, detect the precondition directly: the target half (which did NOT
    reorder — there is a single move direction) changed its neutral or id-less
    content relative to its OWN baseline. Because the target's group order is
    unchanged, that comparison is an ordinary positional compare and is itself sound
    — and it is exhaustive (edit / add / remove / reassignment all perturb the
    ordered sequence). Raise an error so the watermark holds and the buffered flush
    writes nothing, leaving both halves intact on disk for manual reconciliation.

    The legitimate same-direction merge — the author edits ONE half (reordering its
    groups *and* editing its content) while the other half is untouched — never
    fires: there the target half equals its baseline. A no-op when a per-cell
    detector already alerted (``plan.has_errors``), for one clear error per scenario.
    """
    if plan.has_errors:
        return
    move_dirs = {p.direction for p in plan.proposals if p.kind == "move"}
    if len(move_dirs) != 1:
        return  # no move, or moves both ways (ambiguous — deferred elsewhere)
    move_dir = next(iter(move_dirs))
    # The move flows source->target; only the SOURCE half reordered, so the TARGET
    # half's group order still matches baseline and an ordered compare is sound.
    if move_dir == "de->en":
        tgt_neutral = _shared_hashes(en_cells)
        tgt_idless, tgt_idless_base = _idless_localized_hashes(en_cells, "en"), en_idless_baseline
        target = "en"
    else:
        tgt_neutral = _shared_hashes(de_cells)
        tgt_idless, tgt_idless_base = _idless_localized_hashes(de_cells, "de"), de_idless_baseline
        target = "de"

    # Neutral cells are shared across the halves, so the target's baseline IS
    # ``baseline_shared``. A difference (ordered) means the target half edited,
    # added, removed, or re-associated a neutral cell. But that is only a *conflict*
    # if the two halves actually DISAGREE on per-group neutral content: a neutral
    # edit applied identically to BOTH halves keeps the ``unify`` invariant (de == en),
    # so a reorder cannot clobber it — there is nothing one-sided to lose. Gate on a
    # reorder-invariant per-group comparison (a flat hash multiset is blind to a
    # cross-group reassignment; a flat ORDERED compare is fooled by the reorder).
    neutral_conflict = (
        baseline_shared is not None
        and tgt_neutral != baseline_shared
        and _grouped_neutral_map(de_cells) != _grouped_neutral_map(en_cells)
    )
    # Id-less localized cells are per-language (translated), so the two halves cannot
    # be compared directly; a target-side change from its own baseline is treated as a
    # conflict (the structural pass would re-translate over it). Conservative by design.
    idless_changed = tgt_idless_base is not None and tgt_idless != tgt_idless_base
    if neutral_conflict or idless_changed:
        plan.issues.append(
            PlanIssue(
                severity="error",
                slide_id=None,
                reason=f"slide groups were reordered {move_dir} but the {target} half also "
                "changed a language-neutral or id-less-localized cell; a group reorder and a "
                "concurrent edit on the other half cannot be reconciled in one pass (positional "
                "pairing is unsound across a reorder) — apply both on the same half, or sync "
                "them in separate steps, then re-run",
            )
        )


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
    # The ``.de`` / ``.en`` tag sits before the final extension
    # (``foo.de.cs`` -> "de"), so this is prefix- and extension-agnostic
    # across .py/.cs/.cpp/.java/.ts split halves.
    parts = path.name.split(".")
    if len(parts) >= 3 and parts[-2] in ("de", "en"):
        return parts[-2]
    return None


def _git_show(cwd: Path, spec: str) -> str | None:
    """``git show <spec>`` run in ``cwd``, or ``None`` on any failure."""
    try:
        completed = subprocess.run(
            ["git", "show", spec],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _git_historical_paths(path: Path, ref: str) -> list[str]:
    """Repo-root-relative names ``path`` has had through history, newest first.

    Follows git rename detection (``git log --follow -M``) so a deck that was
    renamed (or whose content git tracks across a rename) can be located at an
    arbitrary ``ref`` even though its *current* name did not exist there. Each
    returned name is repo-root-relative (``git show <ref>:<name>`` addresses it
    directly). Empty when git is unavailable or the file has no tracked history.
    ``ref`` is accepted for symmetry but does not constrain the walk — the caller
    tries every historical name at ``ref`` and keeps the one that resolves (at any
    given commit the file exists under exactly one of them).
    """
    try:
        completed = subprocess.run(
            ["git", "log", "--follow", "-M", "--name-only", "--format=", "--", path.name],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return []
    if completed.returncode != 0:
        return []
    names: list[str] = []
    for line in completed.stdout.splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return names


def _git_ref_text(path: Path, ref: str = "HEAD") -> str | None:
    """The text of ``path`` at git ``ref`` (default ``HEAD``), or ``None``.

    ``None`` when git is unavailable, the file is untracked at ``ref`` (even after
    following renames), the ref does not resolve, or ``git show`` fails. ``ref`` may
    be any revision spec (``HEAD~1``, a commit SHA, ``origin/master``, …) — this
    powers both the git-HEAD baseline fallback and the explicit ``--baseline <ref>``
    flag.

    Issue #2: a deck renamed since ``ref`` does not exist there under its *current*
    name, which used to degrade silently to ``baseline=none``. We first try the
    current name (the fast, common path), then fall back to each name the file has
    had through history (rename-following), so a rename no longer hides the
    baseline. A topic *split* (one file becoming several) is not a git rename and is
    not recovered here — the caller surfaces an explicit diagnostic instead of
    degrading silently.
    """
    text = _git_show(path.parent, f"{ref}:./{path.name}")
    if text is not None:
        return text
    for root_rel in _git_historical_paths(path, ref):
        text = _git_show(path.parent, f"{ref}:{root_rel}")
        if text is not None:
            return text
    return None


def _git_head_text(path: Path) -> str | None:
    """The committed (HEAD) text of ``path`` — thin wrapper over :func:`_git_ref_text`.

    Shared by the per-cell baseline (:func:`_baseline_from_git_head`) and the
    shared/header baselines so they all read the *same* committed snapshot.
    """
    return _git_ref_text(path, "HEAD")


def _bundle_from_watermark(
    cache: SyncWatermarkCache, de_path: Path, en_path: Path
) -> BaselineBundle | None:
    """The pair's recorded watermark as a :class:`BaselineBundle`, or ``None``.

    A straight read of the partitions :func:`clm.slides.sync_apply._record_watermark`
    stores. ``None`` when the pair has no watermark (the caller then falls back to
    git HEAD). A pre-#198 watermark simply yields empty tag maps (direction
    degrades to undeterminable); a pre-#269 one yields empty header partitions
    (both halves read as drifted → no false one-sided alert).
    """
    if not cache.has_pair(str(de_path), str(en_path)):
        return None
    de, en = str(de_path), str(en_path)
    return BaselineBundle(
        source="watermark",
        rows={
            "de": cache.get_deck(de, en, "de"),
            "en": cache.get_deck(de, en, "en"),
            "shared": cache.get_deck(de, en, "shared"),
        },
        tags={
            "de": cache.get_deck_tags(de, en, "de"),
            "en": cache.get_deck_tags(de, en, "en"),
            "shared": cache.get_deck_tags(de, en, "shared"),
        },
        header_hashes={
            "de": [chash for (_p, _s, _r, chash, _c) in cache.get_deck(de, en, "de-header")],
            "en": [chash for (_p, _s, _r, chash, _c) in cache.get_deck(de, en, "en-header")],
        },
    )


def _bundle_from_git_ref(de_path: Path, en_path: Path, ref: str = "HEAD") -> BaselineBundle | None:
    """The pair at git ``ref`` re-derived as a :class:`BaselineBundle`, or ``None``.

    Derives **exactly** the rows a watermark recording of the ``ref`` text would
    store — the same :func:`watermark_rows` / :func:`watermark_tag_map` /
    :func:`_header_hashes` chokepoints ``_record_watermark`` uses — so every
    consumer downstream is source-agnostic by construction. The ``shared``
    partition is taken from the DE half (neutral cells are byte-identical across
    the halves — the ``unify`` invariant — exactly as ``_record_watermark``
    records it). ``None`` when git/the ``ref`` text is unavailable or a deck's
    language cannot be inferred from its name (the caller then runs with no
    baseline).

    ``source`` is ``"git-head"`` for the default HEAD fallback (unchanged) and
    ``"git:<ref>"`` for an explicit ``--baseline`` ref, so the plan headline
    names the baseline that was used.
    """
    if _lang_for_path(de_path) is None or _lang_for_path(en_path) is None:
        return None
    de_text = _git_ref_text(de_path, ref)
    en_text = _git_ref_text(en_path, ref)
    if de_text is None or en_text is None:
        return None
    de_head = parse_cells(de_text, comment_token_for_path(de_path))
    en_head = parse_cells(en_text, comment_token_for_path(en_path))
    de_rows = watermark_rows(de_head)
    en_rows = watermark_rows(en_head)
    de_tags = watermark_tag_map(de_head)
    en_tags = watermark_tag_map(en_head)
    return BaselineBundle(
        source="git-head" if ref == "HEAD" else f"git:{ref}",
        rows={"de": de_rows["de"], "en": en_rows["en"], "shared": de_rows["shared"]},
        tags={"de": de_tags["de"], "en": en_tags["en"], "shared": de_tags["shared"]},
        header_hashes={"de": _header_hashes(de_head), "en": _header_hashes(en_head)},
    )


def _bundle_from_git_head(de_path: Path, en_path: Path) -> BaselineBundle | None:
    """The committed (HEAD) pair as a :class:`BaselineBundle` — wrapper over
    :func:`_bundle_from_git_ref`."""
    return _bundle_from_git_ref(de_path, en_path, "HEAD")


def _baseline_ref_unresolved_reason(de_path: Path, en_path: Path, ref: str) -> str | None:
    """Why an explicit ``--baseline <ref>`` could not be resolved, or ``None``.

    Issue #2: an explicit baseline ref that does not yield a usable bundle used to
    degrade silently to ``baseline=none`` (no edit detection), leaving the user to
    hand-derive a per-deck baseline. This names the concrete cause so the run says
    *why* edits could not be detected:

    - a deck's language cannot be inferred from its name (not a ``*.de.*`` /
      ``*.en.*`` split half), or
    - a deck is absent at ``ref`` even after following renames — typically because
      it was created or **split** out of another file after ``ref`` (a split is not
      a git rename, so it cannot be auto-followed; pass a ref where the current name
      exists, or sync with the recorded watermark).

    ``None`` when the ref resolves for both halves (the bundle would be usable).
    """
    if _lang_for_path(de_path) is None or _lang_for_path(en_path) is None:
        return (
            f"--baseline {ref}: cannot infer a language from the deck name(s) "
            "(expected a *.de.* / *.en.* split half) — baseline skipped"
        )
    missing = [p.name for p in (de_path, en_path) if _git_ref_text(p, ref) is None]
    if missing:
        return (
            f"--baseline {ref}: {', '.join(missing)} not found at that ref even after "
            "following renames (likely created or split out of another file since "
            f"{ref}) — edits cannot be detected against it. Pass a ref where the "
            "current name exists, or sync against the recorded watermark"
        )
    return None


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
    provider_available: bool = False,
) -> SyncPlan:
    """Diff both decks against their baselines into a typed :class:`SyncPlan`.

    Pure: no IO, no LLM. ``de_baseline`` / ``en_baseline`` are ``None`` when no
    baseline exists for that deck; if either is ``None`` the pair runs in the
    limited cold-start path (id-less adds + shared-id pairing only).

    ``provider_available`` (#228) is the plan-time fact "a correspondence verifier
    will be available at apply time"; when true, a both-directions committed
    mismatched-id bucket that is the whole actionable plan is upgraded to
    ``reconcile`` candidates instead of ``refuse`` (:func:`_refuse_idcarrying_mismatched`).
    A marker only — no LLM is called here; the verifier runs in apply.
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
                # Issue #4: a both-edited cell is NOT a conflict when the two halves
                # now hash identically — both were corrected to the same value (a
                # shared/identically-edited cell), so they are already in sync and
                # need no action. (Localized cells differ by language and so never
                # hit this exact-equality path; their translation-equivalence is
                # checked at apply time via the judge's in_sync verdict.)
                if de_now.content_hash == en_now.content_hash:
                    plan.in_sync_count += 1
                else:
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
    _refuse_idcarrying_mismatched(plan, de_base, en_base, baseline_source, provider_available)
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


def _reconcile(source: Proposal) -> Proposal:
    """A ``reconcile`` candidate standing in for an ambiguous add we will *resolve* (#228).

    The strategy-B premium of :func:`_refuse`: instead of declining the suspect, mark
    it for apply-time correspondence verification. Keeps the source cell's identity
    (direction / slide_id / role / source_position) so apply can locate the cell, build
    the cross-language candidate pairs, and — for a confirmed twin — rewrite the
    divergent id (EN-authority). ``disposition == "pending"`` mirrors mint/adopt: the
    dry-run discloses it, and apply confirms before any id reaches disk.
    """
    return Proposal(
        kind="reconcile",
        role=source.role,
        direction=source.direction,
        slide_id=source.slide_id,
        reason="mismatched-id twins (committed) — pending correspondence verification "
        "and id rewrite (#228)",
        source_position=source.source_position,
        disposition="pending",
    )


def _replace_adds_with_reconcile(plan: SyncPlan, doomed: list[Proposal]) -> None:
    """Swap the ``doomed`` ambiguous add proposals out of the plan for ``reconcile`` items."""
    doomed_ids = {id(p) for p in doomed}
    plan.proposals = [p for p in plan.proposals if id(p) not in doomed_ids]
    plan.proposals.extend(_reconcile(p) for p in doomed)


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
    provider_available: bool = False,
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
    from a genuinely-distinct slide — only cross-language content correspondence can —
    so the conservative default refuses the whole ambiguous set rather than guess.
    (Considering the buckets separately misses the *mixed* case, where the id'd half
    and the id-less half each flow in a single — but opposite — direction.)

    Strategy B (#228): when ``provider_available`` and the ambiguous bucket is the
    **whole actionable plan**, the set is upgraded to ``reconcile`` candidates
    (:func:`_replace_adds_with_reconcile`) instead of refusing — apply then confirms a
    twin by content correspondence and rewrites the divergent id. Any other case (no
    provider, a coexisting proposal, or a plan issue/error) keeps the refusal.

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
    # Strategy B (#228): with a provider/verifier available, *reconcile* a confirmed
    # mismatched twin (rewrite the divergent id) instead of refusing — but only when the
    # ambiguous bucket IS the whole actionable plan (the canonical partial-overlap shape:
    # a shared in-sync slide with no proposal, plus the mismatched twins). The apply-time
    # reconcile is a whole-plan short-circuit (like mint/adopt), so a coexisting
    # edit/add/move/conflict/issue would be skipped by it — those cases keep refusing
    # (strategy A) and the author reconciles in a two-step sync. ``provider_available`` is
    # a plan-time fact (identical in dry-run and apply), so the two agree on
    # reconcile-vs-refuse.
    if (
        provider_available
        and not plan.has_errors
        and not plan.issues
        and len(ambiguous) == len(plan.proposals)
    ):
        _replace_adds_with_reconcile(plan, ambiguous)
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
    de_rows: list[tuple[int, str | None, str, str, str | None]],
    en_rows: list[tuple[int, str | None, str, str, str | None]],
    de_base_tags: dict[int, frozenset[str]],
    en_base_tags: dict[int, frozenset[str]],
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
    one-sided-drift rule (:func:`_retag_direction`) against the baseline's recorded
    tag set, emitting an id-less ``retag`` the apply targets by position.

    ``de_rows``/``en_rows`` and ``de_base_tags``/``en_base_tags`` are the
    membership-widened baseline rows + per-position tag sets, from **either**
    baseline source: the watermark, or — Issue #289 — re-derived from the committed
    git-HEAD text via :func:`watermark_rows` / :func:`watermark_tag_map`, so the
    first sync of a committed pair mirrors an id-less tag edit instead of silently
    dropping it (the pre-#289 ``source == "watermark"`` gate). A pre-#198 watermark
    row simply has no recorded tag set, so direction degrades to undeterminable.

    Conservative by construction — any doubt declines either the whole pass or the
    individual cell rather than risk mirroring a tag onto the wrong cell:

    - **no ``move``**: a reorder invalidates LIVE positional pairing — a tag drift
      under a move is mirrored via the reorder-invariant baseline-twin route, or
      alerted (:func:`_classify_idless_retags_under_move`, #285) — never skipped;
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
        # A reorder this pass — CURRENT-position pairing is unsound. Mirror via
        # the reorder-invariant hash/baseline-position route instead (#285); a
        # drift that route cannot mirror safely is alerted, never dropped.
        _classify_idless_retags_under_move(
            plan, de_cells, en_cells, de_rows, en_rows, de_base_tags, en_base_tags
        )
        return
    de_loc = _localized_lang_cells(de_cells, "de")
    en_loc = _localized_lang_cells(en_cells, "en")
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


# Proposal kinds that make the under-move tag mirror unsafe: any of these can
# reshape a localized stream between plan time and the retag's apply step (a
# remove shifts the current positions the id-less retag applier targets), so a
# pass carrying one keeps the conservative alert instead of mirroring.
_MOVE_RETAG_BLOCKERS = frozenset(
    {"add", "remove", "rename", "conflict", "refuse", "mint", "adopt", "reconcile"}
)


def _classify_idless_retags_under_move(
    plan: SyncPlan,
    de_cells: list[Cell],
    en_cells: list[Cell],
    de_rows: list[tuple[int, str | None, str, str, str | None]],
    en_rows: list[tuple[int, str | None, str, str, str | None]],
    de_base_tags: dict[int, frozenset[str]],
    en_base_tags: dict[int, frozenset[str]],
) -> None:
    """Mirror a tag-only id-less edit across a group reorder, or alert (#285).

    Tier C's live positional pairing is unsound under a move, but the **baseline**
    still provides a sound, reorder-invariant join:

    1. the drifted cell is located in its own half by **unique body hash**
       against its own baseline (a tag-only edit never changes the body);
    2. its **baseline position** indexes the twin: at the last sync the two
       localized streams were positional twins (the invariant the no-move path
       asserts live; verified here on the recorded rows), so the same position
       in the *other* half's baseline names the twin's body hash;
    3. that hash locates the twin in the other half's **current** (reordered)
       stream — hash-keyed, so the reorder cannot mis-pair it.

    Every lookup carries the recurring non-unique-anchor guard (the hash must be
    unique in baseline AND current, on the half where it is used). Anything the
    route cannot mirror — a duplicated body, a twin whose own body changed, tags
    drifted on *both* twins, misaligned baseline streams, or a pass that also
    carries a stream-reshaping proposal (:data:`_MOVE_RETAG_BLOCKERS` — a remove
    would shift the current positions the retag applier targets) — is **alerted**
    (error, watermark held, nothing written), never silently dropped: the pre-#289
    behavior was the #285 silent drop, the #290 fix alerted, this mirrors.
    """
    if plan.has_errors:
        return
    localized = (LOCALIZED_CODE_ROLE, LOCALIZED_MARKDOWN_ROLE)
    mirror_ok = not any(p.kind in _MOVE_RETAG_BLOCKERS for p in plan.proposals)
    # The cross-half join key is the BASELINE position — sound only if the
    # recorded streams are positional twins (same length, localized rows of the
    # same kind facing each other).
    aligned = len(de_rows) == len(en_rows) and all(
        (de_row[2] in localized) == (en_row[2] in localized)
        and (de_row[2] == en_row[2] if de_row[2] in localized else True)
        for de_row, en_row in zip(de_rows, en_rows, strict=True)
    )

    def _index_half(lang: str, cells: list[Cell], rows, base_tags):
        """Per half: baseline pos→hash (+uniqueness) and unique-hash→current cell."""
        idless_rows = [(pos, chash) for (pos, _sid, role, chash, _c) in rows if role in localized]
        base_counts = Counter(chash for _pos, chash in idless_rows)
        hash_by_pos = dict(idless_rows)
        stream = [c for c in cells if not c.metadata.is_j2 and c.metadata.lang == lang]
        cur_counts = Counter(
            cell_content_hash(c.content) for c in stream if role_of(c.metadata) is None
        )
        cell_by_hash: dict[str, tuple[int, Cell]] = {}
        for idx, c in enumerate(stream):
            if role_of(c.metadata) is not None:
                continue
            chash = cell_content_hash(c.content)
            if cur_counts[chash] == 1 and base_counts.get(chash, 0) <= 1:
                cell_by_hash[chash] = (idx, c)
        return base_counts, hash_by_pos, base_tags, cell_by_hash

    halves = {
        "de": _index_half("de", de_cells, de_rows, de_base_tags),
        "en": _index_half("en", en_cells, en_rows, en_base_tags),
    }

    def _locate(lang: str, pos: int):
        """``(drifted, tags_now, stream_idx, cell)`` at baseline ``pos``, or ``None``.

        ``None`` when the cell cannot be soundly located there: no recorded
        hash/tags at ``pos``, a non-unique body (baseline or current), or the
        body itself changed (the cell's hash no longer occurs — #282's domain).
        """
        base_counts, hash_by_pos, base_tags, cell_by_hash = halves[lang]
        chash = hash_by_pos.get(pos)
        recorded = base_tags.get(pos)
        if chash is None or recorded is None or base_counts[chash] != 1:
            return None
        entry = cell_by_hash.get(chash)
        if entry is None:
            return None
        idx, cell = entry
        now = frozenset(cell.metadata.tags)
        return (now != recorded, now, idx, cell)

    def _alert(reason: str) -> None:
        plan.issues.append(PlanIssue(severity="error", slide_id=None, reason=reason))

    # Duplicate-bodied cells defeat the hash anchor, so a tag drift among them
    # cannot be attributed to a cell — but it can still be DETECTED: for a hash
    # whose multiplicity is unchanged, the multiset of tag sets across the
    # duplicates must match the baseline's. A pure swap of identical cells keeps
    # the multiset (no false positive); a tag edit changes it → alert (the
    # pre-#285 paths skipped these silently, deferring to the validator). A
    # multiplicity change is a body add/remove — the body channel's domain.
    for lang, cells, rows, base_tags in (
        ("de", de_cells, de_rows, de_base_tags),
        ("en", en_cells, en_rows, en_base_tags),
    ):
        base_groups: dict[str, list[frozenset[str] | None]] = {}
        for pos, _sid, role, chash, _c in rows:
            if role in localized:
                base_groups.setdefault(chash, []).append(base_tags.get(pos))
        cur_groups: dict[str, list[frozenset[str]]] = {}
        for c in cells:
            if not c.metadata.is_j2 and c.metadata.lang == lang and role_of(c.metadata) is None:
                cur_groups.setdefault(cell_content_hash(c.content), []).append(
                    frozenset(c.metadata.tags)
                )
        for chash, base_tag_sets in base_groups.items():
            if len(base_tag_sets) < 2 or any(t is None for t in base_tag_sets):
                continue  # unique (the positional route's job) or pre-#198 rows
            now_tag_sets = cur_groups.get(chash, [])
            if len(now_tag_sets) != len(base_tag_sets):
                continue  # a duplicate was added/removed — body channel's domain
            if Counter(now_tag_sets) != Counter(base_tag_sets):
                _alert(
                    f"tags changed on duplicate-bodied id-less localized {lang} cells "
                    "while slide groups were reordered; identical bodies cannot anchor "
                    "a tag mirror (#285) — apply the tag change on both halves, then "
                    "re-run"
                )
                return

    positions = sorted(
        {pos for (pos, _s, role, _h, _c) in de_rows if role in localized}
        | {pos for (pos, _s, role, _h, _c) in en_rows if role in localized}
    )
    for pos in positions:
        de_loc = _locate("de", pos)
        en_loc = _locate("en", pos)
        de_drift = de_loc is not None and de_loc[0]
        en_drift = en_loc is not None and en_loc[0]
        if not de_drift and not en_drift:
            continue
        if de_drift and en_drift:
            _alert(
                f"tags of the id-less localized twin pair at baseline position {pos} "
                "changed on both decks while slide groups were reordered; "
                "reconcile the tags manually, then re-run"
            )
            return
        source_lang = "de" if de_drift else "en"
        target_lang = "en" if de_drift else "de"
        source = de_loc if de_drift else en_loc
        target = en_loc if de_drift else de_loc
        assert source is not None
        if not (mirror_ok and aligned) or target is None:
            _alert(
                f"tags changed on an id-less localized {source_lang} cell while slide "
                "groups were reordered, and the twin cannot be safely located "
                "(#285) — apply the tag change on both halves (or sync the reorder "
                "first), then re-run"
            )
            return
        _drifted, tags_now, source_idx, source_cell = source
        _t_drifted, _t_tags, target_idx, _target_cell = target
        kind_label = "code" if source_cell.metadata.cell_type == "code" else "markdown"
        plan.proposals.append(
            Proposal(
                kind="retag",
                role=kind_label,
                direction=f"{source_lang}->{target_lang}",
                slide_id=None,
                reason=f"tags changed on {source_lang.upper()} ({sorted(tags_now)}) — "
                "id-less localized cell, mirrored across a group reorder via its "
                "baseline twin (#285)",
                source_position=source_idx,
                target_position=target_idx,
                tags=tuple(source_cell.metadata.tags),
            )
        )


def _cell_first_line(body: str, limit: int = 48) -> str:
    """A short locatable excerpt of a cell body (its first non-blank line)."""
    for raw in body.split("\n"):
        line = raw.strip()
        if line.startswith("# "):
            line = line[2:].strip()
        elif line.startswith("// "):
            line = line[3:].strip()
        if line and line not in ("#", "//"):
            return line[:limit] + ("…" if len(line) > limit else "")
    return "(empty cell)"


def _grouped_neutral_tagged(
    cells: list[Cell],
) -> dict[str | None, list[tuple[str, frozenset[str], str]]]:
    """``group_slide_id -> ordered [(content_hash, tags, body)]`` of its neutral cells.

    The tag-carrying sibling of :func:`_grouped_neutral_map` (same group-keyed,
    reorder-invariant association, same ``shared`` predicate); the body rides along
    only so an alert can name the cell.
    """
    out: dict[str | None, list[tuple[str, frozenset[str], str]]] = {}
    group_sid: str | None = None
    for cell in cells:
        meta = cell.metadata
        if meta.is_slide_start:
            group_sid = meta.slide_id
        if meta.is_j2 or meta.lang in ("de", "en"):
            continue
        out.setdefault(group_sid, []).append(
            (cell_content_hash(cell.content), frozenset(meta.tags), cell.content)
        )
    return out


def _classify_neutral_tag_drift(plan: SyncPlan, de_cells: list[Cell], en_cells: list[Cell]) -> None:
    """Alert on a tag-only divergence of a language-neutral shared cell (Issue #289).

    A neutral cell is shared **verbatim** across the split halves — header
    included — so its tag set must match across de and en. Every body-channel
    detector hashes ``Cell.content`` (body only), so a one-sided tag-only edit was
    invisible: the run reported "decks already consistent" and the watermark
    advanced over the divergence (the #289 P9 silent drop; the watermark even
    records shared-partition tags, but nothing consumed them).

    Scoped to **equal-body groups**: a group whose neutral *hash sequence* differs
    across the halves is skipped entirely — body drift is owned by
    :func:`align_anchored` + the structural pass, whose region rebuild copies the
    source cell verbatim *including its header*, so a combined body+tag edit
    propagates there and must not be double-alerted here. Only when the bodies
    fully agree and the tags do not is the divergence un-propagatable by any
    existing path → error, watermark held, nothing written. Group-keyed like
    :func:`_grouped_neutral_map`, so a one-sided group reorder cannot mis-pair the
    comparison. Baseline-free by design (like the post-apply parity fail-safe): a
    standing tag asymmetry is a unify-invariant violation the run must not call
    "consistent", whoever caused it.
    """
    if plan.has_errors:
        return
    de_map = _grouped_neutral_tagged(de_cells)
    en_map = _grouped_neutral_tagged(en_cells)
    for key in sorted(de_map.keys() | en_map.keys(), key=lambda k: (k is None, k or "")):
        de_list = de_map.get(key, [])
        en_list = en_map.get(key, [])
        if [h for h, _t, _b in de_list] != [h for h, _t, _b in en_list]:
            continue  # body drift in this group — the body machinery owns it
        for (_h, de_tags, body), (_h2, en_tags, _b2) in zip(de_list, en_list, strict=True):
            if de_tags != en_tags:
                plan.issues.append(
                    PlanIssue(
                        severity="error",
                        slide_id=None,
                        reason="tags on a language-neutral (shared) cell differ between "
                        f"the halves (cell {_cell_first_line(body)!r}: "
                        f"de={sorted(de_tags)}, en={sorted(en_tags)}); sync does not "
                        "mirror neutral-cell tag edits — apply the tag change to both "
                        "halves, then re-run",
                    )
                )
                return


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
    "reconcile": 10,
}


def _proposal_sort_key(p: Proposal) -> tuple:
    return (
        _KIND_ORDER.get(p.kind, 11),
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
    baseline_ref: str | None = None,
) -> SyncPlan:
    """Resolve the baseline and classify the pair into a :class:`SyncPlan`.

    Baseline priority: ``baseline_ref`` (when given) → watermark → git HEAD →
    none (see module docstring). Reads the two files; writes nothing.

    ``baseline_ref`` (the ``--baseline`` flag) pins the baseline to an explicit
    git ref (``HEAD~1``, a SHA, …), bypassing the watermark and the HEAD
    fallback — the deterministic escape hatch for "I committed single-language
    edits before syncing": ``baseline_ref="HEAD~1"`` diffs against the pre-edit
    commit so the edits are seen. When the ref text is unavailable the plan has
    no baseline (it does NOT silently fall back to HEAD).

    ``provider_available`` (#216 Phase 3, design §12) is the plan-time fact "a
    correspondence verifier will be available at apply time" (an LLM provider is
    configured **and** ``--verify-cold-pairs`` is on). When true, a cold-start
    both-id-less refusal over a *unifiable* pair is upgraded to a ``pending`` mint
    candidate instead of a `refuse`; the verifier (stage 2b, in apply) confirms the
    halves correspond before a shared id is minted. Identical in dry-run and apply,
    so the two agree on `refuse`-vs-`pending`. It also drives strategy-B
    ``reconcile`` (#228): a committed mismatched-id bucket that is the whole
    actionable plan upgrades to ``reconcile`` candidates (handled in
    :func:`classify_changes`) instead of refusing.
    """
    de_cells = parse_cells(de_path.read_text(encoding="utf-8"), comment_token_for_path(de_path))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"), comment_token_for_path(en_path))
    de_current = ordered_sync_cells(de_cells, "de")
    en_current = ordered_sync_cells(en_cells, "en")

    de_baseline: list[BaselineCell] | None = None
    en_baseline: list[BaselineCell] | None = None
    baseline_shared: list[str] | None = None
    # Issue #269: ordered content hashes of each half's *id-less localized* cells
    # (the ("L", kind) set the structural pass owns). None when no baseline.
    de_idless_baseline: list[str] | None = None
    en_idless_baseline: list[str] | None = None
    # Issue #269: ordered content hashes of each half's j2 deck-header cells, for the
    # one-sided header-drift alert. None when no baseline.
    de_header_baseline: list[str] | None = None
    en_header_baseline: list[str] | None = None
    source = "none"

    # Resolve the baseline into the ONE representation every consumer reads
    # (#289 P1): the watermark when recorded, else git HEAD re-derived into the
    # identical membership-widened shape. Each per-baseline aspect below — the
    # keyed diff, the neutral/id-less/header drift sequences, Tier C retag, and
    # the apply-side passes (via plan.baseline_bundle) — is derived from the
    # bundle by ONE code path, so the two sources cannot diverge in coverage
    # (the parallel per-aspect git-HEAD plumbing this replaces is how the #269
    # baseline gaps and the #289 git-HEAD tag drop shipped).
    bundle: BaselineBundle | None = None
    if baseline_ref is not None:
        # Explicit --baseline ref: diff against this git ref, ignoring the
        # watermark, and do NOT fall back to HEAD if it is unavailable.
        if not _pair_is_unbootstrapped(de_current, en_current):
            bundle = _bundle_from_git_ref(de_path, en_path, baseline_ref)
    else:
        if watermark_cache is not None:
            bundle = _bundle_from_watermark(watermark_cache, de_path, en_path)
        if (
            bundle is None
            and allow_git_fallback
            and not _pair_is_unbootstrapped(de_current, en_current)
        ):
            bundle = _bundle_from_git_head(de_path, en_path)

    if bundle is not None:
        source = bundle.source
        de_baseline = _baseline_from_watermark(bundle.rows["de"], bundle.tags["de"])
        en_baseline = _baseline_from_watermark(bundle.rows["en"], bundle.tags["en"])
        # Ordered content hashes of the baseline's neutral cells (position order),
        # matching _shared_hashes — see align_anchored for why this is a sequence,
        # not an anchor map.
        baseline_shared = [
            chash for (_pos, _sid, _role, chash, _construct) in bundle.rows["shared"]
        ]
        de_idless_baseline = _idless_localized_baseline_from_rows(bundle.rows["de"])
        en_idless_baseline = _idless_localized_baseline_from_rows(bundle.rows["en"])
        de_header_baseline = bundle.header_hashes["de"]
        en_header_baseline = bundle.header_hashes["en"]

    plan = classify_changes(
        de_current,
        en_current,
        de_baseline,
        en_baseline,
        de_path=de_path,
        en_path=en_path,
        baseline_source=source,
        provider_available=provider_available,
    )
    # Carry the id-less localized baselines onto the plan so the structural pass
    # (hash-anchored drift detection) and the apply-time fail-safe can reach them
    # without re-deriving the baseline (Issue #269) — and the whole bundle, so the
    # apply engine's anchor-reuse / id-migration passes read the SAME baseline the
    # classifier diffed against (#289 P1).
    plan.idless_baseline_de = de_idless_baseline
    plan.idless_baseline_en = en_idless_baseline
    plan.baseline_bundle = bundle

    # Issue #2: an explicit --baseline ref that yielded no bundle used to degrade
    # silently to baseline=none. Surface the concrete reason (rename not followable,
    # split, un-inferrable language) so the user knows WHY edits went undetected
    # rather than discovering it by hand. Only for a bootstrapped pair (an
    # unbootstrapped pair legitimately has no baseline).
    if (
        baseline_ref is not None
        and bundle is None
        and not _pair_is_unbootstrapped(de_current, en_current)
    ):
        reason = _baseline_ref_unresolved_reason(de_path, en_path, baseline_ref)
        if reason is not None:
            plan.issues.append(PlanIssue(severity="warning", slide_id=None, reason=reason))

    # Item-2 (Phase 3a): detect a language-neutral code-only change the keyed
    # classifier cannot see, and hand its direction to the structural pass. Only
    # against a real (watermark) baseline; the keyed direction, when present,
    # already drives the structural pass, so the anchor direction is a *fallback*.
    #
    # Skipped when a single-direction group reorder (a ``move``) is present (Issue
    # #282): a reorder permutes the neutral-cell *sequence*, so ``align_anchored``'s
    # flat POSITIONAL compare is unsound — it mis-reads the permutation as a drift,
    # or cross-pairs two independently edited cells into a phantom §7a divergence and
    # auto-heals (overwriting one half's edit on disk). Under a reorder, neutral
    # reconciliation is governed instead by the reorder-invariant per-group signature
    # in :func:`_classify_move_target_edit_conflict` below.
    single_move = len({p.direction for p in plan.proposals if p.kind == "move"}) == 1
    if baseline_shared is not None and not single_move:
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
        elif _conflicts_with_keyed(plan, alignment.direction):
            # Issue #282 (non-move case): the neutral-cell drift flows the OPPOSITE way
            # to a keyed propagation direction (an add / edit / remove proposal — a
            # reorder is handled above). The structural pass keys on the keyed direction
            # (``_single_direction``) and would silently overwrite the neutral edit, so
            # alert and hold the watermark — as :func:`_classify_idless_localized_drift`
            # does for the id-less-localized analog.
            plan.issues.append(
                PlanIssue(
                    severity="error",
                    slide_id=None,
                    reason=f"a language-neutral cell drifted {alignment.direction} but "
                    f"other cells drifted {_keyed_direction(plan)}; sync cannot apply "
                    "both directions at once — resolve manually",
                )
            )
        else:
            plan.anchor_direction = alignment.direction

    # Issue #269: id-less localized cells (a ``lang=`` cell with no ``slide_id`` —
    # the ("L", kind) set) are reached by neither the keyed walk (no slide_id) nor
    # ``align_anchored`` (it inspects only the neutral ``shared`` partition). A
    # one-sided body edit to one therefore has no direction signal, so the structural
    # pass skips it and the change is dropped while the run reports "consistent".
    # Detect the drift against the baseline and feed a direction (or alert if it is
    # two-sided / conflicts with the other cells' direction). Runs on BOTH baselines.
    if de_idless_baseline is not None and en_idless_baseline is not None:
        _classify_idless_localized_drift(
            plan, de_cells, en_cells, de_idless_baseline, en_idless_baseline
        )

    # Issue #269: the j2 deck header is excluded from every sync partition and is
    # never auto-translated, so a one-sided header edit must be surfaced rather than
    # silently reported "consistent". Runs on BOTH baselines (a pre-#269 watermark
    # recorded no header rows -> empty baseline -> both halves read as "drifted" ->
    # no false one-sided alert, and the next clean sync records the headers).
    if de_header_baseline is not None and en_header_baseline is not None:
        _classify_header_drift(plan, de_cells, en_cells, de_header_baseline, en_header_baseline)

    # Issue #282: a one-sided group reorder (a `move`) on one half collides with an
    # opposite-side neutral / id-less content edit on the other. The positional drift
    # detectors above mis-read the reorder as a drift (or a §7a divergence), masking
    # the edit — a silent drop, or a destructive auto-heal for >=2 reordered neutral
    # cells. Detect it order-blind via multisets and alert. Runs after the per-cell
    # classifiers so it defers to (and dedups against) any error they already raised.
    _classify_move_target_edit_conflict(
        plan, de_cells, en_cells, baseline_shared, de_idless_baseline, en_idless_baseline
    )

    # Issue #289: a language-neutral cell is shared verbatim INCLUDING its header,
    # but every body-channel detector hashes the body only — so a one-sided
    # tag-only edit on a neutral cell was silently dropped (and baselined) while
    # the run reported "consistent". Alert on an equal-body / unequal-tags neutral
    # pair; a body-differing group is left to the body machinery, whose verbatim
    # region rebuild carries the header (and so the tags) across.
    if plan.has_baseline:
        _classify_neutral_tag_drift(plan, de_cells, en_cells)

    # Tier C (Issue #198 / #190 item 3): mirror a tag-only edit on an id-less
    # localized cell — the per-cell engine cannot key it (no slide_id) and the
    # body-hash classifier is blind to a tag change. Baseline rows + tags come
    # straight off the bundle (#289: both sources, so the first sync of a
    # committed pair mirrors the edit too). Appends id-less ``retag`` proposals,
    # then re-sorts so they interleave with the keyed plan.
    if bundle is not None:
        _classify_localized_idless_retags(
            de_cells,
            en_cells,
            bundle.rows["de"],
            bundle.rows["en"],
            bundle.tags["de"],
            bundle.tags["en"],
            plan,
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

    de_cells = parse_cells(de_path.read_text(encoding="utf-8"), comment_token_for_path(de_path))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"), comment_token_for_path(en_path))
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
