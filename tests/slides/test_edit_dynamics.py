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
* **Work-list drift** — every ``(mutation, path)`` verdict is frozen via
  ``Mutation.expected``; ``test_no_drift`` fails *loudly* on any change (a sync
  regression, or a hardening fix flipping a row — then update the baseline). The
  original break-silent work-list has now been fully drained (all rows hardened
  to preserve or break-loud), so ``test_no_break_silent_rows_remain`` also guards
  that no *new* silent footgun creeps back in.

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


# The known silent footguns the harness must keep surfacing. Every one of the
# original break-silent rows has now been hardened to preserve or break-loud:
# the two voiceover Tier-1 breaks (``preserve``), ``commit-without-sync`` and
# ``commit-companion-divergence`` (``break-loud`` — the #162 validate detectives
# catch them), the two per-file assign-ids breaks + ``extract-per-language-twin-
# aware`` (``preserve`` — the #162 defensive twin-aware reuse), ``extract-then-
# split`` (``preserve`` — companion split/unify in lockstep), and finally
# ``build-merge-unmatched`` (``break-loud`` — the build consumer escalates a
# dropped narration to a BuildError that fails under ``--fail-on-error``). The
# catalogue is now **fully loud**: zero break-silent rows. This set is therefore
# empty, and ``test_no_break_silent_rows_remain`` enforces that no NEW silent
# footgun (asserted or not) creeps back in.
_KNOWN_SILENT_BREAKS: set[str] = set()


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


def test_no_break_silent_rows_remain(harness, outcomes):
    # The whole hardening arc's end state: every footgun in the catalogue is now
    # LOUD (preserve or break-loud). A NEW break-silent row — a fresh silent
    # footgun, or a regression that re-silences a hardened one — fails here even
    # if it's a non-asserted observe-only row that test_no_drift wouldn't catch.
    silent = sorted(o.name for o in outcomes if o.verdict == harness.BREAK_SILENT)
    assert silent == [], f"new/regressed break-silent footgun(s): {silent}\n" + harness.render(
        outcomes
    )
    assert _KNOWN_SILENT_BREAKS == set()  # the work-list is fully drained
