"""Corpus-wide no-op backstop for ``clm slides sync`` (Issue #190, Phase 0).

Runs the read-only :mod:`scripts.sync_corpus_harness` over the real PythonCourses
split decks and asserts the **no-op invariant** every later #190 phase must
preserve: a plan with nothing to do applies as a true no-op — **zero bytes
written, zero LLM calls**. The harness operates on temp copies with a counting
(no-network) translator/judge, so the course repo is never touched.

This is the regression backstop that de-risks the anchor-identity work in
Phases 1-3: if a future phase makes an already-synced pair churn (re-translate
or rewrite), the no-op-pair count craters or a violation appears here.

Marked ``slow`` + ``integration`` (≈18 s over 212 pairs) and **skipped** when
the corpus is absent (CI, fresh clone) — point it at a corpus with
``CLM_SYNC_CORPUS_DIR``.

The same harness doubles as the churn *measurement* (item-2 / item-3 exposure
populations); run it directly for the numbers::

    python scripts/sync_corpus_harness.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

# Phase-0 measurement (2026-06-01): 81 of 212 pairs are already in post-sync-clean
# shape; the other 131 carry id-less narrative cells that predate #166 adoption.
# The floor guards against a *catastrophic* engine regression (a phase that makes
# synced pairs churn) while tolerating ordinary course-repo edits; it is well
# below the measured 81.
_PHASE0_NOOP_PAIRS = 81
_NOOP_FLOOR = 40


def _corpus_dir() -> Path | None:
    """The sync corpus root, or ``None`` when unavailable (skip).

    Requires at least one *real* ``.de.py`` / ``.en.py`` pair (not merely a
    ``.de.py``), so the skip predicate agrees with ``discover_pairs``: a de-only
    or unpaired tree skips cleanly instead of running an empty harness that would
    trip ``test_corpus_discovered``'s ``total_pairs >= 100`` floor.
    """
    candidates: list[Path] = []
    env = os.environ.get("CLM_SYNC_CORPUS_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(r"C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\slides"))
    for cand in candidates:
        if not cand.is_dir():
            continue
        for de_path in cand.rglob("*.de.py"):
            en_path = de_path.with_name(de_path.name[: -len(".de.py")] + ".en.py")
            if en_path.exists():
                return cand  # at least one real pair -> usable corpus
    return None


_CORPUS = _corpus_dir()

pytestmark = [
    pytest.mark.slow,
    pytest.mark.integration,
    pytest.mark.skipif(_CORPUS is None, reason="PythonCourses sync corpus not available"),
]


@pytest.fixture(scope="module")
def report():
    """Run the corpus harness once; all assertions share the single result."""
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    import sync_corpus_harness as harness

    assert _CORPUS is not None  # guarded by the module skipif
    result = harness.run(_CORPUS)
    if result.total_pairs == 0:  # defense-in-depth: no real pairs -> skip, don't fail
        pytest.skip(f"no de/en pairs under corpus root {_CORPUS}")
    return result


def test_corpus_discovered(report):
    # Sanity: the harness actually found a substantial corpus (else every other
    # assertion is vacuously true).
    assert report.total_pairs >= 100, report.render()
    assert report.census.code_total > 0
    assert report.census.md_total > 0


def test_noop_plans_apply_with_zero_bytes_and_zero_llm(report):
    # THE backstop: any pair whose seeded plan is a no-op must apply as a true
    # no-op — no bytes written, no translate/judge call. A violation is a pure
    # engine bug (a "nothing to do" plan that nonetheless did something).
    assert report.violations == [], report.render()


def test_noop_pairs_do_not_catastrophically_regress(report):
    # If a later phase makes already-synced pairs churn, they stop classifying
    # as no-op and this floor trips. (Phase-0 measured 81; floor has headroom for
    # ordinary course-repo edits.)
    assert report.noop_pairs >= _NOOP_FLOOR, (
        f"only {report.noop_pairs} no-op pairs (Phase-0 baseline {_PHASE0_NOOP_PAIRS}); "
        "a phase may have made synced pairs churn\n" + report.render()
    )


def test_churn_baseline_populations_present(report):
    # The measurement half: the item-2 (neutral, silent-drop) and item-3 (id-less
    # localized, re-translated) exposure populations are what Phases 2-3 must
    # drive to zero. Guard that the harness still measures them (a non-zero,
    # plausible magnitude), so a refactor can't silently zero the baseline out.
    c = report.census
    assert c.item2_population > 1000, report.render()  # Phase-0: ~8,014
    assert c.item3_population > 100, report.render()  # Phase-0: ~1,702
    # The per-group blast radius pins the GROUPING (not just the predicate): a
    # broken _split_groups would collapse cells into too few groups (inflating
    # max_in_one_group) or shatter them (deflating it). `blast_total ==
    # item3_population` is true by construction (same predicate, same cells), so
    # assert the grouping-shaped quantities instead.
    assert report.blast_groups_with_idless > 100, report.render()  # Phase-0: ~948
    assert 1 <= report.blast_max_in_one_group <= 50, report.render()  # Phase-0: ~10
    assert report.blast_max_in_one_group <= report.census.item3_population
