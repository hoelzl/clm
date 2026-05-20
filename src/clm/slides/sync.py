"""Cross-language sync for split-format slide pairs.

Phase 7 of the slide-format-redesign. After an author edits one half of
a split deck (``<deck>.de.py`` or ``<deck>.en.py``), this module walks
the DE/EN pair by ``slide_id``, asks the
:class:`clm.infrastructure.llm.ollama_client.SyncJudge` to propose any
needed update to the target side, and emits a unified diff.

For v1 the only supported mode is ``--dry-run`` — the module surfaces
proposals as diffs in the report; no writes happen. Interactive
apply/skip/edit and ``--apply --trivial`` modes are planned follow-ups
and not implemented here.

Design notes (see ``handover-slide-format-redesign-clm.md`` §3 Phase 7
in the PythonCourses repo and the proposal at
``docs/proposals/PYTHON_COURSES_REVAMP_NEXT_STEPS_2026-05-19.md`` §2):

- The :class:`~clm.infrastructure.llm.cache.SyncCache` memoizes
  ``(de_hash, en_hash, prompt_version) -> proposal``. Re-runs against an
  unchanged pair hit the cache and avoid LLM spend.
- The pair walker only syncs markdown ``slide`` / ``subslide`` and
  narrative ``voiceover`` / ``notes`` cells. Code cells are shared
  across split companions by design; their consistency is checked by
  the Phase-6 validator, not by sync.
- Direction is explicit via :attr:`SyncOptions.source_lang`. The user
  tells us which side was edited; we ask the judge to propose updates
  for the other side. A future version may auto-detect direction via
  the cache's last-known-synced state.
- Cells without a ``slide_id`` are skipped with an info-level warning —
  the assign-ids step must run first.
- A slide_id with mismatched cell counts on the two sides surfaces as a
  structural-mismatch issue and the slide_id is skipped (LLM cannot
  pair cells unambiguously).

Pilot instrumentation: :class:`SyncResult` exposes per-session counters
(``pairs_visited``, ``pairs_in_sync``, ``pairs_proposed``,
``pairs_error``, ``cache_hits``) so the PythonCourses pilot can quote a
real accept rate when the interactive walker lands.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clm.infrastructure.llm.ollama_client import OllamaError, SyncProposal
from clm.notebooks.slide_parser import Cell, parse_cells

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncCache
    from clm.infrastructure.llm.ollama_client import SyncJudge

logger = logging.getLogger(__name__)

__all__ = [
    "PairOutcome",
    "SyncIssue",
    "SyncOptions",
    "SyncResult",
    "sync_split_pair",
]

# Roles that participate in cross-language sync. Slide cells carry the
# heading/bullet content; narrative cells carry voiceover/notes prose.
# Code cells are intentionally excluded — they're shared by design.
_ROLE_TAGS = {
    "slide": "slide",
    "subslide": "subslide",
    "voiceover": "voiceover",
    "notes": "notes",
}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class SyncOptions:
    """Knobs for one sync pass.

    ``source_lang`` is the language the author just edited; ``target_lang``
    is implied to be the other. The judge is asked to propose updates for
    the target side based on the source side's content.

    ``judge`` is the mockable :class:`SyncJudge`. Passing ``None`` skips
    every LLM call — the pass still runs, every pair becomes an "LLM
    unavailable" error, and the report is still useful as a structural
    audit.

    ``cache`` is the optional :class:`SyncCache` for memoization.
    """

    source_lang: str  # "de" or "en"
    judge: SyncJudge | None = None
    cache: SyncCache | None = None

    @property
    def target_lang(self) -> str:
        return "en" if self.source_lang == "de" else "de"


@dataclass
class SyncIssue:
    """A structural problem that prevents syncing a slide_id."""

    slide_id: str
    severity: str  # "warning" / "error"
    reason: str
    de_count: int = 0
    en_count: int = 0


@dataclass
class PairOutcome:
    """The result of asking the judge about one paired cell."""

    slide_id: str
    role: str
    de_line: int  # 1-based line number of the DE cell header
    en_line: int  # 1-based line number of the EN cell header
    direction: str  # "de->en" or "en->de"
    verdict: str  # "in_sync" / "update" / "error"
    reason: str = ""
    proposal: SyncProposal | None = None
    diff: str = ""  # unified diff (target_body -> proposed_text), empty for in_sync/error
    error: str = ""  # non-empty when verdict == "error"
    cached: bool = False  # True when this came from SyncCache instead of fresh LLM
    de_hash: str = ""  # sha256 of the DE cell body (slide_parser-stripped)
    en_hash: str = ""  # sha256 of the EN cell body (slide_parser-stripped)


@dataclass
class SyncResult:
    """Outcome of one ``clm slides sync`` run.

    Counters are pilot instrumentation per the proposal §2 — the
    PythonCourses Phase D pilot's ship/cancel criterion is
    ``pairs_proposed / pairs_visited > 80% accept rate``. v1 cannot
    measure accept rate yet (no interactive walker), but the
    denominator is recorded so v2 can plug in.
    """

    de_path: Path
    en_path: Path
    outcomes: list[PairOutcome] = field(default_factory=list)
    issues: list[SyncIssue] = field(default_factory=list)
    pairs_visited: int = 0
    pairs_in_sync: int = 0
    pairs_proposed: int = 0
    pairs_error: int = 0
    cache_hits: int = 0
    # Interactive-walker accept counters (Phase 7 v2). Zero when the
    # walker did not run. ``pairs_accepted + pairs_skipped +
    # pairs_edited`` sums to the number of proposals the walker
    # actually reached; ``pairs_quit`` records proposals that were
    # left unvisited because the user quit.
    pairs_accepted: int = 0
    pairs_skipped: int = 0
    pairs_edited: int = 0
    pairs_quit: int = 0

    @property
    def has_proposals(self) -> bool:
        return self.pairs_proposed > 0

    @property
    def has_errors(self) -> bool:
        return self.pairs_error > 0

    @property
    def pairs_resolved(self) -> int:
        """Proposals the walker took action on (accept + skip + edit).

        Used by the pilot accept-rate computation:
        ``accept_rate = (pairs_accepted + pairs_edited) / pairs_resolved``.
        """
        return self.pairs_accepted + self.pairs_skipped + self.pairs_edited


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def sync_split_pair(
    de_path: Path,
    en_path: Path,
    options: SyncOptions,
) -> SyncResult:
    """Run a cross-language sync pass over a split DE/EN pair.

    Returns a :class:`SyncResult`. No files are modified — v1 is
    read-only. Cached proposals are written through to the LLM cache
    if ``options.cache`` is set (memoization side effect; not a
    content edit).
    """
    if options.source_lang not in ("de", "en"):
        raise ValueError(f"source_lang must be 'de' or 'en', got {options.source_lang!r}")

    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")

    de_cells = parse_cells(de_text)
    en_cells = parse_cells(en_text)

    de_index = _index_cells(de_cells, "de")
    en_index = _index_cells(en_cells, "en")

    result = SyncResult(de_path=de_path, en_path=en_path)
    seen_keys: set[tuple[str, str]] = set()

    for key in sorted(set(de_index) | set(en_index)):
        slide_id, role = key
        if key in seen_keys:
            continue
        seen_keys.add(key)

        de_cells_for_key = de_index.get(key, [])
        en_cells_for_key = en_index.get(key, [])

        if not de_cells_for_key and en_cells_for_key:
            result.issues.append(
                SyncIssue(
                    slide_id=slide_id,
                    severity="warning",
                    reason=f"role={role!r} exists only on the EN side; "
                    "no DE counterpart to sync against",
                    de_count=0,
                    en_count=len(en_cells_for_key),
                )
            )
            continue

        if de_cells_for_key and not en_cells_for_key:
            result.issues.append(
                SyncIssue(
                    slide_id=slide_id,
                    severity="warning",
                    reason=f"role={role!r} exists only on the DE side; "
                    "no EN counterpart to sync against",
                    de_count=len(de_cells_for_key),
                    en_count=0,
                )
            )
            continue

        if len(de_cells_for_key) != len(en_cells_for_key):
            result.issues.append(
                SyncIssue(
                    slide_id=slide_id,
                    severity="error",
                    reason=f"role={role!r} has {len(de_cells_for_key)} cells on the "
                    f"DE side but {len(en_cells_for_key)} on the EN side; "
                    "structural mismatch — pair this manually",
                    de_count=len(de_cells_for_key),
                    en_count=len(en_cells_for_key),
                )
            )
            continue

        for de_cell, en_cell in zip(de_cells_for_key, en_cells_for_key, strict=True):
            outcome = _sync_pair(
                slide_id=slide_id,
                role=role,
                de_cell=de_cell,
                en_cell=en_cell,
                options=options,
            )
            result.outcomes.append(outcome)
            result.pairs_visited += 1
            if outcome.cached:
                result.cache_hits += 1
            if outcome.verdict == "in_sync":
                result.pairs_in_sync += 1
            elif outcome.verdict == "update":
                result.pairs_proposed += 1
            elif outcome.verdict == "error":
                result.pairs_error += 1

    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _index_cells(cells: list[Cell], expected_lang: str) -> dict[tuple[str, str], list[Cell]]:
    """Group cells by ``(slide_id, role)`` for ``expected_lang`` only.

    Cells without a slide_id, without an explicit lang, or whose lang
    doesn't match ``expected_lang`` are silently skipped. Code, j2,
    and title-macro cells are also skipped (they aren't sync targets).
    """
    index: dict[tuple[str, str], list[Cell]] = {}
    for cell in cells:
        role = _role_for_cell(cell)
        if role is None:
            continue
        sid = cell.metadata.slide_id
        if not sid:
            continue
        if cell.metadata.lang != expected_lang:
            continue
        index.setdefault((sid, role), []).append(cell)
    return index


def _role_for_cell(cell: Cell) -> str | None:
    """Return the sync role for ``cell``, or ``None`` to skip.

    Roles: ``"slide"``, ``"subslide"``, ``"voiceover"``, ``"notes"``.
    """
    if cell.metadata.is_j2:
        return None
    if cell.metadata.cell_type != "markdown":
        return None
    for tag in cell.metadata.tags:
        role = _ROLE_TAGS.get(tag)
        if role is not None:
            return role
    return None


def _sync_pair(
    *,
    slide_id: str,
    role: str,
    de_cell: Cell,
    en_cell: Cell,
    options: SyncOptions,
) -> PairOutcome:
    de_body = de_cell.content
    en_body = en_cell.content
    de_hash = _hash(de_body)
    en_hash = _hash(en_body)

    if options.source_lang == "de":
        source_body, target_body = de_body, en_body
        direction = "de->en"
    else:
        source_body, target_body = en_body, de_body
        direction = "en->de"

    # Cache lookup — memoizes the LLM call for the same (de_hash, en_hash)
    # pair. The cache stores (direction, proposal_json); we re-honor the
    # cached direction so swapping ``--source-lang`` mid-session doesn't
    # silently mask a cached "en->de" proposal.
    if options.cache is not None and options.judge is not None:
        cached = options.cache.get(de_hash, en_hash, options.judge.prompt_version)
        if cached is not None:
            cached_direction, cached_payload = cached
            if cached_direction == direction:
                proposal = SyncProposal.from_json(cached_payload)
                return _outcome_from_proposal(
                    slide_id=slide_id,
                    role=role,
                    de_cell=de_cell,
                    en_cell=en_cell,
                    direction=direction,
                    proposal=proposal,
                    target_body=target_body,
                    cached=True,
                    de_hash=de_hash,
                    en_hash=en_hash,
                )

    if options.judge is None:
        return PairOutcome(
            slide_id=slide_id,
            role=role,
            de_line=de_cell.line_number,
            en_line=en_cell.line_number,
            direction=direction,
            verdict="error",
            error="no judge configured (LLM unavailable)",
            de_hash=de_hash,
            en_hash=en_hash,
        )

    try:
        proposal = options.judge.propose(
            source_body,
            target_body,
            source_lang=options.source_lang,
            target_lang=options.target_lang,
        )
    except OllamaError as exc:
        logger.info("sync judge failed on %s/%s: %s", slide_id, role, exc)
        return PairOutcome(
            slide_id=slide_id,
            role=role,
            de_line=de_cell.line_number,
            en_line=en_cell.line_number,
            direction=direction,
            verdict="error",
            error=str(exc),
            de_hash=de_hash,
            en_hash=en_hash,
        )

    if options.cache is not None:
        options.cache.put(
            de_hash,
            en_hash,
            options.judge.prompt_version,
            direction,
            proposal.to_json(),
        )

    return _outcome_from_proposal(
        slide_id=slide_id,
        role=role,
        de_cell=de_cell,
        en_cell=en_cell,
        direction=direction,
        proposal=proposal,
        target_body=target_body,
        cached=False,
        de_hash=de_hash,
        en_hash=en_hash,
    )


def _outcome_from_proposal(
    *,
    slide_id: str,
    role: str,
    de_cell: Cell,
    en_cell: Cell,
    direction: str,
    proposal: SyncProposal,
    target_body: str,
    cached: bool,
    de_hash: str,
    en_hash: str,
) -> PairOutcome:
    if proposal.verdict == "in_sync":
        return PairOutcome(
            slide_id=slide_id,
            role=role,
            de_line=de_cell.line_number,
            en_line=en_cell.line_number,
            direction=direction,
            verdict="in_sync",
            reason=proposal.reason,
            proposal=proposal,
            cached=cached,
            de_hash=de_hash,
            en_hash=en_hash,
        )

    # verdict == "update"
    target_label, proposed_label = _diff_labels(direction)
    diff = _build_diff(
        target_body=target_body,
        proposed_text=proposal.proposed_text,
        target_label=target_label,
        proposed_label=proposed_label,
    )
    return PairOutcome(
        slide_id=slide_id,
        role=role,
        de_line=de_cell.line_number,
        en_line=en_cell.line_number,
        direction=direction,
        verdict="update",
        reason=proposal.reason,
        proposal=proposal,
        diff=diff,
        cached=cached,
        de_hash=de_hash,
        en_hash=en_hash,
    )


def _diff_labels(direction: str) -> tuple[str, str]:
    if direction == "de->en":
        return ("current EN", "proposed EN")
    return ("current DE", "proposed DE")


def _build_diff(
    *,
    target_body: str,
    proposed_text: str,
    target_label: str,
    proposed_label: str,
) -> str:
    """Unified diff from current target to proposed replacement."""
    target_lines = target_body.splitlines(keepends=True)
    proposed_lines = proposed_text.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        target_lines,
        proposed_lines,
        fromfile=target_label,
        tofile=proposed_label,
        lineterm="",
    )
    return "\n".join(diff_lines)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
