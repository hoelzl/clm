"""Meta-tests for the ``load_sensitive`` pre-push gate tier (issue #926).

The pre-push hook (``scripts/run_pytest_hook.py``) narrows the fast suite
with ``-m "... and not load_sensitive"`` so the local gate stays fast and
deterministic; CI and a plain ``uv run pytest`` run everything. The tagging
itself is automatic in ``tests/conftest.py`` (directory-based, see
``LOAD_SENSITIVE_TEST_DIRS``) — these tests pin that the wiring works:

1. every collected test under a listed family directory carries the marker;
2. the conftest hook's tally covers every listed family when unfiltered.
"""

from __future__ import annotations

import pytest

from tests.conftest import LOAD_SENSITIVE_TEST_DIRS


def test_tagged_families_cover_listed_directories(request) -> None:
    """Every family in LOAD_SENSITIVE_TEST_DIRS has tagged items this run.

    Skipped per family when a narrowed ``-m`` filter deselects it (e.g. the
    pre-push tier itself, which deselects *all* of them).
    """
    counts = getattr(request.config, "_clm_load_sensitive_counts", None)
    assert counts is not None, "the load_sensitive tagging hook did not run"
    if not counts:
        pytest.skip("running under a filter that deselects the load_sensitive tier")
    for family in LOAD_SENSITIVE_TEST_DIRS:
        name = "/".join(family)
        assert counts.get(name, 0) > 0, f"no load_sensitive tests tagged under tests/{name}"


def test_collected_items_under_family_dirs_are_tagged(request) -> None:
    """Spot-check the tagging invariant from the item side: any collected
    item whose path sits under a family directory must carry the marker
    (this is what makes adding a test there need no manual marker)."""
    from pathlib import Path

    tests_root = Path(__file__).resolve().parent
    checked = 0
    for item in request.session.items:
        try:
            rel_parts = Path(str(item.fspath)).resolve().relative_to(tests_root).parts
        except ValueError:
            continue
        if any(rel_parts[: len(f)] == f for f in LOAD_SENSITIVE_TEST_DIRS):
            checked += 1
            assert item.get_closest_marker("load_sensitive") is not None, (
                f"{item.nodeid} sits under a load_sensitive family but is untagged"
            )
    if checked == 0:
        pytest.skip("no load_sensitive family items collected under this filter")
