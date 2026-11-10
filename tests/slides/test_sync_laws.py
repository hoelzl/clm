"""Ratchet for the oracle-free sync laws on the real engine; see ``sync_laws.py``.

``slow`` tier (it runs on every PR in CI): 4,767 cases over 63 recorded
decks, one test per recorded deck.

The engine violates the laws today. ``sync_laws_baseline.json`` holds every
violating case with what the engine does instead, and each test compares its
deck's cases with it exactly:

- a case outside the baseline must satisfy its law;
- a baseline case must still fail the same way. When a fix makes it hold,
  the test FAILS until the entry is removed
  (``python scripts/sync_laws.py update``), so the count can only go down.
  The script refuses to write a baseline in which a case got worse.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from tests.slides.sync_laws import HOLDS, Case, Word, cases, check, load_baseline

pytestmark = pytest.mark.slow

_BASELINE = load_baseline()
_BY_DECK: dict[Word, list[Case]] = defaultdict(list)
for _case in cases("ci"):
    _BY_DECK[_case.base].append(_case)


@pytest.mark.parametrize(
    "deck", [pytest.param(deck, id=" ".join(deck) or "empty") for deck in sorted(_BY_DECK)]
)
def test_laws_for_recorded_deck(deck: Word) -> None:
    drift = [
        f"{case.id}: {known} -> {got}"
        for case in _BY_DECK[deck]
        if (got := check(case)) != (known := _BASELINE.get(case.id, HOLDS))
    ]
    assert not drift, (
        f"{len(drift)} cases differ from sync_laws_baseline.json (baseline -> now). "
        "A case that now holds: run `python scripts/sync_laws.py update`. "
        "Anything else is a regression:\n" + "\n".join(drift[:20])
    )


def test_baseline_names_only_real_cases() -> None:
    """A stale baseline entry (a renamed or removed case) would hide nothing
    and drift silently."""
    ids = {case.id for deck_cases in _BY_DECK.values() for case in deck_cases}
    assert set(_BASELINE) <= ids, sorted(set(_BASELINE) - ids)[:10]
