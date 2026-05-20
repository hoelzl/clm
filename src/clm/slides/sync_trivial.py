"""Auto-apply trivial sync proposals without prompting.

Phase 7 v2 follow-up of the slide-format-redesign. ``--apply --trivial``
fast-forwards the safe subset of LLM sync proposals — diffs that only
change end-of-line shape, or that move whitespace within a single line —
straight to disk. The remaining proposals fall through to the report (or
to :mod:`clm.slides.sync_walker` when ``--interactive`` is also passed)
so a human still reviews anything semantically interesting.

The bar for "trivial" is deliberately narrow:

1. *EOL-only*: both sides equal after CR/CRLF→LF normalisation and
   strip.
2. *Whitespace-only one-line diff*: same line count, exactly one line
   differs, and that line is equal once internal whitespace runs are
   collapsed and the line stripped.

Anything else — even a single non-whitespace character — falls back to
human review. That keeps a typo-fix or a tone change from sliding into
production via an autopilot.

The module reuses :mod:`clm.slides.sync_writeback`, so file writes share
the same byte-preserving cell-rewrite machinery as the interactive
walker (PR #110).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from clm.slides.sync_writeback import (
    FileState,
    record_snapshot,
    target_path_for_outcome,
)

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncSnapshotCache
    from clm.slides.sync import PairOutcome, SyncResult


logger = logging.getLogger(__name__)

__all__ = [
    "apply_trivial_proposals",
    "is_trivial_diff",
]


def is_trivial_diff(target_body: str, proposed_text: str) -> bool:
    """Return ``True`` iff ``proposed_text`` is a trivial replacement.

    See the module docstring for the exact rules. The check is
    intentionally conservative — anything not covered here defers to a
    human via the report or ``--interactive`` walker.
    """
    norm_target = _normalize(target_body)
    norm_proposed = _normalize(proposed_text)
    if norm_target == norm_proposed:
        return True

    target_lines = norm_target.split("\n")
    proposed_lines = norm_proposed.split("\n")
    if len(target_lines) != len(proposed_lines):
        return False

    differing = [(t, p) for t, p in zip(target_lines, proposed_lines, strict=True) if t != p]
    if len(differing) != 1:
        return False

    target_line, proposed_line = differing[0]
    return _collapse_ws(target_line) == _collapse_ws(proposed_line)


def apply_trivial_proposals(
    result: SyncResult,
    *,
    snapshot_cache: SyncSnapshotCache | None = None,
) -> list[PairOutcome]:
    """Auto-apply every ``update`` outcome whose diff is trivial.

    Mutates ``result`` in place: sets ``outcome.applied_trivially`` and
    bumps ``result.pairs_auto_applied`` for each write. Snapshot rows
    are written through to ``snapshot_cache`` when provided so the
    auto-applied side becomes the new last-known-synced state. Non-
    trivial outcomes are left untouched — the interactive walker (or a
    follow-up sync pass) handles them next.

    Returns the list of outcomes that were auto-applied. Useful for
    tests and for surfacing the per-pair detail in the JSON report.

    Failures (file write error, parse failure) are logged and the
    outcome is left as if non-trivial; callers can re-surface it via
    the walker or a re-run.
    """
    applied: list[PairOutcome] = []
    file_state: dict[Path, FileState] = {}

    for outcome in result.outcomes:
        if outcome.verdict != "update":
            continue
        if outcome.applied_trivially:
            continue
        if outcome.proposal is None:
            continue
        if not is_trivial_diff(outcome.target_body, outcome.proposal.proposed_text):
            continue

        target_path = target_path_for_outcome(outcome, result)
        proposed_text = outcome.proposal.proposed_text

        try:
            state = file_state.setdefault(target_path, FileState.load(target_path))
            state.replace_body(outcome, proposed_text)
            state.flush()
            record_snapshot(
                snapshot_cache,
                result=result,
                outcome=outcome,
                new_target_text=proposed_text,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a log + skip
            logger.warning(
                "trivial-apply failed for %s/%s on %s: %s",
                outcome.slide_id,
                outcome.role,
                target_path,
                exc,
            )
            continue

        outcome.applied_trivially = True
        result.pairs_auto_applied += 1
        applied.append(outcome)

    return applied


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Drop CR/CRLF distinctions and leading/trailing whitespace.

    Mirrors how ``Cell.content`` is shaped by ``parse_cells`` so the
    LLM-proposed text (which may carry editor-style trailing newlines)
    compares like-for-like against the slide_parser-stripped target.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


_WS_RE = re.compile(r"\s+")


def _collapse_ws(line: str) -> str:
    return _WS_RE.sub(" ", line).strip()
