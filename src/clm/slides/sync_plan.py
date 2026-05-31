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

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clm.notebooks.slide_parser import Cell, parse_cells
from clm.slides.sync_writeback import cell_content_hash

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncWatermarkCache

__all__ = [
    "BaselineCell",
    "CurrentCell",
    "PlanIssue",
    "Proposal",
    "SyncPlan",
    "build_sync_plan",
    "classify_changes",
    "ordered_sync_cells",
    "render_plan",
]

# Roles that participate in cross-language sync. Mirrors
# ``clm.slides.sync._ROLE_TAGS`` (duplicated to keep this module independent of
# the engine module and avoid a circular import).
_ROLE_TAGS = {
    "slide": "slide",
    "subslide": "subslide",
    "voiceover": "voiceover",
    "notes": "notes",
}


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


@dataclass(frozen=True)
class CurrentCell:
    """One sync-relevant cell as it exists in the working tree right now."""

    position: int  # index among sync-relevant cells of this deck's language
    slide_id: str | None
    role: str
    content_hash: str
    line_number: int  # 1-based header line, for anchoring / messaging


@dataclass
class Proposal:
    """One cross-language change the sync would make.

    ``kind`` is ``add`` / ``edit`` / ``move`` / ``remove`` / ``conflict``.
    ``direction`` is ``"de->en"`` / ``"en->de"`` (the side that drifted is the
    source), or ``None`` for a conflict. ``slide_id`` is ``None`` for an
    id-less add. Positions are 0-based indices among sync-relevant cells and
    are best-effort context for later phases (anchoring, walker rendering).
    """

    kind: str
    role: str
    direction: str | None
    slide_id: str | None
    reason: str = ""
    translation_pending: bool = False  # True for ``add`` (content not yet made)
    source_position: int | None = None
    target_position: int | None = None
    old_position: int | None = None
    new_position: int | None = None


@dataclass
class PlanIssue:
    """A structural situation the classifier will not turn into a proposal."""

    severity: str  # "warning" | "error"
    slide_id: str | None
    reason: str


@dataclass
class SyncPlan:
    """The full result of classifying one split pair against its baseline."""

    de_path: Path
    en_path: Path
    baseline_source: str  # "watermark" | "git-head" | "none"
    proposals: list[Proposal] = field(default_factory=list)
    issues: list[PlanIssue] = field(default_factory=list)
    in_sync_count: int = 0

    @property
    def has_baseline(self) -> bool:
        return self.baseline_source != "none"

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def is_noop(self) -> bool:
        """True when there is nothing to apply (and nothing went wrong)."""
        return not self.proposals and not self.has_errors

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
                f"{self.count('move')} move",
                f"{self.count('remove')} remove",
                f"{self.count('conflict')} conflict",
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
    if cell.metadata.is_j2:
        return None
    if cell.metadata.cell_type != "markdown":
        return None
    for tag in cell.metadata.tags:
        role = _ROLE_TAGS.get(tag)
        if role is not None:
            return role
    return None


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
            )
        )
        position += 1
    return out


def _baseline_from_watermark(
    rows: list[tuple[int, str | None, str, str]],
) -> list[BaselineCell]:
    return [
        BaselineCell(position=pos, slide_id=sid, role=role, content_hash=chash)
        for (pos, sid, role, chash) in rows
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
        )
        for c in ordered_sync_cells(cells, lang)
    ]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _index_by_key(
    cells: list[CurrentCell],
) -> tuple[dict[tuple[str, str], CurrentCell], list[CurrentCell], set[tuple[str, str]]]:
    """Index id-carrying cells by ``(slide_id, role)``.

    Returns ``(by_key, idless, collisions)``. ``collisions`` holds keys that
    appear more than once (duplicate id in one deck) — those keys are excluded
    from normal pairing and surfaced as an error issue by the caller.
    """
    by_key: dict[tuple[str, str], CurrentCell] = {}
    collisions: set[tuple[str, str]] = set()
    idless: list[CurrentCell] = []
    for cell in cells:
        if cell.slide_id is None:
            idless.append(cell)
            continue
        key = (cell.slide_id, cell.role)
        if key in by_key:
            collisions.add(key)
            continue
        by_key[key] = cell
    return by_key, idless, collisions


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

    de_by_key, de_idless, de_collisions = _index_by_key(de_current)
    en_by_key, en_idless, en_collisions = _index_by_key(en_current)

    for key in sorted(de_collisions | en_collisions):
        sid, role = key
        side = "DE" if key in de_collisions else "EN"
        if key in de_collisions and key in en_collisions:
            side = "both decks"
        plan.issues.append(
            PlanIssue(
                severity="error",
                slide_id=sid,
                reason=f"role={role!r} slide_id appears more than once on {side}; "
                "ids must be unique within a deck — resolve the collision",
            )
        )

    has_baseline = de_baseline is not None and en_baseline is not None
    if not has_baseline:
        _classify_cold(
            plan, de_by_key, en_by_key, de_idless, en_idless, de_collisions | en_collisions
        )
        _append_idless_adds(plan, de_idless, en_idless)
        plan.proposals.sort(key=_proposal_sort_key)
        return plan

    de_base = _baseline_index(de_baseline)
    en_base = _baseline_index(en_baseline)

    excluded = de_collisions | en_collisions
    keys = (set(de_by_key) | set(en_by_key) | set(de_base) | set(en_base)) - excluded

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


_KIND_ORDER = {"conflict": 0, "remove": 1, "edit": 2, "move": 3, "add": 4}


def _proposal_sort_key(p: Proposal) -> tuple:
    return (
        _KIND_ORDER.get(p.kind, 9),
        p.source_position if p.source_position is not None else 1_000_000,
        p.slide_id or "",
        p.role,
    )


# ---------------------------------------------------------------------------
# IO wrapper + report
# ---------------------------------------------------------------------------


def build_sync_plan(
    de_path: Path,
    en_path: Path,
    *,
    watermark_cache: SyncWatermarkCache | None = None,
    allow_git_fallback: bool = True,
) -> SyncPlan:
    """Resolve the baseline and classify the pair into a :class:`SyncPlan`.

    Baseline priority: watermark → git HEAD → none (see module docstring).
    Reads the two files; writes nothing.
    """
    de_cells = parse_cells(de_path.read_text(encoding="utf-8"))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"))
    de_current = ordered_sync_cells(de_cells, "de")
    en_current = ordered_sync_cells(en_cells, "en")

    de_baseline: list[BaselineCell] | None = None
    en_baseline: list[BaselineCell] | None = None
    source = "none"

    if watermark_cache is not None and watermark_cache.has_pair(str(de_path), str(en_path)):
        de_baseline = _baseline_from_watermark(
            watermark_cache.get_deck(str(de_path), str(en_path), "de")
        )
        en_baseline = _baseline_from_watermark(
            watermark_cache.get_deck(str(de_path), str(en_path), "en")
        )
        source = "watermark"
    elif allow_git_fallback:
        gb_de = _baseline_from_git_head(de_path)
        gb_en = _baseline_from_git_head(en_path)
        if gb_de is not None and gb_en is not None:
            de_baseline, en_baseline = gb_de, gb_en
            source = "git-head"

    return classify_changes(
        de_current,
        en_current,
        de_baseline,
        en_baseline,
        de_path=de_path,
        en_path=en_path,
        baseline_source=source,
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
