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


# The committed synthetic corpus shared with ``test_sync_corpus_mutation.py``
# (one tiny in-sync bilingual deck). It is a *fallback*: a real PythonCourses
# checkout still wins. On the bundled corpus only the scale-independent no-op
# *invariant* runs — the population/size floors below are real-corpus-scale and
# would be vacuous on one pair, so they stay gated to a real corpus. See Phase C
# of ``docs/claude/sync-corpus-mutation-443-investigation-handover.md``.
_BUNDLED_CORPUS = Path(__file__).resolve().parents[1] / "data" / "sync_corpus"


def _has_pair(cand: Path) -> bool:
    if not cand.is_dir():
        return False
    for de_path in cand.rglob("*.de.py"):
        if de_path.with_name(de_path.name[: -len(".de.py")] + ".en.py").exists():
            return True
    return False


def _corpus_dir() -> tuple[Path, bool] | None:
    """Resolve the corpus root and whether it is the bundled synthetic one.

    Order: ``CLM_SYNC_CORPUS_DIR`` env, the maintainer's PythonCourses checkout,
    then the committed synthetic corpus. Requires at least one real ``.de.py`` /
    ``.en.py`` pair so the skip predicate agrees with ``discover_pairs``.
    Returns ``(root, is_bundled)``.
    """
    external: list[Path] = []
    env = os.environ.get("CLM_SYNC_CORPUS_DIR")
    if env:
        external.append(Path(env))
    external.append(Path(r"C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\slides"))
    bundled = _BUNDLED_CORPUS.resolve()
    for cand in external:
        if _has_pair(cand):
            return cand, cand.resolve() == bundled
    if _has_pair(_BUNDLED_CORPUS):
        return _BUNDLED_CORPUS, True
    return None


_resolved = _corpus_dir()
_CORPUS = _resolved[0] if _resolved else None
_BUNDLED = _resolved[1] if _resolved else False

# Real corpus = ``slow`` (≈18 s over 212 pairs) and CI never runs ``slow``; the
# bundled fallback is one pair (~instant), so drop ``slow`` there and let the
# ``integration`` CI job run the no-op invariant. No ``skipif``: bundled always present.
pytestmark = [pytest.mark.integration]
if not _BUNDLED:
    pytestmark.append(pytest.mark.slow)


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


_real_corpus_only = pytest.mark.skipif(
    _BUNDLED,
    reason="scale-dependent floor — meaningful only on the full PythonCourses corpus",
)


@_real_corpus_only
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


@_real_corpus_only
def test_noop_pairs_do_not_catastrophically_regress(report):
    # If a later phase makes already-synced pairs churn, they stop classifying
    # as no-op and this floor trips. (Phase-0 measured 81; floor has headroom for
    # ordinary course-repo edits.)
    assert report.noop_pairs >= _NOOP_FLOOR, (
        f"only {report.noop_pairs} no-op pairs (Phase-0 baseline {_PHASE0_NOOP_PAIRS}); "
        "a phase may have made synced pairs churn\n" + report.render()
    )


@_real_corpus_only
def test_churn_baseline_populations_present(report):
    # The measurement half. Item-2 (neutral, silent-drop exposure) is still a
    # live population — shared cells are never stamped (#520 §3.4) — so guard
    # that the harness keeps measuring it (a non-zero, plausible magnitude).
    c = report.census
    assert c.item2_population > 1000, report.render()  # Phase-0: ~8,014
    # Item-3 (id-less localized, re-translated) was ~1,702 before the sync-v3
    # Phase 0 normalize commit (`clm slides normalize --stamp-ids`, #520)
    # stamped ids on the whole population. Post-normalize this is a CEILING:
    # only the handful of cells on refused asymmetric decks may remain
    # id-less (~5 measured), and the number regressing upward means id-less
    # localized cells are being (re)introduced — the exposure the one-time
    # normalization exists to keep closed.
    assert c.item3_population < 100, report.render()  # normalized: ~5
    assert report.blast_max_in_one_group <= max(report.census.item3_population, 1)
