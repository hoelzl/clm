"""Ratchet for the reorder x edit sweep (#1130); see ``sync_sweep.py``.

``slow`` tier (it runs on every PR in CI): 2,112 cases over two domains,
about 30 s on 8 workers.

A case in ``sync_sweep_baseline.json`` is a known failure and xfails while
it fails the same way. When a fix makes it converge, the test FAILS until
the baseline entry is removed (``python scripts/update_sync_sweep_baseline.py``),
so the failure count can only go down. A case outside the baseline must
converge with every edit kept.
"""

from __future__ import annotations

import pytest

from tests.slides.sync_sweep import Case, cases, load_baseline, run_case

pytestmark = pytest.mark.slow

_BASELINE = load_baseline()


@pytest.mark.parametrize(
    "case", [pytest.param(c, id=c.id) for domain in ("four", "five") for c in cases(domain)]
)
def test_reorder_edit_case(case: Case) -> None:
    status, passes = run_case(case)
    known = _BASELINE.get(case.id)
    if known is None:
        assert status == "converged", (status, passes)
        return
    if status == "converged":
        pytest.fail(
            f"{case.id} now converges -- remove it from sync_sweep_baseline.json "
            f"(python scripts/update_sync_sweep_baseline.py)"
        )
    assert status == known, (f"failure kind changed: {known} -> {status}", passes)
    pytest.xfail(f"#1130 baseline: {known}")


def test_baseline_names_only_real_cases() -> None:
    """A stale baseline entry (a renamed or removed case) would hide nothing
    and drift silently."""
    ids = {c.id for domain in ("four", "five") for c in cases(domain)}
    assert set(_BASELINE) <= ids, sorted(set(_BASELINE) - ids)[:10]


@pytest.mark.parametrize(
    "case", [pytest.param(c, id=c.id) for domain in ("four", "five") for c in cases(domain)]
)
def test_reorder_edit_case_on_the_sequence_core(case: Case) -> None:
    """Phase C of ``docs/claude/design/sync-sequence-core.md``: every case
    converges on the sequence core's pass, so it has no baseline. Where both
    readings of the files differ it asks ``identify``, which the sweep
    answers as a careful agent would (``sync_engines.answer_identify``)."""
    from tests.slides.sync_engines import SEQUENCE

    status, passes = run_case(case, engine=SEQUENCE)
    assert status == "converged", (status, passes)
