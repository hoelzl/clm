"""Fast cross-command property backstop for the split + voiceover workflow.

Runs :mod:`scripts.edit_dynamics_harness` (synthetic arm: no corpus, no network)
and freezes its classification of every ``(mutation, command-path)`` as
**preserve / break-loud / break-silent**. This closes the gap the design doc
(§6, §11) flags: today the per-cell sync fixes run in CI, but no test exercises
the *editing dynamics* across commands — the silent footguns where a real author
loses data or diverges the two halves.

Two kinds of guard, both diagnosable:

* **Engine regression** — every ``sync`` mutation must stay ``preserve``. The
  safe funnel keeps both halves consistent; if a change makes it diverge, the
  ``sync``-path assertions fail with the offending row.
* **Work-list drift** — the known silent breaks (per-file ``assign-ids``,
  the voiceover seams, the missing commit gate) are frozen as ``break-silent``.
  When a hardening fix lands and flips one to ``preserve``, ``test_no_drift``
  fails *loudly* — the signal to update ``Mutation.expected`` in the harness.

Pure-synthetic, so it runs in the fast suite (no markers). The real-deck arm
(mutating corpus pairs) stays out of CI, like ``test_sync_corpus_noop``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def harness():
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    import edit_dynamics_harness as h

    return h


@pytest.fixture(scope="module")
def outcomes(harness):
    """Run the catalogue once; all assertions share the single result."""
    return harness.run()


# The known silent footguns the harness must keep surfacing — a refactor that
# zeroes the work-list out (e.g. by neutering a runner) should fail here, not
# quietly report "all green".
_KNOWN_SILENT_BREAKS = {
    "add-then-assign-ids-per-file",
    "born-split-assign-ids",
    "commit-without-sync",
    "extract-then-split",
    "inline-after-rename",
    "re-extract-over-edited-companion",
}


def test_harness_runs_without_errors(harness, outcomes):
    # An ERROR verdict means the harness (or the engine) raised — never a
    # legitimate classification. Surface it, don't tolerate it.
    errored = [o for o in outcomes if o.verdict == harness.ERROR]
    assert errored == [], harness.render(outcomes)


def test_no_drift(harness, outcomes):
    # THE backstop: every asserted row matches its frozen baseline verdict. A
    # drift is either a sync regression (preserve -> break) or a hardening fix
    # landing (break-silent -> preserve). Both demand attention; on a fix, update
    # the row's ``expected`` in scripts/edit_dynamics_harness.py.
    drift = harness.asserted_drift(outcomes)
    assert drift == [], harness.render(outcomes)


def test_sync_funnel_always_preserves(harness, outcomes):
    # The architectural invariant (design §5): structural edits routed through
    # ``sync`` keep slide_id parity across both halves. This is the engine
    # regression guard, stated per-path for a diagnosable failure.
    offenders = [
        (o.name, o.verdict, o.detail)
        for o in outcomes
        if o.path == "sync" and o.verdict != harness.PRESERVE
    ]
    assert offenders == [], f"sync funnel no longer preserves correspondence: {offenders}"


def test_voiceover_round_trips_preserve(harness, outcomes):
    by_name = {o.name: o for o in outcomes}
    for name in ("extract-inline-round-trip", "unify-split-round-trip"):
        o = by_name[name]
        assert o.verdict == harness.PRESERVE, f"{name}: {o.verdict} — {o.detail}"


def test_known_silent_breaks_still_surfaced(harness, outcomes):
    # Guard the work-list itself: the documented footguns must keep classifying
    # as break-silent until they are actually hardened (then test_no_drift fires
    # and the baseline is updated). Prevents a silent loss of coverage.
    silent = {o.name for o in outcomes if o.verdict == harness.BREAK_SILENT}
    missing = _KNOWN_SILENT_BREAKS - silent
    assert missing == set(), (
        f"these known silent breaks stopped being detected: {sorted(missing)}\n"
        + harness.render(outcomes)
    )
